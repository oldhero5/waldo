---
title: Quickstart
sidebar_position: 2
---

import Demo from "@site/src/components/Demo";

# Quickstart: label your first video

This walks you through ingesting a video, running auto-labeling, reviewing the results, training a YOLO26 detector, and deploying it. Total time: about 15 minutes for a 60-second clip.

<Demo
  src="/img/recordings/tour.mp4"
  poster="/img/recordings/tour.poster.jpg"
  caption="The pages we'll touch: dashboard → datasets → workflows → deploy."
/>

## 0. Bring up the stack

```bash
git clone https://github.com/oldhero5/waldo.git
cd waldo
cp .env.example .env
docker compose up -d
```

Wait ~30 seconds for Postgres to migrate, then:

```bash
docker compose logs app | grep -A 2 "bootstrapped first admin"
```

Save the printed admin password — it's the only time it's shown.

## 1. Sign in & create a dataset

Open [http://localhost:8000](http://localhost:8000). The login page accepts the bootstrap credentials.

![Login](/img/screenshots/login.png)

Once you're in, the dashboard greets you with workspace stats and a "next step" nudge. Head to **Datasets** in the sidebar.

![Dashboard](/img/screenshots/dashboard.png)

Click **+ New Dataset** and give it a name.

![Datasets](/img/screenshots/datasets.png)

## 2. Upload a video

Drop a `.mp4`, `.mov`, or `.mkv` into the upload zone. The backend extracts metadata via FFmpeg, stores the file in MinIO, and queues frame extraction.

![Upload](/img/screenshots/upload.png)

For batch uploads from the command line:

```bash
TOKEN=...   # JWT from POST /api/v1/auth/login
curl -X POST http://localhost:8000/api/v1/upload/batch \
  -H "Authorization: Bearer $TOKEN" \
  -F "project_id=$PROJECT_ID" \
  -F "files=@clip1.mp4" \
  -F "files=@clip2.mp4"
```

## 3. Start a labeling job

From the dataset, click **Auto-label**. Provide either:

- **Text prompts** (one per line, e.g. `person`, `car`, `truck`) — SAM 3.1 grounded by free text.
- **Visual prompts** — drag a box around an example object in the first frame; SAM 3 finds visually similar objects across the video.

Pick a confidence threshold (default `0.5`) and resolution (`1008` is a good middle ground). Click **Preview** to test on a handful of frames; click **Start labeling** to commit.

The job streams progress back to the UI over WebSocket. You can switch to Review as soon as the first frames complete.

## 4. Review

The Review canvas shows each frame with overlaid boxes. Accept, reject, edit, redraw — every action PATCHes back to the API.

![Review](/img/screenshots/review.jpg)

<Demo
  src="/img/recordings/review.mp4"
  poster="/img/recordings/review.poster.jpg"
  caption="Scrolling through reviewed frames."
/>

Keyboard shortcuts:

| Key | Action |
| --- | --- |
| `J` / `K` | Prev / next frame |
| `Space` | Toggle play |
| `D` | Delete the highlighted box |
| `Shift+drag` | Draw a new box |
| `R` | Reject the whole frame |

Rejected frames are excluded from training exports.

## 5. Train

Open **Train** for the job. Pick a YOLO26 variant (`yolo26n` for fastest, `yolo26m` for a sensible default, `yolo26x` for max accuracy), pick an augmentation preset, and click **Start training**.

![Train](/img/screenshots/train.png)

Live logs stream from the trainer worker. Loss + mAP charts auto-scroll. The trained weights register in the model registry automatically when the run finishes.

## 6. Deploy

Open **Deploy → Models** and star your new model to mark it active. The default `/predict/*` endpoints will use it from the next request.

![Deploy](/img/screenshots/deploy.png)

<Demo
  src="/img/recordings/deploy.mp4"
  poster="/img/recordings/deploy.poster.jpg"
  caption="Stepping through the Deploy tabs."
/>

Try it from the **Test** tab (drag in an image), or hit the API directly:

```bash
curl -X POST http://localhost:8000/api/v1/predict/image \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@sample.jpg"
```

That's the round trip — raw footage to a deployed model in one session.

## Where next

- [UI Tour](../ui/overview) — every page, screenshotted and explained
- [Workflow Blocks](../workflows/overview) — chain SAM, YOLO, and post-processing into a graph
- [Edge deployment](../deployment/edge) — push the model to a Jetson or Pi+TPU
- [API Reference](../api/overview) — the full OpenAPI surface
