"""Opt-in hardware loop: two local sources, reviewed export, training, and serving.

Set WALDO_E2E=1, WALDO_E2E_BASE_URL, WALDO_E2E_SOURCE_A, and
WALDO_E2E_SOURCE_B. Both sources must contain an object detectable with
WALDO_E2E_PROMPT (default "car"). Use an isolated live test stack with real
labeling and training workers. Distinct sources here do not form a model holdout.
"""

import hashlib
import io
import json
import os
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import numpy as np
import pytest


@pytest.mark.skipif(
    os.environ.get("WALDO_E2E") != "1",
    reason="Set WALDO_E2E=1 to run against an isolated live stack",
)
class TestFullLoopE2E:
    @pytest.fixture(autouse=True)
    def client(self, register_test_client):
        base_url = os.environ.get("WALDO_E2E_BASE_URL")
        assert base_url, "Set WALDO_E2E_BASE_URL to the isolated live test API"
        parsed_url = urlsplit(base_url)
        assert parsed_url.scheme == "http" and parsed_url.hostname in {"localhost", "127.0.0.1", "::1"}, (
            "WALDO_E2E_BASE_URL must point to a local isolated test API"
        )
        source_a = os.environ.get("WALDO_E2E_SOURCE_A")
        source_b = os.environ.get("WALDO_E2E_SOURCE_B")
        self.prompt = os.environ.get("WALDO_E2E_PROMPT", "car").strip()
        assert source_a, "Set WALDO_E2E_SOURCE_A to a trusted local known-positive clip"
        assert source_b, "Set WALDO_E2E_SOURCE_B to a different trusted local known-positive clip"
        assert self.prompt, "WALDO_E2E_PROMPT must name an object detectable in both clips"
        self.source_a, self.source_b = Path(source_a).expanduser(), Path(source_b).expanduser()
        assert self.source_a.is_file(), "WALDO_E2E_SOURCE_A must name an existing local file"
        assert self.source_b.is_file(), "WALDO_E2E_SOURCE_B must name an existing local file"
        assert self.source_a.resolve() != self.source_b.resolve(), "The two source paths must refer to different files"
        with self.source_a.open("rb") as first, self.source_b.open("rb") as second:
            assert hashlib.file_digest(first, "sha256").digest() != hashlib.file_digest(second, "sha256").digest(), (
                "The two source files must have different content"
            )
        self.client = register_test_client(httpx.Client(base_url=base_url, timeout=600))
        yield
        self.client.close()

    def test_full_pipeline(self):
        """Upload -> Label -> Train -> Activate -> Predict -> Export -> Video Predict."""

        source_a, source_b, prompt = self.source_a, self.source_b, self.prompt

        # Upload both distinct sources into one new project before labeling.
        with source_a.open("rb") as first, source_b.open("rb") as second:
            resp = self.client.post(
                "/api/v1/upload/batch",
                params={"project_name": f"e2e-full-{uuid.uuid4().hex}"},
                files=[
                    ("files", (f"source_a{source_a.suffix}", first, "application/octet-stream")),
                    ("files", (f"source_b{source_b.suffix}", second, "application/octet-stream")),
                ],
            )
        assert resp.status_code == 201, resp.text
        uploaded = resp.json()
        video_ids = {video["video_id"] for video in uploaded["videos"]}
        assert len(video_ids) == 2, "Batch upload must create two distinct source records"

        # One project job preserves both source IDs in one training export.
        resp = self.client.post(
            "/api/v1/label",
            json={
                "project_id": uploaded["project_id"],
                "text_prompt": prompt,
                "task_type": "segment",
                "fps": 1.0,
            },
        )
        assert resp.status_code == 202, resp.text
        job_id = resp.json()["job_id"]

        # Polling is bounded; a missing worker is a failure when opted in.
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            resp = self.client.get(f"/api/v1/status/{job_id}", timeout=10)
            assert resp.status_code == 200, resp.text
            status = resp.json()
            if status["status"] in ("completed", "failed", "partial"):
                break
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        else:
            pytest.fail(f"Labeling worker did not finish within 600 seconds: {status['status']}")
        assert status["status"] == "completed", f"Labeling failed: {status.get('error_message')}"

        resp = self.client.get(f"/api/v1/jobs/{job_id}/annotations", params={"limit": 10000})
        assert resp.status_code == 200, resp.text
        annotations = resp.json()
        observed_ids = {annotation["source_video_id"] for annotation in annotations}
        assert observed_ids == video_ids, "Both local clips must yield positive observations"
        for video_id in video_ids:
            annotation = next(annotation for annotation in annotations if annotation["source_video_id"] == video_id)
            reviewed = self.client.patch(f"/api/v1/annotations/{annotation['id']}", json={"status": "accepted"})
            assert reviewed.status_code == 200, reviewed.text
            assert reviewed.json()["status"] == "accepted"

        exported = self.client.post(f"/api/v1/jobs/{job_id}/export", json={"format": "segment"})
        assert exported.status_code == 200, exported.text
        assert exported.json()["format"] == "segment"
        download_url = exported.json()["download_url"]
        assert download_url.startswith("/api/v1/download/")
        downloaded = self.client.get(download_url, timeout=60)
        assert downloaded.status_code == 200, downloaded.text
        with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["split_strategy"] == "group"
            group_splits = {}
            for sample in manifest["samples"]:
                group_splits.setdefault(sample["group_id"], set()).add(sample["split"])
            assert set(group_splits) == video_ids, "Export must retain both source groups"
            assert {next(iter(splits)) for splits in group_splits.values()} == {"train", "val"}
            assert all(len(splits) == 1 for splits in group_splits.values())

        # Train one epoch from the reviewed export.
        model_name = f"e2e_test_model_{uuid.uuid4().hex}"
        resp = self.client.post(
            "/api/v1/train",
            json={
                "job_id": job_id,
                "name": model_name,
                "model_variant": "yolo11n-seg",
                "task_type": "segment",
                "hyperparameters": {"epochs": 1, "batch": 1},
            },
        )
        assert resp.status_code == 202, resp.text
        run_id = resp.json()["run_id"]

        # 5. Poll until training completes
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            resp = self.client.get(f"/api/v1/train/{run_id}", timeout=10)
            assert resp.status_code == 200, resp.text
            run_status = resp.json()
            if run_status["status"] in ("completed", "failed"):
                break
            time.sleep(min(5, max(0, deadline - time.monotonic())))
        else:
            pytest.fail(f"Training worker did not finish within 600 seconds: {run_status['status']}")
        assert run_status["status"] == "completed", f"Training failed: {run_status.get('error_message')}"

        # 6. Get model ID from models list
        resp = self.client.get("/api/v1/models")
        assert resp.status_code == 200
        models = [model for model in resp.json() if model["name"] == model_name]
        assert len(models) == 1, "Completed run must register its named model"
        model_id = models[0]["id"]

        # 7. Activate model
        resp = self.client.post(f"/api/v1/models/{model_id}/activate")
        assert resp.status_code == 200
        assert resp.json()["status"] == "activated"

        # 8. Check serve status
        resp = self.client.get("/api/v1/serve/status")
        assert resp.status_code == 200
        serve = resp.json()
        assert serve["loaded"] is True
        assert serve["model_id"] == model_id

        # 9. Predict on image
        # Create a test image
        test_img = np.random.randint(0, 255, (640, 640, 3), dtype=np.uint8)
        import cv2

        _, img_bytes = cv2.imencode(".jpg", test_img)
        resp = self.client.post(
            "/api/v1/predict/image",
            files={"file": ("test.jpg", io.BytesIO(img_bytes.tobytes()), "image/jpeg")},
        )
        assert resp.status_code == 200
        pred = resp.json()
        assert "detections" in pred
        assert "count" in pred
        assert pred["model_id"] == model_id

        # 10. Export to ONNX
        resp = self.client.post(
            f"/api/v1/models/{model_id}/export",
            json={"format": "onnx"},
        )
        assert resp.status_code == 202
        assert "task_id" in resp.json()

        # 11. Predict on video
        with source_a.open("rb") as f:
            resp = self.client.post(
                "/api/v1/predict/video",
                files={"file": (f"source_a{source_a.suffix}", f, "application/octet-stream")},
            )
        assert resp.status_code in (200, 202)
        video_pred = resp.json()

        if "frames" in video_pred:
            # Synchronous response (short video)
            assert video_pred["total_frames"] >= 1
            assert video_pred["model_id"] == model_id
            # Check tracking IDs exist
            for frame in video_pred["frames"]:
                assert "frame_index" in frame
                assert "timestamp_s" in frame
                assert "detections" in frame
        else:
            # Async response
            assert "session_id" in video_pred
            assert "celery_task_id" in video_pred
