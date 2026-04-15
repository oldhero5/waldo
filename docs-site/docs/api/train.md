---
title: Train
sidebar_position: 6
---

# Train

Source: [`app/api/train.py`](https://github.com/your-org/waldo/blob/main/app/api/train.py)

## Variants

### `GET /api/v1/train/variants`
List available YOLO26 variants (`yolo26n`, `yolo26s`, `yolo26m`, `yolo26l`, `yolo26x`).

## Training runs

### `POST /api/v1/train`
Start a training run.

```json
{
  "job_id": "...",
  "variant": "yolo26m",
  "epochs": 50,
  "image_size": 640,
  "batch_size": 16,
  "name": "person-car-v3"
}
```

Returns `202` with `{ run_id }`. Progress is published over WebSocket as the trainer streams logs.

### `GET /api/v1/train`
List all training runs.

### `GET /api/v1/train/{run_id}`
Detailed status: epoch, loss, mAP, ETA.

### `PATCH /api/v1/train/{run_id}`
Update name or notes.

### `DELETE /api/v1/train/{run_id}`
Delete a training run and its artifacts.

### `POST /api/v1/train/{run_id}/stop`
Gracefully stop a running training job.

## Models

### `GET /api/v1/models`
List all models in the registry.

### `POST /api/v1/models/{model_id}/export`
Export to ONNX or CoreML for edge deployment.
