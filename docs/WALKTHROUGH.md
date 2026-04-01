# Waldo Walkthrough

A step-by-step guide showing how to use Waldo: upload video, auto-label with SAM 3, review annotations, and train YOLO models.

This walkthrough uses a real dashcam clip (`Target.mp4` — 4K HEVC, 6 seconds) to demonstrate the full pipeline.

---

## 1. Upload Video

Navigate to the Upload page. Drag and drop a video file or click "Choose File" to browse.

![Upload Page](screenshots/01-upload-page.png)

Waldo accepts any video format ffmpeg can decode (MP4, AVI, MOV, MKV, etc.). The video is uploaded to MinIO object storage and metadata (fps, duration, resolution) is extracted via ffprobe.

---

## 2. Label with Text Search

After upload, you're taken to the Label page. Type a natural language description of what you're looking for and click **Search**.

![Label Page](screenshots/02-label-page.png)

Here we search for **"person"** using the default **Segmentation** task type:

![Search Filled](screenshots/03-label-search-filled.png)

The job enters the pipeline: extracting frames, running SAM 3 inference, converting masks to YOLO format.

![In Progress](screenshots/04-label-in-progress.png)

When complete, you get buttons to **Review Results** or **Download Dataset**:

![Completed](screenshots/05-label-completed.png)

SAM 3 extracted 3 unique frames (deduplication via perceptual hashing) and processed all of them. The YOLO dataset is available as a zip download.

---

## 3. Review Annotations

Click **Review Results** to see every annotation overlaid on the original frames. The green polygons show SAM 3's segmentation masks for each detected person.

![Review Page](screenshots/06-review-page.png)

**Key features:**
- **Accept/Reject buttons** per annotation — curate your training data
- **Dataset Stats sidebar** — annotation counts, class breakdown, review status, density
- **Train Model button** — jump straight to training when satisfied
- **Download link** — grab the YOLO dataset zip anytime

In this example, SAM 3 found **17 person instances** across 3 frames from the dashcam footage — people walking in a parking lot captured in various positions and distances.

---

## 4. Click Mode (Exemplar Labeling)

When text search doesn't find what you need, switch to **Click Mode**. This shows the extracted frames as a grid — click one to enter annotation mode.

![Click Mode](screenshots/08-click-mode.png)

- **Left-click** on the target object = positive prompt (green dot)
- **Right-click** on background = negative prompt (red dot)
- SAM 3's tracker propagates your clicks across all frames automatically

This uses `Sam3TrackerVideoModel` under the hood — a different model path optimized for point-prompt tracking.

---

## 5. Task Types

Waldo supports all five YOLO task types. Select the task before labeling — SAM 3 always outputs masks, and the converter transforms them to the right format:

![Detection Mode](screenshots/10-detection-mode.png)

| Task | What it outputs |
|------|----------------|
| **Segmentation** | Polygon vertices (normalized 0-1) |
| **Detection** | Bounding boxes (cx, cy, w, h) |
| **Classification** | Cropped images sorted into class folders |
| **Oriented BBox** | 4 rotated corner points |
| **Pose** | Bbox + centroid keypoint |

---

## 6. Train YOLO Model

From the Review page, click **Train Model** to configure and launch YOLO training.

![Train Page](screenshots/09-train-page.png)

**Configuration options:**
- **Task Type** — matches your labeling task (Segmentation, Detection, etc.)
- **Model Variant** — yolo11n-seg (nano, fast) through yolo11x-seg (extra-large, accurate)
- **Epochs** — training iterations (default 100, with early stopping)
- **Batch Size** — samples per step (default 8 for Apple Silicon)
- **Image Size** — input resolution (default 640px)

Click **Start Training** to queue a Celery task. Live metrics stream via WebSocket — watch loss and mAP update in real-time.

---

## 7. Jobs Dashboard

The Jobs page shows all labeling jobs with their status and progress.

![Jobs Page](screenshots/07-jobs-page.png)

Click any completed job to jump to its Review page.

---

## Pipeline Summary

```
Upload Video → Extract Frames (ffmpeg + phash dedup)
            → SAM 3 Segmentation (text or click prompts)
            → Convert to YOLO Format (5 task types)
            → Review & Curate (accept/reject)
            → Train YOLO Model (16 variants)
            → Get Notified (Slack/email/ntfy)
            → Download Weights
```

## Running Locally

```bash
make setup           # Install deps
make infra           # Start Postgres + Redis + MinIO
make migrate         # Create tables
make download-models # Get SAM 3 weights

# Three terminals:
make dev-app         # API + UI on :8000
make dev-labeler     # SAM 3 worker
make dev-trainer     # YOLO worker

# Open http://localhost:8000
```
