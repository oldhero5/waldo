"""Mask → YOLO classification format (cropped images in class directories)."""

import unicodedata
from pathlib import Path

import cv2
import numpy as np

from labeler.converters.common import export_stem, generate_data_yaml, split_indices, write_manifest


def validate_class_names(class_names: list[str]) -> None:
    """Require distinct portable class directories without renaming labels."""
    seen = {}
    for name in class_names:
        if (
            not isinstance(name, str)
            or not name
            or name in (".", "..")
            or any(value in name for value in ("/", "\\", "\x00"))
        ):
            raise ValueError("Classification class name must be one safe directory component; rename it before export")
        canonical = unicodedata.normalize("NFC", unicodedata.normalize("NFC", name).casefold())
        if canonical in seen:
            raise ValueError(
                f"Classification class names {seen[canonical]!r} and {name!r} collide after Unicode normalization "
                "and case folding; rename one before export"
            )
        seen[canonical] = name


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
    *,
    group_ids: list[str] | None = None,
) -> Path:
    """Write YOLO classification dataset: class_name/image.jpg directory structure."""
    validate_class_names(class_names)
    output_dir = Path(output_dir)
    if len(frame_paths) != len(crops_per_frame):
        raise ValueError("frame_paths and crops_per_frame length must match")
    val_indices = split_indices(len(frame_paths), val_split, group_ids)

    destinations = []
    for frame_idx, crops in enumerate(crops_per_frame):
        split = "val" if frame_idx in val_indices else "train"
        for crop_idx, (crop, cls_name) in enumerate(crops):
            if cls_name not in class_names:
                raise ValueError(f"Unknown crop class: {cls_name}")
            filename = f"{export_stem(frame_paths[frame_idx], frame_idx)}_crop{crop_idx:06d}.jpg"
            dst = output_dir / split / cls_name / filename
            if dst.exists():
                raise FileExistsError(f"Export destination already exists: {dst}")
            destinations.append((frame_idx, crop_idx, split, crop, dst))

    for split in ("train", "val"):
        for cls_name in class_names:
            (output_dir / split / cls_name).mkdir(parents=True, exist_ok=True)

    samples = []
    for frame_idx, crop_idx, split, crop, dst in destinations:
        if not cv2.imwrite(str(dst), crop):
            raise OSError(f"Could not write crop: {dst}")
        samples.append(
            {
                "source_index": frame_idx,
                "source_path": str(frame_paths[frame_idx]),
                "crop_index": crop_idx,
                "group_id": group_ids[frame_idx] if group_ids is not None else None,
                "split": split,
                "image_path": str(dst.relative_to(output_dir)),
                "label_path": None,
            }
        )

    yaml_path = output_dir / "data.yaml"
    yaml_content = generate_data_yaml(class_names, task="classify")
    yaml_path.write_text(yaml_content)
    write_manifest(output_dir, samples, "classify", group_ids is not None)

    return output_dir
