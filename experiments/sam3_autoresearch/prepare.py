#!/usr/bin/env python3
"""Prepare benchmark dataset and evaluation utilities for SAM3.1 autoresearch.

Run once before starting experiments:
    cd /Users/atlas/repos/waldo
    .venv/bin/python experiments/sam3_autoresearch/prepare.py [--max-frames 500]

This exports labeled frames + ground-truth annotations from the Waldo database
into a local cache for benchmarking SAM3.1 inference accuracy and speed.

READ-ONLY for the autoresearch agent. Do NOT modify this file.
"""

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# ---------------------------------------------------------------------------
# Constants — imported by train.py
# ---------------------------------------------------------------------------

CACHE_DIR = Path.home() / ".cache" / "sam3_autoresearch"
BENCHMARK_DIR = CACHE_DIR / "benchmark"
TIME_BUDGET = 180  # seconds per experiment run

SUBSETS = {
    "tiny_50": 50,
    "small_200": 200,
    "medium_500": 500,
    "full": None,
}

# ---------------------------------------------------------------------------
# Evaluation functions — called by train.py, do NOT modify
# ---------------------------------------------------------------------------


def compute_mask_iou(pred_mask: np.ndarray, gt_mask: np.ndarray) -> float:
    """IoU between two binary masks of the same shape."""
    pred = pred_mask.astype(bool)
    gt = gt_mask.astype(bool)
    intersection = (pred & gt).sum()
    union = (pred | gt).sum()
    if union == 0:
        return 1.0 if intersection == 0 else 0.0
    return float(intersection / union)


def compute_box_iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    """IoU between two boxes in [x1, y1, x2, y2] pixel coordinates."""
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = (box_a[2] - box_a[0]) * (box_a[3] - box_a[1])
    area_b = (box_b[2] - box_b[0]) * (box_b[3] - box_b[1])
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return float(inter / union)


def polygon_to_mask(polygon: list[float], width: int, height: int) -> np.ndarray:
    """Convert a normalized polygon [x1,y1,x2,y2,...] to a binary mask."""
    pts = []
    for i in range(0, len(polygon), 2):
        px = int(polygon[i] * width)
        py = int(polygon[i + 1] * height)
        pts.append([px, py])
    if len(pts) < 3:
        return np.zeros((height, width), dtype=np.uint8)
    pts_arr = np.array(pts, dtype=np.int32).reshape(-1, 1, 2)
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.fillPoly(mask, [pts_arr], 1)
    return mask


def evaluate(predictions: dict, ground_truth: dict, iou_threshold: float = 0.5) -> dict:
    """Evaluate predictions against ground truth across the entire benchmark.

    Args:
        predictions: {image_id: {"boxes": (N,4), "masks": (N,H,W), "scores": (N,), "labels": [str]}}
        ground_truth: {image_id: [{"class_name", "polygon", "bbox", "width", "height"}]}
        iou_threshold: IoU threshold for a match

    Returns:
        dict with: mean_iou, precision, recall, f1, matched_count, total_pred, total_gt
    """
    all_ious = []
    total_tp = 0
    total_fp = 0
    total_fn = 0

    for img_id, gt_anns in ground_truth.items():
        pred = predictions.get(img_id)
        if pred is None or len(pred["scores"]) == 0:
            total_fn += len(gt_anns)
            continue

        W = gt_anns[0]["width"] if gt_anns else 1
        H = gt_anns[0]["height"] if gt_anns else 1

        # Build GT masks
        gt_masks = []
        for ann in gt_anns:
            if ann.get("polygon") and len(ann["polygon"]) >= 6:
                gt_masks.append(polygon_to_mask(ann["polygon"], W, H))
            elif ann.get("bbox"):
                mask = np.zeros((H, W), dtype=np.uint8)
                bx = ann["bbox"]
                x1, y1 = int(bx[0]), int(bx[1])
                x2, y2 = int(bx[2]), int(bx[3])
                mask[y1:y2, x1:x2] = 1
                gt_masks.append(mask)
            else:
                gt_masks.append(np.zeros((H, W), dtype=np.uint8))

        pred_masks = pred["masks"]
        pred_scores = pred["scores"]

        # Match predictions to GT by IoU (greedy, score-ordered)
        order = np.argsort(-pred_scores)
        gt_matched = [False] * len(gt_masks)

        for pi in order:
            pm = pred_masks[pi]
            if pm.shape != (H, W):
                pm = cv2.resize(pm.astype(np.uint8), (W, H), interpolation=cv2.INTER_NEAREST)

            best_iou = 0.0
            best_gi = -1
            for gi, gm in enumerate(gt_masks):
                if gt_matched[gi]:
                    continue
                iou = compute_mask_iou(pm, gm)
                if iou > best_iou:
                    best_iou = iou
                    best_gi = gi

            if best_iou >= iou_threshold and best_gi >= 0:
                gt_matched[best_gi] = True
                total_tp += 1
                all_ious.append(best_iou)
            else:
                total_fp += 1

        total_fn += sum(1 for m in gt_matched if not m)

    precision = total_tp / max(1, total_tp + total_fp)
    recall = total_tp / max(1, total_tp + total_fn)
    f1 = 2 * precision * recall / max(1e-8, precision + recall)
    mean_iou = float(np.mean(all_ious)) if all_ious else 0.0

    return {
        "mean_iou": mean_iou,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "matched_count": total_tp,
        "total_pred": total_tp + total_fp,
        "total_gt": total_tp + total_fn,
    }


def load_benchmark(subset: str = "small_200") -> tuple[dict, dict, list[str]]:
    """Load benchmark dataset from cache.

    Returns:
        images: {image_id: Path} — path to image files
        ground_truth: {image_id: [annotation dicts]}
        prompts: list of class name strings to use as text prompts
    """
    gt_path = BENCHMARK_DIR / "ground_truth.json"
    meta_path = BENCHMARK_DIR / "metadata.json"
    img_dir = BENCHMARK_DIR / "images"

    if not gt_path.exists():
        print("ERROR: Benchmark not prepared. Run prepare.py first.", file=sys.stderr)
        sys.exit(1)

    with open(gt_path) as f:
        full_gt = json.load(f)
    with open(meta_path) as f:
        meta = json.load(f)

    prompts = meta["classes"]

    # Apply subset
    subset_path = BENCHMARK_DIR / "subsets" / f"{subset}.json"
    if subset_path.exists():
        with open(subset_path) as f:
            subset_ids = set(json.load(f))
    else:
        subset_ids = set(full_gt.keys())

    images = {}
    ground_truth = {}
    for img_id in subset_ids:
        img_path = img_dir / f"{img_id}.jpg"
        if img_path.exists() and img_id in full_gt:
            images[img_id] = img_path
            ground_truth[img_id] = full_gt[img_id]

    return images, ground_truth, prompts


# ---------------------------------------------------------------------------
# Dataset export from Waldo DB
# ---------------------------------------------------------------------------


def export_from_waldo(max_frames: Optional[int] = None):
    """Export ground truth from Waldo database to cache directory."""
    # Import Waldo modules
    project_root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(project_root))

    from lib.config import settings  # noqa: E402
    from lib.db import Annotation, Frame, LabelingJob, SessionLocal  # noqa: E402
    from lib.storage import download_file  # noqa: E402

    # Prepare directories
    img_dir = BENCHMARK_DIR / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    (BENCHMARK_DIR / "subsets").mkdir(exist_ok=True)

    session = SessionLocal()
    try:
        # Find all jobs with accepted/pending annotations
        jobs = session.query(LabelingJob).filter(
            LabelingJob.status == "completed"
        ).all()

        if not jobs:
            print("No completed labeling jobs found in Waldo DB.")
            print("Label some data first, then re-run prepare.py.")
            sys.exit(1)

        print(f"Found {len(jobs)} completed labeling jobs")

        # Collect all annotations grouped by frame
        all_annotations = session.query(Annotation).filter(
            Annotation.status.in_(["accepted", "pending"]),
            Annotation.job_id.in_([j.id for j in jobs]),
        ).all()

        if not all_annotations:
            print("No annotations found. Label some data first.")
            sys.exit(1)

        # Group by frame
        frame_anns: dict[str, list] = {}
        for ann in all_annotations:
            fid = str(ann.frame_id)
            frame_anns.setdefault(fid, []).append(ann)

        # Collect class names
        class_names = sorted(set(a.class_name for a in all_annotations))
        print(f"Classes: {class_names}")
        print(f"Total annotations: {len(all_annotations)} across {len(frame_anns)} frames")

        # Download frames and build ground truth
        ground_truth = {}
        exported = 0

        frame_ids = list(frame_anns.keys())
        random.shuffle(frame_ids)
        if max_frames:
            frame_ids = frame_ids[:max_frames]

        for fid in frame_ids:
            frame = session.query(Frame).filter_by(id=fid).first()
            if not frame or not frame.minio_key:
                continue

            img_id = f"{frame.frame_number:06d}_{str(frame.id)[:8]}"
            img_path = img_dir / f"{img_id}.jpg"

            if not img_path.exists():
                try:
                    download_file(frame.minio_key, img_path)
                except Exception as e:
                    print(f"  Skip frame {fid}: {e}")
                    continue

            # Verify image
            img = cv2.imread(str(img_path))
            if img is None:
                img_path.unlink(missing_ok=True)
                continue

            H, W = img.shape[:2]

            # Build annotation list
            anns = []
            for ann in frame_anns[fid]:
                entry = {
                    "class_name": ann.class_name,
                    "class_index": ann.class_index,
                    "confidence": ann.confidence or 1.0,
                    "width": W,
                    "height": H,
                }
                if ann.polygon and len(ann.polygon) >= 6:
                    entry["polygon"] = ann.polygon
                    # Compute bbox from polygon
                    xs = [ann.polygon[i] * W for i in range(0, len(ann.polygon), 2)]
                    ys = [ann.polygon[i] * H for i in range(1, len(ann.polygon), 2)]
                    entry["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
                elif ann.bbox:
                    entry["bbox"] = ann.bbox[:4]
                    entry["polygon"] = None
                else:
                    continue
                anns.append(entry)

            if anns:
                ground_truth[img_id] = anns
                exported += 1

            if exported % 50 == 0 and exported > 0:
                print(f"  Exported {exported} frames...")

        print(f"Exported {exported} frames with ground truth")

        # Save ground truth
        with open(BENCHMARK_DIR / "ground_truth.json", "w") as f:
            json.dump(ground_truth, f)

        # Save metadata
        meta = {
            "classes": class_names,
            "prompts": class_names,  # Use class names as text prompts
            "total_frames": exported,
            "total_annotations": sum(len(v) for v in ground_truth.values()),
            "source_jobs": [str(j.id) for j in jobs],
        }
        with open(BENCHMARK_DIR / "metadata.json", "w") as f:
            json.dump(meta, f, indent=2)

        # Create subsets
        all_ids = list(ground_truth.keys())
        random.shuffle(all_ids)

        for name, size in SUBSETS.items():
            if size is None:
                subset_ids = all_ids
            else:
                subset_ids = all_ids[:min(size, len(all_ids))]
            with open(BENCHMARK_DIR / "subsets" / f"{name}.json", "w") as f:
                json.dump(subset_ids, f)
            print(f"  Subset '{name}': {len(subset_ids)} frames")

        print(f"\nBenchmark saved to: {BENCHMARK_DIR}")
        print(f"Run experiments with: uv run train.py")

    finally:
        session.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Prepare SAM3.1 autoresearch benchmark")
    parser.add_argument("--max-frames", type=int, default=None,
                        help="Max frames to export (default: all)")
    args = parser.parse_args()

    export_from_waldo(max_frames=args.max_frames)
