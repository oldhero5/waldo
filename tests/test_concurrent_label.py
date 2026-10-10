"""Concurrency regression test for #9.

Before the async-polling rework, `/label/preview` (and any future endpoint
that called `AsyncResult.get(timeout=180)` inside `asyncio.to_thread`) could
fill FastAPI's default thread pool and stall every other request, including
the `/health` liveness probe. This test fires 50 concurrent labeling
requests and asserts that `/health` keeps replying quickly while they're
in flight.

We hit `/api/v1/label` (the canonical labeling endpoint named in the issue)
rather than `/label/preview` so the same harness covers any future regression
where a labeling endpoint accidentally re-introduces a blocking `.get()`.
"""

import asyncio
import time
import uuid
from unittest.mock import patch

import httpx
import pytest


@pytest.fixture
def patched_label(tmp_path, monkeypatch):
    """Exercise real JWT, membership and ownership with an isolated SQLite DB."""
    from types import SimpleNamespace

    from fastapi import FastAPI
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.api import job, label
    from lib import auth, authorization
    from lib.db import Base, LabelingJob, Project, User, Video, Workspace, WorkspaceMember

    engine = create_engine(f"sqlite:///{tmp_path / 'concurrency.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    for module in (auth, authorization, label):
        monkeypatch.setattr(module, "SessionLocal", factory)
    with factory() as session:
        user = User(email="concurrency@example.invalid", password_hash="x", display_name="Concurrency")
        workspace = Workspace(name="Concurrency", slug=uuid.uuid4().hex)
        session.add_all([user, workspace])
        session.flush()
        project = Project(name="Owned", workspace_id=workspace.id)
        session.add_all([project, WorkspaceMember(user_id=user.id, workspace_id=workspace.id, role="admin")])
        session.flush()
        video = Video(project_id=project.id, filename="owned.mp4", minio_key="videos/owned.mp4")
        session.add(video)
        session.flush()
        session.add(LabelingJob(project_id=project.id, video_id=video.id, celery_task_id="abc"))
        session.commit()
        headers = {
            "Authorization": f"Bearer {auth.create_access_token(str(user.id))}",
            "X-Workspace-ID": str(workspace.id),
        }
        video_id = str(video.id)

    def delayed_publish(*args, **kwargs):
        time.sleep(0.04)
        return SimpleNamespace(id=str(uuid.uuid4()))

    monkeypatch.setattr(label.label_video, "delay", delayed_publish)
    app = FastAPI()
    app.include_router(label.router, prefix="/api/v1")
    app.include_router(job.router, prefix="/api/v1")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    try:
        yield app, headers, video_id
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_job_endpoint_maps_celery_states(patched_label):
    """`GET /api/v1/job/{job_id}` translates Celery states into the
    queued/running/completed/failed enum the polling helper expects.
    """
    app, headers, video_id = patched_label
    transport = httpx.ASGITransport(app=app)

    class _AR:
        def __init__(self, state, result=None):
            self.state = state
            self.result = result

    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        # PENDING → queued
        with patch("app.api.job.celery_app.AsyncResult", return_value=_AR("PENDING")):
            r = await client.get("/api/v1/job/abc")
            assert r.status_code == 200
            assert r.json()["status"] == "queued"

        # SUCCESS → completed, with the dict result echoed back
        with patch("app.api.job.celery_app.AsyncResult", return_value=_AR("SUCCESS", {"frames": []})):
            r = await client.get("/api/v1/job/abc")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "completed"
            assert body["result"] == {"frames": []}

        # FAILURE → failed, error stringified
        with patch(
            "app.api.job.celery_app.AsyncResult",
            return_value=_AR("FAILURE", RuntimeError("boom")),
        ):
            r = await client.get("/api/v1/job/abc")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "failed"
            assert "boom" in body["error"]


@pytest.mark.asyncio
async def test_label_storm_does_not_starve_health(patched_label):
    """50 simultaneous /label posts must not block /health beyond 200ms."""
    app, headers, video_id = patched_label
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        # Warm the app once so first-hit JIT/import costs don't pollute timings.
        warm = await client.get("/health")
        assert warm.status_code == 200

        async def one_label() -> int:
            r = await client.post(
                "/api/v1/label",
                json={
                    "video_id": video_id,
                    "text_prompt": "person",
                    "task_type": "segment",
                },
            )
            return r.status_code

        async def health_probes(stop_event: asyncio.Event) -> list[float]:
            timings: list[float] = []
            while not stop_event.is_set():
                t0 = time.perf_counter()
                r = await client.get("/health")
                timings.append((time.perf_counter() - t0) * 1000)
                assert r.status_code == 200
                await asyncio.sleep(0.01)
            return timings

        stop = asyncio.Event()
        health_task = asyncio.create_task(health_probes(stop))

        # Fire the storm.
        results = await asyncio.gather(*[one_label() for _ in range(50)])
        stop.set()
        health_timings = await health_task

        # Every label call must succeed (202).
        assert all(s == 202 for s in results), f"some /label calls failed: {results}"

        # Health must have been probed at least a couple of times during the
        # storm and the worst case must be under the 200ms threshold from #9.
        assert len(health_timings) >= 2, f"health was probed only {len(health_timings)}x — storm too short?"
        worst = max(health_timings)
        assert worst < 200.0, (
            f"/health degraded under /label storm: worst={worst:.1f}ms "
            f"(timings={[round(t, 1) for t in health_timings]})"
        )
