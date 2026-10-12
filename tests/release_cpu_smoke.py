#!/usr/bin/env python3
"""Test the immutable candidate through HTTP and a separate production Celery worker.

Only this test bootstrap is mounted. No checkout application module or test
dependency is copied into the candidate. All containers and networks are owned
by this run and removed on exit. Readiness: 180 s; queued job: 60 s.
"""

import argparse
import io
import json
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def docker(*args, timeout=180):
    try:
        return subprocess.run(
            ["docker", *map(str, args)], check=True, text=True, capture_output=True, timeout=timeout
        ).stdout.strip()  # noqa: S603,S607
    except subprocess.CalledProcessError as error:
        # Never echo argv: service credentials occur in container creation args.
        raise RuntimeError(f"Docker {args[0]} failed: {error.stderr.strip()}") from error


def request(base, path, *, headers=None, data=None, method=None):
    req = urllib.request.Request(base + path, headers=headers or {}, data=data, method=method)  # noqa: S310
    with urllib.request.urlopen(req, timeout=10) as response:  # noqa: S310
        return response.read()


def wait(callback, seconds, description):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            result = callback()
            if result:
                return result
        except (urllib.error.URLError, subprocess.SubprocessError, OSError) as error:
            last = error
        time.sleep(1)
    raise TimeoutError(f"{description} did not pass within {seconds}s: {last}")


def run(image, output, platform, runnable_digest=None):
    identity = "waldo-release-" + uuid.uuid4().hex[:10]
    containers = []
    report = {
        "schema": 1,
        "passed": False,
        "image": image,
        "runnable_digest": runnable_digest,
        "platform": platform,
        "readiness_timeout_seconds": 180,
        "job_timeout_seconds": 60,
    }
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="waldo-release-smoke-") as temporary:
            temporary = Path(temporary)
            minio = temporary / "minio"
            # Docker internal bridges suppress host port mappings. A dedicated
            # bridge with ports bound only to loopback permits the host HTTP probe.
            docker("network", "create", identity)
            network_created = True
            # Official archived MinIO server. Run in the candidate's OS, without installing anything.
            minio_arch = {"linux/amd64": "amd64", "linux/arm64": "arm64"}[platform]
            minio_hash = {
                "amd64": "53e2a2cb16c5366ea6fbbc479c19ddb4c6a0948273e752f740fb1fbf27bb817c",
                "arm64": "6c2f3142c94240206123177f4ba1e360daa5d1e0a4962e90757ef4f92c3ab57c",
            }[minio_arch]
            subprocess.run(
                [
                    "curl",
                    "-fsSL",
                    "--retry",
                    "3",
                    "-o",
                    str(minio),
                    "https://github.com/minio/minio/releases/download/RELEASE.2025-04-22T22-12-26Z/"
                    f"minio.linux-{minio_arch}.RELEASE.2025-04-22T22-12-26Z",
                ],
                check=True,
                timeout=180,
            )  # noqa: S603,S607
            import hashlib

            with minio.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != minio_hash:
                    raise RuntimeError("MinIO checksum mismatch")
            minio.chmod(0o755)
            password = secrets.token_hex(20)
            access = secrets.token_hex(10)
            jwt = secrets.token_hex(32)

            def start(role, candidate, *args):
                name = identity + "-" + role
                containers.append(name)  # Record before create so failures also clean up this name.
                docker(
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--hostname",
                    name,
                    "--network",
                    identity,
                    "--network-alias",
                    role,
                    *args,
                    candidate,
                )
                return name

            start(
                "postgres",
                "postgres:16",
                "-e",
                "POSTGRES_USER=waldo",
                "-e",
                f"POSTGRES_PASSWORD={password}",
                "-e",
                "POSTGRES_DB=release_smoke",
            )
            start("redis", "redis:7")
            minio_name = identity + "-minio"
            containers.append(minio_name)
            docker(
                "run",
                "-d",
                "--platform",
                platform,
                "--name",
                minio_name,
                "--network",
                identity,
                "--network-alias",
                "minio",
                "-p",
                "127.0.0.1::9000",
                "-e",
                f"MINIO_ROOT_USER={access}",
                "-e",
                f"MINIO_ROOT_PASSWORD={password}",
                "-v",
                f"{minio}:/release/minio:ro",
                "--entrypoint",
                "/release/minio",
                image,
                "server",
                "/tmp/minio-data",
                "--address",
                ":9000",
            )
            minio_port = docker("port", minio_name, "9000/tcp").rsplit(":", 1)[1]
            wait(lambda: (request(f"http://127.0.0.1:{minio_port}", "/minio/health/live"), True)[1], 180, "MinIO")
            env = {
                "APP_ENV": "production",
                "DEVICE": "cpu",
                "POSTGRES_HOST": "postgres",
                "POSTGRES_USER": "waldo",
                "POSTGRES_PASSWORD": password,
                "POSTGRES_DB": "release_smoke",
                "REDIS_URL": "redis://redis:6379/0",
                "MINIO_ENDPOINT": "minio:9000",
                "MINIO_EXTERNAL_ENDPOINT": f"127.0.0.1:{minio_port}",
                "MINIO_ACCESS_KEY": access,
                "MINIO_SECRET_KEY": password,
                "JWT_SECRET": jwt,
                "ADMIN_BOOTSTRAP_PASSWORD": password,
                "UV_NO_SYNC": "1",
            }
            env_args = [item for key, value in env.items() for item in ("-e", f"{key}={value}")]
            bucket_name = identity + "-bucket"
            containers.append(bucket_name)
            docker(
                "run",
                "--name",
                bucket_name,
                "--network",
                identity,
                "--platform",
                platform,
                *env_args,
                "--entrypoint",
                "/app/.venv/bin/python",
                image,
                "-c",
                "from lib.storage import ensure_bucket; ensure_bucket()",
            )
            api = start("app", image, "--platform", platform, "-p", "127.0.0.1::8000", *env_args)
            api_port = docker("port", api, "8000/tcp").rsplit(":", 1)[1]
            base = f"http://127.0.0.1:{api_port}"

            def healthy():
                if docker("inspect", "--format", "{{.State.Running}}", api) != "true":
                    raise RuntimeError("API exited before readiness")
                return json.loads(request(base, "/health"))["status"] == "ok"

            wait(healthy, 180, "API health/migrations")
            index = request(base, "/").decode()
            if '<div id="root">' not in index or "/assets/" not in index:
                raise RuntimeError("Built static UI is missing")
            import re

            asset = re.search(r'(?:src|href)="(/assets/[^\"]+)"', index)
            if not asset or not request(base, asset[1]):
                raise RuntimeError("Built UI asset is missing")
            production = start("labeler", image, "--platform", platform, *env_args, "-e", "WALDO_ROLE=labeler")

            def ping(expected):
                reply = docker(
                    "exec",
                    api,
                    "/app/.venv/bin/python",
                    "-c",
                    "from lib.tasks import app; import json; print(json.dumps(app.control.ping(timeout=3)))",
                    timeout=15,
                )
                return any(f"celery@{expected}" in item for item in json.loads(reply))

            wait(lambda: ping(production), 60, "Production labeler worker readiness")
            trainer = start("trainer", image, "--platform", platform, *env_args, "-e", "WALDO_ROLE=trainer")
            wait(lambda: ping(trainer), 60, "Production trainer worker readiness")
            docker("stop", "--time", "10", trainer)
            docker("stop", "--time", "10", production)
            # The real task/queue/decoder/storage remain candidate code. Replace inference only.
            bootstrap = ROOT / "tests/release_worker.py"
            worker_name = identity + "-synthetic-worker"
            containers.append(worker_name)
            docker(
                "run",
                "-d",
                "--name",
                worker_name,
                "--hostname",
                worker_name,
                "--network",
                identity,
                "--platform",
                platform,
                *env_args,
                "-e",
                "WALDO_RELEASE_SMOKE=1",
                "-e",
                "PYTHONPATH=/release",
                "-v",
                f"{bootstrap}:/release/release_worker.py:ro",
                "--entrypoint",
                "/app/.venv/bin/celery",
                image,
                "-A",
                "release_worker",
                "worker",
                "--pool=solo",
                "--concurrency=1",
                "--loglevel=info",
            )
            wait(lambda: ping(worker_name), 60, "Synthetic boundary worker readiness")
            headers = {"Content-Type": "application/json"}

            def api_json(path, data=None, method=None):
                return json.loads(
                    request(
                        base,
                        path,
                        headers=headers,
                        data=json.dumps(data).encode() if data is not None else None,
                        method=method,
                    )
                )

            user = api_json(
                "/api/v1/auth/register",
                {
                    "email": f"{identity}@example.invalid",
                    "password": password,
                    "display_name": "Release smoke",
                    "workspace_name": identity,
                },
            )
            headers["Authorization"] = "Bearer " + user["access_token"]
            headers["X-Workspace-ID"] = api_json("/api/v1/auth/me")["workspace_id"]
            boundary = uuid.uuid4().hex
            clip = (ROOT / "tests/fixtures/test_clip.mp4").read_bytes()
            multipart = (
                (
                    f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic.mp4"'
                    "\r\nContent-Type: video/mp4\r\n\r\n"
                ).encode()
                + clip
                + f"\r\n--{boundary}--\r\n".encode()
            )
            upload_headers = {**headers, "Content-Type": f"multipart/form-data; boundary={boundary}"}
            upload = json.loads(request(base, "/api/v1/upload", headers=upload_headers, data=multipart))
            job = api_json(
                "/api/v1/label",
                {"video_id": upload["video_id"], "text_prompt": "synthetic_object", "task_type": "segment"},
            )["job_id"]

            def completed():
                if docker("inspect", "--format", "{{.State.Running}}", worker_name) != "true":
                    raise RuntimeError("Worker exited before completing queued job")
                status = api_json(f"/api/v1/status/{job}")
                if status["status"] == "failed":
                    raise RuntimeError(f"Queued worker failed: {status.get('error_message')}")
                return status if status["status"] == "completed" else None

            finished = wait(completed, 60, "Queued HTTP label job")
            assert finished["result_url"] is None, "Export occurred before review"
            rows = api_json(f"/api/v1/jobs/{job}/annotations?limit=1000")
            assert len(rows) == finished["processed_frames"] > 0
            assert all(
                row["bbox"] == [0.5, 0.5, 0.5, 0.5] and row["source_video_id"] == upload["video_id"] for row in rows
            )
            polygon = [0.1, 0.1, 0.4, 0.1, 0.4, 0.4, 0.1, 0.4]
            row = rows[0]
            edited = api_json(
                f"/api/v1/annotations/{row['id']}",
                {
                    "status": "accepted",
                    "class_name": "reviewed_object",
                    "polygon": polygon,
                    "bbox": [0.25, 0.25, 0.3, 0.3],
                },
                "PATCH",
            )
            assert edited["polygon"] == polygon and edited["bbox"] == [0.25, 0.25, 0.3, 0.3]
            saved = next(
                item for item in api_json(f"/api/v1/jobs/{job}/annotations?limit=1000") if item["id"] == row["id"]
            )
            assert saved["polygon"] == polygon and saved["status"] == "accepted"
            assert api_json(f"/api/v1/status/{job}")["result_url"] is None
            export = api_json(f"/api/v1/jobs/{job}/export", {"format": "segment"})
            url = export["download_url"]
            if url.startswith("http"):
                url = urllib.parse.urlsplit(url).path + "?" + urllib.parse.urlsplit(url).query
            archive_bytes = request(base, url, headers=headers)
            with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                assert manifest["task"] == "segment" and manifest["split_strategy"] == "group"
                assert len(manifest["samples"]) == len(rows)
                assert {sample["group_id"] for sample in manifest["samples"]} == {upload["video_id"]}
                sample = next(
                    sample
                    for sample in manifest["samples"]
                    if sample["source_path"].endswith(f"/{row['frame_id']}.jpg")
                )
                label = archive.read(sample["label_path"]).decode().strip()
                assert label.endswith(" ".join(f"{value:.6f}" for value in polygon))
                image_bytes = archive.read(sample["image_path"])
                assert image_bytes.startswith(b"\xff\xd8")
                assert image_bytes == request(base, row["frame_url"], headers=headers)
            report.update(
                passed=True,
                processed_frames=len(rows),
                source_video_id=upload["video_id"],
                checks=[
                    "migrations",
                    "HTTP health",
                    "static UI/assets",
                    "production labeler/trainer ping",
                    "HTTP upload",
                    "queued worker",
                    "saved geometry edit",
                    "explicit export/download",
                    "archive source grouping, edited polygon and frame bytes",
                ],
            )
    except Exception as error:
        report["error"] = str(error)
        for name in containers:
            try:
                print(f"Container {name}:\n{docker('logs', '--tail', '50', name)}")
            except (subprocess.SubprocessError, RuntimeError):
                pass
        raise
    finally:
        report["elapsed_seconds"] = time.monotonic() - started
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        Path(output).write_text(json.dumps(report, indent=2) + "\n")
        for name in reversed(containers):
            subprocess.run(["docker", "rm", "-f", "-v", name], capture_output=True, timeout=30, check=False)  # noqa: S603,S607
        if locals().get("network_created"):
            subprocess.run(["docker", "network", "rm", identity], capture_output=True, timeout=30, check=False)  # noqa: S603,S607
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--output", required=True)
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument("--runnable-digest")
    args = parser.parse_args()
    run(args.image, args.output, args.platform, args.runnable_digest)
