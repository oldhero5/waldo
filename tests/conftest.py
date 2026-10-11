import os
import platform
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def test_clip():
    """Path to a small test video clip."""
    from pathlib import Path

    clip = Path(__file__).parent / "fixtures" / "test_clip.mp4"
    if not clip.exists():
        if os.environ.get("WALDO_WORKER_INTEGRATION") == "1":
            pytest.fail("Required worker fixture test_clip.mp4 is missing")
        pytest.skip("test_clip.mp4 not found in fixtures/")
    return clip


@pytest.fixture
def register_test_client():
    """Authenticate through the public registration API, without dependency overrides."""

    def register(client):
        identity = uuid.uuid4().hex
        response = client.post(
            "/api/v1/auth/register",
            json={
                "email": f"pytest-{identity}@example.invalid",
                "password": uuid.uuid4().hex,
                "display_name": "API test",
                "workspace_name": f"pytest-{identity}",
            },
        )
        assert response.status_code == 201, response.text
        client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
        me = client.get("/api/v1/auth/me")
        assert me.status_code == 200, me.text
        client.headers["X-Workspace-ID"] = me.json()["workspace_id"]
        assert me.json()["is_platform_admin"] is False
        return client

    return register


@pytest.fixture
def service_client(monkeypatch, register_test_client):
    """Opt in with WALDO_TEST_POSTGRES_URL pointing at a migrated disposable DB.

    Each case commits a unique test account/workspace, so explicitly enabled
    external workers can see its jobs. The disposable database's owner handles
    teardown; fixtures never touch the application's default database.
    """
    url = os.environ.get("WALDO_TEST_POSTGRES_URL")
    required_worker = os.environ.get("WALDO_WORKER_INTEGRATION") == "1"
    if required_worker:
        assert os.environ.get("WALDO_SERVICE_STACK") == "1", (
            "WALDO_WORKER_INTEGRATION=1 requires WALDO_SERVICE_STACK=1 on a disposable stack"
        )
        assert url, "WALDO_WORKER_INTEGRATION=1 requires WALDO_TEST_POSTGRES_URL"
        from sqlalchemy.engine import make_url

        from lib.config import settings

        assert make_url(url) == make_url(settings.postgres_dsn), (
            "WALDO_TEST_POSTGRES_URL must match the configured disposable PostgreSQL database"
        )
    if not url:
        pytest.skip("Set WALDO_TEST_POSTGRES_URL to a migrated disposable service-test database")
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.main import app

    engine = create_engine(url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for name, module in list(sys.modules.items()):
        if name in ("lib.db", "lib.auth", "lib.authorization", "lib.tasks") or name.startswith("app.api."):
            if hasattr(module, "SessionLocal"):
                monkeypatch.setattr(module, "SessionLocal", factory)
    client = TestClient(app)
    try:
        yield register_test_client(client)
    finally:
        client.close()
        engine.dispose()


@pytest.fixture
def worker_queue(monkeypatch):
    """Route only this test's labeling tasks to a disposable Redis queue."""
    if os.environ.get("WALDO_WORKER_INTEGRATION") != "1":
        yield None
        return
    from kombu import Queue

    from lib.config import settings
    from lib.tasks import app as celery_app

    queue = f"waldo-test-{uuid.uuid4().hex}"
    monkeypatch.setattr(celery_app.conf, "task_default_queue", queue)
    celery_app.amqp.queues.add(Queue(queue))
    assert not celery_app.conf.task_always_eager, "Worker integration requires queued Celery tasks"
    try:
        yield queue
    finally:
        from redis import Redis

        # The queue contains only messages created by this test.
        Redis.from_url(settings.redis_url).delete(queue)


@pytest.fixture
def worker_harness(worker_queue, tmp_path):
    """Start and stop one real Celery process for this test's queue."""
    if worker_queue is None:
        yield None
        return
    assert platform.system() == "Linux", "Run worker integration in the Linux service runner"
    control = tmp_path / "worker-control"
    control.mkdir()
    log_path = tmp_path / "celery.log"
    process = None

    def stop():
        nonlocal process
        if process is None:
            return None
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        exit_code = process.returncode
        process = None
        return exit_code

    def start():
        nonlocal process
        assert process is None, "Stop the prior worker before starting another"
        log_offset = log_path.stat().st_size if log_path.exists() else 0
        env = os.environ.copy()
        env["WALDO_TEST_WORKER_CONTROL_DIR"] = str(control)
        name = f"waldo-test-{uuid.uuid4().hex[:12]}@%h"
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "tests.worker_integration_worker:app",
                    "worker",
                    "--pool=solo",
                    "--concurrency=1",
                    "--loglevel=INFO",
                    "-Q",
                    worker_queue,
                    "--hostname",
                    name,
                ],
                cwd=Path(__file__).resolve().parent.parent,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            output = log_path.read_text(errors="replace")[log_offset:]
            if "ready." in output:
                return
            if process.poll() is not None:
                pytest.fail(f"Worker exited before ready ({process.returncode}):\n{output}")
            time.sleep(0.2)
        stop()
        pytest.fail(f"Worker did not become ready within 30s:\n{log_path.read_text(errors='replace')}")

    try:
        yield SimpleNamespace(start=start, stop=stop, control=control, log_path=log_path)
    finally:
        stop()


def pytest_configure(config):
    # Retained for older explicit auth tests; there is no global bypass now.
    config.addinivalue_line("markers", "no_auth_bypass: authentication is exercised without global overrides")
