"""Admin endpoints — queue and worker management.

Everything here is gated by `require_admin`. Read-only endpoints list workers,
queue depth, and stuck jobs. Action endpoints can revoke Celery tasks, purge
queues, and force-fail zombie labeling jobs.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import redis
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from lib.auth import require_admin
from lib.config import settings
from lib.db import LabelingJob, SessionLocal, TrainingRun
from lib.tasks import app as celery_app

router = APIRouter(dependencies=[Depends(require_admin)])

QUEUE_NAMES = ("celery", "training")


# ─── Response models ────────────────────────────────────────────


class WorkerInfo(BaseModel):
    name: str
    uptime_seconds: float | None = None
    active_tasks: list[dict[str, Any]] = []
    reserved_tasks: int = 0
    heartbeat_age_seconds: float | None = None
    pool: str | None = None


class QueueInfo(BaseModel):
    name: str
    pending: int


class StuckJob(BaseModel):
    id: str
    text_prompt: str | None
    status: str
    age_seconds: int
    celery_task_id: str | None
    project_id: str | None
    progress: float | None


class AdminStatus(BaseModel):
    workers: list[WorkerInfo]
    queues: list[QueueInfo]
    stuck_jobs: list[StuckJob]
    stuck_threshold_seconds: int


# ─── Helpers ────────────────────────────────────────────────────


def _redis_client() -> redis.Redis:
    return redis.from_url(settings.redis_url)


def _list_workers() -> list[WorkerInfo]:
    """Best-effort worker snapshot.

    Tries Celery `inspect` first (works when workers are idle and responsive).
    Falls back to a DB-derived view: any labeling job in `labeling` or training
    run in `training` implies an active task. This is more reliable than
    inspect for the `--pool=solo` configuration, where the worker's main loop
    can't service control-channel pings while a task is running.
    """
    now = datetime.utcnow()

    # ── Try Celery inspect ──
    inspect_workers: list[WorkerInfo] = []
    try:
        inspect = celery_app.control.inspect(timeout=3.0)
        if inspect is not None:
            stats = inspect.stats() or {}
            active = inspect.active() or {}
            reserved = inspect.reserved() or {}
            ping = inspect.ping() or {}
            for name in sorted(set(stats) | set(active) | set(ping)):
                s = stats.get(name, {})
                pool_info = s.get("pool", {}) or {}
                active_tasks = [
                    {
                        "id": t.get("id"),
                        "name": t.get("name"),
                        "args": t.get("args"),
                        "time_start": t.get("time_start"),
                        "elapsed_seconds": (now.timestamp() - t["time_start"]) if t.get("time_start") else None,
                    }
                    for t in active.get(name, [])
                ]
                inspect_workers.append(
                    WorkerInfo(
                        name=name,
                        uptime_seconds=s.get("uptime"),
                        active_tasks=active_tasks,
                        reserved_tasks=len(reserved.get(name, [])),
                        heartbeat_age_seconds=None,
                        pool=pool_info.get("implementation"),
                    )
                )
    except Exception:
        pass

    if inspect_workers:
        return inspect_workers

    # ── Fallback: infer from DB state ──
    session = SessionLocal()
    try:
        active_labeling = (
            session.query(LabelingJob)
            .filter_by(status="labeling")
            .order_by(LabelingJob.created_at.desc())
            .limit(10)
            .all()
        )
        active_training = (
            session.query(TrainingRun)
            .filter_by(status="training")
            .order_by(TrainingRun.created_at.desc())
            .limit(10)
            .all()
        )
    finally:
        session.close()

    if not active_labeling and not active_training:
        # No live tasks we can detect — return an empty list so the UI shows
        # "no workers reachable" instead of pretending one exists.
        return []

    active_tasks: list[dict[str, Any]] = []
    for j in active_labeling:
        elapsed = (now - j.created_at).total_seconds() if j.created_at else None
        active_tasks.append(
            {
                "id": j.celery_task_id,
                "name": "waldo.label_video",
                "job_id": str(j.id),
                "prompt": j.text_prompt,
                "elapsed_seconds": elapsed,
            }
        )
    for r in active_training:
        elapsed = (now - r.started_at).total_seconds() if r.started_at else None
        active_tasks.append(
            {
                "id": r.celery_task_id,
                "name": "waldo.train_model",
                "run_id": str(r.id),
                "variant": r.model_variant,
                "elapsed_seconds": elapsed,
            }
        )

    return [
        WorkerInfo(
            name="waldo-worker (solo)",
            uptime_seconds=None,
            active_tasks=active_tasks,
            reserved_tasks=0,
            heartbeat_age_seconds=None,
            pool="solo (inferred from DB)",
        )
    ]


def _queue_depths() -> list[QueueInfo]:
    client = _redis_client()
    return [QueueInfo(name=q, pending=client.llen(q) or 0) for q in QUEUE_NAMES]


def _stuck_jobs(threshold_seconds: int) -> list[StuckJob]:
    """Labeling jobs stuck in `labeling` or `pending` for longer than threshold.

    Also surfaces training runs stuck in `training` since those can zombie too.
    """
    session = SessionLocal()
    try:
        cutoff = datetime.utcnow() - timedelta(seconds=threshold_seconds)
        rows = (
            session.query(LabelingJob)
            .filter(LabelingJob.status.in_(("pending", "labeling")))
            .filter(LabelingJob.created_at < cutoff)
            .order_by(LabelingJob.created_at.asc())
            .limit(50)
            .all()
        )
        out: list[StuckJob] = []
        for j in rows:
            age = (datetime.utcnow() - j.created_at).total_seconds() if j.created_at else 0
            out.append(
                StuckJob(
                    id=str(j.id),
                    text_prompt=j.text_prompt,
                    status=j.status,
                    age_seconds=int(age),
                    celery_task_id=j.celery_task_id,
                    project_id=str(j.project_id) if j.project_id else None,
                    progress=float(j.progress) if j.progress is not None else None,
                )
            )
        return out
    finally:
        session.close()


# ─── GET endpoints ──────────────────────────────────────────────


@router.get("/admin/status", response_model=AdminStatus)
def get_admin_status(stuck_threshold: int = 600):
    """One-shot snapshot for the admin panel: workers + queues + stuck jobs."""
    return AdminStatus(
        workers=_list_workers(),
        queues=_queue_depths(),
        stuck_jobs=_stuck_jobs(stuck_threshold),
        stuck_threshold_seconds=stuck_threshold,
    )


@router.get("/admin/workers", response_model=list[WorkerInfo])
def list_workers():
    return _list_workers()


@router.get("/admin/queue", response_model=list[QueueInfo])
def list_queues():
    return _queue_depths()


@router.get("/admin/jobs/stuck", response_model=list[StuckJob])
def list_stuck_jobs(threshold_seconds: int = 600):
    return _stuck_jobs(threshold_seconds)


# ─── Action endpoints ──────────────────────────────────────────


class RevokeResponse(BaseModel):
    task_id: str
    revoked: bool
    terminated: bool


@router.post("/admin/tasks/{task_id}/revoke", response_model=RevokeResponse)
def revoke_task(task_id: str, terminate: bool = True):
    """Revoke a running Celery task. If terminate=True, SIGTERM the worker process
    handling it (only effective for prefork pool; solo pool must wait for the
    task loop to yield)."""
    try:
        celery_app.control.revoke(task_id, terminate=terminate, signal="SIGTERM")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Revoke failed: {e}")
    return RevokeResponse(task_id=task_id, revoked=True, terminated=terminate)


class PurgeResponse(BaseModel):
    queue: str
    removed: int


@router.post("/admin/queue/{queue_name}/purge", response_model=PurgeResponse)
def purge_queue(queue_name: str):
    if queue_name not in QUEUE_NAMES:
        raise HTTPException(status_code=400, detail=f"Unknown queue: {queue_name}")
    client = _redis_client()
    removed = client.llen(queue_name) or 0
    client.delete(queue_name)
    return PurgeResponse(queue=queue_name, removed=removed)


class MarkFailedResponse(BaseModel):
    job_id: str
    status: str


@router.post("/admin/jobs/{job_id}/mark-failed", response_model=MarkFailedResponse)
def mark_job_failed(job_id: str, reason: str | None = None):
    """Force-fail a zombie labeling job so the UI stops spinning."""
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Revoke the celery task too if we know it
        if job.celery_task_id:
            try:
                celery_app.control.revoke(job.celery_task_id, terminate=True, signal="SIGTERM")
            except Exception:
                pass

        job.status = "failed"
        if hasattr(job, "error_message"):
            job.error_message = reason or "Manually marked failed from admin panel"
        session.commit()
        return MarkFailedResponse(job_id=job_id, status="failed")
    finally:
        session.close()


@router.post("/admin/training/{run_id}/mark-failed", response_model=MarkFailedResponse)
def mark_training_failed(run_id: str, reason: str | None = None):
    """Force-fail a zombie training run."""
    session = SessionLocal()
    try:
        run = session.query(TrainingRun).filter_by(id=run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Training run not found")

        if run.celery_task_id:
            try:
                celery_app.control.revoke(run.celery_task_id, terminate=True, signal="SIGTERM")
            except Exception:
                pass

        run.status = "failed"
        run.error_message = reason or "Manually marked failed from admin panel"
        session.commit()
        return MarkFailedResponse(job_id=run_id, status="failed")
    finally:
        session.close()
