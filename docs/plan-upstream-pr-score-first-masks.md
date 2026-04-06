# Plan: Upstream PR to mlx-vlm — Score-First Mask Generation

Push the score-first mask generation optimization to the upstream `mlx-vlm` library (github.com/Blaizzy/mlx-vlm) so all SAM3/SAM3.1 users benefit.

## Context

`mlx-vlm` v0.4.3 generates masks for all 200 DETR queries before filtering by score. On M4 Max, the mask decoder takes ~29ms for 200 queries. With score-first generation (mask only the ~5 kept detections), this drops to ~3-5ms — a 6x speedup on the mask decoder step, ~25ms saved per inference call.

The optimization is in `mlx_vlm/models/sam3/generate.py` and affects three code paths:
1. `Sam3Predictor._postprocess()` + `predict()` — single-prompt inference
2. `predict_multi()` — multi-prompt inference
3. `_detect_with_backbone()` — video pipeline with backbone caching

## Pre-work

### Fork and clone
```bash
gh repo fork Blaizzy/mlx-vlm --clone
cd mlx-vlm
git checkout -b feat/score-first-mask-generation
```

### Understand the code paths

All three entry points converge on the same pattern (mask decoder runs on all queries, then score filter). The fix is the same in each case.

**File**: `mlx_vlm/models/sam3/generate.py`

## Changes

### Change 1: `_detect_with_backbone()` (lines ~437-464)

This is the video pipeline function — highest impact since it's called per-frame.

**Before** (lines 437-453):
```python
last_hs = hs[-1]
seg_out = det.mask_decoder(
    last_hs, list(fpn_trimmed), encoder_hidden_states=encoded,
    prompt_features=inputs_embeds, prompt_mask=attention_mask,
)

scores = mx.sigmoid(pred_logits[0].squeeze())
if presence is not None:
    scores = scores * mx.sigmoid(presence[0])
boxes = pred_boxes_xyxy[0] * mx.array([W, H, W, H], ...)
boxes = mx.clip(boxes, 0, max(H, W))
mx.eval(scores, boxes, seg_out)

scores_np = np.array(scores)
keep = scores_np > threshold
if not keep.any():
    continue
masks_np = np.array(seg_out["pred_masks"][0])[keep]
```

**After**:
```python
# Score FIRST — evaluate scores and boxes without mask decoder
scores = mx.sigmoid(pred_logits[0].squeeze())
if presence is not None:
    scores = scores * mx.sigmoid(presence[0])
boxes = pred_boxes_xyxy[0] * mx.array([W, H, W, H], ...)
boxes = mx.clip(boxes, 0, max(H, W))
mx.eval(scores, boxes)

scores_np = np.array(scores)
keep = scores_np > threshold
if not keep.any():
    continue

# Only generate masks for kept detections
keep_indices = mx.array(np.where(keep)[0].astype(np.int32))
last_hs_kept = hs[-1][:, keep_indices]
seg_out = det.mask_decoder(
    last_hs_kept, list(fpn_trimmed), encoder_hidden_states=encoded,
    prompt_features=inputs_embeds, prompt_mask=attention_mask,
)
mx.eval(seg_out)

boxes_np = np.array(boxes)[keep]
masks_np = np.array(seg_out["pred_masks"][0])
```

### Change 2: `predict_multi()` (lines ~312-320)

Same pattern. Currently generates masks for all queries via `seg_out`, then passes everything to `_postprocess` which filters by score. Instead, score first.

**Before** (lines 308-331):
```python
seg_out = det.mask_decoder(last_hs, list(fpn_trimmed), ...)
mx.eval(pred_logits, pred_boxes_xyxy, seg_out, presence)
outputs = {"pred_masks": seg_out["pred_masks"], ...}
result = predictor._postprocess(outputs, image_size, threshold)
```

**After**:
```python
mx.eval(pred_logits, pred_boxes_xyxy, presence)

# Score filter
scores_np = _sigmoid(np.array(pred_logits[0]).squeeze())
if presence is not None:
    scores_np = scores_np * _sigmoid(np.array(presence[0]))
keep = scores_np > threshold

if not keep.any():
    continue

# Mask only for kept queries
keep_indices = mx.array(np.where(keep)[0].astype(np.int32))
last_hs_kept = hs[-1][:, keep_indices]
seg_out = det.mask_decoder(last_hs_kept, list(fpn_trimmed), ...)
mx.eval(seg_out)

# Build result directly (skip _postprocess)
boxes_np = np.array(pred_boxes_xyxy[0])[keep]
W, H = image_size if isinstance(image_size, tuple) else (image_size[1], image_size[0])
boxes_np[:, [0, 2]] *= W
boxes_np[:, [1, 3]] *= H
boxes_np = np.clip(boxes_np, 0, max(H, W))
masks_resized = _resize_masks(np.array(seg_out["pred_masks"][0]), (H, W))
masks_binary = (masks_resized > 0).astype(np.uint8)
result = DetectionResult(boxes=boxes_np, masks=masks_binary, scores=scores_np[keep])
```

### Change 3: `Sam3Predictor.predict()` (line ~169)

This calls `model.detect()` which returns a single dict with all 200 queries' outputs, then passes to `_postprocess()`. The optimization requires restructuring to call the detection sub-components individually (like `predict_multi` does) so we can insert the score filter before mask generation.

**Approach**: Refactor `predict()` to use the same score-first pattern as the modified `predict_multi()`, or have `predict()` delegate to `predict_multi([prompt])`.

The simplest approach: make `predict()` call `predict_multi()` with a single-element prompt list, and put the optimization in `predict_multi()` only.

### Change 4: Add `score_first_masks` parameter (optional, for backwards compatibility)

Add a parameter to `Sam3Predictor.__init__` and the free functions:
```python
def predict_multi(predictor, image, prompts, ..., score_first_masks: bool = True):
```

Default `True` (the optimization is always beneficial). Users can set `False` to get the old behavior if needed for debugging.

## Testing

### Unit test
```python
def test_score_first_masks_identical():
    """Score-first mask generation produces identical results to original."""
    predictor = Sam3Predictor(model, processor, score_threshold=0.35)
    image = Image.open("test_image.jpg")
    
    # Original
    result_orig = predict_multi(predictor, image, ["test"], score_first_masks=False)
    # Optimized
    result_fast = predict_multi(predictor, image, ["test"], score_first_masks=True)
    
    np.testing.assert_array_equal(result_orig.boxes, result_fast.boxes)
    np.testing.assert_array_equal(result_orig.masks, result_fast.masks)
    np.testing.assert_array_equal(result_orig.scores, result_fast.scores)
```

### Benchmark
```python
def test_score_first_masks_faster():
    """Score-first should be faster (fewer mask decoder calls)."""
    # Run with score_first_masks=False, time 100 inferences
    # Run with score_first_masks=True, time 100 inferences
    # Assert speedup > 10%
```

## PR description

**Title**: `perf: score-first mask generation for SAM3 DETR pipeline`

**Body**:
```
## Summary

Skip mask decoder for queries that will be filtered by score threshold.
The DETR decoder proposes 200 candidates but typically only 1-10 pass the
score threshold. Generating masks for all 200 then discarding ~195 wastes
~25ms per inference call on M4 Max.

This PR evaluates detection scores first, filters by threshold, then
runs the mask decoder only on kept detections.

## Benchmarks (M4 Max, 1008px, SAM3.1-bf16)

| Metric | Before | After | Change |
|--------|--------|-------|--------|
| Mask decoder time | 29ms | 3-5ms | -83% |
| Total per-frame | 115ms | 107ms | -7% |
| mean_iou | 0.9644 | 0.9644 | identical |

Measured on 200 surveillance camera frames with ground truth evaluation.
The optimization has zero accuracy impact because the same detections
are kept — only the order of operations changes.

## Changes

- `generate.py`: Score-first mask generation in `_detect_with_backbone()`,
  `predict_multi()`, and `predict()` (delegates to `predict_multi`)
- Added `score_first_masks` parameter (default True) for backwards compat
```

## PR checklist

- [ ] Fork mlx-vlm, create feature branch
- [ ] Implement changes in `mlx_vlm/models/sam3/generate.py`
- [ ] Run existing mlx-vlm tests: `pytest tests/`
- [ ] Add benchmark script showing speedup
- [ ] Write PR description with benchmarks
- [ ] Submit PR to Blaizzy/mlx-vlm
- [ ] Cross-reference from Waldo's own implementation
