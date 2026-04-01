"""Build a YOLO-ready dataset from a completed labeling job's annotations."""
import logging
import random
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

# Number of crops to generate per annotation at each scale
CROPS_PER_SCALE = 6

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

    # Apply false-positive feedback: flagged detections become negative examples
    # (keep the image, remove the flagged annotation lines → creates background images)
    _apply_feedback_corrections(dataset_dir)

    # Add negative (background) images — frames with empty label files
    # help the model learn what is NOT the target, reducing false positives
    _ensure_background_images(dataset_dir)

    # Check if annotations are small and augment if needed
    if _has_small_objects(dataset_dir):
        logger.info("Small objects detected — generating zoomed crop augmentations")
        _augment_small_objects(dataset_dir)

    return dataset_dir


def _apply_feedback_corrections(dataset_dir: Path) -> None:
    """Apply false-positive feedback from the Demo page to the training dataset.

    For each false-positive feedback entry, finds matching annotation lines
    (by class name and approximate bbox) and removes them. If all annotations
    in a label file are removed, the file becomes empty — which YOLO treats as
    a negative/background example. This teaches the model "this image does NOT
    contain the object", which is the correct way to handle false positives.
    """
    from lib.db import DemoFeedback, SessionLocal

    session = SessionLocal()
    try:
        feedback = session.query(DemoFeedback).filter_by(feedback_type="false_positive").all()
        if not feedback:
            return

        # Build a set of (class_name, approx_bbox_hash) for fast lookup
        fp_signatures = set()
        for fb in feedback:
            # Create a coarse signature from class + rounded bbox center
            if fb.bbox and len(fb.bbox) >= 4:
                cx = round((fb.bbox[0] + fb.bbox[2]) / 2, -1)  # Round to nearest 10px
                cy = round((fb.bbox[1] + fb.bbox[3]) / 2, -1)
                fp_signatures.add((fb.class_name, cx, cy))

        if not fp_signatures:
            return

        # Read data.yaml to get class names
        import yaml
        yaml_path = dataset_dir / "data.yaml"
        if not yaml_path.exists():
            return
        with open(yaml_path) as f:
            data_cfg = yaml.safe_load(f)
        names = data_cfg.get("names", {})
        if isinstance(names, list):
            idx_to_name = {i: n for i, n in enumerate(names)}
        elif isinstance(names, dict):
            idx_to_name = {int(k): v for k, v in names.items()}
        else:
            return

        removed_count = 0
        for split in ("train", "val"):
            label_dir = dataset_dir / "labels" / split
            img_dir = dataset_dir / "images" / split
            if not label_dir.exists():
                continue

            for label_path in label_dir.glob("*.txt"):
                content = label_path.read_text().strip()
                if not content:
                    continue

                # Find matching image to get dimensions
                img_path = None
                for ext in (".jpg", ".jpeg", ".png"):
                    candidate = img_dir / (label_path.stem + ext)
                    if candidate.exists():
                        img_path = candidate
                        break

                if not img_path:
                    continue

                # Get image dimensions for denormalizing
                import cv2
                img = cv2.imread(str(img_path))
                if img is None:
                    continue
                ih, iw = img.shape[:2]

                kept_lines = []
                for line in content.split("\n"):
                    parts = line.strip().split()
                    if len(parts) < 5:
                        kept_lines.append(line)
                        continue

                    cls_idx = int(parts[0])
                    cls_name = idx_to_name.get(cls_idx, "")
                    coords = [float(x) for x in parts[1:]]

                    # Compute approximate center in pixel coords
                    xs = [coords[i] * iw for i in range(0, len(coords), 2)]
                    ys = [coords[i] * ih for i in range(1, len(coords), 2)]
                    cx = round((min(xs) + max(xs)) / 2, -1)
                    cy = round((min(ys) + max(ys)) / 2, -1)

                    sig = (cls_name, cx, cy)
                    if sig in fp_signatures:
                        removed_count += 1
                        continue  # Skip this annotation — it's a false positive

                    kept_lines.append(line)

                # Write back (may be empty → becomes negative example)
                label_path.write_text("\n".join(kept_lines) + "\n" if kept_lines else "")

        if removed_count > 0:
            logger.info("Applied %d false-positive corrections from feedback", removed_count)

    finally:
        session.close()


def _ensure_background_images(dataset_dir: Path) -> None:
    """Ensure some images have empty label files (negative examples).

    YOLO uses images with empty label files as background/negative samples.
    These teach the model what is NOT a detection, reducing false positives.
    If the dataset has no empty labels, we check for images without corresponding
    label files and create empty ones. YOLO recommends 1-10% background images.
    """
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
                # Image without label — create empty label to mark as background
                label_path.write_text("")
                empty_count += 1
            elif label_path.stat().st_size == 0 or label_path.read_text().strip() == "":
                empty_count += 1

        if empty_count > 0:
            logger.info(
                "%s: %d/%d images are background (negative) samples",
                split, empty_count, total_count,
            )


def _parse_label_file(label_path: Path) -> list[dict]:
    """Parse a YOLO label file into structured annotations."""
    annotations = []
    content = label_path.read_text().strip()
    if not content:
        return annotations

    for line in content.split("\n"):
        parts = line.strip().split()
        if len(parts) < 5:
            continue

        cls = parts[0]
        coords = [float(x) for x in parts[1:]]

        xs = coords[0::2]
        ys = coords[1::2]
        obj_w = max(xs) - min(xs)
        obj_h = max(ys) - min(ys)
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)

        annotations.append({
            "cls": cls,
            "coords": coords,
            "cx": cx,
            "cy": cy,
            "w": obj_w,
            "h": obj_h,
            "area": obj_w * obj_h,
            "raw": line.strip(),
        })

    return annotations


def _has_small_objects(dataset_dir: Path) -> bool:
    """Check if any annotations are too small for effective YOLO training."""
    for split in ("train", "val"):
        label_dir = dataset_dir / "labels" / split
        if not label_dir.exists():
            continue
        for label_path in label_dir.glob("*.txt"):
            for ann in _parse_label_file(label_path):
                if ann["area"] < SMALL_OBJECT_AREA_THRESHOLD:
                    logger.info(
                        "Found small annotation: %.4f x %.4f (area=%.6f) in %s",
                        ann["w"], ann["h"], ann["area"], label_path.name,
                    )
                    return True
    return False


def _augment_small_objects(dataset_dir: Path) -> None:
    """Generate zoomed crops around small annotations to make them learnable.

    For each small annotation, creates multiple crops at different scales and
    with slight position jitter, resized to CROP_SIZE. Remaps polygon coordinates
    into the crop's local coordinate space.

    Modifies the dataset in-place by adding crop images and labels.
    """
    yaml_path = dataset_dir / "data.yaml"

    crop_id = 0
    jitter_offsets = [
        (0, 0), (0.3, 0), (-0.3, 0), (0, 0.3), (0, -0.3),
        (0.2, 0.2), (-0.2, -0.2),
    ]

    for split in ("train", "val"):
        img_dir = dataset_dir / "images" / split
        label_dir = dataset_dir / "labels" / split

        if not img_dir.exists() or not label_dir.exists():
            continue



        for img_path in sorted(img_dir.glob("*")):
            if img_path.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                continue

            label_path = label_dir / (img_path.stem + ".txt")
            if not label_path.exists():
                continue

            annotations = _parse_label_file(label_path)
            small_anns = [a for a in annotations if a["area"] < SMALL_OBJECT_AREA_THRESHOLD]

            if not small_anns:
                continue

            img = cv2.imread(str(img_path))
            if img is None:
                continue
            img_h, img_w = img.shape[:2]

            for ann in small_anns:
                for scale in CROP_SCALES:
                    for jx, jy in jitter_offsets:
                        # Jitter is relative to the scale
                        offset_x = jx * scale
                        offset_y = jy * scale
                        crop_cx = ann["cx"] + offset_x
                        crop_cy = ann["cy"] + offset_y

                        half_w = scale / 2
                        half_h = scale / 2

                        # Crop bounds in normalized coords
                        x1_n = max(0.0, crop_cx - half_w)
                        y1_n = max(0.0, crop_cy - half_h)
                        x2_n = min(1.0, crop_cx + half_w)
                        y2_n = min(1.0, crop_cy + half_h)

                        crop_w_n = x2_n - x1_n
                        crop_h_n = y2_n - y1_n

                        if crop_w_n < 0.02 or crop_h_n < 0.02:
                            continue

                        # Pixel coords
                        x1 = int(x1_n * img_w)
                        y1 = int(y1_n * img_h)
                        x2 = int(x2_n * img_w)
                        y2 = int(y2_n * img_h)

                        crop = img[y1:y2, x1:x2]
                        if crop.shape[0] < 16 or crop.shape[1] < 16:
                            continue

                        crop_resized = cv2.resize(crop, (CROP_SIZE, CROP_SIZE))

                        # Remap annotation polygon to crop coordinate space
                        coords = ann["coords"]
                        new_coords = []
                        valid = True
                        for i in range(0, len(coords), 2):
                            nx = (coords[i] - x1_n) / crop_w_n
                            ny = (coords[i + 1] - y1_n) / crop_h_n
                            # Allow small overshoot from jitter, clamp to [0,1]
                            if nx < -0.1 or nx > 1.1 or ny < -0.1 or ny > 1.1:
                                valid = False
                                break
                            new_coords.append(max(0.0, min(1.0, nx)))
                            new_coords.append(max(0.0, min(1.0, ny)))

                        if not valid:
                            continue

                        # Assign to train (85%) or val (15%)
                        target_split = "train" if random.random() < 0.85 else "val"
                        crop_name = f"crop_{crop_id:05d}.jpg"

                        crop_img_path = dataset_dir / "images" / target_split / crop_name
                        crop_lbl_path = dataset_dir / "labels" / target_split / f"crop_{crop_id:05d}.txt"

                        cv2.imwrite(str(crop_img_path), crop_resized)
                        label_str = f"{ann['cls']} " + " ".join(f"{c:.6f}" for c in new_coords)
                        crop_lbl_path.write_text(label_str + "\n")

                        crop_id += 1

    if crop_id > 0:
        logger.info("Generated %d zoomed crop augmentations for small objects", crop_id)

        # Update data.yaml path to point to the resolved dataset dir
        # (already done, but re-confirm the path is absolute)
        if yaml_path.exists():
            content = yaml_path.read_text()
            if "path: ." in content:
                content = content.replace("path: .", f"path: {dataset_dir.resolve()}")
                yaml_path.write_text(content)
