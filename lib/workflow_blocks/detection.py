"""Detection block — runs YOLO object detection on an image."""

from typing import Any

from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class DetectionBlock(BlockBase):
    name = "detection"
    display_name = "Object Detection"
    description = "Detect objects in an image using the active YOLO model in the current workspace."
    category = "models"
    input_ports = [Port("image", "image", "Input image (numpy array)")]
    output_ports = [
        Port("detections", "detections", "List of detected objects with bboxes and classes"),
        Port("image", "image", "Original image (passthrough)"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        from lib.workflow_blocks.platform import get_workspace_model_engine

        image = inputs["image"]
        conf = self.config.get("confidence", 0.25)
        class_filter = self.config.get("class_filter", None)

        engine = get_workspace_model_engine(self)
        detections = engine.predict_image(image, conf=conf, class_filter=class_filter)

        return BlockResult(
            outputs={
                "detections": detections,
                "image": image,
            },
            metadata={"detection_count": len(detections)},
        )

    def _config_schema(self) -> dict:
        return {
            "confidence": {"type": "number", "default": 0.25, "min": 0, "max": 1, "label": "Confidence threshold"},
            "class_filter": {"type": "string_list", "default": None, "label": "Filter to classes (optional)"},
        }
