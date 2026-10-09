"""Append-only segmentation results owned by the caller's working directory."""

import operator
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import numpy as np

if TYPE_CHECKING:
    from labeler.sam3_engine import SegmentationResult


class DiskResultSequence(Sequence["SegmentationResult"]):
    """Repeatable indexed access without keeping dense result arrays resident."""

    def __init__(self, directory: Path):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=True)
        self._length = 0

    def append(self, result: "SegmentationResult") -> None:
        path = self.directory / f"{self._length}.npz"
        temporary = self.directory / f"{uuid4().hex}.tmp"
        try:
            with temporary.open("wb") as file:
                np.savez(
                    file,
                    frame_index=np.asarray(result.frame_index),
                    masks=result.masks,
                    boxes=result.boxes,
                    scores=result.scores,
                    has_class_indices=np.asarray(result.class_indices is not None),
                    class_indices=result.class_indices
                    if result.class_indices is not None
                    else np.empty(0, dtype=np.int64),
                )
            temporary.replace(path)
            self._length += 1
        finally:
            temporary.unlink(missing_ok=True)

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, index: int | slice):
        from labeler.sam3_engine import SegmentationResult

        if isinstance(index, slice):
            return [self[ordinal] for ordinal in range(*index.indices(self._length))]
        index = operator.index(index)
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError(index)
        with np.load(self.directory / f"{index}.npz", allow_pickle=False) as archive:
            return SegmentationResult(
                frame_index=int(archive["frame_index"]),
                masks=archive["masks"].copy(),
                boxes=archive["boxes"].copy(),
                scores=archive["scores"].copy(),
                class_indices=archive["class_indices"].copy() if bool(archive["has_class_indices"]) else None,
            )
