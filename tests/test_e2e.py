"""Opt-in live label, review, and export check with a trusted local positive clip.

Set WALDO_E2E=1, WALDO_E2E_BASE_URL, and WALDO_E2E_SOURCE_A. The named
server must use an isolated test stack. WALDO_E2E_PROMPT defaults to "car";
the source must contain an object the installed model can find with that prompt.
"""

import io
import os
import time
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest


@pytest.mark.skipif(
    os.environ.get("WALDO_E2E") != "1",
    reason="Set WALDO_E2E=1 to run against an isolated live stack",
)
class TestE2E:
    def test_full_pipeline(self, register_test_client):
        base_url = os.environ.get("WALDO_E2E_BASE_URL")
        source = os.environ.get("WALDO_E2E_SOURCE_A")
        prompt = os.environ.get("WALDO_E2E_PROMPT", "car").strip()
        assert base_url, "Set WALDO_E2E_BASE_URL to the isolated live test API"
        parsed_url = urlsplit(base_url)
        assert parsed_url.scheme == "http" and parsed_url.hostname in {"localhost", "127.0.0.1", "::1"}, (
            "WALDO_E2E_BASE_URL must point to a local isolated test API"
        )
        assert source, "Set WALDO_E2E_SOURCE_A to a trusted local clip with a detectable object"
        assert prompt, "WALDO_E2E_PROMPT must name a detectable object"
        source = Path(source).expanduser()
        assert source.is_file(), "WALDO_E2E_SOURCE_A must name an existing local file"

        with httpx.Client(base_url=base_url, timeout=300) as raw_client:
            client = register_test_client(raw_client)

            with source.open("rb") as video:
                resp = client.post(
                    "/api/v1/upload",
                    params={"project_name": f"e2e-{uuid.uuid4().hex}"},
                    files={"file": (f"e2e_source{source.suffix}", video, "application/octet-stream")},
                )
            assert resp.status_code == 201, resp.text
            video_id = resp.json()["video_id"]

            resp = client.post(
                "/api/v1/label",
                json={"video_id": video_id, "text_prompt": prompt, "fps": 1.0},
            )
            assert resp.status_code == 202, resp.text
            job_id = resp.json()["job_id"]

            deadline = time.monotonic() + 600
            while time.monotonic() < deadline:
                resp = client.get(f"/api/v1/status/{job_id}", timeout=10)
                assert resp.status_code == 200, resp.text
                job_result = resp.json()
                if job_result["status"] in ("completed", "failed", "partial"):
                    break
                time.sleep(min(5, max(0, deadline - time.monotonic())))
            else:
                pytest.fail(f"Labeling worker did not finish within 600 seconds: {job_result['status']}")
            assert job_result["status"] == "completed", f"Labeling failed: {job_result.get('error_message')}"

            resp = client.get(f"/api/v1/jobs/{job_id}/annotations", params={"limit": 10000})
            assert resp.status_code == 200, resp.text
            annotations = resp.json()
            assert annotations, "The local source and prompt must produce a detectable object"
            assert {ann["source_video_id"] for ann in annotations} == {video_id}
            reviewed = client.patch(f"/api/v1/annotations/{annotations[0]['id']}", json={"status": "accepted"})
            assert reviewed.status_code == 200, reviewed.text
            assert reviewed.json()["status"] == "accepted"

            exported = client.post(f"/api/v1/jobs/{job_id}/export", json={"format": "segment"})
            assert exported.status_code == 200, exported.text
            assert exported.json()["format"] == "segment"
            download_url = exported.json()["download_url"]
            assert download_url.startswith("/api/v1/download/")
            result_resp = client.get(download_url, timeout=60)
            assert result_resp.status_code == 200, result_resp.text

            with zipfile.ZipFile(io.BytesIO(result_resp.content)) as archive:
                names = archive.namelist()
                assert "data.yaml" in names
                image_files = [name for name in names if name.startswith("images/") and not name.endswith("/")]
                label_files = [name for name in names if name.startswith("labels/") and name.endswith(".txt")]
                assert image_files, f"Expected at least one image: {names}"
                assert label_files, f"Expected at least one label: {names}"

                data_yaml = archive.read("data.yaml").decode()
                assert "nc:" in data_yaml
                assert prompt in data_yaml
                label_lines = [
                    line for name in label_files for line in archive.read(name).decode().splitlines() if line.strip()
                ]
                assert label_lines, "Reviewed positive observation is missing from the export"
                for line in label_lines:
                    parts = line.split()
                    assert int(parts[0]) >= 0
                    coords = [float(value) for value in parts[1:]]
                    assert len(coords) >= 6
                    assert all(0 <= value <= 1 for value in coords)
