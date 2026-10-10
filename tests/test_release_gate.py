"""Fail-closed checks for the manual Docker Hub release gate."""

import json
import os
import subprocess
from pathlib import Path

import pytest

SHA = "a" * 40
SCRIPT = Path(__file__).resolve().parents[1] / ".github/scripts/check-release.sh"


@pytest.fixture
def release_api(tmp_path):
    responses = tmp_path / "responses"
    responses.mkdir()
    (responses / "ref.json").write_text(json.dumps({"object": {"sha": SHA}}))
    (responses / "checks.json").write_text(
        json.dumps(
            {
                "total_count": 2,
                "check_runs": [
                    {
                        "id": index,
                        "name": name,
                        "app": {"slug": "github-actions"},
                        "status": "completed",
                        "conclusion": "success",
                        "completed_at": "2026-10-09T10:00:00Z",
                    }
                    for index, name in enumerate(("Lint + Test + Build", "UI browser smoke"), start=1)
                ],
            }
        )
    )
    (responses / "statuses.json").write_text(
        json.dumps(
            {
                "sha": SHA,
                "statuses": [
                    {"context": name, "state": "success"} for name in ("Independent review", "Human approval")
                ],
            }
        )
    )
    fake_gh = tmp_path / "gh"
    fake_gh.write_text(
        "#!/bin/sh\n"
        'case "$2" in\n'
        "  */git/ref/heads/main) jq -r '.object.sha' \"$FAKE_API_DIR/ref.json\" ;;\n"
        '  */check-runs*filter=all*) cat "$FAKE_API_DIR/checks.json" ;;\n'
        '  */status) cat "$FAKE_API_DIR/statuses.json" ;;\n'
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    fake_gh.chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "FAKE_API_DIR": str(responses),
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_SHA": SHA,
        "GITHUB_REPOSITORY": "oldhero5/waldo",
        "RELEASE_VERSION": "v1.2.3",
    }
    return responses, env


def run_gate(env):
    return subprocess.run(["bash", str(SCRIPT)], env=env, text=True, capture_output=True, check=False)


def test_release_gate_accepts_current_main_with_all_evidence(release_api):
    _, env = release_api
    result = run_gate(env)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("ref", ["refs/heads/feat/ci", "refs/tags/v1.2.3"])
def test_release_gate_rejects_non_main_dispatch(release_api, ref):
    _, env = release_api
    result = run_gate({**env, "GITHUB_REF": ref})
    assert result.returncode != 0


def test_release_gate_rejects_stale_main_sha(release_api):
    responses, env = release_api
    (responses / "ref.json").write_text(json.dumps({"object": {"sha": "b" * 40}}))
    assert run_gate(env).returncode != 0


@pytest.mark.parametrize("version", ["latest", "v1.2", "v1.2.3;echo bad"])
def test_release_gate_rejects_invalid_version(release_api, version):
    _, env = release_api
    assert run_gate({**env, "RELEASE_VERSION": version}).returncode != 0


@pytest.mark.parametrize("missing", ["Lint + Test + Build", "UI browser smoke"])
def test_release_gate_requires_each_ci_check(release_api, missing):
    responses, env = release_api
    path = responses / "checks.json"
    data = json.loads(path.read_text())
    data["check_runs"] = [check for check in data["check_runs"] if check["name"] != missing]
    data["total_count"] = len(data["check_runs"])
    path.write_text(json.dumps(data))
    assert run_gate(env).returncode != 0


@pytest.mark.parametrize("missing", ["Independent review", "Human approval"])
def test_release_gate_requires_each_approval_status(release_api, missing):
    responses, env = release_api
    path = responses / "statuses.json"
    data = json.loads(path.read_text())
    data["statuses"] = [status for status in data["statuses"] if status["context"] != missing]
    path.write_text(json.dumps(data))
    assert run_gate(env).returncode != 0


def test_release_gate_rejects_latest_failed_check(release_api):
    responses, env = release_api
    path = responses / "checks.json"
    data = json.loads(path.read_text())
    data["check_runs"].append(
        {
            "id": 101,
            "name": "UI browser smoke",
            "app": {"slug": "github-actions"},
            "status": "completed",
            "conclusion": "failure",
            "completed_at": "2026-10-09T11:00:00Z",
        }
    )
    data["total_count"] = len(data["check_runs"])
    path.write_text(json.dumps(data))
    assert run_gate(env).returncode != 0


def test_release_gate_rejects_truncated_check_run_response(release_api):
    responses, env = release_api
    path = responses / "checks.json"
    data = json.loads(path.read_text())
    data["total_count"] = 101
    path.write_text(json.dumps(data))
    assert run_gate(env).returncode != 0


def test_release_gate_rejects_in_progress_rerun_after_old_success(release_api):
    responses, env = release_api
    path = responses / "checks.json"
    data = json.loads(path.read_text())
    old = next(check for check in data["check_runs"] if check["name"] == "UI browser smoke")
    old.update({"id": 100, "status": "completed"})
    data["check_runs"].append(
        {
            "id": 101,
            "name": "UI browser smoke",
            "app": {"slug": "github-actions"},
            "status": "in_progress",
            "conclusion": None,
            "completed_at": None,
        }
    )
    data["total_count"] = len(data["check_runs"])
    path.write_text(json.dumps(data))
    result = run_gate(env)
    assert result.returncode != 0, result.stdout


def test_release_gate_rejects_failed_approval_status(release_api):
    responses, env = release_api
    path = responses / "statuses.json"
    data = json.loads(path.read_text())
    data["statuses"][1]["state"] = "failure"
    path.write_text(json.dumps(data))
    assert run_gate(env).returncode != 0
