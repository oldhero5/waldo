---
title: Deploy
sidebar_position: 5
---

import Demo from "@site/src/components/Demo";

# Deploy Page

Route: `/deploy` or `/deploy/:tab` — Source: [`ui/src/pages/DeployPage.tsx`](https://github.com/oldhero5/waldo/blob/main/ui/src/pages/DeployPage.tsx)

Inspect model prediction URLs, test inference, manage the model registry, and monitor inference metrics. The current client has four tabs; `/deploy` opens Endpoints.

![Deploy — overview](/img/screenshots/deploy.png)

<Demo
  src="/img/recordings/deploy.mp4"
  poster="/img/recordings/deploy.poster.jpg"
  caption="Deploy walkthrough. Existing media may show an earlier interface."
/>

## Tabs

| Tab | Route | Purpose |
| --- | --- | --- |
| Endpoints | `/deploy/endpoints` | Per-model prediction URLs, copyable examples, and API reference |
| Test | `/deploy/test` | Image/video inference and comparison of two models |
| Models | `/deploy/models` | Registry, metrics, champion promotion, export, and experiments |
| Monitor | `/deploy/monitor` | Request counts, latency, confidence, and detection breakdowns |

The legacy `/deploy/api` URL selects Endpoints. Unknown tab names also display Endpoints. Edge devices have no tab in this client; see [Edge deployment](../deployment/edge) for the separate deployment documentation.

### Endpoints

![Deploy — endpoints](/img/screenshots/deploy-endpoints.png)

Each trained model has a prediction URL. Inspect the model's active status and available classes, then copy its URL or a Python, curl, or JavaScript example. The Reference section documents image and video requests and response fields. This tab does not provide a named-endpoint creation form or routing-rule editor.

### Test

![Deploy — test](/img/screenshots/deploy-test.png)

Choose Image, Video, or Compare. Image and video views run inference against the serving model, display detections, and provide confidence and class filters. Video results include track and playback controls. Compare runs two selected models on the same image or video and retains session information while processing continues.

### Models

![Deploy — models](/img/screenshots/deploy-models.png)

Search the registry and inspect model metrics. Promote a model to champion after confirming the traffic change, export weights, or configure an experiment with a champion, challenger, and traffic split. Models are grouped by task type; active and best-metric states are identified in each card.

### Monitor

![Deploy — monitor](/img/screenshots/deploy-monitor.png)

The page polls `GET /api/v1/metrics/summary` every 15 seconds while visible. Choose a 1-hour, 24-hour, or 7-day window to inspect total requests, average latency, p95 latency, and average confidence, along with request volume and breakdowns by detection class and model. It does not currently display p99 latency or an error-rate chart.

## Related API

- [`POST /api/v1/models/{id}/promote`](../api/serve#post-apiv1modelsmodel_idpromote)
- [`POST /api/v1/endpoints/{slug}/predict`](../api/serve#post-apiv1endpointsslugpredict)

Screenshots and recordings may show an earlier appearance; regenerate them before publishing visual documentation for a release.
