---
title: Contributing
sidebar_position: 4
---

# Contributing

## Branch model

- `main` — always shippable
- `v2/...` — long-lived feature branches for major rewrites
- `feature/...`, `fix/...` — short-lived topic branches

PR target is `main` unless the work is part of an in-flight v2 effort.

## Commit messages

Conventional-ish but not strict. The first line is a sentence; the body explains *why*. Examples from the history:

- `Add score-first mask generation to SAM3.1 video labeling pipeline`
- `experiment: float16 DETR components`
- `experiment: pre-compute NMS too — zero ML ops in timing loop`

## Code style

- Python: ruff handles everything. Don't fight it.
- TypeScript: ESLint + Prettier.
- No comments unless the *why* is non-obvious.
- No backwards-compat shims, dead code, or "future use" stubs. If it's not used now, it doesn't belong.

## What we ship

- **A change should land as one PR.** Splitting a refactor into 12 PRs is just churn.
- **A bug fix is a bug fix.** Don't fold cleanups into it.
- **No new docs files unless they earn their keep.** Update existing pages first.

## Testing expectations

- New endpoints: integration test that hits a real DB and asserts the happy path + at least one error path.
- New workflow blocks: unit test the `run()` method against mock inputs.
- UI changes: manual smoke test (open the page, do the thing); Playwright if it's a critical path.

## Reviewing

PRs need one approving review. Reviewers should check:

- The change does what the description says
- Tests cover the new behavior
- No secrets, no commented-out code, no `console.log` / `print` left behind
- Pre-commit passes locally (CI will catch it otherwise)
