"""Processing contracts verified with synthetic frames and model boundaries."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import cv2
import numpy as np
import pytest
from PIL import Image

from tests.test_video_evidence_regressions import _load_subject

pytestmark = pytest.mark.no_auth_bypass


def test_evidence_extraction_bypasses_stale_cache_and_keeps_distinct_sampling_rates(monkeypatch, tmp_path):
    import lib.frame_cache

    subject = _load_subject("labeler.frame_extractor")
    source = tmp_path / "video.mp4"
    source.write_bytes(b"video")
    monkeypatch.setattr(lib.frame_cache, "load_cached_frames", lambda *a: [SimpleNamespace(timestamp_s=7)])
    monkeypatch.setattr(lib.frame_cache, "save_cached_frames", lambda *a: None)

    def extract(command, **kwargs):
        fps = float(command[command.index("-vf") + 1].split("=")[1])
        for index in range(int(fps)):
            Image.new("RGB", (16, 16), color=(10 + index * 10, 0, 0)).save(command[-1] % (index + 1))

    monkeypatch.setattr(subject.subprocess, "run", extract)
    first = subject.extract_frames(source, tmp_path / "one", fps=1, dedup_threshold=-1, use_cache=False)
    second = subject.extract_frames(source, tmp_path / "two", fps=2, dedup_threshold=-1, use_cache=False)

    assert [frame.timestamp_s for frame in first] == [0]
    assert [frame.timestamp_s for frame in second] == [0, 0.5]
    assert [Image.open(frame.file_path).getpixel((0, 0))[0] for frame in second] == pytest.approx([10, 20], abs=2)


@pytest.fixture
def sam_stub(monkeypatch):
    module = SimpleNamespace(SegmentationResult=lambda **kwargs: SimpleNamespace(**kwargs), get_engine=lambda: None)
    monkeypatch.setitem(sys.modules, "labeler.sam3_engine", module)
    return module


def test_text_processing_consumes_job_sampling_and_threshold_without_evidence_dedup(monkeypatch, tmp_path, sam_stub):
    monkeypatch.setitem(
        sys.modules,
        "labeler.pipeline",
        SimpleNamespace(
            _update_job=lambda *a, **k: None,
            convert_and_store=lambda *a: None,
            replace_raw_observations=lambda *a: None,
        ),
    )
    subject = _load_subject("labeler.text_labeler")
    image_path = tmp_path / "frame.png"
    cv2.imwrite(str(image_path), np.zeros((8, 8, 3), dtype=np.uint8))
    frame = SimpleNamespace(file_path=image_path, frame_number=0, timestamp_s=0, phash="", width=8, height=8)
    calls = []

    def extract(path, directory, **kwargs):
        calls.append(kwargs)
        return [frame]

    monkeypatch.setattr(subject, "extract_frames", extract)
    monkeypatch.setattr(subject, "download_file", lambda *a: None)
    monkeypatch.setattr(subject, "upload_file", lambda *a: None)
    engine = MagicMock()
    engine.iter_segment_frame_paths.side_effect = lambda *a, **kw: (
        item
        for item in [
            SimpleNamespace(
                frame_index=0,
                masks=np.ones((1, 8, 8), dtype=bool),
                boxes=np.array([[0, 0, 7, 7]]),
                scores=np.array([0.9]),
                class_indices=None,
            )
        ]
    )
    job = SimpleNamespace(id="run", score_threshold=0.12, sample_fps=2.5)
    video = SimpleNamespace(id="video", filename="video.mp4", minio_key="videos/video.mp4")

    results, _, _ = subject._process_single_video(
        MagicMock(), job, video, engine, tmp_path, [{"name": "camera", "prompt": "camera"}]
    )

    assert len(results) == 1
    assert calls == [{"fps": 2.5, "dedup_threshold": -1, "use_cache": False}]
    assert engine.iter_segment_frame_paths.call_args.kwargs["threshold"] == 0.12
    assert subject._compute_stride(10000, None) == 1


def test_conversion_rejects_misaligned_frame_lists_before_any_write(monkeypatch, tmp_path, sam_stub):
    subject = _load_subject("labeler.pipeline")
    session = MagicMock()
    job = SimpleNamespace(id="job", task_type="segment")
    monkeypatch.setattr(subject, "upload_file", lambda *a: None)

    with pytest.raises(ValueError, match="length"):
        subject.convert_and_store(session, job, [], [SimpleNamespace()], [], ["camera"], tmp_path)

    session.add.assert_not_called()
    assert not (tmp_path / "dataset.zip").exists()


def test_converter_keeps_mask_score_alignment_and_video_groups(monkeypatch, tmp_path, sam_stub):
    subject = _load_subject("labeler.pipeline")
    session = MagicMock()
    # First mask has no usable polygon; second same-class mask is the real target.
    masks = np.zeros((2, 40, 40), dtype=bool)
    masks[1, 10:30, 10:30] = True
    result = SimpleNamespace(
        frame_index=0,
        masks=masks,
        boxes=np.array([[0, 0, 1, 1], [10, 10, 30, 30]]),
        scores=np.array([0.3, 0.9]),
        class_indices=np.array([0, 0]),
        track_ids=None,
    )
    frame_path = tmp_path / "frame.png"
    cv2.imwrite(str(frame_path), np.zeros((40, 40, 3), dtype=np.uint8))
    job = SimpleNamespace(id="job", task_type="segment")
    frame = SimpleNamespace(id="frame", video_id="video")
    written = []
    monkeypatch.setattr(subject.to_segment, "write_yolo_dataset", lambda *a, **kw: written.append(kw))
    monkeypatch.setattr(subject, "upload_file", lambda *a: None)

    subject._convert_label_format(
        session,
        job,
        [result],
        [frame],
        [SimpleNamespace(file_path=frame_path)],
        ["camera"],
        tmp_path / "dataset",
        "segment",
    )

    annotations = [call.args[0] for call in session.add.call_args_list]
    assert [ann.confidence for ann in annotations] == [0.3, 0.9]
    assert annotations[0].polygon == []
    assert annotations[1].bbox == [0.5, 0.5, 0.5, 0.5]
    assert written[0]["group_ids"] == []  # Unrepresentable visible label excludes this image from segmentation export.


@pytest.mark.parametrize("native", [True, False])
def test_dispatch_uses_available_backend_for_both_project_and_single_video(monkeypatch, native):
    import lib.db
    import lib.tasks

    outputs = []
    monkeypatch.setitem(
        sys.modules,
        "labeler.video_labeler",
        SimpleNamespace(run_video_labeling_pipeline=lambda *a: outputs.append("native") or {"status": "completed"}),
    )
    monkeypatch.setitem(
        sys.modules,
        "labeler.text_labeler",
        SimpleNamespace(run_labeling_pipeline=lambda *a: outputs.append("pytorch") or {"status": "completed"}),
    )
    # The future capability selector is patched only at its platform/dependency boundaries.
    monkeypatch.setattr(lib.tasks, "_native_video_available", lambda: native, raising=False)
    session = MagicMock()
    monkeypatch.setattr(lib.db, "SessionLocal", lambda: session)
    for project_id in (None, "project"):
        session.query.return_value.filter_by.return_value.first.return_value = SimpleNamespace(project_id=project_id)
        lib.tasks.label_video.run("job")

    assert outputs == (["native", "native"] if native else ["pytorch", "pytorch"])


def test_text_pipeline_reports_successful_subset_when_another_video_fails(monkeypatch, sam_stub):
    pipeline = _load_subject("labeler.pipeline")
    monkeypatch.setitem(sys.modules, "labeler.pipeline", pipeline)
    subject = _load_subject("labeler.text_labeler")
    job = SimpleNamespace(
        id="job",
        project_id="project",
        video_id=None,
        text_prompt="camera",
        class_prompts=None,
        score_threshold=0.2,
        sample_fps=2,
        processing_summary=None,
    )
    videos = [SimpleNamespace(id="video-a", frame_count=30), SimpleNamespace(id="video-b", frame_count=30)]
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one.return_value = job
    session.query.return_value.filter_by.return_value.all.return_value = videos
    session.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(subject, "SessionLocal", lambda: session)
    monkeypatch.setattr(subject, "get_engine", lambda: object())

    def process(session, job, video, *a, **kw):
        if video.id == "video-b":
            raise RuntimeError("decoder failure")
        return (
            [SimpleNamespace(frame_index=0, masks=np.empty((0, 8, 8)), boxes=[], scores=[], class_indices=[])],
            [SimpleNamespace(video_id=video.id)],
            [SimpleNamespace(timestamp_s=0)],
        )

    persisted = []
    monkeypatch.setattr(subject, "_process_single_video", process)
    monkeypatch.setattr(subject, "replace_raw_observations", lambda s, j, results, frames, *a: persisted.extend(frames))

    result = subject.run_labeling_pipeline(MagicMock(), "job")

    assert result["status"] == "partial"
    assert [frame.video_id for frame in persisted] == ["video-a"]
    assert result["result_minio_key"] is None
    assert [entry["status"] for entry in job.processing_summary["videos"]] == ["completed", "failed"]
    assert job.progress == 0.5


@pytest.fixture
def native_run(monkeypatch, sam_stub):
    subject = _load_subject("labeler.video_labeler")
    job = SimpleNamespace(
        id="job",
        project_id="project",
        video_id=None,
        text_prompt="camera",
        class_prompts=None,
        score_threshold=0.2,
        sample_fps=2,
        task_type="segment",
        processing_summary=None,
    )
    videos = [
        SimpleNamespace(id="video-a", filename="a.mp4", minio_key="a"),
        SimpleNamespace(id="video-b", filename="b.mp4", minio_key="b"),
    ]
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one.return_value = job
    session.query.return_value.filter_by.return_value.all.return_value = videos
    session.query.return_value.filter_by.return_value.first.return_value = None
    session.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(subject, "SessionLocal", lambda: session)
    monkeypatch.setattr(subject, "download_file", lambda key, path: path.write_bytes(b"video"))
    monkeypatch.setattr(subject, "upload_file", lambda *a: None)
    monkeypatch.setattr(subject, "_publish_progress", lambda *a, **k: None)
    monkeypatch.setattr(subject, "_publish_detection", lambda *a, **k: None)

    class Capture:
        def isOpened(self):
            return True

        def set(self, *a):
            return True

        def read(self):
            return True, np.zeros((40, 40, 3), dtype=np.uint8)

        def release(self):
            pass

    monkeypatch.setattr(subject.cv2, "VideoCapture", lambda *a: Capture())
    return subject, session, job, videos


def _native_frames():
    return [
        {
            "frame_idx": index,
            "timestamp_s": index / 30,
            "width": 40,
            "height": 40,
            "detections": [
                {"label": "camera", "score": 0.9, "track_id": 7, "bbox": [10, 10, 30, 30], "polygon": polygon}
            ],
        }
        for index, polygon in ((0, [0.25, 0.25, 0.75, 0.25, 0.75, 0.75]), (30, None))
    ]


def test_native_pipeline_preserves_each_track_sighting_and_bbox_only_observation(monkeypatch, native_run):
    subject, session, job, videos = native_run
    calls = []

    def process(path, prompts, **kwargs):
        calls.append(kwargs)
        return _native_frames()

    monkeypatch.setattr(subject, "process_video_native", process)
    result = subject.run_video_labeling_pipeline(MagicMock(), "job")

    annotations = [call.args[0] for call in session.add.call_args_list if isinstance(call.args[0], subject.Annotation)]
    assert result["annotations_created"] == 4
    assert len(annotations) == 4
    assert [ann.track_id for ann in annotations] == [7, 7, 7, 7]
    assert annotations[1].polygon == []
    assert all(ann.bbox == [0.5, 0.5, 0.5, 0.5] for ann in annotations)
    assert calls == [{"threshold": 0.2, "sample_fps": 2}, {"threshold": 0.2, "sample_fps": 2}]
    assert job.processing_summary["videos"][0]["assessed_timestamps_s"] == [0, 1]


@pytest.mark.parametrize("fail_all,status,processed", [(True, "failed", 0), (False, "partial", 1)])
def test_native_clip_failures_cannot_report_complete_coverage(monkeypatch, native_run, fail_all, status, processed):
    subject, session, job, videos = native_run

    def process(path, prompts, **kwargs):
        if fail_all or Path(path).name.endswith("_b.mp4"):
            raise RuntimeError("decode failed at frame 30")
        return _native_frames()

    monkeypatch.setattr(subject, "process_video_native", process)
    result = subject.run_video_labeling_pipeline(MagicMock(), "job")

    assert result["status"] == status
    assert result["videos_processed"] == processed
    assert job.status == status
    assert job.progress == processed / 2
    failures = [entry for entry in job.processing_summary["videos"] if entry["status"] == "failed"]
    assert len(failures) == (2 if fail_all else 1)
    assert failures[-1]["video_id"] == "video-b"
    assert "decode failed" in failures[-1]["error"]


@pytest.fixture
def own_model_video(monkeypatch):
    import lib.inference_engine
    import lib.redis_client
    import lib.storage
    from tests.test_video_evidence_regressions import _Capture

    tracker = _load_subject("lib.video_tracker")
    monkeypatch.setitem(sys.modules, "lib.video_tracker", tracker)
    # This fixture checks model authorization with fake media and decoding.
    monkeypatch.setattr(tracker, "probe_frame_timing", lambda path: [])
    monkeypatch.setattr(tracker, "validate_video", lambda path: {"fps": 8, "width": 4, "height": 4, "frame_count": 1})
    monkeypatch.setattr(tracker.cv2, "VideoCapture", lambda path: _Capture(1))
    monkeypatch.setattr(tracker, "get_engine", lambda: pytest.fail("requested model fell through to global default"))
    engines = {
        name: SimpleNamespace(
            _needs_tiling=lambda *a: True,
            _predict_tiled=lambda frame, conf, name=name: [tracker.Detection(name, 0, conf, [0, 0, 2, 2])],
        )
        for name in ("model-a", "model-b")
    }
    monkeypatch.setattr(lib.inference_engine, "get_pool", lambda: SimpleNamespace(get_model=lambda name: engines[name]))
    client = MagicMock()
    client.get.return_value = None
    monkeypatch.setattr(lib.redis_client, "get_redis", lambda: client)
    monkeypatch.setattr(lib.storage, "download_file", lambda key, path: Path(path).write_bytes(b"synthetic video"))
    monkeypatch.setattr(lib.storage, "delete_object", lambda key: None)
    return tracker, engines, client


def test_prediction_stream_uses_authorized_model_engine(own_model_video):
    import lib.tasks
    from lib.redis_serde import unpack

    _, _, client = own_model_video
    result = lib.tasks.predict_video_task.run(
        "inference/00000000-0000-0000-0000-000000000001/00000000-0000-0000-0000-000000000002/input.mp4",
        0.42,
        "session",
        model_id="model-a",
    )
    frames = [unpack(call.args[1]) for call in client.publish.call_args_list]
    assert result["total_frames"] == 1
    assert frames[0]["detections"][0]["class_name"] == "model-a"
    assert frames[0]["detections"][0]["confidence"] == 0.42


def test_comparison_runs_each_requested_video_model(own_model_video):
    import json

    import lib.tasks

    _, _, client = own_model_video
    lib.tasks.compare_models_task.run(
        "session",
        "inference/00000000-0000-0000-0000-000000000001/00000000-0000-0000-0000-000000000002/input.mp4",
        True,
        "model-a",
        "model-b",
        0.3,
    )
    result = json.loads(client.setex.call_args.args[2])["results"]
    assert result["a"]["error"] is None
    assert result["b"]["error"] is None
    assert result["a"]["frames"][0]["detections"][0]["class_name"] == "model-a"
    assert result["b"]["frames"][0]["detections"][0]["class_name"] == "model-b"


def test_retry_replaces_only_successful_video_annotations_in_this_job(monkeypatch, tmp_path, sam_stub):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from lib.db import Annotation, Base, Frame, LabelingJob, Project, Video

    subject = _load_subject("labeler.pipeline")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    image = tmp_path / "source.png"
    cv2.imwrite(str(image), np.zeros((8, 8, 3), dtype=np.uint8))
    monkeypatch.setattr(subject, "upload_file", lambda *a: None)
    with Session(engine) as session:
        project = Project(name="source")
        session.add(project)
        session.flush()
        videos = [Video(project_id=project.id, filename=name, minio_key=name) for name in ("ok.mp4", "failed.mp4")]
        session.add_all(videos)
        session.flush()
        frames = [
            Frame(video_id=video.id, frame_number=0, timestamp_s=0, minio_key=video.filename, width=8, height=8)
            for video in videos
        ]
        jobs = [LabelingJob(project_id=project.id, task_type="detect") for _ in range(2)]
        session.add_all(frames + jobs)
        session.flush()
        for frame, job, name in (
            (frames[0], jobs[0], "old"),
            (frames[1], jobs[0], "failed-retained"),
            (frames[0], jobs[1], "other-job"),
        ):
            session.add(Annotation(frame_id=frame.id, job_id=job.id, class_name=name, class_index=0, polygon=[]))
        session.commit()
        result = SimpleNamespace(
            frame_index=0,
            masks=np.zeros((1, 8, 8), dtype=bool),
            boxes=np.array([[1, 1, 2, 2]]),
            scores=np.array([0.9]),
            class_indices=np.array([0]),
        )
        info = SimpleNamespace(file_path=image)
        for attempt in range(2):
            directory = tmp_path / f"attempt-{attempt}"
            directory.mkdir()
            subject.convert_and_store(session, jobs[0], [result], [frames[0]], [info], ["new"], directory)
            session.commit()
        assert sorted(ann.class_name for ann in session.query(Annotation)) == ["failed-retained", "new", "other-job"]


def test_processing_summary_persists_each_appended_video(monkeypatch, sam_stub):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from lib.db import Base, LabelingJob

    subject = _load_subject("labeler.pipeline")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        job = LabelingJob()
        session.add(job)
        session.commit()
        summary = {"videos": [{"video_id": "first", "status": "completed"}]}
        subject._update_job(session, job, processing_summary=summary)
        summary["videos"].append({"video_id": "second", "status": "failed"})
        subject._update_job(session, job, processing_summary=summary)
        session.expire(job)
        assert len(job.processing_summary["videos"]) == 2


@pytest.mark.parametrize(
    "system,machine,dependencies,expected",
    [
        ("Linux", "x86_64", True, False),
        ("Darwin", "x86_64", True, False),
        ("Darwin", "arm64", False, False),
        ("Darwin", "arm64", True, True),
    ],
)
def test_native_capability_requires_apple_silicon_and_runtime(monkeypatch, system, machine, dependencies, expected):
    import lib.tasks

    monkeypatch.setattr(lib.tasks.platform, "system", lambda: system)
    monkeypatch.setattr(lib.tasks.platform, "machine", lambda: machine)
    monkeypatch.setattr(lib.tasks.importlib.util, "find_spec", lambda name: object() if dependencies else None)
    assert lib.tasks._native_video_available() is expected


def test_native_serialization_keeps_small_usable_mask_polygon(monkeypatch, sam_stub):
    subject = _load_subject("labeler.video_labeler")
    mask = np.zeros((8, 8), dtype=np.float32)
    mask[2:4, 2:4] = 1
    result = SimpleNamespace(
        masks=[mask], boxes=np.array([[2, 2, 4, 4]]), scores=[0.8], labels=["camera"], track_ids=None
    )
    detection = subject._result_to_detections(result, 8, 8, ["camera"])[0]
    assert detection["polygon"] is not None
    assert len(detection["polygon"]) >= 6


@pytest.mark.parametrize("early_stop", [False, True])
def test_native_sampling_keeps_negative_assessments_and_rejects_decode_gaps(monkeypatch, sam_stub, early_stop):
    from contextlib import nullcontext

    from tests.test_video_evidence_regressions import _Capture

    subject = _load_subject("labeler.video_labeler")
    core = SimpleNamespace(array=np.array)
    for name, module in {
        "mlx": SimpleNamespace(core=core),
        "mlx.core": core,
        "mlx_vlm.generate": SimpleNamespace(wired_limit=lambda model: nullcontext()),
        "mlx_vlm.models.sam3.generate": SimpleNamespace(
            SimpleTracker=lambda: SimpleNamespace(update=lambda result: result)
        ),
        "mlx_vlm.models.sam3_1.generate": SimpleNamespace(_get_backbone_features=lambda model, pixels: pixels),
        "labeler.sam3_optimized": SimpleNamespace(
            detect_with_backbone_fast=lambda *a, **k: SimpleNamespace(
                scores=[], boxes=[], masks=[], labels=[], track_ids=None
            )
        ),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    predictor = SimpleNamespace(
        model=object(), processor=SimpleNamespace(preprocess_image=lambda image: {"pixel_values": np.asarray(image)})
    )
    monkeypatch.setattr(subject, "_get_predictor", lambda *a: predictor)
    capture = _Capture(25 if early_stop else 61)
    original_get = capture.get
    monkeypatch.setattr(capture, "get", lambda prop: 61 if prop == cv2.CAP_PROP_FRAME_COUNT else original_get(prop))
    monkeypatch.setattr(subject.cv2, "VideoCapture", lambda *a: capture)
    if early_stop:
        with pytest.raises(RuntimeError, match="source frame 25 of 61"):
            subject.process_video_native("synthetic.mp4", ["camera"], sample_fps=2)
    else:
        results = subject.process_video_native("synthetic.mp4", ["camera"], sample_fps=2)
        assert [frame["frame_idx"] for frame in results] == [0, 15, 30, 45, 60]
        assert [frame["timestamp_s"] for frame in results] == [0, 0.5, 1, 1.5, 2]
        assert all(frame["detections"] == [] for frame in results)
    assert capture.released


@pytest.mark.parametrize("fps", [0, -1, float("nan"), float("inf")])
def test_evidence_extraction_rejects_invalid_sampling_before_process_launch(monkeypatch, tmp_path, fps):
    subject = _load_subject("labeler.frame_extractor")
    launched = MagicMock()
    monkeypatch.setattr(subject.subprocess, "run", launched)
    with pytest.raises(ValueError, match="fps"):
        subject.extract_frames(tmp_path / "video.mp4", tmp_path / "frames", fps=fps, use_cache=False)
    launched.assert_not_called()


@pytest.mark.parametrize("native", [False, True])
def test_transient_download_failure_is_retried_then_completes(monkeypatch, tmp_path, sam_stub, native):
    pipeline = _load_subject("labeler.pipeline")
    monkeypatch.setitem(sys.modules, "labeler.pipeline", pipeline)
    subject = _load_subject("labeler.video_labeler" if native else "labeler.text_labeler")
    job = SimpleNamespace(
        id="job",
        project_id="project",
        video_id=None,
        text_prompt="camera",
        class_prompts=None,
        score_threshold=0.2,
        sample_fps=2,
        processing_summary=None,
    )
    video = SimpleNamespace(id="video", filename="video.mp4", minio_key="source", frame_count=30)
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one.return_value = job
    session.query.return_value.filter_by.return_value.all.return_value = [video]
    session.query.return_value.filter_by.return_value.first.return_value = None
    session.query.return_value.filter.return_value.first.return_value = None
    monkeypatch.setattr(subject, "SessionLocal", lambda: session)
    attempts = []

    def download(key, path):
        attempts.append(key)
        if len(attempts) == 1:
            raise ConnectionError("object store unavailable")
        path.write_bytes(b"source")

    monkeypatch.setattr(subject, "download_file", download)
    if native:
        monkeypatch.setattr(
            subject,
            "process_video_native",
            lambda *a, **k: [{"frame_idx": 0, "timestamp_s": 0, "width": 8, "height": 8, "detections": []}],
        )
        monkeypatch.setattr(subject, "_replace_native_observations", lambda *a: [])
        runner = subject.run_video_labeling_pipeline
    else:
        monkeypatch.setattr(
            subject,
            "get_engine",
            lambda: SimpleNamespace(
                iter_segment_frame_paths=lambda *a, **kw: (
                    item
                    for item in [
                        SimpleNamespace(
                            frame_index=0,
                            masks=np.empty((0, 8, 8), dtype=bool),
                            boxes=np.empty((0, 4)),
                            scores=np.empty(0),
                            class_indices=np.empty(0),
                        )
                    ]
                )
            ),
        )
        frame_path = tmp_path / "frame.png"
        cv2.imwrite(str(frame_path), np.zeros((8, 8, 3), dtype=np.uint8))
        monkeypatch.setattr(
            subject,
            "extract_frames",
            lambda *a, **kw: [
                SimpleNamespace(file_path=frame_path, frame_number=0, timestamp_s=0, phash="", width=8, height=8)
            ],
        )
        monkeypatch.setattr(subject, "upload_file", lambda *a: None)
        runner = subject.run_labeling_pipeline

    with pytest.raises(subject.RetryableLabelingError, match="object store unavailable"):
        runner(MagicMock(), "job")
    assert job.status == "retrying"
    assert job.processing_summary["videos"][0]["status"] == "retrying"
    assert runner(MagicMock(), "job")["status"] == "completed"
    assert attempts == ["source", "source"]


@pytest.mark.parametrize("retries,status", [(0, "retrying"), (3, "failed")])
@pytest.mark.parametrize("stage", ["pipeline", "merge"])
def test_task_wrapper_marks_retrying_until_operational_retries_exhausted(monkeypatch, retries, status, stage):
    import lib.db
    import lib.tasks
    from labeler.errors import RetryableLabelingError

    pipeline = SimpleNamespace(
        run_video_labeling_pipeline=MagicMock(
            side_effect=RetryableLabelingError("storage temporarily down") if stage == "pipeline" else None,
            return_value={"status": "completed"},
        )
    )
    if stage == "merge":
        monkeypatch.setattr(
            lib.tasks,
            "_merge_completed_labeling_job",
            MagicMock(side_effect=ConnectionError("storage temporarily down")),
        )
    monkeypatch.setitem(sys.modules, "labeler.video_labeler", pipeline)
    monkeypatch.setattr(lib.tasks, "_native_video_available", lambda: True)
    job = SimpleNamespace(status="labeling", processing_summary={"videos": [{"status": "retrying"}]})
    session = MagicMock()
    session.query.return_value.filter_by.return_value.one.return_value = job
    monkeypatch.setattr(lib.db, "SessionLocal", lambda: session)
    lib.tasks.label_video.push_request(retries=retries, called_directly=True)
    try:
        with pytest.raises(RetryableLabelingError, match="storage temporarily down"):
            lib.tasks.label_video.run("job", merge_into="parent" if stage == "merge" else None)
    finally:
        lib.tasks.label_video.pop_request()
    assert job.status == status


def test_text_successful_clip_evidence_survives_later_transient_failure_and_retry(monkeypatch, tmp_path, sam_stub):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from labeler.errors import RetryableLabelingError
    from lib.db import Annotation, Base, LabelingJob, Project, Video

    pipeline = _load_subject("labeler.pipeline")
    monkeypatch.setitem(sys.modules, "labeler.pipeline", pipeline)
    subject = _load_subject("labeler.text_labeler")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(name="same source")
    session.add(project)
    session.flush()
    videos = [Video(project_id=project.id, filename=f"{index}.mp4", minio_key=f"source-{index}") for index in range(2)]
    job = LabelingJob(
        project_id=project.id, text_prompt="camera", task_type="detect", sample_fps=2, score_threshold=0.2
    )
    session.add_all(videos + [job])
    session.commit()
    job_id = job.id
    failed = False

    def download(key, path):
        nonlocal failed
        if key == "source-1" and not failed:
            failed = True
            raise ConnectionError("temporary storage outage")
        path.write_bytes(b"source")

    monkeypatch.setattr(subject, "SessionLocal", lambda: session)
    monkeypatch.setattr(subject, "download_file", download)
    monkeypatch.setattr(subject, "upload_file", lambda *a: None)
    monkeypatch.setattr(pipeline, "upload_file", lambda *a: None)

    def extract(source, directory, **kwargs):
        directory.mkdir()
        path = directory / "frame.png"
        cv2.imwrite(str(path), np.zeros((8, 8, 3), dtype=np.uint8))
        return [SimpleNamespace(file_path=path, frame_number=0, timestamp_s=0, phash="", width=8, height=8)]

    monkeypatch.setattr(subject, "extract_frames", extract)
    monkeypatch.setattr(
        subject,
        "get_engine",
        lambda: SimpleNamespace(
            iter_segment_frame_paths=lambda *a, **kw: (
                item
                for item in [
                    SimpleNamespace(
                        frame_index=0,
                        masks=np.zeros((1, 8, 8), dtype=bool),
                        boxes=np.array([[1, 1, 2, 2]]),
                        scores=np.array([0.9]),
                        class_indices=np.array([0]),
                    )
                ]
            )
        ),
    )
    with pytest.raises(RetryableLabelingError):
        subject.run_labeling_pipeline(MagicMock(), job_id)
    assert session.query(Annotation).count() == 1
    assert subject.run_labeling_pipeline(MagicMock(), job_id)["status"] == "completed"
    assert session.query(Annotation).count() == 2
    session.close()


def test_add_class_merge_remaps_local_tracks_keeps_run_metadata_and_invalidates_artifact(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    import lib.db
    import lib.tasks
    from lib.db import Annotation, Base, Frame, LabelingJob, Project, Video

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = Session(engine)
    project = Project(name="same collection")
    session.add(project)
    session.flush()
    videos = [Video(project_id=project.id, filename=f"{index}.mp4", minio_key=f"source-{index}") for index in range(2)]
    session.add_all(videos)
    session.flush()
    frames = [Frame(video_id=video.id, frame_number=0, timestamp_s=0, minio_key=video.minio_key) for video in videos]
    master = LabelingJob(project_id=project.id, status="completed", result_minio_key="old.zip")
    child = LabelingJob(
        project_id=project.id,
        status="completed",
        sample_fps=3,
        score_threshold=0.4,
        processing_summary={"coverage": "sampled", "videos": [{"video_id": str(videos[0].id), "status": "completed"}]},
    )
    session.add_all(frames + [master, child])
    session.flush()
    for job, frame, track, name in (
        (master, frames[0], 7, "parent"),
        (master, frames[1], 2, "parent"),
        (child, frames[0], 7, "new"),
        (child, frames[0], 7, "new"),
        (child, frames[1], 2, "new"),
    ):
        session.add(
            Annotation(job_id=job.id, frame_id=frame.id, class_name=name, class_index=0, polygon=[], track_id=track)
        )
    session.commit()
    master_id, child_id = master.id, child.id
    video_ids = [video.id for video in videos]
    frame_ids = [frame.id for frame in frames]
    monkeypatch.setattr(lib.db, "SessionLocal", lambda: session)
    monkeypatch.setattr(lib.tasks, "_native_video_available", lambda: True)
    monkeypatch.setitem(
        sys.modules,
        "labeler.video_labeler",
        SimpleNamespace(run_video_labeling_pipeline=lambda *a: {"status": "completed"}),
    )
    lib.tasks.label_video.run(child_id, merge_into=master_id)

    master = session.get(LabelingJob, master_id)
    child = session.get(LabelingJob, child_id)
    assert master.result_minio_key is None
    assert child is not None and child.sample_fps == 3 and child.score_threshold == 0.4
    assert child.processing_summary["merged_into"] == str(master_id)
    rows = session.query(Annotation, Frame.video_id).join(Frame).filter(Annotation.job_id == master_id).all()
    assert len(rows) == 5
    for video_id in video_ids:
        parents = {ann.track_id for ann, vid in rows if vid == video_id and ann.class_name == "parent"}
        new = {ann.track_id for ann, vid in rows if vid == video_id and ann.class_name == "new"}
        assert parents.isdisjoint(new)
        assert len(new) == 1
    assert master.processing_summary["merged_runs"][0]["job_id"] == str(child_id)
    # Redelivery after export must not duplicate the child run or invalidate
    # the unchanged, newly generated parent artifact.
    master.result_minio_key = "fresh.zip"
    session.add(
        Annotation(job_id=child_id, frame_id=frame_ids[0], class_name="new", class_index=0, polygon=[], track_id=7)
    )
    session.commit()
    lib.tasks.label_video.run(child_id, merge_into=master_id)
    master = session.get(LabelingJob, master_id)
    assert master.result_minio_key == "fresh.zip"
    assert len(master.processing_summary["merged_runs"]) == 1
    assert session.query(Annotation).filter_by(job_id=master_id).count() == 5
    assert session.query(Annotation).filter_by(job_id=child_id).count() == 0
    session.close()
