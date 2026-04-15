---
title: Workflows
sidebar_position: 8
---

# Workflows

Source: [`app/api/workflows.py`](https://github.com/oldhero5/waldo/blob/main/app/api/workflows.py)

A workflow is a graph of [workflow blocks](../workflows/overview) that processes an image or video. The visual editor lives at `/workflows`.

## Block discovery

### `GET /api/v1/workflows/blocks`
Return the catalog of available blocks (with input/output types and parameter schemas) so the editor can render the palette.

## Saved workflows

### `POST /api/v1/workflows`
Create a workflow from a graph definition.

### `GET /api/v1/workflows/saved`
List saved workflows in your workspace.

### `GET /api/v1/workflows/saved/{slug}`
Fetch one workflow by slug.

### `DELETE /api/v1/workflows/saved/{slug}`

### `POST /api/v1/workflows/saved/{slug}/deploy`
Promote a saved workflow to a serve endpoint.

## Execution

### `POST /api/v1/workflows/serve/{slug}`
Run a deployed workflow. Inputs vary by block graph.

### `POST /api/v1/workflows/run`
Run an ad-hoc workflow without saving it. The graph is in the request body.

### `POST /api/v1/workflows/run/image`
Convenience endpoint for image-only workflows.
