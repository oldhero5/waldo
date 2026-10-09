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
Export the saved annotations to an immutable ZIP in MinIO. The request is
`{"format": "segment"}`; supported formats are `segment`, `detect`, `obb`,
`classify`, and `pose`. The response includes the format and a download URL.

Pending and accepted annotations are included; rejected annotations are omitted.
Export does not certify that a person accepted every label. Rejected-only frames
are excluded, since a rejected detection does not establish an empty scene.
Source videos stay in separate train/validation groups. One source video cannot
provide independent held-out validation, and training rejects that case.

Classification produces rectangular crops from each saved polygon envelope with
five pixels of padding, clipped to the source image. Files use
`train/<class>/*.jpg` and `val/<class>/*.jpg`. Class names must be safe single
folder names. Pose produces one polygon-centroid keypoint per object with
`kpt_shape: [1, 3]`. Its box uses the saved valid bbox, or polygon bounds when
the bbox is absent. This is a centroid target, not a skeleton or a manually
marked anatomical landmark. There is no keypoint editor.

Both formats require finite, nondegenerate normalized polygons. Missing or
invalid geometry stops the export; the service does not silently omit an object
or substitute a box center for its centroid. The crop and centroid derive from
the saved polygon, which can differ from the original mask and retains only its
largest contour. The export manifest records source annotation identities and
the geometry method.

Only an export matching the job's task updates its current training artifact.
Later annotation edits invalidate that pointer. Existing training runs keep
their immutable input snapshot. Export again after review changes.
If evidence changes while a matching export is being built, publication returns
HTTP 409 instead of marking the older ZIP as current. Export again after the
edit completes.
