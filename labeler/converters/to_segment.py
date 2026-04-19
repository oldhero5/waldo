"""Mask → YOLO segmentation polygon format."""

from pathlib import Path

import cv2
import numpy as np

from labeler.converters.common import write_yolo_label_dataset


def masks_to_yolo_polygons(
    masks: np.ndarray,
    class_indices: list[int],
    min_area: int = 100,
    epsilon_factor: float = 0.001,
) -> list[str]:
    lines: list[str] = []
    h, w = masks.shape[1], masks.shape[2]

    for mask, cls_idx in zip(masks, class_indices):
        mask_uint8 = mask.astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue

            epsilon = epsilon_factor * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)

            if len(approx) < 3:
                continue

            points = approx.reshape(-1, 2)
            normalized = []
            for px, py in points:
                normalized.append(f"{px / w:.6f}")
                normalized.append(f"{py / h:.6f}")

            lines.append(f"{cls_idx} " + " ".join(normalized))

    return lines


def write_yolo_dataset(
    output_dir: str | Path,
    frame_paths: list[Path],
    annotation_lines: list[list[str]],
    class_names: list[str],
    val_split: float = 0.1,
) -> Path:
    return write_yolo_label_dataset(output_dir, frame_paths, annotation_lines, class_names, val_split, task="segment")
