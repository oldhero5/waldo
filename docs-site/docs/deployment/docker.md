---
title: Docker (all platforms)
sidebar_position: 1
---

# Docker Deployment

Docker Compose runs infrastructure and the API. Linux workers also run in containers; `make up` on macOS starts native workers for Apple MLX/MPS access.

## Image tags

Compose references Docker Hub images at [`oldhero5/waldo`](https://hub.docker.com/r/oldhero5/waldo). Each image supports three roles (app, labeler, trainer), selected via `WALDO_ROLE`.

| Tag | Base | When to use |
| --- | --- | --- |
| `oldhero5/waldo:latest` | `python:3.11-slim` | App container and Linux/CPU workers |
| `oldhero5/waldo:cuda` | `nvidia/cuda:13.0.3-runtime-ubuntu24.04`; torch/torchvision installed from `cu130` wheel index | NVIDIA GPU labeler / trainer |

The CUDA runtime and explicit torch wheel source both use the CUDA 13.0 family; torch/torchvision versions are pinned. The image and host driver still need qualification on the target GPU before release. The base tag is listed in [NVIDIA supported tags](https://gitlab.com/nvidia/container-images/cuda/-/blob/master/doc/supported-tags.md).

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

On Linux, override detection with `make up PROFILE=nvidia` (or `cpu`). On macOS, `make up` selects `up-mac` regardless of profile. These startup paths pull images; see the build override below for local image builds.

Labeling dispatch selects native MLX only when the host/device supports it; project shape no longer selects the backend. Darwin dependency markers exclude `mlx` and `mlx-vlm` from Linux installs.

## docker-compose.yml

The Compose file brings up infrastructure and the selected worker profile. Add
`--profile local-chat` to include Ollama. `make up` retains local chat by default;
`make up CHAT_PROFILE=` omits it. `AGENT_MODEL` is the canonical model setting; the
old `WALDO_AGENT_MODEL` value is no longer used by the model download container.
The API does not wait for Ollama, and liveness does not imply model readiness.

For an external Ollama server inside Compose, set `OLLAMA_COMPOSE_URL`. Service
credentials use the same `POSTGRES_*` and `MINIO_*` variables as the application.
Changing these values does not rotate credentials in an existing database volume.

Basic commands:

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
| `ollama` (`local-chat`) | 11434 | `ollama/ollama` | `ollama list` |

Postgres, Redis, Ollama and MinIO are bound to `127.0.0.1` by default — the dev-default `minioadmin/minioadmin` credentials should never be reachable from the LAN. Open the [console](http://127.0.0.1:9001) or [S3 API](http://127.0.0.1:9000) locally. Set `MINIO_BIND=0.0.0.0` in `.env` if you need to reach it from another machine on your LAN.

## Profiles

The compose file uses Docker Compose profiles to route services to the right hardware. `make up` auto-detects the right one; you can also pick one explicitly:

```bash
docker compose --profile nvidia up -d   # Linux/WSL + NVIDIA GPU (pulls :cuda)
docker compose --profile cpu    up -d   # Linux/WSL without a GPU (pulls :latest)
make up                               # macOS: containers + native workers
```

The `apple` and `cpu` Compose profiles activate the same Linux worker containers. Selecting `apple` directly does not enable MLX/MPS; use `make up` on macOS for native workers.

## Volumes

| Volume | Purpose |
| --- | --- |
| `pgdata` | Database |
| `miniodata` | Object store |
| `model_cache` | HuggingFace model cache shared across workers |
| `ollama_data` | Sidecar LLM weights for the in-app agent |

Back the database up with:

```bash
docker compose exec -T postgres pg_dump -U waldo -d waldo -Fc > waldo-db.dump
```

## Updating

```bash
git pull
docker compose --profile <cpu|nvidia|apple> pull
docker compose --profile <cpu|nvidia|apple> up -d
```

Migrations run automatically on `waldo-app` startup via Alembic. Before upgrading existing data, read [legacy ownership recovery](../architecture/security#upgrading-legacy-ownership). There is no separate `--build` step — the image is published.

## Image internals

Both `Dockerfile` and `Dockerfile.cuda` use a multi-stage layout:

1. **`ui-builder`** (node:20-alpine) — `npm ci` + `npm run build`, dropping the SPA into `/app/static`.
2. **`runtime`** (slim or nvidia/cuda) — installs ffmpeg and Python dependencies, copies source trees and the built UI, and installs the project into the venv. The CPU image uses frozen `uv sync`; the CUDA image separately installs torch/torchvision from the `cu130` index and finishes with `uv pip install --no-deps -e .`.

The `WALDO_ROLE` environment variable picks which process runs at startup:

```sh
WALDO_ROLE=app       # uv run alembic upgrade head && uvicorn app.main:app
WALDO_ROLE=labeler   # celery -A lib.tasks worker -Q celery
WALDO_ROLE=trainer   # celery -A lib.tasks worker -Q training
```

For CUDA builds, the worker entrypoint additionally prints `nvidia-smi` and `torch.cuda.is_available()` at boot, so `docker compose logs waldo-labeler-nvidia` immediately tells you whether passthrough is wired up.
