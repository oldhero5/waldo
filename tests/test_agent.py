"""Tests for the LangGraph agent — tools, graph, and HTTP endpoint.

The LLM call itself is mocked so these run fast without an Ollama server.
We assert:

  * AgentContext scopes tool calls to a workspace.
  * Action tools are blocked when allow_actions=False.
  * The ReAct loop turns tool_calls -> ToolMessage -> final answer.
  * /api/v1/agent/chat threads context, returns the final content.
  * /api/v1/agent/stream emits the expected SSE event sequence.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage

# Force action-tool tests not to enqueue Celery (no broker in CI).
os.environ.setdefault("WALDO_AGENT_SKIP_DISPATCH", "1")


# ── Fixtures ───────────────────────────────────────────────────────
@pytest.fixture
def db_session():
    from lib.db import SessionLocal

    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture
def workspace_with_data(db_session):
    """Build a tiny workspace with project + video + completed job + model."""
    from lib.db import LabelingJob, ModelRegistry, Project, Video, Workspace

    ws = Workspace(name="test-agent-ws", slug=f"test-{uuid.uuid4().hex[:8]}")
    db_session.add(ws)
    db_session.flush()

    proj = Project(name="agent-test-proj", workspace_id=ws.id)
    db_session.add(proj)
    db_session.flush()

    video = Video(
        project_id=proj.id,
        filename="agent.mp4",
        minio_key="agent.mp4",
        fps=30.0,
        duration_s=10.0,
        width=1920,
        height=1080,
        frame_count=300,
    )
    db_session.add(video)
    db_session.flush()

    job = LabelingJob(
        video_id=video.id,
        project_id=proj.id,
        text_prompt="car",
        prompt_type="text",
        task_type="segment",
        status="completed",
        total_frames=300,
        processed_frames=300,
    )
    db_session.add(job)
    db_session.flush()

    model = ModelRegistry(
        project_id=proj.id,
        training_run_id=uuid.uuid4(),  # foreign key not enforced in dev sqlite-style flow
        name="best-yolo",
        task_type="segment",
        model_variant="yolo26n-seg",
        weights_minio_key="weights.pt",
        metrics={"metrics/mAP50(B)": 0.78},
        is_active=False,
        created_at=datetime.now(UTC),
    )
    # training_run_id has a real FK; skip writing the model if FK enforcement
    # is on by inserting a parent TrainingRun first.
    from lib.db import TrainingRun

    run = TrainingRun(
        project_id=proj.id,
        job_id=job.id,
        name="run-for-tests",
        task_type="segment",
        model_variant="yolo26n-seg",
        status="completed",
        total_epochs=10,
        epoch_current=10,
    )
    db_session.add(run)
    db_session.flush()
    model.training_run_id = run.id
    db_session.add(model)
    db_session.commit()

    yield {"workspace": ws, "project": proj, "video": video, "job": job, "model": model, "run": run}

    # Cleanup is FK-order sensitive AND has to handle rows created by tests
    # themselves (e.g. start_labeling_job inserts a brand-new LabelingJob).
    # We rollback first to clear any aborted txn, then bulk-delete by parent id.
    from lib.db import (
        Annotation as Ann,
    )
    from lib.db import (
        LabelingJob as LJ,
    )
    from lib.db import (
        ModelRegistry as MR,
    )
    from lib.db import (
        TrainingRun as TR,
    )
    from lib.db import (
        Video as V,
    )

    db_session.rollback()
    pid = proj.id
    db_session.query(Ann).filter(Ann.job_id.in_(db_session.query(LJ.id).filter_by(project_id=pid))).delete(
        synchronize_session=False
    )
    db_session.query(MR).filter_by(project_id=pid).delete(synchronize_session=False)
    db_session.query(TR).filter_by(project_id=pid).delete(synchronize_session=False)
    db_session.query(LJ).filter_by(project_id=pid).delete(synchronize_session=False)
    db_session.query(V).filter_by(project_id=pid).delete(synchronize_session=False)
    db_session.commit()
    try:
        db_session.delete(proj)
        db_session.delete(ws)
        db_session.commit()
    except Exception:  # noqa: BLE001
        db_session.rollback()


@pytest.fixture
def ctx_for(workspace_with_data):
    from lib.agent.tools import AgentContext

    def _make(*, allow_actions: bool = True) -> AgentContext:
        return AgentContext(
            user_id=str(uuid.uuid4()),
            workspace_id=str(workspace_with_data["workspace"].id),
            allow_actions=allow_actions,
        )

    return _make


# ── Tool layer ─────────────────────────────────────────────────────
class TestTools:
    def test_list_projects_scoped_to_workspace(self, ctx_for, workspace_with_data):
        from lib.agent.tools import list_projects, set_context

        set_context(ctx_for())
        out = list_projects.invoke({})
        assert workspace_with_data["project"].name in out

    def test_list_projects_excludes_other_workspace(self, db_session, ctx_for):
        """A workspace that isn't ours should not show up."""
        from lib.agent.tools import list_projects, set_context
        from lib.db import Project, Workspace

        other_ws = Workspace(name="not-mine", slug=f"other-{uuid.uuid4().hex[:8]}")
        db_session.add(other_ws)
        db_session.flush()
        other_proj = Project(name="alien-project", workspace_id=other_ws.id)
        db_session.add(other_proj)
        db_session.commit()

        try:
            set_context(ctx_for())
            out = list_projects.invoke({})
            assert "alien-project" not in out
        finally:
            db_session.delete(other_proj)
            db_session.delete(other_ws)
            db_session.commit()

    def test_list_models_returns_active_status(self, db_session, ctx_for, workspace_with_data):
        from lib.agent.tools import list_models, set_context

        # Flip the seeded model active and verify the tool surfaces that.
        workspace_with_data["model"].is_active = True
        db_session.commit()

        set_context(ctx_for())
        out = list_models.invoke({})
        assert "best-yolo" in out
        assert "[ACTIVE]" in out

    def test_get_system_info_includes_device(self, ctx_for):
        from lib.agent.tools import get_system_info, set_context

        set_context(ctx_for())
        out = get_system_info.invoke({})
        info = json.loads(out)
        assert "device" in info
        assert "agent_model" in info
        assert info["agent_model"]  # non-empty default

    def test_action_tool_blocked_when_read_only(self, ctx_for, workspace_with_data):
        """allow_actions=False must refuse start_labeling_job."""
        from lib.agent.tools import set_context, start_labeling_job

        set_context(ctx_for(allow_actions=False))
        with pytest.raises(Exception, match="read-only"):
            start_labeling_job.invoke({"video_id": str(workspace_with_data["video"].id), "text_prompt": "car"})

    def test_start_labeling_job_creates_pending_row(self, db_session, ctx_for, workspace_with_data):
        """Action tool dispatches a real DB row (Celery dispatch is stubbed)."""
        from lib.agent.tools import set_context, start_labeling_job
        from lib.db import LabelingJob

        set_context(ctx_for())
        out = start_labeling_job.invoke(
            {
                "video_id": str(workspace_with_data["video"].id),
                "text_prompt": "delivery truck",
            }
        )
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["text_prompt"] == "delivery truck"
        assert payload["ui_url"].startswith("/review/")

        job = db_session.query(LabelingJob).filter_by(id=payload["job_id"]).one()
        assert job.text_prompt == "delivery truck"
        assert job.status == "pending"
        # Test mode is configured to skip Celery dispatch — celery_task_id stays None.
        assert job.celery_task_id is None

    def test_start_labeling_job_rejects_foreign_video(self, db_session, ctx_for):
        """A video outside the workspace must be rejected."""
        from lib.agent.tools import set_context, start_labeling_job
        from lib.db import Project, Video, Workspace

        ws2 = Workspace(name="ws2", slug=f"ws2-{uuid.uuid4().hex[:8]}")
        db_session.add(ws2)
        db_session.flush()
        proj2 = Project(name="p2", workspace_id=ws2.id)
        db_session.add(proj2)
        db_session.flush()
        v = Video(project_id=proj2.id, filename="x.mp4", minio_key="x.mp4")
        db_session.add(v)
        db_session.commit()

        try:
            set_context(ctx_for())  # caller is in a different workspace
            with pytest.raises(ValueError, match="not found in your workspace"):
                start_labeling_job.invoke({"video_id": str(v.id), "text_prompt": "car"})
        finally:
            db_session.delete(v)
            db_session.delete(proj2)
            db_session.delete(ws2)
            db_session.commit()

    def test_start_training_validates_args(self, ctx_for, workspace_with_data):
        from lib.agent.tools import set_context, start_training

        set_context(ctx_for())
        with pytest.raises(ValueError, match="epochs"):
            start_training.invoke(
                {
                    "job_id": str(workspace_with_data["job"].id),
                    "name": "x",
                    "epochs": 10000,
                }
            )

    def test_activate_model_sets_only_one_active(self, db_session, ctx_for, workspace_with_data):
        """Activating a model must clear the prior active row in the same workspace."""
        from lib.agent.tools import activate_model, set_context
        from lib.db import ModelRegistry

        # Add a second model and mark it active so we can prove the swap.
        prior = ModelRegistry(
            project_id=workspace_with_data["project"].id,
            training_run_id=workspace_with_data["run"].id,
            name="prior-active",
            task_type="segment",
            model_variant="yolo26n-seg",
            weights_minio_key="prior.pt",
            metrics={},
            is_active=True,
        )
        db_session.add(prior)
        db_session.commit()

        try:
            set_context(ctx_for())
            out = activate_model.invoke({"model_id": str(workspace_with_data["model"].id)})
            payload = json.loads(out)
            assert payload["ok"] is True

            db_session.refresh(prior)
            db_session.refresh(workspace_with_data["model"])
            assert prior.is_active is False
            assert workspace_with_data["model"].is_active is True
        finally:
            db_session.delete(prior)
            db_session.commit()


# ── Graph layer ────────────────────────────────────────────────────
class FakeChatOllama:
    """Minimal ChatOllama stand-in that returns canned messages.

    The agent loop calls ``invoke(messages)``; we step through ``responses``
    in order so we can simulate "first turn = tool call, second turn = final answer".
    """

    def __init__(self, responses: list[AIMessage]):
        self._responses = list(responses)

    def bind_tools(self, _tools):
        return self

    def invoke(self, _messages, **_kwargs):
        if not self._responses:
            return AIMessage(content="(no more canned responses)")
        return self._responses.pop(0)


class TestGraph:
    def test_run_agent_no_tools(self, ctx_for, monkeypatch):
        from lib.agent import graph as graph_mod
        from lib.agent.graph import run_agent

        monkeypatch.setattr(
            graph_mod,
            "_build_llm",
            lambda *a, **kw: FakeChatOllama([AIMessage(content="hello world")]),
        )

        out = run_agent(
            [{"role": "user", "content": "hi"}],
            context=ctx_for(),
        )
        assert out["content"] == "hello world"
        assert out["tool_calls"] == []

    def test_run_agent_with_tool_call(self, ctx_for, workspace_with_data, monkeypatch):
        """LLM asks for list_models, ToolNode executes, LLM finalizes."""
        from lib.agent import graph as graph_mod
        from lib.agent.graph import run_agent

        # Mark the model active so list_models has something to say.
        from lib.db import SessionLocal

        s = SessionLocal()
        try:
            from lib.db import ModelRegistry as MR

            s.query(MR).filter_by(id=workspace_with_data["model"].id).update({MR.is_active: True})
            s.commit()
        finally:
            s.close()

        first_turn = AIMessage(
            content="",
            tool_calls=[{"name": "list_models", "args": {}, "id": "call_1", "type": "tool_call"}],
        )
        second_turn = AIMessage(content="You have one model: best-yolo (mAP50=0.78).")
        monkeypatch.setattr(graph_mod, "_build_llm", lambda *a, **kw: FakeChatOllama([first_turn, second_turn]))

        out = run_agent(
            [{"role": "user", "content": "What models do I have?"}],
            context=ctx_for(),
        )
        assert "best-yolo" in out["content"]
        assert any(tc["name"] == "list_models" for tc in out["tool_calls"])

    def test_run_agent_action_blocked_in_read_only(self, ctx_for, workspace_with_data, monkeypatch):
        """When ctx.allow_actions=False, the action tool should not be bound."""
        from lib.agent import graph as graph_mod
        from lib.agent.graph import run_agent
        from lib.agent.tools import ACTION_TOOLS, READ_TOOLS

        captured: dict = {}

        original_build_graph = graph_mod.build_graph

        def spying_build_graph(*, model=None, allow_actions=True):
            captured["allow_actions"] = allow_actions
            return original_build_graph(model=model, allow_actions=allow_actions)

        monkeypatch.setattr(graph_mod, "build_graph", spying_build_graph)
        monkeypatch.setattr(graph_mod, "_build_llm", lambda *a, **kw: FakeChatOllama([AIMessage(content="ok")]))

        run_agent(
            [{"role": "user", "content": "noop"}],
            context=ctx_for(allow_actions=False),
        )
        assert captured["allow_actions"] is False
        # Defensive: ensure read/action sets are non-empty so the assertion above
        # actually means something.
        assert READ_TOOLS and ACTION_TOOLS


# ── HTTP endpoint ─────────────────────────────────────────────────
class TestEndpoint:
    def _client(self):
        from fastapi.testclient import TestClient

        from app.main import app

        return TestClient(app)

    def test_chat_returns_content(self, monkeypatch, workspace_with_data):
        from lib.agent import graph as graph_mod

        monkeypatch.setattr(
            graph_mod,
            "_build_llm",
            lambda *a, **kw: FakeChatOllama([AIMessage(content="hi from agent")]),
        )

        # Pin the auth bypass user to the test workspace so workspace_id resolves.
        from app.main import app
        from lib.auth import get_current_user
        from lib.db import SessionLocal, User, WorkspaceMember

        s = SessionLocal()
        try:
            user = User(email=f"agent-{uuid.uuid4().hex}@waldo.local", password_hash="x", display_name="t")
            s.add(user)
            s.flush()
            s.add(
                WorkspaceMember(
                    workspace_id=workspace_with_data["workspace"].id,
                    user_id=user.id,
                    role="admin",
                )
            )
            s.commit()
            user_id = user.id
        finally:
            s.close()

        async def _fake_user():
            from lib.db import SessionLocal as SL

            ss = SL()
            try:
                return ss.query(User).filter_by(id=user_id).one()
            finally:
                ss.close()

        app.dependency_overrides[get_current_user] = _fake_user
        try:
            client = self._client()
            r = client.post(
                "/api/v1/agent/chat",
                json={"messages": [{"role": "user", "content": "ping"}]},
                headers={"Authorization": "Bearer test"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["content"] == "hi from agent"
            assert body["model"]
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            ss = SessionLocal()
            try:
                ss.query(WorkspaceMember).filter_by(user_id=user_id).delete()
                ss.query(User).filter_by(id=user_id).delete()
                ss.commit()
            finally:
                ss.close()

    def test_chat_rejects_empty_messages(self):
        client = self._client()
        r = client.post("/api/v1/agent/chat", json={"messages": []})
        assert r.status_code == 400

    def test_stream_emits_done_event(self, monkeypatch):
        from lib.agent import graph as graph_mod

        monkeypatch.setattr(
            graph_mod,
            "_build_llm",
            lambda *a, **kw: FakeChatOllama([AIMessage(content="streamed")]),
        )

        client = self._client()
        with client.stream(
            "POST",
            "/api/v1/agent/stream",
            json={"messages": [{"role": "user", "content": "ping"}]},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            events = []
            for chunk in resp.iter_text():
                for line in chunk.splitlines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:].strip()))
        types = [e.get("type") for e in events]
        assert "done" in types
