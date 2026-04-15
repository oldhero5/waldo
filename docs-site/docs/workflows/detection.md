---
title: Detection Blocks
sidebar_position: 2
---

# Detection Blocks

Source: [`lib/workflow_blocks/detection.py`](https://github.com/your-org/waldo/blob/main/lib/workflow_blocks/detection.py)

These blocks run object detection or segmentation models against input images.

## YOLO Detection

Runs a YOLO26 model (the active one from the registry, or a specific version) against an input image and emits bounding boxes.

**Inputs:** `image: ndarray (H, W, 3)`
**Outputs:** `detections: list[Detection]`
**Params:**
- `model_id` — model registry UUID, or `"active"` for the default
- `confidence` — minimum confidence (default `0.25`)
- `iou` — NMS IoU threshold (default `0.45`)
- `classes` — optional class allowlist

## SAM Segmentation

Runs SAM 3 against an image with a text or visual prompt, returns masks.

**Inputs:** `image`, optional `prompt_text` or `prompt_box`
**Outputs:** `masks: list[Mask]`
**Params:**
- `prompts` — list of text prompts
- `threshold` — mask confidence cutoff
- `resolution` — input resize before inference

## SAM Video Track

Runs SAM 3 against a stream of frames and tracks instances across them.

**Inputs:** `frames: iterator[ndarray]`
**Outputs:** `tracks: list[Track]`
**Params:** prompts, tracking confidence, max gap frames
