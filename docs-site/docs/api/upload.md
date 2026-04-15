---
title: Upload & Projects
sidebar_position: 3
---

# Upload & Projects

Source: [`app/api/upload.py`](https://github.com/oldhero5/waldo/blob/main/app/api/upload.py)

## Projects

### `GET /api/v1/projects`
List projects in your workspaces.

### `GET /api/v1/projects/{project_id}/videos`
List all videos in a project.

## Video upload

### `POST /api/v1/upload`
Upload a single video. `multipart/form-data` with fields:

- `project_id` — UUID of the target project
- `file` — the video binary

Returns `201` with the new `Video` row.

### `POST /api/v1/upload/batch`
Upload multiple videos in one request. Same field shape but `files` is a list.

### `POST /api/v1/upload/images`
Upload still images instead of videos. Each image becomes a 1-frame video so the rest of the pipeline still applies.

## Linking external storage

### `POST /api/v1/link-videos`
Register videos that already exist in MinIO without re-uploading. Useful for very large datasets staged out-of-band.

```json
{
  "project_id": "...",
  "objects": [
    { "minio_key": "raw/clip001.mp4", "filename": "clip001.mp4" }
  ]
}
```
