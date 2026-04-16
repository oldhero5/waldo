# ── OS detection — picks the right backend automatically ────
# macOS (Darwin) → MLX via mlx-vlm, workers run natively so MPS/Metal is reachable.
# Linux / Windows (WSL) → PyTorch SAM 3, workers run inside Docker.
# Override PROFILE manually for nvidia: `make up PROFILE=nvidia`.
UNAME_S := $(shell uname -s)

ifeq ($(UNAME_S),Darwin)
  PROFILE ?= apple
  BACKEND := mlx
else
  PROFILE ?= apple
  BACKEND := pytorch
endif

.PHONY: setup up up-mac up-linux up-gpu down down-gpu logs dev-app dev-labeler dev-trainer dev-ui build-ui migrate test test-browser download-models

# ── Docker (primary) ─────────────────────────────────────────

# `make up` auto-routes: Darwin → up-mac (native MLX workers),
# everything else → up-linux (everything in Docker with PyTorch).
up: build-ui
ifeq ($(BACKEND),mlx)
	@$(MAKE) --no-print-directory up-mac
else
	@$(MAKE) --no-print-directory up-linux
endif

up-linux:
	@echo "==> Linux/Windows path: PyTorch workers in Docker"
	docker compose --profile $(PROFILE) up -d --build
	@echo ""
	@echo "Waldo is running at http://localhost:8000"
	@echo "MinIO console at http://localhost:9001"

up-mac:
	@echo "==> macOS path: infra+app in Docker, MLX workers native"
	docker compose up -d --build
	@docker compose stop waldo-labeler waldo-trainer 2>/dev/null || true
	@-pkill -f "celery.*lib.tasks" 2>/dev/null; sleep 1
	@set -a && . ./.env && set +a && nohup uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo -Q celery > /tmp/waldo-labeler.log 2>&1 & disown
	@set -a && . ./.env && set +a && nohup uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo -Q training > /tmp/waldo-trainer.log 2>&1 & disown
	@echo ""
	@echo "Waldo is running at http://localhost:8000"
	@echo "  Labeler (MLX): logs at /tmp/waldo-labeler.log"
	@echo "  Trainer (MPS): logs at /tmp/waldo-trainer.log"

# Legacy alias — kept so old muscle memory still works.
up-gpu: up-mac

down:
	docker compose --profile $(PROFILE) down
	@-pkill -f "celery.*lib.tasks" 2>/dev/null

logs:
	docker compose --profile $(PROFILE) logs -f

# ── Local dev ────────────────────────────────────────────────

setup:
	uv sync
	cd ui && npm install --legacy-peer-deps
	cp -n .env.example .env || true

dev-app:
	uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

dev-worker:
	uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo -Q celery,training

dev-labeler:
	uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo -Q celery,training

dev-trainer:
	uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo -Q training

dev-ui:
	cd ui && npm run dev

build-ui:
	cd ui && npm run build

# ── Database ─────────────────────────────────────────────────

migrate:
	uv run alembic upgrade head

# ── Testing ──────────────────────────────────────────────────

test:
	uv run pytest -v

test-browser:
	cd ui && npx playwright test

# ── Models ───────────────────────────────────────────────────

download-models:
	bash scripts/download_models.sh

# ── GPU verification (Linux / Windows-via-WSL) ───────────────

# Confirms the NVIDIA driver + container toolkit are wired up on the host.
# Run this BEFORE `make up PROFILE=nvidia` to catch GPU passthrough issues.
gpu-check:
	@echo "==> Host GPU visibility (should show your card):"
	@docker run --rm --gpus all nvidia/cuda:12.4.0-base-ubuntu22.04 nvidia-smi || \
	  (echo "GPU passthrough FAILED. Install nvidia-container-toolkit and restart Docker."; exit 1)
	@echo ""
	@echo "==> Host looks good. Run 'make up PROFILE=nvidia' to start Waldo with GPU workers."

# Stream only the GPU check output from the running worker container.
gpu-logs:
	docker compose --profile nvidia logs waldo-labeler-nvidia waldo-trainer-nvidia 2>&1 | grep -iE "gpu|cuda|nvidia|entrypoint" | head -40
