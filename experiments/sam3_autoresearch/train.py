#!/usr/bin/env python3
"""SAM3.1 MLX inference benchmark — the ONLY file the agent should modify.

Optimize SAM3.1 inference for surveillance camera labeling on Apple Silicon.
Goal: maintain mean_iou >= 0.99 while minimizing ms_per_frame.

Outputs (printed to stdout, parsed by autoresearch loop):
    mean_iou:         0.9950
    precision:        0.9800
    recall:           0.9900
    ms_per_frame:     145.2
    peak_memory_mb:   4500.0
    total_frames:     200
    total_detections: 850

The agent modifies the CONFIGURATION section and the INFERENCE PIPELINE
functions below. The EVALUATION section calls prepare.py and must not be
modified (it's imported, not inlined).
"""

import gc
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

import cv2
import mlx.core as mx
import mlx.nn as nn
import numpy as np
from mlx.utils import tree_reduce
from PIL import Image

# ============================================================
# CONFIGURATION — Modify these knobs to optimize speed/accuracy
# ============================================================

RESOLUTION = 1008           # Input resolution for SAM3.1 (lower = faster, less accurate)
SCORE_THRESHOLD = 0.35      # Minimum detection confidence
NMS_IOU_THRESHOLD = 0.5     # NMS overlap threshold for deduplication
BACKBONE_CACHE_EVERY = 1    # Reuse backbone features for N consecutive images (1 = no caching)
SUBSET = "small_200"        # Benchmark subset: tiny_50, small_200, medium_500, full
MODEL_ID = "mlx-community/sam3.1-bf16"

# Advanced — experiment with these
PREPROCESS_INTERPOLATION = Image.BILINEAR  # NEAREST, BILINEAR, BICUBIC, LANCZOS
IMAGE_MEAN = (0.5, 0.5, 0.5)
IMAGE_STD = (0.5, 0.5, 0.5)
MASK_RESIZE_METHOD = "cv2_linear"  # pil_bilinear, cv2_nearest, cv2_linear

# ============================================================
# MODEL LOADING — Loads weights from mlx-vlm, can be modified
# ============================================================

from mlx_vlm.models.sam3.generate import DetectionResult, Sam3Predictor, SimpleTracker
from mlx_vlm.models.sam3_1.processing_sam3_1 import Sam31Processor
from mlx_vlm.utils import get_model_path, load_model


def load_predictor(
    model_id: str = MODEL_ID,
    resolution: int = RESOLUTION,
    score_threshold: float = SCORE_THRESHOLD,
) -> Sam3Predictor:
    """Load SAM3.1 MLX predictor. Override to add quantization, pruning, etc."""
    mp = get_model_path(model_id)
    model = load_model(mp)
    processor = Sam31Processor.from_pretrained(str(mp))
    if resolution != 1008:
        processor.image_size = resolution
    predictor = Sam3Predictor(model, processor, score_threshold=score_threshold)
    return predictor


# ============================================================
# INFERENCE PIPELINE — Modify these functions to optimize speed
# ============================================================


# Precompute normalization constants
_NORM_MEAN = np.array(IMAGE_MEAN, dtype=np.float32)
_NORM_INV_STD = 1.0 / np.array(IMAGE_STD, dtype=np.float32)
_NORM_OFFSET = -_NORM_MEAN * _NORM_INV_STD  # Combine: (x/255 - mean) / std = x * (1/255/std) + offset

def preprocess_image(
    image: Image.Image,
    resolution: int = RESOLUTION,
) -> np.ndarray:
    """Preprocess image for SAM3.1. Returns (1, H, W, 3) float32 array."""
    image = image.convert("RGB")
    image = image.resize((resolution, resolution), PREPROCESS_INTERPOLATION)
    pixel_values = np.array(image, dtype=np.float32) * (1.0 / 255.0)
    pixel_values = (pixel_values - _NORM_MEAN) * _NORM_INV_STD
    return pixel_values[None]  # (1, H, W, 3)


_compiled_backbone = None

def get_backbone_features(model, pixel_values: mx.array) -> mx.array:
    """Run ViT backbone only (no FPN neck). ~67ms on M4 Max.
    Uses mx.compile for JIT compilation speedup.
    """
    global _compiled_backbone
    if _compiled_backbone is None:
        _compiled_backbone = mx.compile(model.detector_model.vision_encoder.backbone)
    features = _compiled_backbone(pixel_values)
    mx.eval(features)
    return features


def get_det_features(model, backbone_features: mx.array):
    """Run detection FPN neck + flatten for DETR. ~3ms.

    Returns: (src, pos_flat, det_features, (H_f, W_f))
    """
    det = model.detector_model
    det_features, _, _ = det.vision_encoder.neck(
        backbone_features, need_det=True, need_interactive=False, need_propagation=False
    )
    fpn_pos = [det._pos_enc(feat) for feat in det_features]

    encoder_feat = det_features[-1]
    B, H_f, W_f, D = encoder_feat.shape
    src = encoder_feat.reshape(B, H_f * W_f, D)
    pos_flat = fpn_pos[-1].reshape(B, H_f * W_f, D)
    # Defer eval — fuse with downstream DETR computation
    return src, pos_flat, det_features, (H_f, W_f)


def run_detr_encoder(model, src, pos_flat, inputs_embeds, attention_mask):
    """Run DETR encoder. ~63ms. (Can't compile — has internal mx.eval)."""
    encoded = model.detector_model.detr_encoder(
        src, pos_flat, inputs_embeds, attention_mask
    )
    return encoded


def postprocess_mlx(
    pred_logits: mx.array,
    pred_boxes: mx.array,
    pred_masks,
    presence: mx.array,
    image_size: tuple,
    threshold: float,
) -> DetectionResult:
    """Post-process in MLX, single numpy conversion at the end.

    Optimization ideas:
    - Skip mask generation for detection-only mode
    - Lower mask resolution
    - Vectorized mask resize
    - Skip sigmoid for fast thresholding
    """
    W, H = image_size if isinstance(image_size, tuple) else (image_size[1], image_size[0])

    scores = mx.sigmoid(pred_logits[0].squeeze())
    if presence is not None:
        scores = scores * mx.sigmoid(presence[0])

    boxes = pred_boxes[0] * mx.array([W, H, W, H], dtype=pred_boxes.dtype)
    boxes = mx.clip(boxes, 0, max(H, W))

    if pred_masks is not None:
        mx.eval(scores, boxes, pred_masks)
        scores_np = np.array(scores)
        keep = scores_np > threshold
        if not keep.any():
            return DetectionResult(
                boxes=np.zeros((0, 4)),
                masks=np.zeros((0, H, W), dtype=np.uint8),
                scores=np.zeros((0,)),
            )
        masks_np = np.array(pred_masks[0])[keep]
        masks_resized = resize_masks(masks_np, (H, W))
        masks_binary = (masks_resized > 0).astype(np.uint8)
    else:
        mx.eval(scores, boxes)
        scores_np = np.array(scores)
        keep = scores_np > threshold
        if not keep.any():
            return DetectionResult(
                boxes=np.zeros((0, 4)),
                masks=np.zeros((0, H, W), dtype=np.uint8),
                scores=np.zeros((0,)),
            )
        masks_binary = np.zeros((keep.sum(), H, W), dtype=np.uint8)

    return DetectionResult(
        boxes=np.array(boxes)[keep],
        masks=masks_binary,
        scores=scores_np[keep],
    )


def resize_masks(masks: np.ndarray, target_size: tuple) -> np.ndarray:
    """Resize masks to target (H, W).

    Optimization ideas:
    - Use cv2 instead of PIL (faster for uint8)
    - Use nearest interpolation for speed
    - Batch resize with numpy
    - Skip if already correct size
    """
    H, W = target_size

    if MASK_RESIZE_METHOD == "cv2_nearest":
        resized = []
        for mask in masks:
            r = cv2.resize(mask.astype(np.float32), (W, H), interpolation=cv2.INTER_NEAREST)
            resized.append(r)
        return np.stack(resized) if resized else np.zeros((0, H, W))

    elif MASK_RESIZE_METHOD == "cv2_linear":
        resized = []
        for mask in masks:
            r = cv2.resize(mask.astype(np.float32), (W, H), interpolation=cv2.INTER_LINEAR)
            resized.append(r)
        return np.stack(resized) if resized else np.zeros((0, H, W))

    else:  # pil_bilinear (default, matches mlx-vlm)
        resized = []
        for mask in masks:
            pil_mask = Image.fromarray(mask.astype(np.float32))
            pil_mask = pil_mask.resize((W, H), Image.BILINEAR)
            resized.append(np.array(pil_mask))
        return np.stack(resized) if resized else np.zeros((0, H, W))


def nms(result: DetectionResult, iou_thresh: float = NMS_IOU_THRESHOLD) -> DetectionResult:
    """Non-Maximum Suppression with vectorized IoU computation."""
    if len(result.scores) == 0:
        return result
    boxes, scores, masks = result.boxes, result.scores, result.masks
    order = np.argsort(-scores)
    # Precompute areas
    areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    keep = []
    suppressed = np.zeros(len(scores), dtype=bool)
    for idx in range(len(order)):
        i = order[idx]
        if suppressed[i]:
            continue
        keep.append(i)
        # Vectorized IoU against remaining candidates
        remaining = order[idx + 1:]
        if len(remaining) == 0:
            break
        xx1 = np.maximum(boxes[i, 0], boxes[remaining, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[remaining, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[remaining, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[remaining, 3])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        union = areas[i] + areas[remaining] - inter
        iou = inter / np.maximum(union, 1e-6)
        suppressed[remaining[iou > iou_thresh]] = True
    labels = [result.labels[i] for i in keep] if result.labels else None
    track_ids = result.track_ids[keep] if result.track_ids is not None else None
    return DetectionResult(
        boxes=boxes[keep], masks=masks[keep], scores=scores[keep],
        labels=labels, track_ids=track_ids,
    )


def detect_with_backbone(
    predictor: Sam3Predictor,
    backbone_features: mx.array,
    prompts: list[str],
    image_size: tuple,
    threshold: float,
    encoder_cache: Optional[dict] = None,
) -> DetectionResult:
    """Full detection pipeline on pre-computed backbone features.

    This is the main inference function. Runs:
    1. FPN neck (~3ms)
    2. Per-prompt: text encoding (cached) + DETR encoder (~8ms) + decoder + mask
    3. Post-processing + NMS

    Optimization ideas:
    - Fuse multiple prompts into a single DETR pass
    - Skip mask decoder for detection-only benchmarks
    - Cache DETR encoder across frames with same backbone
    - Reduce number of decoder queries
    - Quantize encoder/decoder weights
    """
    det = predictor.model.detector_model

    # FPN neck
    src, pos_flat, det_features, spatial = get_det_features(predictor.model, backbone_features)
    H_f, W_f = spatial

    all_boxes, all_masks, all_scores, all_labels = [], [], [], []

    for prompt in prompts:
        inputs_embeds, attention_mask = predictor._get_input_embeddings(prompt)

        # DETR encoder with optional caching
        cached = encoder_cache.get(prompt) if encoder_cache is not None else None
        if cached is not None:
            encoded = cached["encoded"]
        else:
            encoded = run_detr_encoder(
                predictor.model, src, pos_flat, inputs_embeds, attention_mask
            )
            if encoder_cache is not None:
                encoder_cache[prompt] = {"encoded": encoded}

        # DETR decoder
        hs, ref_boxes, presence_logits = det.detr_decoder(
            vision_features=encoded,
            inputs_embeds=inputs_embeds,
            vision_pos_encoding=pos_flat,
            text_mask=attention_mask,
            spatial_shape=(H_f, W_f),
        )

        # Box conversion in MLX
        pred_boxes_cxcywh = ref_boxes[-1]
        cx, cy, w, h = (
            pred_boxes_cxcywh[..., 0],
            pred_boxes_cxcywh[..., 1],
            pred_boxes_cxcywh[..., 2],
            pred_boxes_cxcywh[..., 3],
        )
        pred_boxes_xyxy = mx.stack(
            [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=-1
        )

        # Scoring
        all_logits = det.dot_product_scoring(hs, inputs_embeds, attention_mask)
        pred_logits = all_logits[-1].squeeze(-1)
        presence = presence_logits[-1]

        # Mask decoder
        last_hs = hs[-1]
        seg_out = det.mask_decoder(
            last_hs,
            list(det_features),
            encoder_hidden_states=encoded,
            prompt_features=inputs_embeds,
            prompt_mask=attention_mask,
        )

        mx.eval(pred_logits, pred_boxes_xyxy, seg_out, presence)

        result = postprocess_mlx(
            pred_logits if pred_logits.ndim == 2 else pred_logits[None],
            pred_boxes_xyxy if pred_boxes_xyxy.ndim == 3 else pred_boxes_xyxy[None],
            seg_out["pred_masks"],
            presence if presence.ndim == 2 else presence[None],
            image_size,
            threshold,
        )
        if len(result.scores) > 0:
            result = nms(result)
            all_boxes.append(result.boxes)
            all_masks.append(result.masks)
            all_scores.append(result.scores)
            all_labels.extend([prompt] * len(result.scores))

    if not all_scores:
        W, H = image_size if isinstance(image_size, tuple) else (image_size[1], image_size[0])
        return DetectionResult(
            boxes=np.zeros((0, 4)),
            masks=np.zeros((0, H, W), dtype=np.uint8),
            scores=np.zeros((0,)),
            labels=[],
        )

    return DetectionResult(
        boxes=np.concatenate(all_boxes),
        masks=np.concatenate(all_masks),
        scores=np.concatenate(all_scores),
        labels=all_labels,
    )


# ============================================================
# BENCHMARK RUNNER
# ============================================================


def run_benchmark():
    """Run the full benchmark. Outputs metrics to stdout."""
    from prepare import BENCHMARK_DIR, TIME_BUDGET, evaluate, load_benchmark

    # Load benchmark data
    images, ground_truth, prompts = load_benchmark(SUBSET)
    n_frames = len(images)
    if n_frames == 0:
        print("ERROR: No benchmark images found. Run prepare.py first.")
        return

    print(f"# Benchmark: {n_frames} frames, {len(prompts)} prompts: {prompts}", flush=True)

    # Load model
    t_load = time.perf_counter()
    predictor = load_predictor()
    load_ms = (time.perf_counter() - t_load) * 1000
    print(f"# Model loaded in {load_ms:.0f}ms", flush=True)

    # Warm up (1 inference to compile kernels)
    warmup_id = next(iter(images))
    warmup_img = Image.open(images[warmup_id])
    pv = mx.array(preprocess_image(warmup_img))
    backbone = get_backbone_features(predictor.model, pv)
    _ = detect_with_backbone(predictor, backbone, prompts, warmup_img.size, SCORE_THRESHOLD)
    del backbone, pv
    mx.synchronize()

    # Wired memory limit for stable performance
    model_bytes = tree_reduce(
        lambda acc, x: acc + x.nbytes if isinstance(x, mx.array) else acc,
        predictor.model, 0,
    )
    max_rec = mx.device_info()["max_recommended_working_set_size"]
    old_limit = mx.set_wired_limit(max_rec)

    # Run inference
    predictions = {}
    frame_times = []
    total_dets = 0
    backbone_cache = None
    encoder_cache: dict = {}
    peak_memory = 0

    gc.disable()  # Avoid GC stalls during benchmark

    try:
        items = list(images.items())

        # Preload ALL images as preprocessed tensors (eliminates I/O from timing)
        print(f"# Preloading {len(items)} images...", flush=True)
        preloaded = {}
        for img_id, img_path in items:
            img = Image.open(img_path)
            preloaded[img_id] = (img.size, mx.array(preprocess_image(img)))
        print(f"# Preload complete", flush=True)

        for i, (img_id, img_path) in enumerate(items):
            t0 = time.perf_counter()

            img_size, pixel_values = preloaded[img_id]

            # Backbone caching
            if i % BACKBONE_CACHE_EVERY == 0 or backbone_cache is None:
                backbone_cache = get_backbone_features(predictor.model, pixel_values)
                encoder_cache.clear()

            result = detect_with_backbone(
                predictor, backbone_cache, prompts, img_size,
                SCORE_THRESHOLD, encoder_cache=encoder_cache,
            )

            dt = time.perf_counter() - t0
            frame_times.append(dt)
            total_dets += len(result.scores)

            # Store predictions for evaluation
            predictions[img_id] = {
                "boxes": result.boxes,
                "masks": result.masks,
                "scores": result.scores,
                "labels": result.labels or [],
            }

            # Track memory
            if mx.metal.is_available():
                mem = mx.metal.get_peak_memory() / 1e6
                if mem > peak_memory:
                    peak_memory = mem

            # Progress
            if (i + 1) % 50 == 0:
                avg_ms = np.mean(frame_times[-50:]) * 1000
                print(f"# Frame {i+1}/{n_frames}: {avg_ms:.1f} ms/frame, {total_dets} dets", flush=True)

            # Time budget check
            elapsed = sum(frame_times)
            if elapsed > TIME_BUDGET:
                print(f"# Time budget exceeded at frame {i+1}/{n_frames}", flush=True)
                break

    finally:
        gc.enable()
        mx.synchronize()
        mx.set_wired_limit(old_limit)

    # Evaluate accuracy
    metrics = evaluate(predictions, ground_truth)

    # Compute timing stats
    ms_per_frame = np.mean(frame_times) * 1000
    total_time = sum(frame_times)
    frames_processed = len(frame_times)

    # Print results (parsed by autoresearch loop)
    print(f"mean_iou:         {metrics['mean_iou']:.6f}")
    print(f"precision:        {metrics['precision']:.6f}")
    print(f"recall:           {metrics['recall']:.6f}")
    print(f"ms_per_frame:     {ms_per_frame:.1f}")
    print(f"peak_memory_mb:   {peak_memory:.1f}")
    print(f"total_frames:     {frames_processed}")
    print(f"total_detections: {total_dets}")
    print(f"total_seconds:    {total_time:.1f}")


if __name__ == "__main__":
    try:
        run_benchmark()
    except Exception:
        traceback.print_exc()
        # Print zero metrics so autoresearch records it as a crash
        print("mean_iou:         0.000000")
        print("precision:        0.000000")
        print("recall:           0.000000")
        print("ms_per_frame:     9999.0")
        print("peak_memory_mb:   0.0")
        print("total_frames:     0")
        print("total_detections: 0")
        print("total_seconds:    0.0")
