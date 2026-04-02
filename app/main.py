from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse

from app.api import (
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

app = FastAPI(title="Waldo", version="0.5.0")

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
