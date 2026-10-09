"""Resource API ownership and mutation policy with isolated SQLite rows."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import feedback, frames, label, review, serve, status, train, upload
from lib import auth, authorization
from lib.db import (
    Annotation,
    Base,
    ComparisonRun,
    DemoFeedback,
    DeploymentTarget,
    EdgeDevice,
    Frame,
    LabelingJob,
    ModelRegistry,
    Project,
    TrainingRun,
    User,
    Video,
    Workspace,
    WorkspaceMember,
)


@pytest.fixture
def resources(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(auth, "SessionLocal", factory)
    monkeypatch.setattr(authorization, "SessionLocal", factory)
    for module in (feedback, frames, label, review, serve, status, train, upload):
        monkeypatch.setattr(module, "SessionLocal", factory)
        if hasattr(module, "get_download_url"):
            monkeypatch.setattr(module, "get_download_url", lambda key: f"/media/{key}")
    from lib import redis_client

    monkeypatch.setattr(
        redis_client, "get_redis", lambda: SimpleNamespace(get=lambda _: None, setex=lambda *args: True)
    )
    monkeypatch.setattr(label.label_video, "delay", lambda *args, **kwargs: SimpleNamespace(id="test-task"))
    with factory() as session:
        user = User(email="resource@example.com", password_hash="x", display_name="Resource")
        own = Workspace(name="Own", slug="own")
        foreign = Workspace(name="Foreign", slug="foreign")
        session.add_all([user, own, foreign])
        session.flush()
        member = WorkspaceMember(user_id=user.id, workspace_id=own.id, role="admin")
        session.add(member)
        rows = {}
        for prefix, workspace in [("own", own), ("foreign", foreign), ("legacy", None)]:
            project = Project(name=prefix, workspace_id=workspace.id if workspace else None)
            session.add(project)
            session.flush()
            video = Video(project_id=project.id, filename=f"{prefix}.mp4", minio_key=f"videos/{prefix}.mp4")
            session.add(video)
            session.flush()
            frame = Frame(video_id=video.id, frame_number=0, timestamp_s=0, minio_key=f"frames/{prefix}.jpg")
            job = LabelingJob(project_id=project.id, video_id=video.id, text_prompt=prefix, status="completed")
            session.add_all([frame, job])
            session.flush()
            annotation = Annotation(frame_id=frame.id, job_id=job.id, class_name=prefix, class_index=0, polygon=[])
            run = TrainingRun(project_id=project.id, job_id=job.id, name=prefix, model_variant="yolo26n")
            session.add_all([annotation, run])
            session.flush()
            model = ModelRegistry(
                project_id=project.id,
                training_run_id=run.id,
                name=prefix,
                task_type="detect",
                model_variant="yolo26n",
                weights_minio_key=f"models/{prefix}.pt",
            )
            session.add(model)
            session.flush()
            target = DeploymentTarget(
                workspace_id=workspace.id if workspace else None, name=prefix, slug=prefix, model_id=model.id
            )
            comparison = ComparisonRun(
                workspace_id=workspace.id if workspace else None,
                name=prefix,
                file_name="compare.jpg",
                model_a_name="SAM",
                model_b_name=prefix,
            )
            feedback_row = DemoFeedback(
                workspace_id=workspace.id if workspace else None,
                model_id=model.id,
                class_name=prefix,
                bbox=[0, 0, 1, 1],
            )
            session.add_all([target, comparison, feedback_row])
            session.flush()
            device = EdgeDevice(
                workspace_id=workspace.id if workspace else None,
                name=prefix,
                device_type="jetson_orin",
                model_id=model.id,
                target_id=target.id,
            )
            session.add(device)
            session.flush()
            rows[prefix] = dict(
                project=project, video=video, frame=frame, job=job, annotation=annotation, run=run, model=model
            )
            rows[prefix].update(target=target, comparison=comparison, feedback=feedback_row, device=device)
        session.commit()
        app = FastAPI()
        for module in (feedback, frames, label, review, serve, status, train, upload):
            app.include_router(module.router, prefix="/api/v1")
        client = TestClient(app)
        client.headers["Authorization"] = f"Bearer {auth.create_access_token(str(user.id))}"
        yield session, client, member, rows
    engine.dispose()


READ_PATHS = [
    ("/projects/{project}/videos", "project"),
    ("/videos/{video}/frames", "video"),
    ("/frames/{frame}", "frame"),
    ("/status/{job}", "job"),
    ("/jobs/{job}/annotations", "job"),
    ("/jobs/{job}/classes", "job"),
    ("/jobs/{job}/overview", "job"),
    ("/jobs/{job}/stats", "job"),
    ("/train/dataset-stats/{job}", "job"),
    ("/train/{run}", "run"),
]


@pytest.mark.parametrize("prefix", ["foreign", "legacy"])
@pytest.mark.parametrize("path, resource", READ_PATHS)
def test_foreign_or_unassigned_resource_reads_return_404(resources, prefix, path, resource):
    _, client, _, rows = resources
    response = client.get("/api/v1" + path.format(**{resource: rows[prefix][resource].id}))
    assert response.status_code == 404


@pytest.mark.parametrize(
    "path, id_field", [("/projects", "id"), ("/status", "job_id"), ("/train", "run_id"), ("/models", "id")]
)
def test_resource_lists_only_include_current_workspace(resources, path, id_field):
    _, client, _, rows = resources
    response = client.get("/api/v1" + path)
    assert response.status_code == 200
    resource = {"/projects": "project", "/status": "job", "/train": "run", "/models": "model"}[path]
    assert [row[id_field] for row in response.json()] == [str(rows["own"][resource].id)]


WRITE_CASES = [
    ("POST", "/label", lambda row: {"video_id": str(row["video"].id), "text_prompt": "car"}),
    ("PATCH", "/jobs/{job}", lambda row: {"name": "changed"}),
    ("DELETE", "/jobs/{job}", lambda row: None),
    ("POST", "/jobs/{job}/duplicate", lambda row: None),
    ("POST", "/jobs/{job}/add-class", lambda row: {"class_name": "truck"}),
    ("PATCH", "/annotations/{annotation}", lambda row: {"status": "accepted"}),
    ("POST", "/train", lambda row: {"job_id": str(row["job"].id), "name": "run", "model_variant": "yolo26n"}),
    ("PATCH", "/train/{run}", lambda row: {"name": "changed"}),
    ("DELETE", "/train/{run}", lambda row: None),
    ("POST", "/train/{run}/stop", lambda row: None),
    ("POST", "/models/{model}/export", lambda row: {"format": "onnx"}),
    ("POST", "/models/{model}/activate", lambda row: None),
    ("POST", "/models/{model}/promote", lambda row: None),
]


@pytest.mark.parametrize("method, path, body", WRITE_CASES)
def test_foreign_resource_mutation_returns_404(resources, method, path, body):
    _, client, _, rows = resources
    row = rows["foreign"]
    response = client.request(
        method, "/api/v1" + path.format(**{name: value.id for name, value in row.items()}), json=body(row)
    )
    assert response.status_code == 404


@pytest.mark.parametrize("method, path, body", WRITE_CASES)
def test_viewer_cannot_mutate_own_resources(resources, method, path, body):
    session, client, member, rows = resources
    member.role = "viewer"
    session.commit()
    row = rows["own"]
    response = client.request(
        method, "/api/v1" + path.format(**{name: value.id for name, value in row.items()}), json=body(row)
    )
    assert response.status_code == 403


def test_labeling_video_persists_its_owned_project(resources):
    session, client, _, rows = resources
    response = client.post("/api/v1/label", json={"video_id": str(rows["own"]["video"].id), "text_prompt": "car"})
    assert response.status_code == 202
    job = session.get(LabelingJob, uuid.UUID(response.json()["job_id"]))
    assert job.project_id == rows["own"]["project"].id


@pytest.mark.parametrize(
    "path, resource",
    [("/targets", "target"), ("/devices", "device"), ("/comparisons", "comparison"), ("/feedback", "feedback")],
)
def test_deployment_lists_only_include_current_workspace(resources, path, resource):
    _, client, _, rows = resources
    response = client.get("/api/v1" + path)
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == [str(rows["own"][resource].id)]


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("PATCH", "/targets/{target}", {"name": "changed"}),
        ("DELETE", "/targets/{target}", None),
        ("POST", "/devices/{device}/heartbeat", None),
        ("DELETE", "/comparisons/{comparison}", None),
    ],
)
def test_foreign_deployment_mutations_return_404(resources, method, path, body):
    _, client, _, rows = resources
    row = rows["foreign"]
    response = client.request(
        method, "/api/v1" + path.format(**{name: value.id for name, value in row.items()}), json=body
    )
    assert response.status_code == 404


def test_target_create_persists_selected_workspace(resources):
    session, client, _, rows = resources
    response = client.post("/api/v1/targets", json={"name": "Owned target", "model_id": str(rows["own"]["model"].id)})
    assert response.status_code == 200
    target = session.get(DeploymentTarget, uuid.UUID(response.json()["id"]))
    assert target.workspace_id == rows["own"]["project"].workspace_id


def test_target_create_rejects_foreign_model(resources):
    _, client, _, rows = resources
    response = client.post(
        "/api/v1/targets", json={"name": "Invalid target", "model_id": str(rows["foreign"]["model"].id)}
    )
    assert response.status_code == 404


def test_activation_does_not_clear_other_workspace_active_model(resources, monkeypatch):
    session, client, _, rows = resources
    rows["foreign"]["model"].is_active = True
    session.commit()
    monkeypatch.setattr(serve, "get_pool", lambda: SimpleNamespace(reload_model=lambda model_id: None))
    response = client.post(f"/api/v1/models/{rows['own']['model'].id}/activate")
    assert response.status_code == 200
    session.refresh(rows["foreign"]["model"])
    assert rows["foreign"]["model"].is_active is True


@pytest.mark.parametrize("action", ["activate", "promote?alias=champion"])
@pytest.mark.parametrize("initially_active", [False, True])
def test_repeated_model_selection_persists_active_model_in_own_workspace(
    resources, monkeypatch, action, initially_active
):
    session, client, member, rows = resources
    selected = rows["own"]["model"]
    selected.is_active = initially_active
    selected.alias = "staging"
    prior = ModelRegistry(
        project_id=selected.project_id,
        training_run_id=selected.training_run_id,
        name="Prior active model",
        task_type="detect",
        model_variant="yolo26n",
        weights_minio_key="models/prior.pt",
        is_active=True,
        alias="champion",
    )
    session.add(prior)
    for prefix in ("foreign", "legacy"):
        rows[prefix]["model"].is_active = True
        rows[prefix]["model"].alias = "champion"
    session.commit()
    principal = authorization.WorkspacePrincipal(authorization.Principal(member.user_id), member.workspace_id, "admin")
    monkeypatch.setattr(serve, "get_pool", lambda: SimpleNamespace(reload_model=lambda model_id: None))

    for request_number in range(1, 3):
        response = client.post(f"/api/v1/models/{selected.id}/{action}")
        assert response.status_code == 200
        with serve.SessionLocal() as persisted:
            assert persisted.get(ModelRegistry, selected.id).is_active is True, f"{action} request {request_number}"
            assert persisted.get(ModelRegistry, prior.id).is_active is False
            if action == "promote?alias=champion":
                assert persisted.get(ModelRegistry, selected.id).alias == "champion"
                assert persisted.get(ModelRegistry, prior.id).alias is None
            for prefix in ("foreign", "legacy"):
                untouched = persisted.get(ModelRegistry, rows[prefix]["model"].id)
                assert untouched.is_active is True
                assert untouched.alias == "champion"
        assert serve._resolve_model_id(principal) == str(selected.id)


def test_serve_status_does_not_load_another_workspace_active_model(resources, monkeypatch):
    session, client, _, rows = resources
    rows["foreign"]["model"].is_active = True
    session.commit()

    def forbidden_pool():
        pytest.fail("No model is active in the caller workspace; inference must not load a global model")

    monkeypatch.setattr(serve, "get_pool", forbidden_pool)
    response = client.get("/api/v1/serve/status")
    assert response.status_code == 200
    assert response.json()["loaded"] is False


def test_predict_image_rejects_foreign_model_before_loading(resources, monkeypatch):
    from io import BytesIO

    from PIL import Image

    _, client, _, rows = resources
    image = BytesIO()
    Image.new("RGB", (4, 4)).save(image, format="JPEG")

    def forbidden_pool():
        pytest.fail("Foreign model must be denied before inference loading")

    monkeypatch.setattr(serve, "get_pool", forbidden_pool)
    response = client.post(
        f"/api/v1/predict/image?model_id={rows['foreign']['model'].id}",
        files={"file": ("input.jpg", image.getvalue(), "image/jpeg")},
    )
    assert response.status_code == 404


def test_unknown_task_is_denied_before_celery_read(resources, monkeypatch):

    from app.api import job

    _, client, _, _ = resources
    client.app.include_router(job.router, prefix="/api/v1")
    monkeypatch.setattr(job.celery_app, "AsyncResult", lambda _: pytest.fail("foreign task read"))
    response = client.get("/api/v1/job/unknown")
    assert response.status_code == 404


@pytest.mark.parametrize("terminal", ["partial", "failed"])
def test_celery_success_preserves_pipeline_failure_status(resources, monkeypatch, terminal):
    from app.api import job

    _, client, _, rows = resources
    client.app.include_router(job.router, prefix="/api/v1")
    rows["own"]["job"].celery_task_id = "owned-task"
    resources[0].commit()
    monkeypatch.setattr(
        job.celery_app,
        "AsyncResult",
        lambda _: SimpleNamespace(state="SUCCESS", result={"status": terminal, "processing_summary": {"processed": 3}}),
    )
    response = client.get("/api/v1/job/owned-task")
    assert response.status_code == 200
    assert response.json()["status"] == terminal
    assert response.json()["result"]["processing_summary"]["processed"] == 3


def test_foreign_resume_model_denied_before_training_dispatch(resources, monkeypatch):
    _, client, _, rows = resources
    monkeypatch.setattr(train.train_model, "delay", lambda *args: pytest.fail("foreign resume dispatched"))
    response = client.post(
        "/api/v1/train",
        json={
            "job_id": str(rows["own"]["job"].id),
            "hyperparameters": {"resume_from": str(rows["foreign"]["model"].id)},
        },
    )
    assert response.status_code == 404


def test_owned_dataset_delete_and_annotation_provenance(resources):
    session, client, _, rows = resources
    rows["own"]["annotation"].track_id = 17
    session.commit()
    response = client.get(f"/api/v1/jobs/{rows['own']['job'].id}/annotations")
    assert response.status_code == 200
    assert response.json()[0]["track_id"] == 17
    assert response.json()[0]["source_video_id"] == str(rows["own"]["video"].id)
    assert client.delete(f"/api/v1/jobs/{rows['own']['job'].id}").status_code == 200


def test_metrics_all_aggregates_include_typed_workspace_constraint(resources, monkeypatch):
    _, client, member, _ = resources
    seen = []

    def execute(statement, params):
        assert "workspace_id = :workspace_id" in str(statement)
        assert params == {"workspace_id": member.workspace_id}
        seen.append(statement)
        return SimpleNamespace(fetchone=lambda: None, fetchall=lambda: [])

    monkeypatch.setattr(serve, "SessionLocal", lambda: SimpleNamespace(execute=execute, close=lambda: None))
    response = client.get("/api/v1/metrics/summary")
    assert response.status_code == 200
    assert len(seen) == 5


def test_workspace_scope_key_cannot_create_or_list_other_workspaces(resources, monkeypatch):
    from app.api import workspaces
    from lib.db import ApiKey

    session, client, member, rows = resources
    monkeypatch.setattr(workspaces, "SessionLocal", authorization.SessionLocal)
    client.app.include_router(workspaces.router, prefix="/api/v1")
    foreign_workspace = session.query(Project).filter_by(id=rows["foreign"]["project"].id).one().workspace_id
    session.add(WorkspaceMember(user_id=member.user_id, workspace_id=foreign_workspace, role="admin"))
    raw = "wld_workspacebound"
    session.add(
        ApiKey(
            user_id=member.user_id,
            workspace_id=member.workspace_id,
            name="Bound",
            key_prefix=raw[:8],
            key_hash=auth.hash_password(raw),
            scopes=["read", "write"],
        )
    )
    session.commit()
    client.headers["Authorization"] = f"Bearer {raw}"
    assert [row["id"] for row in client.get("/api/v1/workspaces").json()] == [str(member.workspace_id)]
    assert client.post("/api/v1/workspaces", json={"name": "New"}).status_code == 403


@pytest.mark.parametrize("prefix", ["foreign", "legacy"])
def test_training_socket_denies_foreign_before_metrics_read(resources, monkeypatch, prefix):
    from starlette.websockets import WebSocketDisconnect

    from app import ws

    _, client, _, rows = resources
    monkeypatch.setattr(ws, "SessionLocal", authorization.SessionLocal)
    monkeypatch.setattr(ws, "get_latest_metrics", lambda _: pytest.fail("foreign metrics read"))
    client.app.include_router(ws.router)
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect(f"/ws/training/{rows[prefix]['run'].id}"):
            pass
    assert error.value.code == 1008


def test_owned_training_socket_accepts_bearer_subprotocol(resources, monkeypatch):
    from app import ws

    _, client, _, rows = resources
    monkeypatch.setattr(ws, "SessionLocal", authorization.SessionLocal)
    monkeypatch.setattr(ws, "get_latest_metrics", lambda _: {"status": "partial"})

    async def no_stream(*args):
        pass

    monkeypatch.setattr(ws, "_stream_channel", no_stream)
    client.app.include_router(ws.router)
    token = client.headers.pop("Authorization")[7:]
    with client.websocket_connect(
        f"/ws/training/{rows['own']['run'].id}", subprotocols=["waldo", f"bearer.{token}"]
    ) as socket:
        assert socket.accepted_subprotocol == "waldo"
        assert socket.receive_json()["status"] == "partial"


def test_job_stats_count_only_assessed_frames_and_normalized_bbox(resources):
    session, client, _, rows = resources
    row = rows["own"]
    row["job"].total_frames = 2
    row["job"].processing_summary = {"videos": [{"status": "completed", "sampled_frames": 2}]}
    row["annotation"].bbox = [0.5, 0.5, 0.2, 0.3]
    session.add(Frame(video_id=row["video"].id, frame_number=99, timestamp_s=9, minio_key="frames/unassessed.jpg"))
    session.add(Frame(video_id=row["video"].id, frame_number=100, timestamp_s=10, minio_key="frames/other-run.jpg"))
    session.commit()
    for path in (f"/api/v1/jobs/{row['job'].id}/stats", f"/api/v1/train/dataset-stats/{row['job'].id}"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.json()["total_frames"] == 2
        assert response.json()["empty_frames"] == 1
    assert client.get(f"/api/v1/train/dataset-stats/{row['job'].id}").json()["avg_bbox_area"] == pytest.approx(0.06)


def test_training_missing_artifact_denied_before_enqueue(resources, monkeypatch):
    _, client, _, rows = resources
    monkeypatch.setattr(train.train_model, "delay", lambda *args: pytest.fail("missing artifact dispatched"))
    response = client.post("/api/v1/train", json={"job_id": str(rows["own"]["job"].id)})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "job_status", ["pending", "retrying", "extracting", "labeling", "converting", "queued", "unknown", None]
)
def test_duplicate_rejects_nonterminal_source_without_copying_evidence(resources, job_status):
    session, client, _, rows = resources
    original = rows["own"]["job"]
    original.status = job_status
    original.processing_summary = {"videos": [{"status": "retrying", "sampled_frames": 1}]}
    session.commit()
    job_count = session.query(LabelingJob).count()
    annotation_count = session.query(Annotation).count()

    response = client.post(f"/api/v1/jobs/{original.id}/duplicate")

    assert response.status_code == 409, response.text
    session.expire_all()
    assert session.query(LabelingJob).count() == job_count
    assert session.query(Annotation).count() == annotation_count
    assert original.status == job_status
    assert original.processing_summary == {"videos": [{"status": "retrying", "sampled_frames": 1}]}


@pytest.mark.parametrize("job_status", ["completed", "partial", "failed"])
def test_duplicate_preserves_terminal_evidence_and_can_be_deleted(resources, job_status):
    session, client, _, rows = resources
    original = rows["own"]["job"]
    original.status = job_status
    original.processing_summary = {"videos": [{"status": job_status, "sampled_frames": 2, "error": "inference failed"}]}
    original.total_frames = 2
    original.processed_frames = 1
    original.result_minio_key = "results/source.zip"
    rows["own"]["annotation"].track_id = 33
    session.commit()
    response = client.post(f"/api/v1/jobs/{original.id}/duplicate")
    assert response.status_code == 200
    new_id = response.json()["new_id"]
    duplicate = session.get(LabelingJob, uuid.UUID(new_id))
    assert duplicate.status == job_status
    assert duplicate.processing_summary == {
        "videos": [{"status": job_status, "sampled_frames": 2, "error": "inference failed"}]
    }
    assert duplicate.total_frames == 2
    assert duplicate.processed_frames == 1
    assert duplicate.result_minio_key == "results/source.zip"
    assert duplicate.celery_task_id is None
    copied = client.get(f"/api/v1/jobs/{new_id}/annotations").json()
    assert len(copied) == 1
    assert copied[0]["track_id"] == 33
    assert copied[0]["source_video_id"] == str(rows["own"]["video"].id)

    deleted = client.delete(f"/api/v1/jobs/{new_id}")
    assert deleted.status_code == 200, deleted.text
    session.expire_all()
    assert session.query(LabelingJob).filter_by(id=uuid.UUID(new_id)).count() == 0
    assert session.query(Annotation).filter_by(job_id=uuid.UUID(new_id)).count() == 0
    assert session.get(LabelingJob, original.id).status == job_status
    assert session.query(Annotation).filter_by(job_id=original.id).count() == 1


def test_partial_child_is_terminal_and_partial_artifact_is_visible(resources):
    session, client, _, rows = resources
    own = rows["own"]
    own["job"].status = "partial"
    own["job"].result_minio_key = "results/partial.zip"
    own["job"].score_threshold = 0.4
    own["job"].sample_fps = 2
    own["job"].processing_summary = {"coverage": "sampled"}
    child = LabelingJob(
        project_id=own["project"].id,
        video_id=own["video"].id,
        parent_id=own["job"].id,
        status="partial",
        text_prompt="truck",
    )
    session.add(child)
    session.commit()
    response = client.get(f"/api/v1/status/{own['job'].id}")
    assert response.json()["result_url"] == "/media/results/partial.zip"
    assert response.json()["processing_summary"] == {"coverage": "sampled"}
    assert response.json()["sample_fps"] == 2
    assert client.get(f"/api/v1/jobs/{own['job'].id}/overview").json()["labeling_in_progress"] == 0


def test_training_task_must_match_exported_dataset_task(resources, monkeypatch):
    session, client, _, rows = resources
    rows["own"]["job"].task_type = "detect"
    rows["own"]["job"].result_minio_key = "results/detect.zip"
    session.commit()
    monkeypatch.setattr(train.train_model, "delay", lambda *args: pytest.fail("mismatched task dispatched"))
    response = client.post("/api/v1/train", json={"job_id": str(rows["own"]["job"].id), "task_type": "segment"})
    assert response.status_code == 400


def test_review_exports_are_immutable_and_only_matching_format_becomes_training_artifact(resources, monkeypatch):
    session, client, _, rows = resources
    job = rows["own"]["job"]
    job.task_type = "detect"
    rows["own"]["annotation"].bbox = [0.5, 0.5, 0.2, 0.2]
    rows["own"]["annotation"].polygon = [0.4, 0.4, 0.6, 0.4, 0.6, 0.6]
    session.commit()
    uploaded = []
    monkeypatch.setattr(review, "download_file", lambda key, path: path.write_bytes(b"image"))
    monkeypatch.setattr(review, "upload_file", lambda key, path: uploaded.append(key))
    for fmt in ("detect", "segment", "detect"):
        response = client.post(f"/api/v1/jobs/{job.id}/export", json={"format": fmt})
        assert response.status_code == 200
        session.refresh(job)
        assert job.result_minio_key == uploaded[-1 if fmt == "detect" else -2]
    assert len(set(uploaded)) == 3


def test_training_stats_exclude_rejected_geometry_and_class_recommendations(resources):
    session, client, _, rows = resources
    own = rows["own"]
    own["annotation"].status = None  # Legacy unreviewed rows remain exportable.
    own["annotation"].bbox = [0.5, 0.5, 0.2, 0.3]
    own["job"].total_frames = 2
    rejected_frame = Frame(video_id=own["video"].id, frame_number=10, timestamp_s=1, minio_key="frames/rejected.jpg")
    session.add(rejected_frame)
    session.flush()
    session.add(
        Annotation(
            frame_id=rejected_frame.id,
            job_id=own["job"].id,
            class_name="rejected-only",
            class_index=1,
            status="rejected",
            bbox=[0.5, 0.5, 0.001, 0.001],
            polygon=[],
        )
    )
    session.commit()
    response = client.get(f"/api/v1/train/dataset-stats/{own['job'].id}")
    assert response.status_code == 200
    stats = response.json()
    assert stats["total_annotations"] == 1
    assert stats["annotated_frames"] == 1
    assert [entry["name"] for entry in stats["classes"]] == ["own"]
    assert stats["avg_bbox_area"] == pytest.approx(0.06)
    assert stats["small_object_ratio"] == 0
    assert stats["recommended_imgsz"] == 640


@pytest.mark.parametrize(
    "update",
    [
        {"status": "accepted"},
        {"status": "rejected"},
        {"polygon": [0.1, 0.1, 0.9, 0.1, 0.9, 0.9]},
        {"bbox": [0.5, 0.5, 0.1, 0.1]},
        {"class_name": "renamed"},
        {"class_index": 7},
    ],
)
def test_annotation_mutation_invalidates_export_without_changing_existing_run_snapshot(resources, update):
    session, client, _, rows = resources
    own = rows["own"]
    own["job"].result_minio_key = "results/immutable-old.zip"
    own["run"].dataset_minio_key = "results/immutable-old.zip"
    session.commit()
    response = client.patch(f"/api/v1/annotations/{own['annotation'].id}", json=update)
    assert response.status_code == 200
    session.refresh(own["job"])
    session.refresh(own["run"])
    assert own["job"].result_minio_key is None
    assert own["run"].dataset_minio_key == "results/immutable-old.zip"


@pytest.mark.parametrize("action", ["merge", "delete_class", "create", "add_class"])
def test_dataset_annotation_set_changes_invalidate_export(resources, action):
    session, client, _, rows = resources
    own = rows["own"]
    own["job"].result_minio_key = "results/old.zip"
    session.commit()
    if action == "merge":
        response = client.post(
            "/api/v1/annotations/merge-classes",
            json={"job_id": str(own["job"].id), "source_class": "own", "target_class": "renamed"},
        )
    elif action == "delete_class":
        response = client.delete(f"/api/v1/jobs/{own['job'].id}/classes/own")
    elif action == "create":
        response = client.post(
            "/api/v1/annotations",
            json={
                "job_id": str(own["job"].id),
                "frame_id": str(own["frame"].id),
                "class_name": "new",
                "polygon": [0.1, 0.1, 0.9, 0.1, 0.9, 0.9],
            },
        )
    else:
        response = client.post(f"/api/v1/jobs/{own['job'].id}/add-class", json={"class_name": "new"})
    assert response.status_code in (200, 201)
    session.refresh(own["job"])
    assert own["job"].result_minio_key is None


def test_changed_annotation_requires_reexport_and_preserves_previous_training_input(resources, monkeypatch):
    session, client, _, rows = resources
    own = rows["own"]
    monkeypatch.setattr(train.train_model, "delay", lambda *args: pytest.fail("stale export training dispatched"))
    own["job"].task_type = "detect"
    own["job"].result_minio_key = "results/snapshot-before-edit.zip"
    own["run"].dataset_minio_key = own["job"].result_minio_key
    own["annotation"].bbox = [0.5, 0.5, 0.2, 0.2]
    session.commit()
    assert (
        client.patch(f"/api/v1/annotations/{own['annotation'].id}", json={"bbox": [0.5, 0.5, 0.3, 0.3]}).status_code
        == 200
    )
    assert client.post("/api/v1/train", json={"job_id": str(own["job"].id), "task_type": "detect"}).status_code == 400
    monkeypatch.setattr(review, "download_file", lambda key, path: path.write_bytes(b"image"))
    monkeypatch.setattr(review, "upload_file", lambda key, path: None)
    assert client.post(f"/api/v1/jobs/{own['job'].id}/export", json={"format": "detect"}).status_code == 200
    session.refresh(own["job"])
    session.refresh(own["run"])
    assert own["job"].result_minio_key is not None
    assert own["job"].result_minio_key != own["run"].dataset_minio_key
    assert own["run"].dataset_minio_key == "results/snapshot-before-edit.zip"
