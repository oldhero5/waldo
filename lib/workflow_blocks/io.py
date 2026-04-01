"""I/O blocks — image input, video input, JSON output."""
from typing import Any

import cv2
import numpy as np

from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class ImageInputBlock(BlockBase):
    name = "image_input"
    display_name = "Image Input"
    description = "Accept an image as workflow input."
    category = "io"
    input_ports = []
    output_ports = [
        Port("image", "image", "Decoded image as numpy array"),
        Port("width", "number", "Image width"),
        Port("height", "number", "Image height"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        # The image is passed in via workflow execution context, not ports
        image = inputs.get("__image__")
        if image is None:
            raise ValueError("No image provided to workflow")

        if isinstance(image, bytes):
            nparr = np.frombuffer(image, np.uint8)
            image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        h, w = image.shape[:2]
        return BlockResult(
            outputs={"image": image, "width": w, "height": h},
            metadata={"width": w, "height": h},
        )


class OutputBlock(BlockBase):
    name = "output"
    display_name = "Output"
    description = "Collect workflow results for the API response."
    category = "io"
    input_ports = [Port("data", "any", "Data to include in the output")]
    output_ports = []

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        from dataclasses import asdict

        data = inputs.get("data")

        # Serialize detections if present
        if isinstance(data, list) and len(data) > 0 and hasattr(data[0], "bbox"):
            data = [asdict(d) for d in data]

        return BlockResult(
            outputs={"__result__": data},
            metadata={},
        )
