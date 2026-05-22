#!/bin/sh
# Single entrypoint for the unified Waldo image. Picks the process to run from
# $WALDO_ROLE: app | labeler | trainer. Default is app.
#
# When DEVICE=cuda we additionally call the worker GPU-check helper so
# `docker compose logs` immediately shows whether passthrough is wired up.
set -e

ROLE="${WALDO_ROLE:-app}"

run_worker() {
    queue="$1"
    if [ "${DEVICE:-cpu}" = "cuda" ] && [ -x /app/scripts/entrypoint-worker.sh ]; then
        exec /app/scripts/entrypoint-worker.sh \
            uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo -Q "$queue"
    else
        exec uv run celery -A lib.tasks worker --loglevel=info --concurrency=1 --pool=solo -Q "$queue"
    fi
}

case "$ROLE" in
    app)
        echo "==> Running database migrations..."
        uv run alembic upgrade head
        echo "==> Starting Waldo API..."
        exec uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
        ;;
    labeler)
        # Celery's default queue name is "celery" — that's what label_video uses.
        run_worker celery
        ;;
    trainer)
        run_worker training
        ;;
    *)
        echo "Unknown WALDO_ROLE: $ROLE (expected app | labeler | trainer)" >&2
        exit 1
        ;;
esac
