"""Native sample evidence survives independently of positive image artifacts."""

import copy
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, event, text
from sqlalchemy.exc import IntegrityError

from lib import db
from lib.db import Annotation, Frame, LabelingJob
from tests.test_native_retry_integrity import native_job as native_job_fixture
from tests.test_native_video_eof import estimated_count_video as vfr_fixture
from tests.test_native_video_eof import native_subject as native_subject_fixture

native_job = native_job_fixture
native_subject = native_subject_fixture
estimated_count_video = vfr_fixture


def assessment_model():
    model = getattr(db, "NativeFrameAssessment", None)
    assert model is not None, "native sample evidence has no durable table"
    return model


def test_positive_and_empty_samples_survive_reopen_without_empty_images(native_job, monkeypatch):
    kit = native_job
    original = kit.subject.process_video_native

    def inference(*args, **kwargs):
        positive = original(*args, **kwargs)[0]
        positive.update(timestamp_s=-0.125, frame_duration_s=0.04)
        return [
            positive,
            {
                **positive,
                "frame_idx": 1,
                "timestamp_s": 0.08,
                "frame_duration_s": None,
                "timestamp_method": "frame_index/fps",
                "detections": [],
            },
        ]

    monkeypatch.setattr(kit.subject, "process_video_native", inference)
    assert kit.subject.run_video_labeling_pipeline(NoneTask(), kit.job_id)["status"] == "completed"
    model = assessment_model()
    with kit.factory() as session:
        rows = session.query(model).order_by(model.video_id, model.source_frame_index).all()
        assert len(rows) == 4
        assert [(r.source_frame_index, r.timestamp_s, r.frame_duration_s, r.detection_count) for r in rows] == [
            (0, -0.125, 0.04, 1),
            (1, 0.08, None, 0),
            (0, -0.125, 0.04, 1),
            (1, 0.08, None, 0),
        ]
        assert session.query(Frame).count() == 2
        assert session.query(Annotation).count() == 2
        assert all(
            c["native_assessment_version"] == 1
            for c in session.get(LabelingJob, kit.job_id).processing_summary["videos"]
        )
        session.query(Annotation).delete()
        session.commit()
    with kit.factory() as session:
        assert [r.detection_count for r in session.query(model).order_by(model.video_id, model.source_frame_index)] == [
            1,
            0,
            1,
            0,
        ]


class NoneTask:
    def update_state(self, **kwargs):
        pass


@pytest.mark.parametrize(
    "field,value",
    [
        ("frame_idx", True),
        ("frame_idx", -1),
        ("frame_idx", 0.5),
        ("frame_idx", 2147483648),
        ("timestamp_s", float("inf")),
        ("timestamp_s", float("nan")),
        ("timestamp_method", "unknown"),
        ("width", True),
        ("width", 0),
        ("height", 1.2),
        ("frame_duration_s", 0),
        ("frame_duration_s", float("inf")),
    ],
)
def test_invalid_native_output_rolls_back_clip(native_job, monkeypatch, field, value):
    kit = native_job
    result = kit.subject.process_video_native()[0]
    result[field] = value
    monkeypatch.setattr(kit.subject, "process_video_native", lambda *a, **k: [copy.deepcopy(result)])
    outcome = kit.subject.run_video_labeling_pipeline(NoneTask(), kit.job_id)
    assert outcome["status"] == "failed", "invalid native provenance was accepted"
    model = assessment_model()
    with kit.factory() as session:
        assert session.query(model).count() == session.query(Annotation).count() == session.query(Frame).count() == 0
        assert all(c["status"] == "failed" for c in session.get(LabelingJob, kit.job_id).processing_summary["videos"])


def test_assessments_share_clip_rollback(native_job):
    kit = native_job
    model = assessment_model()

    def fail_commit(session):
        if any(isinstance(row, Annotation) for row in session.new):
            assert session.query(model).count() > 0
            assert any(
                clip.get("native_assessment_version") == 1
                for clip in session.get(LabelingJob, kit.job_id).processing_summary["videos"]
            )
            raise ValueError("simulated evidence transaction failure")

    event.listen(kit.factory.class_, "before_commit", fail_commit)
    try:
        assert kit.subject.run_video_labeling_pipeline(NoneTask(), kit.job_id)["status"] == "failed"
    finally:
        event.remove(kit.factory.class_, "before_commit", fail_commit)
    with kit.factory() as session:
        assert session.query(model).count() == session.query(Annotation).count() == session.query(Frame).count() == 0
        assert all(c["status"] == "failed" for c in session.get(LabelingJob, kit.job_id).processing_summary["videos"])


def test_unique_sample_and_job_delete_cascade(native_job):
    kit = native_job
    model = assessment_model()
    assert kit.subject.run_video_labeling_pipeline(NoneTask(), kit.job_id)["status"] == "completed"
    with kit.factory() as session:
        row = session.query(model).first()
        session.add(
            model(
                job_id=row.job_id,
                video_id=row.video_id,
                source_frame_index=row.source_frame_index,
                timestamp_s=0,
                timestamp_method="source_pts",
                source_width=40,
                source_height=40,
                detection_count=0,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        # SQLite needs explicit foreign-key enforcement, as PostgreSQL has by default.
        session.execute(text("PRAGMA foreign_keys=ON"))
        session.execute(delete(Annotation).where(Annotation.job_id == kit.job_id))
        session.execute(delete(LabelingJob).where(LabelingJob.id == kit.job_id))
        session.commit()
    with kit.factory() as session:
        assert session.query(model).count() == 0


@pytest.mark.parametrize("native_job", ["postgres"], indirect=True)
@pytest.mark.parametrize(
    "field,value",
    [
        ("timestamp_s", float("nan")),
        ("timestamp_s", float("inf")),
        ("timestamp_s", -float("inf")),
        ("frame_duration_s", float("nan")),
        ("frame_duration_s", float("inf")),
        ("frame_duration_s", 0),
        ("source_frame_index", -1),
        ("source_width", 0),
        ("source_height", -1),
        ("detection_count", -1),
        ("timestamp_method", "unknown"),
        ("job_id", uuid.UUID(int=1)),
        ("video_id", uuid.UUID(int=1)),
    ],
)
def test_postgres_rejects_invalid_assessments(native_job, field, value):
    kit = native_job
    model = assessment_model()
    with kit.factory() as session:
        row = model(
            job_id=kit.job_id,
            video_id=kit.video_ids[0],
            source_frame_index=0,
            timestamp_s=-0.125,
            timestamp_method="source_pts",
            source_width=40,
            source_height=40,
            frame_duration_s=None,
            detection_count=0,
        )
        setattr(row, field, value)
        session.add(row)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert session.query(model).count() == 0


@pytest.mark.parametrize("native_job", ["postgres"], indirect=True)
def test_postgres_video_and_job_cascades_remove_only_owned_assessments(native_job):
    from lib.db import Video

    kit = native_job
    model = assessment_model()
    assert kit.subject.run_video_labeling_pipeline(NoneTask(), kit.job_id)["status"] == "completed"
    with kit.factory() as session:
        session.query(Annotation).delete()
        session.query(Frame).delete()
        session.execute(delete(Video).where(Video.id == kit.video_ids[0]))
        session.commit()
        assert session.query(model).count() == 1
        session.execute(delete(LabelingJob).where(LabelingJob.id == kit.job_id))
        session.commit()
        assert session.query(model).count() == 0


@pytest.mark.parametrize("fallback", [False, True])
def test_real_vfr_decoder_samples_persist_with_explicit_fallback(
    native_job, native_subject, estimated_count_video, monkeypatch, fallback
):
    kit = native_job
    kit.sources["source-0"] = estimated_count_video
    kit.sources["source-1"] = estimated_count_video
    with kit.factory() as session:
        session.get(LabelingJob, kit.job_id).sample_fps = 100
        session.commit()
    if fallback:
        monkeypatch.setattr(native_subject, "probe_frame_timing", lambda *args: [])
        # The fallback has no decoded-count probe. Use a correct container count
        # for this case; the existing EOF suite tests unreliable count handling.
        import cv2

        original_capture = cv2.VideoCapture

        def capture(path):
            cap = original_capture(path)
            return SimpleNamespace(
                isOpened=cap.isOpened,
                read=cap.read,
                release=cap.release,
                get=lambda prop: 6 if prop == cv2.CAP_PROP_FRAME_COUNT else cap.get(prop),
            )

        monkeypatch.setattr(native_subject.cv2, "VideoCapture", capture)
    monkeypatch.setattr(kit.subject, "process_video_native", native_subject.process_video_native)
    assert kit.subject.run_video_labeling_pipeline(NoneTask(), kit.job_id)["status"] == "completed"
    model = assessment_model()
    with kit.factory() as session:
        rows = session.query(model).filter_by(video_id=kit.video_ids[0]).order_by(model.source_frame_index).all()
        assert [row.source_frame_index for row in rows] == [0, 1, 2, 3, 4, 5]
        if fallback:
            assert {row.timestamp_method for row in rows} == {"frame_index/fps"}
            assert [row.timestamp_s for row in rows] == pytest.approx([0, 0.04, 0.08, 0.12, 0.16, 0.20])
        else:
            assert {row.timestamp_method for row in rows} == {"source_pts"}
            assert [row.timestamp_s for row in rows] == pytest.approx([0, 0.12, 0.48, 0.60, 0.96, 1.08])
        assert all(row.detection_count == 0 and row.frame_duration_s > 0 for row in rows)
        assert session.query(Frame).count() == 0
