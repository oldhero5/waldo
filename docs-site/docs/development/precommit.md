---
title: Pre-commit Hooks
sidebar_position: 2
---

# Pre-commit Hooks

Waldo ships a [pre-commit](https://pre-commit.com/) configuration that runs security and lint checks on every commit. The hooks are designed so contributors don't need Python or Node installed locally — Docker is the fallback.

## What runs

| Hook | Purpose |
| --- | --- |
| `trailing-whitespace`, `end-of-file-fixer`, `mixed-line-ending` | File hygiene |
| `check-yaml`, `check-toml`, `check-json` | Syntax sanity |
| `check-added-large-files` | Block files > 1 MB |
| `detect-private-key` | Catch SSH keys / PEM in commits |
| `gitleaks` | Secret scanning |
| `detect-secrets` | Secondary secret scanner with baseline |
| `ruff` (lint + format) | Python lint + format |
| `eslint` | UI lint |
| `prettier` | UI format |
| `hadolint` | Dockerfile lint |
| `yamllint` | YAML lint |
| `shellcheck` | Shell script lint |

Config: [`.pre-commit-config.yaml`](https://github.com/your-org/waldo/blob/main/.pre-commit-config.yaml)

## Install (host)

```bash
uv run pre-commit install            # set up the git hook
uv run pre-commit run --all-files    # initial run on the whole repo
```

After `pre-commit install`, every `git commit` runs the relevant hooks against staged files. Failures abort the commit; many hooks auto-fix and re-stage.

## Install (Docker — Linux/Windows)

If you don't want Python, Node, or hadolint installed locally:

```bash
docker compose -f docker-compose.precommit.yml run --rm precommit
```

The image bundles git, uv, Python, Node, npm, and shellcheck. The `.git` directory is mounted from the host so the hook results land back in your working tree.

## Updating the secrets baseline

When detect-secrets flags a false positive (e.g. a hex string that isn't actually a secret), audit and update the baseline:

```bash
uv run detect-secrets scan --baseline .secrets.baseline
uv run detect-secrets audit .secrets.baseline
git add .secrets.baseline
```

## Skipping hooks

Don't. If a hook is wrong, fix the hook config so the next commit doesn't have the same problem. Skipping hooks (`SKIP=...` or `--no-verify`) is reserved for emergencies and should be reverted in the next PR.

## CI

CI runs the same hook set via `pre-commit run --all-files`. The `hadolint-docker` hook is skipped in CI (no docker-in-docker) — lint Dockerfiles locally before pushing.
