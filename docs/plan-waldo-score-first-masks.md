# Plan: Score-First Mask Generation in Waldo

Implement the score-first mask generation optimization in Waldo's SAM3.1 MLX video labeling pipeline. Instead of generating masks for all 200 DETR queries then filtering, score first and only generate masks for kept detections.

## Context

The SAM3.1 DETR decoder produces 200 candidate object queries. The current pipeline runs the mask decoder on all 200, then filters by score threshold — typically keeping only 1-10 detections. This wastes ~25ms/frame generating masks that are immediately discarded.

**Measured impact**: 29ms mask decoder time reduced to ~3-5ms (only generates masks for ~5 kept queries). Validated across 200 frames with zero accuracy loss (0.964381 IoU before and after).

## Files to modify

1. **`labeler/video_labeler.py`** — Production video labeling pipeline. Currently calls `_detect_with_backbone` from `mlx_vlm`. We need to either:
   - Override with our own detection function that uses score-first masking, OR
   - Monkey-patch `_detect_with_backbone` at import time

2. **`labeler/sam3_engine.py`** — The PyTorch/transformers pipeline. Score-first masking applies here too but the code path is different (HuggingFace model outputs). Lower priority since this pipeline is used for interactive single-frame segmentation where latency is less critical.

## Implementation

### Step 1: Create optimized detection function

Create `labeler/sam3_optimized.py` with a `detect_with_backbone_fast()` that replaces the library's `_detect_with_backbone`. The key change is in the per-prompt loop:

**Before** (current library code in `mlx_vlm/models/sam3/generate.py:437-453`):
```python
# Runs mask decoder on ALL 200 queries
last_hs = hs[-1]
seg_out = det.mask_decoder(last_hs, ...)
mx.eval(scores, boxes, seg_out)  # eval everything together

scores_np = np.array(scores)
keep = scores_np > threshold
masks_np = np.array(seg_out["pred_masks"][0])[keep]  # discard ~195 masks
```

**After** (score-first):
```python
# Score first — no mask decoder yet
mx.eval(scores, boxes)
scores_np = np.array(scores)
keep = scores_np > threshold

if keep.any():
    # Only run mask decoder on kept queries
    keep_idx = mx.array(np.where(keep)[0].astype(np.int32))
    last_hs_kept = hs[-1][:, keep_idx]  # (1, K, D) where K << 200
    seg_out = det.mask_decoder(last_hs_kept, ...)
    mx.eval(seg_out)
    masks_np = np.array(seg_out["pred_masks"][0])
```

### Step 2: Update video_labeler.py

Replace the import of `_detect_with_backbone` from `mlx_vlm` with our optimized version:

```python
# Before
from mlx_vlm.models.sam3_1.generate import _detect_with_backbone

# After
from labeler.sam3_optimized import detect_with_backbone_fast as _detect_with_backbone
```

### Step 3: Verify correctness

Run the autoresearch benchmark to confirm identical output:
```bash
cd experiments/sam3_autoresearch
uv run python train.py
```

Expected: mean_iou identical (0.9643xx), ms_per_frame reduced by ~25ms in the per-frame pipeline.

### Step 4: Integration test

Process a test video through the full pipeline and compare annotation outputs:
```bash
# Label a test video with the old pipeline, save results
# Label the same video with the new pipeline, diff results
# Annotations should be identical (same boxes, masks, scores)
```

## Risks

- **Mask decoder input shape**: The mask decoder must handle variable-size hidden state input (K queries instead of fixed 200). Verified this works — the decoder's cross-attention is agnostic to query count.
- **Edge case: 0 detections**: When `keep.any()` is False, skip mask decoder entirely and return empty result. Already handled.
- **Edge case: many detections**: If score threshold is very low and 50+ detections pass, the savings are smaller but still positive (50 masks instead of 200).

## Success criteria

- ms_per_frame reduced by 20-25ms in the video labeling pipeline
- Zero change in annotation accuracy (same IoU, precision, recall)
- All existing labeling tests pass
