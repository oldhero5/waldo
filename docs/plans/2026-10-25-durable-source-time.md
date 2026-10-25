# Durable native frame assessments implementation plan

> Use `superpowers:subagent-driven-development` for the implementation and a
> fresh independent session for review. Track each check below before handoff.

**Goal:** Preserve which native video samples were assessed, including samples
with no detections, and return their source times through an authorized API.

**Architecture:** Add a small assessment table beside image frames. Commit its
rows with each video's annotations and completion record. Keep the existing
native decoder, inference adapter, queue, storage, review and export contracts.

**Tech stack:** Python, SQLAlchemy, PostgreSQL, Alembic, FastAPI, Celery and pytest.

**Spec:** [Accepted assessment scope](2026-10-09-delivery.md#wave-1-durable-native-frame-assessments)
and [delivery wave 3](2026-10-10-next-waves.md#wave-3-store-durable-native-assessments).
The owner authorized this wave on October 25, 2026, after approving PR #24.
PR #24 merged as `46e4d5f0d95f5122a054b7b83130f7eb3c63825b`.
Work on `feat/durable-source-time` in the existing dedicated checkout.

## Global constraints and data contract

- Follow `AGENTS.md` and `CONTRIBUTING.md`. Use Sol for implementation and
  independent review. Root owns commits, services and final qualification.
- Keep CUDA publication blocked. No release, image push, deployment, secret
  change, new UI, model/default/provider/seed change or training in this wave.
- Keep private videos and derived evidence out of Git, build contexts, CI and
  public reports. Synthetic data belongs in CI. Local videos are development
  data, not a holdout; no model-accuracy result is inferred from them.
- Use existing video and job UUIDs. A job identifies an original inference run;
  later dataset edits and combinations do not create new assessments.
- Add `NativeFrameAssessment` with UUID `id`, UUID `job_id`/`video_id` foreign
  keys, and unique `(job_id, video_id, source_frame_index)` ownership. The native
  source index is a nonnegative integer in presentation order. It is not an
  FFmpeg resampling ordinal. Index `video_id`; the unique index covers job reads.
- Persist finite double `timestamp_s`, `timestamp_method` restricted to
  `source_pts` or `frame_index/fps`, nullable positive finite `frame_duration_s`,
  positive integer `source_width`/`source_height`, and nonnegative integer
  `detection_count`. Enforce constraints in PostgreSQL and validate inference
  output before persistence. Reject Boolean values where an integer is required.
- Seconds use the existing container-start origin. Finite negative times are
  permitted. These values are not raw PTS ticks, recording UTC or geolocation.
  Recorder GPS cannot establish an observed object's position.
- Detection count is the original inference count. Review edits, rejection,
  class deletion and annotation transfer do not change it. Zero means assessed
  with no detections; it is not ground truth. A missing row means unknown.
- Do not add `frame_id`, change `Frame` nullability, or create images for empty
  assessments. Existing positive images and annotation links remain unchanged.
- Add one revision after `4e5f6a7b8c9d`. Do not backfill historical rows from JSON
  summary arrays. Keep those arrays for compatibility. Retain the additive table
  if application code is rolled back; do not drop persisted evidence to roll back.
- New clip completion metadata carries `native_assessment_version=1`. Assessment
  rows, annotations and that completion record commit or roll back together.
- Completed deliveries and completed clips on partial retry retain evidence,
  including edited annotation IDs and export behavior. Historical completed
  clips stay reusable even when their durable assessment coverage is unavailable.
- Use a short job-row lock, refresh and completion recheck before writes. Keep
  inference outside the lock. Reconcile the saved completion record before a
  stale delivery writes evidence or status. Do not introduce a generic worker
  serialization framework or claim exactly-once inference.
- Write plain technical English, roughly 80% ASD-STE100.

## Paired decisions

Root and Sol `/root/assessment_design` reaffirmed a separate table. Reusing
`Frame` would require fake or nullable image objects; summary JSON cannot enforce
native-frame uniqueness. Existing inference already returns empty samples,
source times and dimensions, so the feature can reuse that boundary.

The read endpoint reports only the requested job's own rows. It never copies or
aggregates parent or merged-run assessments. A copied dataset has no new native
rows. An add-class child can have real rows even though it has a parent. A master
with merged-in runs retains its own rows but reports partial availability: those
rows do not cover all evidence now visible in the combined dataset.

For an unmerged job, annotation timing can prefer durable provenance only when
job, video, native index and timestamp match. Combined jobs keep the existing
summary traversal and ambiguity-to-`unknown` behavior. Their annotations can
come from another run at the same video/index. Existing response fields stay
compatible with the source inspector. Empty-frame UI remains a later feature.

## Review focus

1. A completed empty frame must survive reopen without a fake image. Task 1
   tests positive and zero-detection storage, database constraints and rollback.
2. A stale delivery must not erase a review or change a completed job to failed.
   Task 1 tests sequential retry and coordinated independent PostgreSQL writers.
3. Legacy, copied and combined jobs must not claim complete native evidence.
   Task 1 tests availability and source-run retention after dataset operations.
4. An owned job linked to a foreign video must not reveal that video. Task 1
   tests both ownership paths, invalid cursors and deterministic page boundaries.
5. VFR samples, negative relative times and FPS fallback must retain their real
   meaning. Task 1 tests them synthetically; Task 2 checks local development media.

## Task 1: Persist and serve native assessments

**Files:**

- Modify `lib/db.py`, `labeler/video_labeler.py`, `lib/authorization.py` and
  `app/api/review.py`; add one Alembic revision under `alembic/versions/`.
  Guard the separate retry status write in `lib/tasks.py` with the same
  completed-job check. It can otherwise undo the native pipeline's protection.
- Add focused `tests/test_native_assessments.py` and
  `tests/test_assessment_api.py`. Extend `tests/test_native_retry_integrity.py`,
  `tests/test_review_timing.py`, `tests/test_migrations.py` and
  `tests/test_worker_integration.py` where their existing fixtures fit.
  Update the native mocks in `tests/test_labeling_processing_integrity.py` for
  the lock query and required provenance without weakening their assertions.
- Extend only the necessary test harness in `tests/conftest.py` and
  `tests/worker_integration_worker.py` for a native mode. Linux currently routes
  the worker test through text inference; that path cannot prove native storage.
- Root owns this plan and documentation. No unrelated refactoring or commits.

**Interfaces:**

- Consume `process_video_native` results with native `frame_idx`, `timestamp_s`,
  `timestamp_method`, optional `frame_duration_s`, decoded `width`/`height` and
  `detections`. Do not change model inputs or existing inference defaults.
- Produce the `NativeFrameAssessment` ORM model and
  `GET /api/v1/jobs/{job_id}/assessments` in the existing review router.
- Endpoint query: `limit=100`, allowed 1–500; optional `cursor` at most 256
  characters. Cursor is URL-safe base64 JSON with `job_id`, `video_id` and
  `source_frame_index`. Require valid UUID strings and an integer index from 0
  through 2147483647; reject booleans, malformed fields and a different job ID.
- Response: `job_id`, `coverage_status`, `coverage_scope="sampled_native_frames"`,
  `items`, `next_cursor`. Each item exposes its assessment ID, source video ID,
  native index, timing, duration, dimensions and original detection count.
- Order by `(video_id, source_frame_index)`. Fetch `limit + 1` rows to determine
  the next cursor. Authorize the job before cursor decoding; scope rows through
  both job and video ownership. Invalid cursors return 400; hidden jobs return 404.
- Coverage is `unavailable` with no own scoped rows. With rows it is `partial`
  if the job is unfinished, metadata is insufficient, any completed clip lacks
  version 1 or matching row counts, or merged-in runs exist. It is `available`
  only for a completed unmerged job whose eligible completed clips all match.
  Compare counts to `sampled_frames`, never current annotations. Return retained
  rows even when partial. No value claims full-video or model completeness.
- Pagination is not a snapshot while work continues. Clients must restart a
  scan after completion to obtain every committed row. Cursor is a position,
  not an authorization token; no signing service is needed.

- [x] Write and run failing persistence cases for positive/empty samples,
  reopened sessions, uniqueness, invalid values, foreign keys, and atomic
  rollback of assessments, observations and clip completion.
- [x] Implement the model, additive migration and native writer. Test deletion
  cleanup or database cascades. Do not write empty image artifacts.
- [x] Extend retry tests before implementation: completed and partial retries
  keep reviewed IDs/geometry/status; orphan assessments without clip completion
  stop replacement; a concurrent stale writer reuses the winner and cannot
  regress completed state. Use real PostgreSQL sessions for lock behavior.
- [x] Implement the small commit-time recheck and status protection in the
  native pipeline. Keep existing text/exemplar paths and exports unchanged.
- [x] Write and run failing API tests for exact/fallback timing, negative times,
  nullable duration, complete/partial/legacy/copied/combined coverage, retained
  source-child rows, page order/bounds, cursor validation and foreign job/video
  denial. Then implement the endpoint and compatible annotation timing lookup.
- [x] Add a previous-populated-head migration test in a disposable schema. Verify
  old jobs, annotations and export pointers survive with no fabricated rows.
- [x] Add native mode to the test-only separate worker. Keep real task dispatch,
  queue, decoding, storage, database, review and exports; replace inference only.
  Test empty and positive rows, review/partial retry and graceful worker restart.
  Required service mode must fail rather than skip when its services are absent.
- [x] Verify real synthetic VFR persistence and explicit FPS fallback. Preserve
  the decoded-count/early-stop tests; metadata counts are not decoded counts.
- [x] Run focused tests, Ruff and whitespace checks. Report RED/GREEN commands,
  results, skips, assumptions, files and limits. Root will run full isolated
  service checks and arrange a fresh independent review of the final head.

**Acceptance:** Durable positive and empty records survive reopen and redelivery;
edits remain intact; authorization and availability remain truthful; migration
and service tests pass. No claim of real GPU inference follows from a fake worker.

## Task 2: Qualify and document the completed feature

Root owns disposable services, local media qualification, affected documentation,
coherent commits, PR creation and final checks. Keep the current checkout and its
locked dependencies; no extra worktree or dependency upgrade is required.

- [x] Run the new tests on disposable PostgreSQL/Redis/MinIO with a separate
  Linux worker.
- [ ] Run required migration, backend and browser CI on the PR head. Record
  exact-head results in the PR body.
- [x] Run a bounded source-time check on the private local videos. Use the
  current native path and cached MLX weights offline where hardware permits.
  Record code/model/configuration, elapsed time, sampling and limits locally.
  Test data must not leave the machine. Report any hardware test limit precisely.
- [x] Update only relevant API/data-model/testing docs and the delivery status.
  Include sample responses, cursor semantics, original inference counts, legacy
  availability, the migration/rollback policy and a short human test plan.
- [ ] Check the complete diff, source exclusions and secret scan. Commit focused
  groups; create and attach one PR. Set both approval attestations pending.
- [ ] Obtain a fresh independent Sol review of the exact head, covering spec
  compliance, data integrity, authorization, concurrency and simplicity. Fix
  findings, re-test and re-review material changes.
- [ ] Return the PR only after local acceptance, hosted CI and independent review
  pass. Keep its human approval pending. Do not merge or publish this wave.

**Human test:** Submit a short native labeling job on a trusted local build.
Read its assessment pages, including zero-detection samples; compare source times
with the saved source. Edit one annotation and retry; its ID and edit must remain,
and original assessment counts must not change. Historical jobs show unavailable
coverage. Review the synthetic service evidence if no local model run is possible.

**Rollback:** Build and review the prior application modules while retaining the
new migration file, schema revision, table and stored rows. Startup runs Alembic
and must recognize the retained revision. Use a forward repair if needed; the
migration refuses downgrade. No UI or model rollback is
required. Release qualification and CUDA publication remain separate blocked gates.


## Validation recorded before PR review

- Initial missing storage/API cases failed 32 times before implementation.
  Later regressions reproduced stale-delivery review loss, status regression,
  unsafe downgrade and missing native worker records before their fixes.
- Disposable PostgreSQL/Redis/MinIO checks passed 64 tests with zero skips,
  including concurrency, native worker retry/restart and VFR persistence.
- A full backend run exposed three test-harness defects: an old schema-head
  assertion, SQLite converting a valid UUID to a number, and an optional HEIF
  loader changing a corrupt-PNG failure. Root and Sol chose test-only repairs:
  update the expected revision, store SQLite test UUIDs as text, and use the
  original Pillow decoder in that disk-I/O fixture. The corrupt image and
  rollback assertions remain. No dependency was added. The affected suite then
  passed 234 tests with zero skips. A new full run is required before the PR.
- All 127 synthetic browser tests passed. UI build/lint, Python lint/format,
  documentation build and whitespace checks passed.
- The seven local development videos passed source-timing probes over 21,839
  decoded frames. One 4.23-second clip ran through a real offline native worker
  with SAM 3.1 revision `a992e302ea9b0f03f41dfd93414a4fd0e818f65b`, MLX 0.31.2
  and mlx-vlm 0.5.0. Prompt `camera`, threshold 0.35 and sample rate 1 Hz produced
  five empty assessments with source-PTS times. Pagination and redelivery kept
  their IDs/counts. Total elapsed time was 22.93 seconds. No accuracy claim follows.

The test-only corrections also touch `tests/test_postgres_upgrade.py`,
`tests/test_resource_authorization.py`, `tests/test_text_resource_bounds.py` and
`tests/conftest.py`. They make the full regression gate deterministic while
preserving application behavior. The PR body records the final full-suite,
exact-commit CI, independent review and owner approval. Those external gates
remain open at this implementation commit.

## Independent review correction

Sol `/root/assessment_review` reproduced a finalization race with independent
PostgreSQL sessions. A second delivery removed a failed clip from the summary
while retrying it. The first delivery then reported completion because no failed
entry remained, and the second discarded its result after seeing that status.

Root and the implementer chose two small guards: retain eligible incomplete
records until a new clip transaction replaces them, and require a completed
record for every eligible video before final completion. Counters use completed
clips only. New tests cover a second delivery that succeeds or fails and an
incomplete summary. Overlapping deliveries can report partial evidence while
another attempt finishes; this change does not track global worker activity.
The repaired exact commit requires a new full test run and independent review.
