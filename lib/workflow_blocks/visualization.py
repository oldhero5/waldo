"""Visualization blocks — render detections on images for visual output."""
from typing import Any

import cv2

from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class BoundingBoxVisualization(BlockBase):
    name = "visualize_bbox"
    display_name = "Draw Bounding Boxes"
    description = "Draw bounding boxes with labels and confidence on the image."
    category = "visualization"
    input_ports = [
        Port("image", "image", "Source image"),
        Port("detections", "detections", "Detection results"),
    ]
    output_ports = [Port("image", "image", "Annotated image")]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        image = inputs["image"].copy()
        detections = inputs["detections"]
        thickness = self.config.get("thickness", 2)
        font_scale = self.config.get("font_scale", 0.5)

        colors = [(66, 133, 244), (234, 67, 53), (52, 168, 83), (251, 188, 4), (103, 58, 183)]

        for i, det in enumerate(detections):
            color = colors[det.class_index % len(colors)]
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            cv2.rectangle(image, (x1, y1), (x2, y2), color, thickness)
            label = f"{det.class_name} {det.confidence:.0%}"
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, font_scale, 1)
            cv2.rectangle(image, (x1, y1 - th - 6), (x1 + tw + 4, y1), color, -1)
            cv2.putText(image, label, (x1 + 2, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), 1)

        return BlockResult(outputs={"image": image}, metadata={"drawn": len(detections)})

    def _config_schema(self) -> dict:
        return {
            "thickness": {"type": "number", "default": 2, "label": "Line thickness"},
            "font_scale": {"type": "number", "default": 0.5, "label": "Font scale"},
        }


class BlurVisualization(BlockBase):
    name = "visualize_blur"
    display_name = "Blur Detections"
    description = "Blur detected regions for privacy (faces, license plates, etc.)."
    category = "visualization"
    input_ports = [
        Port("image", "image", "Source image"),
        Port("detections", "detections", "Regions to blur"),
    ]
    output_ports = [Port("image", "image", "Image with blurred regions")]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        image = inputs["image"].copy()
        detections = inputs["detections"]
        kernel = self.config.get("kernel_size", 51)
        if kernel % 2 == 0:
            kernel += 1

        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            x1, y1 = max(0, x1), max(0, y1)
            x2, y2 = min(image.shape[1], x2), min(image.shape[0], y2)
            roi = image[y1:y2, x1:x2]
            if roi.size > 0:
                image[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (kernel, kernel), 0)

        return BlockResult(outputs={"image": image}, metadata={"blurred": len(detections)})

    def _config_schema(self) -> dict:
        return {"kernel_size": {"type": "number", "default": 51, "label": "Blur strength (odd number)"}}


class CountVisualization(BlockBase):
    name = "count"
    display_name = "Count Objects"
    description = "Count the number of detections, optionally grouped by class."
    category = "logic"
    input_ports = [Port("detections", "detections", "Input detections")]
    output_ports = [
        Port("total", "number", "Total count"),
        Port("by_class", "any", "Count per class name"),
        Port("detections", "detections", "Pass-through detections"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        detections = inputs["detections"]
        by_class: dict[str, int] = {}
        for d in detections:
            by_class[d.class_name] = by_class.get(d.class_name, 0) + 1

        return BlockResult(
            outputs={"total": len(detections), "by_class": by_class, "detections": detections},
            metadata={"total": len(detections), "classes": len(by_class)},
        )
