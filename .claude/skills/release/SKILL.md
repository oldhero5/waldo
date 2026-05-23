---
name: release
description: Cut a new Waldo release. Bumps the version in pyproject.toml, creates and pushes an annotated v* tag, then watches .github/workflows/release.yml until both oldhero5/waldo:<version> and :<version>-cuda show up on Docker Hub. Use when the user says "release", "cut a release", "tag a version", or "publish v0.x.y".
disable-model-invocation: true
---

You are running the Waldo release flow. This is user-only — don't auto-trigger it.

## Inputs

The user will give you a target version (e.g. `0.2.0`). Normalize to `vX.Y.Z`. Refuse anything that doesn't match `v\d+\.\d+\.\d+`.

## Steps

1. **Sanity-check the working tree**
   ```bash
   git fetch origin main --quiet
   git status --short
   git rev-list --count HEAD..origin/main   # must be 0
   git rev-list --count origin/main..HEAD   # must be 0 (we tag main exactly)
   ```
   If any of those report drift, stop and tell the user.

2. **Refuse to re-use an existing tag**
   ```bash
   git tag -l "v$VERSION" | grep -q . && echo "tag exists" && exit 1
   ```

3. **Bump pyproject.toml** — single line edit:
   ```
   version = "X.Y.Z"
   ```
   Show the diff before continuing.

4. **Commit + tag + push**
   ```bash
   git add pyproject.toml
   git commit -m "chore(release): vX.Y.Z"
   git tag -a vX.Y.Z -m "vX.Y.Z"
   git push origin main vX.Y.Z
   ```

5. **Watch CI** — `release.yml` triggers on the tag. Find the run, watch until done:
   ```bash
   sleep 4
   RUN=$(gh run list --workflow=release.yml --repo oldhero5/waldo --limit 1 \
         --event push --json databaseId --jq '.[0].databaseId')
   gh run watch "$RUN" --repo oldhero5/waldo --exit-status
   ```

6. **Verify Hub published the new tags**
   ```bash
   curl -fs https://hub.docker.com/v2/repositories/oldhero5/waldo/tags?page_size=30 \
       | python3 -c 'import sys,json; d=json.load(sys.stdin); \
                     have={t["name"] for t in d["results"]}; \
                     need={"X.Y.Z","X.Y.Z-cuda","latest","cuda"}; \
                     missing=need-have; \
                     print("MISSING:", missing) if missing else print("OK:", sorted(need))'
   ```

7. **Report** — print the Hub URLs and a one-line summary. If anything failed, surface the actual error from `gh run view --log-failed` rather than a generic message.

## Guardrails

- Don't `--no-verify` the commit. If pre-commit blocks, fix the underlying issue.
- Don't force-push or move tags. If you discover a mistake mid-flight, ask the user what to do.
- Don't proceed past step 1 if there are uncommitted changes — release from a clean tree only.
