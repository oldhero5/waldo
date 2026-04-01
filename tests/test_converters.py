"""Tests for all five YOLO converter modules."""
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from labeler.converters.to_classify import masks_to_crops, write_yolo_dataset as write_cls
from labeler.converters.to_detect import masks_to_yolo_bboxes, write_yolo_dataset as write_det
from labeler.converters.to_obb import masks_to_yolo_obb, write_yolo_dataset as write_obb
from labeler.converters.to_pose import masks_to_yolo_pose, write_yolo_dataset as write_pose
from labeler.converters.to_segment import masks_to_yolo_polygons, write_yolo_dataset as write_seg


def _make_circle_mask(h: int, w: int, cy: int, cx: int, r: int) -> np.ndarray:
    y, x = np.ogrid[:h, :w]
    return ((x - cx) ** 2 + (y - cy) ** 2 <= r**2).astype(bool)


def _make_rect_mask(h: int, w: int, y1: int, x1: int, y2: int, x2: int) -> np.ndarray:
    mask = np.zeros((h, w), dtype=bool)
    mask[y1:y2, x1:x2] = True
    return mask


H, W = 200, 300
CIRCLE = _make_circle_mask(H, W, cy=100, cx=150, r=50)
RECT = _make_rect_mask(H, W, y1=20, x1=30, y2=80, x2=120)
TWO_MASKS = np.array([CIRCLE, RECT])
TINY = _make_circle_mask(H, W, cy=10, cx=10, r=2)


# ── Segmentation ────────────────────────────────────────────

class TestSegmentation:
    def test_basic(self):
        lines = masks_to_yolo_polygons(np.array([CIRCLE]), class_indices=[0])
        assert len(lines) == 1
        parts = lines[0].split()
        assert parts[0] == "0"
        coords = [float(x) for x in parts[1:]]
        assert len(coords) >= 6
        assert all(0 <= c <= 1 for c in coords)

    def test_filters_small(self):
        lines = masks_to_yolo_polygons(np.array([TINY]), class_indices=[0], min_area=100)
        assert len(lines) == 0

    def test_multiple(self):
        lines = masks_to_yolo_polygons(TWO_MASKS, class_indices=[0, 1])
        assert len(lines) == 2
        assert lines[0].startswith("0 ")
        assert lines[1].startswith("1 ")

    def test_write_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            frames = self._make_fake_frames(Path(tmpdir), 5)
            ann = [["0 0.1 0.1 0.2 0.1 0.2 0.2"]] * 5
            result = write_seg(Path(tmpdir) / "out", frames, ann, ["car"])
            assert (result / "data.yaml").exists()
            assert len(list((result / "images").rglob("*.jpg"))) == 5
            assert len(list((result / "labels").rglob("*.txt"))) == 5

    @staticmethod
    def _make_fake_frames(d: Path, n: int) -> list[Path]:
        d = d / "frames"
        d.mkdir()
        paths = []
        for i in range(n):
            p = d / f"frame_{i:06d}.jpg"
            p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 100)
            paths.append(p)
        return paths


# ── Detection ───────────────────────────────────────────────

class TestDetection:
    def test_basic(self):
        lines = masks_to_yolo_bboxes(np.array([CIRCLE]), class_indices=[0])
        assert len(lines) == 1
        parts = lines[0].split()
        assert parts[0] == "0"
        cx, cy, bw, bh = [float(x) for x in parts[1:]]
        assert 0 < cx < 1 and 0 < cy < 1
        assert 0 < bw < 1 and 0 < bh < 1

    def test_filters_small(self):
        lines = masks_to_yolo_bboxes(np.array([TINY]), class_indices=[0], min_area=100)
        assert len(lines) == 0

    def test_rect_accuracy(self):
        lines = masks_to_yolo_bboxes(np.array([RECT]), class_indices=[2])
        assert len(lines) == 1
        parts = lines[0].split()
        assert parts[0] == "2"
        cx, cy, bw, bh = [float(x) for x in parts[1:]]
        # Rect is at x1=30,y1=20 to x2=120,y2=80, so center ~ (75/300, 50/200)
        assert abs(cx - 75 / W) < 0.02
        assert abs(cy - 50 / H) < 0.02


# ── OBB ─────────────────────────────────────────────────────

class TestOBB:
    def test_basic(self):
        lines = masks_to_yolo_obb(np.array([CIRCLE]), class_indices=[0])
        assert len(lines) == 1
        parts = lines[0].split()
        assert parts[0] == "0"
        coords = [float(x) for x in parts[1:]]
        assert len(coords) == 8  # 4 corner points × 2
        assert all(0 <= c <= 1 for c in coords)

    def test_filters_small(self):
        lines = masks_to_yolo_obb(np.array([TINY]), class_indices=[0], min_area=100)
        assert len(lines) == 0

    def test_rect_gives_4_corners(self):
        lines = masks_to_yolo_obb(np.array([RECT]), class_indices=[0])
        assert len(lines) == 1
        coords = [float(x) for x in lines[0].split()[1:]]
        assert len(coords) == 8


# ── Pose ────────────────────────────────────────────────────

class TestPose:
    def test_basic(self):
        lines = masks_to_yolo_pose(np.array([CIRCLE]), class_indices=[0])
        assert len(lines) == 1
        parts = lines[0].split()
        assert parts[0] == "0"
        # cx cy w h kp_x kp_y visible = 7 values
        vals = [float(x) for x in parts[1:]]
        assert len(vals) == 7
        cx, cy, bw, bh, kp_x, kp_y, vis = vals
        assert 0 < cx < 1 and 0 < cy < 1
        assert 0 < kp_x < 1 and 0 < kp_y < 1
        assert vis == 2  # visible and labeled

    def test_centroid_near_center(self):
        lines = masks_to_yolo_pose(np.array([CIRCLE]), class_indices=[0])
        vals = [float(x) for x in lines[0].split()[1:]]
        kp_x, kp_y = vals[4], vals[5]
        # Circle centered at (150, 100) in 300×200 frame
        assert abs(kp_x - 150 / W) < 0.02
        assert abs(kp_y - 100 / H) < 0.02

    def test_filters_small(self):
        lines = masks_to_yolo_pose(np.array([TINY]), class_indices=[0], min_area=100)
        assert len(lines) == 0


# ── Classification ──────────────────────────────────────────

class TestClassification:
    def test_masks_to_crops(self):
        frame = np.random.randint(0, 255, (H, W, 3), dtype=np.uint8)
        crops = masks_to_crops(
            np.array([CIRCLE, RECT]), frame, ["car", "truck"], [0, 1]
        )
        assert len(crops) == 2
        for crop_img, cls_name in crops:
            assert crop_img.ndim == 3
            assert cls_name in ("car", "truck")

    def test_filters_small(self):
        frame = np.random.randint(0, 255, (H, W, 3), dtype=np.uint8)
        crops = masks_to_crops(np.array([TINY]), frame, ["car"], [0], min_area=100)
        assert len(crops) == 0

    def test_write_dataset(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            frame = np.random.randint(0, 255, (H, W, 3), dtype=np.uint8)
            crops_per_frame = [
                [(frame[20:80, 30:120], "car"), (frame[50:150, 100:200], "truck")],
                [(frame[10:60, 10:60], "car")],
            ]
            fake_frames = [Path(tmpdir) / f"f{i}.jpg" for i in range(2)]
            result = write_cls(
                Path(tmpdir) / "out", fake_frames, crops_per_frame, ["car", "truck"]
            )
            assert (result / "data.yaml").exists()
            all_imgs = list(result.rglob("*.jpg"))
            assert len(all_imgs) == 3
            # Check class dirs exist
            assert (result / "train").is_dir() or (result / "val").is_dir()


# ── data.yaml variants ─────────────────────────────────────

class TestDataYaml:
    def test_segment_yaml(self):
        from labeler.converters.common import generate_data_yaml
        y = generate_data_yaml(["car", "truck"], task="segment")
        assert "nc: 2" in y
        assert "path: ." in y
        assert "kpt_shape" not in y

    def test_pose_yaml(self):
        from labeler.converters.common import generate_data_yaml
        y = generate_data_yaml(["person"], task="pose")
        assert "kpt_shape: [1, 3]" in y
        assert "nc: 1" in y
