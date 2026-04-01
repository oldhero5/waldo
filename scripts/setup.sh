#!/usr/bin/env bash
set -euo pipefail

echo "==> Starting infrastructure..."
docker compose up -d postgres redis minio minio-init

echo "==> Waiting for services to be healthy..."
for service in postgres redis minio; do
    echo "    Waiting for $service..."
    until docker compose ps "$service" --format json | grep -q '"healthy"'; do
        sleep 1
    done
    echo "    $service is healthy."
done

echo "==> Running database migrations..."
uv run alembic upgrade head

echo "==> Setup complete!"
echo "    - PostgreSQL: localhost:5432"
echo "    - Redis:      localhost:6379"
echo "    - MinIO:      localhost:9000 (console: localhost:9001)"
