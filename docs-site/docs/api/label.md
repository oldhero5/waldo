---
title: Label
sidebar_position: 4
---

# Label

Source: [`app/api/label.py`](https://github.com/oldhero5/waldo/blob/main/app/api/label.py)

## `POST /api/v1/label`

Start a SAM 3 auto-labeling job on one or more videos.

```json
{
  "project_id": "...",
  "video_ids": ["..."],
  "prompts": ["person", "car"],
  "threshold": 0.5,
  "resolution": 1008,
  "fps": 5
}
```

Returns `202 Accepted` with `{ job_id }`. Poll `/api/v1/status/{job_id}` or subscribe to the WebSocket for progress.

## `POST /api/v1/label/exemplar`

Visual-prompt variant — instead of text, provide an image crop (or bbox) and SAM 3 finds visually similar objects.

## `POST /api/v1/label/preview`

Run SAM 3 on a small sample of frames to preview a prompt before committing
to a full job. Returns base64-encoded JPEG previews.

**Async by default.** The endpoint dispatches a Celery task and returns
`202 Accepted` with `{job_id, status, result_url}`. Poll
`GET /api/v1/job/{job_id}` until `status == "completed"`, then read the
preview body from `result`. See the [polling pattern](./overview#async-polling-pattern)
for the envelope shape.

For backward compatibility during migration, pass `?wait=true` to block
the request thread until the preview finishes (180s timeout). The flag is
deprecated and will be removed in a future release; new callers should
poll instead.

## `POST /api/v1/label/segment-points`

Interactive segmentation. Send click points + frame and SAM 3 returns a mask. Used by the click-to-segment tool in the review canvas.

## `POST /api/v1/annotations`

Create an annotation manually (e.g. when a human draws a box in the review UI).
