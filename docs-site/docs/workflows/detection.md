---
title: Detection Blocks
sidebar_position: 2
---

# Detection Blocks

Source: [`lib/workflow_blocks/detection.py`](https://github.com/oldhero5/waldo/blob/main/lib/workflow_blocks/detection.py)

The registered `detection` block runs YOLO inference against one input image. SAM segmentation and SAM video tracking are not registered workflow blocks.

## YOLO Detection

Uses the shared inference engine's current model. For a specific registry model, use the separate `model_select` block in `platform.py`, configured with `model_id` and `confidence`.

**Inputs:** `image: ndarray (H, W, 3)`
**Outputs:** `detections: list[Detection]`, original `image`

**Config:**

- `confidence` — minimum confidence (default `0.25`)
- `class_filter` — optional class-name allowlist

Detections contain class name/index, confidence, and pixel xyxy boxes; segmentation models may also produce masks. This block performs image prediction and does not assign temporal track identities. The block does not expose `iou` or `model_id` configuration.
