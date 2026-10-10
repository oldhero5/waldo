import os
import sys
import uuid

import pytest


@pytest.fixture
def test_clip():
    """Path to a small test video clip."""
    from pathlib import Path

    clip = Path(__file__).parent / "fixtures" / "test_clip.mp4"
    if not clip.exists():
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


def pytest_configure(config):
    # Retained for older explicit auth tests; there is no global bypass now.
    config.addinivalue_line("markers", "no_auth_bypass: authentication is exercised without global overrides")
