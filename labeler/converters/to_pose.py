"""Mask → YOLO pose format with centroid keypoint."""

from pathlib import Path

import cv2
import numpy as np

from labeler.converters.common import write_yolo_label_dataset


def masks_to_yolo_pose(
    masks: np.ndarray,
    class_indices: list[int],
    min_area: int = 100,
) -> list[str]:
    """Convert masks to YOLO pose format: class_idx cx cy w h kp_x kp_y visible (normalized).

    Uses the mask centroid as a single keypoint. Users can add more keypoints in the review UI.
    """
    lines: list[str] = []
    h, w = masks.shape[1], masks.shape[2]

    for mask, cls_idx in zip(masks, class_indices):
        mask_uint8 = mask.astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue

            x, y, bw, bh = cv2.boundingRect(contour)
            x_center = (x + bw / 2) / w
            y_center = (y + bh / 2) / h
            nw = bw / w
            nh = bh / h

            # Centroid keypoint from mask moments
            moments = cv2.moments(contour)
            if moments["m00"] > 0:
                kp_x = (moments["m10"] / moments["m00"]) / w
                kp_y = (moments["m01"] / moments["m00"]) / h
            else:
                kp_x = x_center
                kp_y = y_center

            # visible=2 means visible and labeled
            lines.append(f"{cls_idx} {x_center:.6f} {y_center:.6f} {nw:.6f} {nh:.6f} {kp_x:.6f} {kp_y:.6f} 2")

    return lines


def write_yolo_dataset(
    output_dir: str | Path,
    frame_paths: list[Path],
    annotation_lines: list[list[str]],
    class_names: list[str],
    val_split: float = 0.1,
) -> Path:
    return write_yolo_label_dataset(output_dir, frame_paths, annotation_lines, class_names, val_split, task="pose")
