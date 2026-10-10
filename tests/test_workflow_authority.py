"""Offline workflow authority and execution regressions with real SQLite ownership."""

from types import SimpleNamespace
from uuid import UUID

import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import workflows as api
from lib import authorization, workflow_engine
from lib.authorization import Principal, WorkspacePrincipal
from lib.db import (
    Base,
    LabelingJob,
    ModelRegistry,
    Project,
    SavedWorkflow,
    TrainingRun,
    User,
    Video,
    Workspace,
    WorkspaceMember,
)
from lib.workflow_blocks import platform
from lib.workflow_blocks.base import BlockBase, BlockResult, Port
from lib.workflow_blocks.logic import ConditionalBlock


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(authorization, "SessionLocal", factory)
    with factory() as session:
        own, foreign = Workspace(name="Own", slug="own"), Workspace(name="Foreign", slug="foreign")
        user = User(email="workflow@example.test", password_hash="x", display_name="Workflow")
        session.add_all([own, foreign, user])
        session.flush()
        session.add(WorkspaceMember(user_id=user.id, workspace_id=own.id, role="admin"))
        projects = [Project(name="Own", workspace_id=own.id), Project(name="Foreign", workspace_id=foreign.id)]
        session.add_all(projects)
        session.flush()
        videos = [Video(project_id=p.id, filename=f"{p.name}.mp4", minio_key=f"{p.name}.mp4") for p in projects]
        session.add_all(videos)
        session.flush()
        jobs = [LabelingJob(video_id=v.id, status="completed", result_minio_key=f"{v.filename}.zip") for v in videos]
        session.add_all(jobs)
        session.flush()
        runs = [
            TrainingRun(project_id=p.id, job_id=j.id, name=p.name, model_variant="yolo26n-seg")
            for p, j in zip(projects, jobs, strict=True)
        ]
        session.add_all(runs)
        session.flush()
        models = [
            ModelRegistry(
                project_id=p.id,
                training_run_id=r.id,
                name=p.name,
                task_type="segment",
                model_variant="yolo26n-seg",
                weights_minio_key=f"{p.name}.pt",
                is_active=True,
            )
            for p, r in zip(projects, runs, strict=True)
        ]
        session.add_all(models)
        session.commit()
        principal = WorkspacePrincipal(Principal(user.id), own.id, "admin")
        yield SimpleNamespace(
            session=session,
            factory=factory,
            user=user,
            own=own,
            foreign=foreign,
            projects=projects,
            videos=videos,
            jobs=jobs,
            models=models,
            principal=principal,
        )
    engine.dispose()


class Source(BlockBase):
    name = "source"
    output_ports = [Port("value", "any")]

    def execute(self, inputs):
        return BlockResult(outputs={"value": self.config.get("value", 0)})


class SideEffect(BlockBase):
    name = "effect"
    input_ports = [Port("data", "any", required=False)]
    output_ports = [Port("data", "any")]
    calls = []

    def execute(self, inputs):
        self.calls.append(self.config.get("label", "effect"))
        return BlockResult(outputs={"data": inputs.get("data")})


@pytest.fixture
def registry(monkeypatch):
    SideEffect.calls = []
    monkeypatch.setattr(
        workflow_engine, "BLOCK_REGISTRY", {"source": Source, "effect": SideEffect, "conditional": ConditionalBlock}
    )


def test_missing_required_input_rejected_before_independent_side_effect(registry):
    result = workflow_engine.execute_workflow(
        {
            "nodes": [
                {"id": "effect", "type": "effect"},
                {"id": "invalid", "type": "conditional"},
            ],
            "edges": [],
        }
    )
    assert result["errors"]
    assert SideEffect.calls == []


@pytest.mark.parametrize(
    "graph",
    [
        {"nodes": [{"id": "effect", "type": "effect"}, {"id": "effect", "type": "effect"}], "edges": []},
        {
            "nodes": [{"id": "effect", "type": "effect"}],
            "edges": [{"source": "absent", "source_port": "data", "target": "effect", "target_port": "data"}],
        },
        {
            "nodes": [{"id": "effect", "type": "effect"}],
            "edges": [{"source": "effect", "source_port": "data", "target": "effect", "target_port": "data"}],
        },
        {"nodes": [{"id": "effect", "type": "effect"}, {"id": "unknown", "type": "missing"}], "edges": []},
    ],
)
def test_invalid_graph_cannot_execute_any_block(registry, graph):
    result = workflow_engine.execute_workflow(graph)
    assert result["errors"]
    assert SideEffect.calls == []


def test_false_branch_skips_optional_trigger_and_descendants(registry):
    graph = {
        "nodes": [
            {"id": "source", "type": "source"},
            {"id": "condition", "type": "conditional", "config": {"operator": "gt", "threshold": 0}},
            {"id": "trigger", "type": "effect"},
            {"id": "later", "type": "effect"},
            {"id": "independent", "type": "effect", "config": {"label": "independent"}},
        ],
        "edges": [
            {"source": "source", "source_port": "value", "target": "condition", "target_port": "value"},
            {"source": "condition", "source_port": "passed", "target": "trigger", "target_port": "data"},
            {"source": "trigger", "source_port": "data", "target": "later", "target_port": "data"},
        ],
    }
    result = workflow_engine.execute_workflow(graph)
    assert result["errors"] == []
    assert SideEffect.calls == ["independent"]
    assert result["metadata"]["trigger"]["skipped"] is True
    assert result["metadata"]["later"]["skipped"] is True


def api_client(principal):
    app = FastAPI()
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[authorization.get_workspace_principal] = lambda: principal
    return TestClient(app)


def test_workflow_crud_excludes_foreign_and_legacy_workspace(db, registry):
    graph = {"nodes": [{"id": "source", "type": "source"}], "edges": []}
    for slug, ws in [("own", db.own.id), ("foreign", db.foreign.id), ("legacy", None)]:
        db.session.add(SavedWorkflow(name=slug, slug=slug, graph=graph, workspace_id=ws))
    db.session.commit()
    client = api_client(db.principal)
    assert [row["slug"] for row in client.get("/api/v1/workflows/saved").json()] == ["own"]
    for slug in ["foreign", "legacy"]:
        assert client.get(f"/api/v1/workflows/saved/{slug}").status_code == 404
        assert client.delete(f"/api/v1/workflows/saved/{slug}").status_code == 404
        assert client.post(f"/api/v1/workflows/saved/{slug}/deploy").status_code == 404
    response = client.post("/api/v1/workflows", json={"name": "New", "graph": graph})
    assert response.status_code == 201
    assert db.session.get(SavedWorkflow, UUID(response.json()["id"])).workspace_id == db.own.id


def test_viewer_and_read_only_key_cannot_save_or_execute_workflow(db, registry):
    graph = {"nodes": [{"id": "effect", "type": "effect"}], "edges": []}
    actors = [
        WorkspacePrincipal(db.principal.identity, db.own.id, "viewer"),
        WorkspacePrincipal(
            Principal(db.user.id, auth_kind="api_key", workspace_id=db.own.id, scopes=frozenset({"read"})),
            db.own.id,
            "admin",
        ),
    ]
    for principal in actors:
        client = api_client(principal)
        assert client.post("/api/v1/workflows", json={"name": "Forbidden", "graph": graph}).status_code == 403
        assert client.post("/api/v1/workflows/run", json={"graph": graph}).status_code == 403
    assert SideEffect.calls == []


def test_model_selector_uses_owned_pool_entry_without_reloading_global_engine(db, monkeypatch):
    from lib import db as db_module
    from lib import inference_engine

    monkeypatch.setattr(db_module, "SessionLocal", db.factory)
    selected = []
    engine = SimpleNamespace(model_name="Own", predict_image=lambda image, **kwargs: ["detection"])
    monkeypatch.setattr(
        inference_engine,
        "get_pool",
        lambda: SimpleNamespace(get_model=lambda model_id: selected.append(model_id) or engine),
    )
    monkeypatch.setattr(inference_engine, "get_engine", lambda: pytest.fail("global active engine must not be used"))
    block = platform.ModelSelectorBlock({"model_id": str(db.models[0].id)})
    block.execution_principal = db.principal
    assert block.execute({"image": np.zeros((2, 2, 3))}).outputs["detections"] == ["detection"]
    assert selected == [str(db.models[0].id)]
    block.config["model_id"] = str(db.models[1].id)
    with pytest.raises(HTTPException) as error:
        block.execute({"image": np.zeros((2, 2, 3))})
    assert error.value.status_code == 404
    assert len(selected) == 1


def test_train_trigger_rejects_foreign_dataset_and_missing_context_before_task(db, monkeypatch):
    from lib import db as db_module
    from lib import tasks

    monkeypatch.setattr(db_module, "SessionLocal", db.factory)
    monkeypatch.setattr(tasks.train_model, "delay", lambda *args: pytest.fail("unauthorized task was queued"))
    block = platform.TrainTriggerBlock({"dataset_id": str(db.jobs[1].id), "workspace_id": str(db.foreign.id)})
    block.execution_principal = db.principal
    with pytest.raises(HTTPException) as error:
        block.execute({})
    assert error.value.status_code == 404
    block = platform.TrainTriggerBlock({"dataset_id": str(db.jobs[0].id), "user_id": str(db.user.id)})
    with pytest.raises((HTTPException, ValueError)):
        block.execute({})


def test_dataset_foreign_id_denied_before_storage_read(db, monkeypatch):
    from lib import db as db_module
    from lib import storage

    monkeypatch.setattr(db_module, "SessionLocal", db.factory)
    monkeypatch.setattr(storage, "download_file", lambda *args: pytest.fail("foreign storage was read"))
    block = platform.DatasetInputBlock({"dataset_id": str(db.jobs[1].id)})
    block.execution_principal = db.principal
    with pytest.raises(HTTPException) as error:
        block.execute({})
    assert error.value.status_code == 404


def test_training_uses_dataset_video_project_and_rechecks_changed_membership(db, monkeypatch):
    from lib import db as db_module
    from lib import tasks

    monkeypatch.setattr(db_module, "SessionLocal", db.factory)
    queued = []
    monkeypatch.setattr(
        tasks.train_model, "delay", lambda run_id: queued.append(run_id) or SimpleNamespace(id="queued-task")
    )
    block = platform.TrainTriggerBlock({"dataset_id": str(db.jobs[0].id), "epochs": 2})
    block.execution_principal = db.principal
    result = block.execute({})
    run = db.session.get(TrainingRun, UUID(result.outputs["run_id"]))
    assert run.project_id == db.projects[0].id
    assert run.job_id == db.jobs[0].id
    assert queued == [str(run.id)]
    member = db.session.query(WorkspaceMember).filter_by(user_id=db.user.id).one()
    member.role = "viewer"
    db.session.commit()
    with pytest.raises(HTTPException) as error:
        block.execute({})
    assert error.value.status_code == 403
    assert len(queued) == 1


def test_workspace_active_model_does_not_select_foreign_global_active(db, monkeypatch):
    from lib import db as db_module
    from lib import inference_engine

    monkeypatch.setattr(db_module, "SessionLocal", db.factory)
    db.models[0].is_active = False
    db.session.commit()
    monkeypatch.setattr(inference_engine, "get_pool", lambda: pytest.fail("foreign active model must not be loaded"))
    block = platform.ModelSelectorBlock()
    block.execution_principal = db.principal
    with pytest.raises(ValueError, match="No active model in this workspace"):
        block.execute({"image": np.zeros((2, 2, 3))})


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "fd00::1", "0.0.0.0"])
def test_webhook_rejects_private_or_local_dns_even_with_graph_opt_in(monkeypatch, address):
    import socket

    monkeypatch.delenv("WALDO_WORKFLOW_ALLOW_PRIVATE_WEBHOOKS", raising=False)
    monkeypatch.setattr(
        platform.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))],
    )
    with pytest.raises(ValueError, match="public IP"):
        platform._webhook_destination("https://attacker.example/webhook")


def test_webhook_connection_is_pinned_preserves_tls_host_and_does_not_follow_redirects(db, monkeypatch):
    import socket

    import httpx

    from lib import db as db_module

    monkeypatch.setattr(db_module, "SessionLocal", db.factory)
    monkeypatch.delenv("WALDO_WORKFLOW_ALLOW_PRIVATE_WEBHOOKS", raising=False)
    monkeypatch.setattr(
        platform.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))],
    )
    requests = []
    real_client = httpx.Client

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        assert kwargs["trust_env"] is False

        def send(request):
            requests.append(request)
            assert request.url.host == "1.1.1.1"
            assert request.headers["host"] == "public.example"
            assert request.extensions["sni_hostname"] == "public.example"
            return httpx.Response(200, json={"ok": True})

        return real_client(transport=httpx.MockTransport(send), **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    block = platform.WebhookBlock({"url": "https://public.example/events", "allow_private": True})
    block.execution_principal = db.principal
    assert block.execute({"data": {"count": 3}}).outputs["status"] == 200
    assert len(requests) == 1


def test_invalid_api_graph_is_rejected_before_side_effects(db, registry):
    client = api_client(db.principal)
    graph = {"nodes": [{"id": "effect", "type": "effect"}, {"id": "bad", "type": "missing"}], "edges": []}
    assert client.post("/api/v1/workflows/run", json={"graph": graph}).status_code == 400
    assert client.post("/api/v1/workflows", json={"name": "Bad", "graph": graph}).status_code == 400
    assert SideEffect.calls == []
    assert db.session.query(SavedWorkflow).count() == 0


def test_dataset_reads_only_its_owned_annotated_frame_and_cleans_temporary_file(db, monkeypatch):
    import cv2

    from lib import db as db_module
    from lib import storage
    from lib.db import Annotation, Frame

    monkeypatch.setattr(db_module, "SessionLocal", db.factory)
    frame = Frame(video_id=db.videos[0].id, frame_number=0, timestamp_s=0, minio_key="own/frame.jpg")
    db.session.add(frame)
    db.session.flush()
    db.session.add(
        Annotation(frame_id=frame.id, job_id=db.jobs[0].id, class_name="car", class_index=0, polygon=[0, 0, 1, 0, 1, 1])
    )
    db.session.commit()
    downloads = []

    def download(key, path):
        downloads.append((key, path))
        assert cv2.imwrite(str(path), np.zeros((3, 4, 3), dtype=np.uint8))

    monkeypatch.setattr(storage, "download_file", download)
    block = platform.DatasetInputBlock({"dataset_id": str(db.jobs[0].id)})
    block.execution_principal = db.principal
    result = block.execute({})
    assert result.outputs["image"].shape == (3, 4, 3)
    assert result.outputs["count"] == 1
    assert downloads[0][0] == "own/frame.jpg"
    assert not downloads[0][1].exists()


def test_detection_and_plate_blocks_use_workspace_pool_selection(db, monkeypatch):
    from lib import db as db_module
    from lib import inference_engine
    from lib.workflow_blocks.detection import DetectionBlock
    from lib.workflow_blocks.specialized import LicensePlateBlock

    monkeypatch.setattr(db_module, "SessionLocal", db.factory)
    monkeypatch.setattr(inference_engine, "get_engine", lambda: pytest.fail("global active engine used"))
    engine = SimpleNamespace(model_name="Owned", predict_image=lambda image, **kwargs: [])
    selected = []
    monkeypatch.setattr(
        inference_engine,
        "get_pool",
        lambda: SimpleNamespace(get_model=lambda model_id: selected.append(model_id) or engine),
    )
    for block in [DetectionBlock(), LicensePlateBlock()]:
        block.execution_principal = db.principal
        block.execute({"image": np.zeros((2, 2, 3), dtype=np.uint8)})
    assert selected == [str(db.models[0].id)] * 2


def test_engine_passes_principal_outside_graph_configuration(db, monkeypatch):
    class Identity(BlockBase):
        name = "identity"

        def execute(self, inputs):
            assert self.execution_principal is db.principal
            return BlockResult(outputs={"__result__": str(self.execution_principal.workspace_id)})

    monkeypatch.setattr(workflow_engine, "BLOCK_REGISTRY", {"identity": Identity})
    graph = {
        "nodes": [
            {
                "id": "identity",
                "type": "identity",
                "config": {
                    "workspace_id": str(db.foreign.id),
                    "role": "admin",
                    "execution_principal": {"workspace_id": str(db.foreign.id)},
                },
            }
        ],
        "edges": [],
    }
    result = workflow_engine.execute_workflow(graph, principal=db.principal)
    assert result["errors"] == []
    assert result["result"] == str(db.own.id)


def test_saved_workflow_update_keeps_identity_and_reopens_by_slug_or_id(db, registry):
    client = api_client(db.principal)
    graph = {"nodes": [{"id": "source", "type": "source", "config": {"value": 1}}], "edges": []}
    created = client.post(
        "/api/v1/workflows", json={"name": "Editable", "description": "Keep this", "graph": graph}
    ).json()
    edited = {
        "nodes": [{"id": "source", "type": "source", "config": {"value": 2}, "position": {"x": 20, "y": 40}}],
        "edges": [],
    }
    response = client.put(
        f"/api/v1/workflows/saved/{created['id']}",
        json={"name": "Renamed", "description": "Keep this", "graph": edited},
    )
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]
    assert response.json()["slug"] == created["slug"]
    for identifier in [created["slug"], created["id"]]:
        loaded = client.get(f"/api/v1/workflows/saved/{identifier}").json()
        assert loaded["graph"] == edited
        assert loaded["name"] == "Renamed"
        assert loaded["description"] == "Keep this"
    assert db.session.query(SavedWorkflow).count() == 1


def test_saved_workflow_updates_reject_missing_foreign_legacy_and_read_only_actors(db, registry):
    graph = {"nodes": [{"id": "source", "type": "source"}], "edges": []}
    records = []
    for slug, workspace_id in [("own-edit", db.own.id), ("foreign-edit", db.foreign.id), ("legacy-edit", None)]:
        record = SavedWorkflow(name=slug, slug=slug, workspace_id=workspace_id, graph=graph)
        db.session.add(record)
        records.append(record)
    db.session.commit()
    body = {"name": "Changed", "graph": graph}
    client = api_client(db.principal)
    for identifier in ["missing", records[1].slug, str(records[1].id), records[2].slug]:
        assert client.put(f"/api/v1/workflows/saved/{identifier}", json=body).status_code == 404
    for identity, role in [
        (db.principal.identity, "viewer"),
        (Principal(db.user.id, auth_kind="api_key", workspace_id=db.own.id, scopes=frozenset({"read"})), "admin"),
    ]:
        actor = WorkspacePrincipal(identity, db.own.id, role)
        assert api_client(actor).put(f"/api/v1/workflows/saved/{records[0].slug}", json=body).status_code == 403
    invalid = {"name": "Changed", "graph": {"nodes": [{"id": "bad", "type": "missing"}], "edges": []}}
    assert client.put(f"/api/v1/workflows/saved/{records[0].slug}", json=invalid).status_code == 400
    db.session.expire_all()
    assert db.session.get(SavedWorkflow, records[0].id).graph == graph
    assert all(record.name == record.slug for record in records)
