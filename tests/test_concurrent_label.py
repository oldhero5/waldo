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
from typing import Any
from unittest.mock import patch

import httpx
import pytest


def _fake_label_video_delay(*_args: Any, **_kwargs: Any):
    """Stand-in for `lib.tasks.label_video.delay` — returns an object with the
    `.id` attribute the route reads, without actually dispatching to Celery.
    """

    class _FakeAsyncResult:
        id = "00000000-0000-0000-0000-000000000000"

    return _FakeAsyncResult()


@pytest.fixture
def fake_video_id():
    return str(uuid.uuid4())


@pytest.fixture
def patched_label(fake_video_id):
    """Patch the DB lookup, the LabelingJob constructor, and Celery dispatch
    so the route doesn't need real infra. We assign a uuid + default status
    inside `session.add()` to mimic what SQLAlchemy + a real Postgres do for
    inserted rows (defaults + id auto-generation).
    """
    fake_video = type("V", (), {"id": fake_video_id})()

    class _Q:
        def filter_by(self, **_kw):
            return self

        def first(self):
            return fake_video

    class _S:
        def query(self, _model):
            return _Q()

        def add(self, obj):
            # Mimic Postgres-side defaults so the route's `LabelResponse(...)`
            # doesn't blow up on Pydantic validation.
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()
            if getattr(obj, "status", None) is None:
                obj.status = "pending"

        def commit(self):
            pass

        def close(self):
            pass

    with (
        patch("app.api.label.SessionLocal", return_value=_S()),
        patch("app.api.label.label_video.delay", side_effect=_fake_label_video_delay),
    ):
        yield


@pytest.mark.asyncio
async def test_job_endpoint_maps_celery_states():
    """`GET /api/v1/job/{job_id}` translates Celery states into the
    queued/running/completed/failed enum the polling helper expects.
    """
    from app.main import app

    transport = httpx.ASGITransport(app=app)

    class _AR:
        def __init__(self, state, result=None):
            self.state = state
            self.result = result

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
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
async def test_label_storm_does_not_starve_health(patched_label, fake_video_id):
    """50 simultaneous /label posts must not block /health beyond 200ms."""
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Warm the app once so first-hit JIT/import costs don't pollute timings.
        warm = await client.get("/health")
        assert warm.status_code == 200

        async def one_label() -> int:
            r = await client.post(
                "/api/v1/label",
                json={
                    "video_id": fake_video_id,
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
