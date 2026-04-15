---
title: Docker (all platforms)
sidebar_position: 1
---

# Docker Deployment

Docker is the **only** supported runtime for Waldo. Everything else in this section is a flavor of Docker on a specific OS.

## docker-compose.yml

The default compose file at the repo root brings up the full stack:

```bash
docker compose up -d            # start everything in the background
docker compose ps               # check service health
docker compose logs -f app      # tail backend logs
docker compose down             # stop everything (data persists)
docker compose down -v          # nuclear: also delete volumes
```

## Services

| Service | Port | Health endpoint |
| --- | --- | --- |
| `app` | 8000 | `/health` |
| `postgres` | 5432 | internal |
| `redis` | 6379 | internal |
| `minio` | 9000 (S3) / 9001 (console) | `/minio/health/live` |
| `labeler` | — | Celery `ping` |
| `trainer` | — | Celery `ping` |

## Profiles

The compose file uses Docker Compose profiles to support both NVIDIA GPU and Apple Silicon hosts:

```bash
docker compose --profile nvidia up -d   # Linux + NVIDIA GPU
docker compose --profile apple up -d    # Apple Silicon (labeler stays on host for MLX)
```

## Volumes

| Volume | Purpose |
| --- | --- |
| `postgres-data` | Database |
| `minio-data` | Object store |
| `model_cache` | HuggingFace model cache shared across workers |

Back these up with `docker run --rm -v waldo_postgres-data:/data -v $(pwd):/backup alpine tar czf /backup/db.tgz -C /data .`

## Updating

```bash
git pull
docker compose pull
docker compose up -d --build
```

Migrations run automatically on `app` startup via Alembic.
