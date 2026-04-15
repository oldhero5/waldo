---
title: Datasets
sidebar_position: 2
---

# Datasets Page

Route: `/datasets` — Source: [`ui/src/pages/DatasetsPage.tsx`](https://github.com/your-org/waldo/blob/main/ui/src/pages/DatasetsPage.tsx)

The Datasets page is the home for everything project-related: browse projects, drill into one to see videos and labeling jobs, kick off new auto-label runs, and import existing datasets.

## What you can do here

- **Create projects** — give them a name; they're scoped to your active workspace.
- **Upload videos** — drop files or batch-upload via the URL list.
- **Browse datasets** — each project card shows video count, frame count, and the latest job state.
- **Import** — pull datasets from existing YOLO directories on disk or from MinIO keys.
- **Configure prompts** — define text or visual prompts at the project level so new uploads auto-label automatically.

## Notable interactions

- Expanding a project card calls `GET /api/v1/jobs/{job_id}/overview` for inline stats.
- The import panel is hidden by default to keep the page snappy — flips the `showImport` query enable.
- TanStack Query caches results with a 5-minute `staleTime`; pull to refresh forces a refetch.

![Datasets page](/img/screenshots/datasets.png)
