"""Crop block — extracts image regions from detection bboxes."""
from typing import Any

from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class CropBlock(BlockBase):
    name = "crop"
    display_name = "Crop Detections"
    description = "Crop detected regions from the image for further processing."
    category = "transforms"
    input_ports = [
        Port("image", "image", "Source image"),
        Port("detections", "detections", "Detection results with bboxes"),
    ]
    output_ports = [
        Port("crops", "image_list", "List of cropped image regions"),
        Port("detections", "detections", "Original detections (passthrough)"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        image = inputs["image"]
        detections = inputs["detections"]
        padding = self.config.get("padding", 0)
        h, w = image.shape[:2]

        crops = []
        for det in detections:
            x1, y1, x2, y2 = [int(v) for v in det.bbox]
            # Add padding
            x1 = max(0, x1 - padding)
            y1 = max(0, y1 - padding)
            x2 = min(w, x2 + padding)
            y2 = min(h, y2 + padding)
            crop = image[y1:y2, x1:x2]
            if crop.size > 0:
                crops.append(crop)

        return BlockResult(
            outputs={"crops": crops, "detections": detections},
            metadata={"crop_count": len(crops)},
        )

    def _config_schema(self) -> dict:
        return {
            "padding": {"type": "number", "default": 0, "min": 0, "label": "Padding (pixels)"},
        }
