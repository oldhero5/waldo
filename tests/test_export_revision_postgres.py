"""Committed two-session export races on an opt-in disposable PostgreSQL DB."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient

from app.api import review
from app.main import app
from lib.db import Annotation, Frame, LabelingJob, Project, Video


@pytest.mark.parametrize("task_type", ["detect", "detect_transformer"])
def test_postgres_edit_during_export_cannot_republish_stale_snapshot(service_client, monkeypatch, task_type):
    client = service_client
    workspace_id = client.get("/api/v1/auth/me").json()["workspace_id"]
    with review.SessionLocal() as session:
        project = Project(workspace_id=workspace_id, name="Revision race")
        session.add(project)
        session.flush()
        video = Video(project_id=project.id, filename="race.mp4", minio_key="videos/race.mp4")
        session.add(video)
        session.flush()
        frame = Frame(video_id=video.id, frame_number=0, timestamp_s=0, minio_key="frames/race.jpg")
        job = LabelingJob(project_id=project.id, video_id=video.id, status="completed", task_type=task_type)
        session.add_all([frame, job])
        session.flush()
        annotation = Annotation(
            job_id=job.id,
            frame_id=frame.id,
            class_name="camera",
            class_index=0,
            polygon=[],
            bbox=[0.5, 0.5, 0.2, 0.2],
            status="accepted",
        )
        session.add(annotation)
        session.commit()
        job_id, annotation_id = job.id, annotation.id

    uploading = Event()
    continue_upload = Event()
    uploaded, deleted = [], []

    def pause_upload(key, path):
        uploaded.append(key)
        uploading.set()
        assert continue_upload.wait(timeout=15), "edit never released the export"

    monkeypatch.setattr(review, "download_file", lambda key, path: path.write_bytes(b"image"))
    monkeypatch.setattr(review, "upload_file", pause_upload)
    monkeypatch.setattr(review, "delete_object", deleted.append)
    monkeypatch.setattr(review, "get_download_url", lambda key: f"/media/{key}")

    # Each request creates and commits its own SessionLocal / PostgreSQL connection.
    with TestClient(app) as export_client, ThreadPoolExecutor(max_workers=1) as workers:
        export_client.headers.update(client.headers)
        exporting = workers.submit(export_client.post, f"/api/v1/jobs/{job_id}/export", json={"format": "detect"})
        try:
            assert uploading.wait(timeout=15), "export did not reach upload"
            edit = client.patch(
                f"/api/v1/annotations/{annotation_id}",
                json={"bbox": [0.5, 0.5, 0.3, 0.3]},
            )
            assert edit.status_code == 200, edit.text
        finally:
            continue_upload.set()
        result = exporting.result(timeout=15)

    assert result.status_code == 409, result.text
    assert len(uploaded) == 1
    assert deleted == uploaded
    with review.SessionLocal() as session:
        current = session.get(LabelingJob, job_id)
        changed = session.get(Annotation, annotation_id)
        assert current.evidence_revision == 1
        assert current.result_minio_key is None
        assert changed.bbox == [0.5, 0.5, 0.3, 0.3]


@pytest.mark.parametrize("task_type", ["detect", "detect_transformer"])
def test_postgres_edit_waits_for_published_export_then_invalidates_it(service_client, monkeypatch, task_type):
    client = service_client
    workspace_id = client.get("/api/v1/auth/me").json()["workspace_id"]
    with review.SessionLocal() as session:
        project = Project(workspace_id=workspace_id, name="Publication race")
        session.add(project)
        session.flush()
        video = Video(project_id=project.id, filename="publish.mp4", minio_key="videos/publish.mp4")
        session.add(video)
        session.flush()
        frame = Frame(video_id=video.id, frame_number=0, timestamp_s=0, minio_key="frames/publish.jpg")
        job = LabelingJob(project_id=project.id, video_id=video.id, status="completed", task_type=task_type)
        session.add_all([frame, job])
        session.flush()
        annotation = Annotation(
            job_id=job.id,
            frame_id=frame.id,
            class_name="camera",
            class_index=0,
            polygon=[],
            bbox=[0.5, 0.5, 0.2, 0.2],
            status="accepted",
        )
        session.add(annotation)
        session.commit()
        job_id, annotation_id = job.id, annotation.id

    published = Event()
    continue_publish = Event()
    invalidation_started = Event()
    actual_publish = review.publish_current_export
    actual_invalidate = review.invalidate_current_export
    uploaded = []

    def pause_after_compare_and_set(session, job_id, expected_revision, key):
        result = actual_publish(session, job_id, expected_revision, key)
        assert result
        published.set()
        assert continue_publish.wait(timeout=15), "edit never reached invalidation"
        return result

    def signal_invalidation(session, job_id):
        invalidation_started.set()
        return actual_invalidate(session, job_id)

    monkeypatch.setattr(review, "download_file", lambda key, path: path.write_bytes(b"image"))
    monkeypatch.setattr(review, "upload_file", lambda key, path: uploaded.append(key))
    monkeypatch.setattr(review, "get_download_url", lambda key: f"/media/{key}")
    monkeypatch.setattr(review, "publish_current_export", pause_after_compare_and_set)
    monkeypatch.setattr(review, "invalidate_current_export", signal_invalidation)

    with TestClient(app) as export_client, ThreadPoolExecutor(max_workers=2) as workers:
        export_client.headers.update(client.headers)
        exporting = workers.submit(export_client.post, f"/api/v1/jobs/{job_id}/export", json={"format": "detect"})
        try:
            assert published.wait(timeout=15), "export did not publish its compare-and-set"
            editing = workers.submit(
                client.patch,
                f"/api/v1/annotations/{annotation_id}",
                json={"bbox": [0.5, 0.5, 0.4, 0.4]},
            )
            assert invalidation_started.wait(timeout=15), "edit did not reach invalidation"
        finally:
            continue_publish.set()
        export_result = exporting.result(timeout=15)
        edit_result = editing.result(timeout=15)

    assert export_result.status_code == 200, export_result.text
    assert edit_result.status_code == 200, edit_result.text
    assert len(uploaded) == 1
    with review.SessionLocal() as session:
        current = session.get(LabelingJob, job_id)
        changed = session.get(Annotation, annotation_id)
        assert current.evidence_revision == 1
        assert current.result_minio_key is None
        assert changed.bbox == [0.5, 0.5, 0.4, 0.4]
