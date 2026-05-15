"""Tests for inference engine, video tracker, serve API, and export validation."""

from dataclasses import asdict

import pytest

from lib.inference_engine import Detection, FrameResult, InferenceEngine


class TestDetectionDataclass:
    def test_detection_fields(self):
        d = Detection(
            class_name="car",
            class_index=0,
            confidence=0.95,
            bbox=[10.0, 20.0, 100.0, 200.0],
        )
        assert d.class_name == "car"
        assert d.track_id is None
        assert d.mask is None

    def test_detection_with_track_id(self):
        d = Detection(
            class_name="person",
            class_index=1,
            confidence=0.8,
            bbox=[0, 0, 50, 50],
            track_id=5,
        )
        assert d.track_id == 5

    def test_detection_serializable(self):
        d = Detection(
            class_name="dog",
            class_index=2,
            confidence=0.7,
            bbox=[1.0, 2.0, 3.0, 4.0],
            mask=[[1.0, 2.0], [3.0, 4.0]],
        )
        data = asdict(d)
        assert data["class_name"] == "dog"
        assert len(data["mask"]) == 2


class TestFrameResult:
    def test_frame_result_defaults(self):
        fr = FrameResult(frame_index=0, timestamp_s=0.0)
        assert fr.detections == []

    def test_frame_result_with_detections(self):
        dets = [
            Detection("a", 0, 0.9, [0, 0, 1, 1]),
            Detection("b", 1, 0.8, [2, 2, 3, 3]),
        ]
        fr = FrameResult(frame_index=5, timestamp_s=0.5, detections=dets)
        assert len(fr.detections) == 2
        assert fr.frame_index == 5


class TestInferenceEngine:
    def test_engine_init(self):
        engine = InferenceEngine()
        assert engine.model is None
        assert engine.model_id is None

    def test_predict_without_loaded_model_raises(self):
        # predict_image is the public entry point; it must refuse to run when
        # no model has been loaded (instead of segfaulting in YOLO).
        import numpy as np

        engine = InferenceEngine()
        with pytest.raises(RuntimeError, match="no model loaded"):
            engine.predict_image(np.zeros((64, 64, 3), dtype=np.uint8))

    def test_clear_device_cache_no_error(self):
        engine = InferenceEngine()
        # Should not raise even if no GPU
        engine._clear_device_cache()


class TestVideoTracker:
    def test_validate_video_invalid(self):
        from lib.video_tracker import validate_video

        with pytest.raises(ValueError, match="Cannot open video"):
            validate_video("/nonexistent/video.mp4")

    def test_validate_video_corrupt_file(self, tmp_path):
        from lib.video_tracker import validate_video

        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"not a video")
        with pytest.raises(ValueError, match="Cannot open video"):
            validate_video(str(bad))


class TestNanDetection:
    def test_has_nan_true(self):
        from trainer.metrics_streamer import _has_nan

        assert _has_nan({"loss": float("nan")}) is True

    def test_has_nan_false(self):
        from trainer.metrics_streamer import _has_nan

        assert _has_nan({"loss": 0.5, "mAP": 0.9}) is False

    def test_has_nan_empty(self):
        from trainer.metrics_streamer import _has_nan

        assert _has_nan({}) is False

    def test_has_nan_non_numeric(self):
        from trainer.metrics_streamer import _has_nan

        assert _has_nan({"name": "test"}) is False


class TestExportValidation:
    def test_supported_formats(self):
        from trainer.exporter import SUPPORTED_FORMATS

        assert "onnx" in SUPPORTED_FORMATS
        assert "coreml" in SUPPORTED_FORMATS
        assert "tflite" in SUPPORTED_FORMATS

    def test_unsupported_format_raises(self):
        from trainer.exporter import export_model

        with pytest.raises(ValueError, match="Unsupported format"):
            export_model("fake-id", "invalid_format")


class TestServeAPI:
    """Test serve API endpoint schemas using test client."""

    def test_serve_status_endpoint(self):
        from app.api.serve import ServeStatus

        # Verify the response model
        assert ServeStatus.model_fields["loaded"].annotation is bool
        assert ServeStatus.model_fields["device"].annotation is str

    def test_detection_out_model(self):
        from app.api.serve import DetectionOut

        d = DetectionOut(
            class_name="car",
            class_index=0,
            confidence=0.9,
            bbox=[0, 0, 100, 100],
        )
        assert d.track_id is None
        assert d.mask is None

    def test_image_prediction_response_model(self):
        from app.api.serve import DetectionOut, ImagePredictionResponse

        resp = ImagePredictionResponse(
            detections=[
                DetectionOut(class_name="car", class_index=0, confidence=0.9, bbox=[0, 0, 100, 100]),
            ],
            model_id="test-id",
            count=1,
        )
        assert resp.count == 1
        assert resp.detections[0].class_name == "car"
