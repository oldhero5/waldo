"""Opt-in checks against a migrated disposable PostgreSQL database.

Set WALDO_TEST_POSTGRES_URL explicitly. Each case rolls back its own outer
transaction; this module never creates, migrates or drops a database.
"""

import os
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.api import status, upload
from lib import auth, authorization
from lib.db import Base, LabelingJob, Project, User, Video, Workspace, WorkspaceMember


@pytest.fixture
def postgres(monkeypatch):
    url = os.environ.get("WALDO_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set WALDO_TEST_POSTGRES_URL to a migrated disposable database")
    engine = create_engine(url)
    with engine.connect() as connection:
        transaction = connection.begin()
        factory = sessionmaker(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        for module in (auth, authorization, upload, status):
            monkeypatch.setattr(module, "SessionLocal", factory)
        try:
            yield connection, factory
        finally:
            transaction.rollback()
    engine.dispose()


def test_migrated_postgres_has_all_mapped_columns(postgres):
    connection, _ = postgres
    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        actual = {c["name"] for c in inspector.get_columns(table.name)}
        assert {c.name for c in table.columns} <= actual, table.name
    assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "3d4e5f6a7b8c"


def test_real_postgres_workspace_boundary_and_partial_provenance(postgres):
    _, factory = postgres
    with factory() as session:
        own, foreign = [Workspace(name=n, slug=uuid.uuid4().hex) for n in ("Own", "Foreign")]
        user = User(email=f"{uuid.uuid4()}@example.test", display_name="PG test", password_hash="x")
        session.add_all([own, foreign, user])
        session.flush()
        member = WorkspaceMember(workspace_id=own.id, user_id=user.id, role="viewer")
        projects = [Project(name=w.name, workspace_id=w.id) for w in (own, foreign)]
        session.add_all([member, *projects])
        session.flush()
        videos = [Video(project_id=p.id, filename=f"{p.name}.mp4", minio_key=f"{p.id}.mp4") for p in projects]
        session.add_all(videos)
        session.flush()
        jobs = [
            LabelingJob(
                project_id=v.project_id,
                video_id=v.id,
                text_prompt="camera",
                status="partial",
                sample_fps=4,
                score_threshold=0.27,
                processing_summary={"coverage": "sampled", "videos": [{"video_id": str(v.id), "status": "failed"}]},
            )
            for v in videos
        ]
        session.add_all(jobs)
        session.commit()
        app = FastAPI()
        app.include_router(upload.router)
        app.include_router(status.router)
        client = TestClient(app)
        headers = {"Authorization": f"Bearer {auth.create_access_token(str(user.id))}"}
        response = client.get(f"/status/{jobs[0].id}", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "partial"
        assert response.json()["processing_summary"]["coverage"] == "sampled"
        assert response.json()["sample_fps"] == 4
        assert client.get(f"/status/{jobs[1].id}", headers=headers).status_code == 404
        assert (
            client.post(
                "/link-videos", json={"video_ids": [str(videos[0].id)], "target_project_name": "Copy"}, headers=headers
            ).status_code
            == 403
        )
        session.delete(member)
        session.commit()
        assert client.get(f"/status/{jobs[0].id}", headers=headers).status_code == 403
