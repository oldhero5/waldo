"""Classical computer vision blocks — no ML required."""

from typing import Any

import cv2
import numpy as np

from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class GrayscaleBlock(BlockBase):
    name = "grayscale"
    display_name = "Grayscale"
    description = "Convert image to grayscale."
    category = "classical_cv"
    input_ports = [Port("image", "image", "Input image")]
    output_ports = [Port("image", "image", "Grayscale image")]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        image = inputs["image"]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray_3ch = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        return BlockResult(outputs={"image": gray_3ch})


class ResizeBlock(BlockBase):
    name = "resize"
    display_name = "Resize Image"
    description = "Resize image to a target width while maintaining aspect ratio."
    category = "transforms"
    input_ports = [Port("image", "image", "Input image")]
    output_ports = [
        Port("image", "image", "Resized image"),
        Port("scale", "number", "Scale factor applied"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        image = inputs["image"]
        target_width = self.config.get("width", 640)
        h, w = image.shape[:2]
        scale = target_width / w
        new_h = int(h * scale)
        resized = cv2.resize(image, (target_width, new_h))
        return BlockResult(
            outputs={"image": resized, "scale": scale},
            metadata={"original": f"{w}x{h}", "resized": f"{target_width}x{new_h}"},
        )

    def _config_schema(self) -> dict:
        return {"width": {"type": "number", "default": 640, "label": "Target width (px)"}}


class ContourDetectionBlock(BlockBase):
    name = "contours"
    display_name = "Find Contours"
    description = "Detect contours/edges in the image using classical CV."
    category = "classical_cv"
    input_ports = [Port("image", "image", "Input image")]
    output_ports = [
        Port("image", "image", "Image with contours drawn"),
        Port("count", "number", "Number of contours found"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        image = inputs["image"]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        thresh = self.config.get("threshold", 127)
        _, binary = cv2.threshold(blurred, thresh, 255, cv2.THRESH_BINARY)
        contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        result = image.copy()
        min_area = self.config.get("min_area", 100)
        filtered = [c for c in contours if cv2.contourArea(c) >= min_area]
        cv2.drawContours(result, filtered, -1, (0, 255, 0), 2)

        return BlockResult(
            outputs={"image": result, "count": len(filtered)},
            metadata={"total_contours": len(contours), "filtered": len(filtered)},
        )

    def _config_schema(self) -> dict:
        return {
            "threshold": {"type": "number", "default": 127, "label": "Binary threshold (0-255)"},
            "min_area": {"type": "number", "default": 100, "label": "Min contour area (px²)"},
        }


class DominantColorBlock(BlockBase):
    name = "dominant_color"
    display_name = "Dominant Color"
    description = "Extract the dominant color from an image or crop region."
    category = "classical_cv"
    input_ports = [Port("image", "image", "Input image")]
    output_ports = [
        Port("color_hex", "text", "Dominant color as hex string"),
        Port("color_rgb", "any", "Dominant color as [R, G, B]"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        image = inputs["image"]
        pixels = image.reshape(-1, 3).astype(np.float32)
        k = self.config.get("clusters", 3)
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
        _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 3, cv2.KMEANS_PP_CENTERS)
        # Find the most common cluster
        counts = np.bincount(labels.flatten())
        dominant = centers[counts.argmax()].astype(int)
        b, g, r = int(dominant[0]), int(dominant[1]), int(dominant[2])
        hex_color = f"#{r:02x}{g:02x}{b:02x}"
        return BlockResult(outputs={"color_hex": hex_color, "color_rgb": [r, g, b]}, metadata={"color": hex_color})

    def _config_schema(self) -> dict:
        return {"clusters": {"type": "number", "default": 3, "label": "K-means clusters"}}
