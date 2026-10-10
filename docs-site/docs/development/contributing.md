---
title: Contributing
sidebar_position: 4
---

# Contributing

## Branch model

- `main` — always shippable
- `feat/<feature>` — one short-lived feature or fix

PRs target `main`. A dependent PR can target its prerequisite feature branch;
document the dependency and retarget it when the prerequisite lands.

The repository's [AGENTS.md](https://github.com/oldhero5/waldo/blob/main/AGENTS.md)
and [CONTRIBUTING.md](https://github.com/oldhero5/waldo/blob/main/CONTRIBUTING.md)
define the operating rules: paired design choices, test-first implementation,
surgical changes, and verified acceptance criteria. Use simple, direct language
that follows the intent of ASD-STE100 without claiming formal compliance.

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

Each PR needs a fresh independent session review and the human owner's approval
of the exact commit. The owner approves here in the project chat; the PR author
and owner use the same GitHub account, which cannot approve its own PR on GitHub.
The orchestrator records the evidence and required `Independent review` and
`Human approval` statuses. A new head needs new approval. Green CI is not consent
to merge, publish, or deploy. Every PR includes a human test plan.

Reviewers should check:

- The change does what the description says
- Tests cover the new behavior
- No secrets, no commented-out code, no `console.log` / `print` left behind
- Pre-commit passes locally (CI will catch it otherwise)

Private development videos, labels, and telemetry in `test_data/` stay local and
are excluded from Git and Docker contexts. Hosted CI uses synthetic fixtures.
Do not describe the development corpus as a held-out model benchmark.
