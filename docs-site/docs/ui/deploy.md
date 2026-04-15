---
title: Deploy
sidebar_position: 5
---

# Deploy Page

Route: `/deploy` — Source: [`ui/src/pages/DeployPage.tsx`](https://github.com/your-org/waldo/blob/main/ui/src/pages/DeployPage.tsx)

Promote trained models to a serving endpoint, configure named endpoints, and watch live prediction metrics.

## Activate a model

Click the star icon next to any model in the registry to mark it active. The default `/predict/*` endpoints will use it from the next request.

## Named endpoints

Create endpoints with their own slug, model pin, and routing rules. Useful for:

- **Blue/green deploys** — run new model on `/endpoints/staging/predict` until you're confident, then promote.
- **Per-customer models** — `/endpoints/customer-x/predict` pinned to a custom-trained variant.
- **A/B testing** — see [Experiments](../api/serve#experiments).

## Edge devices

The bottom panel lists registered Jetson and Pi+TPU devices, their last heartbeat, battery, and the model version they're running. See [edge deployment](../deployment/edge).

![Deploy page](/img/screenshots/deploy.png)
