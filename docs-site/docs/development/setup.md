---
title: Development Setup
sidebar_position: 1
---

# Development Setup

For day-to-day development, run the dependencies in Docker but the API and UI on the host so you get fast reloads.

## macOS (Apple Silicon)

```bash
git clone https://github.com/oldhero5/waldo.git
cd waldo
cp .env.example .env

# Install uv (Python package manager — never use pip directly on this project)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Bring up only the dependency services
docker compose up -d postgres redis minio

# Install Python deps
uv sync

# Run migrations
uv run alembic upgrade head

# Run the API
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# In a second terminal, the labeler worker (uses MLX natively, must be on host)
uv run celery -A lib.tasks worker -Q labeler -l info

# In a third terminal, the UI
cd ui
npm install
npm run dev   # http://localhost:5173
```

## Linux / Windows

Same pattern but the labeler can run inside Docker since you don't need MLX:

```bash
docker compose up -d postgres redis minio
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload
docker compose up -d labeler trainer  # workers in containers
cd ui && npm install && npm run dev
```

## Hot reload

- `uvicorn --reload` watches Python files
- `npm run dev` runs Vite with HMR
- Restart the labeler/trainer manually after Python changes (Celery doesn't hot-reload)

## Testing

```bash
uv run pytest                      # all tests
uv run pytest tests/test_auth.py   # one file
uv run pytest -k labeling          # by keyword
```

## Reset everything

```bash
docker compose down -v             # delete volumes
rm -rf .venv                       # delete Python env
uv sync                            # reinstall
docker compose up -d postgres redis minio
uv run alembic upgrade head
```
