"""Test-only Celery entry point with deterministic text inference.

Import this module only in the disposable Linux worker integration runner.
It patches one boundary in the worker process: text_labeler.get_engine.
"""

import os
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
from sqlalchemy.engine import make_url

from labeler import text_labeler
from labeler.sam3_engine import SegmentationResult
from lib import tasks
from lib.config import settings
from lib.tasks import app

__all__ = ["app"]


if os.environ.get("WALDO_WORKER_INTEGRATION") != "1" or os.environ.get("WALDO_SERVICE_STACK") != "1":
    raise RuntimeError("Test worker requires the disposable worker integration stack")
if make_url(os.environ["WALDO_TEST_POSTGRES_URL"]) != make_url(settings.postgres_dsn):
    raise RuntimeError("Test worker database must match the disposable API database")

CONTROL = Path(os.environ["WALDO_TEST_WORKER_CONTROL_DIR"])
if not CONTROL.is_dir():
    raise RuntimeError("Test worker control directory is missing")


class DeterministicEngine:
    def iter_segment_frame_paths(self, paths, prompt, *, threshold, working_dir):
        video_id = paths[0].parent.name.removeprefix("frames_")
        block = CONTROL / f"block-{video_id}"
        deadline = time.monotonic() + 45
        while block.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Test worker remained blocked for video {video_id}")
            time.sleep(0.1)

        transient = CONTROL / f"transient-{video_id}"
        if transient.exists():
            transient.unlink()
            (CONTROL / f"transient-fired-{video_id}").touch()
            raise OSError(f"test transient inference error for video {video_id}")

        mode = (CONTROL / "mode").read_text().strip() if (CONTROL / "mode").exists() else "POS"
        if mode == "ERROR":
            raise ValueError("test terminal inference error")
        if mode not in {"POS", "EMPTY"}:
            raise ValueError(f"Unknown deterministic inference mode: {mode}")

        for ordinal, path in enumerate(paths):
            with Image.open(path) as image:
                width, height = image.size
            if mode == "EMPTY":
                masks = np.empty((0, height, width), dtype=bool)
                boxes = np.empty((0, 4), dtype=np.float32)
                scores = np.empty(0, dtype=np.float32)
            else:
                x1, y1, x2, y2 = width // 4, height // 4, width * 3 // 4, height * 3 // 4
                masks = np.zeros((1, height, width), dtype=bool)
                masks[0, y1:y2, x1:x2] = True
                boxes = np.array([[x1, y1, x2, y2]], dtype=np.float32)
                scores = np.array([0.9], dtype=np.float32)
            yield SegmentationResult(frame_index=ordinal, masks=masks, boxes=boxes, scores=scores)


text_labeler.get_engine = DeterministicEngine


if os.environ.get("WALDO_TEST_NATIVE_MODE") == "1":
    # Keep the production native decoder, timing, sampling and persistence. Fake
    # only model preprocessing, features, detection and tracking on Linux.
    from labeler import video_labeler

    def native_detection(predictor, features, prompts, image_size, threshold, encoder_cache):
        mode = (CONTROL / "mode").read_text().strip() if (CONTROL / "mode").exists() else "POS"
        if mode == "ERROR":
            raise ValueError("test terminal inference error")
        width, height = image_size
        if mode == "EMPTY":
            return SimpleNamespace(scores=[], boxes=[], masks=[], labels=[], track_ids=None)
        if mode != "POS":
            raise ValueError(f"Unknown deterministic inference mode: {mode}")
        mask = np.zeros((height, width), dtype=bool)
        mask[height // 4 : height * 3 // 4, width // 4 : width * 3 // 4] = True
        return SimpleNamespace(
            scores=[0.9],
            boxes=np.array([[width / 4, height / 4, width * 3 / 4, height * 3 / 4]]),
            masks=[mask],
            labels=[prompts[0]],
            track_ids=[7],
        )

    core = SimpleNamespace(array=np.array)
    for module_name, module in {
        "mlx": SimpleNamespace(core=core),
        "mlx.core": core,
        "mlx_vlm.generate": SimpleNamespace(wired_limit=lambda model: nullcontext()),
        "mlx_vlm.models.sam3.generate": SimpleNamespace(
            SimpleTracker=lambda: SimpleNamespace(update=lambda result: result)
        ),
        "mlx_vlm.models.sam3_1.generate": SimpleNamespace(_get_backbone_features=lambda model, pixels: pixels),
        "labeler.sam3_optimized": SimpleNamespace(detect_with_backbone_fast=native_detection),
    }.items():
        sys.modules[module_name] = module
    video_labeler._get_predictor = lambda *args: SimpleNamespace(
        model=object(), processor=SimpleNamespace(preprocess_image=lambda image: {"pixel_values": np.asarray(image)})
    )
    original_native_process = video_labeler.process_video_native

    def controlled_native_process(path, *args, **kwargs):
        video_id = Path(path).name.split("_", 1)[0]
        block = CONTROL / f"block-{video_id}"
        deadline = time.monotonic() + 45
        while block.exists():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Test worker remained blocked for video {video_id}")
            time.sleep(0.1)
        transient = CONTROL / f"transient-{video_id}"
        if transient.exists():
            transient.unlink()
            (CONTROL / f"transient-fired-{video_id}").touch()
            raise OSError(f"test transient inference error for video {video_id}")
        return original_native_process(path, *args, **kwargs)

    video_labeler.process_video_native = controlled_native_process
    tasks._native_video_available = lambda: True
