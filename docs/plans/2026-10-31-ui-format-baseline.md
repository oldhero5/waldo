# Wave 0: UI format baseline implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every file under `ui/src` matches the locked Prettier version and
`ui/.prettierrc.json`, with no change in behavior.

**Architecture:** One mechanical commit produced by `npm run format`. The
evidence of no behavior change is identical build and lint output, an
identical browser smoke result, and a reviewed explanation of every bundle
difference (see [Results](#results); the planned byte-identical bundle check
could not pass). A second commit tells `git blame` to skip the formatting
commit.

**Tech Stack:** Prettier 3.8.2 (from `ui/package-lock.json`), Node 20, Vite 8,
ESLint 9, Playwright 1.58.2.

**Spec:** [Waldo interface redesign](2026-10-31-ui-redesign.md), wave 0. The
owner chose a format-only PR first so that later waves show only real changes.
The spec is added by its own docs PR, which merges before this one.

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

- [x] **Step 1: Install the locked dependencies**

Run: `cd ui && npm ci --legacy-peer-deps`
Expected: exit code 0.

- [x] **Step 2: Count unformatted files**

Run: `npx --no-install prettier --list-different src | wc -l`
Expected: `60`.

- [x] **Step 3: Build and hash the bundle**

Run: `npm run build && (cd ../app/static && find . -type f | sort | xargs shasum -a 256) > "$BASE/hashes-before.txt"`
Expected: build exit code 0; the file lists `index.html` and the hashed assets.

- [x] **Step 4: Record lint output**

Run: `npm run lint > "$BASE/lint-before.txt" 2>&1; echo $?`
Expected: exit code 0. Keep the full output.

- [x] **Step 5: Record the smoke result**

Run: `lsof -nP -iTCP:4173 -sTCP:LISTEN` (expect no output), then
`CI=1 WALDO_SCREENSHOT_DIR="$BASE/visual-before" npx playwright test -c playwright.smoke.config.ts --reporter=line > "$BASE/smoke-before.txt" 2>&1; echo $?`
Expected: the summary line gives passed, skipped and failed counts. Record them.

### Task 2: Format `ui/src`

**Files:**
- Modify: the 60 files that Prettier lists under `ui/src`

- [x] **Step 1: Run the formatter**

Run: `cd ui && npm run format`
Expected: exit code 0.

- [x] **Step 2: Check the result**

Run: `npm run format:check; git diff --name-only | grep -v '^ui/src/'; grep -n '^import' src/lib/videoPresentation.ts`
Expected: "All matched files use Prettier code style!"; no paths outside
`ui/src`; no import in `videoPresentation.ts`.

- [x] **Step 3: Commit**

```bash
git add ui/src
git commit -m "chore: format ui/src with the locked Prettier 3.8.2"
```

### Task 3: Prove that behavior did not change

**Files:** none changed.

- [x] **Step 1: Rebuild and compare the bundle**

Run: `npm run build && (cd ../app/static && find . -type f | sort | xargs shasum -a 256) > "$BASE/hashes-after.txt" && diff "$BASE/hashes-before.txt" "$BASE/hashes-after.txt"`
Expected: no output. If a file differs, compare that file before and after
with a pretty-printed diff, record the cause and stop.

- [x] **Step 2: Compare lint output**

Run: `npm run lint > "$BASE/lint-after.txt" 2>&1; echo $?; diff "$BASE/lint-before.txt" "$BASE/lint-after.txt"`
Expected: exit code 0 and no difference.

- [x] **Step 3: Compare the smoke result**

Run the Task 1 Step 5 commands with `visual-after` and `smoke-after.txt`.
Expected: the same passed, skipped and failed counts as before.

### Task 4: Record the evidence and skip the commit in blame

**Files:**
- Create: `.git-blame-ignore-revs`
- Modify: `docs/plans/2026-10-31-ui-format-baseline.md` (the Results section)

- [x] **Step 1: Add the formatting commit to the blame ignore list**

`.git-blame-ignore-revs` contains a comment line naming the change and the
full 40-character SHA of the Task 2 commit. GitHub reads this file by default.
Local users run `git config blame.ignoreRevsFile .git-blame-ignore-revs`.

- [x] **Step 2: Fill in the Results section below with the measured values**

- [x] **Step 3: Commit**

```bash
git add .git-blame-ignore-revs docs/plans/2026-10-31-ui-format-baseline.md
git commit -m "docs: record the UI format baseline and skip it in blame"
```

## Results

Measured on October 31, 2026 on macOS with Node 20.19.6, Prettier 3.8.2 and
Playwright 1.58.2. The formatting commit is `2dd3b9e`. The baseline is `a795755`
(the plan commit on top of `46e4d5f`).

| Check | Before | After |
| --- | --- | --- |
| Files under `ui/src` that Prettier would change | 60 of 65 | 0 |
| `npm run build` (`tsc -b` and Vite) | Pass | Pass |
| `npm run lint` | Exit 0, no warnings | Identical output |
| Browser smoke (`CI=1`, `WALDO_SCREENSHOT_DIR` set) | 127 passed, 0 skipped, 0 failed | 127 passed, 0 skipped, 0 failed |
| Paths changed outside `ui/src` | — | None |

**The bundle is not byte-identical.** Task 3 Step 1 expected identical hashes.
22 of the 48 JavaScript chunks and `index.html` changed; CSS, `api.ts`,
`videoPresentation.ts` and the icon chunks did not. Each changed chunk was
pretty-printed with Prettier's default settings, chunk-hash names were
normalized, and every differing line was read (99 lines with this method).
They fall into three groups:

1. Import names of other chunks whose hashes changed.
2. JSX text that Prettier split or joined at a different point, for example
   `" · "` became `" "` and `"· "`. The joined text is the same.
3. Three doubled JSX spaces that Prettier collapsed into one. In the source,
   `Export ready. {" "}` in `DatasetsPage.tsx`, `{mediaError} {" "}` in
   `AnnotationCanvas.tsx` and `{media.error} {" "}` in `RenewableFrameImage.tsx`
   each became `…{" "}`. These elements use normal white-space, so the browser
   shows one space in both versions. Only `textContent` loses one space. No
   element that preserves spaces (`<pre>`, `pre-wrap`) has a text difference.

**Screenshots.** The smoke suite saves 7 synthetic screenshots. Three are
pixel-identical. Three others also differ between two runs of the unchanged
baseline build (video frame timing and render noise). One,
`waldo-decoded-mask-alignment.png`, is stable across baseline runs but differs
by 128 pixels, at most 35 of 255 in intensity, in its caption row. That is the
Deploy test summary line ("7 frames · 1 tracked objects · …" in `TestTab.tsx`).
Its text `" tracked objects · "` is now two text nodes, which moves sub-pixel
anti-aliasing. The text reads the same, and `ui/e2e/video-overlay.spec.ts:429`,
which checks the exact caption string, passes.

**Changed acceptance check.** Prettier's JSX output cannot meet the planned
byte-identical bundle check, so the implementing agent stopped and found the
cause as Task 3 required. It then replaced the check with "rendered text
identical after normal HTML white-space collapsing". The spec's own acceptance
is still met in full: `ui/src` is Prettier-clean, and build, lint and smoke
results are identical. The independent reviewer rebuilt both commits, read the
bundle differences, found the same three collapsed spaces and nothing else, and
confirmed by re-running Prettier on the base commit that no file was edited by
hand. The owner accepts or rejects the replaced check when approving the PR.

Hosted CI for the PR head is recorded in the PR.

## Rollback

Revert the PR's merge commit. The change has no data, schema, dependency or
runtime effect.
