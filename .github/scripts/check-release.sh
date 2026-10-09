#!/usr/bin/env bash
set -euo pipefail

# A manual dispatch must target the current main tip. All evidence is checked
# against that exact commit, immediately before access to Docker Hub secrets.
if [[ "${GITHUB_REF:-}" != "refs/heads/main" ]]; then
  echo "Release must be dispatched from main." >&2
  exit 1
fi
if [[ ! "${GITHUB_SHA:-}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Release commit SHA is missing or invalid." >&2
  exit 1
fi
if [[ -n "${RELEASE_VERSION:-}" && ! "${RELEASE_VERSION}" =~ ^v?[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Release version must be vMAJOR.MINOR.PATCH (or blank for main tags)." >&2
  exit 1
fi

repo="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
main_sha="$(gh api "repos/${repo}/git/ref/heads/main" --jq '.object.sha')"
if [[ "$main_sha" != "$GITHUB_SHA" ]]; then
  echo "Selected SHA is no longer the main branch tip." >&2
  exit 1
fi

checks="$(gh api "repos/${repo}/commits/${GITHUB_SHA}/check-runs?filter=latest&per_page=100")"
for name in "Lint + Test + Build" "UI browser smoke"; do
  if ! jq -e --arg name "$name" '
    [.check_runs[] | select(.name == $name and .app.slug == "github-actions")]
    | sort_by(.completed_at // "") | last | .conclusion == "success"
  ' >/dev/null <<<"$checks"; then
    echo "Required CI check is missing or not successful: $name" >&2
    exit 1
  fi
done

# The combined-status endpoint provides the latest status for each context.
statuses="$(gh api "repos/${repo}/commits/${GITHUB_SHA}/status")"
for context in "Independent review" "Human approval"; do
  if ! jq -e --arg sha "$GITHUB_SHA" --arg context "$context" '
    .sha == $sha and
    ([.statuses[] | select(.context == $context and .state == "success")] | length == 1)
  ' >/dev/null <<<"$statuses"; then
    echo "Required approval status is missing or not successful: $context" >&2
    exit 1
  fi
done

echo "Current main SHA has both CI checks and both approval statuses."
