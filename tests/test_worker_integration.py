"""Opt-in service tests for the real text-labeling Celery task.

The worker uses deterministic results at the inference boundary. All upload,
queue, storage, persistence, review, and export paths remain real.
"""

import io
import json
import os
import time
import uuid
import zipfile

import pytest
from PIL import Image

pytestmark = pytest.mark.skipif(
    os.environ.get("WALDO_WORKER_INTEGRATION") != "1",
    reason="Set WALDO_WORKER_INTEGRATION=1 on a disposable service stack",
)


def _wait_for_status(client, job_id, wanted, timeout_s=3):
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/status/{job_id}")
        assert response.status_code == 200, response.text
        status = response.json()
        if status["status"] in wanted:
            return status
        time.sleep(0.2)
    raise TimeoutError(f"Job {job_id} did not reach {sorted(wanted)} in {timeout_s}s; last status: {status}")


def _upload(client, clip, name, project_name="default"):
    with clip.open("rb") as video:
        response = client.post(
            "/api/v1/upload",
            params={"project_name": project_name},
            files={"file": (name, video, "video/mp4")},
        )
    assert response.status_code == 201, response.text
    return response.json()


def _label(client, *, video_id=None, project_id=None):
    response = client.post(
        "/api/v1/label",
        json={"video_id": video_id, "project_id": project_id, "text_prompt": "test_object", "task_type": "segment"},
    )
    assert response.status_code == 202, response.text
    return response.json()["job_id"]


def _annotations(client, job_id):
    response = client.get(f"/api/v1/jobs/{job_id}/annotations", params={"limit": 1000})
    assert response.status_code == 200, response.text
    return response.json()


def test_uploaded_video_completes_only_when_real_worker_consumes_job(service_client, test_clip, worker_harness):
    worker_harness.start()
    uploaded = _upload(service_client, test_clip, "worker-positive.mp4")
    job_id = _label(service_client, video_id=uploaded["video_id"])
    finished = _wait_for_status(service_client, job_id, {"completed"}, timeout_s=60)
    assert finished["result_url"] is None


def test_missing_worker_fails_within_bounded_poll(service_client, test_clip, worker_queue):
    assert worker_queue
    uploaded = _upload(service_client, test_clip, "worker-absent.mp4")
    job_id = _label(service_client, video_id=uploaded["video_id"])
    start = time.monotonic()
    with pytest.raises(TimeoutError, match="did not reach"):
        _wait_for_status(service_client, job_id, {"completed"}, timeout_s=2)
    assert time.monotonic() - start < 5
    status = service_client.get(f"/api/v1/status/{job_id}").json()
    assert status["status"] == "pending"


def test_reviewed_geometry_is_exported_with_source_ids_and_pixels(
    service_client, register_test_client, test_clip, worker_harness
):
    worker_harness.start()
    uploaded = _upload(service_client, test_clip, "review-source.mp4")
    job_id = _label(service_client, video_id=uploaded["video_id"])
    finished = _wait_for_status(service_client, job_id, {"completed"}, timeout_s=60)
    assert finished["result_url"] is None
    observations = _annotations(service_client, job_id)
    assert len(observations) == finished["processed_frames"] > 0
    assert {item["source_video_id"] for item in observations} == {uploaded["video_id"]}
    assert all(item["timestamp_method"] == "resampled_ordinal/fps" for item in observations)
    assert all(item["bbox"] == [0.5, 0.5, 0.5, 0.5] for item in observations)
    assert all(item["confidence"] == pytest.approx(0.9) for item in observations)

    edited = observations[0]
    polygon = [0.1, 0.1, 0.4, 0.1, 0.4, 0.4, 0.1, 0.4]
    update = service_client.patch(
        f"/api/v1/annotations/{edited['id']}",
        json={
            "status": "accepted",
            "class_name": "reviewed_object",
            "polygon": polygon,
            "bbox": [0.25, 0.25, 0.3, 0.3],
        },
    )
    assert update.status_code == 200, update.text
    assert update.json()["polygon"] == polygon
    assert update.json()["status"] == "accepted"
    assert service_client.get(f"/api/v1/status/{job_id}").json()["result_url"] is None

    exported = service_client.post(f"/api/v1/jobs/{job_id}/export", json={"format": "segment"})
    assert exported.status_code == 200, exported.text
    archive_response = service_client.get(exported.json()["download_url"])
    assert archive_response.status_code == 200, archive_response.text
    with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["task"] == "segment"
        assert manifest["split_strategy"] == "group"
        assert len(manifest["samples"]) == len(observations)
        assert {sample["group_id"] for sample in manifest["samples"]} == {uploaded["video_id"]}
        sample = next(
            sample for sample in manifest["samples"] if sample["source_path"].endswith(f"/{edited['frame_id']}.jpg")
        )
        label = archive.read(sample["label_path"]).decode().strip()
        assert label.endswith(" ".join(f"{value:.6f}" for value in polygon))
        frame_response = service_client.get(edited["frame_url"])
        assert frame_response.status_code == 200
        image_bytes = archive.read(sample["image_path"])
        assert image_bytes == frame_response.content
        with Image.open(io.BytesIO(image_bytes)) as image:
            assert image.size == (160, 120)
            image.verify()

    from fastapi.testclient import TestClient

    from app.main import app

    foreign = register_test_client(TestClient(app))
    try:
        assert foreign.get(f"/api/v1/status/{job_id}").status_code == 404
        assert foreign.get(f"/api/v1/jobs/{job_id}/annotations").status_code == 404
        assert foreign.post(f"/api/v1/jobs/{job_id}/export", json={"format": "segment"}).status_code == 404
    finally:
        foreign.close()


def test_empty_inference_completes_with_zero_annotations_and_rejects_export(service_client, test_clip, worker_harness):
    (worker_harness.control / "mode").write_text("EMPTY")
    worker_harness.start()
    uploaded = _upload(service_client, test_clip, "worker-empty.mp4")
    job_id = _label(service_client, video_id=uploaded["video_id"])
    finished = _wait_for_status(service_client, job_id, {"completed"}, timeout_s=60)
    assert finished["processed_frames"] > 0
    assert finished["result_url"] is None
    assert _annotations(service_client, job_id) == []
    stats = service_client.get(f"/api/v1/jobs/{job_id}/stats")
    assert stats.status_code == 200, stats.text
    assert stats.json()["total_frames"] == finished["processed_frames"]
    assert stats.json()["empty_frames"] == finished["processed_frames"]
    assert stats.json()["total_annotations"] == 0
    exported = service_client.post(f"/api/v1/jobs/{job_id}/export", json={"format": "segment"})
    assert exported.status_code == 400


def test_inference_error_stays_failed(service_client, test_clip, worker_harness):
    (worker_harness.control / "mode").write_text("ERROR")
    worker_harness.start()
    uploaded = _upload(service_client, test_clip, "worker-error.mp4")
    job_id = _label(service_client, video_id=uploaded["video_id"])
    failed = _wait_for_status(service_client, job_id, {"failed"}, timeout_s=60)
    assert "test terminal inference error" in failed["error_message"]
    assert failed["result_url"] is None
    assert _annotations(service_client, job_id) == []
    time.sleep(1)
    assert service_client.get(f"/api/v1/status/{job_id}").json()["status"] == "failed"


def test_blocked_worker_times_out_then_completes_after_release(service_client, test_clip, worker_harness):
    uploaded = _upload(service_client, test_clip, "worker-blocked.mp4")
    block = worker_harness.control / f"block-{uploaded['video_id']}"
    block.touch()
    worker_harness.start()
    job_id = _label(service_client, video_id=uploaded["video_id"])
    start = time.monotonic()
    with pytest.raises(TimeoutError, match="did not reach"):
        _wait_for_status(service_client, job_id, {"completed"}, timeout_s=2)
    assert time.monotonic() - start < 5
    assert _annotations(service_client, job_id) == []
    block.unlink()
    assert _wait_for_status(service_client, job_id, {"completed"}, timeout_s=60)["processed_frames"] > 0


def test_partial_retry_keeps_committed_review_and_adds_second_clip_once(service_client, test_clip, worker_harness):
    project = f"worker-retry-{uuid.uuid4().hex}"
    first = _upload(service_client, test_clip, "first.mp4", project)
    second = _upload(service_client, test_clip, "second.mp4", project)
    assert first["project_id"] == second["project_id"]
    block = worker_harness.control / f"block-{second['video_id']}"
    block.touch()
    (worker_harness.control / f"transient-{second['video_id']}").touch()
    worker_harness.start()
    job_id = _label(service_client, project_id=first["project_id"])

    deadline = time.monotonic() + 45
    while time.monotonic() < deadline:
        status = service_client.get(f"/api/v1/status/{job_id}").json()
        clips = (status.get("processing_summary") or {}).get("videos", [])
        if len(clips) == 1 and clips[0]["video_id"] == first["video_id"] and clips[0]["status"] == "completed":
            break
        time.sleep(0.2)
    else:
        pytest.fail(f"First clip was not committed before second clip blocked: {status}")

    first_rows = [item for item in _annotations(service_client, job_id) if item["source_video_id"] == first["video_id"]]
    assert len(first_rows) == clips[0]["sampled_frames"] > 0
    edit = service_client.patch(
        f"/api/v1/annotations/{first_rows[0]['id']}",
        json={
            "status": "accepted",
            "class_name": "human_review",
            "bbox": [0.25, 0.25, 0.3, 0.3],
            "polygon": [0.1, 0.1, 0.4, 0.1, 0.4, 0.4, 0.1, 0.4],
        },
    )
    assert edit.status_code == 200, edit.text
    edited = edit.json()
    original_ids = {item["id"] for item in first_rows}
    block.unlink()
    finished = _wait_for_status(service_client, job_id, {"completed"}, timeout_s=90)
    assert (worker_harness.control / f"transient-fired-{second['video_id']}").exists()
    assert finished["result_url"] is None
    summary = finished["processing_summary"]
    assert len(summary["videos"]) == 2
    assert {item["video_id"] for item in summary["videos"]} == {first["video_id"], second["video_id"]}
    assert all(item["status"] == "completed" for item in summary["videos"])
    assert finished["processed_frames"] == sum(item["sampled_frames"] for item in summary["videos"])

    rows = _annotations(service_client, job_id)
    kept = {item["id"]: item for item in rows if item["source_video_id"] == first["video_id"]}
    added = [item for item in rows if item["source_video_id"] == second["video_id"]]
    assert set(kept) == original_ids
    assert len(added) == next(
        item["sampled_frames"] for item in summary["videos"] if item["video_id"] == second["video_id"]
    )
    assert all(item["id"] not in original_ids for item in added)
    assert kept[edited["id"]]["status"] == "accepted"
    assert kept[edited["id"]]["class_name"] == "human_review"
    assert kept[edited["id"]]["bbox"] == edited["bbox"]
    assert kept[edited["id"]]["polygon"] == edited["polygon"]
    exported = service_client.post(f"/api/v1/jobs/{job_id}/export", json={"format": "segment"})
    assert exported.status_code == 200, exported.text
    archive_response = service_client.get(exported.json()["download_url"])
    assert archive_response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive_response.content)) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert {sample["group_id"] for sample in manifest["samples"]} == {first["video_id"], second["video_id"]}
        sample = next(
            sample for sample in manifest["samples"] if sample["source_path"].endswith(f"/{edited['frame_id']}.jpg")
        )
        assert (
            archive.read(sample["label_path"])
            .decode()
            .strip()
            .endswith(" ".join(f"{value:.6f}" for value in edited["polygon"]))
        )


def test_graceful_worker_restart_and_completed_redelivery_preserve_review_export(
    service_client, test_clip, worker_harness
):
    worker_harness.start()
    uploaded = _upload(service_client, test_clip, "worker-redelivery.mp4")
    job_id = _label(service_client, video_id=uploaded["video_id"])
    _wait_for_status(service_client, job_id, {"completed"}, timeout_s=60)
    row = _annotations(service_client, job_id)[0]
    edit = service_client.patch(
        f"/api/v1/annotations/{row['id']}",
        json={"status": "accepted", "class_name": "restart_review"},
    )
    assert edit.status_code == 200, edit.text
    exported = service_client.post(f"/api/v1/jobs/{job_id}/export", json={"format": "segment"})
    assert exported.status_code == 200, exported.text
    before = service_client.get(f"/api/v1/status/{job_id}").json()
    assert before["result_url"]
    annotations_before = [
        {key: value for key, value in item.items() if key != "frame_url"}
        for item in _annotations(service_client, job_id)
    ]
    stats_before = service_client.get(f"/api/v1/jobs/{job_id}/stats").json()

    assert worker_harness.stop() == 0, "Celery did not stop gracefully after SIGTERM"
    (worker_harness.control / "mode").write_text("ERROR")
    from lib.tasks import label_video

    redelivery = label_video.delay(job_id)
    worker_harness.start()
    assert redelivery.get(timeout=60)["status"] == "completed"
    after = service_client.get(f"/api/v1/status/{job_id}").json()
    assert after["processing_summary"] == before["processing_summary"]
    assert after["processed_frames"] == before["processed_frames"]
    assert after["result_url"].split("?")[0] == before["result_url"].split("?")[0]
    assert service_client.get(after["result_url"]).status_code == 200
    assert [
        {key: value for key, value in item.items() if key != "frame_url"}
        for item in _annotations(service_client, job_id)
    ] == annotations_before
    assert service_client.get(f"/api/v1/jobs/{job_id}/stats").json() == stats_before
