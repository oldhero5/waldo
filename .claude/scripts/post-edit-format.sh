#!/usr/bin/env bash
# Auto-run ruff on Python files Claude just edited. Reads the hook payload
# from stdin (PostToolUse → tool_input.file_path).
#
# Exits 0 even on lint errors so the assistant sees the result and can
# react, but doesn't get blocked from continuing.
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
[ ! -f "$file" ] && exit 0

case "$file" in
    *.py)
        # Format first (deterministic), then check with --fix for safe lints.
        # Ignore exit codes — we want the output, not a hard fail.
        cd "$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
        uv run ruff format "$file" 2>&1 | sed 's/^/[ruff format] /' || true
        uv run ruff check --fix "$file" 2>&1 | sed 's/^/[ruff check]  /' || true
        ;;
esac

exit 0
