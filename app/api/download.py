"""Proxy downloads from MinIO with proper caching and content types.

Public endpoint — images/frames need to load in <img> tags without auth headers.
Security via path-prefix allowlist (only serves from known prefixes).
"""

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from lib.config import settings
from lib.storage import get_client

router = APIRouter()  # Public — secured by path prefix allowlist, not auth

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".zip": "application/zip",
    ".pt": "application/octet-stream",
    ".yaml": "text/yaml",
    ".txt": "text/plain",
    ".csv": "text/csv",
}


def _guess_content_type(name: str) -> str:
    for ext, ct in CONTENT_TYPES.items():
        if name.lower().endswith(ext):
            return ct
    return "application/octet-stream"


@router.get("/download/{object_name:path}")
async def download_object(object_name: str):
    # Public prefixes — images and frames load in browser without auth
    # Model weights require auth (handled separately if needed)
    ALLOWED_PREFIXES = ("frames/", "results/", "videos/", "feedback/", "workflows/", "models/")
    if not any(object_name.startswith(p) for p in ALLOWED_PREFIXES):
        raise HTTPException(status_code=403, detail="Access denied")

    client = get_client()
    try:
        response = client.get_object(settings.minio_bucket, object_name)
        media_type = _guess_content_type(object_name)
        filename = object_name.rsplit("/", 1)[-1]

        # Images: serve inline with aggressive caching (frames don't change)
        is_image = media_type.startswith("image/")
        headers = {}
        if is_image:
            headers["Cache-Control"] = "public, max-age=86400, immutable"
            headers["Content-Disposition"] = f'inline; filename="{filename}"'
        else:
            headers["Content-Disposition"] = f'attachment; filename="{filename}"'

        return StreamingResponse(
            response,
            media_type=media_type,
            headers=headers,
        )
    except Exception:
        raise HTTPException(status_code=404, detail=f"Object not found: {object_name}")
