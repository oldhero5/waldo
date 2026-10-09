"""Offline iterator behavior with real upstream session and disk-backed history."""

import gc
import os
import weakref
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import torch
from PIL import Image
from transformers import Sam3ImageProcessor
from transformers.models.sam3_video.modeling_sam3_video import Sam3VideoInferenceSession

from labeler import sam3_engine
from labeler.sam3_engine import Sam3Engine
from labeler.sam3_frame_storage import PathBackedSam3Session
from lib.config import settings


class Processor:
    def __init__(self, *, single=True):
        self.image_processor = Sam3ImageProcessor(size={"height": 32, "width": 32})
        self.single = single
        self.prompts = []
        self.pixels = []

    def __call__(self, *, images, return_tensors):
        if self.single:
            assert len(images) == 1
        processed = self.image_processor(images=images, return_tensors=return_tensors)
        self.pixels.append(weakref.ref(processed["pixel_values"]))
        return processed

    def add_text_prompt(self, session, text):
        self.prompts.append(text)
        session.add_prompt(text)


class Model:
    def __init__(self, count):
        self.count = count
        self.calls = []
        self.sessions = []
        self.session_ids = []
        self.dense = []

    def __call__(self, *, inference_session, frame_idx):
        # Strict signature rejects streaming frame= calls. Real history drives
        # the next output, so a reset/chunk changes the result or fails the read.
        session = inference_session
        assert isinstance(session, Sam3VideoInferenceSession)
        assert session.num_frames == self.count
        self.sessions.append(weakref.ref(session))
        self.session_ids.append(id(session))
        self.calls.append(frame_idx)
        pixels = session.get_frame(frame_idx)
        self.dense.append(weakref.ref(pixels))
        idx = session.obj_id_to_idx(1)
        if frame_idx:
            previous = session.get_output(idx, frame_idx - 1, "maskmem_features", is_conditioning_frame=False)
            value = int(previous[0, 0, 0]) + 1
            self.dense.append(weakref.ref(previous))
        else:
            value = 1
        mask = torch.zeros(1, 1, 24, 32)
        mask[:, :, 3:9, 4:12] = 1
        memory = torch.full((32, 24, 4), value, dtype=torch.float32)
        high_res = mask.clone()
        self.dense.extend(weakref.ref(tensor) for tensor in (mask, memory, high_res))
        session.store_output(
            idx,
            frame_idx,
            output_value={"pred_masks": mask, "high_res_masks": high_res, "maskmem_features": memory},
            is_conditioning_frame=False,
        )
        return SimpleNamespace(frame_idx=frame_idx, obj_id_to_mask={1: mask}, obj_id_to_score={1: 0.6})


def make_engine(count, *, single=True):
    engine = Sam3Engine.__new__(Sam3Engine)
    engine.device = "cpu"
    engine.torch_dtype = torch.float32
    engine.processor = Processor(single=single)
    engine.model = Model(count)
    return engine


def paths(tmp_path, count):
    source = tmp_path / "image.png"
    Image.new("RGB", (32, 24), (40, 60, 80)).save(source)
    return [source] * count


def assert_mask(result, ordinal):
    assert result.frame_index == ordinal
    expected = np.zeros((1, 24, 32), dtype=bool)
    expected[:, 3:9, 4:12] = True
    np.testing.assert_array_equal(result.masks, expected)
    np.testing.assert_array_equal(result.boxes, [[4, 3, 11, 8]])
    np.testing.assert_array_equal(result.scores, np.array([0.6], dtype=np.float32))


def test_one_full_count_session_offline_order_matches_eager(tmp_path):
    frames = paths(tmp_path, 4)
    eager, streamed = make_engine(4, single=False), make_engine(4)
    with Image.open(frames[0]) as image:
        expected = eager.segment_frames([image] * 4, "pipe", threshold=0.5)
    actual = list(streamed.iter_segment_frame_paths(frames, "pipe", threshold=0.5, working_dir=tmp_path / "h"))
    for ordinal, (got, want) in enumerate(zip(actual, expected, strict=True)):
        assert_mask(got, ordinal)
        for field in ("masks", "boxes", "scores"):
            np.testing.assert_array_equal(getattr(got, field), getattr(want, field))
    assert streamed.model.calls == [0, 1, 2, 3]
    assert streamed.processor.prompts == ["pipe"]
    assert len(set(streamed.model.session_ids)) == 1
    assert all(ref() is None for ref in streamed.model.sessions)


def test_empty_does_not_create_session_or_process_images(tmp_path, monkeypatch):
    engine = make_engine(0)

    def forbidden(*args, **kwargs):
        pytest.fail("empty iterator must not allocate a session")

    monkeypatch.setattr(PathBackedSam3Session, "__init__", forbidden)
    assert list(engine.iter_segment_frame_paths([], "pipe", working_dir=tmp_path)) == []
    assert engine.processor.prompts == []
    assert engine.model.calls == []


@pytest.mark.parametrize("override, expected_count", [(None, 0), (0.5, 1)])
def test_default_and_override_threshold(tmp_path, monkeypatch, override, expected_count):
    monkeypatch.setattr(settings, "sam3_score_threshold", 0.7)
    engine = make_engine(1)
    actual = list(
        engine.iter_segment_frame_paths(paths(tmp_path, 1), "pipe", threshold=override, working_dir=tmp_path / "h")
    )
    assert actual[0].masks.shape == (expected_count, 24, 32)


@pytest.mark.parametrize(
    "termination", ["success", "close", "prompt", "model", "processor", "write", "read", "extract"]
)
def test_close_precedes_device_cleanup_on_all_exits(tmp_path, monkeypatch, termination):
    engine = make_engine(3)
    closed = []
    close = PathBackedSam3Session.close

    def record_close(session):
        closed.append(weakref.ref(session))
        close(session)

    def cleanup(device):
        assert len(closed) == 1
        assert device == "cpu"

    def fail(*args, **kwargs):
        raise OSError("injected failure")

    monkeypatch.setattr(PathBackedSam3Session, "close", record_close)
    monkeypatch.setattr(sam3_engine, "_cleanup", cleanup)
    if termination == "prompt":
        monkeypatch.setattr(engine.processor, "add_text_prompt", fail)
    elif termination == "model":
        engine.model = fail
    elif termination == "processor":
        monkeypatch.setattr(engine.processor.image_processor, "preprocess", fail)
    elif termination == "write":
        monkeypatch.setattr(torch, "save", fail)
    elif termination == "read":
        monkeypatch.setattr(torch, "load", fail)
    elif termination == "extract":
        monkeypatch.setattr(sam3_engine, "_extract_masks_from_output", fail)
    iterator = engine.iter_segment_frame_paths(paths(tmp_path, 3), "pipe", working_dir=tmp_path / "h")
    if termination == "close":
        assert_mask(next(iterator), 0)
        iterator.close()
    elif termination == "success":
        assert len(list(iterator)) == 3
    else:
        with pytest.raises(OSError, match="injected failure"):
            list(iterator)
    assert len(closed) == 1


def test_3600_dense_frames_have_bounded_live_arrays_and_image_descriptors(tmp_path, monkeypatch):
    from labeler.result_store import DiskResultSequence

    count = 3600
    frames = paths(tmp_path, count)
    engine = make_engine(count)
    store = DiskResultSequence(tmp_path / "results")
    opened, arrays, samples = [], [], []
    open_image = Image.open
    fd_dir = Path("/dev/fd") if Path("/dev/fd").exists() else Path("/proc/self/fd")
    descriptors_before = len(os.listdir(fd_dir))

    def counted_open(*args, **kwargs):
        assert sum(ref() is not None and ref().fp is not None for ref in opened) == 0
        image = open_image(*args, **kwargs)
        opened.append(weakref.ref(image))
        return image

    monkeypatch.setattr(Image, "open", counted_open)
    iterator = engine.iter_segment_frame_paths(frames, "pipe", working_dir=tmp_path / "history")
    for ordinal, item in enumerate(iterator):
        assert_mask(item, ordinal)
        arrays.extend(weakref.ref(getattr(item, field)) for field in ("masks", "boxes", "scores"))
        store.append(item)
        del item
        if ordinal % 100 == 0 or ordinal == count - 1:
            gc.collect()
            sample = (
                sum(ref() is not None for ref in engine.model.dense),
                sum(ref() is not None for ref in engine.processor.pixels),
                sum(ref() is not None for ref in arrays),
                sum(ref() is not None and ref().fp is not None for ref in opened),
                len(os.listdir(fd_dir)),
            )
            samples.append(sample)
    gc.collect()
    assert len(store) == count
    assert engine.model.calls == list(range(count))
    assert len(set(engine.model.session_ids)) == 1
    assert engine.processor.prompts == ["pipe"]
    assert all(ref() is None for ref in engine.model.sessions + engine.model.dense + engine.processor.pixels + arrays)
    assert all(sample[0] == 0 and sample[1] == 0 and sample[2] <= 3 and sample[3] == 0 for sample in samples)
    assert max(sample[4] for sample in samples) <= descriptors_before + 1
    assert samples[:5] == samples[-5:]
    assert len(list((tmp_path / "history").rglob("*.pt"))) == count * 3
    assert len(list(store.directory.glob("*.npz"))) == count
    assert_mask(store[0], 0)
    assert_mask(store[-1], count - 1)
    print(
        f"resource proof: early={samples[:5]}, late={samples[-5:]}, history_files={count * 3}, results={len(store)}, disk_bytes={sum(path.stat().st_size for path in tmp_path.rglob('*') if path.is_file())}"
    )


@pytest.mark.parametrize("termination", ["success", "close", "failure"])
def test_mps_history_read_lease_released_before_device_cleanup(tmp_path, monkeypatch, termination):
    if not torch.backends.mps.is_available():
        pytest.skip("MPS unavailable")
    engine = make_engine(3)
    engine.device = "mps"
    closed = []
    original_close = PathBackedSam3Session.close

    def close(session):
        source = weakref.ref(session._read_lease._value)
        assert source() is not None
        original_close(session)
        assert source() is None
        closed.append(True)

    def cleanup(device):
        assert device == "mps"
        assert closed == [True]

    monkeypatch.setattr(PathBackedSam3Session, "close", close)
    monkeypatch.setattr(sam3_engine, "_cleanup", cleanup)
    iterator = engine.iter_segment_frame_paths(paths(tmp_path, 3), "pipe", working_dir=tmp_path / "history")
    assert_mask(next(iterator), 0)
    assert_mask(next(iterator), 1)
    if termination == "close":
        iterator.close()
    elif termination == "failure":

        def fail(*args, **kwargs):
            raise OSError("extraction failed")

        monkeypatch.setattr(sam3_engine, "_extract_masks_from_output", fail)
        with pytest.raises(OSError, match="extraction failed"):
            next(iterator)
    else:
        assert len(list(iterator)) == 1
    assert closed == [True]
