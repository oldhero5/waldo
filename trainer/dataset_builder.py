"""Build a YOLO-ready dataset from a completed labeling job's annotations."""

import json
import logging
import math
import zipfile
from pathlib import Path

import cv2

from lib.db import LabelingJob, SessionLocal
from lib.storage import download_file

logger = logging.getLogger(__name__)

# Annotations smaller than this fraction of image area trigger crop augmentation.
# 0.01 = 1% of image area — at 640px training, a 1% object is ~64x64px (borderline).
# Anything below that needs crops to be learnable.
SMALL_OBJECT_AREA_THRESHOLD = 0.01

# Crop scales: fraction of image width/height centered on annotation
CROP_SCALES = [0.06, 0.08, 0.10, 0.13, 0.17, 0.22]

# Crop output size
CROP_SIZE = 640


def build_dataset_from_job(job_id: str) -> str:
    """Download the labeling job's dataset zip, return its MinIO key.

    If the job already has a result_minio_key, just returns it.
    """
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).one()
        if job.result_minio_key:
            return job.result_minio_key
        raise ValueError(f"Job {job_id} has no dataset (status: {job.status})")
    finally:
        session.close()


def prepare_dataset_dir(dataset_minio_key: str, work_dir: Path) -> Path:
    """Download and extract a dataset zip to a working directory.

    If annotations are very small relative to image size, automatically
    generates zoomed crops so the objects are large enough for YOLO to learn.

    Returns the path to the extracted dataset containing data.yaml.
    """
    zip_path = work_dir / "dataset.zip"
    download_file(dataset_minio_key, zip_path)

    dataset_dir = work_dir / "dataset"
    dataset_dir.mkdir(exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(dataset_dir)

    # Fix data.yaml path to point to the extracted location
    yaml_path = dataset_dir / "data.yaml"
    if yaml_path.exists():
        content = yaml_path.read_text()
        content = content.replace("path: .", f"path: {dataset_dir.resolve()}")
        yaml_path.write_text(content)

    # Demo feedback lacks the immutable image/annotation/workspace anchors needed
    # to revise this dataset. Coordinate coincidence is not object identity.
    logger.warning("Skipping demo feedback corrections: exact source provenance is unavailable")

    # Keep explicitly empty background labels; missing labels are unknown.
    _ensure_background_images(dataset_dir)

    # Check if annotations are small and augment if needed
    _augment_small_objects(dataset_dir)

    return dataset_dir


def _ensure_background_images(dataset_dir: Path) -> None:
    """Report explicit background labels without turning unknown images negative."""
    for split in ("train", "val"):
        img_dir = dataset_dir / "images" / split
        label_dir = dataset_dir / "labels" / split
        if not img_dir.exists() or not label_dir.exists():
            continue

        img_files = sorted(img_dir.glob("*"))
        img_files = [f for f in img_files if f.suffix.lower() in (".jpg", ".jpeg", ".png")]

        empty_count = 0
        total_count = 0
        for img_path in img_files:
            label_path = label_dir / (img_path.stem + ".txt")
            total_count += 1
            if not label_path.exists():
                logger.warning("Missing label file; leaving unreviewed image unchanged: %s", img_path)
            elif label_path.stat().st_size == 0 or label_path.read_text().strip() == "":
                empty_count += 1

        if empty_count > 0:
            logger.info(
                "%s: %d/%d images are background (negative) samples",
                split,
                empty_count,
                total_count,
            )


def _parse_label_file(label_path: Path, task: str) -> list[dict]:
    """Parse known formats without interpreting boxes as polygons or keypoints."""
    annotations = []
    for line in label_path.read_text().splitlines():
        if not line.strip():
            continue
        parts = line.split()
        coords = [float(value) for value in parts[1:]]
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in coords):
            raise ValueError(f"Invalid normalized coordinates in {label_path}")
        if task == "detect" and len(coords) == 4:
            cx, cy, width, height = coords
            x1, y1, x2, y2 = cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2
        elif task == "segment" and len(coords) >= 6 and len(coords) % 2 == 0:
            xs, ys = coords[::2], coords[1::2]
            x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
        else:
            raise ValueError(f"Label does not match {task} format in {label_path}")
        if x2 <= x1 or y2 <= y1:
            raise ValueError(f"Empty annotation in {label_path}")
        annotations.append(
            {
                "cls": parts[0],
                "coords": coords,
                "bbox": (x1, y1, x2, y2),
                "cx": (x1 + x2) / 2,
                "cy": (y1 + y2) / 2,
                "area": (x2 - x1) * (y2 - y1),
            }
        )
    return annotations


def _crop_annotations(
    annotations: list[dict], bounds: tuple[float, float, float, float], task: str
) -> list[str] | None:
    """Transform every visible label; skip segment crops that cut a polygon."""
    x1, y1, x2, y2 = bounds
    width, height = x2 - x1, y2 - y1
    lines = []
    for ann in annotations:
        ax1, ay1, ax2, ay2 = ann["bbox"]
        left, top, right, bottom = max(ax1, x1), max(ay1, y1), min(ax2, x2), min(ay2, y2)
        if right <= left or bottom <= top:
            continue
        if task == "detect":
            coords = [
                ((left + right) / 2 - x1) / width,
                ((top + bottom) / 2 - y1) / height,
                (right - left) / width,
                (bottom - top) / height,
            ]
        else:
            # A clipped polygon can change topology. Skip rather than inventing
            # a shape or omitting a partially visible neighboring object.
            if ax1 < x1 or ay1 < y1 or ax2 > x2 or ay2 > y2:
                return None
            coords = [
                (value - (x1 if index % 2 == 0 else y1)) / (width if index % 2 == 0 else height)
                for index, value in enumerate(ann["coords"])
            ]
        lines.append(ann["cls"] + " " + " ".join(f"{value:.6f}" for value in coords))
    return lines


def _augment_small_objects(dataset_dir: Path) -> None:
    """Add training-only crops with all visible labels and parent provenance.

    Detection boxes are clipped; segmentation crops cutting any polygon are
    skipped. OBB, pose, classification and archives without task metadata are
    left unchanged because their geometry requires a different transform.
    """
    import yaml

    yaml_path = dataset_dir / "data.yaml"
    config = yaml.safe_load(yaml_path.read_text()) if yaml_path.exists() else {}
    task = (config or {}).get("waldo_task")
    if task not in ("detect", "segment"):
        logger.warning("Skipping crop augmentation for unsupported or unspecified task: %s", task)
        return

    manifest_path = dataset_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text())
        if manifest_path.exists()
        else {
            "version": 1,
            "task": task,
            "split_strategy": "preserved",
            "samples": [],
        }
    )
    parents = {entry["image_path"]: entry for entry in manifest["samples"]}
    img_dir, label_dir = dataset_dir / "images/train", dataset_dir / "labels/train"
    if not img_dir.exists() or not label_dir.exists():
        return

    crop_count = 0
    jitter_offsets = [(0, 0), (0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3), (0.2, 0.2), (-0.2, -0.2)]
    for img_path in sorted(img_dir.iterdir()):
        if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png") or img_path.stem.startswith("crop_"):
            continue
        label_path = label_dir / (img_path.stem + ".txt")
        if not label_path.exists():
            continue
        annotations = _parse_label_file(label_path, task)
        small = [(index, ann) for index, ann in enumerate(annotations) if ann["area"] < SMALL_OBJECT_AREA_THRESHOLD]
        if not small:
            continue
        image = cv2.imread(str(img_path))
        if image is None:
            raise ValueError(f"Cannot decode dataset image: {img_path}")
        image_height, image_width = image.shape[:2]
        parent_path = str(img_path.relative_to(dataset_dir))
        parent = parents.get(parent_path, {})
        for ann_index, ann in small:
            for scale_index, scale in enumerate(CROP_SCALES):
                for jitter_index, (jx, jy) in enumerate(jitter_offsets):
                    cx, cy = ann["cx"] + jx * scale, ann["cy"] + jy * scale
                    x1 = int(max(0, cx - scale / 2) * image_width)
                    y1 = int(max(0, cy - scale / 2) * image_height)
                    x2 = int(min(1, cx + scale / 2) * image_width)
                    y2 = int(min(1, cy + scale / 2) * image_height)
                    if x2 - x1 < 16 or y2 - y1 < 16:
                        continue
                    # Geometry follows the actual integer pixel slice, not its
                    # pre-rounding floating-point proposal.
                    bounds = (x1 / image_width, y1 / image_height, x2 / image_width, y2 / image_height)
                    lines = _crop_annotations(annotations, bounds, task)
                    if not lines:
                        continue
                    stem = f"crop_{img_path.stem}_{ann_index}_{scale_index}_{jitter_index}"
                    crop_image, crop_label = img_dir / (stem + ".jpg"), label_dir / (stem + ".txt")
                    if crop_image.exists() or crop_label.exists():
                        if (
                            str(crop_image.relative_to(dataset_dir)) in parents
                            and crop_image.exists()
                            and crop_label.exists()
                        ):
                            continue
                        raise FileExistsError(f"Crop destination already exists: {crop_image}")
                    crop = cv2.resize(image[y1:y2, x1:x2], (CROP_SIZE, CROP_SIZE))
                    if not cv2.imwrite(str(crop_image), crop):
                        raise OSError(f"Could not write crop: {crop_image}")
                    crop_label.write_text("\n".join(lines) + "\n")
                    manifest["samples"].append(
                        {
                            "source_index": parent.get("source_index"),
                            "source_path": parent.get("source_path", str(img_path)),
                            "group_id": parent.get("group_id"),
                            "split": "train",
                            "parent_image_path": parent_path,
                            "crop_bounds_px": [x1, y1, x2, y2],
                            "image_path": str(crop_image.relative_to(dataset_dir)),
                            "label_path": str(crop_label.relative_to(dataset_dir)),
                        }
                    )
                    crop_count += 1
    if crop_count:
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        logger.info("Generated %d training crops with source provenance", crop_count)
