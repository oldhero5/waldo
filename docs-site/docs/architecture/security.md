---
title: Security
sidebar_position: 3
---

# Security

Waldo separates authentication, workspace authorization and installation administration.
The October 2026 cleanup adds shared ownership checks across API resources,
workflows, agent tools, media capabilities and task streams. Focused regressions
exercise these boundaries; this is not a claim of a completed external security audit.

## Authentication

- **JWT bearer tokens** issued by `/api/v1/auth/login`. HS256 signed, 24h default TTL.
- **API keys** (`wld_…` prefix) for programmatic access. Stored as bcrypt hashes.
- **Bootstrap admin** is created on startup when no users exist. Development defaults are `admin@waldo.ai` / `waldopass`; they are fixed credentials, not a random password. Set `ADMIN_BOOTSTRAP_EMAIL` and `ADMIN_BOOTSTRAP_PASSWORD` to override them. Production requires an explicit bootstrap password when creating the first user.

The `JWT_SECRET` MUST be overridden in production. The app refuses to start if it's still on the dev default when `APP_ENV=production`.

## Authorization

`lib/authorization.py` resolves a typed principal and an active workspace. JWT
sessions may supply `X-Workspace-ID`; otherwise the oldest membership is used.
API keys are pinned to their recorded workspace, require explicit `read`/`write`
scopes, and honor expiry and current membership. Unknown/unassigned ownership is
excluded. Resource IDs from other workspaces return `404`.

Workspace `admin`/`editor` membership gates operational mutations. Agent tools and
privileged workflow blocks recheck membership and API-key grants during execution.
The chat caller can narrow its authority to read-only; it cannot grant itself a
stronger role. Installation operations require `User.is_platform_admin`, which
registration never grants. The first bootstrap user receives it; existing users
require an explicit operator grant when upgrading.

Browser media URLs carry an exact-object, expiring capability rather than relying
on a guessable storage prefix. These URLs are bearer capabilities: protect them
and expect access to remain possible until expiry. Workflow webhooks reject
non-public destinations by default; only an operator environment setting can
permit private endpoints.

## Upgrading legacy ownership

The new migrations preserve legacy data, but intentionally leave previously
unowned resources unassigned. Do not restore visibility with a NULL-workspace
fallback. After backing up and upgrading the database, an installation operator
can explicitly recover administration with `scripts/reset_admin.py`, and review
resource assignment with:

```bash
python -m scripts.assign_workspace --workspace-id WORKSPACE_UUID --resource project --id PROJECT_UUID
# Add --apply only after checking this dry-run. Repeat --id for explicit IDs.
```

Supported kinds include project, workflow, target, device, comparison, feedback
and inference-log. Already-owned resources cannot be reassigned by this utility;
linked model/endpoint ownership must agree. Assign parents before dependents.
These commands are operator actions, not actions a chat model may invoke.

## Hardening checklist

Configure these deployment settings and validate the intended operating environment:

- [ ] `APP_ENV=production`
- [ ] `JWT_SECRET` set to a random 32+ byte value (`openssl rand -hex 32`)
- [ ] `POSTGRES_PASSWORD` rotated from the default
- [ ] `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` rotated
- [ ] `ADMIN_BOOTSTRAP_PASSWORD` set explicitly
- [ ] `CORS_ORIGINS` restricted to your real frontend origin
- [ ] HTTPS terminated at a reverse proxy (Caddy, nginx, Cloudflare)
- [ ] `MINIO_SECURE=true` if MinIO is reachable across an untrusted network
- [ ] Infrastructure port bindings limited to loopback/internal interfaces
- [ ] Pre-commit hooks installed so secrets never enter git (see [development/precommit](../development/precommit))

## Headers

The API sends:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` (production only)

## Known gaps

Remaining release considerations:

- **Tokens stored in localStorage.** XSS can expose bearer credentials.
- **No rate limiting on `/auth/login` or `/auth/register`.**
- **Runtime provider settings are process-local.** Use deployment environment secrets for durable/multiworker configuration until encrypted shared credential storage is implemented.
- **Streaming and capability lifecycle.** Validate token redaction, expiry and revocation behavior with the deployment proxy and real clients.

Use the focused authorization suite and service integration checks as release gates;
also assess login abuse protection and browser credential storage before public deployment.
