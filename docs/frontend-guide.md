# Waldo Frontend Guide: Training a Surveillance Camera Detector

This guide walks through using the Waldo web UI to train and deploy a custom YOLO26n-seg surveillance camera detector — the same workflow demonstrated in `training/surveillance_camera/docs/`.

## Prerequisites

Start the full stack with native MPS GPU workers:

```bash
# Start infrastructure (Postgres, Redis, MinIO)
docker compose up -d postgres redis minio minio-init

# Run database migrations
uv run alembic upgrade head

# Start the API server
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# In a second terminal — start the labeler worker (SAM3, uses MPS GPU)
uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo

# In a third terminal — start the trainer worker (YOLO, uses MPS GPU)
uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo -Q training
```

Or use the one-liner:

```bash
make up-gpu
```

Open **http://localhost:8000** in your browser.

---

## Step 1: Upload Video

**Navigate to: Upload** (or `/upload`)

1. Click **Choose File** or drag-and-drop your video onto the upload area
2. Select `Target.mp4` (or any video containing surveillance cameras)
3. The file uploads automatically — you'll be redirected to the Label page

![Upload Page](../training/surveillance_camera/docs/01_source_video_sample.jpg)

> **What happens behind the scenes**: The video is stored in MinIO, metadata (resolution, FPS, duration) is extracted and saved to PostgreSQL.

---

## Step 2: Label with SAM3 (Teacher)

**Navigate to: Label** (auto-redirected after upload, or `/label/{videoId}`)

### Text Search Mode (default)

1. Ensure **Text Search** tab is selected (it is by default)
2. Set the **Task Type** dropdown to **Segmentation**
3. In the text input, type: `surveillance camera`
4. Click **Search**

### What to expect

- A progress bar appears showing frame extraction and SAM3 processing
- Status moves through: `extracting` → `labeling` → `converting` → `completed`
- Frames are extracted from the video, then SAM3 processes each frame looking for objects matching your text prompt
- This takes 30-60 seconds depending on video length

### When complete

Two buttons appear:
- **Review Results** — go to the review page to inspect annotations
- **Download Dataset** — download the raw YOLO-format dataset ZIP

Click **Review Results**.

> **Tip**: For click-based labeling (exemplar mode), switch to **Click Mode**, select a frame, click on the camera with left-click (positive point), then click **Label**.

---

## Step 3: Review Labels

**Navigate to: Review** (`/review/{jobId}`)

This page shows every frame with its annotations overlaid.

1. **Browse frames** — each frame displays the video image with green polygon overlays marking detected surveillance cameras
2. **Check stats** — the sidebar shows:
   - Total annotations count
   - Total frames and annotated frame count
   - Class distribution breakdown
   - Accept/reject counts
3. **Accept or reject** each annotation:
   - Click **Accept** (checkmark) to confirm correct labels
   - Click **Reject** (X) to discard false positives
4. Review all annotations — **accept the good ones, reject the bad**

### What to look for

- SAM3 should have found dome/bullet cameras mounted on the building
- Confidence scores around 0.70-0.80 are typical for text-prompted detection
- The segmentation polygon should tightly outline the camera shape

When satisfied, click **Train Model** to proceed.

> **Important**: Only accepted annotations are used for training. Rejecting false positives improves model quality.

---

## Step 4: Train YOLO26n-seg (Student)

**Navigate to: Train** (`/train/{jobId}`)

### Configure training

1. **Task Type**: Select **Segmentation** (should auto-populate)
2. **Model Variant**: Select **yolo26n-seg** from the dropdown
   - `yolo26n-seg` — fastest, smallest (6.4MB, 10.2 GFLOPs)
   - `yolo26s-seg` — slightly larger, more accurate
   - `yolo26m-seg` / `yolo26l-seg` / `yolo26x-seg` — progressively larger
3. **Epochs**: Set to `100` (or `50` for a quick test)
4. **Batch Size**: Set to `8` (reduce to `2` or `1` if you get OOM errors)
5. **Image Size**: Set to `640` (default, or `1280` for small objects)

Click **Start Training**.

### Monitor training

The page switches to a live training dashboard:

- **Progress bar** showing current epoch / total epochs
- **Live metrics** via WebSocket streaming:
  - `mAP50(B)` — box mean average precision at IoU 0.50
  - `mAP50-95(B)` — box mAP averaged over IoU 0.50-0.95
  - `mAP50(M)` / `mAP50-95(M)` — mask (segmentation) equivalents
  - `precision(B)` / `recall(B)` — detection precision and recall
  - Various loss values (box, seg, cls, dfl)

### What to expect

| Epoch Range | Typical mAP50 | Notes |
|-------------|---------------|-------|
| 1-3 | 0.05-0.70 | Model warming up |
| 5-10 | 0.70-0.90 | Rapid improvement |
| 15-50 | 0.90-0.99 | Convergence |
| 50+ | 0.99+ | Plateau (early stop) |

Training on an M4 Max takes ~20 seconds per epoch. Total: ~30-40 minutes for 100 epochs.

### When complete

- Status shows **completed**
- A **Download Weights** button appears with the `best.pt` file
- Metrics show final precision, recall, and mAP values

> **Note on small objects**: If training on full 4K frames with tiny objects (like distant cameras), mAP may stay at 0. In that case, create zoomed crops of the annotated regions first. See `training/surveillance_camera/docs/README.md` for the augmentation strategy used.

---

## Step 5: Deploy Model

**Navigate to: Deploy** (`/deploy`)

### Activate the trained model

1. The **Models** section lists all trained models
2. Find your `surveillance_camera` model (most recent)
3. Click **Activate** — this loads the model into the inference engine on MPS GPU

The **Inference Server** panel at the top updates to show:
- **Status**: Loaded
- **Model**: your model name
- **Type**: segment
- **Device**: mps

### Export (optional)

To export the model for deployment outside Waldo:

1. Select an export format from the dropdown:
   - **ONNX** — cross-platform, most compatible
   - **CoreML** — optimized for Apple devices
   - **TFLite** — for mobile/edge
   - **TorchScript** — for PyTorch serving
   - **OpenVINO** — for Intel hardware
2. Click **Export** — the export runs asynchronously

### API usage reference

The Deploy page shows `curl` commands you can copy-paste:

```bash
# Image prediction
curl -X POST http://localhost:8000/api/v1/predict/image \
  -F "file=@image.jpg" | jq

# Video prediction (with object tracking)
curl -X POST http://localhost:8000/api/v1/predict/video \
  -F "file=@video.mp4" | jq

# Check server status
curl http://localhost:8000/api/v1/serve/status | jq
```

---

## Step 6: Test with Demo Page

**Navigate to: Demo** (`/demo`)

### Image prediction

1. Ensure **Image** tab is selected
2. Click **Choose Image** and select a frame or photo containing surveillance cameras
3. Click **Predict**
4. The image renders with:
   - Green bounding boxes around detected cameras
   - Segmentation mask overlays (semi-transparent)
   - Class label + confidence percentage
5. Adjust the **Confidence** slider to filter detections (default 25%)

### Video prediction

1. Switch to **Video** tab
2. Click **Choose Video** and select `Target.mp4`
3. Click **Track Objects**
4. For short videos (< 500 frames): results return immediately as JSON
5. For long videos: a WebSocket connection streams per-frame results live

After processing:
- Use the **frame slider** to scrub through detections
- Each frame shows:
  - Detection count
  - Object class, track ID (`#1`, `#2`...), and confidence
- Summary shows total frames tracked and unique track count

> **Tip**: Lower the confidence threshold to 0.15-0.20 to catch more distant cameras at the expense of potential false positives.

---

## Complete Navigation Flow

```
┌──────────┐    ┌───────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐    ┌──────────┐
│  Upload  │───►│   Label   │───►│  Review  │───►│   Train   │───►│  Deploy  │───►│   Demo   │
│          │    │           │    │          │    │           │    │          │    │          │
│ Drop     │    │ "surv.    │    │ Accept/  │    │ yolo26n-  │    │ Activate │    │ Upload   │
│ video    │    │  camera"  │    │ reject   │    │ seg, 100  │    │ model,   │    │ image or │
│          │    │ + Search  │    │ labels   │    │ epochs    │    │ export   │    │ video    │
└──────────┘    └───────────┘    └──────────┘    └───────────┘    └──────────┘    └──────────┘
    /upload      /label/:id      /review/:id     /train/:id        /deploy          /demo
```

The **Jobs** page (`/jobs`) provides a dashboard of all labeling jobs with status, progress, and links back to review or retrain.

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Upload fails | Check MinIO is running: `curl http://localhost:9000/minio/health/live` |
| Labeling stuck on "pending" | Check Celery labeler worker is running and SAM3 model is downloaded |
| Training mAP stays at 0 | Objects too small at training resolution. Use zoomed crops or increase `imgsz` |
| "No active model" on Demo page | Go to Deploy, click Activate on your model |
| OOM during training | Reduce batch size to 1, or reduce imgsz to 320 |
| WebSocket not connecting | Ensure the app is running on the same host/port your browser is pointing to |
