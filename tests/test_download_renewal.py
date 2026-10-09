"""Expired export capabilities renew only for a live, authorized job's exact object."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from jose import jwt

from app.api import download, review
from lib.auth import hash_password
from lib.config import settings
from lib.db import ApiKey, LabelingJob, Project, WorkspaceMember

JOB_ID = uuid.UUID("e2c559ec-779c-4992-8057-1705d879c974")
EXPORT_ID = uuid.UUID("c606739d-48eb-4b56-a84c-7ba7823b9cd7")
OTHER_JOB_ID = uuid.UUID("311cfe9c-ab9d-4a83-9fd7-c681b1e8d2bb")
LEGACY_KEY = f"results/{JOB_ID}/dataset.zip"
HISTORICAL_KEY = f"results/{JOB_ID}/exports/{EXPORT_ID}/dataset-detect.zip"


def expired_capability(key, **claims):
    payload = {"type": "download", "object": key, "exp": datetime.now(UTC) - timedelta(seconds=1)}
    payload.update(claims)
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@pytest.mark.parametrize(
    "key,current_key",
    [
        (LEGACY_KEY, None),
        (LEGACY_KEY, "results/new-current.zip"),
        ("results/current.zip", "results/current.zip"),
        (f"results/{OTHER_JOB_ID}/dataset.zip", f"results/{OTHER_JOB_ID}/dataset.zip"),
        *[
            (f"results/{JOB_ID}/exports/{EXPORT_ID}/dataset-{fmt}.zip", "results/new-current.zip")
            for fmt in ("segment", "detect", "obb", "classify", "pose")
        ],
    ],
)
def test_expired_export_renews_exact_current_legacy_or_historical_object(key, current_key):
    job = SimpleNamespace(id=JOB_ID, result_minio_key=current_key)
    assert review._renewable_export_object(expired_capability(key), job) == key


@pytest.mark.parametrize("expiry", [0, 0.5, 9999999999, 10**400])
def test_numeric_expiry_can_renew(expiry):
    job = SimpleNamespace(id=JOB_ID, result_minio_key=None)
    assert review._renewable_export_object(expired_capability(LEGACY_KEY, exp=expiry), job) == LEGACY_KEY


@pytest.mark.parametrize(
    "claims",
    [
        {"type": "access"},
        {"type": None},
        {"object": None},
        {"object": 5},
        {"object": [LEGACY_KEY]},
        {"object": ""},
        {"exp": None},
        {"exp": "123"},
        {"exp": True},
        {"exp": False},
        {"exp": float("nan")},
        {"exp": float("inf")},
        {"nbf": datetime.now(UTC) + timedelta(minutes=5)},
        {"aud": "another-service"},
    ],
)
def test_invalid_download_claims_are_unauthorized(claims):
    job = SimpleNamespace(id=JOB_ID, result_minio_key=LEGACY_KEY)
    with pytest.raises(HTTPException) as caught:
        review._renewable_export_object(expired_capability(LEGACY_KEY, **claims), job)
    assert caught.value.status_code == 401


@pytest.mark.parametrize("missing", ["type", "object", "exp"])
def test_missing_download_claim_is_unauthorized(missing):
    claims = {"type": "download", "object": LEGACY_KEY, "exp": 0}
    del claims[missing]
    token = jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    with pytest.raises(HTTPException) as caught:
        review._renewable_export_object(token, SimpleNamespace(id=JOB_ID, result_minio_key=LEGACY_KEY))
    assert caught.value.status_code == 401


def test_forged_download_signature_is_unauthorized():
    token = jwt.encode(
        {"type": "download", "object": LEGACY_KEY, "exp": 0}, "forged-secret", algorithm=settings.jwt_algorithm
    )
    with pytest.raises(HTTPException) as caught:
        review._renewable_export_object(token, SimpleNamespace(id=JOB_ID, result_minio_key=LEGACY_KEY))
    assert caught.value.status_code == 401


@pytest.mark.parametrize(
    "key",
    [
        f"results/{OTHER_JOB_ID}/dataset.zip",
        f"results/{OTHER_JOB_ID}/exports/{EXPORT_ID}/dataset-detect.zip",
        f"results/{str(JOB_ID).upper()}/dataset.zip",
        f"results/{JOB_ID.hex}/dataset.zip",
        LEGACY_KEY + "/suffix",
        HISTORICAL_KEY + "/suffix",
        HISTORICAL_KEY + ".bak",
        f"results/{JOB_ID}/exports/not-a-uuid/dataset-detect.zip",
        f"results/{JOB_ID}/exports/{EXPORT_ID.hex}/dataset-detect.zip",
        f"results/{JOB_ID}/exports/{str(EXPORT_ID).upper()}/dataset-detect.zip",
        f"results/{JOB_ID}/exports/{EXPORT_ID}/dataset-unknown.zip",
        f"results/{JOB_ID}/exports/{EXPORT_ID}/dataset-detect.ZIP",
        f"results/{JOB_ID}/exports/{EXPORT_ID}/../dataset-detect.zip",
        f"results/{JOB_ID}/../{OTHER_JOB_ID}/dataset.zip",
        f"results/{JOB_ID}/dataset.zip?other=1",
        "frames/owned.jpg",
    ],
)
def test_noncanonical_or_out_of_scope_export_object_is_unauthorized(key):
    job = SimpleNamespace(id=JOB_ID, result_minio_key="results/new-current.zip")
    with pytest.raises(HTTPException) as caught:
        review._renewable_export_object(expired_capability(key), job)
    assert caught.value.status_code == 401


@pytest.fixture
def renewal_job(service_client, monkeypatch):
    # URL signing is local. Any storage access or export dispatch is a regression.
    def unexpected_io(*args, **kwargs):
        pytest.fail("Download renewal must not access storage or generate an export")

    for name in ("download_file", "upload_file", "delete_object", "_write_review_export"):
        monkeypatch.setattr(review, name, unexpected_io)
    monkeypatch.setattr(download, "get_client", unexpected_io)
    workspace_id = uuid.UUID(service_client.headers["X-Workspace-ID"])
    with review.SessionLocal() as session:
        project = Project(name="Renewal", workspace_id=workspace_id)
        session.add(project)
        session.flush()
        job = LabelingJob(project_id=project.id, text_prompt="car", status="completed")
        session.add(job)
        session.flush()
        key = f"results/{job.id}/exports/{uuid.uuid4()}/dataset-detect.zip"
        job.result_minio_key = key
        session.commit()
        return service_client, job.id, workspace_id, key


def renew(client, job_id, key, **claims):
    return client.get(f"/api/v1/jobs/{job_id}/download-url", params={"token": expired_capability(key, **claims)})


def assert_exact_fresh_url(response, key):
    assert response.status_code == 200, response.text
    url = urlsplit(response.json()["download_url"])
    assert url.path == f"/api/v1/download/{key}"
    token = parse_qs(url.query)["token"][0]
    claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    assert claims["object"] == key
    assert claims["type"] == "download"
    remaining = claims["exp"] - datetime.now(UTC).timestamp()
    assert 0 < remaining <= 15 * 60


def test_owner_renews_same_object_after_current_pointer_changes(renewal_job):
    client, job_id, _, key = renewal_job
    with review.SessionLocal() as session:
        job = session.get(LabelingJob, job_id)
        job.result_minio_key = f"results/{job_id}/exports/{uuid.uuid4()}/dataset-segment.zip"
        session.commit()
    assert_exact_fresh_url(renew(client, job_id, key), key)


def test_viewer_can_renew_export(renewal_job):
    client, job_id, workspace_id, key = renewal_job
    with review.SessionLocal() as session:
        member = session.query(WorkspaceMember).filter_by(workspace_id=workspace_id).one()
        member.role = "viewer"
        session.commit()
    assert_exact_fresh_url(renew(client, job_id, key), key)


def test_read_only_api_key_can_renew_export(renewal_job):
    client, job_id, workspace_id, key = renewal_job
    raw_key = f"wld_{uuid.uuid4().hex}"
    with review.SessionLocal() as session:
        member = session.query(WorkspaceMember).filter_by(workspace_id=workspace_id).one()
        session.add(
            ApiKey(
                user_id=member.user_id,
                workspace_id=workspace_id,
                name="Renewal read",
                key_hash=hash_password(raw_key),
                key_prefix=raw_key[:8],
                scopes=["read"],
            )
        )
        session.commit()
    client.headers["Authorization"] = f"Bearer {raw_key}"
    assert_exact_fresh_url(renew(client, job_id, key), key)


def test_missing_authentication_cannot_renew(renewal_job):
    client, job_id, _, key = renewal_job
    del client.headers["Authorization"]
    assert renew(client, job_id, key).status_code == 401


def test_unrelated_workspace_cannot_renew_valid_capability(renewal_job, register_test_client):
    client, job_id, _, key = renewal_job
    del client.headers["X-Workspace-ID"]
    register_test_client(client)
    assert renew(client, job_id, key).status_code == 404


def test_deleted_job_cannot_renew_valid_capability(renewal_job):
    client, job_id, _, key = renewal_job
    with review.SessionLocal() as session:
        session.delete(session.get(LabelingJob, job_id))
        session.commit()
    assert renew(client, job_id, key).status_code == 404


def test_valid_capability_for_another_job_cannot_renew(renewal_job):
    client, job_id, _, _ = renewal_job
    assert renew(client, job_id, f"results/{OTHER_JOB_ID}/dataset.zip").status_code == 401


def test_duplicate_alias_renews_only_while_it_is_current(renewal_job):
    client, job_id, _, _ = renewal_job
    source_key = f"results/{OTHER_JOB_ID}/exports/{EXPORT_ID}/dataset-detect.zip"
    with review.SessionLocal() as session:
        job = session.get(LabelingJob, job_id)
        job.result_minio_key = source_key
        session.commit()
    assert_exact_fresh_url(renew(client, job_id, source_key), source_key)
    with review.SessionLocal() as session:
        job = session.get(LabelingJob, job_id)
        job.result_minio_key = None
        session.commit()
    assert renew(client, job_id, source_key).status_code == 401


def test_renewal_does_not_allow_expired_ordinary_download(renewal_job):
    client, job_id, _, key = renewal_job
    token = expired_capability(key)
    assert client.get(f"/api/v1/download/{key}", params={"token": token}).status_code == 401
    assert_exact_fresh_url(renew(client, job_id, key), key)


def test_owner_can_renew_legacy_export_without_current_pointer(renewal_job):
    client, job_id, _, _ = renewal_job
    key = f"results/{job_id}/dataset.zip"
    with review.SessionLocal() as session:
        session.get(LabelingJob, job_id).result_minio_key = None
        session.commit()
    assert_exact_fresh_url(renew(client, job_id, key), key)


@pytest.mark.parametrize("invalid_token", ["forged", "wrong-type", "missing-expiry"])
def test_endpoint_rejects_invalid_capability(renewal_job, invalid_token):
    client, job_id, _, key = renewal_job
    claims = {"type": "download", "object": key, "exp": 0}
    secret = settings.jwt_secret
    if invalid_token == "forged":
        secret = "forged-secret"
    elif invalid_token == "wrong-type":
        claims["type"] = "access"
    else:
        del claims["exp"]
    token = jwt.encode(claims, secret, algorithm=settings.jwt_algorithm)
    response = client.get(f"/api/v1/jobs/{job_id}/download-url", params={"token": token})
    assert response.status_code == 401
