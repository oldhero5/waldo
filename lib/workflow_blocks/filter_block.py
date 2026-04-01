"""Filter block — filter detections by confidence, class, or size."""
from typing import Any

from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class FilterBlock(BlockBase):
    name = "filter"
    display_name = "Filter Detections"
    description = "Filter detections by confidence threshold, class name, or bounding box size."
    category = "transforms"
    input_ports = [Port("detections", "detections", "Input detections")]
    output_ports = [
        Port("detections", "detections", "Filtered detections"),
        Port("count", "number", "Number of detections after filtering"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        detections = inputs["detections"]
        min_conf = self.config.get("min_confidence", 0.0)
        max_conf = self.config.get("max_confidence", 1.0)
        classes = self.config.get("classes", None)
        min_area = self.config.get("min_area", 0)

        filtered = []
        for d in detections:
            if d.confidence < min_conf or d.confidence > max_conf:
                continue
            if classes and d.class_name not in classes:
                continue
            w = d.bbox[2] - d.bbox[0]
            h = d.bbox[3] - d.bbox[1]
            if w * h < min_area:
                continue
            filtered.append(d)

        return BlockResult(
            outputs={"detections": filtered, "count": len(filtered)},
            metadata={"input_count": len(detections), "output_count": len(filtered)},
        )

    def _config_schema(self) -> dict:
        return {
            "min_confidence": {"type": "number", "default": 0.0, "min": 0, "max": 1, "label": "Min confidence"},
            "max_confidence": {"type": "number", "default": 1.0, "min": 0, "max": 1, "label": "Max confidence"},
            "classes": {"type": "string_list", "default": None, "label": "Allow only these classes"},
            "min_area": {"type": "number", "default": 0, "min": 0, "label": "Min bbox area (px²)"},
        }
