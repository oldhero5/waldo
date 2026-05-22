# Unified Waldo image — runs the FastAPI app, Celery labeler, or Celery trainer
# depending on $WALDO_ROLE (app | labeler | trainer; default app).
#
# This is the CPU/Apple variant. NVIDIA users want Dockerfile.cuda, which has
# the same layout but on a CUDA base with GPU-enabled torch.
#
# Built and published as: docker.io/oldhero5/waldo:latest

# ── Stage 1: build the React UI ──────────────────────────────────────
FROM node:20-alpine AS ui-builder
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --legacy-peer-deps --no-audit --no-fund
COPY ui/ ./
# Vite's outDir is ../app/static (relative to the ui/ folder).
RUN mkdir -p /app/static && npm run build

# ── Stage 2: runtime ─────────────────────────────────────────────────
FROM python:3.11-slim AS runtime
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 curl && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies first so the slow torch/transformers/ultralytics
# layer is reusable when only source changes.
COPY pyproject.toml .python-version uv.lock ./
RUN uv sync --frozen --no-dev --group app --group labeler --group trainer --no-install-project

# Source code for all roles.
COPY lib/ lib/
COPY app/ app/
COPY labeler/ labeler/
COPY trainer/ trainer/
COPY alembic.ini ./
COPY alembic/ alembic/
COPY scripts/ scripts/

# Pre-built UI lands in app/static/ (where app/main.py serves it from).
COPY --from=ui-builder /app/static /app/app/static

# Install the project itself into the existing venv so `lib`, `app`, etc. are
# importable from anywhere (alembic env.py, celery boot). Deps are already
# present from the previous layer, so this is fast.
RUN uv sync --frozen --no-dev --group app --group labeler --group trainer

COPY scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh /app/scripts/entrypoint-worker.sh

ENV DEVICE=cpu \
    WALDO_ROLE=app \
    UV_NO_SYNC=1 \
    UV_PROJECT_ENVIRONMENT=/app/.venv

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
