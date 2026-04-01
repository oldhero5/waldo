#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[dev]${NC} $*"; }
warn() { echo -e "${YELLOW}[dev]${NC} $*"; }
err()  { echo -e "${RED}[dev]${NC} $*" >&2; }

cleanup() {
    log "Shutting down..."
    kill $(jobs -p) 2>/dev/null || true
    wait 2>/dev/null || true
    log "Done."
}
trap cleanup EXIT

# ── Preflight checks ──────────────────────────────────────────

command -v docker >/dev/null || { err "docker not found"; exit 1; }
command -v uv >/dev/null     || { err "uv not found — install with: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
command -v node >/dev/null   || { err "node not found"; exit 1; }

# ── .env ──────────────────────────────────────────────────────

if [ ! -f .env ]; then
    warn ".env not found — copying from .env.example"
    cp .env.example .env
fi
source .env

# ── Python + JS deps ─────────────────────────────────────────

log "Syncing Python deps..."
uv sync --quiet

if [ ! -d ui/node_modules ]; then
    log "Installing UI deps..."
    (cd ui && npm install --legacy-peer-deps --silent)
fi

# ── Infrastructure (Docker) ──────────────────────────────────

log "Starting Postgres, Redis, MinIO..."
docker compose up -d postgres redis minio minio-init

log "Waiting for Postgres..."
until docker compose exec -T postgres pg_isready -U waldo -q 2>/dev/null; do
    sleep 1
done

log "Waiting for MinIO..."
until curl -sf http://localhost:9000/minio/health/live >/dev/null 2>&1; do
    sleep 1
done

# ── Database migration ────────────────────────────────────────

log "Running migrations..."
uv run alembic upgrade head

# ── Build UI (for FastAPI static serving) ─────────────────────

log "Building UI..."
(cd ui && npm run build --silent)

# ── Start services ────────────────────────────────────────────

log "Starting FastAPI app on :8000..."
uv run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &

log "Starting Celery labeler worker (MPS)..."
uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo &

log "Starting Celery trainer worker (MPS)..."
uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo -Q training &

log "Starting UI dev server on :5173..."
(cd ui && npm run dev) &

sleep 2
echo ""
log "================================================"
log "  Waldo is running!"
log "  UI (hot reload): http://localhost:5173"
log "  API:             http://localhost:8000"
log "  MinIO console:   http://localhost:9001"
log "================================================"
echo ""
log "Press Ctrl+C to stop everything."
wait
