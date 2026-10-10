"""Workflow DAG executor — topologically sorts nodes and runs blocks."""

import logging
import time
from collections import deque
from copy import deepcopy
from typing import Any

from lib.authorization import WorkspacePrincipal
from lib.workflow_blocks.base import BlockBase

logger = logging.getLogger(__name__)

# Registry of available block types
BLOCK_REGISTRY: dict[str, type[BlockBase]] = {}


def register_block(cls: type[BlockBase]) -> type[BlockBase]:
    BLOCK_REGISTRY[cls.name] = cls
    return cls


def _load_blocks():
    """Import all block modules to populate the registry."""
    from lib.workflow_blocks.classical_cv import ContourDetectionBlock, DominantColorBlock, GrayscaleBlock, ResizeBlock
    from lib.workflow_blocks.crop import CropBlock
    from lib.workflow_blocks.detection import DetectionBlock
    from lib.workflow_blocks.filter_block import FilterBlock
    from lib.workflow_blocks.io import ImageInputBlock, OutputBlock
    from lib.workflow_blocks.llm import LLMBlock
    from lib.workflow_blocks.logic import ConditionalBlock, ExpressionBlock
    from lib.workflow_blocks.platform import DatasetInputBlock, ModelSelectorBlock, TrainTriggerBlock, WebhookBlock
    from lib.workflow_blocks.specialized import LicensePlateBlock, LineCounterBlock, OCRBlock, ZoneCounterBlock
    from lib.workflow_blocks.visualization import BlurVisualization, BoundingBoxVisualization, CountVisualization

    for cls in [
        # I/O
        ImageInputBlock,
        OutputBlock,
        WebhookBlock,
        # Platform
        DatasetInputBlock,
        TrainTriggerBlock,
        # Models
        DetectionBlock,
        ModelSelectorBlock,
        # Transforms
        CropBlock,
        FilterBlock,
        ResizeBlock,
        # Visualization
        BoundingBoxVisualization,
        BlurVisualization,
        CountVisualization,
        # Logic
        ConditionalBlock,
        ExpressionBlock,
        # Classical CV
        GrayscaleBlock,
        ContourDetectionBlock,
        DominantColorBlock,
        # AI
        LLMBlock,
        # Specialized
        OCRBlock,
        LicensePlateBlock,
        LineCounterBlock,
        ZoneCounterBlock,
    ]:
        BLOCK_REGISTRY[cls.name] = cls


def get_block_schemas() -> list[dict]:
    """Return schemas for all available blocks (for the frontend palette)."""
    if not BLOCK_REGISTRY:
        _load_blocks()
    return [cls({}).to_schema() for cls in BLOCK_REGISTRY.values()]


def get_block(block_type: str, config: dict | None = None) -> BlockBase:
    """Instantiate a block by type name."""
    if not BLOCK_REGISTRY:
        _load_blocks()
    cls = BLOCK_REGISTRY.get(block_type)
    if not cls:
        raise ValueError(f"Unknown block type: {block_type}. Available: {list(BLOCK_REGISTRY.keys())}")
    return cls(config)


def _prepare_graph(graph: dict):
    """Validate the complete graph before any block can run."""
    if not isinstance(graph, dict):
        raise ValueError("Workflow must be an object")
    nodes, edges = graph.get("nodes"), graph.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("Empty workflow or invalid nodes")
    if not isinstance(edges, list):
        raise ValueError("Workflow edges must be a list")
    node_map, blocks = {}, {}
    for raw_node in nodes:
        if not isinstance(raw_node, dict):
            raise ValueError("Workflow nodes must be objects")
        node = deepcopy(raw_node)
        nid = node.get("id")
        if not isinstance(nid, str) or not nid.strip():
            raise ValueError("Each workflow node needs a nonempty string ID")
        if nid in node_map:
            raise ValueError(f"Duplicate workflow node ID: {nid}")
        if not isinstance(node.get("type"), str) or not isinstance(node.get("config", {}), dict):
            raise ValueError(f"Invalid type or config for node: {nid}")
        node_map[nid] = node
        blocks[nid] = get_block(node["type"], node.get("config", {}))

    incoming = {nid: [] for nid in node_map}
    outgoing = {nid: [] for nid in node_map}
    targets = set()
    for raw_edge in edges:
        if not isinstance(raw_edge, dict):
            raise ValueError("Workflow edges must be objects")
        edge = deepcopy(raw_edge)
        source, target = edge.get("source"), edge.get("target")
        if (
            not isinstance(source, str)
            or source not in node_map
            or not isinstance(target, str)
            or target not in node_map
        ):
            raise ValueError("Workflow edge references an unknown node")
        source_ports = {port.name: port.type for port in blocks[source].output_ports}
        target_ports = {port.name: port.type for port in blocks[target].input_ports}
        source_port, target_port = edge.get("source_port"), edge.get("target_port")
        if not isinstance(source_port, str) or source_port not in source_ports:
            raise ValueError(f"Unknown output port on node: {source}")
        if not isinstance(target_port, str) or target_port not in target_ports:
            raise ValueError(f"Unknown input port on node: {target}")
        if source_ports[source_port] != target_ports[target_port] and "any" not in (
            source_ports[source_port],
            target_ports[target_port],
        ):
            raise ValueError(f"Incompatible ports between nodes: {source} and {target}")
        target_key = (target, target_port)
        if target_key in targets:
            raise ValueError(f"Multiple edges provide input {target_port} on node: {target}")
        targets.add(target_key)
        incoming[target].append(edge)
        outgoing[source].append(edge)

    in_degree = {nid: len(incoming[nid]) for nid in node_map}
    queue = deque(nid for nid, degree in in_degree.items() if degree == 0)
    order = []
    while queue:
        nid = queue.popleft()
        order.append(nid)
        for edge in outgoing[nid]:
            in_degree[edge["target"]] -= 1
            if in_degree[edge["target"]] == 0:
                queue.append(edge["target"])
    if len(order) != len(node_map):
        raise ValueError("Workflow has cycles")
    return node_map, blocks, incoming, order


def validate_workflow(graph: dict) -> None:
    _prepare_graph(graph)


def execute_workflow(
    graph: dict,
    initial_inputs: dict[str, Any] | None = None,
    *,
    principal: WorkspacePrincipal | None = None,
) -> dict:
    """Execute a validated DAG under immutable caller authority.

    Graph configuration and port values never supply the principal. Pure blocks
    may run without one; platform/inference blocks require it and revalidate
    current membership and ownership at the resource boundary.
    """
    try:
        if principal is not None and not isinstance(principal, WorkspacePrincipal):
            raise ValueError("Invalid workflow execution principal")
        node_map, blocks, incoming, order = _prepare_graph(graph)
        # Check every statically knowable input before any independent branch can
        # perform a side effect. Values from upstream nodes are validated at run time.
        for nid, block in blocks.items():
            available = {edge["target_port"]: None for edge in incoming[nid]}
            if not incoming[nid] and initial_inputs:
                available.update(initial_inputs)
            missing = block.validate_inputs(available)
            if missing:
                raise ValueError(f"Node '{nid}': {'; '.join(missing)}")
    except (ValueError, TypeError, KeyError) as error:
        return {"result": None, "metadata": {}, "errors": [f"Invalid workflow: {error}"]}

    provider_config = None
    if any(node["type"] == "llm" for node in node_map.values()):
        from lib.agent.providers import get_config

        provider_config = get_config(str(principal.workspace_id) if principal else None)

    node_outputs: dict[str, dict[str, Any]] = {}
    blocked: set[str] = set()
    metadata: dict[str, dict] = {}
    result = None
    errors: list[str] = []
    for nid in order:
        block_type = node_map[nid]["type"]
        # A closed conditional branch must not fall back to optional input config.
        branch_closed = any(
            edge["source"] in blocked
            or (
                node_map[edge["source"]]["type"] == "conditional"
                and edge["source_port"] == "passed"
                and node_outputs.get(edge["source"], {}).get("passed") is None
            )
            for edge in incoming[nid]
        )
        if branch_closed:
            blocked.add(nid)
            metadata[nid] = {"block_type": block_type, "skipped": True, "reason": "Upstream branch is closed or failed"}
            continue
        try:
            block = blocks[nid]
            block.execution_principal = principal
            block.provider_config = provider_config
            inputs: dict[str, Any] = {}
            for edge in incoming[nid]:
                value = node_outputs.get(edge["source"], {}).get(edge["source_port"])
                if value is not None:
                    inputs[edge["target_port"]] = value
            if not incoming[nid] and initial_inputs:
                inputs.update(initial_inputs)
            validation_errors = block.validate_inputs(inputs)
            if validation_errors:
                errors.extend(f"Node '{nid}' ({block_type}): {error}" for error in validation_errors)
                blocked.add(nid)
                continue
            start = time.perf_counter()
            block_result = block.execute(inputs)
            elapsed = time.perf_counter() - start
            node_outputs[nid] = block_result.outputs
            metadata[nid] = {"block_type": block_type, "elapsed_ms": round(elapsed * 1000, 1), **block_result.metadata}
            if "__result__" in block_result.outputs:
                result = block_result.outputs["__result__"]
            logger.info("Block %s (%s): %dms", nid, block_type, int(elapsed * 1000))
        except Exception as error:
            blocked.add(nid)
            errors.append(f"Node '{nid}' ({block_type}) failed: {error}")
            logger.exception("Workflow block %s failed", nid)
    return {"result": result, "metadata": metadata, "errors": errors}
