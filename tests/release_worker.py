"""Read-only test bootstrap. Replace only text inference in the exact release image."""

import os

import numpy as np
from PIL import Image

from labeler import text_labeler
from labeler.sam3_engine import SegmentationResult
from lib.tasks import app

__all__ = ["app"]

if os.environ.get("WALDO_RELEASE_SMOKE") != "1":
    raise RuntimeError("Release test worker requires the disposable smoke stack")


class SyntheticEngine:
    def iter_segment_frame_paths(self, paths, prompt, *, threshold, working_dir):
        for ordinal, path in enumerate(paths):
            with Image.open(path) as image:
                width, height = image.size
            x1, y1, x2, y2 = width // 4, height // 4, width * 3 // 4, height * 3 // 4
            masks = np.zeros((1, height, width), dtype=bool)
            masks[0, y1:y2, x1:x2] = True
            yield SegmentationResult(
                frame_index=ordinal,
                masks=masks,
                boxes=np.array([[x1, y1, x2, y2]], dtype=np.float32),
                scores=np.array([0.9], dtype=np.float32),
            )


text_labeler.get_engine = SyntheticEngine
