---
title: Auth
sidebar_position: 2
---

# Auth

Source: [`app/api/auth.py`](https://github.com/your-org/waldo/blob/main/app/api/auth.py)

## `POST /api/v1/auth/register`

Create a new user account.

```http
POST /api/v1/auth/register
Content-Type: application/json

{ "email": "user@example.com", "password": "...", "display_name": "User" }
```

Returns `201` with `{ access_token, refresh_token, token_type }`. The new user is automatically added to the default workspace as a `viewer`.

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
POST /api/v1/auth/refresh
Content-Type: application/json

{ "refresh_token": "..." }
```

Returns a fresh `access_token`. Refresh tokens are valid for 30 days; access tokens for 24h by default.

## `GET /api/v1/auth/me`

Return the currently authenticated user.

Requires `Authorization: Bearer <token>`. Returns `{ id, email, display_name, created_at }`.
