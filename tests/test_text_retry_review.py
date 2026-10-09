"""Text labeling retries preserve committed observations and human review."""

import copy
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from labeler.errors import RetryableLabelingError
from lib.db import Annotation, Base, LabelingJob, Project, Video
from tests.test_video_evidence_regressions import _load_subject

pytestmark = pytest.mark.no_auth_bypass


@pytest.fixture
def text_run(monkeypatch):
    monkeypatch.setitem(sys.modules, "labeler.sam3_engine", SimpleNamespace(SegmentationResult=SimpleNamespace))
    pipeline = _load_subject("labeler.pipeline")
    monkeypatch.setitem(sys.modules, "labeler.pipeline", pipeline)
    # The model boundary is injected; no checkpoint is loaded or downloaded.
    sys.modules["labeler.sam3_engine"].get_engine = lambda: None
    subject = _load_subject("labeler.text_labeler")
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)

    class TestSession(Session):
        pass

    sessions = sessionmaker(bind=database, class_=TestSession)
    with sessions() as session:
        project = Project(name="synthetic")
        session.add(project)
        session.flush()
        videos = [Video(project_id=project.id, filename=f"{i}.mp4", minio_key=f"source-{i}") for i in range(2)]
        job = LabelingJob(
            project_id=project.id, text_prompt="camera", task_type="detect", sample_fps=2, score_threshold=0.2
        )
        session.add_all([*videos, job])
        session.commit()
        job_id, video_ids = job.id, [video.id for video in videos]
    downloads, uploads = [], []

    def download(key, path):
        downloads.append(key)
        if key == "source-1" and downloads.count(key) == 1:
            raise ConnectionError("temporary storage outage")
        path.write_bytes(b"source")

    def extract(source, directory, **kwargs):
        directory.mkdir()
        path = directory / "frame.png"
        Image.new("RGB", (8, 8)).save(path)
        return [SimpleNamespace(file_path=path, frame_number=0, timestamp_s=0, phash="", width=8, height=8)]

    model = SimpleNamespace(
        iter_segment_frame_paths=lambda *a, **kw: (
            item
            for item in [
                SimpleNamespace(
                    frame_index=0,
                    masks=np.ones((3, 8, 8), dtype=bool),
                    boxes=np.array([[1, 1, 7, 7]] * 3),
                    scores=np.array([0.9] * 3),
                    class_indices=np.array([0] * 3),
                )
            ]
        )
    )
    monkeypatch.setattr(subject, "SessionLocal", sessions)
    monkeypatch.setattr(subject, "get_engine", lambda: model)
    monkeypatch.setattr(subject, "download_file", download)
    monkeypatch.setattr(subject, "extract_frames", extract)
    monkeypatch.setattr(subject, "upload_file", lambda key, path: uploads.append(key))
    monkeypatch.setattr(pipeline, "upload_file", lambda key, path: uploads.append(key))
    yield SimpleNamespace(
        subject=subject, sessions=sessions, job_id=job_id, video_ids=video_ids, downloads=downloads, uploads=uploads
    )
    database.dispose()


def _first_attempt(run):
    with pytest.raises(RetryableLabelingError, match="temporary storage"):
        run.subject.run_labeling_pipeline(MagicMock(), run.job_id)


def _rows(session, job_id):
    return {
        row.id: (row.status, row.class_name, row.polygon, row.bbox)
        for row in session.query(Annotation).filter_by(job_id=job_id)
    }


def test_retry_preserves_accepted_rejected_and_pending_edits_without_automatic_export(text_run):
    _first_attempt(text_run)
    with text_run.sessions() as session:
        rows = session.query(Annotation).filter_by(job_id=text_run.job_id).all()
        for row, status in zip(rows, ["accepted", "rejected", "pending"], strict=True):
            row.status, row.class_name = status, f"edited-{status}"
            row.polygon, row.bbox = [0, 0, 0.5, 0, 0.5, 0.5], [0.25, 0.25, 0.5, 0.5]
        session.get(LabelingJob, text_run.job_id).result_minio_key = "partial-reviewed-export.zip"
        session.commit()
        original = _rows(session, text_run.job_id)
    result = text_run.subject.run_labeling_pipeline(MagicMock(), text_run.job_id)
    assert result["status"] == "completed"
    assert result["result_minio_key"] is None
    assert text_run.downloads == ["source-0", "source-1", "source-1"]
    assert all(key.startswith("frames/") for key in text_run.uploads)
    with text_run.sessions() as session:
        after = _rows(session, text_run.job_id)
        assert {key: after[key] for key in original} == original
        assert len(after) == 6
        job = session.get(LabelingJob, text_run.job_id)
        assert job.result_minio_key is None
        assert job.total_frames == job.processed_frames == 2
        assert len(job.processing_summary["videos"]) == 2


@pytest.mark.parametrize("artifact", [None, "reviewed-export.zip"])
def test_completed_redelivery_keeps_review_and_export_without_processing(text_run, monkeypatch, artifact):
    _first_attempt(text_run)
    text_run.subject.run_labeling_pipeline(MagicMock(), text_run.job_id)
    with text_run.sessions() as session:
        job = session.get(LabelingJob, text_run.job_id)
        job.result_minio_key = artifact
        row = session.query(Annotation).filter_by(job_id=job.id).first()
        row.class_name, row.status = "manual-edit", "accepted"
        session.commit()
        original, summary = _rows(session, job.id), copy.deepcopy(job.processing_summary)
    monkeypatch.setattr(text_run.subject, "get_engine", lambda: pytest.fail("completed job loaded model"))
    assert text_run.subject.run_labeling_pipeline(MagicMock(), text_run.job_id)["result_minio_key"] == artifact
    with text_run.sessions() as session:
        assert _rows(session, text_run.job_id) == original
        assert session.get(LabelingJob, text_run.job_id).processing_summary == summary


@pytest.mark.parametrize("field,value", [("sample_fps", 3), ("score_threshold", 0.4)])
def test_partial_retry_rejects_changed_effective_configuration_before_processing(text_run, field, value):
    _first_attempt(text_run)
    with text_run.sessions() as session:
        job = session.get(LabelingJob, text_run.job_id)
        setattr(job, field, value)
        session.commit()
        original, summary = _rows(session, job.id), copy.deepcopy(job.processing_summary)
    downloads = list(text_run.downloads)
    with pytest.raises(ValueError, match="Retry configuration differs"):
        text_run.subject.run_labeling_pipeline(MagicMock(), text_run.job_id)
    assert text_run.downloads == downloads
    with text_run.sessions() as session:
        assert _rows(session, text_run.job_id) == original
        assert session.get(LabelingJob, text_run.job_id).processing_summary == summary


def test_clip_observations_and_completed_summary_rollback_together(text_run):
    failed = False

    def fail_clip_commit(session):
        nonlocal failed
        job = session.get(LabelingJob, text_run.job_id)
        if not failed and any(
            entry["status"] == "completed" for entry in (job.processing_summary or {}).get("videos", [])
        ):
            failed = True
            raise ConnectionError("clip commit unavailable")

    event.listen(text_run.sessions.class_, "before_commit", fail_clip_commit)
    with pytest.raises(RetryableLabelingError, match="clip commit unavailable"):
        text_run.subject.run_labeling_pipeline(MagicMock(), text_run.job_id)
    with text_run.sessions() as session:
        assert not _rows(session, text_run.job_id)
        assert not any(
            entry["status"] == "completed"
            for entry in session.get(LabelingJob, text_run.job_id).processing_summary["videos"]
        )
        assert all(
            "sampled_frames" not in entry
            for entry in session.get(LabelingJob, text_run.job_id).processing_summary["videos"]
        )


@pytest.mark.parametrize("missing_summary", [False, True])
def test_historical_observations_without_completed_marker_fail_closed(text_run, missing_summary):
    _first_attempt(text_run)
    with text_run.sessions() as session:
        job = session.get(LabelingJob, text_run.job_id)
        job.processing_summary = (
            None if missing_summary else {**job.processing_summary, "videos": [job.processing_summary["videos"][1]]}
        )
        row = session.query(Annotation).filter_by(job_id=job.id).first()
        row.class_name, row.status = "manual-edit", "pending"
        job.result_minio_key = "partial-export.zip"
        session.commit()
        original, summary = _rows(session, job.id), copy.deepcopy(job.processing_summary)
    downloads = list(text_run.downloads)
    with pytest.raises(ValueError, match="completed clip summary.*new labeling job"):
        text_run.subject.run_labeling_pipeline(MagicMock(), text_run.job_id)
    assert text_run.downloads == downloads
    with text_run.sessions() as session:
        job = session.get(LabelingJob, text_run.job_id)
        assert _rows(session, job.id) == original
        assert job.processing_summary == summary
        assert job.result_minio_key == "partial-export.zip"
