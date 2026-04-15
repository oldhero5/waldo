---
title: Serve
sidebar_position: 7
---

# Serve

Source: [`app/api/serve.py`](https://github.com/oldhero5/waldo/blob/main/app/api/serve.py)

The serve module is the largest in the API — it covers prediction endpoints, model management, deployment targets, edge devices, A/B comparisons, and metrics.

## Prediction

### `POST /api/v1/predict/image`
Run the active YOLO model against a single image. `multipart/form-data` with `file`. Returns detections as `[{ class, confidence, bbox }]`.

### `POST /api/v1/predict/video`
Run the active model against an uploaded video. Returns per-frame detections.

### `POST /api/v1/predict/sam`
Run SAM 3 against an image with text or visual prompt.

### `POST /api/v1/predict/sam/video`
SAM 3 video segmentation against an uploaded clip.

## Model management

### `POST /api/v1/models/{model_id}/activate`
Set a model as the active one for the default `/predict/*` endpoints.

### `POST /api/v1/models/{model_id}/promote`
Promote a model to a labeled alias (`production`, `staging`, `canary`).

### `GET /api/v1/serve/classes`
List the class vocabulary of the currently active model.

### `GET /api/v1/serve/status`
Health + active model + warmup state.

## Endpoints (named deployments)

### `POST /api/v1/endpoints/{slug}/predict`
Run prediction against a specific named endpoint instead of the default. Endpoints can pin a specific model version.

### `GET /api/v1/endpoints/{slug}/status`
Per-endpoint health and stats.

## A/B comparisons

### `GET /api/v1/comparisons`
List comparison configurations (two models running side-by-side).

### `POST /api/v1/comparisons`
Create a comparison.

### `POST /api/v1/comparisons/run`
Run both models against an input and return a side-by-side diff.

### `GET /api/v1/comparisons/result/{session_id}`
Fetch a previously-run comparison result.

### `DELETE /api/v1/comparisons/{comparison_id}`

## Experiments

### `GET /api/v1/experiments`
List experiments — long-running comparison campaigns with metrics.

### `POST /api/v1/experiments`
Create an experiment.

### `POST /api/v1/experiments/{experiment_id}/complete`
Mark an experiment finished.

## Deployment targets

### `GET /api/v1/targets`
List remote inference targets (other Waldo instances, edge devices, third-party servers).

### `POST /api/v1/targets`
Register a new target.

### `PATCH /api/v1/targets/{target_id}`
Update target config.

### `DELETE /api/v1/targets/{target_id}`

## Edge devices

### `GET /api/v1/devices`
List registered edge devices (Jetson, Pi + Coral TPU, etc).

### `POST /api/v1/devices`
Register a device.

### `POST /api/v1/devices/{device_id}/heartbeat`
Device check-in. Body includes battery, temperature, uptime.

### `POST /api/v1/devices/{device_id}/sync-logs`
Push prediction logs from the device back to the central API.

## Metrics

### `GET /api/v1/metrics/summary`
Aggregate prediction stats across the active deployment.
