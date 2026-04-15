---
title: Review
sidebar_position: 5
---

# Review

Source: [`app/api/review.py`](https://github.com/oldhero5/waldo/blob/main/app/api/review.py)

## Annotations

### `GET /api/v1/jobs/{job_id}/annotations`
Paginated list of annotations for a labeling job. Query params: `offset`, `limit`, `frame_id`, `class_name`.

### `PATCH /api/v1/annotations/{annotation_id}`
Update an annotation's bbox, class, or accepted state.

### `POST /api/v1/annotations/merge-classes`
Bulk-rename one class to another across a job (e.g. merge `truck` and `lorry`).

## Job management

### `PATCH /api/v1/jobs/{job_id}`
Update job metadata (name, description).

### `DELETE /api/v1/jobs/{job_id}`
Delete a job and all its annotations.

### `POST /api/v1/jobs/{job_id}/duplicate`
Clone a job — useful when you want to re-run with different prompts but keep the original.

### `POST /api/v1/jobs/{job_id}/add-class`
Add a new class to a finished job and re-run SAM 3 just for that class.

## Classes

### `GET /api/v1/jobs/{job_id}/classes`
List all classes present in a job's annotations.

### `DELETE /api/v1/jobs/{job_id}/classes/{class_name}`
Remove a class entirely (deletes all annotations of that class).

## Stats & export

### `GET /api/v1/jobs/{job_id}/overview`
High-level summary: frame count, annotation count per class, completion %.

### `GET /api/v1/jobs/{job_id}/stats`
Detailed stats — distributions, confidence histograms, per-class precision when ground truth is available.

### `POST /api/v1/jobs/{job_id}/export`
Generate a YOLO-format dataset (images + labels) and stage it in MinIO. Returns a download URL.
