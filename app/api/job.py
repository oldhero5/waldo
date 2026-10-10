"""Generic Celery job polling endpoint.

This is the polling backend for endpoints that returned a `job_id` instead of a
synchronous result body. Clients hit `GET /api/v1/job/{job_id}` until the
status becomes `completed` (or `failed`) and then read the `result` field.

This sits alongside (not on top of) `/api/v1/status/{job_id}`, which is
specific to `LabelingJob` rows and reports per-frame progress. `/job/{job_id}`
is the generic Celery `AsyncResult.id` polling surface — anything that
dispatched a Celery task and stashed the task id can be polled here.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from lib import authorization
from lib.auth import get_current_user
from lib.authorization import WorkspacePrincipal, get_workspace_principal, require_task_owner
from lib.db import LabelingJob
from lib.tasks import app as celery_app

router = APIRouter(dependencies=[Depends(get_current_user)])


class JobResultResponse(BaseModel):
    job_id: str
    # Lower-cased, harness-agnostic status. We map Celery's PENDING/STARTED/
    # SUCCESS/FAILURE/RETRY/REVOKED into the four states UIs actually care about.
    status: str  # one of: queued, running, completed, failed
    # Present only when status == "completed". Shape is task-defined.
    result: dict | list | None = None
    # Present only when status == "failed".
    error: str | None = None
    processing_summary: dict | None = None
    score_threshold: float | None = None
    sample_fps: float | None = None


def _celery_state_to_status(state: str) -> str:
    if state in ("PENDING", "RECEIVED", "RETRY"):
        return "queued"
    if state == "STARTED":
        return "running"
    if state == "SUCCESS":
        return "completed"
    if state in ("FAILURE", "REVOKED"):
        return "failed"
    # Custom task-set states (e.g. "PROGRESS") map to running.
    return "running"


@router.get("/job/{job_id}", response_model=JobResultResponse)
def get_job_result(
    job_id: str,
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Poll a Celery task by id. Returns the task result when SUCCESS, error
    info on FAILURE, otherwise the current state.

    `job_id` is the Celery task id returned by endpoints that dispatch async
    work (e.g. `POST /label/preview` without `?wait=true`).
    """
    require_task_owner(job_id, principal)
    session = authorization.SessionLocal()
    try:
        job = (
            authorization.scope_resources(session.query(LabelingJob), LabelingJob, principal)
            .filter_by(celery_task_id=job_id)
            .first()
        )
        metadata = {
            name: getattr(job, name) if job else None
            for name in ("processing_summary", "score_threshold", "sample_fps")
        }
    finally:
        session.close()
    async_result = celery_app.AsyncResult(job_id)
    state = async_result.state
    status = _celery_state_to_status(state)

    if status == "completed":
        # `.result` is the task's return value; safe to read once SUCCESS.
        result = async_result.result
        # Tasks that return non-dict/list shapes (rare) get wrapped so the
        # response model stays consistent.
        if not isinstance(result, (dict, list)):
            result = {"value": result}
        terminal = result.get("status") if isinstance(result, dict) else None
        status = terminal if terminal in ("completed", "partial", "failed") else "completed"
        return JobResultResponse(job_id=job_id, status=status, result=result, **metadata)

    if status == "failed":
        # Celery stores the exception object in `.result` when state == FAILURE.
        err = async_result.result
        return JobResultResponse(job_id=job_id, status="failed", error=str(err) if err else "task failed", **metadata)

    # queued or running: no result yet. Don't 404 — PENDING is also returned
    # for unknown task ids (Celery doesn't track id existence in the broker),
    # so we report "queued" and let the caller keep polling.
    return JobResultResponse(job_id=job_id, status=status, result=None, **metadata)


# Re-exported so callers building 202 responses can reference a single canonical
# URL builder rather than hardcoding the prefix in multiple routers.
def result_url_for(task_id: str) -> str:
    return f"/api/v1/job/{task_id}"


# Legacy path: some older UIs still hit /jobs/{job_id} (plural) — accept both.
# The plural form is *not* the same as `/jobs/{job_id}/...` review endpoints,
# which are scoped to LabelingJob rows. We deliberately use the singular form
# `/job` for the Celery polling surface to avoid the collision.
__all__ = ["router", "result_url_for"]
