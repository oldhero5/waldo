---
title: Specialized Blocks
sidebar_position: 3
---

# Specialized Blocks

Source: [`lib/workflow_blocks/specialized.py`](https://github.com/oldhero5/waldo/blob/main/lib/workflow_blocks/specialized.py)

Registered blocks are `ocr`, `license_plate`, `line_counter`, and `zone_counter`. There are no registered face-detection or pose-estimation blocks.

## License Plate Detection

The `license_plate` block runs the shared YOLO engine, crops each detection, and attempts EasyOCR. It requires an appropriate trained detector; it does not supply a dedicated plate model or filter detections to a plate class. Without EasyOCR, text is a placeholder such as `[plate:class_name]`.

**Inputs:** `image`
**Outputs:** `plates` containing `{ bbox, text, confidence, class }`, and `count`. Confidence is the detector score. Config: `confidence` (default `0.3`).

## OCR

The `ocr` block uses EasyOCR when installed. Otherwise it returns contour regions with `text: "?"` and confidence `0.0`, plus a message requesting EasyOCR. That fallback locates candidate regions without recognizing text. Config: `language` (default `eng`).

**Inputs:** `image`
**Outputs:** `text: str`, `regions` with bounding boxes, text, and per-region confidence. EasyOCR returns quadrilateral coordinates; the contour fallback returns pixel x/y/width/height.

## Line Counter

`line_counter` accepts `detections` and returns `count` plus the detections. It counts centers near `line_y` in a single image; it has no temporal crossing history. `threshold` sets proximity, and `direction` is currently unused. Pixel center-y values are normalized against an assumed height of 1080.

## Zone Counter

`zone_counter` accepts `detections` and returns `in_zone`, `outside_zone`, and only the detections inside the rectangular zone. Configure `zone_x1`, `zone_y1`, `zone_x2`, and `zone_y2` in normalized coordinates. Pixel centers are normalized against assumed dimensions of 1920×1080, so counts on other image sizes need that limitation considered.

---

The full list of blocks updates as new ones are registered. Use `GET /api/v1/workflows/blocks` to fetch the live catalog with input/output schemas.
