from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import download
from lib.storage import get_download_url


def test_unsigned_object_denied_before_storage(monkeypatch):
    app = FastAPI()
    app.include_router(download.router, prefix="/api/v1")
    monkeypatch.setattr(download, "get_client", lambda: (_ for _ in ()).throw(AssertionError("unsigned read")))
    assert TestClient(app).get("/api/v1/download/frames/foreign.jpg").status_code == 401


def test_signed_download_is_exact_object_only(monkeypatch):
    app = FastAPI()
    app.include_router(download.router, prefix="/api/v1")
    storage = SimpleNamespace(stat_object=lambda *_: SimpleNamespace(size=3), get_object=lambda *_: iter([b"abc"]))
    monkeypatch.setattr(download, "get_client", lambda: storage)
    client = TestClient(app)
    url = get_download_url("frames/owned.jpg")
    response = client.get(url)
    assert response.status_code == 200
    assert response.content == b"abc"
    assert response.headers["cache-control"].startswith("private")
    token = parse_qs(urlsplit(url).query)["token"][0]
    assert client.get("/api/v1/download/frames/foreign.jpg", params={"token": token}).status_code == 401


def test_expired_and_access_token_cannot_download(monkeypatch):
    from datetime import UTC, datetime, timedelta

    from jose import jwt

    from lib.auth import create_access_token
    from lib.config import settings

    app = FastAPI()
    app.include_router(download.router, prefix="/api/v1")
    monkeypatch.setattr(download, "get_client", lambda: (_ for _ in ()).throw(AssertionError("invalid read")))
    expired = jwt.encode(
        {"type": "download", "object": "frames/x.jpg", "exp": datetime.now(UTC) - timedelta(seconds=1)},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    client = TestClient(app)
    for token in (expired, create_access_token("user")):
        assert client.get("/api/v1/download/frames/x.jpg", params={"token": token}).status_code == 401
