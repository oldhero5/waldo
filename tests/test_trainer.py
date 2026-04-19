"""Tests for trainer module: dataset_builder, notifiers, metrics_streamer, train_manager."""

import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

from trainer.dataset_builder import prepare_dataset_dir
from trainer.metrics_streamer import get_latest_metrics, publish_metrics
from trainer.notifiers import _format_summary, notify_training_complete
from trainer.train_manager import (
    DEFAULT_HYPERPARAMS,
    TASK_TO_DEFAULT_VARIANT,
    VARIANTS,
)


class TestVariants:
    def test_all_task_types_have_defaults(self):
        for task in ("segment", "detect", "classify", "pose", "obb"):
            assert task in TASK_TO_DEFAULT_VARIANT
            default = TASK_TO_DEFAULT_VARIANT[task]
            assert default in VARIANTS

    def test_default_hyperparams_complete(self):
        required = ("epochs", "imgsz", "batch", "patience", "optimizer", "lr0")
        for key in required:
            assert key in DEFAULT_HYPERPARAMS

    def test_variant_count(self):
        assert len(VARIANTS) >= 10  # at least 10 variants


class TestDatasetBuilder:
    def test_prepare_dataset_dir(self):
        """Test extracting a dataset zip and fixing data.yaml path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)

            # Create a mock dataset zip
            zip_path = tmpdir / "source.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("data.yaml", "path: .\ntrain: images/train\nval: images/val\nnc: 1\nnames: ['car']\n")
                zf.writestr("images/train/f1.jpg", b"\xff\xd8")
                zf.writestr("labels/train/f1.txt", "0 0.5 0.5 0.6 0.5 0.6 0.6")

            # Upload to MinIO and test prepare
            from lib.storage import ensure_bucket, upload_file

            ensure_bucket()
            upload_file("test/dataset.zip", zip_path)

            work_dir = tmpdir / "work"
            work_dir.mkdir()
            result = prepare_dataset_dir("test/dataset.zip", work_dir)

            assert (result / "data.yaml").exists()
            assert (result / "images" / "train" / "f1.jpg").exists()
            assert (result / "labels" / "train" / "f1.txt").exists()

            # Check path was fixed
            yaml_content = (result / "data.yaml").read_text()
            assert str(result.resolve()) in yaml_content


class TestMetricsStreamer:
    def test_publish_and_retrieve(self):
        """Test metrics round-trip through Redis."""
        run_id = "test-metrics-run"
        metrics = {"epoch": 5, "loss": 0.123, "mAP50": 0.85}
        publish_metrics(run_id, metrics)

        latest = get_latest_metrics(run_id)
        assert latest is not None
        assert latest["epoch"] == 5
        assert latest["mAP50"] == 0.85

    def test_get_latest_nonexistent(self):
        result = get_latest_metrics("nonexistent-run-xyz")
        assert result is None


class TestNotifiers:
    def test_format_summary(self):
        summary = _format_summary("test_run", {"mAP50": 0.85, "loss": 0.05}, "abc-123")
        assert "test_run" in summary
        assert "mAP50" in summary
        assert "0.8500" in summary
        assert "abc-123" in summary

    def test_notify_no_config_returns_empty(self):
        """When no notification channels are configured, returns empty list."""
        with patch("trainer.notifiers.settings") as mock_settings:
            mock_settings.slack_webhook_url = ""
            mock_settings.ntfy_topic = ""
            mock_settings.smtp_host = ""
            mock_settings.alert_email = ""
            result = notify_training_complete("test", {"mAP50": 0.9}, "id-1")
            assert result == []

    @patch("trainer.notifiers.requests.post")
    def test_notify_slack(self, mock_post):
        with patch("trainer.notifiers.settings") as mock_settings:
            mock_settings.slack_webhook_url = "https://hooks.slack.com/test"
            mock_settings.ntfy_topic = ""
            mock_settings.smtp_host = ""
            mock_settings.alert_email = ""
            result = notify_training_complete("test_model", {"mAP50": 0.9}, "run-1")
            assert "slack" in result
            mock_post.assert_called_once()

    @patch("trainer.notifiers.requests.post")
    def test_notify_ntfy(self, mock_post):
        with patch("trainer.notifiers.settings") as mock_settings:
            mock_settings.slack_webhook_url = ""
            mock_settings.ntfy_topic = "waldo-alerts"
            mock_settings.ntfy_server = "https://ntfy.sh"
            mock_settings.smtp_host = ""
            mock_settings.alert_email = ""
            result = notify_training_complete("test_model", {}, "run-2")
            assert "ntfy" in result
            mock_post.assert_called_once()


class TestExporter:
    def test_supported_formats(self):
        from trainer.exporter import SUPPORTED_FORMATS

        assert "onnx" in SUPPORTED_FORMATS
        assert "tflite" in SUPPORTED_FORMATS
        assert "coreml" in SUPPORTED_FORMATS

    def test_unsupported_format_raises(self):
        from trainer.exporter import export_model

        with pytest.raises(ValueError, match="Unsupported format"):
            export_model("fake-id", "invalid_format")
