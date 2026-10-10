---
title: Video evidence architecture and roadmap
sidebar_position: 5
---

# Video evidence architecture and roadmap

Waldo should become a video evidence workbench: ask a question, retrieve relevant
observations, inspect the original footage, and understand where and when the
observations occurred. Keep the existing Python workers and React application.
The largest gap is a trustworthy evidence model, not a different frontend
framework or a larger chat model.

Updated October 9, 2026 to reflect the current local implementation; the original
architecture assessment started from commit `868ec56`. The first target is
**physical traffic cameras visible in uploaded footage**. Searching
feeds from a directory of known cameras is a different future workflow.

## What is solid enough to build on

| Area | Useful foundation | Qualification |
| --- | --- | --- |
| API and workers | FastAPI, Celery, Redis, separate labeling/training queues, asynchronous polling and streamed progress | Sampled coverage and partial outcomes are now stored; durable cancellation and resumable chunk indexing remain. |
| Storage | PostgreSQL metadata, Alembic migrations, MinIO video/model artifacts | Retain these; extend the schema for observations and spatial/time provenance. |
| Perception | SAM 3 video sessions on PyTorch; a community SAM 3.1 MLX checkpoint; YOLO training/inference; tiled detection | Backend capability and evidence fidelity differ. Neither model name nor a preview proves production recall. |
| Human review | Annotation acceptance/editing, preview overlays, representative images and a track timeline | Reuse these components for evidence inspection. Preview promotion now keeps the displayed run settings. |
| Agent | Small LangGraph model/tool loop, request context, read/action tool separation, SSE events | Shared application authorization now covers the inspected REST/workflow/tool paths; keep it outside model decisions. |
| Frontend | React 19, TypeScript, Vite, Tailwind, TanStack Query, lazy routes and React Flow | “Pretext” describes the existing CSS aesthetic, not a separate installed UI framework. |
| Engineering | Lockfiles, CI, migration tests, unit tests, secret scanning and extensive docs | Some docs describe unimplemented behavior; some tests depend on services or obsolete UI assumptions. |

These are architectural strengths, not certification that every path is correct
or production-ready. The audit read the documentation corpus across specialist
reviews and the coordinating review, inspected the main implementation paths and
all 20 checked-in UI screenshots. It was not an exhaustive runtime test of every
file, installer, GPU, edge device or recorded walkthrough.

## Implemented cleanup

The local patch implements the following foundations. These are source changes
with focused automated verification, not a production deployment or certification
of model accuracy.

| Area | Changed behavior | Practical limit |
| --- | --- | --- |
| Authorization | Shared principal, current workspace membership and API-key scope checks; exact resource ancestry; separate installation admin; scoped REST, agent, workflows, task polling and WebSockets; signed exact-object downloads | Legacy unowned records require explicit operator assignment. Signed media capabilities expire after 15 minutes rather than being instantly revoked. Long-running streams do not continuously reauthorize. |
| Video evidence | Recompute per-frame features; retain brief sightings and sampled native observations; store local track IDs, threshold, sampling rate, per-video outcomes and assessed timestamps; native and preview paths preserve source PTS when available; partial jobs stay partial | FFmpeg-resampled frames can have approximate FPS-derived times, and missing PTS is explicitly approximate. Source PTS is not yet part of a durable observation/run schema; physical-asset identity and exhaustive coverage claims remain unsupported. PyTorch tracking IDs are not exposed by the existing engine. |
| Training data | Source-video grouped exports, collision-safe names and manifests; training-only augmentation with correct geometry; remove unrelated global feedback matching; immutable reviewed export snapshots; checkpoint resume fixes | Grouping by site/drive/date needs new metadata. One-video exports have no independent validation group; training rejects them with an actionable message. Historical datasets and mixed-geometry annotations are not repaired automatically. |
| Workflows | Validate graph structure and required inputs before execution; skip closed/failed branches; live workspace authority; owned model-pool selection; bounded public webhook destinations | Workflows remain image-oriented. Port metadata is not a complete runtime payload schema, and queued work is not a general durable agent runtime. |
| Chat providers | Shared OpenAI, Anthropic, Ollama, vLLM and OpenRouter adapters; configured model allowlist; workspace-admin key form and connection test; explicit cloud-text choice; safe public errors | Browser-entered settings are process-local and reset on restart. Environment configuration is the persistent operator path. Ollama `gemma4:12b-mlx` completed an actual local tool-use smoke; other live accounts and model-specific tool behavior still need qualification. |
| Interface | Neutral charcoal/gray/white styling; honest workspace identity; account cache isolation; immutable preview provenance; visible sampled/partial coverage and export-before-training behavior | The map/search/evidence workspace below is still a future feature. |
| Deployment and schema | Loopback service ports, consistent credentials, optional Ollama profile and one model setting; append-only migrations for authority, ownership, evidence configuration and the missing deployment slug | Local OrbStack API/PostgreSQL/Redis/MinIO with native MLX/MPS workers and Ollama were exercised. Live SAM image/video handoff, comparison completion/failure, repeated terminal polling and input cleanup passed. This does not qualify outage recovery, production, all providers, CUDA, or physical-camera accuracy. Migrations were checked against disposable PostgreSQL and applied to the new local test installation, not an existing populated user database. |

Training exports now carry a manifest and are immutable snapshots. Native and
text-prompt labeling require review/export before training. Annotation changes invalidate the job’s
current export; an already-created training run retains its immutable snapshot. A download in another dataset format does
not silently replace the job's training artifact. Existing datasets should be
re-exported or regenerated before trusting evaluation numbers affected by prior
split leakage, geometry or brief-sighting loss.

## Remaining release gates and product work

| Priority | Remaining gap | Required outcome |
| --- | --- | --- |
| P1 evidence provenance | Native and preview paths preserve source PTS when available, and VFR tracking has fixture coverage; FFmpeg-resampled output remains approximate and source time is not in a durable observation/run schema | Add canonical durable source-time records, chunk idempotency and restart-safe coverage without silently dropping observations. |
| P1 model qualification | Neither official SAM 3.1 video propagation nor alternatives have been benchmarked on physical cameras | Freeze a site-separated holdout, compare the shortlist below and measure brief/tiny-camera event recall at a fixed total processing budget. |
| P1 integration qualification | Full backend suite (445 passed, 11 skipped) and UI suite (40 passed with mock API plus a real decoded-video fixture) passed; local OrbStack API/PostgreSQL/Redis/MinIO and native MLX/MPS/Ollama paths were exercised | Live shared-MinIO handoff and Redis terminal polling passed. Still run outage/cancellation/restart tests and concurrent model selection; qualify remaining live provider contracts and intended GPU/OS targets. These checks do not establish production readiness or physical-camera accuracy. |
| P2 provider operations | Runtime keys are temporary; arbitrary configured models may not support the required tools | Add encrypted durable workspace credentials if needed, account budgets, usage records, provider-specific routing metadata and model capability qualification. |
| P2 geospatial evidence | Recorder telemetry, calibration, asset hypotheses and spatial indexes are not implemented | Add source-linked pose/uncertainty and PostGIS queries; never substitute recorder position for an observed camera position. |
| P2 search experience | Current Library/Review/Deploy pages are not a unified natural-language evidence browser | Implement synchronized map, cards, source player and disjoint timeline intervals with clear unknown-location and partial-coverage states. |
| P2 operational access | Tokens remain browser-local; login throttling and continuous stream reauthorization are absent | Decide deployment exposure and add the appropriate session/abuse controls before a shared public service. |

Do not use “all cameras found” as a product claim while sampling gaps, recall
uncertainty and unprocessed footage remain relevant.

## The traffic camera experience

The user imports footage and any telemetry, selects an area on a map, and asks
“Show me all traffic cameras in this area.” Waldo displays the interpreted area,
time scope, selected footage and indexing coverage before returning evidence.
Search should work for a new concept without first requiring a YOLO training run.

1. Validate the workspace and compile the request into a typed search plan.
2. Find footage whose known recording trajectory or surveyed coverage intersects
   the area. Keep unknown-location footage visible as unlocated evidence.
3. Retrieve candidates using concept labels, visual/text embeddings and optional
   OCR. Expand candidates into neighboring time windows; offer a thorough scan
   when existing coverage is insufficient. Cheap retrieval must not silently
   exclude the only brief appearance.
4. Detect candidates with a qualified prompted or camera-specific detector;
   add segmentation/tracking where useful and use full-resolution tiles for small objects and temporal propagation for continuity. Preserve
   observations even when confidence is low enough to require review.
5. Group observations into tracks, then propose physical-asset associations
   using time, geometry and appearance. Keep merges reversible and evidence-linked.
6. Return map features, thumbnail cards and disjoint visible video intervals.
   Selecting any one selects the same evidence in all three views. Clicking an
   interval seeks to the original footage with an overlay and surrounding context.

**A visible object is not automatically a geolocated object.** GoPro GPS locates
the recorder, not the physical traffic camera seen in its image. Keep the recorder
trajectory separate from asset-location claims. A detection or mask supplies
image evidence; calibrated image geometry can supply a bearing. A world bearing
also needs time-aligned position and Earth-referenced camera orientation. A
position needs another constraint: measured depth, a justified surveyed surface,
static multi-view triangulation with useful parallax, or independently verified
reference information.

GoPro localization must qualify the actual camera, capture mode and rendered
image: lens distortion/fisheye, stabilization, horizon control, crop/zoom,
telemetry timing and camera-to-body transforms all matter. Capture-relative
orientation must not silently become north-referenced heading. Projecting an
elevated camera housing onto a ground plane gives the wrong physical point;
moving objects need a different model from fixed roadside cameras. The proposed
[geolocation design](geolocation-design) defines these cases, evidence contracts
and staged acceptance tests. This engine and its schema are not implemented.

Show unknown, estimated and verified asset locations with their method,
assumptions and uncertainty. Preserve unlocated evidence in area searches;
uncertainty that overlaps the requested area is a possible match, not grounds for
exclusion. Recorder-path intersection is a retrieval hint, not a proof that every
visible object lies inside or outside an area. “All” means all detections within
stated footage and coverage, with measured recall; it cannot promise all cameras
that exist in the world.

## Evidence architecture

Keep PostgreSQL as the record of truth and MinIO for source media, masks, crops
and clips. Add PostGIS for indexed area, radius and trajectory queries. Introduce
vectors for semantic candidate retrieval after exact permission/time/location
filters; no additional vector service is required for the first version.

| Proposed record | Essential fields and rules |
| --- | --- |
| Media | Workspace, immutable checksum/object version, stream/timebase, duration, capture time with timezone/uncertainty, telemetry/calibration references. Upload time stays separate. |
| Sensor pose | Media/source timestamp, recorder position and coordinate reference system, orientation, calibration, accuracy/covariance and source. |
| Processing run and coverage | Model/backend/checkpoint/code/config versions, sampled timestamps, covered intervals, gaps, failures, cancellation state and cost. |
| Observation | Source media + PTS, class concept, bbox/mask and coordinate space, crop/source pointers, detector score, run and local-track key, review revision. |
| Track | Run-scoped identity hypothesis, observations, disjoint visible intervals, representative image pointer and association quality. |
| Physical asset | Optional verified/estimated position or region, method and uncertainty, supporting tracks, review state and merge history. |
| Relation | Typed relation, participating evidence/asset IDs, spatial frame, valid time interval, confidence and supporting observations. |

Use GiST spatial indexes and source/time indexes; choose partitioning from
measured volume. Workers process resumable overlapping chunks and reconcile
boundary identities. Uniqueness constraints and idempotency keys prevent retry
duplicates. Return signed or authorized media references, never arbitrary storage
paths supplied by the model.

Start with deterministic relations: co-visible, before/after, repeated at the
same site and inside a verified area. World distance and movement require
calibrated geometry. A tracker ID alone does not establish the same physical
object across different videos.

## Perception backend selection

Compare SAM 3.1 with YOLOE-26, a fine-tuned RF-DETR/YOLO26 detector and a
Grounding DINO + SAM 2.1 baseline. The [model comparison](perception-selection)
explains the tradeoffs and held-out evaluation gate. Do not switch production
backends based on public aggregate benchmarks alone.

The CUDA path uses `facebook/sam3` through Transformers video sessions. The
native Mac path loads `mlx-community/sam3.1-bf16`; a moving-bus fixture exercised
native SAM video processing plus preview, full labeling, review and export with
source PTS. This fixture smoke does not qualify physical-camera recall or
accuracy, nor Meta's SAM 3.1 Object Multiplex video pipeline.

Meta's March 27 release introduces shared multi-object tracking and new
checkpoints. Its reported speedup is hardware/workload-specific, with mixed
results on some video benchmarks. Treat it as a candidate to benchmark, not a
promised improvement for Waldo. [Official release notes](https://github.com/facebookresearch/sam3/blob/main/RELEASE_SAM3p1.md).

Create a capability-based perception adapter: image detection, video-session
creation, prompting, propagation and close. Store stable run/object identities
at this boundary. Establish an official CUDA baseline in an isolated worker
environment; qualify MLX against the same fixtures. Upstream currently specifies
Python 3.12+, PyTorch 2.7+ and CUDA 12.6+; Waldo's existing environment needs a
deliberate compatibility check. Keep a tested fallback until the new backend
passes. [Meta installation](https://github.com/facebookresearch/sam3),
[MLX checkpoint](https://huggingface.co/mlx-community/sam3.1-bf16).

## Chat providers and agent design

LangGraph now uses a shared server-side text-provider boundary with workflow LLM
blocks. It snapshots the provider configuration for a request, checks configured
models and deadlines, and requires explicit cloud-text permission. Current chat
sends text/tool results; it does not upload images or videos. A separate visual
reasoning adapter, capability records, durable secret references and budgets remain
future work.

| Provider | Adapter and acceptance requirement |
| --- | --- |
| OpenAI | Responses/function-calling adapter; preserve tool IDs, streaming and usage. |
| Anthropic | Messages adapter; preserve tool-use/tool-result blocks and IDs. |
| Ollama | Retain the local adapter; `gemma4:12b-mlx` completed an actual local tool-use smoke; qualify other selected models individually. |
| vLLM | OpenAI-compatible endpoint; qualify the model, parser and chat template together. |
| OpenRouter | Compatible adapter with explicit routing/fallback policy; record actual downstream provider. |

References: [OpenAI tools](https://developers.openai.com/api/docs/guides/function-calling),
[Anthropic tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview),
[Ollama](https://docs.ollama.com/capabilities/tool-calling),
[vLLM](https://docs.vllm.ai/en/latest/features/tool_calling/),
[OpenRouter routing](https://openrouter.ai/docs/guides/routing/provider-selection).

Settings lets a workspace administrator enter a key and model, explicitly allow
cloud text and run a small connection test before applying a temporary override.
Keys are write-only and excluded from workflow JSON and browser persistence. This
test checks a text response, not image capability; the local `gemma4:12b-mlx`
tool-use smoke is separate evidence for that exact model. Overrides reset on API
restart and are not shared across API processes or workers; use environment
configuration for a durable deployment. Encrypted workspace storage and rotation
are future work. There is no silent local-to-cloud fallback. A hosted-only
installation can omit the optional Ollama profile.

Use one orchestrator with typed tools such as search evidence, inspect an
observation, expand a time window, request an indexing job and explain coverage.
Run parallel video work in bounded queues rather than creating an LLM agent per
frame. The model proposes a plan; ordinary code performs permission checks,
spatial/time arithmetic, count aggregation and side effects. Record tool-call IDs,
arguments, evidence, usage and results. Add cancellation, deadlines, cost limits
and idempotency. Treat OCR, filenames and transcripts as untrusted data.

## Scope decisions

System 1 / Jev / Decisions experiments are removed from the active plan at the
user's request. Desktop packaging and the Tauri pilot are on hold. The perception
model decision remains provisional pending the [SAM 3.1 and alternatives comparison](perception-selection).

## Design direction

Use charcoal canvas `#181818`, surfaces `#222222`/`#2c2c2c`, white primary text,
readable gray secondary text and light primary buttons with dark labels. Reserve
color for detection overlays, selection and meaningful status. Prefer system
sans typography, tabular timecodes, restrained borders and limited shadows.

Make Search the primary workspace: a scope/search bar, results column, map and
resizable evidence inspector with a timeline. Library, Review and Models remain
supporting destinations. One evidence selection drives cards, map and player;
the URL retains query, scope, selected evidence and playback time. On small
screens these become views of the same selection, not compressed columns.
Keyboard selection and seeking, visible focus, reduced motion, contrast and
distinct empty/loading/error/partial states are acceptance criteria.

## Delivery plan and agent ownership

The table separates the implemented foundation from the remaining product work.
Acceptance gates describe the intended completed package; partial implementation
does not imply that every gate has passed. Sol handles implementation and routine review;
Luna is suitable for bounded documentation, fixture inventory and mechanical
checks. Reserve stronger reasoning for cross-system decisions, geometry and
final reconciliation. Keep agents on separate files and share small contracts.

| Order | Work package and current status | Owner and dependency | Remaining acceptance gate |
| --- | --- | --- | --- |
| 1 | **Implemented foundation:** shared authorization, scoped routes/workflows/agent/WS/downloads and new migrations | Backend/security Sol; first | Offline role/key/resource matrix and real PostgreSQL ownership checks pass. Extend to deployed concurrent calls, Redis task ownership and stream lifecycle. |
| 2 | **Implemented foundation:** evidence retention, partial outcomes, config/coverage, export/augmentation integrity and retry handling | Vision/data Sol; parallel with 1 | Focused wrong-frame, brief-sighting, filename, split, feedback, failure, geometry and preview regressions pass; VFR/source-PTS fixtures pass where source PTS exists. FFmpeg-resampled timing and durable source-time persistence remain open. Local SAM handoff/polling/cleanup smoke passed; broader model and outage/restart qualification remain. |
| 3 | **Next:** durable evidence schema and retrieval; `lib/db.py`, new Alembic revision, evidence services/routes and indexing tasks | Data Sol; depends on 1–2 | Durable source PTS/timebase and coverage survive retries/restarts; each observation resolves to its correct source/crop; track gaps remain explicit. |
| 4 | **Implemented foundation:** five provider adapters, temporary workspace Settings, environment configuration, shared workflow client and optional local chat | Provider Sol; depends on 1; parallel with 3 | Offline adapter/tool/error/redaction contracts pass. Qualify live accounts, chosen model tool support and failure/disconnect behavior; add durable secrets if required. |
| 5 | **Decision gate:** SAM 3.1, YOLOE-26, RF-DETR/YOLO26 and Grounding DINO comparison; then implement the selected capability adapters | Vision Sol plus architecture review; depends on 2–3 | Separate zero-shot and trained comparisons on one held-out camera corpus; event recall, review burden, total latency, memory and identity continuity on intended hardware. |
| 6 | **Planned:** PostGIS/telemetry/localization and typed spatial/time query API | Geospatial Sol plus geometry review; depends on 3 | Surveyed controls measure position error and uncertainty coverage; missing pose stays unknown; no recorder-GPS-as-asset shortcut. |
| 7 | **Planned:** Search/map/evidence/timeline workspace; extract reusable Playground/player/timeline components | UI Sol; depends on 3/6 contracts; fixtures can start earlier | Map/card/seek selection consistency, deep links, keyboard paths, delayed results, missing location, partial indexing and mobile behavior. |
| 8 | **Planned:** grounded agent tools and answer cards | Agent Sol; depends on 3–7 | Frozen natural-language queries return authorized cited evidence; no invented IDs/locations; bounded job budgets and cancellation. |
| 9 | Docs and release verification | Luna for source-to-doc reconciliation, Sol for integration review; every package | Document actual behavior and effective model versions; run focused regressions, then service-backed integration and GPU/OS qualification before release. |

The first demonstrable milestone is a single imported, telemetry-backed drive:
find physical cameras, retain brief sightings, inspect every match in the source
video, and show verified/estimated/unknown locations distinctly. API chat support
can ship independently after authorization, without waiting for the map UI.

## Evaluation and useful extensions

Curate a held-out physical-camera dataset with small distant targets, different
mounts, night/glare/occlusion, moving footage and hard negatives such as traffic
lights and signs. Split by drive, site and date; neighboring frames must not
bridge training and evaluation. Measure event recall by pixel size and visibility
duration, false positives per video-hour, source-time error, track identity
switches, mask quality, location error/uncertainty, time to first evidence,
throughput and peak memory. Set release thresholds from this baseline and the
cost of missed evidence, not a fabricated accuracy promise.

Useful follow-on analyses include asset appearance/disappearance across repeat
drives, relocation hypotheses, installation-change timelines, road-sign or
infrastructure inventories, calibrated trajectories/geofence events, and maps of
survey coverage and blind spots. “Not seen” must account for viewpoint, occlusion,
weather and incomplete coverage before implying removal. Prefer asset/event
analysis first; cross-video identity remains a hypothesis until supported.

## Installable application decision

**On hold by user decision.** Keep React and Python; no desktop pilot or framework
migration is in the active implementation plan. If revisited, a Tauri shell could
retain React and a Python inference service. Tauri supports a native host and external
binary sidecars; it does not remove Python, model downloads or service lifecycle
work. Validate video codecs, range seeking, map rendering, MLX/CUDA dependencies,
signing, updates and crash recovery on each intended OS.
[Tauri process model](https://v2.tauri.app/concept/process-model/),
[sidecars](https://v2.tauri.app/develop/sidecar/).

Electron is a reasonable alternative when consistent Chromium behavior and
TypeScript desktop tooling matter more than shell footprint. A PWA improves
installation of the client but cannot by itself supervise the existing Python,
database and model stack. A native SwiftUI rewrite is only justified by an
Apple-only product commitment and specific native requirements.
[Electron](https://www.electronjs.org/docs/latest/tutorial/process-model),
[PWA installability](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable),
[SwiftUI](https://developer.apple.com/swiftui/).

If desktop work resumes, first evaluate a client connected to a managed local/remote
worker; consider a self-contained single-user runtime separately. Desktop packaging will
not itself speed inference. No packaging, framework migration or installer
change is part of this cleanup.
