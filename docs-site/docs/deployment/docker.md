---
title: Docker (all platforms)
sidebar_position: 1
---

# Docker Deployment

Docker is the **only** supported runtime for Waldo. Everything else in this section is a flavor of Docker on a specific OS.

## Two published images

Waldo is published to Docker Hub at [`oldhero5/waldo`](https://hub.docker.com/r/oldhero5/waldo). One image runs all three roles (app, labeler, trainer); the role is selected at runtime via the `WALDO_ROLE` environment variable.

| Tag | Base | When to use |
| --- | --- | --- |
| `oldhero5/waldo:latest` | `python:3.11-slim` | App container, Apple Silicon, Linux/CPU workers |
| `oldhero5/waldo:cuda`   | `nvidia/cuda:12.4.0-devel-ubuntu22.04` + `torch+cu124` | NVIDIA GPU labeler / trainer |

Why two tags and not one? Carrying CUDA into every image would push the CPU/Apple footprint to ~14 GB unnecessarily. Two tags keep the CPU image at ~6.5 GB.

The compose file picks the right tag per service:

- `waldo-app` → `:latest` (no GPU needed for the API tier)
- `waldo-labeler`, `waldo-trainer` (`apple` + `cpu` profiles) → `:latest`
- `waldo-labeler-nvidia`, `waldo-trainer-nvidia` (`nvidia` profile) → `:cuda`

Override either tag with `WALDO_TAG`/`WALDO_CUDA_TAG` if you need to pin a specific version (e.g. `WALDO_TAG=v0.4.1`).

## Quickest start: `make up`

`make up` auto-detects your host OS **and** GPU at make-time, pulls the published image(s), and brings the stack up. The detection lives at the top of the Makefile:

- **macOS (Darwin)** → `apple` profile, runs `make up-mac`: infra + app pulled from Docker Hub, labeler and trainer workers **natively on the host** so they can reach Apple's MPS/MLX (MLX cannot run inside a Linux container). Labeler logs land in `/tmp/waldo-labeler.log`, trainer logs in `/tmp/waldo-trainer.log`.
- **Linux / WSL2 with `nvidia-smi` on `$PATH`** → `nvidia` profile, runs `make up-linux`: pulls `:cuda` and starts the GPU workers.
- **Linux / WSL2 without an NVIDIA GPU** → `cpu` profile, runs `make up-linux` against the CPU image.

Override at any time with `make up PROFILE=nvidia` (or `cpu`, or `apple`). There is no local image build in any of these paths — see the build override below.

The `video_labeler.run_playground` helper and the `label_video` task branch on `platform.system()` at runtime, so the same image ships to both backends. `mlx` and `mlx-vlm` are listed under the `labeler` dependency group with `platform_system=='Darwin'` markers, so the published Linux image skips them automatically.

## docker-compose.yml

The default compose file at the repo root brings up the full stack from the published images:

```bash
docker compose --profile cpu pull          # or: --profile nvidia / --profile apple
docker compose --profile cpu up -d
docker compose ps                          # check service health
docker compose logs -f waldo-app           # tail backend logs
docker compose down                        # stop everything (data persists)
docker compose down -v                     # nuclear: also delete volumes
```

To build the images locally instead of pulling — for contributing, or running an unmerged branch — layer the build override:

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml \
    --profile nvidia up -d --build
# or:
make build PROFILE=nvidia
```

The override builds `Dockerfile` (CPU) and/or `Dockerfile.cuda` (GPU) locally and tags them `oldhero5/waldo:dev` / `:dev-cuda` so they don't shadow the published images.

## Services

| Service | Port | Image | Health endpoint |
| --- | --- | --- | --- |
| `waldo-app` | 8000 | `oldhero5/waldo:latest` | `/health` |
| `waldo-labeler` (`apple` + `cpu`) | — | `oldhero5/waldo:latest` | Celery `ping` |
| `waldo-trainer` (`apple` + `cpu`) | — | `oldhero5/waldo:latest` | Celery `ping` |
| `waldo-labeler-nvidia` (`nvidia`) | — | `oldhero5/waldo:cuda` | Celery `ping` |
| `waldo-trainer-nvidia` (`nvidia`) | — | `oldhero5/waldo:cuda` | Celery `ping` |
| `postgres` | 5432 | `postgres:16-alpine` | internal |
| `redis` | 6379 | `redis:7-alpine` | internal |
| `minio` | 9000 (S3) / 9001 (console) | `minio/minio` | `/minio/health/live` |
| `ollama` | 11434 | `ollama/ollama` | `ollama list` |

## Profiles

The compose file uses Docker Compose profiles to route services to the right hardware. `make up` auto-detects the right one; you can also pick one explicitly:

```bash
docker compose --profile nvidia up -d   # Linux/WSL + NVIDIA GPU (pulls :cuda)
docker compose --profile cpu    up -d   # Linux/WSL without a GPU (pulls :latest)
docker compose --profile apple  up -d   # Apple Silicon (alias of `cpu`, retained for one release)
```

The `apple` and `cpu` profiles activate the same `waldo-labeler` / `waldo-trainer` services — `apple` is kept as an alias so existing `make up PROFILE=apple` invocations don't break. New users on Linux/WSL should prefer `cpu`; the alias will be removed in a future release.

## Volumes

| Volume | Purpose |
| --- | --- |
| `pgdata` | Database |
| `miniodata` | Object store |
| `model_cache` | HuggingFace model cache shared across workers |
| `ollama_data` | Sidecar LLM weights for the in-app agent |

Back the database up with:

```bash
docker run --rm -v waldo_pgdata:/data -v $(pwd):/backup alpine \
    tar czf /backup/db.tgz -C /data .
```

## Updating

```bash
git pull
docker compose --profile <cpu|nvidia|apple> pull
docker compose --profile <cpu|nvidia|apple> up -d
```

Migrations run automatically on `waldo-app` startup via Alembic. There is no separate `--build` step — the image is published.

## Image internals

Both `Dockerfile` and `Dockerfile.cuda` use a multi-stage layout:

1. **`ui-builder`** (node:20-alpine) — `npm ci` + `npm run build`, dropping the SPA into `/app/static`.
2. **`runtime`** (slim or nvidia/cuda) — installs ffmpeg + Python deps via `uv sync`, copies all source trees (`lib/`, `app/`, `labeler/`, `trainer/`, `alembic/`), pulls the built UI from stage 1, and finally runs `uv sync` again to install the project itself into the venv (so `lib`, `app`, etc. are importable).

The `WALDO_ROLE` environment variable picks which process runs at startup:

```sh
WALDO_ROLE=app       # uv run alembic upgrade head && uvicorn app.main:app
WALDO_ROLE=labeler   # celery -A lib.tasks worker -Q celery
WALDO_ROLE=trainer   # celery -A lib.tasks worker -Q training
```

For CUDA builds, the worker entrypoint additionally prints `nvidia-smi` and `torch.cuda.is_available()` at boot, so `docker compose logs waldo-labeler-nvidia` immediately tells you whether passthrough is wired up.
