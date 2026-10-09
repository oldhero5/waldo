"""LangChain tools the Waldo agent can call on a user's behalf.

Every tool runs inside an `AgentContext` that pins the call to the signed-in
user's workspace, so the agent can never see or mutate another tenant's data.
The graph (lib.agent.graph) injects the context with `tools_with_context()`.

Tools are split into two layers:

  * Read tools — list / inspect projects, datasets, models, training runs,
    plus a `get_system_info` that scopes DEVICE/DTYPE to the API process;
    workers and text providers may run on other hosts.
  * Action tools — kick off labeling jobs, start training, activate a model.
    These produce side effects, so we keep their argument surface narrow and
    we always return both a human string AND a structured JSON payload the
    UI can render as a "the agent did X" pill.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException
from langchain_core.tools import tool

from lib.authorization import Principal, require_scope, require_workspace_role, resolve_workspace, scope_resources
from lib.config import settings
from lib.db import (
    Annotation,
    LabelingJob,
    ModelRegistry,
    Project,
    SessionLocal,
    TrainingRun,
    Video,
)

logger = logging.getLogger(__name__)

# Allow tests to short-circuit Celery dispatch without a running broker.
# Action tools will still write rows to the DB so we can assert against them.
SKIP_DISPATCH = os.environ.get("WALDO_AGENT_SKIP_DISPATCH") == "1"


# ── Per-call context ───────────────────────────────────────────────
@dataclass(frozen=True)
class AgentContext:
    """Auth-scoped context bound to one /agent/chat invocation.

    All tools read this — never bypass it. ``user_id`` and ``workspace_id``
    must be set; the server-resolved ``workspace_role`` grants actions, while
    ``allow_actions`` lets the caller narrow that grant to read-only.
    """

    user_id: str
    workspace_id: str | None = None
    allow_actions: bool = True
    workspace_role: str | None = None
    identity: Principal | None = None

    @property
    def actions_allowed(self) -> bool:
        return (
            self.allow_actions
            and self.workspace_role in {"admin", "editor"}
            and (self.identity is None or self.identity.auth_kind != "api_key" or "write" in self.identity.scopes)
        )


_ctx: ContextVar[AgentContext | None] = ContextVar("waldo_agent_ctx", default=None)


def set_context(ctx: AgentContext) -> None:
    _ctx.set(ctx)


def _ctx_or_raise() -> AgentContext:
    c = _ctx.get()
    if c is None:
        raise RuntimeError("Agent tool called outside of an AgentContext — refusing for safety.")
    if not c.user_id or not c.workspace_id:
        raise RuntimeError("Agent requires an authenticated user and workspace — refusing for safety.")
    return c


def _projects_query(session, ctx: AgentContext):
    """Filter by exact workspace ownership; unassigned legacy rows are excluded."""
    if not ctx.workspace_id:
        raise RuntimeError("Agent requires a workspace — refusing for safety.")
    return scope_resources(session.query(Project), Project, _live_principal(session, ctx))


def _live_principal(session, ctx: AgentContext):
    """Membership and key grants can change while the model is thinking."""
    try:
        identity = ctx.identity or Principal(user_id=_safe_uuid(ctx.user_id, "user_id"))
        if str(identity.user_id) != ctx.user_id:
            raise RuntimeError("Agent identity does not match authenticated user")
        principal = resolve_workspace(session, identity, ctx.workspace_id)
        require_scope(principal.identity, "read")
        return principal
    except HTTPException as error:
        raise RuntimeError(str(error.detail)) from error


def _resource_query(session, model, ctx: AgentContext):
    return scope_resources(session.query(model), model, _live_principal(session, ctx))


# ── Read tools ─────────────────────────────────────────────────────
@tool
def list_projects() -> str:
    """List projects in the current workspace with video counts."""
    ctx = _ctx_or_raise()
    session = SessionLocal()
    try:
        projects = _projects_query(session, ctx).order_by(Project.created_at.desc()).limit(25).all()
        if not projects:
            return "No projects found. Upload a video at /upload to create one."
        lines = []
        for p in projects:
            vid_count = session.query(Video).filter_by(project_id=p.id).count()
            lines.append(f"- {p.name} (id={p.id}): {vid_count} video(s)")
        return f"Projects ({len(projects)}):\n" + "\n".join(lines)
    finally:
        session.close()


@tool
def list_videos(project_id: str | None = None, limit: int = 20) -> str:
    """List uploaded videos. If ``project_id`` is omitted, returns the most
    recent videos across the workspace.
    """
    ctx = _ctx_or_raise()
    session = SessionLocal()
    try:
        # Restrict the candidate project set to the workspace, then filter videos.
        proj_ids = [p.id for p in _projects_query(session, ctx).all()]
        q = session.query(Video).filter(Video.project_id.in_(proj_ids))
        if project_id:
            q = q.filter(Video.project_id == _safe_uuid(project_id, "project_id"))
        videos = q.order_by(Video.created_at.desc()).limit(max(1, min(limit, 100))).all()
        if not videos:
            return "No videos in this workspace yet."
        lines = []
        for v in videos:
            dur = f"{v.duration_s:.1f}s" if v.duration_s else "?"
            res = f"{v.width}x{v.height}" if v.width else "?"
            lines.append(f"- {v.filename} (id={v.id}): {res}, {dur}")
        return f"Videos ({len(videos)}):\n" + "\n".join(lines)
    finally:
        session.close()


@tool
def list_datasets(limit: int = 10) -> str:
    """List completed labeling jobs (ready-to-train datasets) with annotation counts."""
    ctx = _ctx_or_raise()
    session = SessionLocal()
    try:
        proj_ids = [p.id for p in _projects_query(session, ctx).all()]
        jobs = (
            _resource_query(session, LabelingJob, ctx)
            .filter(LabelingJob.project_id.in_(proj_ids))
            .filter(LabelingJob.status == "completed")
            .order_by(LabelingJob.created_at.desc())
            .limit(max(1, min(limit, 50)))
            .all()
        )
        if not jobs:
            return "No completed labeling jobs yet — start one with start_labeling_job."
        lines = []
        for j in jobs:
            ann_count = session.query(Annotation).filter_by(job_id=j.id).count()
            label = j.text_prompt or j.name or "unnamed"
            lines.append(f'- "{label}" (id={j.id}): {ann_count} annotations across {j.total_frames} frames')
        return f"Datasets ({len(jobs)}):\n" + "\n".join(lines)
    finally:
        session.close()


@tool
def list_models(limit: int = 10) -> str:
    """List trained models in this workspace with mAP and active status."""
    ctx = _ctx_or_raise()
    session = SessionLocal()
    try:
        proj_ids = [p.id for p in _projects_query(session, ctx).all()]
        models = (
            _resource_query(session, ModelRegistry, ctx)
            .filter(ModelRegistry.project_id.in_(proj_ids))
            .order_by(ModelRegistry.created_at.desc())
            .limit(max(1, min(limit, 50)))
            .all()
        )
        if not models:
            return "No trained models yet. Run start_training to produce one."
        lines = []
        for m in models:
            metrics = m.metrics or {}
            mAP = metrics.get("metrics/mAP50(B)") or metrics.get("metrics/mAP50(M)") or "?"
            active = " [ACTIVE]" if m.is_active else ""
            lines.append(f"- {m.name} (id={m.id}, {m.model_variant}): mAP50={mAP}{active}")
        return f"Models ({len(models)}):\n" + "\n".join(lines)
    finally:
        session.close()


@tool
def list_training_runs(limit: int = 5) -> str:
    """List the most recent training runs in this workspace with progress."""
    ctx = _ctx_or_raise()
    session = SessionLocal()
    try:
        proj_ids = [p.id for p in _projects_query(session, ctx).all()]
        runs = (
            _resource_query(session, TrainingRun, ctx)
            .filter(TrainingRun.project_id.in_(proj_ids))
            .order_by(TrainingRun.created_at.desc())
            .limit(max(1, min(limit, 25)))
            .all()
        )
        if not runs:
            return "No training experiments yet."
        lines = []
        for r in runs:
            best = (r.best_metrics or {}).get("metrics/mAP50(B)", "?")
            lines.append(
                f"- {r.name} (id={r.id}): {r.status}, epoch {r.epoch_current}/{r.total_epochs}, best mAP50={best}"
            )
        return "Recent training runs:\n" + "\n".join(lines)
    finally:
        session.close()


@tool
def get_system_info() -> str:
    """Return API-process hardware and workspace model configuration.

    Worker and text-provider hardware are not inferred from the API device.
    """
    ctx = _ctx_or_raise()
    from lib.agent.providers import get_config

    provider = get_config(ctx.workspace_id)
    info: dict[str, Any] = {
        "hardware_scope": "api_process",
        "worker_hardware": "not_reported",
        "hardware_note": "API device/GPU checks do not describe native or remote workers or text-provider hardware.",
        "device": settings.device,
        "dtype": settings.dtype,
        "agent_model": provider.model,
        "agent_provider": provider.provider,
        "agent_configuration_source": provider.source,
        "agent_model_note": "Configured workspace default; a request may select another allowed model.",
        "sam3_model_id": settings.sam3_model_id,
    }

    # CUDA / MPS detection — best-effort, never raise.
    try:
        import torch  # noqa: PLC0415

        info["torch_version"] = torch.__version__
        info["cuda_available"] = bool(torch.cuda.is_available())
        if torch.cuda.is_available():
            info["cuda_device_name"] = torch.cuda.get_device_name(0)
            info["cuda_total_memory_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 1e9, 1)
        info["mps_available"] = bool(
            getattr(torch, "backends", None)
            and getattr(torch.backends, "mps", None)
            and torch.backends.mps.is_available()
        )
    except Exception as e:  # noqa: BLE001
        info["torch_error"] = str(e)

    # Active served model (the one that /predict/* will use).
    session = SessionLocal()
    try:
        proj_ids = [p.id for p in _projects_query(session, ctx).all()]
        active = (
            _resource_query(session, ModelRegistry, ctx)
            .filter(ModelRegistry.project_id.in_(proj_ids))
            .filter(ModelRegistry.is_active.is_(True))
            .first()
        )
        info["active_model"] = (
            {"id": str(active.id), "name": active.name, "variant": active.model_variant} if active else None
        )
    finally:
        session.close()

    return json.dumps(info, default=str, indent=2)


@tool
def get_training_tips(dataset_size: int, task_type: str = "segment") -> str:
    """Recommend YOLO training hyperparameters for a given dataset size + task.

    Args:
        dataset_size: number of annotated frames available.
        task_type: "segment" | "detect" | "classify".
    """
    tt = (task_type or "segment").lower()
    suffix = {"segment": "-seg", "detect": "", "classify": "-cls"}.get(tt, "-seg")
    if dataset_size < 50:
        return (
            f"Only {dataset_size} frames — training will overfit easily.\n"
            "- Augmentation: Standard\n"
            f"- Model: yolo26n{suffix} (nano)\n"
            "- Epochs: 50–100 (patience=5)\n"
            "- Batch size: 4\n"
            "- Image size: 640\n"
            "Recommend collecting 200+ frames per class before serious training."
        )
    if dataset_size < 500:
        return (
            f"Modest dataset ({dataset_size} frames).\n"
            "- Augmentation: Standard\n"
            f"- Model: yolo26s{suffix} (small)\n"
            "- Epochs: 100 (patience=10)\n"
            "- Batch size: 8\n"
            "- Image size: 640 (or 1280 if objects are tiny)"
        )
    return (
        f"Healthy dataset ({dataset_size} frames).\n"
        "- Augmentation: Aggressive\n"
        f"- Model: yolo26m{suffix} or yolo26l{suffix}\n"
        "- Epochs: 100–300 (patience=20)\n"
        "- Batch size: 8–16\n"
        "- Consider fine-tuning from a previous checkpoint."
    )


# ── Action tools ───────────────────────────────────────────────────
def _require_actions(ctx: AgentContext) -> None:
    if not ctx.actions_allowed:
        raise RuntimeError("This conversation is read-only — action tools are disabled.")
    with SessionLocal() as session:
        principal = _live_principal(session, ctx)
        try:
            require_workspace_role(principal, "admin", "editor")
        except HTTPException as error:
            raise RuntimeError(str(error.detail)) from error


def _safe_uuid(value: str, name: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as e:
        raise ValueError(f"{name} must be a UUID, got {value!r}") from e


@tool
def start_labeling_job(
    video_id: str,
    text_prompt: str,
    task_type: str = "segment",
    threshold: float = 0.35,
) -> str:
    """Queue a SAM-3 auto-labeling job on a video.

    Args:
        video_id: UUID of the uploaded video.
        text_prompt: free-text class prompt, e.g. "person", "delivery truck".
        task_type: "segment" (default), "detect", "classify", "obb", "pose".
        threshold: detection score threshold (0.0–1.0). Lower = more recall, more noise.

    Returns a JSON object with ``job_id`` so the caller can poll status.
    """
    ctx = _ctx_or_raise()
    _require_actions(ctx)
    vid = _safe_uuid(video_id, "video_id")
    if not text_prompt or not text_prompt.strip():
        raise ValueError("text_prompt must be non-empty")
    if not (0.0 <= threshold <= 1.0):
        raise ValueError("threshold must be between 0.0 and 1.0")

    session = SessionLocal()
    try:
        # Confirm the video belongs to the caller's workspace before queuing.
        proj_ids = [p.id for p in _projects_query(session, ctx).all()]
        video = session.query(Video).filter(Video.id == vid, Video.project_id.in_(proj_ids)).first()
        if not video:
            raise ValueError(f"video {video_id} not found in your workspace")

        job = LabelingJob(
            video_id=video.id,
            project_id=video.project_id,
            text_prompt=text_prompt.strip(),
            task_type=task_type,
            prompt_type="text",
            score_threshold=threshold,
            sample_fps=1.0,
            status="pending",
            total_frames=video.frame_count or 0,
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        celery_task_id: str | None = None
        if not SKIP_DISPATCH:
            # Dispatch via Celery — same task name the API uses.
            from lib.tasks import app as celery_app  # noqa: PLC0415

            async_result = celery_app.send_task(
                "waldo.label_video",
                kwargs={"job_id": str(job.id)},
            )
            celery_task_id = async_result.id
            job.celery_task_id = celery_task_id
            session.commit()

        return json.dumps(
            {
                "ok": True,
                "action": "start_labeling_job",
                "job_id": str(job.id),
                "video_id": str(video.id),
                "text_prompt": text_prompt.strip(),
                "status": job.status,
                "celery_task_id": celery_task_id,
                "ui_url": f"/review/{job.id}",
            }
        )
    finally:
        session.close()


@tool
def start_training(
    job_id: str,
    name: str,
    model_variant: str = "yolo26n-seg",
    epochs: int = 50,
    batch_size: int = 8,
    imgsz: int = 640,
) -> str:
    """Queue a YOLO training run on a completed labeling job.

    Args:
        job_id: UUID of a completed LabelingJob.
        name: human-readable run name.
        model_variant: e.g. yolo26n-seg, yolo26s, yolo26m-seg, yolo26l-cls.
        epochs: training epochs (1–1000).
        batch_size: per-step batch size (1–128).
        imgsz: square training image size (320–1920, multiple of 32).

    Returns a JSON object with ``run_id`` so the caller can poll progress.
    """
    ctx = _ctx_or_raise()
    _require_actions(ctx)
    jid = _safe_uuid(job_id, "job_id")
    if not name.strip():
        raise ValueError("name must be non-empty")
    if not (1 <= epochs <= 1000):
        raise ValueError("epochs must be 1–1000")
    if not (1 <= batch_size <= 128):
        raise ValueError("batch_size must be 1–128")
    if not (320 <= imgsz <= 1920) or imgsz % 32:
        raise ValueError("imgsz must be 320–1920 and divisible by 32")

    session = SessionLocal()
    try:
        proj_ids = [p.id for p in _projects_query(session, ctx).all()]
        job = (
            _resource_query(session, LabelingJob, ctx)
            .filter(LabelingJob.id == jid, LabelingJob.project_id.in_(proj_ids))
            .first()
        )
        if not job:
            raise ValueError(f"labeling job {job_id} not found in your workspace")
        if job.status != "completed":
            raise ValueError(f"labeling job {job_id} is {job.status!r} — it must be completed before training")
        if not job.result_minio_key:
            raise ValueError("Export the reviewed dataset in its training task format before starting training")

        # Infer task_type from variant suffix; falls back to job's task_type.
        if model_variant.endswith("-seg"):
            task_type = "segment"
        elif model_variant.endswith("-cls"):
            task_type = "classify"
        elif model_variant.endswith(("-obb", "-pose")):
            task_type = model_variant.rsplit("-", 1)[1]
        else:
            task_type = job.task_type or "detect"

        run = TrainingRun(
            project_id=job.project_id,
            job_id=job.id,
            name=name.strip(),
            task_type=task_type,
            model_variant=model_variant,
            dataset_minio_key=job.result_minio_key,
            hyperparameters={"epochs": epochs, "batch": batch_size, "imgsz": imgsz},
            status="queued",
            total_epochs=epochs,
        )
        session.add(run)
        session.commit()
        session.refresh(run)

        celery_task_id: str | None = None
        if not SKIP_DISPATCH:
            from lib.tasks import app as celery_app  # noqa: PLC0415

            async_result = celery_app.send_task(
                "waldo.train_model",
                kwargs={"run_id": str(run.id)},
            )
            celery_task_id = async_result.id
            run.celery_task_id = celery_task_id
            session.commit()

        return json.dumps(
            {
                "ok": True,
                "action": "start_training",
                "run_id": str(run.id),
                "job_id": str(job.id),
                "name": run.name,
                "model_variant": model_variant,
                "epochs": epochs,
                "celery_task_id": celery_task_id,
                "ui_url": f"/train/{run.id}",
            }
        )
    finally:
        session.close()


@tool
def activate_model(model_id: str) -> str:
    """Mark a trained model as the active one for /predict/* endpoints.

    Args:
        model_id: UUID of an entry in ModelRegistry.
    """
    ctx = _ctx_or_raise()
    _require_actions(ctx)
    mid = _safe_uuid(model_id, "model_id")

    session = SessionLocal()
    try:
        proj_ids = [p.id for p in _projects_query(session, ctx).all()]
        model = (
            _resource_query(session, ModelRegistry, ctx)
            .filter(ModelRegistry.id == mid, ModelRegistry.project_id.in_(proj_ids))
            .first()
        )
        if not model:
            raise ValueError(f"model {model_id} not found in your workspace")

        # Atomic flip — clear all-active in the workspace, set one true.
        _resource_query(session, ModelRegistry, ctx).filter(ModelRegistry.project_id.in_(proj_ids)).update(
            {ModelRegistry.is_active: False}, synchronize_session=False
        )
        model.is_active = True
        session.commit()
        return json.dumps(
            {
                "ok": True,
                "action": "activate_model",
                "model_id": str(model.id),
                "name": model.name,
                "variant": model.model_variant,
            }
        )
    finally:
        session.close()


# ── Tool registry ──────────────────────────────────────────────────
READ_TOOLS = [
    list_projects,
    list_videos,
    list_datasets,
    list_models,
    list_training_runs,
    get_system_info,
    get_training_tips,
]

ACTION_TOOLS = [
    start_labeling_job,
    start_training,
    activate_model,
]

WALDO_TOOLS = READ_TOOLS + ACTION_TOOLS


def get_tools(*, allow_actions: bool = True) -> list:
    """Return the tool set the graph should bind to the LLM."""
    return list(WALDO_TOOLS) if allow_actions else list(READ_TOOLS)


if SKIP_DISPATCH:  # pragma: no cover — opt-in test path
    logger.warning("WALDO_AGENT_SKIP_DISPATCH=1: action tools will record DB rows but not enqueue Celery.")
