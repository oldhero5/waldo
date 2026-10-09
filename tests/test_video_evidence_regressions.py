"""Evidence regressions without model weights or a GPU."""

import importlib.util
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytestmark = pytest.mark.no_auth_bypass


def _load_subject(name):
    path = Path(__file__).parents[1] / (name.replace(".", "/") + ".py")
    spec = importlib.util.spec_from_file_location(f"_test_{name.replace('.', '_')}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Capture:
    def __init__(self, count):
        self.count = count
        self.index = 0
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        import cv2

        return {
            cv2.CAP_PROP_FRAME_COUNT: self.count,
            cv2.CAP_PROP_FPS: 30,
            cv2.CAP_PROP_FRAME_WIDTH: 4,
            cv2.CAP_PROP_FRAME_HEIGHT: 4,
        }[prop]

    def read(self):
        if self.index == self.count:
            return False, None
        frame = np.full((4, 4, 3), self.index % 256, dtype=np.uint8)
        self.index += 1
        return True, frame

    def release(self):
        self.released = True


def test_native_detections_use_current_sampled_frame_features(monkeypatch):
    # MLX and pipeline imports are external to this processing contract.
    monkeypatch.setitem(sys.modules, "labeler.pipeline", SimpleNamespace(_update_job=lambda *a, **kw: None))
    subject = _load_subject("labeler.video_labeler")
    backbone_frames = []

    def backbone(model, pixels):
        marker = int(pixels[0, 0, 0])
        backbone_frames.append(marker)
        return marker

    def detect(predictor, features, prompts, image_size, threshold, encoder_cache):
        # Mimic the image-dependent encoder's per-prompt cache.
        markers = [encoder_cache.setdefault(prompt, features) for prompt in prompts]
        return SimpleNamespace(
            boxes=np.array([[marker, 0, marker + 1, 1] for marker in markers]),
            scores=np.full(len(prompts), 0.9),
            masks=[],
            labels=prompts,
            track_ids=None,
        )

    mlx_core = SimpleNamespace(array=np.array)
    for name, module in {
        "mlx": SimpleNamespace(core=mlx_core),
        "mlx.core": mlx_core,
        "mlx_vlm.generate": SimpleNamespace(wired_limit=lambda model: nullcontext()),
        "mlx_vlm.models.sam3.generate": SimpleNamespace(
            SimpleTracker=lambda: SimpleNamespace(update=lambda result: result)
        ),
        "mlx_vlm.models.sam3_1.generate": SimpleNamespace(_get_backbone_features=backbone),
        "labeler.sam3_optimized": SimpleNamespace(detect_with_backbone_fast=detect),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    predictor = SimpleNamespace(
        model=object(),
        processor=SimpleNamespace(preprocess_image=lambda image: {"pixel_values": np.asarray(image)}),
    )
    monkeypatch.setattr(subject, "_get_predictor", lambda *a: predictor)
    cap = _Capture(91)
    monkeypatch.setattr(subject.cv2, "VideoCapture", lambda path: cap)

    results = subject.process_video_native("synthetic.mp4", ["camera", "camera", "pole"])

    assert [r["frame_idx"] for r in results] == [0, 30, 60, 90]
    assert [r["timestamp_s"] for r in results] == [0, 1, 2, 3]
    assert [[d["bbox"][0] for d in r["detections"]] for r in results] == [
        [0, 0, 0],
        [30, 30, 30],
        [60, 60, 60],
        [90, 90, 90],
    ]
    assert backbone_frames == [0, 30, 60, 90]
    assert cap.released


@pytest.mark.parametrize("negative_frames", [0, 240])
def test_tiled_tracking_preserves_one_frame_sighting_with_appended_negatives(monkeypatch, negative_frames):
    subject = _load_subject("lib.video_tracker")
    cap = _Capture(1 + negative_frames)
    monkeypatch.setattr(subject.cv2, "VideoCapture", lambda path: cap)

    def predict(frame, conf):
        if int(frame[0, 0, 0]) == 0:
            return [subject.Detection("camera", 0, 0.9, [1, 1, 3, 3])]
        return []

    meta = {"fps": 8, "width": 4, "height": 4}
    callbacks = []
    results = subject.VideoTracker()._track_with_tiling(
        "synthetic.mp4", meta, SimpleNamespace(_predict_tiled=predict), callbacks.append
    )

    assert len(results) == 1 + negative_frames
    assert [(r.frame_index, d.class_name, d.bbox) for r in results for d in r.detections] == [
        (0, "camera", [1, 1, 3, 3])
    ]
    assert callbacks[0].detections == results[0].detections
    assert cap.released
