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

## Native frame assessments

### `GET /api/v1/jobs/{job_id}/assessments`

Return the original inference record for each sampled native source frame owned
by this job. The response contains `job_id`, `coverage_status`,
`coverage_scope` (`sampled_native_frames`), `items`, and `next_cursor`. Each item
contains `id`, `source_video_id`, `source_frame_index`, `timestamp_s`,
`timestamp_method`, nullable `frame_duration_s`, `source_width`, `source_height`
and `detection_count`.

`limit` defaults to 100 and accepts 1–500. `cursor` is an optional URL-safe
base64 JSON position. It is limited to 256 characters and contains the job ID,
video ID and source frame index. Results sort by video ID and then source frame
index. The cursor marks the last returned position; it is not an authorization
token. The server authorizes the job before it reads the cursor and scopes rows
to both the job and accessible video. Invalid cursors return 400. An inaccessible
job returns 404; rows linked to inaccessible videos are omitted.

`coverage_status` is `unavailable` when the job has no accessible assessment
rows, `partial` when evidence or completion metadata is incomplete, and
`available` when a completed, unmerged job has matching version-1 assessment
counts for every completed clip. `available` covers sampled native frames only;
it does not mean full-video coverage or correct detections. A zero
`detection_count` means inference assessed that frame and returned no detections.
It does not mean a reviewer confirmed an empty scene. Missing rows mean unknown.
The count stays fixed when reviewers edit or remove annotations.

Copied jobs do not gain assessment rows. An add-class child reports its own
assessments. A job with merged-in runs retains its own rows and reports partial
coverage because those rows do not cover the combined dataset. Legacy jobs have
unavailable coverage when they have no durable rows; summary arrays are not
backfilled. If work is still running, pages are not a snapshot. Restart the scan
after completion to read all committed rows.

Example response for one assessed sample with no detections:

```json
{
  "job_id": "00000000-0000-4000-8000-000000000001",
  "coverage_status": "available",
  "coverage_scope": "sampled_native_frames",
  "items": [{
    "id": "00000000-0000-4000-8000-000000000002",
    "source_video_id": "00000000-0000-4000-8000-000000000003",
    "source_frame_index": 24,
    "timestamp_s": 1.0,
    "timestamp_method": "source_pts",
    "frame_duration_s": 0.041667,
    "source_width": 1920,
    "source_height": 1080,
    "detection_count": 0
  }],
  "next_cursor": null
}
```

Times use seconds from container start. They can be negative; they are not
recording UTC. Dimensions use decoded pixels. An unknown duration is `null`.

## Source video inspection

### `GET /api/v1/jobs/{job_id}/sources`

List the distinct sources linked to saved annotations in this job. Each entry
contains `id`, `filename`, `duration_s` and `has_source_video`. The list does not
include playback URLs. Image-only sources remain listed but have no video.

### `GET /api/v1/jobs/{job_id}/sources/{video_id}/playback`

Return `video_id` and a fresh signed `url` for that source. The caller must have
access to both the job and video, and a saved annotation must link them. An
unrelated or inaccessible source returns 404, including a clip in the same
workspace that has no saved finding in this job. Image-only sources return 404.
The signed download supports byte ranges for browser seeking.

Use the saved annotation's source ID and time to navigate. `source_pts` times
are relative to container start. Known FPS-derived times are approximate.
Unknown or invalid timing does not support an automatic seek. This endpoint
does not run inference, change a review, or establish continuous coverage.

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
