---
title: Architecture Overview
sidebar_position: 1
---

# Architecture Overview

Waldo is a small constellation of services that share a Postgres database, Redis broker, and MinIO object store. Every service runs in its own container.

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
| `labeler` | `nvidia/cuda` (GPU) or `python:3.11-slim` (Apple) | Celery worker running SAM 3 / SAM 3.1 inference. |
| `trainer` | `nvidia/cuda` (GPU) or local | Celery worker running YOLO26 training. |
| `postgres` | `postgres:16-alpine` | Primary store for users, projects, jobs, annotations, models. |
| `redis` | `redis:7-alpine` | Celery broker + WebSocket pubsub + ephemeral cache. |
| `minio` | `minio/minio` | S3-compatible blob storage for videos, frames, and exported datasets. |

## Request flow: auto-labeling

1. **Upload** — the browser POSTs a video to `/api/v1/upload`. The app stores it in MinIO and inserts a `Video` row.
2. **Frame extraction** — the app dispatches a Celery task to the labeler. FFmpeg extracts frames at a configurable FPS and writes them back to MinIO.
3. **Labeling job** — `POST /api/v1/label` creates a `LabelingJob` row and enqueues SAM 3 inference per frame batch.
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
| Segmentation model | SAM 3 / SAM 3.1 | Best-in-class video segmentation; MLX path on Apple Silicon |
| UI | React + Vite + Tailwind | Fast HMR, modern hooks, zero config |
| Auth | JWT bearer + API keys | Stateless, multi-tenant via workspaces |

See [Data Model](./data-model) for the schema and [Security](./security) for the trust model.
