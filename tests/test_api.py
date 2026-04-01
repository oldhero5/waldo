import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """Create a test client. Requires running infrastructure."""
    from app.main import app

    return TestClient(app)


@pytest.mark.skipif(
    False,  # Set to False when infra is running
    reason="Requires running PostgreSQL, Redis, and MinIO",
)
class TestAPI:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_upload_no_file(self, client):
        resp = client.post("/api/v1/upload")
        assert resp.status_code == 422

    def test_status_not_found(self, client):
        resp = client.get("/api/v1/status/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_list_jobs_empty(self, client):
        resp = client.get("/api/v1/status")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
