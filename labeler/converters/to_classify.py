"""Mask → YOLO classification format (cropped images in class directories)."""
import random
from pathlib import Path

import cv2
import numpy as np

from labeler.converters.common import generate_data_yaml


def masks_to_crops(
    masks: np.ndarray,
    frame: np.ndarray,
    class_names: list[str],
    class_indices: list[int],
    min_area: int = 100,
    padding: int = 5,
) -> list[tuple[np.ndarray, str]]:
    """Extract cropped image regions for each mask instance.

    Returns list of (crop_image, class_name) tuples.
    """
    crops: list[tuple[np.ndarray, str]] = []
    h, w = masks.shape[1], masks.shape[2]

    for mask, cls_idx in zip(masks, class_indices):
        mask_uint8 = mask.astype(np.uint8) * 255
        contours, _ = cv2.findContours(mask_uint8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            area = cv2.contourArea(contour)
            if area < min_area:
                continue

            x, y, bw, bh = cv2.boundingRect(contour)
            # Add padding
            x1 = max(0, x - padding)
            y1 = max(0, y - padding)
            x2 = min(w, x + bw + padding)
            y2 = min(h, y + bh + padding)

            crop = frame[y1:y2, x1:x2]
            if crop.size > 0:
                crops.append((crop, class_names[cls_idx]))

    return crops


def write_yolo_dataset(
    output_dir: str | Path,
    frame_paths: list[Path],
    crops_per_frame: list[list[tuple[np.ndarray, str]]],
    class_names: list[str],
    val_split: float = 0.1,
) -> Path:
    """Write YOLO classification dataset: class_name/image.jpg directory structure."""
    output_dir = Path(output_dir)

    for split in ("train", "val"):
        for cls_name in class_names:
            (output_dir / split / cls_name).mkdir(parents=True, exist_ok=True)

    # Flatten all crops with indices for splitting
    all_crops: list[tuple[np.ndarray, str, int]] = []
    for frame_idx, crops in enumerate(crops_per_frame):
        for crop, cls_name in crops:
            all_crops.append((crop, cls_name, frame_idx))

    indices = list(range(len(all_crops)))
    random.shuffle(indices)
    val_count = max(1, int(len(indices) * val_split)) if len(indices) > 1 else 0
    val_indices = set(indices[:val_count])

    for i, (crop, cls_name, frame_idx) in enumerate(all_crops):
        split = "val" if i in val_indices else "train"
        filename = f"frame{frame_idx:06d}_crop{i:06d}.jpg"
        dst = output_dir / split / cls_name / filename
        cv2.imwrite(str(dst), crop)

    yaml_path = output_dir / "data.yaml"
    yaml_content = generate_data_yaml(class_names, task="classify")
    yaml_path.write_text(yaml_content)

    return output_dir
