import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import (
    admin,
    agent,
    auth,
    download,
    feedback,
    frames,
    label,
    review,
    serve,
    status,
    train,
    upload,
    workflows,
    workspaces,
)
from app.ws import router as ws_router
from lib.config import enforce_production_secrets, settings

enforce_production_secrets()

app = FastAPI(title="Waldo", version="0.5.0")


@app.on_event("startup")
def _startup_bootstrap() -> None:
    from lib.auth import bootstrap_admin_if_empty

    bootstrap_admin_if_empty()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if settings.is_production():
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        return response


app.add_middleware(SecurityHeadersMiddleware)

# CORS — explicit allowlist; in dev, allow Vite dev server. In prod, set CORS_ORIGINS.
_cors_origins = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://localhost:8000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors_origins if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gzip compress all responses > 500 bytes (huge win for JSON annotation lists)
app.add_middleware(GZipMiddleware, minimum_size=500)

app.include_router(auth.router, prefix="/api/v1", tags=["auth"])
app.include_router(upload.router, prefix="/api/v1", tags=["upload"])
app.include_router(label.router, prefix="/api/v1", tags=["label"])
app.include_router(status.router, prefix="/api/v1", tags=["status"])
app.include_router(review.router, prefix="/api/v1", tags=["review"])
app.include_router(frames.router, prefix="/api/v1", tags=["frames"])
app.include_router(train.router, prefix="/api/v1", tags=["train"])
app.include_router(download.router, prefix="/api/v1", tags=["download"])
app.include_router(serve.router, prefix="/api/v1", tags=["serve"])
app.include_router(feedback.router, prefix="/api/v1", tags=["feedback"])
app.include_router(workflows.router, prefix="/api/v1", tags=["workflows"])
app.include_router(agent.router, prefix="/api/v1", tags=["agent"])
app.include_router(workspaces.router, prefix="/api/v1", tags=["workspaces"])
app.include_router(admin.router, prefix="/api/v1", tags=["admin"])
app.include_router(ws_router)


@app.get("/health")
def health():
    return {"status": "ok"}


# Serve React UI as static files (must be last so API routes take priority)
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    # SPA fallback + asset serving with proper cache headers
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = static_dir / full_path
        if not file_path.resolve().is_relative_to(static_dir.resolve()):
            raise HTTPException(status_code=404)
        if file_path.is_file():
            # Hashed assets (JS/CSS) — cache forever (Vite adds content hash to filename)
            if file_path.suffix in (".js", ".css", ".woff2", ".woff"):
                return FileResponse(
                    file_path,
                    headers={"Cache-Control": "public, max-age=31536000, immutable"},
                )
            # Other static files (favicon, images) — cache 1 hour
            return FileResponse(
                file_path,
                headers={"Cache-Control": "public, max-age=3600"},
            )
        # index.html — never cache (so new deploys are picked up immediately)
        return FileResponse(
            static_dir / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
