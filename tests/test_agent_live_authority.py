import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from lib.agent import tools
from lib.db import Base, Project, User, Workspace, WorkspaceMember


@pytest.fixture
def conversation(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(tools, "SessionLocal", factory)
    with factory() as session:
        user = User(id=uuid.uuid4(), email="live@example.test", display_name="Test", password_hash="x")
        workspace = Workspace(id=uuid.uuid4(), name="Test", slug="live")
        member = WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="admin")
        project = Project(name="private-project", workspace_id=workspace.id)
        session.add_all([user, workspace, member, project])
        session.commit()
        context = tools.AgentContext(str(user.id), str(workspace.id), True, "admin")
        tools.set_context(context)
        yield session, member, context
    engine.dispose()


def test_removed_member_cannot_read_during_existing_conversation(conversation):
    session, member, _ = conversation
    assert "private-project" in tools.list_projects.invoke({})
    session.delete(member)
    session.commit()
    with pytest.raises(RuntimeError, match="membership"):
        tools.list_projects.invoke({})


def test_stale_admin_context_cannot_act_after_downgrade(conversation):
    session, member, context = conversation
    tools._require_actions(context)
    member.role = "viewer"
    session.commit()
    with pytest.raises(RuntimeError, match="role"):
        tools._require_actions(context)


def test_system_info_scopes_hardware_to_api_and_reports_workspace_runtime_provider(conversation, monkeypatch):
    import json
    from types import SimpleNamespace

    from lib.agent import providers

    monkeypatch.setattr(
        providers,
        "get_config",
        lambda workspace: SimpleNamespace(provider="vllm", model="runtime-model", source="runtime"),
    )
    info = json.loads(tools.get_system_info.invoke({}))
    assert info["hardware_scope"] == "api_process"
    assert info["worker_hardware"] == "not_reported"
    assert "worker" in info["hardware_note"].lower()
    assert info["agent_model"] == "runtime-model"
    assert info["agent_provider"] == "vllm"
