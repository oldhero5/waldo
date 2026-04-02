"""Workflow DAG executor — topologically sorts nodes and runs blocks."""
import logging
import time
from typing import Any

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
    from lib.workflow_blocks.visualization import BlurVisualization, BoundingBoxVisualization, CountVisualization

    for cls in [
        # I/O
        ImageInputBlock, OutputBlock, WebhookBlock,
        # Platform
        DatasetInputBlock, TrainTriggerBlock,
        # Models
        DetectionBlock, ModelSelectorBlock,
        # Transforms
        CropBlock, FilterBlock, ResizeBlock,
        # Visualization
        BoundingBoxVisualization, BlurVisualization, CountVisualization,
        # Logic
        ConditionalBlock, ExpressionBlock,
        # Classical CV
        GrayscaleBlock, ContourDetectionBlock, DominantColorBlock,
        # AI
        LLMBlock,
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


def execute_workflow(graph: dict, initial_inputs: dict[str, Any] | None = None) -> dict:
    """Execute a workflow graph.

    Args:
        graph: Workflow definition with "nodes" and "edges" lists.
            Each node: {"id": str, "type": str, "config": dict}
            Each edge: {"source": str, "source_port": str, "target": str, "target_port": str}
        initial_inputs: Data to inject into the first node (e.g., {"__image__": np.array}).

    Returns:
        Dict with "result" (output data), "metadata" (per-node timing), and "errors" (if any).
    """
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    if not nodes:
        return {"result": None, "metadata": {}, "errors": ["Empty workflow"]}

    # Build adjacency and incoming edge maps
    node_map = {n["id"]: n for n in nodes}
    incoming: dict[str, list[dict]] = {n["id"]: [] for n in nodes}
    outgoing: dict[str, list[dict]] = {n["id"]: [] for n in nodes}

    for edge in edges:
        incoming[edge["target"]].append(edge)
        outgoing[edge["source"]].append(edge)

    # Topological sort (Kahn's algorithm)
    in_degree = {n["id"]: len(incoming[n["id"]]) for n in nodes}
    queue = [nid for nid, deg in in_degree.items() if deg == 0]
    sorted_nodes = []

    while queue:
        nid = queue.pop(0)
        sorted_nodes.append(nid)
        for edge in outgoing[nid]:
            in_degree[edge["target"]] -= 1
            if in_degree[edge["target"]] == 0:
                queue.append(edge["target"])

    if len(sorted_nodes) != len(nodes):
        return {"result": None, "metadata": {}, "errors": ["Workflow has cycles"]}

    # Execute nodes in topological order
    node_outputs: dict[str, dict[str, Any]] = {}  # node_id -> {port_name: value}
    metadata: dict[str, dict] = {}
    result = None
    errors: list[str] = []

    for nid in sorted_nodes:
        node = node_map[nid]
        block_type = node["type"]
        config = node.get("config", {})

        try:
            block = get_block(block_type, config)

            # Collect inputs from connected edges
            inputs: dict[str, Any] = {}
            for edge in incoming[nid]:
                source_outputs = node_outputs.get(edge["source"], {})
                value = source_outputs.get(edge["source_port"])
                if value is not None:
                    inputs[edge["target_port"]] = value

            # Inject initial inputs for the first node
            if not incoming[nid] and initial_inputs:
                inputs.update(initial_inputs)

            # Validate
            validation_errors = block.validate_inputs(inputs)
            if validation_errors:
                errors.extend([f"Node '{nid}' ({block_type}): {e}" for e in validation_errors])
                continue

            # Execute
            start = time.perf_counter()
            block_result = block.execute(inputs)
            elapsed = time.perf_counter() - start

            node_outputs[nid] = block_result.outputs
            metadata[nid] = {
                "block_type": block_type,
                "elapsed_ms": round(elapsed * 1000, 1),
                **block_result.metadata,
            }

            # Check for final output
            if "__result__" in block_result.outputs:
                result = block_result.outputs["__result__"]

            logger.info("Block %s (%s): %dms", nid, block_type, int(elapsed * 1000))

        except Exception as e:
            errors.append(f"Node '{nid}' ({block_type}) failed: {e}")
            logger.exception("Workflow block %s failed", nid)

    return {
        "result": result,
        "metadata": metadata,
        "errors": errors,
    }
