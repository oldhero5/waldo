---
title: Quickstart
sidebar_position: 2
---

# Quickstart: label your first video

This walks you through ingesting a video, running auto-labeling, reviewing the results, and exporting a YOLO dataset. Total time: ~15 minutes for a 60-second clip.

## 1. Create a project

Open <http://localhost:8000> and sign in with the bootstrap credentials. From the **Datasets** page, click **New Project** and give it a name.

## 2. Upload a video

Drop a `.mp4`, `.mov`, or `.mkv` file into the upload zone. The backend extracts metadata via FFmpeg, stores the file in MinIO, and queues frame extraction.

For batch uploads:

```bash
curl -X POST http://localhost:8000/api/v1/upload/batch \
  -H "Authorization: Bearer $TOKEN" \
  -F "project_id=$PROJECT_ID" \
  -F "files=@clip1.mp4" \
  -F "files=@clip2.mp4"
```

## 3. Start a labeling job

On the project page, click **Auto-label** and provide either:

- **Text prompts** (e.g. `"person, car, truck"`) — SAM 3 grounded by text
- **Visual prompts** (drag a box around an example object in the first frame)

Pick a confidence threshold (default `0.5`) and resolution. SAM 3.1 runs on the labeler worker and publishes detections back over WebSocket.

## 4. Review

Switch to the **Review** page once the job hits ~10% progress. The annotation canvas shows each frame with overlaid boxes. Use:

- `J` / `K` — prev / next frame
- `Space` — toggle play
- `D` — delete the highlighted box
- `Shift+drag` — draw a new box
- `R` — reject the whole frame

Rejected frames are excluded from the export.

## 5. Train

From **Train**, pick a YOLO26 variant (`yolo26n`, `yolo26s`, `yolo26m`...), set epochs, and click **Start training**. Progress streams to the UI; the trained weights register automatically in the model registry when complete.

## 6. Deploy

On **Deploy**, activate the new model. The serve endpoint exposes:

```
POST /api/v1/predict/image
POST /api/v1/predict/video
```

That's the round trip — raw footage to a deployed model in one session.
