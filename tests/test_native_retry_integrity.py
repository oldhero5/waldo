"""Native retries preserve committed review evidence and atomic clip metadata."""

import copy
import os
import shutil
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from lib.db import Annotation, Base, Frame, LabelingJob, Project, Video
from tests.test_video_evidence_regressions import _load_subject


@pytest.fixture
def native_job(monkeypatch, tmp_path, request):
    subject = _load_subject("labeler.video_labeler")
    schema = None
    owner = None
    if getattr(request, "param", None) == "postgres":
        url = os.environ.get("WALDO_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Set WALDO_TEST_POSTGRES_URL to a disposable PostgreSQL database")
        schema = f"waldo_native_{uuid.uuid4().hex}"
        owner = create_engine(url)
        with owner.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    else:
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
    if owner is not None:
        with owner.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        owner.dispose()


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


def test_orphan_empty_assessment_stops_replacement(native_job, monkeypatch):
    from lib import db

    kit = native_job
    model = getattr(db, "NativeFrameAssessment", None)
    assert model is not None, "orphan assessment guard requires durable evidence"
    with kit.factory() as session:
        session.add(
            model(
                job_id=kit.job_id,
                video_id=kit.video_ids[0],
                source_frame_index=0,
                timestamp_s=0,
                timestamp_method="source_pts",
                source_width=40,
                source_height=40,
                detection_count=0,
            )
        )
        session.commit()
    monkeypatch.setattr(kit.subject, "download_file", lambda *a: pytest.fail("orphan evidence was reprocessed"))
    result = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
    assert result["status"] == "failed"
    with kit.factory() as session:
        assert session.query(model).count() == 1
        assert "start a new labeling job" in session.get(LabelingJob, kit.job_id).error_message


def test_stale_delivery_reuses_completion_before_writing(native_job, monkeypatch):
    kit = native_job
    process = kit.subject.process_video_native
    winner = None

    def stale_inference(*args, **kwargs):
        nonlocal winner
        if winner is None:
            monkeypatch.setattr(kit.subject, "process_video_native", process)
            winner = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
            annotation_id = _review_first_clip(kit)
            with kit.factory() as session:
                job = session.get(LabelingJob, kit.job_id)
                job.result_minio_key = "winner-export.zip"
                session.commit()
            winner["reviewed_id"] = annotation_id
        return process(*args, **kwargs)

    monkeypatch.setattr(kit.subject, "process_video_native", stale_inference)
    result = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
    assert result["status"] == "completed"
    _assert_review_retained(kit, winner["reviewed_id"])
    with kit.factory() as session:
        assert session.get(LabelingJob, kit.job_id).result_minio_key == "winner-export.zip"


@pytest.mark.parametrize("exhausted", [False, True])
def test_task_retry_record_cannot_regress_completed_native_job(native_job, monkeypatch, exhausted):
    from lib import db, tasks

    kit = native_job
    assert kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)["status"] == "completed"
    annotation_id = _review_first_clip(kit)
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        job.result_minio_key = "completed-reviewed-export.zip"
        saved = copy.deepcopy(job.processing_summary)
        session.commit()
    monkeypatch.setattr(db, "SessionLocal", kit.factory)
    task = SimpleNamespace(request=SimpleNamespace(retries=3 if exhausted else 0), max_retries=3)
    tasks._record_labeling_retry(task, kit.job_id, ConnectionError("late stale failure"))
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        assert job.status == "completed"
        assert job.processing_summary == saved
        assert job.result_minio_key == "completed-reviewed-export.zip"
    _assert_review_retained(kit, annotation_id)


@pytest.mark.parametrize("native_job", ["postgres"], indirect=True)
@pytest.mark.parametrize("stale_fails", [False, True])
def test_postgres_concurrent_stale_delivery_retains_winner_review(native_job, monkeypatch, stale_fails):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event, current_thread

    from lib.db import NativeFrameAssessment

    kit = native_job
    process = kit.subject.process_video_native
    both_inferred = Barrier(2)
    winner_finished = Event()
    entered = set()

    def inference(*args, **kwargs):
        name = current_thread().name
        if name not in entered:
            entered.add(name)
            both_inferred.wait(timeout=15)
            if name.startswith("stale"):
                assert winner_finished.wait(timeout=20), "winner did not finish"
                if stale_fails:
                    raise ConnectionError("stale delivery failed after winner completed")
        return process(*args, **kwargs)

    monkeypatch.setattr(kit.subject, "process_video_native", inference)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale") as stale_pool:
        stale = stale_pool.submit(kit.subject.run_video_labeling_pipeline, MagicMock(), kit.job_id)
        winner = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
        assert winner["status"] == "completed"
        annotation_id = _review_first_clip(kit)
        with kit.factory() as session:
            job = session.get(LabelingJob, kit.job_id)
            saved = copy.deepcopy(job.processing_summary)
            job.result_minio_key = "winner-reviewed.zip"
            ids = {row.id for row in session.query(NativeFrameAssessment)}
            session.commit()
        winner_finished.set()
        assert stale.result(timeout=20)["status"] == "completed"
    _assert_review_retained(kit, annotation_id)
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        assert job.status == "completed"
        assert job.result_minio_key == "winner-reviewed.zip"
        assert job.processing_summary == saved
        assert {row.id for row in session.query(NativeFrameAssessment)} == ids


@pytest.mark.parametrize("native_job", ["postgres"], indirect=True)
@pytest.mark.parametrize("redelivery_fails", [False, True])
def test_postgres_redelivery_preserves_incomplete_clip_until_replacement(native_job, monkeypatch, redelivery_fails):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread

    from lib.db import NativeFrameAssessment

    kit = native_job
    process = kit.subject.process_video_native
    second_entered, release_second = Event(), Event()
    redelivery = None
    reviewed_id = None
    in_flight_summary = None

    def inference(path, *args, **kwargs):
        if str(kit.video_ids[1]) in path:
            if current_thread().name.startswith("redelivery"):
                second_entered.set()
                assert release_second.wait(15), "redelivery was not released"
                if redelivery_fails:
                    raise ValueError("redelivery terminal failure")
            else:
                raise ValueError("first delivery terminal failure")
        return process(path, *args, **kwargs)

    def progress(job_id, processed, total, filename, **kwargs):
        nonlocal redelivery, reviewed_id, in_flight_summary
        if filename == "1.avi" and not current_thread().name.startswith("redelivery"):
            reviewed_id = _review_first_clip(kit)
            with kit.factory() as session:
                job = session.get(LabelingJob, kit.job_id)
                job.result_minio_key = "partial-reviewed.zip"
                session.commit()
            redelivery = pool.submit(kit.subject.run_video_labeling_pipeline, MagicMock(), kit.job_id)
            assert second_entered.wait(15), "redelivery did not infer the incomplete clip"
            with kit.factory() as session:
                in_flight_summary = copy.deepcopy(session.get(LabelingJob, kit.job_id).processing_summary)

    monkeypatch.setattr(kit.subject, "process_video_native", inference)
    monkeypatch.setattr(kit.subject, "_publish_progress", progress)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="redelivery") as pool:
        try:
            first = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
            with kit.factory() as session:
                job = session.get(LabelingJob, kit.job_id)
                saved_status = job.status
                saved_export = job.result_minio_key
                original_assessment_ids = {row.id for row in session.query(NativeFrameAssessment)}
        finally:
            release_second.set()
        second = redelivery.result(timeout=15)

    assert first["status"] == saved_status == "partial"
    assert saved_export == "partial-reviewed.zip"
    assert {entry["video_id"]: entry["status"] for entry in in_flight_summary["videos"]} == {
        str(kit.video_ids[0]): "completed",
        str(kit.video_ids[1]): "failed",
    }
    assert second["status"] == ("partial" if redelivery_fails else "completed")
    with kit.factory() as session:
        job = session.get(LabelingJob, kit.job_id)
        assert job.status == second["status"]
        entries = {entry["video_id"]: entry for entry in job.processing_summary["videos"]}
        assert set(entries) == {str(video) for video in kit.video_ids}
        assert entries[str(kit.video_ids[0])]["status"] == "completed"
        assert entries[str(kit.video_ids[1])]["status"] == ("failed" if redelivery_fails else "completed")
        assessments = session.query(NativeFrameAssessment).all()
        assert len(assessments) == (1 if redelivery_fails else 2)
        assert original_assessment_ids <= {row.id for row in assessments}
        assert job.result_minio_key == ("partial-reviewed.zip" if redelivery_fails else None)
        row = session.get(Annotation, reviewed_id)
        assert row.status == "accepted" and row.class_name == "edited camera"
        assert row.bbox == [0.25, 0.25, 0.15, 0.15]
        assert row.polygon == [0.1, 0.1, 0.3, 0.1, 0.3, 0.3]
        assert session.query(Annotation).count() == (1 if redelivery_fails else 2)


def test_native_finalization_requires_every_eligible_video_completion(native_job, monkeypatch):
    kit = native_job
    process = kit.subject.process_video_native

    def inference(path, *args, **kwargs):
        if str(kit.video_ids[1]) in path:
            raise ValueError("second clip did not complete")
        return process(path, *args, **kwargs)

    def progress(job_id, processed, total, filename, **kwargs):
        if filename == "1.avi":
            with kit.factory() as session:
                job = session.get(LabelingJob, kit.job_id)
                job.processing_summary = {
                    **job.processing_summary,
                    "videos": [entry for entry in job.processing_summary["videos"] if entry["status"] == "completed"],
                }
                session.commit()

    monkeypatch.setattr(kit.subject, "process_video_native", inference)
    monkeypatch.setattr(kit.subject, "_publish_progress", progress)
    result = kit.subject.run_video_labeling_pipeline(MagicMock(), kit.job_id)
    assert result["status"] == "partial"
    with kit.factory() as session:
        assert session.get(LabelingJob, kit.job_id).status == "partial"
        assert session.query(Annotation).count() == 1
