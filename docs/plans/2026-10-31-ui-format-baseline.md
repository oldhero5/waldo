# Wave 0: UI format baseline implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every file under `ui/src` matches the locked Prettier version and
`ui/.prettierrc.json`, with no change in behavior.

**Architecture:** One mechanical commit produced by `npm run format`. The
evidence of no behavior change is an identical production bundle, identical
lint output and an identical browser smoke result before and after. A second
commit tells `git blame` to skip the formatting commit.

**Tech Stack:** Prettier 3.8.2 (from `ui/package-lock.json`), Node 20, Vite 8,
ESLint 9, Playwright 1.58.2.

**Spec:** [Waldo interface redesign](2026-10-31-ui-redesign.md), wave 0. The
owner chose a format-only PR first so that later waves show only real changes.

## Why

Pre-commit runs `prettier --write` on every touched `ui/src` file
(`.pre-commit-config.yaml:71-76`), but 60 of the 65 files in `ui/src` are not
Prettier-clean at `46e4d5f`. Each redesign wave would otherwise reformat whole
files it touches, which hides the real change and conflicts with the AGENTS.md
rule against reformatting adjacent code.

## Global Constraints

- Run the locked Prettier from `ui/` (`npm run format`, which is
  `prettier --write src`). Do not change `ui/.prettierrc.json`,
  `ui/.prettierignore`, `ui/package.json` or `ui/package-lock.json`.
- Change only files under `ui/src`, plus this plan and `.git-blame-ignore-revs`.
  Do not format `ui/e2e`, `docs-site` or Python files.
- Do not edit any formatted file by hand. If the Prettier output changes build,
  lint or test results, stop and report. Do not repair it in this wave.
- Use Node 20, as CI does (`.github/workflows/test.yml:172,262`).
- `ui/src/lib/videoPresentation.ts` must keep no imports. The smoke suite
  transpiles it in the browser (`ui/e2e/video-overlay.spec.ts:4,236-240`).
- No private data is involved. Store measurement files outside the repository.

## Review Focus

1. **JSX text whitespace.** If Prettier changed rendered text, the bundle
   differs. Compare the hashes of every built file, not only the build exit code.
2. **Moved suppression comments.** If a line rewrap separated an
   `eslint-disable-next-line` or `@ts-expect-error` comment from its line, lint
   or type output changes. Compare the full lint output, not only the exit code.
3. **A stale preview server.** The smoke config reuses a server already on
   127.0.0.1:4173 unless `CI` is set (`ui/playwright.smoke.config.ts:22`). Run
   with `CI=1` after confirming the port is free.
4. **Stray files in the commit.** `app/static/` and `ui/test-results/` must not
   be staged. `git status` must show only the intended paths.
5. **Files outside `ui/src`.** Run Prettier only from `ui/` on `src`, and check
   that `git diff --stat` lists only `ui/src` paths.

---

### Task 1: Record the baseline

**Files:** none changed. Measurements go to `$BASE`, a directory outside the
repository (for example the session scratchpad).

- [ ] **Step 1: Install the locked dependencies**

Run: `cd ui && npm ci --legacy-peer-deps`
Expected: exit code 0.

- [ ] **Step 2: Count unformatted files**

Run: `npx --no-install prettier --list-different src | wc -l`
Expected: `60`.

- [ ] **Step 3: Build and hash the bundle**

Run: `npm run build && (cd ../app/static && find . -type f | sort | xargs shasum -a 256) > "$BASE/hashes-before.txt"`
Expected: build exit code 0; the file lists `index.html` and the hashed assets.

- [ ] **Step 4: Record lint output**

Run: `npm run lint > "$BASE/lint-before.txt" 2>&1; echo $?`
Expected: exit code 0. Keep the full output.

- [ ] **Step 5: Record the smoke result**

Run: `lsof -nP -iTCP:4173 -sTCP:LISTEN` (expect no output), then
`CI=1 WALDO_SCREENSHOT_DIR="$BASE/visual-before" npx playwright test -c playwright.smoke.config.ts --reporter=line > "$BASE/smoke-before.txt" 2>&1; echo $?`
Expected: the summary line gives passed, skipped and failed counts. Record them.

### Task 2: Format `ui/src`

**Files:**
- Modify: the 60 files that Prettier lists under `ui/src`

- [ ] **Step 1: Run the formatter**

Run: `cd ui && npm run format`
Expected: exit code 0.

- [ ] **Step 2: Check the result**

Run: `npm run format:check; git diff --name-only | grep -v '^ui/src/'; grep -n '^import' src/lib/videoPresentation.ts`
Expected: "All matched files use Prettier code style!"; no paths outside
`ui/src`; no import in `videoPresentation.ts`.

- [ ] **Step 3: Commit**

```bash
git add ui/src
git commit -m "chore: format ui/src with the locked Prettier 3.8.2"
```

### Task 3: Prove that behavior did not change

**Files:** none changed.

- [ ] **Step 1: Rebuild and compare the bundle**

Run: `npm run build && (cd ../app/static && find . -type f | sort | xargs shasum -a 256) > "$BASE/hashes-after.txt" && diff "$BASE/hashes-before.txt" "$BASE/hashes-after.txt"`
Expected: no output. If a file differs, compare that file before and after
with a pretty-printed diff, record the cause and stop.

- [ ] **Step 2: Compare lint output**

Run: `npm run lint > "$BASE/lint-after.txt" 2>&1; echo $?; diff "$BASE/lint-before.txt" "$BASE/lint-after.txt"`
Expected: exit code 0 and no difference.

- [ ] **Step 3: Compare the smoke result**

Run the Task 1 Step 5 commands with `visual-after` and `smoke-after.txt`.
Expected: the same passed, skipped and failed counts as before.

### Task 4: Record the evidence and skip the commit in blame

**Files:**
- Create: `.git-blame-ignore-revs`
- Modify: `docs/plans/2026-10-31-ui-format-baseline.md` (the Results section)

- [ ] **Step 1: Add the formatting commit to the blame ignore list**

`.git-blame-ignore-revs` contains a comment line naming the change and the
full 40-character SHA of the Task 2 commit. GitHub reads this file by default.
Local users run `git config blame.ignoreRevsFile .git-blame-ignore-revs`.

- [ ] **Step 2: Fill in the Results section below with the measured values**

- [ ] **Step 3: Commit**

```bash
git add .git-blame-ignore-revs docs/plans/2026-10-31-ui-format-baseline.md
git commit -m "docs: record the UI format baseline and skip it in blame"
```

## Results

To be completed in Task 4 with the measured values: unformatted files before and
after, bundle hash comparison, lint comparison, smoke counts before and after,
and the hosted CI run for the PR head.

## Rollback

Revert the PR's merge commit. The change has no data, schema, dependency or
runtime effect.
