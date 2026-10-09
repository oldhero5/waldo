"""Repeatable result access without retaining arrays or permitting pickle."""

import gc
import weakref

import numpy as np
import pytest

from labeler.result_store import DiskResultSequence
from labeler.sam3_engine import SegmentationResult


def result(index=17, *, empty=False, classes=None):
    count = 0 if empty else 2
    return SegmentationResult(
        index,
        np.arange(count * 24).reshape(count, 4, 6) % 2 == 1,
        np.arange(count * 4, dtype=np.float64).reshape(count, 4),
        np.arange(count, dtype=np.float16),
        classes,
    )


def assert_result(actual, expected):
    assert actual.frame_index == expected.frame_index
    for name in ("masks", "boxes", "scores", "class_indices"):
        got, want = getattr(actual, name), getattr(expected, name)
        if want is None:
            assert got is None
        else:
            np.testing.assert_array_equal(got, want)
            assert got.dtype == want.dtype


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("classes", [None, "present"])
def test_roundtrip_dtypes_nulls_and_repeatable_access(tmp_path, empty, classes):
    count = 0 if empty else 2
    source = result(empty=empty, classes=np.arange(count, dtype=np.int16) if classes else None)
    expected = result(empty=empty, classes=np.arange(count, dtype=np.int16) if classes else None)
    refs = [weakref.ref(source.masks), weakref.ref(source.boxes), weakref.ref(source.scores)]
    store = DiskResultSequence(tmp_path / "results")
    store.append(source)
    del source
    gc.collect()
    assert all(ref() is None for ref in refs)
    assert len(store) == 1
    first, second = store[0], store[0]
    assert_result(first, expected)
    assert_result(second, expected)
    assert not np.shares_memory(first.masks, second.masks)
    for path in store.directory.glob("*.npz"):
        with np.load(path, allow_pickle=False) as archive:
            assert all(archive[name].dtype != object for name in archive.files)
    assert_result(list(store)[0], expected)
    assert_result(list(store)[0], expected)


def test_sequence_indices_slices_and_repeated_iteration(tmp_path):
    store = DiskResultSequence(tmp_path)
    for index in (10, 30, 90):
        store.append(result(index))
    assert [item.frame_index for item in store] == [10, 30, 90]
    assert [item.frame_index for item in store] == [10, 30, 90]
    assert store[-1].frame_index == 90
    assert store[-3].frame_index == 10
    assert [item.frame_index for item in store[::2]] == [10, 90]
    assert [item.frame_index for item in store[::-1]] == [90, 30, 10]
    assert store[3:] == []
    for index in (-4, 3):
        with pytest.raises(IndexError):
            store[index]
    with pytest.raises(TypeError):
        store[1.5]
    with pytest.raises(ValueError):
        store[::0]


def test_failed_write_keeps_length_and_previous_results_and_removes_partial_file(tmp_path, monkeypatch):
    store = DiskResultSequence(tmp_path)
    store.append(result())
    before = set(tmp_path.iterdir())
    save = np.savez

    def fail_save(file, **arrays):
        file.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(np, "savez", fail_save)
    with pytest.raises(OSError, match="disk full"):
        store.append(result(99))
    assert len(store) == 1
    assert set(tmp_path.iterdir()) == before
    assert_result(store[0], result())
    monkeypatch.setattr(np, "savez", save)
    store.append(result(99))
    assert [item.frame_index for item in store] == [17, 99]


def test_replace_and_read_errors_propagate(tmp_path, monkeypatch):
    store = DiskResultSequence(tmp_path)
    from pathlib import Path

    def fail_replace(*args):
        raise OSError("replace failed")

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_replace)
        with pytest.raises(OSError, match="replace failed"):
            store.append(result())
    assert len(store) == 0
    assert not list(tmp_path.iterdir())
    store.append(result())

    def fail_load(*args, **kwargs):
        assert kwargs["allow_pickle"] is False
        raise OSError("read failed")

    monkeypatch.setattr(np, "load", fail_load)
    with pytest.raises(OSError, match="read failed"):
        store[0]
