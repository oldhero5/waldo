"""Extended API tests for review, frames, and training endpoints.

Requires running infrastructure (Postgres, Redis, MinIO).
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture
def uploaded_video(client):
    """Upload a test video and return video_id."""
    from pathlib import Path

    clip = Path(__file__).parent / "fixtures" / "test_clip.mp4"
    with open(clip, "rb") as f:
        resp = client.post("/api/v1/upload", files={"file": ("test.mp4", f, "video/mp4")})
    assert resp.status_code == 201
    return resp.json()["video_id"]


@pytest.fixture
def completed_job(client, uploaded_video):
    """Create and wait for a labeling job to complete. Returns job_id.

    Requires a running labeler Celery worker. When no worker is processing
    the queue (e.g. in CI without the full stack), the job stays in
    'pending' forever — skip the dependent test instead of failing.
    """
    import time

    resp = client.post(
        "/api/v1/label",
        json={
            "video_id": uploaded_video,
            "text_prompt": "test_object",
            "task_type": "segment",
        },
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    status = {"status": "pending"}
    for _ in range(30):
        status = client.get(f"/api/v1/status/{job_id}").json()
        if status["status"] in ("completed", "failed"):
            break
        time.sleep(1)
    if status["status"] not in ("completed", "failed"):
        pytest.skip("No labeler worker is processing jobs (set up the full stack to run this test)")
    assert status["status"] == "completed"
    return job_id


class TestReviewAPI:
    def test_list_annotations(self, client, completed_job):
        resp = client.get(f"/api/v1/jobs/{completed_job}/annotations")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_annotations_404(self, client):
        resp = client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000/annotations")
        assert resp.status_code == 404

    def test_get_stats(self, client, completed_job):
        resp = client.get(f"/api/v1/jobs/{completed_job}/stats")
        assert resp.status_code == 200
        stats = resp.json()
        assert "total_annotations" in stats
        assert "total_frames" in stats
        assert "by_status" in stats
        assert stats["total_frames"] >= 1

    def test_get_stats_404(self, client):
        resp = client.get("/api/v1/jobs/00000000-0000-0000-0000-000000000000/stats")
        assert resp.status_code == 404

    def test_patch_annotation_404(self, client):
        resp = client.patch(
            "/api/v1/annotations/00000000-0000-0000-0000-000000000000",
            json={"status": "accepted"},
        )
        assert resp.status_code == 404


class TestFramesAPI:
    def test_list_frames(self, client, uploaded_video, completed_job):
        resp = client.get(f"/api/v1/videos/{uploaded_video}/frames")
        assert resp.status_code == 200
        frames = resp.json()
        assert len(frames) >= 1
        assert "image_url" in frames[0]
        assert "frame_number" in frames[0]

    def test_list_frames_404(self, client):
        resp = client.get("/api/v1/videos/00000000-0000-0000-0000-000000000000/frames")
        assert resp.status_code == 404

    def test_get_frame_404(self, client):
        resp = client.get("/api/v1/frames/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_get_frame_detail(self, client, uploaded_video, completed_job):
        frames = client.get(f"/api/v1/videos/{uploaded_video}/frames").json()
        if frames:
            resp = client.get(f"/api/v1/frames/{frames[0]['id']}")
            assert resp.status_code == 200
            detail = resp.json()
            assert "annotations" in detail
            assert "image_url" in detail


class TestTrainAPI:
    def test_get_variants(self, client):
        resp = client.get("/api/v1/train/variants")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["variants"]) >= 10
        assert "segment" in body["defaults"]
        assert "epochs" in body["hyperparams"]

    def test_list_training_runs(self, client):
        resp = client.get("/api/v1/train")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_models(self, client):
        resp = client.get("/api/v1/models")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_start_training_job_not_found(self, client):
        resp = client.post(
            "/api/v1/train",
            json={
                "job_id": "00000000-0000-0000-0000-000000000000",
                "name": "test",
            },
        )
        assert resp.status_code == 404

    def test_start_training(self, client, completed_job):
        resp = client.post(
            "/api/v1/train",
            json={
                "job_id": completed_job,
                "name": "pytest_train",
                "model_variant": "yolo11n-seg",
                "task_type": "segment",
                "hyperparameters": {"epochs": 1},
            },
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["run_id"]
        assert body["status"] == "queued"

        # Check status endpoint
        status_resp = client.get(f"/api/v1/train/{body['run_id']}")
        assert status_resp.status_code == 200
        status = status_resp.json()
        assert status["name"] == "pytest_train"
        assert status["model_variant"] == "yolo11n-seg"

    def test_get_training_run_404(self, client):
        resp = client.get("/api/v1/train/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_export_model_404(self, client):
        resp = client.post(
            "/api/v1/models/00000000-0000-0000-0000-000000000000/export",
            json={"format": "onnx"},
        )
        assert resp.status_code == 404

    def test_label_with_detect_task(self, client, uploaded_video):
        """Test labeling with detection task type."""
        import time

        resp = client.post(
            "/api/v1/label",
            json={
                "video_id": uploaded_video,
                "text_prompt": "object",
                "task_type": "detect",
            },
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        status = {"status": "pending"}
        for _ in range(30):
            status = client.get(f"/api/v1/status/{job_id}").json()
            if status["status"] in ("completed", "failed"):
                break
            time.sleep(1)
        if status["status"] not in ("completed", "failed"):
            pytest.skip("No labeler worker is processing jobs (set up the full stack to run this test)")
        assert status["status"] == "completed"
