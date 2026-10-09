"""Text alias integration preserves evidence while dense storage stays bounded."""

import gc
import os
import weakref
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from labeler.result_store import DiskResultSequence
from labeler.sam3_engine import SegmentationResult
from lib.db import Base, Frame, LabelingJob, Project, Video
from tests.test_sam3_path_inference import make_engine
from tests.test_text_retry_review import text_run as _text_run
from tests.test_video_evidence_regressions import _load_subject

pytestmark = pytest.mark.no_auth_bypass


@pytest.fixture
def process_run(monkeypatch, tmp_path):
    subject = _load_subject("labeler.text_labeler")
    database = create_engine("sqlite://")
    Base.metadata.create_all(database)
    session = Session(database)
    project = Project(name="synthetic")
    session.add(project)
    session.flush()
    video = Video(project_id=project.id, filename="synthetic.mp4", minio_key="source")
    job = LabelingJob(text_prompt="pipe", task_type="detect", sample_fps=2, score_threshold=0.25)
    session.add_all([video, job])
    session.commit()
    source = tmp_path / "frame.png"
    Image.new("RGB", (32, 24)).save(source)
    infos = [
        SimpleNamespace(
            file_path=source,
            frame_number=100 + ordinal * 15,
            timestamp_s=7 + ordinal / 2,
            phash="",
            width=32,
            height=24,
        )
        for ordinal in range(4)
    ]
    # Different names retain separate upload/database identities for each sample.
    for ordinal, info in enumerate(infos):
        info.file_path = tmp_path / f"sample-{ordinal}.png"
        info.file_path.write_bytes(source.read_bytes())
    monkeypatch.setattr(subject, "download_file", lambda *a: None)
    monkeypatch.setattr(subject, "upload_file", lambda *a: None)
    monkeypatch.setattr(subject, "extract_frames", lambda *a, **kw: infos)
    yield SimpleNamespace(subject=subject, session=session, video=video, job=job, infos=infos, tmpdir=tmp_path)
    session.close()
    database.dispose()


def result(ordinal, alias):
    masks = np.zeros((1, 24, 32), dtype=bool)
    masks[:, 2 + alias : 8 + alias, 4:12] = True
    return SegmentationResult(
        frame_index=ordinal,
        masks=masks,
        boxes=np.array([[4, 2 + alias, 11, 7 + alias]], dtype=np.float32),
        scores=np.array([0.5 + alias / 10], dtype=np.float32),
        class_indices=np.array([99]),
    )


class AliasEngine:
    def __init__(self, ordinals=None):
        self.ordinals = ordinals
        self.calls = []
        self.closed = []

    def iter_segment_frame_paths(self, paths, prompt, *, threshold, working_dir):
        self.calls.append((list(paths), prompt, threshold, working_dir))
        alias = len(self.calls) - 1
        try:
            for ordinal in range(len(paths)) if self.ordinals is None else self.ordinals:
                yield result(ordinal, alias)
        finally:
            self.closed.append(prompt)


def process(run, engine, prompts=None):
    return run.subject._process_single_video(
        run.session,
        run.job,
        run.video,
        engine,
        run.tmpdir,
        prompts if prompts is not None else [{"name": "pipe", "prompts": ["pipe", "red pipe"]}],
    )


def assert_two_aliases(sequence, count):
    assert isinstance(sequence, DiskResultSequence)
    assert len(sequence) == count
    for _ in range(2):
        for ordinal, item in enumerate(sequence):
            assert item.frame_index == ordinal
            expected = np.zeros((2, 24, 32), dtype=bool)
            expected[0, 2:8, 4:12] = True
            expected[1, 3:9, 4:12] = True
            np.testing.assert_array_equal(item.masks, expected)
            np.testing.assert_array_equal(item.boxes, [[4, 2, 11, 7], [4, 3, 11, 8]])
            np.testing.assert_array_equal(item.scores, np.array([0.5, 0.6], dtype=np.float32))
            np.testing.assert_array_equal(item.class_indices, [0, 0])


def test_two_aliases_keep_every_sample_and_repeatable_values(process_run):
    engine = AliasEngine()
    sequence, frames, infos = process(process_run, engine)
    assert len(sequence) == len(frames) == len(infos) == 4
    assert [frame.frame_number for frame in frames] == [100, 115, 130, 145]
    assert [frame.timestamp_s for frame in frames] == [7, 7.5, 8, 8.5]
    assert [(frame.width, frame.height) for frame in frames] == [(32, 24)] * 4
    assert infos == process_run.infos
    assert [call[1] for call in engine.calls] == ["pipe", "red pipe"]
    assert all(call[0] == [info.file_path for info in infos] and call[2] == 0.25 for call in engine.calls)
    assert engine.calls[0][3] != engine.calls[1][3]
    assert engine.closed == ["pipe", "red pipe"]
    assert_two_aliases(sequence, 4)


@pytest.mark.parametrize("ordinals", [[0, 1], [0, 1, 2, 3, 4], [0, 2, 1, 3], [100, 115, 130, 145]])
def test_invalid_result_count_or_ordinal_fails_and_closes_generator(process_run, ordinals):
    engine = AliasEngine(ordinals)
    with pytest.raises(ValueError, match="Segmentation and sampled-frame") as error:
        process(process_run, engine)
    assert error.value.__traceback__ is not None
    assert engine.closed == ["pipe"]


def test_no_prompts_fails_without_inference(process_run):
    engine = AliasEngine()
    with pytest.raises(ValueError, match="At least one labeling prompt"):
        process(process_run, engine, [])
    assert engine.calls == []


def test_empty_decode_fails_before_frames_or_inference(process_run):
    process_run.infos.clear()
    engine = AliasEngine()
    with pytest.raises(ValueError, match="No sampled frames decoded"):
        process(process_run, engine)
    assert process_run.session.query(Frame).count() == 0
    assert engine.calls == []


def test_alias_flattening_preserves_first_class_order_and_single_alias_store(process_run):
    engine = AliasEngine()
    sequence, _, _ = process(
        process_run,
        engine,
        [
            {"name": "pipe", "prompts": ["pipe", "red pipe"]},
            {"name": "camera", "prompt": "camera"},
            {"name": "pipe", "prompt": "steel pipe"},
        ],
    )
    assert [call[1] for call in engine.calls] == ["pipe", "red pipe", "camera", "steel pipe"]
    np.testing.assert_array_equal(sequence[2].class_indices, [0, 0, 1, 0])
    single, _, _ = process(process_run, AliasEngine(), [{"name": "camera", "prompt": "camera"}])
    assert isinstance(single, DiskResultSequence)
    assert single.directory.name.endswith("_0")
    assert single[3].frame_index == 3
    np.testing.assert_array_equal(single[3].class_indices, [0])


def test_result_write_failure_closes_suspended_generator(process_run, monkeypatch):
    append = DiskResultSequence.append

    def fail_late(store, item):
        if item.frame_index == 2:
            raise OSError("result disk full")
        append(store, item)

    monkeypatch.setattr(DiskResultSequence, "append", fail_late)
    engine = AliasEngine()
    with pytest.raises(OSError, match="disk full") as error:
        process(process_run, engine)
    assert error.value.__traceback__ is not None
    assert engine.closed == ["pipe"]


def test_3600_two_alias_frames_have_bounded_dense_arrays_and_descriptors(process_run, monkeypatch):
    count = 3600
    first = process_run.infos[0]
    process_run.infos.clear()
    source_bytes = first.file_path.read_bytes()
    for ordinal in range(count):
        path = process_run.tmpdir / f"long-{ordinal}.png"
        path.write_bytes(source_bytes)
        process_run.infos.append(
            SimpleNamespace(
                **{
                    **vars(first),
                    "file_path": path,
                    "frame_number": 100 + ordinal * 15,
                    "timestamp_s": 7 + ordinal / 2,
                }
            )
        )
    # Uploaded sample metadata is small and may grow; dense arrays may not.
    arrays, samples, read_samples = [], [], []
    append, getitem = DiskResultSequence.append, DiskResultSequence.__getitem__
    fd_dir = Path("/dev/fd") if Path("/dev/fd").exists() else Path("/proc/self/fd")
    descriptors_before = len(os.listdir(fd_dir))
    descriptor_samples = []
    load, savez = np.load, np.savez

    def counted_load(*args, **kwargs):
        archive = load(*args, **kwargs)
        descriptor_samples.append(len(os.listdir(fd_dir)))
        return archive

    def counted_savez(*args, **kwargs):
        descriptor_samples.append(len(os.listdir(fd_dir)))
        return savez(*args, **kwargs)

    monkeypatch.setattr(np, "load", counted_load)
    monkeypatch.setattr(np, "savez", counted_savez)

    def track(item):
        arrays.extend(weakref.ref(getattr(item, field)) for field in ("masks", "boxes", "scores", "class_indices"))

    def counted_append(store, item):
        track(item)
        append(store, item)
        if item.frame_index % 100 == 0 or item.frame_index == count - 1:
            gc.collect()
            samples.append((sum(ref() is not None for ref in arrays), len(os.listdir(fd_dir))))

    def counted_read(store, ordinal):
        assert not isinstance(ordinal, slice)
        item = getitem(store, ordinal)
        track(item)
        if ordinal % 100 == 0 or ordinal == count - 1:
            read_samples.append(sum(ref() is not None for ref in arrays))
        return item

    monkeypatch.setattr(DiskResultSequence, "append", counted_append)
    monkeypatch.setattr(DiskResultSequence, "__getitem__", counted_read)
    sequence, frames, infos = process(process_run, AliasEngine())
    assert len(sequence) == len(frames) == len(infos) == count
    gc.collect()
    assert all(ref() is None for ref in arrays)
    assert max(sample[0] for sample in samples) <= 12
    assert max(descriptor_samples) <= descriptors_before + 1
    assert [frame.frame_number for frame in frames] == [100 + ordinal * 15 for ordinal in range(count)]
    assert [frame.timestamp_s for frame in frames] == [7 + ordinal / 2 for ordinal in range(count)]
    assert max(sample[0] for sample in samples[:37]) == max(sample[0] for sample in samples[37:74])
    assert_two_aliases(sequence, count)
    gc.collect()
    assert all(ref() is None for ref in arrays)
    assert max(read_samples) <= 12
    assert max(descriptor_samples) <= descriptors_before + 1
    disk_bytes = sum(path.stat().st_size for path in process_run.tmpdir.rglob("*.npz"))
    print(
        f"two-alias resource proof: samples={len(samples)}, early={samples[:5]}, late={samples[-5:]}, peak_append_arrays={max(s[0] for s in samples)}, peak_read_arrays={max(read_samples)}, fd_baseline={descriptors_before}, fd_peak={max(descriptor_samples)}, result_files={len(list(process_run.tmpdir.rglob('*.npz')))}, disk_bytes={disk_bytes}"
    )


@pytest.fixture(name="text_run")
def retry_fixture(monkeypatch):
    yield from _text_run.__wrapped__(monkeypatch)


@pytest.fixture
def persistence_run(text_run, monkeypatch):
    from lib.db import Annotation
    from tests.test_text_retry_review import _first_attempt, _rows

    _first_attempt(text_run)
    with text_run.sessions() as session:
        for row, status in zip(session.query(Annotation).all(), ["accepted", "rejected", "pending"], strict=True):
            row.status, row.class_name = status, f"edited-{status}"
            row.polygon, row.bbox = [0, 0, 0.5, 0, 0.5, 0.5], [0.25, 0.25, 0.5, 0.5]
        session.get(LabelingJob, text_run.job_id).result_minio_key = "partial-reviewed-export.zip"
        session.commit()
        original = _rows(session, text_run.job_id)

    engine = make_engine(3)
    directories, opened, closed, stored = [], [], [], []
    open_image = Image.open
    from labeler.sam3_frame_storage import PathBackedSam3Session

    close = PathBackedSam3Session.close
    # This is the real persistence function's globals, including the real writer.
    pipeline_globals = text_run.subject.replace_raw_observations.__globals__
    store_observation = pipeline_globals["_store_observation"]

    def extract(source, directory, **kwargs):
        directories.append(directory.parent)
        directory.mkdir()
        infos = []
        for ordinal in range(3):
            path = directory / f"sample-{ordinal}.png"
            Image.new("RGB", (32, 24)).save(path)
            infos.append(
                SimpleNamespace(
                    file_path=path,
                    frame_number=100 + ordinal * 15,
                    timestamp_s=7 + ordinal / 2,
                    phash="",
                    width=32,
                    height=24,
                )
            )
        return infos

    def counted_open(*args, **kwargs):
        assert sum(ref() is not None and ref().fp is not None for ref in opened) == 0
        image = open_image(*args, **kwargs)
        opened.append(weakref.ref(image))
        return image

    def counted_close(session):
        close(session)
        closed.append(True)

    def counted_store(*args):
        annotation = store_observation(*args)
        stored.append(annotation.frame_id)
        return annotation

    monkeypatch.setattr(text_run.subject, "get_engine", lambda: engine)
    monkeypatch.setattr(text_run.subject, "extract_frames", extract)
    monkeypatch.setattr(Image, "open", counted_open)
    monkeypatch.setattr(PathBackedSam3Session, "close", counted_close)
    monkeypatch.setitem(pipeline_globals, "_store_observation", counted_store)
    return SimpleNamespace(
        run=text_run,
        engine=engine,
        directories=directories,
        opened=opened,
        closed=closed,
        stored=stored,
        original=original,
        extract=extract,
    )


@pytest.mark.parametrize(
    "stage", ["decode", "history_write", "history_read", "result_write", "result_read", "persistence_read"]
)
def test_late_io_failure_rolls_back_clip_and_preserves_prior_review(persistence_run, monkeypatch, stage):
    import torch

    from labeler.errors import RetryableLabelingError
    from lib.db import Annotation
    from tests.test_text_retry_review import _rows

    state = persistence_run
    run = state.run
    calls = []
    if stage == "decode":

        def bad_decode(*args, **kwargs):
            infos = state.extract(*args, **kwargs)
            infos[2].file_path.write_bytes(b"invalid image")
            return infos

        monkeypatch.setattr(run.subject, "extract_frames", bad_decode)
    else:
        module, method, failure_call = {
            "history_write": (torch, "save", 4),
            "history_read": (torch, "load", 2),
            "result_write": (np, "savez", 2),
            "result_read": (np, "load", 2),
            "persistence_read": (np, "load", 5),
        }[stage]
        original = getattr(module, method)

        def fail_late(*args, **kwargs):
            calls.append(True)
            if len(calls) == failure_call:
                if stage == "history_write":
                    Path(args[1]).write_bytes(b"partial tensor")
                elif stage == "result_write":
                    args[0].write(b"partial archive")
                if stage == "persistence_read":
                    # Validation read all three frames; first observation was really added.
                    assert len(state.stored) == 1
                raise OSError(f"late {stage} failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(module, method, fail_late)

    with pytest.raises(RetryableLabelingError) as error:
        run.subject.run_labeling_pipeline(MagicMock(), run.job_id)
    assert error.value.__traceback__ is not None
    assert state.engine.model.calls[:2] == [0, 1]
    assert state.closed == [True]
    assert state.directories and all(not directory.exists() for directory in state.directories)
    assert all(ref() is None or ref().fp is None for ref in state.opened)
    with run.sessions() as session:
        assert _rows(session, run.job_id) == state.original
        failed_frames = session.query(Frame).filter_by(video_id=run.video_ids[1]).all()
        assert failed_frames == []
        assert session.query(Annotation).count() == 3
        job = session.get(LabelingJob, run.job_id)
        assert job.status == "retrying"
        assert job.result_minio_key == "partial-reviewed-export.zip"
        assert job.processed_frames == 1
        entries = {entry["video_id"]: entry for entry in job.processing_summary["videos"]}
        assert entries[str(run.video_ids[0])]["status"] == "completed"
        assert entries[str(run.video_ids[1])]["status"] == "retrying"
        assert "sampled_frames" not in entries[str(run.video_ids[1])]
        assert "assessed_timestamps_s" not in entries[str(run.video_ids[1])]


def test_disk_sequence_survives_validation_and_real_persistence_then_cleans_up(persistence_run):
    from lib.db import Annotation
    from tests.test_text_retry_review import _rows

    state, run = persistence_run, persistence_run.run
    response = run.subject.run_labeling_pipeline(MagicMock(), run.job_id)
    assert response["status"] == "completed"
    assert state.engine.model.calls == [0, 1, 2]
    assert state.closed == [True]
    assert len(state.stored) == 3
    assert all(not directory.exists() for directory in state.directories)
    assert all(ref() is None or ref().fp is None for ref in state.opened)
    with run.sessions() as session:
        after = _rows(session, run.job_id)
        assert {key: after[key] for key in state.original} == state.original
        frames = session.query(Frame).filter_by(video_id=run.video_ids[1]).order_by(Frame.frame_number).all()
        assert [frame.frame_number for frame in frames] == [100, 115, 130]
        assert [frame.timestamp_s for frame in frames] == [7, 7.5, 8]
        for frame in frames:
            row = session.query(Annotation).filter_by(frame_id=frame.id).one()
            assert row.class_name == "camera" and row.class_index == 0
            assert row.track_id is None
            assert row.confidence == pytest.approx(0.6)
            assert row.bbox == pytest.approx([15 / 64, 11 / 48, 7 / 32, 5 / 24])
        job = session.get(LabelingJob, run.job_id)
        assert job.processed_frames == job.total_frames == 4
        entry = next(item for item in job.processing_summary["videos"] if item["video_id"] == str(run.video_ids[1]))
        assert entry["sampled_frames"] == 3
        assert entry["assessed_timestamps_s"] == [7, 7.5, 8]
