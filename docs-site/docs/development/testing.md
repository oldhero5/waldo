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
disposable test services. These tests create users, jobs and artifacts. Do not use
the running application's database, queue or bucket.

### Queued worker integration

The required CI job sets `WALDO_WORKER_INTEGRATION=1`. The fixture starts a
separate Celery process for each case, with a unique queue. It replaces only the
inference engine in that process. Synthetic video still passes through the real
API routes, Redis, video decoding, PostgreSQL, MinIO, review edits and export.
The API client runs in-process through ASGI; this suite does not test HTTP transport.

On a Linux test host, first configure and migrate the disposable services as
above. Then run:

```bash
WALDO_SERVICE_STACK=1 WALDO_WORKER_INTEGRATION=1 \
  uv run pytest tests/test_worker_integration.py tests/test_api_extended.py -v
```

Keep `WALDO_TEST_POSTGRES_URL` set to the same database as `POSTGRES_*`.
Once worker integration is enabled, missing service settings and unfinished jobs
fail the test. The suite checks edited geometry, source identities, exported
pixels, empty results, inference errors, bounded timeouts, partial-job retry,
and graceful worker restart with completed-job redelivery. It does not prove
general crash recovery or exactly-once execution.

Use a Linux container for this deterministic suite on a Mac. The production Mac
task can select the native MLX path, which is outside the controlled text-engine
fixture. The fixture stops its worker and removes its queue. Discard the test
database, Redis data and MinIO bucket after the run. Do not mount private footage
or local environment files into the test container.

### Live hardware workflow

The live suites require a trusted local API and real workers connected to an
isolated test stack. Set an explicit HTTP loopback URL. There is no default URL
that can silently target the user's application.

```bash
WALDO_E2E=1 WALDO_E2E_BASE_URL=http://127.0.0.1:18000 \
  WALDO_E2E_SOURCE_A=/absolute/path/to/positive-a.mp4 \
  WALDO_E2E_PROMPT=car uv run pytest tests/test_e2e.py -v
```

Choose a clip with an object that the installed model can detect for the prompt.
The test labels it, accepts a result, requests an export, and inspects the archive.
The full suite also trains, activates and uses a model:

```bash
WALDO_E2E=1 WALDO_E2E_BASE_URL=http://127.0.0.1:18000 \
  WALDO_E2E_SOURCE_A=/absolute/path/to/positive-a.mp4 \
  WALDO_E2E_SOURCE_B=/absolute/path/to/positive-b.mp4 \
  WALDO_E2E_PROMPT=car uv run pytest tests/test_e2e_full.py -v
```

Both full-suite clips must produce positive observations. Their paths and content
must differ. The export must keep each source in one split and provide both
training and validation groups. Distinct clips alone do not establish independent
scenes or a holdout; this is a functional check, not an accuracy experiment.
Without `WALDO_E2E=1`, the two live tests skip. With it enabled, missing inputs,
failed jobs and worker deadlines fail. Keep private media and reports local.

Record code SHA, hardware, checkpoint, prompt, threshold, sampling rate, elapsed
time and memory for hardware qualification. Report it separately from the
deterministic CI result. Neither suite establishes model accuracy by itself.

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
