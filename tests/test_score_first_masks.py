"""Verify score-first mask generation produces identical results to original.

Loads a small sample from the benchmark dataset and compares:
- Original: mlx_vlm._detect_with_backbone (masks all 200 queries, then filters)
- Optimized: sam3_optimized.detect_with_backbone_fast (scores first, masks only kept)

Both should produce identical boxes, masks, scores, and labels.
"""

import json
import sys
import time
from pathlib import Path

import mlx.core as mx
import numpy as np
from PIL import Image

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mlx_vlm.generate import wired_limit
from mlx_vlm.models.sam3.generate import Sam3Predictor
from mlx_vlm.models.sam3_1.generate import (
    _detect_with_backbone as detect_original,
    _get_backbone_features,
)
from mlx_vlm.models.sam3_1.processing_sam3_1 import Sam31Processor
from mlx_vlm.utils import get_model_path, load_model

from labeler.sam3_optimized import detect_with_backbone_fast

BENCHMARK_DIR = Path.home() / ".cache" / "sam3_autoresearch" / "benchmark"
N_SAMPLES = 10
THRESHOLD = 0.35


def load_sample_images(n: int = N_SAMPLES):
    """Load a small sample from the benchmark dataset."""
    gt_path = BENCHMARK_DIR / "ground_truth.json"
    img_dir = BENCHMARK_DIR / "images"

    with open(gt_path) as f:
        gt = json.load(f)

    images = {}
    for img_id in list(gt.keys())[:n]:
        img_path = img_dir / f"{img_id}.jpg"
        if img_path.exists():
            images[img_id] = Image.open(img_path)

    prompts_path = BENCHMARK_DIR / "metadata.json"
    with open(prompts_path) as f:
        meta = json.load(f)

    return images, meta["classes"]


def compare_results(orig, fast, img_id: str) -> bool:
    """Compare two DetectionResults for equivalence."""
    ok = True

    if len(orig.scores) != len(fast.scores):
        print(f"  [{img_id}] MISMATCH: orig has {len(orig.scores)} dets, fast has {len(fast.scores)}")
        ok = False
    else:
        # Sort both by score for consistent comparison
        orig_order = np.argsort(-orig.scores)
        fast_order = np.argsort(-fast.scores)

        # Compare scores
        score_diff = np.abs(orig.scores[orig_order] - fast.scores[fast_order])
        max_score_diff = score_diff.max() if len(score_diff) > 0 else 0
        if max_score_diff > 1e-4:
            print(f"  [{img_id}] SCORE DIFF: max={max_score_diff:.6f}")
            ok = False

        # Compare boxes
        if orig.boxes.shape == fast.boxes.shape:
            box_diff = np.abs(orig.boxes[orig_order] - fast.boxes[fast_order])
            max_box_diff = box_diff.max() if box_diff.size > 0 else 0
            if max_box_diff > 1.0:  # 1px tolerance
                print(f"  [{img_id}] BOX DIFF: max={max_box_diff:.2f}px")
                ok = False

        # Compare masks
        if orig.masks.shape == fast.masks.shape and len(orig.masks) > 0:
            mask_match = (orig.masks[orig_order] == fast.masks[fast_order]).mean()
            if mask_match < 0.99:
                print(f"  [{img_id}] MASK MATCH: {mask_match:.4f}")
                ok = False

    return ok


def main():
    print(f"Loading {N_SAMPLES} sample images from benchmark...\n")
    images, prompts = load_sample_images(N_SAMPLES)
    print(f"Loaded {len(images)} images, prompts: {prompts}\n")

    # Load model
    print("Loading SAM3.1 MLX model...")
    mp = get_model_path("mlx-community/sam3.1-bf16")
    model = load_model(mp)
    processor = Sam31Processor.from_pretrained(str(mp))
    predictor = Sam3Predictor(model, processor, score_threshold=THRESHOLD)
    print("Model loaded.\n")

    # Warm up
    first_img = next(iter(images.values()))
    inputs = predictor.processor.preprocess_image(first_img)
    pv = mx.array(inputs["pixel_values"])
    bb = _get_backbone_features(predictor.model, pv)
    _ = detect_original(predictor, bb, prompts, first_img.size, THRESHOLD)
    _ = detect_with_backbone_fast(predictor, bb, prompts, first_img.size, THRESHOLD)
    mx.synchronize()
    print("Warm-up done.\n")

    all_ok = True
    orig_times = []
    fast_times = []
    orig_total_dets = 0
    fast_total_dets = 0

    with wired_limit(model):
        for img_id, img in images.items():
            inputs = predictor.processor.preprocess_image(img)
            pixel_values = mx.array(inputs["pixel_values"])
            backbone = _get_backbone_features(predictor.model, pixel_values)

            # Original
            t0 = time.perf_counter()
            result_orig = detect_original(
                predictor, backbone, prompts, img.size, THRESHOLD,
                encoder_cache={},
            )
            mx.synchronize()
            dt_orig = time.perf_counter() - t0
            orig_times.append(dt_orig)
            orig_total_dets += len(result_orig.scores)

            # Re-compute backbone (fresh state)
            backbone = _get_backbone_features(predictor.model, pixel_values)

            # Optimized
            t0 = time.perf_counter()
            result_fast = detect_with_backbone_fast(
                predictor, backbone, prompts, img.size, THRESHOLD,
                encoder_cache={},
            )
            mx.synchronize()
            dt_fast = time.perf_counter() - t0
            fast_times.append(dt_fast)
            fast_total_dets += len(result_fast.scores)

            # Compare
            ok = compare_results(result_orig, result_fast, img_id)
            status = "OK" if ok else "FAIL"
            print(
                f"  {img_id}: {status} | "
                f"orig={dt_orig*1000:.1f}ms ({len(result_orig.scores)} dets) | "
                f"fast={dt_fast*1000:.1f}ms ({len(result_fast.scores)} dets)"
            )
            if not ok:
                all_ok = False

    print(f"\n{'='*60}")
    print(f"Results across {len(images)} images:")
    print(f"  Original:  {np.mean(orig_times)*1000:.1f} ms/frame avg, {orig_total_dets} total dets")
    print(f"  Optimized: {np.mean(fast_times)*1000:.1f} ms/frame avg, {fast_total_dets} total dets")
    speedup = np.mean(orig_times) / max(np.mean(fast_times), 1e-9)
    savings = (np.mean(orig_times) - np.mean(fast_times)) * 1000
    print(f"  Speedup:   {speedup:.2f}x ({savings:.1f} ms saved per frame)")
    print(f"  Match:     {'ALL IDENTICAL' if all_ok else 'DIFFERENCES FOUND'}")
    print(f"{'='*60}")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
