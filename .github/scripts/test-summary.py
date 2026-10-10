#!/usr/bin/env python3
"""Summarize actual JUnit cases without copying test logs into GitHub Markdown."""

import argparse
import html
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

OUTCOMES = {
    "backend": ("RUFF_LINT", "RUFF_FORMAT", "MIGRATIONS", "PYTEST", "UI_DEPS", "UI_BUILD", "UI_LINT", "SECRET_SCAN"),
    "browser": ("UI_DEPS", "UI_BUILD", "CHROMIUM", "BROWSER"),
}
STEP_LABELS = {
    "RUFF_LINT": "Python lint",
    "RUFF_FORMAT": "Python formatting",
    "MIGRATIONS": "Database migration",
    "PYTEST": "Python tests",
    "UI_DEPS": "UI dependencies",
    "UI_BUILD": "UI build",
    "UI_LINT": "UI lint",
    "SECRET_SCAN": "Secret scan",
    "CHROMIUM": "Browser installation",
    "BROWSER": "Browser tests",
}
SCOPES = {
    "backend": (
        "Required CI includes a separate Celery worker with controlled inference, PostgreSQL, Redis and MinIO."
    ),
    "browser": "Browser tests use a mocked API; they verify UI behavior, not live backend services.",
}


def escape(value):
    """Keep untrusted names on one bounded line, without HTML or Markdown links."""
    value = " ".join(value.split())
    if len(value) > 250:
        value = value[:247] + "..."
    value = html.escape(value, quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def details(lines, title, entries):
    if not entries:
        return
    lines.extend(["", f"### {title}", ""])
    lines.extend(f"- {entry}" for entry in entries[:20])
    if len(entries) > 20:
        lines.append(f"- {len(entries) - 20} more; see the report artifact.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=OUTCOMES, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    doc_sha = os.environ.get("PR_HEAD_SHA") or os.environ.get("GITHUB_SHA", "")
    doc = "CONTRIBUTING.md"
    if re.fullmatch(r"[\w.-]+/[\w.-]+", repo) and re.fullmatch(r"[0-9a-f]{40}", doc_sha):
        doc = f"[validation limits](https://github.com/{repo}/blob/{doc_sha}/CONTRIBUTING.md#validation-and-runners)"
    lines = [
        f"## {args.suite.title()} test evidence",
        "",
        SCOPES[args.suite],
        "Neither suite qualifies real SAM accuracy or a complete live labeling-to-training workflow.",
        "Required worker cases fail on missing services or unfinished jobs. "
        "Real-model HTTP and training workflows remain opt-in hardware checks. "
        f"See {doc}. These are scope notes, not result counts.",
        "",
    ]
    for variable, label in (
        ("GITHUB_SHA", "Tested checkout SHA"),
        ("PR_HEAD_SHA", "PR head SHA"),
        ("PR_BASE_SHA", "PR base SHA"),
    ):
        if os.environ.get(variable):
            value = html.escape(" ".join(os.environ[variable].split())[:250])
            lines.append(f"{label}: <code>{value}</code>")
    lines.extend(
        [
            "",
            "On pull requests the tested checkout is the synthetic merge commit; on pushes it is the pushed head.",
            "",
            "### Step outcomes",
            "",
        ]
    )
    step_failed = False
    for name in OUTCOMES[args.suite]:
        outcome = os.environ.get(f"{name}_OUTCOME", "not reported")
        step_failed |= outcome == "failure"
        lines.append(f"- {STEP_LABELS[name]}: {escape(outcome)}")
    artifact = f"{args.suite}-test-report"
    url = os.environ.get("REPORT_ARTIFACT_URL", "")
    if re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+/actions/runs/\d+/artifacts/\d+", url):
        lines.extend(["", f"[Download {artifact}]({url}) — GitHub sign-in required."])
    else:
        lines.extend(["", f"Report download unavailable; expected artifact: {artifact}."])
    try:
        root = ET.parse(args.report).getroot()  # noqa: S314 — no external entity resolution
        if root.tag not in ("testsuite", "testsuites"):
            raise ValueError("not a JUnit suite")
        cases = list(root.iter("testcase"))
        if not cases:
            raise ValueError("no test cases")
    except (OSError, ET.ParseError, ValueError):
        lines[2:2] = ["**Results unavailable:** report missing, malformed, or contains no JUnit test cases.", ""]
        print("\n".join(lines))
        return 1
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    failures, skips = [], []
    for case in cases:
        name = escape("::".join(filter(None, (case.get("classname"), case.get("name", "unnamed case")))))
        if case.find("error") is not None:
            counts["errors"] += 1
            failures.append(f"{name} (error)")
        elif case.find("failure") is not None:
            counts["failed"] += 1
            failures.append(f"{name} (failure)")
        elif (skipped := case.find("skipped")) is not None:
            counts["skipped"] += 1
            reason = skipped.get("message") or skipped.text or "reason not provided"
            skips.append(f"{name}: {escape(reason)}")
        else:
            counts["passed"] += 1
    lines[2:2] = [
        "| Total | Passed | Failed | Errors | Skipped |",
        "| --- | --- | --- | --- | --- |",
        f"| {len(cases)} | {counts['passed']} | {counts['failed']} | {counts['errors']} | {counts['skipped']} |",
        "",
    ]
    details(lines, "Failed or errored cases", failures)
    details(lines, "Skipped cases and reasons", skips)
    print("\n".join(lines))
    return int(bool(failures) or step_failed)


if __name__ == "__main__":
    raise SystemExit(main())
