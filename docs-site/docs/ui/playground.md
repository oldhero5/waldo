---
title: Playground
sidebar_position: 5
---

# SAM 3.1 Playground

Route: `/playground` — Source: [`ui/src/pages/PlaygroundPage.tsx`](https://github.com/oldhero5/waldo/blob/main/ui/src/pages/PlaygroundPage.tsx)

The Playground is where you iterate on prompts, thresholds, and a short time window of a real video *before* committing to a full labeling job. It runs SAM 3.1 synchronously against a contiguous slice of one video, overlays segmentation masks directly on the playing video, and lets you verify that tracking will dedupe moving objects the way you expect.

## Why it exists

Text prompts to SAM 3.1 look deceptively simple — "pothole", "person crossing", "yellow school bus" — but the outcome is very sensitive to wording, threshold, and how the prompt interacts with object motion. Running a full labeling pass just to check a prompt wastes minutes and disk. The Playground spends ~15s on the first run (model warm-up) and ~1–3s on each subsequent iteration against the same worker.

It is also the fastest way to verify **tracking**. SAM 3.1 assigns a `track_id` to each unique object across frames via `SimpleTracker`. The Playground runs on a **contiguous** time window (not strided samples) so those track IDs actually persist and you can see dedupe behavior at a glance — if your prompt produces 40 raw detections but only 3 unique `track_id`s, tracking is doing its job.

## Controls

**Left panel:**

| Field | Notes |
| --- | --- |
| **Collection** | Project picker — auto-fills from your current workspace. |
| **Video** | Any video in the selected collection. The video's intrinsic resolution and fps are shown below the dropdown. |
| **Prompts** | Add/remove rows for text classes. Each row is an independent class prompt — the preview returns one `label` per detection and colors them consistently. |
| **Confidence threshold** | 0.05 – 0.90. Start at 0.35 and tune. Lower for rare or small objects, higher to kill false positives. |
| **Start time** | Offset into the video, in seconds. The window is always `[start, start + duration]`. |
| **Window duration** | 1–15 s. Longer windows give tracking more frames to build IDs but cost more inference time. |
| **Sample rate** | 1 / 2 / 4 / 8 fps. This is the playground's sampling rate — the underlying video plays at native fps but detections are only computed at the sampled frames. |

The bottom of the panel shows "≈ N frames this run" so you know what you're asking the worker to chew on.

**Right pane** (after you click **Run preview**):

- **Summary bar**: frames processed, total raw detections, **unique tracked objects** (the one that matters for dedupe), and per-class count chips.
- **Promote-to-job CTA**: if the result looks right, one click creates a real labeling job with the same prompts and navigates to the review page.
- **Video player with mask overlay**: plays the original video inline with SVG segmentation polygons rendered on the nearest sampled frame. Polygons are translucent fills (25% alpha) outlined in the track's color, tagged like `#42 pothole · 87%` (track id, class label, score).
- **Scrubber + timeline**: clamped to `[start, start + duration]`. Tick marks on the track mark every sampled frame that had at least one detection. Playback loops at the window end.
- **Fullscreen toggle** (top-right) — native `requestFullscreen()` on the player container.
- **Detected objects list**: one row per unique `track_id` with label, best-frame timestamp, per-track frame count, and best score. Click any row to **zoom to that detection**.

## Zoom-to-detection

Clicking a row in the detected-objects list does three things:

1. Pauses the video and seeks to the detection's best-score frame.
2. Selects the matching polygon in the overlay (it gets a thicker stroke and a stronger fill).
3. CSS-transforms the player stage so the mask bounds fill ≈55% of the viewport, centered.

The transform math is deliberately scale-independent: with `transform-origin: center center`, applying `scale(S) translate(Tx%, Ty%)` sends a container-local point `P` to `S·P + (1−S)·C + S·T`, where `C` is the container center. Solving for "bbox center lands on C" gives `T = C − P` — the translate is the same regardless of the chosen scale, so we just translate by `(0.5 − cxNorm, 0.5 − cyNorm)` in container-percent units and pick the scale separately to hit the target fill fraction. No viewport-pixel measurements needed.

A **Reset zoom** pill appears at the top-left of the player while zoomed; clicking it (or clicking the same detection row again) restores the full view.

## Contiguous window vs. strided sampling

Older versions of the playground sampled N evenly-spaced frames across the whole video. That's fine for "does my prompt find anything at all?" but useless for tracking — each frame is seconds or minutes apart, so `SimpleTracker` can't associate objects. The current implementation defaults to a **contiguous window**: `[start_sec, start_sec + duration_sec]` sampled at `sample_fps` samples/s, running `SimpleTracker` across the sequence. That's the same tracker that runs during real labeling, so the `unique_track_count` you see in the preview is a reliable preview of what a full job would produce.

## Backend routing

The `/api/v1/label/preview` endpoint dispatches a Celery task that routes by host OS:

- **macOS (Darwin)** → `run_playground` via `mlx-vlm`. Requires the native labeler worker started by `make up` (auto-selects `up-mac` on Darwin) or legacy `make up-gpu`. MLX cannot run inside a Linux container.
- **Linux / Windows** → `_run_playground_pytorch` via `Sam3VideoInferenceSession`. Runs inside the Docker labeler with no MLX dependency. Capped at 40 frames per call so the sync HTTP preview stays under the timeout.

Both paths return the same response shape (masks, polygons, `track_id`s, summary stats) so the frontend doesn't branch.

## Related API

- [`POST /api/v1/label/preview`](../api/label) — dispatches the preview task, returns frames + detections
- [`POST /api/v1/label`](../api/label) — start a real labeling job with the prompts from the playground
