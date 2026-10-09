---
title: Local testing on Apple Silicon
sidebar_position: 2
---

# Local testing on Apple Silicon

This checkout can run the API/UI, PostgreSQL, Redis and MinIO in OrbStack while
the labeler, trainer and Ollama run natively on the Mac. Native workers provide
MLX/MPS access; the app container uses CPU. This path builds current source and
uses a private `.waldo-local.env`, separate from the usual `.env`/`make up` path.

The October 7, 2026 test host has **32 GB unified memory**. That is an observed
host configuration, not a minimum requirement or a guarantee that several large
models can run concurrently. The cached `gemma4:12b-mlx` answered locally, and
the app container reached host Ollama. Native SAM also processed a small moving
bus fixture with source PTS; physical-camera accuracy and real GoPro telemetry
have not been qualified by those checks.

## Private configuration

Use the existing ignored `.waldo-local.env` on the test host. For a new checkout,
create it privately from the example settings and set the values below before
starting. Keep credentials out of commands, screenshots and committed files;
restrict the file to the local account with `chmod 600 .waldo-local.env`.

| Setting | Local role |
| --- | --- |
| `WALDO_ENV_FILE=.waldo-local.env` | Selects the app/worker container env file. `--env-file` separately supplies Compose interpolation values. |
| `POSTGRES_HOST=localhost`, `REDIS_URL=redis://localhost:6379/0`, `MINIO_ENDPOINT=localhost:9000` | Native workers reach published infrastructure ports. Compose overrides these to service names inside containers. |
| `POSTGRES_*`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `JWT_SECRET` | Private credentials shared by the appropriate app and workers. Existing volume credentials are not rotated by editing the file. |
| `ADMIN_BOOTSTRAP_EMAIL`, `ADMIN_BOOTSTRAP_PASSWORD` | Initial administrator login for an empty installation. Use these private values in the browser; bootstrap does not change existing users. |
| `DEVICE=mps` | Native worker device; the local app override uses `cpu`. |
| `AGENT_PROVIDER=ollama`, `AGENT_MODEL=gemma4:12b-mlx` | The tested cached local chat model. The tag must exist in the local Ollama installation. |
| `AGENT_ALLOW_CLOUD_TEXT=false` | Keeps the agent's cloud text providers disabled. |
| `OLLAMA_URL=http://localhost:11434` | Native host URL. The local app override uses `http://host.docker.internal:11434`. |
| `WALDO_BIND=127.0.0.1` | API/UI published on loopback. Infrastructure binds also default to loopback. |

Compose interpolation may take values from the shell ahead of the selected env
file; avoid stale exported overrides when checking this configuration.
[Docker environment precedence](https://docs.docker.com/compose/how-tos/environment-variables/variable-interpolation/).

For other providers, configure the selected provider's secret setting privately
(`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `OPENROUTER_API_KEY` or `VLLM_API_KEY`) and
qualify that backend before enabling it. A key alone does not enable cloud text;
provider selection, model policy and cloud permission are separate settings.
These providers were not part of the local Ollama readiness check.

## Start and verify

Run from the repository root with OrbStack running, the locked native environment
available at `.venv`, and native Ollama serving the selected cached model. These
commands do not request a model download or a dependency upgrade.

```bash
docker compose --env-file .waldo-local.env \
  -f docker-compose.yml -f docker-compose.local.yml \
  up -d --build waldo-app
.venv/bin/python scripts/local_workers.py start
.venv/bin/python scripts/local_workers.py status
curl --fail http://127.0.0.1:8000/health
curl --fail http://127.0.0.1:11434/api/tags
```

Open [Waldo](http://127.0.0.1:8000), sign in with the private bootstrap credentials
for this installation, and select the intended workspace. The worker helper
starts one `celery`-queue labeler/inference worker and one `training`-queue worker,
both with concurrency one. Its status check verifies the recorded process
identity; it does not prove model readiness. Logs are in
`.waldo-local/labeler.log` and `.waldo-local/trainer.log`.

The local override builds `waldo:local` from `Dockerfile`, routes chat to native
Ollama and pins the local MinIO/`mc` images from the official Quay namespace.
It leaves the base deployment image choices unchanged. App startup runs Alembic
migrations against the configured database, and Compose reuses its named volumes.
Use the intended test installation and review
[legacy ownership recovery](../architecture/security#upgrading-legacy-ownership)
when upgrading existing data.

`/health` establishes app liveness. Ollama's
[`/api/tags`](https://docs.ollama.com/api/tags) lists available models; a successful
completion checks the selected model. An actual small inference request checks
the native worker and perception weights. These are separate gates. SAM image
and video endpoints dispatch to the native worker and wait at most 300 seconds;
a busy or unavailable queue can yield HTTP 503. Input files move through MinIO,
then workers download to their own temporary directories and remove the
ephemeral input after saving a terminal result. Completed video frames and
comparison failures/results remain available through owned polling routes for
24 hours, so missed WebSocket notifications do not leave the UI running. A
redelivered task returns that saved result rather than fetching a deleted input.
Queued inference messages expire after 24 hours. The app configures a MinIO
lifecycle rule restricted to `inference/`: orphaned inputs and old object versions
become eligible for expiration after two days; actual removal follows the storage
lifecycle scanner. The code also requests a two-day incomplete-multipart abort
rule, but the tested local MinIO release did not retain that field in its returned
policy. Multipart cleanup is therefore not verified on this installation and
needs separate operational qualification. The storage credential
needs lifecycle read/write permission, or that matching policy must already be
configured with read permission. Other source/training objects retain their
existing policy. App and Mac temporary paths are not shared.

Transient Redis errors trigger up to three bounded Celery retries. Polling also
checks the server-recorded Celery task ID while work is running, so exhausted
retries or revocation can become a failed result after the result store recovers.
Worker success logs suppress result bodies, including preview thumbnails.

The agent's hardware information is scoped to the API process. Its GPU/device
checks cannot establish the native worker or Ollama host's hardware; worker
hardware remains unreported by that tool. Chat provider/model information uses
the selected workspace's configuration, including runtime overrides.

## Apply source or configuration changes

Rebuild the app and restart native workers so both load the same task contract.
The helper's stop operation requests a warm shutdown; if it reports active work
still finishing, wait for it to stop before starting replacements.

```bash
.venv/bin/python scripts/local_workers.py stop
.venv/bin/python scripts/local_workers.py status
docker compose --env-file .waldo-local.env \
  -f docker-compose.yml -f docker-compose.local.yml \
  up -d --build waldo-app
.venv/bin/python scripts/local_workers.py start
```

The helper targets this checkout's recorded PID, creation time, working directory
and worker hostname. It does not stop unrelated Celery processes. `.waldo-local`
state/logs and `.waldo-local.env` are ignored by Git.

For shutdown, stop those workers and then stop this Compose stack without
removing volumes:

```bash
.venv/bin/python scripts/local_workers.py stop
docker compose --env-file .waldo-local.env \
  -f docker-compose.yml -f docker-compose.local.yml down
```

The existing CPU Docker build still includes substantial CUDA-related Python
dependencies. Reducing that image is a follow-up; native execution does not
eliminate the current build size. This setup does not implement geolocation or
qualify a production perception backend.
