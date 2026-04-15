---
slug: /
title: Welcome to Waldo
sidebar_position: 1
---

# Waldo

Waldo is a self-hosted ML platform for **video object detection at scale**. It pairs Meta's [SAM 3](https://github.com/facebookresearch/sam) (segment anything in video) with Ultralytics' [YOLO26](https://docs.ultralytics.com) so you can:

- **Auto-label** raw video footage with text or visual prompts
- **Review and refine** annotations in a web UI
- **Train** YOLO26 detectors on the curated dataset
- **Deploy** the trained model to a serving endpoint, edge device, or Jetson/Pi rig
- **Monitor** live predictions and feed corrections back into the dataset

The whole pipeline runs in Docker — backend, ML workers, dev UI, and even the docs you're reading right now.

## Why Waldo

Most labeling tools assume you have humans drawing boxes. Waldo assumes you have an ML model that can usually do the work, and a human who steps in only when the model is wrong. That changes the whole shape of the product:

- The labeler runs first; humans review second.
- The training loop is short — minutes, not days.
- Feedback flows back into the dataset automatically.
- One workspace covers the data, models, deployments, and monitoring.

## What's in these docs

- **[Getting Started](./getting-started/installation)** — install with Docker, run the quickstart, configure your environment.
- **[Architecture](./architecture/overview)** — services, data model, security model.
- **[API Reference](./api/overview)** — every REST endpoint grouped by resource.
- **[Workflow Blocks](./workflows/overview)** — composable blocks for the visual workflow editor.
- **[UI Pages](./ui/overview)** — guided tour of every page in the web UI.
- **[Deployment](./deployment/docker)** — Docker-first instructions for Linux, Windows, and edge.
- **[Development](./development/setup)** — pre-commit hooks, tests, contributing.
