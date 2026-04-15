---
title: UI Overview
sidebar_position: 1
---

# UI Overview

The web UI is a Vite + React 19 + Tailwind 4 SPA. Source lives in [`ui/src`](https://github.com/your-org/waldo/tree/main/ui/src).

## Pages

| Page | Route | Source |
| --- | --- | --- |
| Login | `/login` | `pages/LoginPage.tsx` |
| Register | `/register` | `pages/RegisterPage.tsx` |
| Dashboard | `/` | `pages/DashboardPage.tsx` |
| Datasets | `/datasets` | `pages/DatasetsPage.tsx` |
| Upload | `/upload` | `pages/UploadPage.tsx` |
| Label | `/label` | `pages/LabelPage.tsx` |
| Review | `/review` | `pages/ReviewPage.tsx` |
| Jobs | `/jobs` | `pages/JobsPage.tsx` |
| Train | `/train` | `pages/TrainPage.tsx` |
| Experiments | `/experiments` | `pages/ExperimentsPage.tsx` |
| Deploy | `/deploy` | `pages/DeployPage.tsx` |
| Workflows | `/workflows` | `pages/WorkflowsPage.tsx` |
| Workflow Editor | `/workflows/editor/:slug?` | `pages/WorkflowEditorPage.tsx` |
| Collections | `/collections` | `pages/CollectionsPage.tsx` |
| Agent | `/agent` | `pages/AgentPage.tsx` |
| Settings | `/settings` | `pages/SettingsPage.tsx` |

## State + data fetching

- **Server state:** TanStack Query (`@tanstack/react-query`). Every API call is wrapped in `useQuery` / `useMutation` from `ui/src/api.ts`.
- **Local state:** Zustand for cross-page state (active workspace, theme, sidebar collapsed).
- **WebSocket:** A single connection per session, multiplexed by the server. Subscribed via `useWebSocket()`.

## Design system

Pretext design system: serif headings, monospace eyebrow labels, CSS variables only (no raw Tailwind colors). Conventions live in `ui/src/index.css`.

## Screenshots

Each page has a screenshot in `static/img/screenshots/<page>.png`. To regenerate:

```bash
docker compose -f docker-compose.docs.yml run --rm docs-screenshots
```

The script logs in with `WALDO_USER` / `WALDO_PASSWORD` from your environment and captures every page in the table above.
