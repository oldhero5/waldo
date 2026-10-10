"""Native retries preserve committed review evidence and atomic clip metadata."""

import copy
import shutil
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from lib.db import Annotation, Base, Frame, LabelingJob, Project, Video
from tests.test_video_evidence_regressions import _load_subject


@pytest.fixture
def native_job(monkeypatch, tmp_path):
    subject = _load_subject("labeler.video_labeler")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    sources = {}
    with factory() as session:
        project = Project(name="retry evidence")
        session.add(project)
        session.flush()
        videos = [Video(project_id=project.id, filename=f"{i}.avi", minio_key=f"source-{i}") for i in range(2)]
        job = LabelingJob(project_id=project.id, text_prompt="camera", score_threshold=0.2, sample_fps=2)
        session.add_all([job, *videos])
        session.commit()
        job_id, video_ids = job.id, [video.id for video in videos]
        for video in videos:
            path = tmp_path / video.filename
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 2, (40, 40))
            assert writer.isOpened()
            writer.write(np.zeros((40, 40, 3), dtype=np.uint8))
            writer.release()
            sources[video.minio_key] = path
    monkeypatch.setattr(subject, "SessionLocal", factory)
    monkeypatch.setattr(subject, "download_file", lambda key, path: shutil.copyfile(sources[key], path))
    monkeypatch.setattr(subject, "upload_file", lambda *args: None)
    monkeypatch.setattr(subject, "_publish_progress", lambda *args, **kwargs: None)
    monkeypatch.setattr(subject, "_publish_detection", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        subject,
        "process_video_native",
        lambda *args, **kwargs: [
            {
                "frame_idx": 0,
                "timestamp_s": 0,
                "timestamp_method": "source_pts",
                "width": 40,
                "height": 40,
                "detections": [{"label": "camera", "score": 0.9, "track_id": 7, "bbox": [10, 10, 30, 30]}],
            }
        ],
    )
    yield SimpleNamespace(subject=subject, factory=factory, job_id=job_id, video_ids=video_ids, sources=sources)
    engine.dispose()


def _review_first_clip(kit):
    with kit.factory() as session:
        annotation = session.query(Annotation).join(Frame).filter(Frame.video_id == kit.video_ids[0]).one()
        annotation.status = "accepted"
        annotation.bbox = [0.25, 0.25, 0.15, 0.15]
        annotation.polygon = [0.1, 0.1, 0.3, 0.1, 0.3, 0.3]
        annotation.class_name = "edited camera"
        session.commit()
        return annotation.id


def _assert_review_retained(kit, annotation_id):
    with kit.factory() as session:
        annotation = session.get(Annotation, annotation_id)
        assert annotation is not None, "retry deleted the reviewed annotation ID"
        assert annotation.status == "accepted"
        assert annotation.bbox == [0.25, 0.25, 0.15, 0.15]
        assert annotation.polygon == [0.1, 0.1, 0.3, 0.1, 0.3, 0.3]
        assert annotation.class_name == "edited camera"
        assert annotation.track_id == 7
        assert session.query(Annotation).count() == 2


def _fail_second_download_once(kit, monkeypatch):
    failed = False

    def download(key, path):
        nonlocal failed
        if key == "source-1" and not failed:
            failed = True
            raise ConnectionError("temporary storage outage")
        shutil.copyfile(kit.sources[key], path)

    monkeypatch.setattr(kit.subject, "download_file", download)
    with pytest.raises(kit.subject.RetryableLabelingError):
        kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)


def test_partial_retry_preserves_reviewed_ids_geometry_and_status(native_job, monkeypatch):
    kit = native_job
    _fail_second_download_once(kit, monkeypatch)
    annotation_id = _review_first_clip(kit)
    process = kit.subject.process_video_native

    def remaining_clip(path, *args, **kwargs):
        if str(kit.video_ids[0]) in path:
            pytest.fail("retry re-inferred an already committed clip")
        return process(path, *args, **kwargs)

    monkeypatch.setattr(kit.subject, "process_video_native", remaining_clip)
    result = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
    assert result["status"] == "completed"
    assert result["videos_processed"] == 2
    assert result["annotations_created"] == 2
    assert [clip["status"] for clip in result["processing_summary"]["videos"]] == ["completed", "completed"]
    _assert_review_retained(kit, annotation_id)


def test_new_retry_evidence_invalidates_previous_partial_export(native_job, monkeypatch):
    kit = native_job
    _fail_second_download_once(kit, monkeypatch)
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        job.result_minio_key = "partial-export.zip"
        session.commit()
    assert kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)["status"] == "completed"
    with kit.factory() as session:
        assert session.get(LabelingJob, kit.job_id).result_minio_key is None


def test_completed_redelivery_returns_persisted_result_without_touching_evidence(native_job, monkeypatch):
    kit = native_job
    first = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
    annotation_id = _review_first_clip(kit)
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        job.result_minio_key = "reviewed-export.zip"
        job.score_threshold = 0.8  # A completed delivery still represents the original run.
        saved_summary = copy.deepcopy(job.processing_summary)
        session.commit()
    monkeypatch.setattr(kit.subject, "download_file", lambda *args: pytest.fail("completed job downloaded media"))
    result = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
    assert result == first
    _assert_review_retained(kit, annotation_id)
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        assert job.processing_summary == saved_summary
        assert job.result_minio_key == "reviewed-export.zip"


def test_clip_completion_is_committed_with_its_annotations(native_job):
    kit = native_job
    commits = []

    def snapshot(session):
        for annotation in list(session.new):
            if isinstance(annotation, Annotation):
                frame = session.get(Frame, annotation.frame_id)
                job = session.get(LabelingJob, kit.job_id)
                commits.append((str(frame.video_id), copy.deepcopy(job.processing_summary)))

    event.listen(kit.factory.class_, "before_commit", snapshot)
    try:
        assert kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)["status"] == "completed"
    finally:
        event.remove(kit.factory.class_, "before_commit", snapshot)
    assert len(commits) == 2
    for video_id, summary in commits:
        clip = next((clip for clip in summary["videos"] if clip["video_id"] == video_id), None)
        assert clip is not None, "annotation commit had no completion metadata"
        assert clip["status"] == "completed"
        assert clip["sampled_frames"] == 1
        assert clip["observations"] == 1


def test_failed_clip_commit_rolls_back_rows_and_completion_before_retry(native_job):
    kit = native_job
    failed = False

    def fail_second_commit(session):
        nonlocal failed
        for annotation in list(session.new):
            if isinstance(annotation, Annotation):
                frame = session.get(Frame, annotation.frame_id)
                if frame.video_id == kit.video_ids[1] and not failed:
                    failed = True
                    raise ConnectionError("database commit unavailable")

    event.listen(kit.factory.class_, "before_commit", fail_second_commit)
    try:
        with pytest.raises(kit.subject.RetryableLabelingError):
            kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
        with kit.factory() as session:
            job = session.get(LabelingJob, kit.job_id)
            assert session.query(Annotation).count() == 1
            assert session.query(Frame).filter_by(video_id=kit.video_ids[1]).count() == 0
            assert job.processed_frames == 1
            assert [clip["status"] for clip in job.processing_summary["videos"]] == ["completed", "retrying"]
            assert "sampled_frames" not in job.processing_summary["videos"][1]
        annotation_id = _review_first_clip(kit)
        assert kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)["status"] == "completed"
        _assert_review_retained(kit, annotation_id)
    finally:
        event.remove(kit.factory.class_, "before_commit", fail_second_commit)


@pytest.mark.parametrize("field,value", [("score_threshold", 0.7), ("sample_fps", 4)])
def test_partial_retry_configuration_mismatch_leaves_completed_review_intact(native_job, monkeypatch, field, value):
    kit = native_job
    _fail_second_download_once(kit, monkeypatch)
    annotation_id = _review_first_clip(kit)
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        setattr(job, field, value)
        saved_summary = copy.deepcopy(job.processing_summary)
        session.commit()
    monkeypatch.setattr(kit.subject, "download_file", lambda *args: pytest.fail("mismatched retry downloaded media"))
    result = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
    assert result["status"] == "failed"
    with kit.factory() as session:
        annotation = session.get(Annotation, annotation_id)
        assert annotation is not None and annotation.status == "accepted"
        assert annotation.bbox == [0.25, 0.25, 0.15, 0.15]
        assert session.query(Annotation).count() == 1
        assert session.get(LabelingJob, kit.job_id).processing_summary == saved_summary


def test_legacy_rows_without_completion_record_are_preserved(native_job, monkeypatch):
    kit = native_job
    _fail_second_download_once(kit, monkeypatch)
    annotation_id = _review_first_clip(kit)
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        summary = copy.deepcopy(job.processing_summary)
        summary["videos"] = [entry for entry in summary["videos"] if entry["video_id"] != str(kit.video_ids[0])]
        job.processing_summary = summary
        session.commit()

    def download(key, path):
        assert key != "source-0", "ambiguous historical evidence was reprocessed"
        shutil.copyfile(kit.sources[key], path)

    monkeypatch.setattr(kit.subject, "download_file", download)
    result = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
    assert result["status"] == "failed"
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        assert "start a new labeling job" in job.error_message
        assert job.processing_summary == summary
        annotation = session.get(Annotation, annotation_id)
        assert annotation is not None and annotation.status == "accepted"
        assert annotation.bbox == [0.25, 0.25, 0.15, 0.15]
        assert session.query(Annotation).count() == 1
