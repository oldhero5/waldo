---
title: Train
sidebar_position: 4
---

# Train Page

Route: `/train/:jobId` — Source: [`ui/src/pages/TrainPage.tsx`](https://github.com/oldhero5/waldo/blob/main/ui/src/pages/TrainPage.tsx)

Configure and launch YOLO26 training runs against a finished labeling job. Live logs stream from the trainer worker over WebSocket.

![Train page](/img/screenshots/train.png)

## Configuration

| Field | Notes |
| --- | --- |
| **Model** | YOLO26 (`yolo26n/s/m/l/x`) — accuracy ↔ speed tradeoff |
| **Task** | Detection or segmentation |
| **Pretrained** | Start from COCO, Open Images, or a model already in your registry |
| **Image size** | `640` default; `1280` for small objects |
| **Batch size** | Auto if blank (Ultralytics picks based on VRAM) |
| **Epochs** | `50` is a sensible default; bump to `100`+ for hard datasets |
| **Augmentation preset** | `minimal` / `standard` / `aggressive` |
| **Learning rate** | Auto by default; override for fine-tunes from a strong base |

The Standard preset is highlighted in the UI for a reason — it covers most cases. Pick `aggressive` only when you have lots of frames and want extra robustness.

## Live progress

While a run is active, the page shows:

- **Status badge** — `queued`, `running`, `completed`, `failed`, `stopped`
- **Epoch counter + progress bar** with ETA
- **Loss chart** — box, cls, dfl losses streaming in
- **Validation metrics** — mAP@50 and mAP@50:95 per epoch
- **Live log tail** with auto-scroll (toggle to pause)

You can stop a running training job from the same page (`POST /api/v1/train/{run_id}/stop`) — the trainer flushes the latest checkpoint and registers it.

## After training

Successful runs auto-register a new model in the registry. From there you can:

- **Activate** it (`POST /api/v1/models/{model_id}/activate`) so the default `/predict/*` endpoints use it.
- **Promote** it to a labeled alias (`production`, `staging`, `canary`).
- **Export** to ONNX, CoreML, TFLite, or Edge TPU (`POST /api/v1/models/{model_id}/export`).
- **Deploy** to a named endpoint or push to an [edge device](../deployment/edge).

## Related API

- [`POST /api/v1/train`](../api/train#post-apiv1train) — start a run
- [`GET /api/v1/train/{run_id}`](../api/train#get-apiv1trainrun_id) — current status
- [`POST /api/v1/train/{run_id}/stop`](../api/train#post-apiv1trainrun_idstop) — graceful stop
