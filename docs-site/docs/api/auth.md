---
title: Auth
sidebar_position: 2
---

# Auth

Source: [`app/api/auth.py`](https://github.com/oldhero5/waldo/blob/main/app/api/auth.py)

## `POST /api/v1/auth/register`

Create a new user account.

```http
POST /api/v1/auth/register
Content-Type: application/json

{ "email": "user@example.com", "password": "...", "display_name": "User", "workspace_name": "My Workspace" }
```

Returns `201` with `{ access_token, refresh_token, token_type }`. Registration creates a new workspace and assigns the user its `admin` role. `workspace_name` is optional and defaults to `My Workspace`. Duplicate email returns `400`.

Registration grants no installation-administrator privilege. Global administration requires the separately stored `is_platform_admin` flag. See [Security](../architecture/security).

## `POST /api/v1/auth/login`

Exchange email + password for a token pair.

```http
POST /api/v1/auth/login
Content-Type: application/json

{ "email": "user@example.com", "password": "..." }
```

Returns `200` with `{ access_token, refresh_token, token_type }`. Returns `401` on bad credentials.

## `POST /api/v1/auth/refresh`

Refresh an expired access token.

```http
POST /api/v1/auth/refresh?refresh_token=<url-encoded-refresh-token>
```

The refresh token is a query parameter, not a JSON body. Returns a fresh `{ access_token, refresh_token, token_type }` pair. Refresh tokens are valid for 30 days; access tokens for 24h by default.

## `GET /api/v1/auth/me`

Return the currently authenticated user.

Requires `Authorization: Bearer <token>`. Returns `{ id, email, display_name, avatar_url, workspace_id, workspace_name, role, is_platform_admin }`. JWT callers may select a current membership with `X-Workspace-ID`; without it, the oldest membership ordered by `joined_at`, then membership ID is selected. API keys remain pinned to their workspace. No membership returns `403`. The agent uses the same selection policy.
