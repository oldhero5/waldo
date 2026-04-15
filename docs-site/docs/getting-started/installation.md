---
title: Installation
sidebar_position: 1
---

# Installation

Waldo is designed to run inside Docker. The instructions below work identically on Linux, macOS, and Windows — the only difference is which container runtime you use.

## Prerequisites

| Platform | Requirement |
| --- | --- |
| Linux | Docker Engine 24+, Docker Compose v2 |
| macOS (Intel / Apple Silicon) | Docker Desktop 4.30+, or [OrbStack](https://orbstack.dev/) |
| Windows | Docker Desktop 4.30+ with WSL 2 backend |
| GPU (optional) | NVIDIA Container Toolkit (Linux/Windows) |

For Apple Silicon, Waldo uses MLX for SAM 3.1 inference natively — no extra setup beyond Docker for the API services. ML inference workers run *outside* the container on macOS for GPU access (see [development setup](../development/setup)).

## Clone the repo

```bash
git clone https://github.com/your-org/waldo.git
cd waldo
cp .env.example .env
```

Edit `.env` to set passwords and any non-default ports. **Do not commit `.env`** — it's gitignored.

## First boot

```bash
docker compose up -d
```

That starts:

- `postgres` — primary database
- `redis` — Celery broker + cache
- `minio` — S3-compatible object store for videos and frames
- `app` — FastAPI backend on `:8000`
- `labeler` — Celery worker for SAM 3 inference
- `trainer` — Celery worker for YOLO26 training

Wait ~30 seconds for the database to migrate, then visit **<http://localhost:8000>**. On first access, Waldo bootstraps an admin user and prints the password to the container logs:

```bash
docker compose logs app | grep -A 2 "bootstrapped first admin"
```

Save that password; it's the only time it's shown.

## Production setup

For production, set `APP_ENV=production` plus secure values for:

- `JWT_SECRET` (use `openssl rand -hex 32`)
- `POSTGRES_PASSWORD`
- `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY`
- `ADMIN_BOOTSTRAP_PASSWORD` (the random fallback is dev-only)

The app refuses to start if any of these are still on insecure defaults. See [Security](../architecture/security) for the full hardening checklist.
