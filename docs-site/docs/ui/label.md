---
title: Label
sidebar_position: 3
---

# Label Page

Route: `/label` — Source: [`ui/src/pages/LabelPage.tsx`](https://github.com/your-org/waldo/blob/main/ui/src/pages/LabelPage.tsx)

Configure and start a SAM 3 auto-labeling job. This is where text prompts become bounding boxes.

## Workflow

1. Pick a video.
2. Enter prompts (one per line). Each prompt becomes a class.
3. Adjust the confidence threshold and resolution.
4. Click **Preview** to test on a handful of frames before committing.
5. Click **Start labeling** — the job is queued and progress streams to the UI.

## Visual prompts

Toggle to "exemplar" mode and draw a box around an example object on the first frame. SAM 3 finds visually similar objects across the video.

## Click-to-segment

The fine-grained segmentation tool calls `POST /api/v1/label/segment-points` on every click — useful for polishing individual annotations once review is underway.

![Label page](/img/screenshots/label.png)
