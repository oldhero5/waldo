---
title: Configuration
sidebar_position: 3
---

# Configuration

All configuration is environment variables, loaded by [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) from `.env` or the process environment. The schema lives in [`lib/config.py`](https://github.com/oldhero5/waldo/blob/main/lib/config.py).

## Core

| Variable | Default | Description |
| --- | --- | --- |
| `APP_ENV` | `development` | Set to `production` to enforce secret hardening. |
| `JWT_SECRET` | dev placeholder | **Must override in prod.** Use `openssl rand -hex 32`. |
| `JWT_EXPIRE_MINUTES` | `1440` | Access token TTL. |

## Database

| Variable | Default |
| --- | --- |
| `POSTGRES_HOST` | `localhost` |
| `POSTGRES_PORT` | `5432` |
| `POSTGRES_USER` | `waldo` |
| `POSTGRES_PASSWORD` | `waldo` (insecure — must override in prod) |
| `POSTGRES_DB` | `waldo` |

## Object storage

| Variable | Default |
| --- | --- |
| `MINIO_ENDPOINT` | `localhost:9000` |
| `MINIO_ACCESS_KEY` | `minioadmin` (insecure — must override in prod) |
| `MINIO_SECRET_KEY` | `minioadmin` (insecure — must override in prod) |
| `MINIO_BUCKET` | `waldo` |
| `MINIO_SECURE` | `false` |

## Models

| Variable | Default |
| --- | --- |
| `SAM3_MODEL_ID` | `facebook/sam3` |
| `SAM3_MLX_MODEL_ID` | `mlx-community/sam3.1-bf16` |
| `HF_TOKEN` | _(empty)_ |
| `DEVICE` | `mps` |
| `DTYPE` | `float32` |
| `AGENT_PROVIDER` | `ollama` |
| `AGENT_MODEL` | `gemma4:e4b` (set for selected provider) |
| `AGENT_ALLOWED_MODELS` | _(empty; configured model only)_ |
| `AGENT_ALLOW_CLOUD_TEXT` | `false` |
| `AGENT_TIMEOUT_SECONDS` | `60` |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY` | _(empty)_ |
| `VLLM_URL` | `http://localhost:8001/v1` |
| `VLLM_API_KEY` | _(empty)_ |
| `OLLAMA_URL` | `http://localhost:11434` |
| `AGENT_TEMPERATURE` | `0.2` |

Compose uses `OLLAMA_COMPOSE_URL` when supplied, otherwise its optional `ollama`
service. Both application and local model download use `AGENT_MODEL`. The
`local-chat` profile controls whether Ollama starts. Settings also offers temporary
workspace overrides; see [Agent configuration](../ui/agent#configuration).

## Notifications (optional)

| Variable | Purpose |
| --- | --- |
| `SLACK_WEBHOOK_URL` | Slack notifications from the trainer |
| `NTFY_TOPIC` / `NTFY_SERVER` | ntfy.sh push notifications |
| `SMTP_HOST` ... `SMTP_FROM` | Email alerts |
| `ALERT_EMAIL` | Default destination for alerts |

## CORS

| Variable | Default |
| --- | --- |
| `CORS_ORIGINS` | `http://localhost:5173,http://localhost:8000` |

Comma-separated list of allowed origins. The middleware only honors this list — there is no `*` fallback.

## Bootstrap admin

| Variable | Purpose |
| --- | --- |
| `ADMIN_BOOTSTRAP_EMAIL` | Email used when seeding the first admin (default `admin@waldo.ai`) |
| `ADMIN_BOOTSTRAP_PASSWORD` | Password for the seed admin (required in production when there are no users; defaults to `waldopass` in development) |
