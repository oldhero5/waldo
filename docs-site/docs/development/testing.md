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

### Test database

Integration tests hit a real Postgres — do **not** mock the DB at the SQLAlchemy boundary. We've been burned by mock/prod divergence on migrations. Spin up a disposable test DB:

```bash
docker compose up -d postgres
POSTGRES_DB=waldo_test uv run alembic upgrade head
POSTGRES_DB=waldo_test uv run pytest
```

## UI

The UI uses Playwright for end-to-end tests:

```bash
cd ui
npm install
npx playwright install chromium
npm run test
```

Tests live in `ui/tests/` and target a running dev server (`npm run dev` in another terminal).

## Pre-commit as a test gate

Many small bugs are caught before tests run by the pre-commit hooks (lint, format, type-check). Treat hook failures as test failures.
