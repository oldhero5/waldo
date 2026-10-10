"""Proxy downloads from MinIO with proper caching and content types.

Exact-object signed URLs allow images/video to load without auth headers.
Capabilities expire and cannot be reused for another object.
"""

import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from lib.config import settings
from lib.storage import get_client

router = APIRouter()

CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
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


_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")


@router.get("/download/{object_name:path}")
async def download_object(object_name: str, request: Request):
    from lib.auth import decode_token

    token = request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Signed download URL required")
    claims = decode_token(token)
    if claims.get("type") != "download" or claims.get("object") != object_name:
        raise HTTPException(status_code=401, detail="Invalid download capability")

    client = get_client()
    try:
        # Size comes from MinIO stat so we can honor Range and set Content-Length.
        stat = client.stat_object(settings.minio_bucket, object_name)
        total_size = stat.size
        media_type = _guess_content_type(object_name)
        filename = object_name.rsplit("/", 1)[-1]
        is_image = media_type.startswith("image/")
        is_video = media_type.startswith("video/")

        base_headers: dict[str, str] = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=300"}
        if is_image:
            base_headers["Cache-Control"] = "private, max-age=300"
            base_headers["Content-Disposition"] = f'inline; filename="{filename}"'
        elif is_video:
            base_headers["Cache-Control"] = "private, max-age=300"
            base_headers["Content-Disposition"] = f'inline; filename="{filename}"'
        else:
            base_headers["Content-Disposition"] = f'attachment; filename="{filename}"'

        # Browsers always send `Range: bytes=0-` for `<video>` elements and
        # expect a 206 Partial Content response. Parse the range, stream the
        # requested slice out of MinIO, and return the right status/headers.
        range_header = request.headers.get("range") or request.headers.get("Range")
        if range_header:
            m = _RANGE_RE.match(range_header.strip())
            if not m:
                raise HTTPException(status_code=416, detail="Invalid Range")
            start_s, end_s = m.group(1), m.group(2)
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else total_size - 1
            if start >= total_size or end >= total_size or start > end:
                return StreamingResponse(
                    iter(()),
                    status_code=416,
                    headers={
                        **base_headers,
                        "Content-Range": f"bytes */{total_size}",
                    },
                )
            length = end - start + 1
            response = client.get_object(
                settings.minio_bucket,
                object_name,
                offset=start,
                length=length,
            )
            headers = {
                **base_headers,
                "Content-Range": f"bytes {start}-{end}/{total_size}",
                "Content-Length": str(length),
            }
            return StreamingResponse(
                response,
                status_code=206,
                media_type=media_type,
                headers=headers,
            )

        # No Range header — stream the full object with an explicit length.
        response = client.get_object(settings.minio_bucket, object_name)
        headers = {**base_headers, "Content-Length": str(total_size)}
        return StreamingResponse(
            response,
            media_type=media_type,
            headers=headers,
        )
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=404, detail=f"Object not found: {object_name}")
