#!/usr/bin/env bash
# Block edits to high-risk paths so the assistant has to call them out and
# get explicit approval. Reads the hook payload from stdin
# (PreToolUse → tool_input.file_path) and exits non-zero with an explanation
# when the path matches the blocklist.
set -u

payload="$(cat)"
file="$(printf '%s' "$payload" | python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read() or "{}")
except Exception:
    sys.exit(0)
print(d.get("tool_input", {}).get("file_path", ""))
')"

[ -z "$file" ] && exit 0

# Normalise to a path relative to the repo root if possible.
root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
rel="${file#${root}/}"

block() {
    echo "BLOCKED: $rel" >&2
    echo "Reason: $1" >&2
    echo "If this edit is genuinely needed, ask the user to bypass via 'Edit allow' or temporarily disable this hook in .claude/settings.json." >&2
    exit 2
}

case "$rel" in
    .env)
        block "Direct edits to .env can leak/clobber secrets. Edit .env.example instead, or have the user update .env by hand." ;;
    uv.lock|ui/package-lock.json|docs-site/package-lock.json)
        block "Lock files are generated. Run 'uv sync' / 'npm install' instead of editing the lock directly." ;;
    alembic/versions/*)
        block "Migrations under alembic/versions/ are append-only once applied. Generate a new revision via 'uv run alembic revision --autogenerate' instead of mutating an existing one." ;;
esac

exit 0
