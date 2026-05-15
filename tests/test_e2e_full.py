"""
Full-loop E2E test: Upload -> Label -> Train (1 epoch) -> Activate -> Predict image -> Export ONNX -> Predict video.

Requires:
- Running infrastructure (make infra)
- Running app (make dev-app)
- Running labeler + trainer workers
- SAM 3 model downloaded

This test hits a live HTTP server at BASE_URL — opt in with WALDO_E2E=1.
"""

import io
import os
import time

import httpx
import numpy as np
import pytest

BASE_URL = "http://localhost:8000"


@pytest.mark.skipif(
    os.environ.get("WALDO_E2E") != "1",
    reason="Set WALDO_E2E=1 to run against a live stack",
)
class TestFullLoopE2E:
    @pytest.fixture(autouse=True)
    def client(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=600)
        yield
        self.client.close()

    def test_full_pipeline(self, test_clip):
        """Upload -> Label -> Train -> Activate -> Predict -> Export -> Video Predict."""

        # 1. Upload video
        with open(test_clip, "rb") as f:
            resp = self.client.post(
                "/api/v1/upload",
                files={"file": ("test_clip.mp4", f, "video/mp4")},
            )
        assert resp.status_code == 201
        video_id = resp.json()["video_id"]

        # 2. Start labeling
        resp = self.client.post(
            "/api/v1/label",
            json={"video_id": video_id, "text_prompt": "car", "task_type": "segment"},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # 3. Poll until labeling completes
        for _ in range(120):
            resp = self.client.get(f"/api/v1/status/{job_id}")
            status = resp.json()
            if status["status"] in ("completed", "failed"):
                break
            time.sleep(5)
        assert status["status"] == "completed", f"Labeling failed: {status.get('error_message')}"

        # 4. Start training (1 epoch)
        resp = self.client.post(
            "/api/v1/train",
            json={
                "job_id": job_id,
                "name": "e2e_test_model",
                "model_variant": "yolo11n-seg",
                "task_type": "segment",
                "hyperparameters": {"epochs": 1, "batch": 1},
            },
        )
        assert resp.status_code == 202
        run_id = resp.json()["run_id"]

        # 5. Poll until training completes
        for _ in range(120):
            resp = self.client.get(f"/api/v1/train/{run_id}")
            run_status = resp.json()
            if run_status["status"] in ("completed", "failed"):
                break
            time.sleep(5)
        assert run_status["status"] == "completed", f"Training failed: {run_status.get('error_message')}"

        # 6. Get model ID from models list
        resp = self.client.get("/api/v1/models")
        assert resp.status_code == 200
        models = resp.json()
        assert len(models) >= 1
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
        with open(test_clip, "rb") as f:
            resp = self.client.post(
                "/api/v1/predict/video",
                files={"file": ("test_clip.mp4", f, "video/mp4")},
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
