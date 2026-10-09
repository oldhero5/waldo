---
title: Testing
sidebar_position: 3
---

# Testing

## Python

Tests live under `tests/` and run via `pytest`. We use `pytest-asyncio` for async fixtures.

```bash
uv run pytest                       # all
uv run pytest -x                    # stop on first failure
uv run pytest -k auth               # by keyword
uv run pytest --cov=lib --cov=app   # with coverage (install pytest-cov first)
```

### Disposable service tests

Offline regressions use controlled fixtures and SQLite for many ownership paths.
Migration and service tests require an explicitly selected disposable PostgreSQL
database; ordinary `pytest` never migrates the application's default database.
API fixtures register real test users/workspaces and send JWTs. There is no global
authentication bypass.

```bash
# Point POSTGRES_* at an isolated disposable database, then migrate it.
POSTGRES_DB=waldo_test uv run alembic upgrade head
# Set this URL to the same disposable database, using its actual credentials.
WALDO_TEST_POSTGRES_URL=postgresql://waldo:password@localhost:5432/waldo_test uv run pytest
```

The database must already exist. Setting the URL explicitly enables tests that
create users, workspaces and records there; discard the database afterward.
Set `WALDO_SERVICE_STACK=1` only when the configured Redis and MinIO are also
disposable test services. Worker-dependent API cases require a labeling worker
and skip if it never processes the job. Full external-server tests additionally
require `WALDO_E2E=1` and register their own accounts. None of these flags is a
substitute for real model/GPU qualification.

## UI

The deterministic browser suite uses mocked API responses and the production UI:

```bash
cd ui
npm ci --legacy-peer-deps
npx playwright install chromium
npm run lint
npm run build
npx playwright test -c playwright.smoke.config.ts
```

Set `WALDO_CHROMIUM_PATH` to use an existing Chromium executable. The smoke
configuration starts a local preview on port 4173 and covers identity isolation,
preview provenance, provider settings, partial coverage, training readiness and
selected UI regressions. It does not exercise backend services or model inference.
The older default Playwright suite targets a running app at
`http://localhost:8000` and still needs separate live-flow qualification.

## Pre-commit as a test gate

Many small bugs are caught before tests run by the pre-commit hooks (lint, format, type-check). Treat hook failures as test failures.
