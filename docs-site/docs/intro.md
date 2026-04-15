---
slug: /
title: Welcome to Waldo
sidebar_position: 1
---

import Demo from "@site/src/components/Demo";

# Waldo

> **Where's Waldo? Right here, finding objects in your video.**

Waldo is a self-hosted ML platform for **video object detection at scale**. It pairs Meta's [SAM 3](https://github.com/facebookresearch/sam) (segment anything in video) with Ultralytics' [YOLO26](https://docs.ultralytics.com) so you can:

- **Auto-label** raw video footage with text or visual prompts
- **Review and refine** annotations in a web UI
- **Train** YOLO26 detectors on the curated dataset
- **Deploy** the trained model to a serving endpoint, edge device, or Jetson/Pi rig
- **Monitor** live predictions and feed corrections back into the dataset

The whole pipeline runs in Docker — backend, ML workers, dev UI, and the docs you're reading right now.

![Dashboard](/img/screenshots/dashboard.png)

<Demo
  src="/img/recordings/tour.mp4"
  poster="/img/recordings/tour.poster.jpg"
  caption="Six-second tour: dashboard → datasets → workflows → deploy."
/>

## Why Waldo

Most labeling tools assume you have humans drawing boxes. Waldo assumes you have an ML model that can usually do the work, and a human who steps in only when the model is wrong. That changes the whole shape of the product:

- **The labeler runs first; humans review second.** SAM 3 produces the boxes, you fix the ones it got wrong.
- **The training loop is short** — minutes for a fine-tune, not days.
- **Feedback flows back into the dataset automatically** — every reviewed frame becomes future ground truth.
- **One workspace covers data, models, deployments, and monitoring.** No five-tool stack.

## The pipeline at a glance

```
   raw video                    SAM 3                YOLO26
   ─────────  ─►  upload  ─►  auto‑label  ─►  review  ─►  train  ─►  deploy  ─►  monitor
                                       ▲                                            │
                                       └──────────  feedback loop  ◄────────────────┘
```

Every step has a UI page and an API endpoint. Use whichever you like — they're the same surface.

## What's in these docs

- **[Getting Started](./getting-started/installation)** — install with Docker, run the quickstart, configure your environment.
- **[Architecture](./architecture/overview)** — services, data model, security model.
- **[API Reference](./api/overview)** — every REST endpoint grouped by resource.
- **[Workflow Blocks](./workflows/overview)** — composable blocks for the visual workflow editor.
- **[UI Pages](./ui/overview)** — guided tour of every page in the web UI, with screenshots and short videos.
- **[Deployment](./deployment/docker)** — Docker-first instructions for Linux, Windows, and edge.
- **[Development](./development/setup)** — pre-commit hooks, tests, contributing.

## Five-minute path

1. [Install with Docker](./getting-started/installation) — `docker compose up -d`
2. [Walk through the quickstart](./getting-started/quickstart) — upload a clip, auto-label, train, deploy
3. Skim the [UI Tour](./ui/overview) to see what every page does
4. Bookmark the [API reference](./api/overview) for when you start scripting

Welcome aboard.
