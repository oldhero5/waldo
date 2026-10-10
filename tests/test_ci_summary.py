"""Exercise the reporting CLI, without application services or fabricated totals."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/test-summary.py"


def run_summary(tmp_path, xml, suite="backend", **env):
    report = tmp_path / "junit.xml"
    if xml is not None:
        report.write_text(xml)
    # Deliberately exclude the caller's private environment and CI credentials.
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--suite", suite, "--report", str(report)],
        env={"PATH": os.environ.get("PATH", ""), **env},
        capture_output=True,
        text=True,
        check=False,
    )


def test_pytest_counts_actual_cases_and_reports_skips_and_failures(tmp_path):
    result = run_summary(
        tmp_path,
        """<testsuites><testsuite tests="999" failures="0">
        <testcase classname="tests.test_auth" name="allowed"/>
        <testcase classname="tests.test_auth" name="denied"><failure>private trace</failure></testcase>
        <testcase classname="tests.test_model" name="native"><skipped message="Apple MLX unavailable"/></testcase>
        <testcase classname="tests.test_db" name="setup"><error>private connection string</error></testcase>
        </testsuite></testsuites>""",
    )
    assert result.returncode == 1
    assert "| Total | Passed | Failed | Errors | Skipped |" in result.stdout
    assert "| 4 | 1 | 1 | 1 | 1 |" in result.stdout
    assert "tests\\.test\\_model::native" in result.stdout
    assert "Apple MLX unavailable" in result.stdout
    assert "denied" in result.stdout and "setup" in result.stdout
    assert "private trace" not in result.stdout
    assert "private connection string" not in result.stdout
    assert "backend-test-report" in result.stdout


def test_playwright_nested_suites_report_browser_scope_and_passes(tmp_path):
    result = run_summary(
        tmp_path,
        '<testsuites><testsuite name="chromium"><testsuite><testcase classname="review.spec.ts" '
        'name="review export"/><testcase name="optional"><skipped>Missing browser capability</skipped>'
        "</testcase></testsuite></testsuite></testsuites>",
        suite="browser",
    )
    assert result.returncode == 0
    assert "| 2 | 1 | 0 | 0 | 1 |" in result.stdout
    assert "Missing browser capability" in result.stdout
    assert "mocked API" in result.stdout
    assert "real SAM accuracy" in result.stdout
    assert "browser-test-report" in result.stdout


@pytest.mark.parametrize("xml", [None, "", "<testsuite>", "<testsuite tests='25'/>", "<other/>"])
def test_unavailable_report_never_claims_pass(tmp_path, xml):
    result = run_summary(tmp_path, xml)
    assert result.returncode != 0
    assert "Results unavailable" in result.stdout
    assert "| Total |" not in result.stdout
    assert "Traceback" not in result.stderr


def test_untrusted_names_are_escaped_and_details_are_bounded(tmp_path):
    xml = '<testsuite><testcase name="&lt;script&gt;[click](https://bad)&#10;## heading|`code`">'
    xml += '<skipped message="&lt;img src=x&gt; **reason**"/></testcase>'
    xml += "".join(f'<testcase name="skip{i}"><skipped message="reason"/></testcase>' for i in range(50))
    xml += "</testsuite>"
    result = run_summary(tmp_path, xml)
    assert result.returncode == 0
    assert "| 51 | 0 | 0 | 0 | 51 |" in result.stdout
    assert "<script>" not in result.stdout and "<img" not in result.stdout
    assert "[click](https://bad)" not in result.stdout
    assert "\n## heading" not in result.stdout
    assert "\\[click\\]\\(https://bad\\)" in result.stdout
    assert "31 more" in result.stdout
    assert len(result.stdout) < 12000


def test_only_explicit_ci_metadata_is_reported(tmp_path):
    result = run_summary(
        tmp_path,
        '<testsuite><testcase name="ok"/></testsuite>',
        GITHUB_SHA="merge123",
        PR_HEAD_SHA="head456",
        PR_BASE_SHA="base789",
        PYTEST_OUTCOME="success",
        UI_BUILD_OUTCOME="failure",
        PRIVATE_PASSWORD="do-not-print",
    )
    assert result.returncode == 1
    assert "Tested checkout SHA: <code>merge123</code>" in result.stdout
    assert "PR head SHA: <code>head456</code>" in result.stdout
    assert "PR base SHA: <code>base789</code>" in result.stdout
    assert "success" in result.stdout
    assert "UI build: failure" in result.stdout
    assert "do-not-print" not in result.stdout


def test_push_without_pr_metadata_does_not_invent_head(tmp_path):
    result = run_summary(tmp_path, '<testsuite><testcase name="ok"/></testsuite>', GITHUB_SHA="push123")
    assert result.returncode == 0
    assert "Tested checkout SHA: <code>push123</code>" in result.stdout
    assert "PR head SHA:" not in result.stdout


def test_uploaded_report_has_direct_github_artifact_link(tmp_path):
    result = run_summary(
        tmp_path,
        '<testsuite><testcase name="ok"/></testsuite>',
        REPORT_ARTIFACT_URL="https://github.com/example/waldo/actions/runs/123/artifacts/456",
    )
    assert (
        "[Download backend-test-report](https://github.com/example/waldo/actions/runs/123/artifacts/456)"
        in result.stdout
    )


@pytest.mark.parametrize("url", ["", "javascript:alert(1)", "https://github.com/example/waldo?token=private-token"])
def test_absent_or_unsafe_artifact_url_does_not_invent_download(tmp_path, url):
    result = run_summary(tmp_path, '<testsuite><testcase name="ok"/></testsuite>', REPORT_ARTIFACT_URL=url)
    assert "Report download unavailable" in result.stdout
    assert "javascript:" not in result.stdout
    assert "private-token" not in result.stdout


def test_case_counts_precede_commit_metadata_and_step_details(tmp_path):
    result = run_summary(
        tmp_path,
        '<testsuite><testcase name="ok"/></testsuite>',
        GITHUB_SHA="tested123",
        PYTEST_OUTCOME="success",
    )
    assert result.stdout.index("| 1 | 1 | 0 | 0 | 0 |") < result.stdout.index("Tested checkout SHA:")
    assert "Python tests: success" in result.stdout


def test_validation_document_link_targets_tested_commit(tmp_path):
    result = run_summary(
        tmp_path,
        '<testsuite><testcase name="ok"/></testsuite>',
        GITHUB_REPOSITORY="example/waldo",
        GITHUB_SHA="a" * 40,
    )
    assert (
        "[validation limits](https://github.com/example/waldo/blob/"
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/CONTRIBUTING.md#validation-and-runners)"
    ) in result.stdout


def test_validation_document_link_prefers_pr_head_over_merge(tmp_path):
    result = run_summary(
        tmp_path,
        '<testsuite><testcase name="ok"/></testsuite>',
        GITHUB_REPOSITORY="example/waldo",
        GITHUB_SHA="a" * 40,
        PR_HEAD_SHA="b" * 40,
    )
    assert (
        "https://github.com/example/waldo/blob/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb/CONTRIBUTING.md"
        in result.stdout
    )


def test_invalid_repository_metadata_does_not_create_document_link(tmp_path):
    result = run_summary(
        tmp_path,
        '<testsuite><testcase name="ok"/></testsuite>',
        GITHUB_REPOSITORY="example/waldo?token=hidden-value",
        GITHUB_SHA="a" * 40,
    )
    assert "hidden-value" not in result.stdout
    assert "(CONTRIBUTING.md#" not in result.stdout
    assert "CONTRIBUTING.md" in result.stdout
