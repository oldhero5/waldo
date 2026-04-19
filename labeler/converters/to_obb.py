"""Mask → YOLO oriented bounding box (OBB) format."""

from pathlib import Path

import cv2
import numpy as np

from labeler.converters.common import write_yolo_label_dataset


def masks_to_yolo_obb(
    masks: np.ndarray,
    class_indices: list[int],
    min_area: int = 100,
) -> list[str]:
    """Convert masks to YOLO OBB format: class_idx x1 y1 x2 y2 x3 y3 x4 y4 (normalized)."""
    lines: list[str] = []
    h, w = masks.shape[1], masks.shape[2]

    for mask, cls_idx in zip(masks, class_indices):
        mask_uint8 = mask.astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue

            rect = cv2.minAreaRect(contour)
            box_points = cv2.boxPoints(rect)  # 4 corners as float32

            coords = []
            for px, py in box_points:
                coords.append(f"{np.clip(px / w, 0, 1):.6f}")
                coords.append(f"{np.clip(py / h, 0, 1):.6f}")

            lines.append(f"{cls_idx} " + " ".join(coords))

    return lines


def write_yolo_dataset(
    output_dir: str | Path,
    frame_paths: list[Path],
    annotation_lines: list[list[str]],
    class_names: list[str],
    val_split: float = 0.1,
) -> Path:
    return write_yolo_label_dataset(output_dir, frame_paths, annotation_lines, class_names, val_split, task="obb")
