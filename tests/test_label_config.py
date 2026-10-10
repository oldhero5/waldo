"""A promoted preview must retain its effective sampling configuration."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import label
from lib.auth import get_current_user
from lib.authorization import Principal, WorkspacePrincipal, get_workspace_principal
from lib.db import Base, LabelingJob, Project, User, Video, Workspace


@pytest.mark.parametrize("values", [{"threshold": -0.1}, {"threshold": 1.1}, {"fps": 0}, {"fps": -1}])
def test_invalid_label_configuration_rejected(values):
    with pytest.raises(ValidationError):
        label.LabelRequest(text_prompt="camera", **values)


def test_label_job_retains_promoted_threshold_and_sampling(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(label, "SessionLocal", factory)
    monkeypatch.setattr(label.label_video, "delay", lambda *_: SimpleNamespace(id="task-id"))
    with factory() as session:
        user = User(id=uuid.uuid4(), email="label@example.test", password_hash="x", display_name="Test")
        workspace = Workspace(id=uuid.uuid4(), name="Test", slug="test")
        project = Project(id=uuid.uuid4(), name="Test", workspace_id=workspace.id)
        video = Video(id=uuid.uuid4(), project_id=project.id, filename="a.mp4", minio_key="a.mp4")
        session.add_all([user, workspace, project, video])
        session.commit()
        principal = WorkspacePrincipal(Principal(user_id=user.id), workspace.id, "admin")
        app = FastAPI()
        app.include_router(label.router)
        app.dependency_overrides[get_current_user] = lambda: user
        app.dependency_overrides[get_workspace_principal] = lambda: principal
        response = TestClient(app).post(
            "/label",
            json={
                "video_id": str(video.id),
                "text_prompt": "camera",
                "threshold": 0.27,
                "fps": 4.0,
            },
        )
        assert response.status_code == 202, response.text
        job = session.get(LabelingJob, uuid.UUID(response.json()["job_id"]))
        assert job.score_threshold == 0.27
        assert job.sample_fps == 4.0
    engine.dispose()
