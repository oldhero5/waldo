"""Export trained YOLO models to various formats with validation."""
import tempfile
from pathlib import Path

import numpy as np

from lib.db import ModelRegistry, SessionLocal
from lib.storage import download_file, upload_file

SUPPORTED_FORMATS = ["onnx", "torchscript", "coreml", "tflite", "openvino"]


def _validate_export(original_model, export_path: str, fmt: str) -> bool:
    """Load exported model, run on test image, compare detection count to original."""
    from ultralytics import YOLO

    test_img = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)

    original_results = original_model(test_img, verbose=False)
    original_count = len(original_results[0].boxes) if original_results else 0

    exported_model = YOLO(export_path)
    exported_results = exported_model(test_img, verbose=False)
    exported_count = len(exported_results[0].boxes) if exported_results else 0

    # Both should produce similar results (same count on random noise, usually 0)
    return abs(original_count - exported_count) <= max(1, original_count // 2)


def export_model(model_id: str, fmt: str) -> str:
    """Export a registered model to the given format. Returns the MinIO key."""
    from ultralytics import YOLO

    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported format: {fmt}. Use one of: {SUPPORTED_FORMATS}")

    session = SessionLocal()
    try:
        model_entry = session.query(ModelRegistry).filter_by(id=model_id).one()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Download weights
            weights_path = tmpdir / "best.pt"
            download_file(model_entry.weights_minio_key, weights_path)

            # Export
            model = YOLO(str(weights_path))
            export_path = model.export(format=fmt)

            # Validate exported model
            if not _validate_export(model, export_path, fmt):
                raise RuntimeError(
                    f"Export validation failed for {fmt}: "
                    "exported model produces significantly different results"
                )

            # Upload exported model
            export_key = f"exports/{model_id}/{Path(export_path).name}"
            upload_file(export_key, export_path)

            # Update registry
            formats = model_entry.export_formats or {}
            formats[fmt] = export_key
            model_entry.export_formats = formats
            session.commit()

            return export_key
    finally:
        session.close()
