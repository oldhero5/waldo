"""Path-loaded offline SAM3 frames and mutable, disk-backed dense history.

The caller owns ``working_dir`` for the complete inference/persistence lifetime.
Records survive mapping removal and promotion; their files share that lifetime.
Small object pointers and score logits keep upstream's incoming-device semantics.
"""

from collections.abc import Iterator, Mapping, MutableMapping, Sequence
from pathlib import Path
from uuid import uuid4

import torch
from PIL import Image
from transformers.models.sam3_video.modeling_sam3_video import Sam3VideoInferenceSession

_SMALL_FIELDS = frozenset({"object_pointer", "object_score_logits"})


class _TensorReadLease:
    """Keep one CPU source alive until upstream's async device copy completes."""

    def __init__(self, device: torch.device):
        self.device = device
        self._value: object | None = None

    def retain(self, value: object) -> None:
        self.release()
        self._value = value

    def release(self) -> None:
        if self._value is not None:
            if self.device.type == "mps":
                torch.mps.synchronize()
            else:
                torch.cuda.synchronize(self.device)
            self._value = None


class TensorFieldRecord(MutableMapping[str, object]):
    """A frame record whose dense tensor fields are loaded only when read.

    The locked tracker writes flat tensor fields. Other metadata remains resident,
    including the two small tensor fields that upstream keeps on the device.
    """

    def __init__(
        self,
        directory: Path,
        initial: Mapping[str, object] | None = None,
        *,
        _read_lease: _TensorReadLease | None = None,
    ):
        self.directory = directory
        self._read_lease = _read_lease
        self.directory.mkdir(parents=True, exist_ok=True)
        self._values: dict[str, object] = {}
        self._tensor_paths: dict[str, Path] = {}
        if initial is not None:
            self.update(initial)

    def __getitem__(self, key: str) -> object:
        if key in self._tensor_paths:
            value = torch.load(self._tensor_paths[key], map_location="cpu", weights_only=True)
            if self._read_lease is not None:
                self._read_lease.retain(value)
            return value
        return self._values[key]

    def __setitem__(self, key: str, value: object) -> None:
        if isinstance(value, torch.Tensor) and key not in _SMALL_FIELDS:
            path = self._tensor_paths.get(key, self.directory / f"{uuid4().hex}.pt")
            temporary = self.directory / f"{uuid4().hex}.tmp"
            try:
                # clone() prevents a tensor view from saving its parent's storage.
                torch.save(value.detach().to("cpu").clone(), temporary)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
            self._tensor_paths[key] = path
            self._values[key] = None
        else:
            if key in self._tensor_paths:
                self._tensor_paths[key].unlink()
                del self._tensor_paths[key]
            self._values[key] = value

    def __delitem__(self, key: str) -> None:
        if key in self._tensor_paths:
            self._tensor_paths[key].unlink()
            del self._tensor_paths[key]
        del self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)


class FrameOutputMap(MutableMapping[int, TensorFieldRecord]):
    """Keep stable mutable records across history promotion and object reindexing."""

    def __init__(self, directory: Path, *, _read_lease: _TensorReadLease | None = None):
        self.directory = directory
        self._read_lease = _read_lease
        self._records: dict[int, TensorFieldRecord] = {}

    def __getitem__(self, key: int) -> TensorFieldRecord:
        return self._records[key]

    def __setitem__(self, key: int, value: Mapping[str, object]) -> None:
        if not isinstance(value, TensorFieldRecord):
            value = TensorFieldRecord(self.directory / uuid4().hex, value, _read_lease=self._read_lease)
        self._records[key] = value

    def __delitem__(self, key: int) -> None:
        # References held by another history map or tracker still own the record.
        del self._records[key]

    def __iter__(self) -> Iterator[int]:
        return iter(self._records)

    def __len__(self) -> int:
        return len(self._records)


class PathBackedSam3Session(Sam3VideoInferenceSession):
    """One full-count offline session, with one processed frame resident per read."""

    def __init__(
        self,
        frame_paths: Sequence[Path],
        processor,
        *,
        working_dir: Path,
        inference_device: str,
        dtype: torch.dtype,
    ):
        self.frame_paths = tuple(frame_paths)
        self.processor = processor
        self.working_dir = working_dir
        device = torch.device(inference_device)
        self._read_lease = _TensorReadLease(device) if device.type in {"mps", "cuda"} else None
        with Image.open(self.frame_paths[0]) as first_frame:
            width, height = first_frame.size
        super().__init__(
            video=None,
            video_height=height,
            video_width=width,
            inference_device=inference_device,
            dtype=dtype,
        )

    @property
    def num_frames(self) -> int:
        # Upstream's read-only property derives the count from eager frames.
        return len(self.frame_paths)

    def get_frame(self, frame_idx: int) -> torch.Tensor:
        with Image.open(self.frame_paths[frame_idx]) as image:
            pixels = self.processor(images=[image], return_tensors="pt")["pixel_values"][0]
        return pixels.to(self.inference_device, dtype=self.dtype)

    def store_output(
        self,
        obj_idx: int,
        frame_idx: int,
        output_key: str | None = None,
        output_value: torch.Tensor | dict | None = None,
        is_conditioning_frame: bool = True,
    ):
        if isinstance(output_value, torch.Tensor) and output_key not in _SMALL_FIELDS:
            # Upstream offloads nonblocking before the record setter. Complete that
            # copy here: CPU serialization cannot wait on the source device later.
            output_value = output_value.to(self.inference_state_device, non_blocking=False)
        super().store_output(obj_idx, frame_idx, output_key, output_value, is_conditioning_frame)

    def obj_id_to_idx(self, obj_id: int) -> int:
        is_new = obj_id not in self._obj_id_to_idx
        obj_idx = super().obj_id_to_idx(obj_id)
        if is_new:
            # Object indices are reused after removal/reset. Paths never use them.
            directory = self.working_dir / uuid4().hex
            self.output_dict_per_obj[obj_idx] = {
                "cond_frame_outputs": FrameOutputMap(directory / "cond", _read_lease=self._read_lease),
                "non_cond_frame_outputs": FrameOutputMap(directory / "noncond", _read_lease=self._read_lease),
            }
        return obj_idx

    def close(self) -> None:
        """Complete pending history transfers and release the last CPU source.

        The engine must call this in its iterator's finally block before the owned
        temporary directory is removed. Record files remain for its owner to clean.
        """
        if self._read_lease is not None:
            self._read_lease.release()
