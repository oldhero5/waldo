"""Specialized CV blocks — OCR, counting, zone analysis."""

from typing import Any

from lib.workflow_blocks.base import BlockBase, BlockResult, Port


class OCRBlock(BlockBase):
    name = "ocr"
    display_name = "Text Recognition (OCR)"
    description = "Extract text from images using optical character recognition."
    category = "models"
    input_ports = [Port("image", "image", "Input image or crop")]
    output_ports = [
        Port("text", "text", "Recognized text"),
        Port("regions", "any", "Text region bounding boxes"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        import cv2

        image = inputs["image"]
        lang = self.config.get("language", "eng")

        # Use EasyOCR if available, else fall back to Tesseract
        try:
            import easyocr

            reader = easyocr.Reader([lang[:2]], gpu=False)
            results = reader.readtext(image)
            text = "\n".join([r[1] for r in results])
            regions = [
                {"bbox": [int(c) for p in r[0] for c in p], "text": r[1], "confidence": float(r[2])} for r in results
            ]
        except ImportError:
            # Fallback: simple thresholding + contour-based text detection
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            regions = [
                {"bbox": list(cv2.boundingRect(c)), "text": "?", "confidence": 0.0}
                for c in contours
                if cv2.contourArea(c) > 100
            ]
            text = f"[{len(regions)} text regions detected — install easyocr for full OCR]"

        return BlockResult(
            outputs={"text": text, "regions": regions},
            metadata={"region_count": len(regions)},
        )

    def _config_schema(self) -> dict:
        return {"language": {"type": "string", "default": "eng", "label": "Language (eng, fra, deu, etc.)"}}


class LineCounterBlock(BlockBase):
    name = "line_counter"
    display_name = "Line Counter"
    description = "Count objects crossing a virtual line in the image."
    category = "logic"
    input_ports = [
        Port("detections", "detections", "Tracked detections with track_id"),
    ]
    output_ports = [
        Port("count", "number", "Number of line crossings"),
        Port("detections", "detections", "Pass-through detections"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        detections = inputs["detections"]
        line_y = self.config.get("line_y", 0.5)  # Normalized Y position (0-1)
        _direction = self.config.get("direction", "down")  # Reserved for future directional filtering

        # Simple heuristic: count detections whose center is near the line
        threshold = self.config.get("threshold", 0.05)  # 5% of image height
        crossings = 0

        for det in detections:
            cy = (det.bbox[1] + det.bbox[3]) / 2
            # Normalize if bbox is in pixels (assume 1080p for now)
            if cy > 1:
                cy = cy / 1080.0
            if abs(cy - line_y) < threshold:
                crossings += 1

        return BlockResult(
            outputs={"count": crossings, "detections": detections},
            metadata={"line_y": line_y, "crossings": crossings},
        )

    def _config_schema(self) -> dict:
        return {
            "line_y": {
                "type": "number",
                "default": 0.5,
                "min": 0,
                "max": 1,
                "label": "Line Y position (0=top, 1=bottom)",
            },
            "direction": {"type": "string", "default": "both", "label": "Direction (up, down, both)"},
            "threshold": {"type": "number", "default": 0.05, "label": "Crossing threshold"},
        }


class ZoneCounterBlock(BlockBase):
    name = "zone_counter"
    display_name = "Zone Counter"
    description = "Count objects within a rectangular zone."
    category = "logic"
    input_ports = [Port("detections", "detections", "Input detections")]
    output_ports = [
        Port("in_zone", "number", "Count inside zone"),
        Port("outside_zone", "number", "Count outside zone"),
        Port("detections", "detections", "Detections inside zone only"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        detections = inputs["detections"]
        # Zone defined as normalized coordinates (0-1)
        zx1 = self.config.get("zone_x1", 0.2)
        zy1 = self.config.get("zone_y1", 0.2)
        zx2 = self.config.get("zone_x2", 0.8)
        zy2 = self.config.get("zone_y2", 0.8)

        inside = []
        outside_count = 0

        for det in detections:
            cx = (det.bbox[0] + det.bbox[2]) / 2
            cy = (det.bbox[1] + det.bbox[3]) / 2
            # Normalize if in pixels
            if cx > 1:
                cx = cx / 1920.0
            if cy > 1:
                cy = cy / 1080.0

            if zx1 <= cx <= zx2 and zy1 <= cy <= zy2:
                inside.append(det)
            else:
                outside_count += 1

        return BlockResult(
            outputs={"in_zone": len(inside), "outside_zone": outside_count, "detections": inside},
            metadata={"zone": f"({zx1},{zy1})-({zx2},{zy2})", "in": len(inside), "out": outside_count},
        )

    def _config_schema(self) -> dict:
        return {
            "zone_x1": {"type": "number", "default": 0.2, "min": 0, "max": 1, "label": "Zone left (0-1)"},
            "zone_y1": {"type": "number", "default": 0.2, "min": 0, "max": 1, "label": "Zone top (0-1)"},
            "zone_x2": {"type": "number", "default": 0.8, "min": 0, "max": 1, "label": "Zone right (0-1)"},
            "zone_y2": {"type": "number", "default": 0.8, "min": 0, "max": 1, "label": "Zone bottom (0-1)"},
        }


class LicensePlateBlock(BlockBase):
    name = "license_plate"
    display_name = "License Plate Reader"
    description = "Detect license plates and extract text. Chains detection + OCR."
    category = "models"
    input_ports = [Port("image", "image", "Input image")]
    output_ports = [
        Port("plates", "any", "List of {bbox, text, confidence}"),
        Port("count", "number", "Number of plates found"),
    ]

    def execute(self, inputs: dict[str, Any]) -> BlockResult:
        from lib.inference_engine import get_engine

        image = inputs["image"]
        conf = self.config.get("confidence", 0.3)

        # Step 1: Detect objects (license plates should be a trained class)
        engine = get_engine()
        detections = engine.predict_image(image, conf=conf)

        # Step 2: For each detection, crop and attempt OCR
        plates = []
        for det in detections:
            x1, y1, x2, y2 = (int(v) for v in det.bbox)
            crop = image[max(0, y1) : y2, max(0, x1) : x2]
            if crop.size == 0:
                continue

            # Simple text extraction attempt
            text = f"[plate:{det.class_name}]"
            try:
                import easyocr

                reader = easyocr.Reader(["en"], gpu=False)
                results = reader.readtext(crop)
                if results:
                    text = " ".join([r[1] for r in results])
            except ImportError:
                pass

            plates.append(
                {
                    "bbox": det.bbox,
                    "text": text,
                    "confidence": det.confidence,
                    "class": det.class_name,
                }
            )

        return BlockResult(
            outputs={"plates": plates, "count": len(plates)},
            metadata={"plates_found": len(plates)},
        )

    def _config_schema(self) -> dict:
        return {"confidence": {"type": "number", "default": 0.3, "min": 0, "max": 1, "label": "Detection confidence"}}
