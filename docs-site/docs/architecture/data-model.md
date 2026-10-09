---
title: Data Model
sidebar_position: 2
---

# Data Model

The schema is defined in [`lib/db.py`](https://github.com/oldhero5/waldo/blob/main/lib/db.py). Every table uses UUID primary keys.

## Multi-tenancy

```
Workspace ──┬── WorkspaceMember ── User
            └── Project ── Video ── Frame ── Annotation
                       └── LabelingJob ─────┘
                       └── TrainingRun ── ModelRegistry
```

A **Workspace** groups projects through `Project.workspace_id`, which is nullable for legacy projects. Videos, frames, and annotations relate to projects through their parents. `WorkspaceMember.role` supplies authorization alongside exact parent ownership.
Unassigned legacy projects are excluded until explicitly assigned by an operator.

## Core tables

### `users`
Email, bcrypt password hash, required `display_name`, and optional avatar URL. JWT `sub` claim is the user UUID. `is_platform_admin` is separate from workspace
roles and defaults false; registration never grants it.

### `api_keys`
Long-lived credentials prefixed `wld_`. Stored as `(key_prefix, key_hash)` so lookups stay fast and the raw key is irretrievable.

### `workspaces` / `workspace_members`
Workspace records and memberships. The shared authorization layer checks current membership, API-key scopes and
resource ancestry; admin/editor membership controls operational mutations.

### `projects`
A bucket of related videos. Belongs to a workspace.

### `videos`
A single uploaded video file. Tracks filename, MinIO key, fps, duration, dimensions, frame count, and project. Codec metadata is not stored in this table.

### `frames`
Extracted still images. Indexed by `(video_id, frame_number)`.

### `labeling_jobs`
A labeling run against a video or project, with prompt fields, task type, progress, status, and dataset object key. `score_threshold` and `sample_fps` retain the requested controls; historical NULL
values mean configuration was not recorded. `processing_summary` records sampled
coverage, time approximation method and per-video outcomes. Terminal states include
`completed`, `partial` and `failed`; partial results are reviewable but not silently
eligible as completed training datasets.

### `annotations`
The output of labeling jobs and human edits. Each row references a frame and job:

- `frame_id`, `job_id`
- `class_name`, `class_index`, `confidence`, `status`
- `polygon` (JSON coordinates)
- `bbox` (optional normalized center-x/center-y/width/height)
- `track_id` (nullable tracker-local identity, scoped by job and source video)

New native observations preserve each sampled sighting and normalized geometry;
representative detections no longer replace the timeline. PyTorch track IDs remain
null until the adapter exposes them. Existing rows are not automatically rewritten:
historical native pixel boxes and lost observations need reprocessing with provenance.
RLE masks and reviewer identity still require future schema work. Source timestamps
remain explicitly approximate rather than true presentation timestamps/timebase.

### `training_runs`
A YOLO26 fine-tune. References the dataset slice and produces a `ModelRegistry` row on completion.

### `model_registry`
Versioned model artifacts. Each row points to a MinIO key for weights and stores a metrics JSON object, training metadata, `is_active`, exports, and an optional alias (`champion`, `challenger`, `staging`).

### `deployment_targets` / `edge_devices`
Deployment targets assign models to API endpoints; edge-device records track assignments, heartbeats, and uploaded logs. Explicit nullable `workspace_id` columns now scope deployment targets, edge devices,
comparison runs, feedback and inference logs. Legacy NULL rows are excluded.
These resources have API endpoints; the current Deploy page does not provide a device-management view. Device registration does not install an edge runtime or perform model delivery.

## Indexes

Alembic migration `d4e5f6a7b8c9` already creates these composite indexes:

| Index | Columns |
| --- | --- |
| `ix_labeling_jobs_status_project` | `labeling_jobs(status, project_id)` |
| `ix_annotations_job_frame` | `annotations(job_id, frame_id)` |
| `ix_frames_video_frame_number` | `frames(video_id, frame_number)` |
| `ix_training_runs_status_project` | `training_runs(status, project_id)` |
| `ix_inference_logs_model_created` | `inference_logs(model_id, created_at)` |
| `ix_videos_project_created` | `videos(project_id, created_at)` |

Run migrations to install them; the frame index is not a uniqueness constraint. Frame numbering differs between extraction and native video labeling, so it is not a canonical source-time identity.
