# Verify images before release

> Use `superpowers:subagent-driven-development` for implementation and a fresh
> independent session for final review.

**Goal:** Build each release image once. Test and scan those exact bytes before
Docker Hub receives them. Keep publication blocked when required evidence is absent.

**Architecture:** One main-only candidate workflow builds both images and runs
the optional trusted GPU job. A separate protected workflow publishes the pair.
PR CI checks the guards and a tiny local registry copy without release credentials.

**Stack:** GitHub Actions, Docker BuildKit, OCI, regctl, Trivy, Python and pytest.

**Base:** PR #23 merged as `fe22b4cc06dfb0be731e6487552233dfaa382742` on
October 12, 2026. This is Wave 2 of the [delivery plan](2026-10-10-next-waves.md).
Work on `feat/verified-image-release`. Release and deployment need separate owner
approval; approval to implement this wave does not authorize either action.

## Decisions and limits

The orchestrator and Sol buddy `/root/release_design` chose Open Container
Initiative (OCI) archives. A staging registry would publish images before they
pass and would need credentials during qualification. An archive retains the
image, software bill of materials (SBOM), and build provenance without that access.
Preserve the entire OCI graph when copying it. Record the root index digest,
runnable manifest digest, archive checksum, source commit, and platform separately.
These values describe different objects; an image configuration ID is not a
registry digest.

The implementation and buddy selected checksum-pinned regctl for portable OCI
import and registry copying. Copy the full graph with recursive repair; do not
select a single platform or use the fast-copy flag. Verify the root digest and
referenced content after every copy. This gives the required identity checks
without adding Skopeo's host package dependencies.

Build CPU and CUDA candidates in separate hosted jobs, for `linux/amd64`, from
one current, reviewed main commit. Use a disposable registry for local runtime
tests. Qualification must run the image's application code and entrypoints.
Do not mount source code over the image or install development dependencies in it.
A test-only worker bootstrap may replace only inference for the synthetic queue
test. This proves orchestration, not model accuracy.

On October 12 the owner confirmed that no NVIDIA host is available. Keep CUDA
publication blocked. A future trusted, main-only workflow must run real model
inference on the candidate CUDA digest. A hand-written success file, GPU listing,
or CUDA availability check is insufficient. Verify the producing GitHub run and
artifact identity as well as the report. Do not run untrusted PR code on that host.

The GPU job belongs to the candidate workflow. A skipped GPU job blocks release.
This avoids a third workflow and cross-run evidence pairing. Once hardware is
available, start a new qualification cycle. This costs a new build but keeps
evidence from one known run.

The bounded CUDA probe uses the existing YOLO11 nano FP16 inference path on a
synthetic 640 × 640 image. Pin checkpoint revision
`5599de6f3b0ef95f98f37530ed0f3bf149c10b37` and SHA-256
`0ebbc80d4a7680d14987a577cd21342b65ecfd94632bd9a8da63ae6417644ee1`.
Verify actual CUDA forward execution and finite output. Empty detections are
valid. This probe does not qualify SAM3, full training, or model quality.

The existing release workflow is disabled. Its protected `release` environment
has owner approval but no secrets. Repository-level Docker Hub secret names still
exist. Do not enable publication, change secrets, register runners, tag a release,
or deploy in this wave. The owner must provision environment secrets and remove
the repository copies before a real release. Main merge commits need their own
release evidence; PR-head statuses do not transfer to a merge commit.

The publisher uses new `RELEASE_DOCKERHUB_USERNAME` and
`RELEASE_DOCKERHUB_TOKEN` names to avoid fallback to legacy repository secrets.
Its publication job has a literal false condition until a separate reviewed
enablement change. No new secret or remote workflow setting changes in this PR.

Use one explicit release manifest format and small scripts. No generic release
framework, speculative exception system, multi-platform build, application,
schema, model-default, or dependency refactor belongs here. Fail all high and
critical vulnerabilities, including unfixed findings. Scanner failure also fails
qualification. A future exception requires separate owner review and a scoped
expiry and repair issue.

## Global constraints

- Follow `AGENTS.md` and `CONTRIBUTING.md`. Use Sol for code and independent review.
- Keep private videos, filenames, credentials, and reports out of builds and CI.
  Release tests use the existing small synthetic fixture only.
- Read-only tokens and no release credentials in PR or candidate jobs. Pin new
  Actions and tools to reviewed versions/digests. Use official sources to verify pins.
- Missing, stale, failed, expired, mismatched, or incomplete evidence must fail closed.
- Scan, test, and publish the same OCI content. No rebuild in the publisher.
- Keep existing main, CI, independent review, and human approval checks. Recheck
  them after the protected environment opens and before registry mutation.
- Both variants must qualify before publishing versioned tags. Channel tags move
  only after both versioned images pass readback and a disposable runtime check.
- Registry updates are not atomic. Persist an intent record before mutation and
  an incomplete record on failure. Do not report a mixed pair as complete.
- Do not prune user images, volumes, or cache. Local free disk is limited; use
  bounded tests and one candidate at a time. Local ARM tests do not qualify AMD64.
- Use plain English, roughly 80% ASD-STE100, in plans, docs, reports, and the PR.

## Task 1: Implement the artifact and publication gates

Read `AGENTS.md` and `CONTRIBUTING.md` first. Implement the coupled release flow
in `.github/workflows/release.yml`, additional narrowly scoped release workflows,
`.github/scripts/`, and `tests/test_release*.py`. Add a test-only image smoke
driver/bootstrap under `tests/` if needed. Do not edit deployment docs or this
plan; the orchestrator owns them. Do not commit or spawn subagents.

1. Write behavior tests and record their initial failures. Extend existing source
   gate tests rather than replace them. Cover absent CPU/CUDA evidence, wrong
   source/platform/digest/checksum, changed archive bytes, missing attestations,
   scan failures/high-critical findings (including unfixed), absent real GPU
   evidence, untrusted/failed/stale/expired producer runs, failed runtime tests,
   changed main or approvals after the environment wait, and partial publication.
2. Produce CPU and CUDA OCI artifacts from the same trusted main SHA. Export with
   SBOM and provenance. Make `linux/amd64` explicit. Preserve the complete graph.
   Record immutable GitHub run/artifact identities and tool/scan database versions.
   No login or external registry push occurs here. Keep artifacts short-lived.
3. Scan the exact runnable content with a pinned scanner. Test the CPU image via
   a disposable local registry and isolated PostgreSQL, Redis, and MinIO. Check
   migrations, HTTP health/static UI, production worker readiness, then synthetic
   HTTP upload → queued worker → annotations → edit → explicit export → download.
   Replace only inference in that last test. Do not mount checkout application
   modules or install test packages in the candidate. Validate saved geometry and
   archive contents. Dead workers and timeouts fail within a stated bound.
4. Provide a trusted, main-only manual CUDA qualification path that consumes an
   immutable candidate artifact and performs actual model inference on an NVIDIA
   runner. Record GPU/driver, PyTorch/CUDA/cuDNN, checkpoint hash/revision, dtype,
   input identity, parameters, elapsed time, and candidate identities. Use an
   existing Waldo model adapter; coordinate the probe choice with the orchestrator.
   Verify GitHub workflow ID, repository, event, branch, exact SHA, run conclusion,
   GPU job result, artifact expiry and report binding before accepting GPU evidence.
   No arbitrary report path or boolean input may bypass the trusted producer.
5. Replace direct build-and-push with one publisher. Validate both candidates and
   GPU evidence before login. Require an explicit version for publication. Recheck
   current main, CI and approvals after protected environment approval. Copy each
   archive with digest preservation, reject conflicting version tags, verify
   registry readback, and smoke-test the published pair before channel promotion.
   No new builds. Save previous channel digests and intended new pair before the
   first write. Save confirmed and uncertain operations on failure. A retry can
   resume the same immutable pair; document restoring both old channels. Never
   mark completion until both channel tags match. First-release recovery may lack
   prior tags and must not invent them.
6. Provide a credential-free dry run and a tiny OCI integration test against a
   disposable local registry. Exercise real SBOM/provenance graph copying and
   root-digest preservation, plus injected failure after the first image/channel
   mutation. Unit mocks alone are not full artifact evidence. Connect appropriate
   bounded tests to hosted PR CI, without enabling publishing or self-hosted jobs.
7. Run relevant lint, syntax, regression, and contract checks. Write a report with
   exact RED/GREEN commands, counts, observed outcomes, limitations, and any
   remaining decisions. Do not claim an actual CUDA or Docker Hub release passed.

**Acceptance:** The scripts reject each missing gate. A synthetic OCI round trip
preserves attestations and digest identity. The image smoke driver completes the
isolated HTTP/worker flow using a local CPU image. Actual AMD64 candidate
qualification remains a main-only post-merge gate; the ARM64 Mac has insufficient
space for a second full build and its archive copies. Do not claim that the local
test qualifies the release Dockerfile or platform. Partial publication remains explicitly incomplete.
Workflow custody checks reject forged or stale GPU evidence. Real CUDA and
publication remain blocked until trusted hardware, credentials, and approval exist.

## Task 2: Qualify and document the release path

The orchestrator runs the bounded artifact and CPU checks, inspects their output,
and documents the result in `docs-site/docs/deployment/docker.md` and this plan.
Update only stale release instructions in `CONTRIBUTING.md` if necessary. Keep
private test data local. Do not rerun unrelated video/model experiments; no video,
inference, or UI behavior changes in this wave.

Document the candidate → CPU/scan → GPU → protected publisher sequence, required
main-commit evidence, identities in the manifest, hardware/credential prerequisites,
dry-run steps, failure recovery, previous-pair rollback, and database limits.
Include a small diagram and a human test plan with expected results.

Run affected tests, the documentation build, and current-head hosted CI. Obtain
a fresh independent Sol review of the exact final commit and its evidence. Fix
findings and re-review material changes. Open one focused PR and attach it here.
Keep human approval pending for that PR. Do not merge it or publish a release.

**Acceptance:** The PR states what was tested, what remains blocked, and how the
owner can inspect and repeat the checks. Review and hosted CI refer to its head.

## Local qualification evidence

The first real OCI export and registry copy preserved the root index, runnable
manifest, all eight blobs, SPDX SBOM, and SLSA provenance. Deliberately removing
one SBOM blob made export fail. A recursive copy repaired the existing layout
and preserved the original digest. Repacking changed the tar checksum, as expected;
it did not change the OCI graph. A pinned Trivy scan of that small synthetic
artifact passed. This is transport evidence, not a vulnerability result for Waldo.

The tests exposed two real tool assumptions before CI: regctl's selected digest
template did not work for an OCI index, and Trivy's OCI input required a directory
instead of the exported tar. The implementation uses the working digest command
and scans the exact runnable digest through its local registry.

The CPU smoke initially found test setup defects: an internal Docker network did
not publish loopback ports, and production startup needed a generated bootstrap
password. These were fixed in the test driver. A later run correctly rejected an
older local image whose inference interface differed from current source.

The orchestrator and Sol buddy chose a bounded local overlay for current-source
verification. The cached image's `pyproject.toml`, `uv.lock`, and `.python-version`
matched the repository byte-for-byte. Python was 3.11.17; PyTorch 2.12.0,
torchvision 0.27.0, FastAPI 0.136.1, Celery 5.6.3, Ultralytics 8.4.51,
Transformers 5.8.1, and NumPy 2.4.4 were present. Imports resolved under `/app`.
The new image removed the old application directories and copied 101 tracked
source files plus a fresh UI build. Its parent layer chain matched the exact
cached image. No dependencies or running user containers were changed.

The resulting ARM64 image
`sha256:4bcaf5ba2390fd21dd16345529c8e3f3ca9d793a5a107c909857bce54127951c`
passed the complete smoke in 39.47 seconds: migrations, HTTP health and UI assets,
separate labeler and trainer readiness, synthetic upload, three queued frame
results, a persisted review edit, explicit export, source grouping, edited polygon,
and matching downloaded frame bytes. Test services were removed afterwards.
This qualifies the smoke driver and current-source ARM64 packaging only. The
production Dockerfile, AMD64 candidates, real CUDA inference, Docker Hub writes,
and a release deployment were not qualified by this local test.

The final local release regression suite passed 93 tests. Its one Docker opt-in
case passed separately against a real local registry. That case builds distinct
CPU and CUDA marker graphs, injects failures after the first image and channel
writes, restores both prior channels, and retries the same immutable pair.
The markers test transport and recovery; they contain no Waldo runtime or GPU code.
Ruff, shell syntax, Actionlint, UI build, documentation build, and whitespace
checks passed. Hosted CI and fresh independent review must also pass on the PR head.
