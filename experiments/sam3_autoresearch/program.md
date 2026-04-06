# SAM3.1 MLX Speed Optimization — Autoresearch Program

You are an autonomous ML research agent optimizing SAM3.1 inference on Apple Silicon (M4 Max) for surveillance camera labeling. Your goal: **achieve ≥99% mean IoU while minimizing ms/frame.**

## Setup (run once at start)

1. **Run tag**: Propose a short tag like `apr5` based on the date.
2. **Create branch**: `git checkout -b autoresearch/sam3-<tag>` from the current branch.
3. **Read files**: Read `README.md` (if present), `prepare.py` (read-only), and `train.py` (your playground).
4. **Verify data**: Check `~/.cache/sam3_autoresearch/benchmark/` has images and `ground_truth.json`. If missing, run: `cd /Users/atlas/repos/waldo && .venv/bin/python experiments/sam3_autoresearch/prepare.py`
5. **Initialize results.tsv**: Create with header: `commit\tms_per_frame\tmean_iou\tprecision\trecall\tmemory_gb\tstatus\tdescription`

## Rules

- **Only modify `train.py`**. Do NOT modify `prepare.py` (contains evaluation logic).
- **Cannot install new packages** — use only what's in `pyproject.toml` and the parent project's `.venv`.
- **Time budget**: Each experiment run should complete within 3 minutes (TIME_BUDGET=180s).
- **Primary metric**: `ms_per_frame` (lower is better).
- **Hard constraint**: `mean_iou >= 0.99`. Any experiment dropping below this is immediately discarded.
- **Run command**: `cd /Users/atlas/repos/waldo/experiments/sam3_autoresearch && /Users/atlas/repos/waldo/.venv/bin/python train.py > run.log 2>&1`
- **Parse results**: `grep "^mean_iou:\|^ms_per_frame:\|^precision:\|^recall:\|^peak_memory_mb:" run.log`

## Experiment loop

```
LOOP FOREVER:
  1. Read current train.py, review results.tsv for past experiments
  2. Choose an optimization to try (see ideas below)
  3. Modify train.py
  4. git add train.py && git commit -m "experiment: <description>"
  5. Run: cd /Users/atlas/repos/waldo/experiments/sam3_autoresearch && \
         /Users/atlas/repos/waldo/.venv/bin/python train.py > run.log 2>&1
  6. Parse metrics from run.log
  7. Record in results.tsv (DO NOT commit results.tsv)
  8. Decision:
     - If mean_iou < 0.99: DISCARD (git reset HEAD~1 --hard)
     - If ms_per_frame >= previous best AND mean_iou didn't significantly improve: DISCARD
     - If ms_per_frame < previous best AND mean_iou >= 0.99: KEEP
     - If mean_iou improved significantly (>0.005) at same speed: KEEP
  9. If crashed: read `tail -n 50 run.log`, fix trivial bugs, skip broken ideas
  10. CONTINUE — never stop until interrupted
```

## Results format

Tab-separated `results.tsv` with columns:
- `commit`: 7-char git hash
- `ms_per_frame`: average ms per frame (lower is better)
- `mean_iou`: accuracy metric (must be ≥ 0.99)
- `precision`: detection precision
- `recall`: detection recall
- `memory_gb`: peak memory in GB
- `status`: `keep`, `discard`, or `crash`
- `description`: short text describing what was tried

Example row:
```
a1b2c3d	145.2	0.9953	0.9800	0.9900	4.5	keep	baseline: default parameters
```

## Optimization ideas (non-exhaustive, be creative!)

### Resolution & preprocessing
- Lower resolution: try 784, 672, 512 — find the sweet spot where IoU stays ≥0.99
- Faster interpolation: NEAREST instead of BILINEAR for resize
- Use cv2 resize instead of PIL (faster for large images)
- Skip normalization if model tolerates it
- Precompute mean/std subtraction with integer ops

### Caching strategies
- Backbone caching: reuse ViT features across similar frames
- Encoder caching: cache DETR encoder output per prompt (already partial)
- Text embedding caching: already cached, verify no redundant computation
- FPN feature caching between similar images

### Quantization & precision
- Quantize model weights to int8 or int4 (mlx supports quantization)
- Mixed precision: keep backbone in float16, decoder in float32
- Quantize specific layers (attention, FFN) selectively
- Try `mx.quantize(model, bits=4)` or `mx.quantize(model, bits=8)`

### Architecture modifications
- Skip mask decoder entirely if only boxes are needed for IoU
- Reduce DETR decoder layers (trim `detr_decoder` iterations)
- Prune attention heads that contribute least to accuracy
- Reduce FPN neck computation (skip unused scales)
- Early exit: stop decoder if confidence is already high

### MLX-specific optimizations
- Fuse mx.eval calls to reduce sync overhead
- Use mx.compile for hot functions
- Batch multiple prompts through DETR in a single pass
- Optimize memory layout for Metal GPU (channel-first vs channel-last)
- Use `mx.fast` operations where available

### Post-processing
- Vectorized NMS (batch IoU computation instead of nested loops)
- Skip mask resize for images already at native resolution
- Use cv2 for mask operations instead of PIL (10-50x faster for uint8)
- Approximate contour extraction for polygon generation

### Apple Neural Engine (ANE) — advanced
- The ANE repo is available at `../autoresearch/ane/` (cloned from github.com/maderix/ANE)
- The ANE bridge (`bridge/ane_bridge.h`) exposes C-callable APIs via ctypes
- Potential: offload ViT backbone patches to ANE (convolutions are ANE-native)
- Potential: offload attention QKV projections (linear layers = 1x1 convolutions)
- Must handle `[1, C, 1, S]` tensor layout constraint
- CoreML conversion via `coremltools` is a more stable ANE path
- Generate CoreML model from the SAM3.1 ViT backbone, run via `coremltools.models.MLModel.predict()`

### Pipeline restructuring
- Async pipelining: overlap I/O (image load) with inference
- Process images in batches instead of one-by-one
- Progressive refinement: quick low-res scan, then high-res on detections only
- Multi-scale detection: low-res for large objects, high-res crop for small ones
- Frame differencing: skip re-inference on nearly identical frames

### Model distillation / pruning
- Identify and skip redundant transformer blocks
- Structured pruning of attention heads with lowest impact
- Width pruning: reduce hidden dimension if accuracy allows
- Depth pruning: skip every other decoder layer

## Decision heuristics

- **Simplicity wins**: A 2ms improvement from deleting code beats a 5ms improvement from 50 lines of hacks.
- **Compound gains**: Small improvements stack — 5% here and 8% there add up fast.
- **Measure before optimizing**: Profile where time is actually spent before optimizing.
- **Accuracy is sacred**: Never ship a speed improvement that drops below 99% IoU.
- **VRAM is flexible**: Memory increases are acceptable for meaningful speed gains.
- **Don't break the eval**: The evaluation pipeline in prepare.py must see valid predictions. Ensure masks, boxes, and scores are in the expected format.

## Understanding the pipeline

The current inference flow for one image:
1. **Image load + preprocess** (~5ms): PIL resize to 1008x1008, normalize
2. **ViT backbone** (~67ms): Vision transformer feature extraction
3. **FPN neck** (~3ms): Multi-scale feature pyramid
4. **Text encoding** (cached, ~0ms after first call): Text prompt → embeddings
5. **DETR encoder** (~8ms): Cross-attention between vision and text features
6. **DETR decoder** (~12ms): Object query refinement + box regression
7. **Mask decoder** (~15ms): Per-object mask generation
8. **Post-process** (~5ms): Sigmoid, NMS, mask resize
9. **Total** (~115ms per image at 1008px resolution)

The ViT backbone is the biggest bottleneck at ~58% of total time. Reducing its cost
(via caching, resolution reduction, quantization, or ANE offload) has the highest ROI.
