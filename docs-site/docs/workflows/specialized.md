---
title: Specialized Blocks
sidebar_position: 3
---

# Specialized Blocks

Source: [`lib/workflow_blocks/specialized.py`](https://github.com/oldhero5/waldo/blob/main/lib/workflow_blocks/specialized.py)

Domain-specific detectors that wrap purpose-built models instead of generic YOLO/SAM.

## Face Detection

Lightweight face detector. Used as a pre-filter before face-recognition or blur blocks.

**Inputs:** `image`
**Outputs:** `faces: list[Box]`

## License Plate Detection

Detects license plates and (optionally) crops + OCRs them.

**Inputs:** `image`
**Outputs:** `plates: list[Plate]` where `Plate = { box, text?, confidence }`

## Pose Estimation

Returns 17-keypoint COCO pose for each detected person.

**Inputs:** `image`
**Outputs:** `poses: list[Pose]`

## OCR

General-purpose text recognition. Runs against a cropped region (usually downstream of a detector or crop block).

**Inputs:** `image`
**Outputs:** `text: str`, `confidence: float`

---

The full list of blocks updates as new ones are registered. Use `GET /api/v1/workflows/blocks` to fetch the live catalog with input/output schemas.
