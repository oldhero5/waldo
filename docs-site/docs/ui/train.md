---
title: Train
sidebar_position: 4
---

# Train Page

Route: `/train` — Source: [`ui/src/pages/TrainPage.tsx`](https://github.com/your-org/waldo/blob/main/ui/src/pages/TrainPage.tsx)

Configure and launch YOLO26 training runs. Live logs stream from the trainer worker over WebSocket.

## Configuration

| Field | Notes |
| --- | --- |
| Variant | `n / s / m / l / x` — accuracy ↔ speed tradeoff |
| Image size | 640 default; 1280 for small objects |
| Batch size | Auto if blank (Ultralytics picks based on VRAM) |
| Epochs | 50–100 typical for fine-tunes |
| Augmentation preset | Off / light / standard / aggressive |

## Live logs

Each run gets a log stream with epoch, loss, mAP@50, mAP@50:95, and ETA. The chart auto-scrolls.

## After training

Successful runs register a new model in the registry. Promote it via the **Deploy** page or `POST /api/v1/models/{model_id}/promote`.

![Train page](/img/screenshots/train.png)
