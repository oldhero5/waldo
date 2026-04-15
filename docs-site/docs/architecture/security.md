---
title: Security
sidebar_position: 3
---

# Security

Waldo's security model assumes:

- The API is reachable by trusted users behind your network perimeter, **or**
- The API is exposed publicly over HTTPS with all production hardening enabled.

If neither is true, do not use Waldo.

## Authentication

- **JWT bearer tokens** issued by `/api/v1/auth/login`. HS256 signed, 24h default TTL.
- **API keys** (`wld_…` prefix) for programmatic access. Stored as bcrypt hashes.
- **Bootstrap admin** is created on first start. The password is generated randomly and logged once unless `ADMIN_BOOTSTRAP_PASSWORD` is set.

The `JWT_SECRET` MUST be overridden in production. The app refuses to start if it's still on the dev default when `APP_ENV=production`.

## Authorization

Role-based via `WorkspaceMember.role`:

| Role | Capabilities |
| --- | --- |
| `viewer` | Read access to projects in their workspace |
| `annotator` | Plus: edit annotations |
| `editor` | Plus: create projects, start labeling/training jobs |
| `admin` | Plus: manage workspace members, run admin endpoints |

The `require_admin` FastAPI dependency gates the entire `/admin/*` route tree.

## Hardening checklist

Before exposing Waldo to the internet:

- [ ] `APP_ENV=production`
- [ ] `JWT_SECRET` set to a random 32+ byte value (`openssl rand -hex 32`)
- [ ] `POSTGRES_PASSWORD` rotated from the default
- [ ] `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` rotated
- [ ] `ADMIN_BOOTSTRAP_PASSWORD` set explicitly
- [ ] `CORS_ORIGINS` restricted to your real frontend origin
- [ ] HTTPS terminated at a reverse proxy (Caddy, nginx, Cloudflare)
- [ ] `MINIO_SECURE=true` if MinIO is reachable across an untrusted network
- [ ] Redis bound to the internal Docker network only
- [ ] Pre-commit hooks installed so secrets never enter git (see [development/precommit](../development/precommit))

## Headers

The API sends:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- `Strict-Transport-Security: max-age=31536000; includeSubDomains` (production only)

## Known gaps

These are tracked in the audit report and not yet fixed:

- **Tokens stored in localStorage.** Vulnerable to XSS exfiltration. Migration to HttpOnly cookies + CSRF tokens is planned.
- **No rate limiting on `/auth/login` or `/auth/register`.** Add `slowapi` and per-IP buckets if exposed publicly.
- **IDOR risk on project/video routes.** Some routes verify auth but skip workspace membership checks. Audit before exposing multi-tenant deployments.

If you're running a single-tenant deployment behind your VPN, the gaps above are lower priority. If you're running multi-tenant, fix them first.
