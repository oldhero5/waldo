---
title: Workflow Blocks Overview
sidebar_position: 1
---

# Workflow Blocks

A workflow is a directed graph of blocks executed on supplied inputs, typically one image. The current engine does not provide a video source or temporal SAM tracking block.

Block source: [`lib/workflow_blocks/`](https://github.com/oldhero5/waldo/tree/main/lib/workflow_blocks)

## Block categories

| File | Category | Examples |
| --- | --- | --- |
| `detection.py` | Detection | YOLO image inference |
| `specialized.py` | Specialized | OCR, license-plate reader, line and zone counters |
| `classical_cv.py` | Classical CV | Grayscale, contours, dominant color, resize |
| `crop.py` | Crop | Detection crops with optional padding |
| `filter_block.py` | Filter | Class, confidence, and area filtering |
| `io.py` | I/O | Image input, serialized output |
| `llm.py` | LLM | Text generation through the configured agent provider |
| `logic.py` | Logic | Conditional routing, expressions |
| `platform.py` | Platform | Dataset image input, model selection, training trigger, webhook |
| `visualization.py` | Visualization | Bounding boxes, blur, detection counts |

## Block contract

Every block subclasses `BlockBase` from `lib/workflow_blocks/base.py` and declares:

- `input_ports` and `output_ports` — `Port` metadata with names, type strings, and required flags
- `config` — a configuration dictionary; `_config_schema()` describes editor controls
- `execute(inputs) -> BlockResult` — returns output and metadata dictionaries

The engine in `lib/workflow_engine.py` validates node IDs, edges, port names/types,
required inputs and cycles before executing nodes in topological order. A closed
conditional branch or failed dependency skips downstream nodes. Port metadata and
config schemas are not full runtime payload validation; failures remain visible
in the workflow's `errors` list.

Saved workflows and resource-bearing blocks use the authenticated workspace.
Model selection uses the owned model pool entry without replacing a global active
engine. LLM blocks snapshot the workspace provider configuration for the run; see
[agent configuration](../ui/agent). Graph JSON cannot supply provider credentials.
Webhook destinations default to public addresses with redirects disabled; private
networks require the operator's `WALDO_WORKFLOW_ALLOW_PRIVATE_WEBHOOKS=1` override.

## Editor

The visual editor at `/workflows/editor` uses [`@xyflow/react`](https://reactflow.dev/) for the canvas. The block palette is populated from `GET /api/v1/workflows/blocks`, so any new block class registered server-side appears automatically.
