# Waldo

Auto-label any object in video using text prompts or click-based exemplars. Powered by SAM 3 (`facebook/sam3`). Train YOLO models on the labeled data, get notified when done, export weights for deployment.

## Architecture

```
                    ┌──────────────────────────────────────────┐
                    │              React UI (SPA)              │
                    └──────────────┬───────────────────────────┘
                                   │
                    ┌──────────────▼───────────────────────────┐
                    │          FastAPI  (waldo-app)             │
                    │   REST API  ·  WebSocket  ·  Static UI   │
                    └──┬───────────────┬───────────────┬───────┘
                       │               │               │
              ┌────────▼──┐    ┌───────▼───┐    ┌──────▼──────┐
              │  Celery    │    │  Celery    │    │             │
              │  Labeler   │    │  Trainer   │    │   Infra     │
              │  (SAM 3)   │    │  (YOLO)    │    │             │
              └────────────┘    └───────────┘    │ PostgreSQL  │
                                                  │ Redis       │
                                                  │ MinIO       │
                                                  └─────────────┘
```

**Full Pipeline:** Upload video → Label (text or click) → Review → Train YOLO → Get notified → Download weights

## Quickstart

### Prerequisites
- Docker (OrbStack or Docker Desktop)
- Node.js 20+ and [uv](https://docs.astral.sh/uv/) (for building UI and local dev)
- Hugging Face token (for SAM 3 model download)

### Run (one command)

```bash
cp .env.example .env       # Add your HF_TOKEN
make up                    # Builds UI, builds containers, starts everything
```

That's it. Open `http://localhost:8000`. The app automatically runs database migrations on startup.

Containers started:
- **waldo-app** — FastAPI + React UI (port 8000)
- **waldo-labeler** — SAM 3 Celery worker
- **waldo-trainer** — YOLO Celery worker
- **postgres** — Database
- **redis** — Task broker + metrics pub/sub
- **minio** — Object storage (console at port 9001)

```bash
make logs              # Tail all container logs
make down              # Stop everything
```

### NVIDIA GPU

```bash
make up PROFILE=nvidia
```

### Apple Silicon GPU (MPS)

Apple's MPS cannot be passed through to Docker containers (Docker runs a Linux VM).
`make up` runs workers on CPU which works but is slower. For MPS acceleration:

```bash
make up                 # Everything in Docker (CPU workers)
# OR
make up-gpu             # Infra + app in Docker, native GPU workers
```

`make up-gpu` starts infra + app in Docker, then launches labeler and trainer
natively in the background with MPS access. One command, zero extra terminals.

## Web UI

Served at `http://localhost:8000/` as static files from FastAPI.

| Page | Path | Description |
|------|------|-------------|
| Upload | `/upload` | Drag-and-drop video upload |
| Label | `/label/:videoId` | Text search + click mode, 5 task types |
| Review | `/review/:jobId` | Annotation grid, accept/reject, stats sidebar |
| Train | `/train/:jobId` | Model variant picker, hyperparameters, live metrics |
| Jobs | `/jobs` | All labeling jobs with status and progress |

```bash
make dev-ui    # Vite dev server with hot reload (port 5173, proxies API)
make build-ui  # Production build → app/static/
```

## API Reference

### Labeling

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/upload` | Upload video (multipart form) |
| `POST` | `/api/v1/label` | Start text-prompt labeling |
| `POST` | `/api/v1/label/exemplar` | Start click-based labeling |
| `POST` | `/api/v1/label/segment-points` | Interactive SAM3 segmentation from clicks |
| `POST` | `/api/v1/upload/images` | Upload standalone images to collection |
| `POST` | `/api/v1/link-videos` | Link videos from another collection |
| `GET` | `/api/v1/status/{job_id}` | Get job status + result download URL |
| `GET` | `/api/v1/status` | List all jobs |

### Review & Annotations

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/jobs/{job_id}/annotations` | List annotations (filter: `?status=pending`) |
| `POST` | `/api/v1/annotations` | Create new annotation |
| `PATCH` | `/api/v1/annotations/{id}` | Accept/reject/edit annotation |
| `DELETE` | `/api/v1/jobs/{id}` | Delete dataset and annotations |
| `GET` | `/api/v1/jobs/{job_id}/overview` | Rich dataset overview with thumbnails |
| `GET` | `/api/v1/jobs/{job_id}/stats` | Dataset statistics (counts, density, class breakdown) |
| `GET` | `/api/v1/videos/{video_id}/frames` | List frames with thumbnail URLs |
| `GET` | `/api/v1/frames/{frame_id}` | Frame detail with all annotations |

### Training

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/train` | Start YOLO training run |
| `GET` | `/api/v1/train/{run_id}` | Get training status + metrics + loss history |
| `GET` | `/api/v1/train` | List all training runs |
| `POST` | `/api/v1/train/{run_id}/stop` | Request early stop |
| `DELETE` | `/api/v1/train/{run_id}` | Delete experiment + model |
| `GET` | `/api/v1/train/variants` | Available model variants + default hyperparams |
| `GET` | `/api/v1/models` | List trained models in registry |
| `POST` | `/api/v1/models/{id}/activate` | Activate model for inference |
| `POST` | `/api/v1/models/{id}/export` | Export model (ONNX, TFLite, CoreML, etc.) |
| `WS` | `/ws/training/{run_id}` | Real-time training metrics via WebSocket |

### Feedback

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/v1/feedback` | Submit false positive feedback |
| `POST` | `/api/v1/feedback/batch` | Batch submit feedback |
| `GET` | `/api/v1/feedback` | List feedback entries |

### Example: Full Pipeline

```bash
# 1. Upload video
curl -X POST http://localhost:8000/api/v1/upload -F "file=@dashcam.mp4"
# → {"video_id": "abc-123", ...}

# 2. Label with text prompt
curl -X POST http://localhost:8000/api/v1/label \
  -H "Content-Type: application/json" \
  -d '{"video_id": "abc-123", "text_prompt": "car", "task_type": "segment"}'
# → {"job_id": "def-456", "status": "pending"}

# 3. Poll until done
curl http://localhost:8000/api/v1/status/def-456
# → {"status": "completed", "result_url": "...", ...}

# 4. Train YOLO on the labeled data
curl -X POST http://localhost:8000/api/v1/train \
  -H "Content-Type: application/json" \
  -d '{"job_id": "def-456", "model_variant": "yolo11m-seg", "hyperparameters": {"epochs": 50}}'
# → {"run_id": "ghi-789", "status": "queued"}

# 5. Monitor training (or use WebSocket for live metrics)
curl http://localhost:8000/api/v1/train/ghi-789
# → {"status": "training", "epoch_current": 23, "metrics": {"mAP50": 0.82, ...}}

# 6. Download trained weights
curl http://localhost:8000/api/v1/models
# → [{"weights_url": "...", "metrics": {...}, ...}]
```

## YOLO Task Types

SAM 3 always outputs masks. Converters transform them into the format each YOLO task needs:

| Task | Output Format | YOLO Variants |
|------|---------------|---------------|
| Segmentation | Polygon vertices (normalized) | yolo11n-seg → yolo11x-seg |
| Detection | Bounding boxes (cx, cy, w, h) | yolo11n → yolo11x |
| Classification | Cropped images in class dirs | yolo11n-cls → yolo11x-cls |
| OBB | 4 rotated corner points | yolo11n-obb → yolo11m-obb |
| Pose | Bbox + centroid keypoint | yolo11n-pose → yolo11m-pose |

## Configuration

All configuration via environment variables. See `.env.example`.

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVICE` | `mps` | Compute device: `mps`, `cuda`, `cpu` |
| `DTYPE` | `float32` | Model dtype: `float32`, `bfloat16` |
| `SAM3_MODEL_ID` | `facebook/sam3` | HuggingFace model ID |
| `HF_TOKEN` | — | Hugging Face access token |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook for training alerts |
| `NTFY_TOPIC` | — | ntfy.sh topic for push notifications |
| `ALERT_EMAIL` | — | Email for training completion alerts |

## Database Schema

| Table | Purpose |
|-------|---------|
| `projects` | Group videos |
| `videos` | Video metadata (fps, duration, resolution) |
| `frames` | Extracted frames with phash dedup |
| `labeling_jobs` | Labeling job tracking (text/exemplar, task type) |
| `annotations` | Per-instance labels (polygon, bbox, confidence, status) |
| `training_runs` | YOLO training lifecycle (epochs, metrics, weights) |
| `model_registry` | Trained model versions with export formats |

## Testing

```bash
# Unit tests (no infra needed)
uv run pytest tests/test_converters.py tests/test_frame_extractor.py -v

# Trainer module tests (requires Redis + MinIO)
uv run pytest tests/test_trainer.py -v

# API tests (requires infra + app running)
uv run pytest tests/test_api.py tests/test_api_extended.py -v

# E2E pipeline test (requires full stack + SAM 3 model)
uv run pytest tests/test_e2e.py -v

# All Python tests
uv run pytest -v

# Playwright browser tests (requires full stack + UI built)
cd ui && npx playwright test

# Everything
make test && cd ui && npx playwright test
```

**Test counts:** 54 Python (pytest) + 25 Playwright (browser + API) = 79 total

## Makefile Targets

| Target | Description |
|--------|-------------|
| **`make up`** | **Build UI + start everything in Docker (one command)** |
| `make down` | Stop all containers |
| `make logs` | Tail all container logs |
| `make setup` | Install Python + Node deps locally |
| `make dev-app` | FastAPI dev server (native, port 8000) |
| `make dev-labeler` | SAM 3 Celery worker (native, MPS) |
| `make dev-trainer` | YOLO Celery worker (native, MPS) |
| `make dev-ui` | Vite dev server (port 5173, hot reload) |
| `make build-ui` | Build React UI to `app/static/` |
| `make migrate` | Run Alembic database migrations |
| `make test` | Run Python test suite |
| `make test-browser` | Run Playwright browser tests |
| `make download-models` | Download SAM 3 model |

## Project Structure

```
waldo/
├── app/                    # FastAPI application
│   ├── main.py             # Entrypoint, routers, SPA fallback
│   ├── ws.py               # WebSocket for live training metrics
│   ├── static/             # Built React UI
│   └── api/
│       ├── upload.py       # Video upload
│       ├── label.py        # Text + exemplar labeling
│       ├── status.py       # Job polling
│       ├── review.py       # Annotation CRUD + stats
│       ├── frames.py       # Frame listing + detail
│       └── train.py        # Training runs + model registry + export
├── labeler/                # SAM 3 labeling pipeline
│   ├── sam3_engine.py      # Text (Sam3VideoModel) + click (Sam3TrackerVideoModel)
│   ├── text_labeler.py     # Text-prompt pipeline
│   ├── exemplar_labeler.py # Click/point-prompt pipeline
│   ├── pipeline.py         # Shared conversion + packaging
│   ├── frame_extractor.py  # ffmpeg extraction + phash dedup
│   └── converters/         # Mask → YOLO format
│       ├── common.py       # Shared dataset utilities
│       ├── to_segment.py   # Polygons
│       ├── to_detect.py    # Bounding boxes
│       ├── to_classify.py  # Class-directory crops
│       ├── to_obb.py       # Oriented bounding boxes
│       └── to_pose.py      # Centroid keypoints
├── trainer/                # YOLO training pipeline
│   ├── train_manager.py    # Ultralytics training orchestrator
│   ├── dataset_builder.py  # Dataset preparation from labeling jobs
│   ├── metrics_streamer.py # Redis pub/sub for live metrics
│   ├── exporter.py         # Model export (ONNX, TFLite, CoreML, etc.)
│   └── notifiers.py        # Slack, ntfy.sh, email alerts
├── lib/                    # Shared library
│   ├── config.py           # Pydantic settings
│   ├── db.py               # SQLAlchemy models (7 tables)
│   ├── storage.py          # MinIO client
│   └── tasks.py            # Celery tasks (label, train, export)
├── ui/                     # React 19 + Vite + TypeScript + Tailwind
│   ├── src/
│   │   ├── api.ts          # Typed API client
│   │   ├── pages/          # Upload, Label, Review, Train, Jobs
│   │   └── components/     # Nav, TaskSelector, AnnotationOverlay, ClickCanvas, StatsPanel
│   └── e2e/                # Playwright browser tests
├── alembic/                # Database migrations (3 versions)
├── scripts/                # Setup + model download scripts
├── tests/                  # Python test suite (54 tests)
└── docker-compose.yml      # 10 services (3 infra + 7 app profiles)
```
