---
title: Architecture Overview
sidebar_position: 1
---

# Architecture Overview

Waldo's API and workers share a Postgres database, Redis broker, and MinIO object store. Linux workers run in containers; `make up` on macOS runs labeler and trainer workers natively for MLX/MPS access.

```
                    ┌─────────────────┐
                    │     Browser     │
                    └────────┬────────┘
                             │  HTTPS
                    ┌────────▼────────┐
                    │   FastAPI app   │  ── REST + WebSocket
                    │  (uvicorn :8000)│
                    └─┬──────┬──────┬─┘
              writes  │      │      │ enqueues
                ┌─────▼──┐   │      │
                │Postgres│   │   ┌──▼────┐
                └────────┘   │   │ Redis │
                             │   └──┬────┘
                       reads │      │ Celery tasks
                ┌────────────▼┐  ┌──▼────────────┐
                │    MinIO    │◄─┤  labeler /    │
                │ (S3 store)  │  │  trainer      │
                └─────────────┘  │  workers      │
                                 └───────────────┘
```

## Services

| Service | Image | Purpose |
| --- | --- | --- |
| `app` | `python:3.11-slim` + uv | FastAPI HTTP/WebSocket API. Stateless. |
| `labeler` | CUDA/CPU container on Linux; native process on macOS | Celery worker running SAM 3 / SAM 3.1 inference. |
| `trainer` | `nvidia/cuda` (GPU) or local | Celery worker running YOLO26 training. |
| `postgres` | `postgres:16-alpine` | Primary store for users, projects, jobs, annotations, models. |
| `redis` | `redis:7-alpine` | Celery broker + WebSocket pubsub + ephemeral cache. |
| `minio` | `minio/minio` | S3-compatible blob storage for videos, frames, and exported datasets. |

## Request flow: auto-labeling

1. **Upload** — the browser POSTs a video to `/api/v1/upload`. The app stores it in MinIO and inserts a `Video` row.
2. **Labeling job** — `POST /api/v1/label` creates a `LabelingJob` row and enqueues inference.
3. **Processing** — the PyTorch path extracts sampled frames with FFmpeg; the native MLX path reads sampled video frames directly. Current task dispatch selects MLX for project jobs without checking platform, so Linux collection labeling needs correction.
4. **Streaming** — the labeler publishes detections to a Redis pubsub channel as it goes. The app forwards them over WebSocket so the UI updates live.
5. **Review** — the user opens `/review/<job>`. Annotations are loaded from Postgres, edits PATCH back to the API.
6. **Export** — clicking "Export" generates a YOLO-format dataset (images + label txt files) into MinIO. The download endpoint streams it back to the browser.

## Tech choices

| Concern | Choice | Why |
| --- | --- | --- |
| API | FastAPI | Async, type-checked, generates OpenAPI for free |
| ORM | SQLAlchemy 2.x | Mature, async-friendly, alembic migrations |
| Task queue | Celery + Redis | Battle-tested for long-running ML jobs |
| Object store | MinIO | S3-compatible, runs anywhere, no vendor lock-in |
| Detection model | YOLO26 (Ultralytics) | Fast, accurate, easy to fine-tune |
| Segmentation model | SAM 3 / SAM 3.1 MLX | PyTorch video sessions or MLX image detection with IoU tracking, depending on path |
| UI | React + Vite + Tailwind | Fast HMR, modern hooks, zero config |
| Auth | JWT bearer + API keys | User authentication; workspace authorization remains incomplete |

See [Data Model](./data-model) for the schema and [Security](./security) for the trust model.
