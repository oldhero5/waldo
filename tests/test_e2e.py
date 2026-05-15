"""
End-to-end test: Upload video → label → download valid YOLO-seg dataset.

Requires:
- Running infrastructure (make infra)
- Running app (make dev-app)
- Running labeler (make dev-labeler)
- SAM 3 model downloaded (make download-models)

This test hits a live HTTP server at BASE_URL — opt in with WALDO_E2E=1.
"""

import io
import os
import time
import zipfile

import httpx
import pytest

BASE_URL = "http://localhost:8000"


@pytest.mark.skipif(
    os.environ.get("WALDO_E2E") != "1",
    reason="Set WALDO_E2E=1 to run against a live stack",
)
class TestE2E:
    def test_full_pipeline(self, test_clip):
        client = httpx.Client(base_url=BASE_URL, timeout=300)

        # 1. Upload video
        with open(test_clip, "rb") as f:
            resp = client.post(
                "/api/v1/upload",
                files={"file": ("test_clip.mp4", f, "video/mp4")},
            )
        assert resp.status_code == 201
        video_id = resp.json()["video_id"]

        # 2. Start labeling
        resp = client.post(
            "/api/v1/label",
            json={"video_id": video_id, "text_prompt": "car", "fps": 1.0},
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # 3. Poll until completed
        for _ in range(120):
            resp = client.get(f"/api/v1/status/{job_id}")
            assert resp.status_code == 200
            job_result = resp.json()
            if job_result["status"] in ("completed", "failed"):
                break
            time.sleep(5)

        assert job_result["status"] == "completed", f"Job failed: {job_result.get('error_message')}"
        assert job_result["result_url"]

        # 4. Download result (URL is now a relative API path)
        download_url = job_result["result_url"]
        if download_url.startswith("/"):
            download_url = f"{BASE_URL}{download_url}"
        result_resp = httpx.get(download_url, timeout=60)
        assert result_resp.status_code == 200

        # 5. Verify YOLO dataset structure
        with zipfile.ZipFile(io.BytesIO(result_resp.content)) as zf:
            names = zf.namelist()
            assert "data.yaml" in names

            # Should have images and labels in at least one split
            image_files = [n for n in names if n.startswith("images/") and not n.endswith("/")]
            label_files = [n for n in names if n.startswith("labels/") and n.endswith(".txt")]
            assert len(image_files) >= 1, f"Expected at least 1 image, got: {names}"
            assert len(label_files) >= 1, f"Expected at least 1 label, got: {names}"

            # Check data.yaml content
            data_yaml = zf.read("data.yaml").decode()
            assert "nc:" in data_yaml
            assert "car" in data_yaml

            # Check label format (if any annotations exist)
            for lf in label_files:
                content = zf.read(lf).decode().strip()
                if not content:
                    continue
                for line in content.split("\n"):
                    parts = line.strip().split()
                    assert int(parts[0]) >= 0  # class index
                    coords = [float(x) for x in parts[1:]]
                    assert len(coords) >= 6  # at least 3 xy pairs
                    assert all(0 <= c <= 1 for c in coords)
