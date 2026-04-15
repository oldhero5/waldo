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

A **Workspace** is the unit of isolation. Every project, video, frame, and annotation belongs to exactly one workspace via its parent project. Membership is granted by `WorkspaceMember` rows with a role (`admin`, `editor`, `annotator`, `viewer`).

## Core tables

### `users`
Email + bcrypt password hash + optional `display_name`. JWT `sub` claim is the user UUID.

### `api_keys`
Long-lived credentials prefixed `wld_`. Stored as `(key_prefix, key_hash)` so lookups stay fast and the raw key is irretrievable.

### `workspaces` / `workspace_members`
Tenant boundary + RBAC. All resource-access checks resolve to "is this user a member of the parent workspace?"

### `projects`
A bucket of related videos. Belongs to a workspace.

### `videos`
A single uploaded video file. Tracks MinIO key, codec, fps, duration, and the project it was uploaded to.

### `frames`
Extracted still images. Indexed by `(video_id, frame_number)`.

### `labeling_jobs`
A run of SAM 3 against a video (or set of frames) with a particular prompt and threshold. Status flows: `pending → running → done` (or `failed`).

### `annotations`
The output of labeling jobs and human edits. One row per object instance per frame:
- `frame_id`, `job_id`
- `class_name`, `confidence`
- `bbox` (xyxy normalized)
- `mask` (optional, RLE-encoded)
- `track_id` (for multi-frame instance tracking)
- `accepted_by_user_id` (set when a human reviews and confirms)

### `training_runs`
A YOLO26 fine-tune. References the dataset slice and produces a `ModelRegistry` row on completion.

### `model_registry`
Versioned model artifacts. Each row points to a MinIO key for weights and tracks `alias` (e.g. `production`, `staging`), `mAP50`, training metadata.

### `deployment_targets` / `edge_devices`
Optional resources for pushing models to remote inference endpoints or edge hardware. Currently exposed only via API; UI integration is in progress.

## Indexes

The schema ships with single-column indexes on the obvious foreign keys. The performance audit recommends adding these composites:

```sql
CREATE INDEX idx_labeling_job_project_status
  ON labeling_jobs(project_id, status);

CREATE INDEX idx_annotation_job_frame
  ON annotations(job_id, frame_id);
```

Add them via Alembic migration when you hit the scan threshold.
