---
title: Admin
sidebar_position: 9
---

# Admin

Source: [`app/api/admin.py`](https://github.com/oldhero5/waldo/blob/main/app/api/admin.py)

All admin routes require a user with the `admin` role in at least one workspace. Enforced by the `require_admin` dependency.

## Health

### `GET /api/v1/admin/status`
Cluster-wide status: queue depth, worker count, stuck job count, DB latency.

### `GET /api/v1/admin/workers`
List Celery workers, current task, last heartbeat.

### `GET /api/v1/admin/queue`
List Celery queues with depth and consumer count.

## Stuck jobs

### `GET /api/v1/admin/jobs/stuck`
Find labeling/training jobs that have been `running` for longer than expected.

### `POST /api/v1/admin/jobs/{job_id}/mark-failed`
Force-fail a stuck labeling job.

### `POST /api/v1/admin/training/{run_id}/mark-failed`
Force-fail a stuck training run.

## Task management

### `POST /api/v1/admin/tasks/{task_id}/revoke`
Revoke a Celery task by ID. Will SIGTERM the worker if the task is currently executing.

### `POST /api/v1/admin/queue/{queue_name}/purge`
Drop all pending tasks from a queue. Use with care.
