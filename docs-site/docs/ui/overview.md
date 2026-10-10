---
title: UI Overview
sidebar_position: 1
---

import Demo from "@site/src/components/Demo";

# UI Overview

The web UI is a Vite + React 19 + Tailwind 4 SPA. Source lives in [`ui/src`](https://github.com/oldhero5/waldo/tree/main/ui/src).

![Waldo dashboard](/img/screenshots/dashboard.png)

The dashboard is the landing page after sign-in. It shows your video count, total annotations, models in the registry, recent activity, and quick links to the next sensible action.

<Demo
  src="/img/recordings/tour.mp4"
  poster="/img/recordings/tour.poster.jpg"
  caption="Quick tour of the main pages."
/>

## Pages

| Page | Route | Source |
| --- | --- | --- |
| Login | `/login` | `pages/LoginPage.tsx` |
| Register | `/register` | `pages/RegisterPage.tsx` |
| Dashboard | `/` | `pages/DashboardPage.tsx` |
| Upload | `/upload` | `pages/UploadPage.tsx` |
| Collections | `/collections` | `pages/CollectionsPage.tsx` |
| Datasets | `/datasets` | `pages/DatasetsPage.tsx` |
| Label | `/label/:videoId` | `pages/LabelPage.tsx` |
| Review | `/review/:jobId` | `pages/ReviewPage.tsx` |
| Playground | `/playground` | `pages/PlaygroundPage.tsx` |
| Jobs | `/jobs` | `pages/JobsPage.tsx` |
| Train | `/train/:jobId` | `pages/TrainPage.tsx` |
| Experiments | `/experiments` | `pages/ExperimentsPage.tsx` |
| Workflows | `/workflows` | `pages/WorkflowsPage.tsx` |
| Workflow Editor | `/workflows/new`, `/workflows/:id` | `pages/WorkflowEditorPage.tsx` |
| Deploy | `/deploy/:tab` | `pages/DeployPage.tsx` |
| Agent | `/agent` | `pages/AgentPage.tsx` |
| Settings | `/settings` | `pages/SettingsPage.tsx` |

The deploy page has four tabs (`endpoints`, `test`, `models`, `monitor`) reachable via `/deploy/<tab>`. `/deploy` opens Endpoints. There is no Edge tab in the current client.

## State + data fetching

- **Server state:** TanStack Query caches lists and resource details. API helpers in `ui/src/api.ts` also support direct requests, mutations, job polling, and streaming.
- **Authentication:** `AuthContext` manages the signed-in user and token. The sidebar displays the workspace returned by `/api/v1/auth/me`; it does not offer workspace switching. Sign-in, registration, sign-out, and invalid-token handling cancel pending queries and clear the query cache. The app shell remounts when the authenticated user or workspace changes.
- **Local state:** React state and refs manage controls and selection. Some comparison-session metadata is kept in `sessionStorage` and is removed on authentication transitions. Zustand is installed but is not used by current client source.
- **Streaming:** Training opens a resource-specific WebSocket (`/ws/training/<runId>`), video inference uses `/ws/predict/<sessionId>`, and the agent streams fetch responses. There is no shared session-wide WebSocket hook.

## Appearance and components

The client uses charcoal surfaces, readable gray text, and light primary controls with dark labels. Color identifies status, detections, annotation classes, and chart series. System sans typography handles headings and interface text; monospace is available for code and technical data. Reduced-motion preferences disable CSS animation and transitions. The dashboard presents workspace activity without an ornamental canvas animation.

Tokens and compatibility styles live in `ui/src/index.css`. Use `--text-on-accent` for labels on solid accent backgrounds, and preserve semantic status and evidence colors. Existing pages use a mix of tokens and Tailwind utilities; the client does not have a general Button/Card/form-primitives library.

Reusable components under `ui/src/components/` include `Sidebar`, `AppShell`, `PageLayout`, `Accordion`, `TaskSelector`, `RichText`, `AnnotationCanvas`, `AnnotationOverlay`, `ClickCanvas`, `LineChart`, `StatsPanel`, and `AgentPanel`. Deploy also contains domain components such as `TrackTimeline` and image/video comparison views.

## Screenshots & flow recordings

Screenshots and recordings are captured with Playwright. Existing media may reflect an earlier interface; regenerate it against the current client before publishing visual documentation. The script lives in [`docs-site/scripts/screenshots.spec.ts`](https://github.com/oldhero5/waldo/blob/main/docs-site/scripts/screenshots.spec.ts) and is paired with [`recordings.spec.ts`](https://github.com/oldhero5/waldo/blob/main/docs-site/scripts/recordings.spec.ts) for short flow videos.

To regenerate everything against your local Waldo:

```bash
cd docs-site
npm ci
npx playwright install chromium
export WALDO_BASE_URL="http://localhost:8000"
export WALDO_USER="your-account@example.com"
export WALDO_PASSWORD="your-password"
npm run screenshots
npx playwright test scripts/recordings.spec.ts
```

The screenshots scrape every route, the recording script captures short walkthroughs of the most-used flows.
