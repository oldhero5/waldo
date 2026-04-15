---
title: Workflow Blocks Overview
sidebar_position: 1
---

# Workflow Blocks

A workflow is a directed graph that takes input (an image, a video frame, or a stream) and produces output (annotations, alerts, transformed pixels). Each node in the graph is a **block**.

Block source: [`lib/workflow_blocks/`](https://github.com/oldhero5/waldo/tree/main/lib/workflow_blocks)

## Block categories

| File | Category | Examples |
| --- | --- | --- |
| `detection.py` | Detection | YOLO inference, SAM 3 segmentation |
| `specialized.py` | Specialized | Face detection, OCR, pose estimation |
| `classical_cv.py` | Classical CV | Edge detection, contour finding, Hough transforms |
| `crop.py` | Crop | Bbox crop, fixed-region crop, padded crop |
| `filter_block.py` | Filter | Threshold, NMS, class filter, confidence filter |
| `io.py` | I/O | Image input, video frame source, MinIO read/write |
| `llm.py` | LLM | Gemma agent inference, classification by description |
| `logic.py` | Logic | If/else, switch, merge, broadcast |
| `platform.py` | Platform | Active model lookup, dataset write, alert dispatch |
| `visualization.py` | Visualization | Draw boxes, draw masks, render labels |

## Block contract

Every block subclasses `BaseBlock` from `lib/workflow_blocks/base.py` and declares:

- `inputs` — typed input ports
- `outputs` — typed output ports
- `params` — Pydantic model of configurable parameters
- `run(ctx, inputs) -> outputs` — the actual logic

The workflow engine handles type-checking the graph, scheduling block execution in topological order, and shuttling intermediate values between blocks.

## Editor

The visual editor at `/workflows/editor` uses [`@xyflow/react`](https://reactflow.dev/) for the canvas. The block palette is populated from `GET /api/v1/workflows/blocks`, so any new block class registered server-side appears automatically.
