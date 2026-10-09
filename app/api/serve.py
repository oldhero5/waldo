"""Inference serving API — image prediction, video prediction, model activation, deployment targets."""
# ruff: noqa: S608
# metrics_summary builds SQL with `interval` and `bucket` values from a server-side
# allowlist (window_map) — never user input. S608 is a false positive for this file.

import asyncio
import logging
import random
import shutil
import tempfile
import time
import uuid as _uuid
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from lib.auth import get_current_user
from lib.authorization import (
    WorkspacePrincipal,
    get_workspace_principal,
    register_task_owner,
    require_resource,
    require_task_owner,
    require_workspace_editor,
    scope_resources,
)
from lib.db import (
    ComparisonRun,
    DeploymentExperiment,
    DeploymentTarget,
    EdgeDevice,
    InferenceLog,
    ModelRegistry,
    SessionLocal,
)
from lib.inference_engine import get_pool
from lib.tasks import predict_sam_task, predict_video_task

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(get_current_user)])


# ── Pydantic models ─────────────────────────────────────────────


class DetectionOut(BaseModel):
    class_name: str
    class_index: int
    confidence: float
    bbox: list[float]
    track_id: int | None = None
    mask: list[list[float]] | None = None


class ImagePredictionResponse(BaseModel):
    detections: list[DetectionOut]
    model_id: str | None = None
    count: int


class FrameResultOut(BaseModel):
    frame_index: int
    timestamp_s: float
    detections: list[DetectionOut]
    source_width: int | None = None
    source_height: int | None = None
    frame_duration_s: float | None = None
    timestamp_method: str | None = None


class VideoPredictionResponse(BaseModel):
    frames: list[FrameResultOut]
    total_frames: int
    model_id: str | None = None


class ServeStatus(BaseModel):
    loaded: bool
    model_id: str | None = None
    model_name: str | None = None
    task_type: str | None = None
    model_variant: str | None = None
    device: str
    class_names: list[str] | None = None


class TargetOut(BaseModel):
    id: str
    name: str
    slug: str | None = None
    endpoint_url: str | None = None
    location_label: str | None = None
    target_type: str = "api"
    model_id: str | None = None
    model_name: str | None = None
    config: dict = {}
    is_active: bool = True
    created_at: str = ""


class TargetCreate(BaseModel):
    name: str
    location_label: str | None = None
    target_type: str = "camera"
    model_id: str | None = None
    config: dict = {}


class TargetUpdate(BaseModel):
    name: str | None = None
    location_label: str | None = None
    target_type: str | None = None
    model_id: str | None = None
    config: dict | None = None
    is_active: bool | None = None


class MetricsQuery(BaseModel):
    window: str = "1h"  # 1h, 24h, 7d


# ── Blue-green experiment routing ────────────────────────────────


def _resolve_experiment_model(target_id: str | None, principal: WorkspacePrincipal) -> str | None:
    """If there's a running experiment, probabilistically route to champion or challenger."""
    try:
        session = SessionLocal()
        try:
            query = scope_resources(session.query(DeploymentExperiment), DeploymentExperiment, principal).filter_by(
                status="running"
            )
            if target_id:
                # Match experiment for this specific target, or global experiments (target_id=null)
                from sqlalchemy import or_

                query = query.filter(
                    or_(
                        DeploymentExperiment.target_id == _uuid.UUID(target_id),
                        DeploymentExperiment.target_id.is_(None),
                    )
                )
            else:
                query = query.filter(DeploymentExperiment.target_id.is_(None))

            experiment = query.first()
            if not experiment:
                return None

            # Route: split_pct% goes to challenger, rest to champion
            if random.randint(1, 100) <= experiment.split_pct:
                return str(experiment.challenger_model_id)
            else:
                return str(experiment.champion_model_id)
        finally:
            session.close()
    except Exception:
        return None


def _resolve_model_id(principal: WorkspacePrincipal, model_id: str | None = None, target_id: str | None = None) -> str:
    """Select a persisted model owned by the caller's selected workspace."""
    session = SessionLocal()
    try:
        if target_id:
            target = require_resource(session, principal, DeploymentTarget, target_id)
            if not target.is_active:
                raise HTTPException(status_code=404, detail="Deployment target is inactive")
            model_id = model_id or (str(target.model_id) if target.model_id else None)
        if not model_id:
            model_id = _resolve_experiment_model(target_id, principal)
        if model_id:
            model = require_resource(session, principal, ModelRegistry, model_id)
        else:
            model = (
                scope_resources(session.query(ModelRegistry), ModelRegistry, principal)
                .filter_by(is_active=True)
                .first()
            )
            if model is None:
                raise HTTPException(status_code=404, detail="No active model in this workspace")
        return str(model.id)
    finally:
        session.close()


def _authorize_comparison_models(principal: WorkspacePrincipal, *model_ids: str | None) -> None:
    session = SessionLocal()
    try:
        for model_id in model_ids:
            if model_id is not None and model_id != "sam3.1":
                require_resource(session, principal, ModelRegistry, model_id)
    finally:
        session.close()


# ── Inference logging helper ────────────────────────────────────


def _log_inference(
    principal: WorkspacePrincipal,
    model_id: str | None,
    target_id: str | None,
    request_type: str,
    latency_ms: float,
    detection_count: int,
    avg_confidence: float | None,
    classes_detected: list[str],
    input_resolution: str | None,
    error_code: str | None = None,
):
    """Non-blocking: fire-and-forget insert into inference_logs."""
    try:
        session = SessionLocal()
        try:
            log = InferenceLog(
                workspace_id=principal.workspace_id,
                model_id=_uuid.UUID(model_id) if model_id else None,
                target_id=_uuid.UUID(target_id) if target_id else None,
                request_type=request_type,
                latency_ms=latency_ms,
                detection_count=detection_count,
                avg_confidence=avg_confidence,
                classes_detected=classes_detected,
                input_resolution=input_resolution,
                error_code=error_code,
            )
            session.add(log)
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.debug("Failed to write inference log", exc_info=True)


# ── Prediction endpoints ────────────────────────────────────────


@router.post("/predict/image", response_model=ImagePredictionResponse)
async def predict_image(
    file: UploadFile = File(...),
    conf: float = Query(0.25, ge=0.0, le=1.0),
    classes: str | None = Query(None),
    model_id: str | None = Query(None, description="Specific model ID to use (default: active model)"),
    target_id: str | None = Query(None, description="Deployment target — auto-selects the target's assigned model"),
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Upload an image and get JSON detections back."""
    import cv2
    import numpy as np

    model_id = _resolve_model_id(principal, model_id, target_id)
    t0 = time.perf_counter()

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image file")

    h, w = image.shape[:2]
    resolution = f"{w}x{h}"

    resolved_target_id = target_id
    pool = get_pool()

    # Get the right engine: specific model_id or active model
    try:
        engine = pool.get_model(model_id)
    except Exception as e:
        _log_inference(
            principal,
            model_id,
            resolved_target_id,
            "image",
            (time.perf_counter() - t0) * 1000,
            0,
            None,
            [],
            resolution,
            "model_not_found",
        )
        raise HTTPException(status_code=404, detail=f"Model not found: {e}")

    class_filter = [c.strip() for c in classes.split(",")] if classes else None

    def _run():
        return engine.predict_image(image, conf=conf, class_filter=class_filter)

    try:
        detections = await asyncio.to_thread(_run)
    except RuntimeError as e:
        latency = (time.perf_counter() - t0) * 1000
        _log_inference(
            principal, engine.model_id, resolved_target_id, "image", latency, 0, None, [], resolution, "inference_error"
        )
        logger.exception("Inference error on image prediction")
        raise HTTPException(status_code=503, detail=f"Inference error: {e}")

    dets_out = [DetectionOut(**asdict(d)) for d in detections]

    # Log the inference
    latency = (time.perf_counter() - t0) * 1000
    avg_conf = sum(d.confidence for d in dets_out) / len(dets_out) if dets_out else None
    classes_found = list({d.class_name for d in dets_out})
    _log_inference(
        principal,
        engine.model_id,
        resolved_target_id,
        "image",
        latency,
        len(dets_out),
        avg_conf,
        classes_found,
        resolution,
    )

    return ImagePredictionResponse(
        detections=dets_out,
        model_id=engine.model_id,
        count=len(dets_out),
    )


async def _remove_inference_input(input_key: str) -> None:
    from lib.storage import delete_object

    try:
        await asyncio.to_thread(delete_object, input_key)
    except Exception:
        logger.warning("Failed to remove ephemeral inference input", exc_info=True)


async def _store_inference_input(
    contents: bytes, filename: str | None, principal: WorkspacePrincipal, input_id: str
) -> str:
    from lib.storage import ensure_inference_lifecycle, upload_bytes

    suffix = Path(filename or "input.bin").suffix.lower().lstrip(".")
    if not suffix.isalnum() or len(suffix) > 10:
        suffix = "bin"
    input_key = f"inference/{principal.workspace_id}/{input_id}/input.{suffix}"
    try:
        await asyncio.to_thread(ensure_inference_lifecycle)
        await asyncio.to_thread(upload_bytes, input_key, contents)
    except Exception:
        await _remove_inference_input(input_key)
        raise
    return input_key


async def _dispatch_sam(
    contents: bytes,
    filename: str | None,
    principal: WorkspacePrincipal,
    is_video: bool,
    prompts: list[str],
    conf: float,
) -> dict:
    task_id = str(_uuid.uuid4())
    register_task_owner(task_id, principal)
    input_key = await _store_inference_input(contents, filename, principal, task_id)
    try:
        _prepare_inference_task("sam", task_id, task_id)
        task = predict_sam_task.apply_async(args=[input_key, is_video, prompts, conf], task_id=task_id, expires=86400)
    except Exception:
        await _remove_inference_input(input_key)
        raise
    # Celery's blocking wait belongs in a thread; the native worker owns cleanup.
    return await asyncio.to_thread(task.get, timeout=300)


@router.post("/predict/sam", response_model=ImagePredictionResponse)
async def predict_sam(
    file: UploadFile = File(...),
    prompts: str = Query(..., description="Comma-separated text prompts, e.g. 'person,car'"),
    conf: float = Query(0.15, ge=0.0, le=1.0),
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Run SAM 3.1 on a native worker, preserving the image response contract."""
    import cv2
    import numpy as np

    t0 = time.perf_counter()
    contents = await file.read()
    image = cv2.imdecode(np.frombuffer(contents, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image file")
    h, w = image.shape[:2]
    resolution = f"{w}x{h}"
    prompt_list = [prompt.strip() for prompt in prompts.split(",") if prompt.strip()]
    if not prompt_list:
        raise HTTPException(status_code=400, detail="At least one prompt required")
    try:
        result = await _dispatch_sam(contents, file.filename, principal, False, prompt_list, conf)
        response = ImagePredictionResponse.model_validate(result)
    except Exception as error:
        _log_inference(
            principal, None, None, "image", (time.perf_counter() - t0) * 1000, 0, None, [], resolution, "sam_error"
        )
        logger.exception("SAM 3.1 worker inference error")
        raise HTTPException(status_code=503, detail=f"SAM 3.1 inference error: {error}") from error
    avg_conf = sum(d.confidence for d in response.detections) / response.count if response.count else None
    _log_inference(
        principal,
        None,
        None,
        "image",
        (time.perf_counter() - t0) * 1000,
        response.count,
        avg_conf,
        list({d.class_name for d in response.detections}),
        resolution,
    )
    return response


@router.post("/predict/sam/video")
async def predict_sam_video(
    file: UploadFile = File(...),
    prompts: str = Query(..., description="Comma-separated text prompts"),
    conf: float = Query(0.35, ge=0.0, le=1.0),
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Run SAM 3.1 tracking on a native worker with source timing and geometry."""
    t0 = time.perf_counter()
    prompt_list = [prompt.strip() for prompt in prompts.split(",") if prompt.strip()]
    if not prompt_list:
        raise HTTPException(status_code=400, detail="At least one prompt required")
    try:
        result = await _dispatch_sam(await file.read(), file.filename, principal, True, prompt_list, conf)
        response = VideoPredictionResponse.model_validate(result)
    except Exception as error:
        _log_inference(
            principal, None, None, "video", (time.perf_counter() - t0) * 1000, 0, None, [], None, "sam_video_error"
        )
        logger.exception("SAM 3.1 worker video error")
        raise HTTPException(status_code=503, detail=f"SAM 3.1 video error: {error}") from error
    detections = [d for frame in response.frames for d in frame.detections]
    avg_conf = sum(d.confidence for d in detections) / len(detections) if detections else None
    _log_inference(
        principal,
        None,
        None,
        "video",
        (time.perf_counter() - t0) * 1000,
        len(detections),
        avg_conf,
        list({d.class_name for d in detections}),
        result.get("input_resolution"),
    )
    return JSONResponse(status_code=200, content=response.model_dump())


@router.post("/predict/video")
async def predict_video(
    file: UploadFile = File(...),
    conf: float = Query(0.25, ge=0.0, le=1.0),
    classes: str | None = Query(None),
    target_id: str | None = Query(None),
    model_id: str | None = Query(None, description="Specific model ID to use"),
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Upload a video for tracked prediction.

    Short videos (<=500 frames): returns full results synchronously (200).
    Long videos: dispatches Celery task and returns session_id to poll via WebSocket (202).
    """
    import uuid

    import cv2

    from lib.video_tracker import validate_video

    t0 = time.perf_counter()

    resolved_model_id = _resolve_model_id(principal, model_id, target_id)

    # Save uploaded video to temp location
    session_id = str(uuid.uuid4())
    tmp_dir = Path(tempfile.mkdtemp(prefix="waldo_predict_"))
    safe_name = Path(file.filename).name  # strips directory traversal
    video_path = tmp_dir / safe_name
    contents = await file.read()
    video_path.write_bytes(contents)

    try:
        validate_video(str(video_path))
    except ValueError as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(e))

    cap = cv2.VideoCapture(str(video_path))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    resolution = f"{width}x{height}"

    if frame_count <= 500:
        try:
            from lib.video_tracker import VideoTracker

            pool = get_pool()
            try:
                engine = pool.get_model(resolved_model_id)
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e))

            def _run_tracking():
                tracker = VideoTracker(conf=conf, engine=engine)
                return tracker.track_video(str(video_path))

            try:
                frame_results = await asyncio.to_thread(_run_tracking)
            except Exception as e:
                latency = (time.perf_counter() - t0) * 1000
                _log_inference(
                    principal, engine.model_id, target_id, "video", latency, 0, None, [], resolution, "tracking_error"
                )
                logger.exception("Video tracking failed")
                raise HTTPException(status_code=500, detail=f"Video tracking error: {e}")

            # Apply class filter if specified
            class_filter = [c.strip() for c in classes.split(",")] if classes else None
            if class_filter:
                filter_set = set(class_filter)
                for fr in frame_results:
                    fr.detections = [d for d in fr.detections if d.class_name in filter_set]

            frames_out = []
            total_dets = 0
            total_conf = 0.0
            all_classes: set[str] = set()
            for fr in frame_results:
                fout = FrameResultOut(
                    frame_index=fr.frame_index,
                    timestamp_s=fr.timestamp_s,
                    detections=[DetectionOut(**asdict(d)) for d in fr.detections],
                    source_width=fr.source_width,
                    source_height=fr.source_height,
                    frame_duration_s=fr.frame_duration_s,
                    timestamp_method=fr.timestamp_method,
                )
                frames_out.append(fout)
                for d in fout.detections:
                    total_dets += 1
                    total_conf += d.confidence
                    all_classes.add(d.class_name)

            # Log video inference
            latency = (time.perf_counter() - t0) * 1000
            avg_conf = total_conf / total_dets if total_dets else None
            _log_inference(
                principal,
                engine.model_id,
                target_id,
                "video",
                latency,
                total_dets,
                avg_conf,
                list(all_classes),
                resolution,
            )

            return JSONResponse(
                status_code=200,
                content=VideoPredictionResponse(
                    frames=frames_out,
                    total_frames=len(frames_out),
                    model_id=engine.model_id,
                ).model_dump(),
            )
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    # Native workers cannot access the app container's temporary directory.
    input_key = None
    try:
        task_id = str(_uuid.uuid4())
        register_task_owner(session_id, principal)
        register_task_owner(task_id, principal)
        input_key = await _store_inference_input(contents, file.filename, principal, session_id)
        _prepare_inference_task("predict", session_id, task_id)
        task = predict_video_task.apply_async(
            args=[input_key, conf, session_id], kwargs={"model_id": resolved_model_id}, task_id=task_id, expires=86400
        )
        return JSONResponse(
            status_code=202, content={"session_id": session_id, "celery_task_id": task.id, "frame_count": frame_count}
        )
    except Exception:
        if input_key:
            await _remove_inference_input(input_key)
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ── Model management ────────────────────────────────────────────


def _prepare_inference_task(kind: str, session_id: str, task_id: str) -> None:
    from lib.inference_results import save_inference_result

    save_inference_result(kind, session_id, {"status": "running", "session_id": session_id, "celery_task_id": task_id})


def _owned_inference_result(kind: str, session_id: str, principal: WorkspacePrincipal):
    from lib.inference_results import get_inference_result, save_inference_result

    require_task_owner(session_id, principal)
    try:
        result = get_inference_result(kind, session_id)
        if result and result.get("status") == "running" and result.get("celery_task_id"):
            from lib.tasks import app as celery_app

            task = celery_app.AsyncResult(result["celery_task_id"])
            if task.state in {"FAILURE", "REVOKED"}:
                result = {**result, "status": "failed", "error": str(task.result or "Inference task was revoked")}
                if kind == "compare":
                    result["results"] = {
                        side: {"dets": [], "frames": None, "latency": 0, "error": result["error"]}
                        for side in ("a", "b")
                    }
                save_inference_result(kind, session_id, result)
    except Exception as error:
        raise HTTPException(status_code=503, detail="Inference result store unavailable") from error
    if result is None or result.get("status") == "running":
        return JSONResponse(status_code=202, content=result or {"status": "running", "session_id": session_id})
    return result


@router.get("/predict/video/result/{session_id}")
def get_video_prediction_result(session_id: str, principal: WorkspacePrincipal = Depends(get_workspace_principal)):
    """Recover complete frames or failure after a missed WebSocket notification."""
    return _owned_inference_result("predict", session_id, principal)


@router.post("/models/{model_id}/activate")
def activate_model(
    model_id: str,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Set a model as active and trigger hot-reload in the inference engine."""
    session = SessionLocal()
    try:
        model = require_resource(session, principal, ModelRegistry, model_id)
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")

        # Deactivate all other models
        scope_resources(session.query(ModelRegistry), ModelRegistry, principal).update(
            {"is_active": False}, synchronize_session=False
        )
        model.is_active = True
        session.commit()

        # Hot-reload via pool
        pool = get_pool()
        pool.reload_model(model_id)

        return {"status": "activated", "model_id": model_id, "name": model.name}
    finally:
        session.close()


@router.get("/serve/classes")
def serve_classes(
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Return list of class names from the active model."""
    try:
        model_id = _resolve_model_id(principal)
        engine = get_pool().get_model(model_id)
    except (RuntimeError, HTTPException):
        return {"class_names": []}
    return {"class_names": engine.model_info.get("class_names") or []}


@router.get("/serve/status", response_model=ServeStatus)
def serve_status(
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Return info about the currently loaded model.

    On a fresh install no model is active yet — return loaded=False instead
    of 500ing, so the UI poll on the dashboard doesn't spam errors.
    """
    from lib.config import settings

    try:
        model_id = _resolve_model_id(principal)
        engine = get_pool().get_model(model_id)
    except (RuntimeError, HTTPException):
        return ServeStatus(loaded=False, device=settings.device)
    return ServeStatus(
        loaded=engine.model is not None,
        model_id=engine.model_id,
        model_name=engine.model_info.get("name"),
        task_type=engine.model_info.get("task_type"),
        model_variant=engine.model_info.get("model_variant"),
        device=settings.device,
        class_names=engine.model_info.get("class_names"),
    )


# ── Deployment targets CRUD ─────────────────────────────────────


@router.get("/targets")
def list_targets(
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """List all deployment targets with their assigned model info."""
    session = SessionLocal()
    try:
        targets = (
            scope_resources(session.query(DeploymentTarget), DeploymentTarget, principal)
            .order_by(DeploymentTarget.created_at.desc())
            .all()
        )
        result = []
        for t in targets:
            model_name = None
            if t.model_id:
                model = require_resource(session, principal, ModelRegistry, t.model_id)
                if model:
                    model_name = model.name
            result.append(
                TargetOut(
                    id=str(t.id),
                    name=t.name,
                    slug=t.slug,
                    endpoint_url=f"/api/v1/endpoints/{t.slug}/predict" if t.slug else None,
                    location_label=t.location_label,
                    target_type=t.target_type or "api",
                    model_id=str(t.model_id) if t.model_id else None,
                    model_name=model_name,
                    config=t.config or {},
                    is_active=t.is_active,
                    created_at=t.created_at.isoformat() if t.created_at else "",
                )
            )
        return result
    finally:
        session.close()


@router.post("/targets")
def create_target(
    body: TargetCreate,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Create a new deployment target (camera, zone, or region)."""
    session = SessionLocal()
    try:
        # Validate model_id if provided
        if body.model_id:
            model = require_resource(session, principal, ModelRegistry, body.model_id)
            if not model:
                raise HTTPException(status_code=404, detail="Model not found")

        # Auto-generate slug from name
        import re

        slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:80]
        slug = f"{slug}-{_uuid.uuid4().hex[:12]}"

        target = DeploymentTarget(
            workspace_id=principal.workspace_id,
            name=body.name,
            slug=slug,
            location_label=body.location_label,
            target_type=body.target_type or "api",
            model_id=model.id if body.model_id else None,
            config=body.config,
        )
        session.add(target)
        session.commit()
        session.refresh(target)

        return TargetOut(
            id=str(target.id),
            name=target.name,
            slug=target.slug,
            endpoint_url=f"/api/v1/endpoints/{target.slug}/predict",
            location_label=target.location_label,
            target_type=target.target_type or "api",
            model_id=str(target.model_id) if target.model_id else None,
            model_name=None,
            config=target.config or {},
            is_active=target.is_active,
            created_at=target.created_at.isoformat() if target.created_at else "",
        )
    finally:
        session.close()


@router.patch("/targets/{target_id}")
def update_target(
    target_id: str,
    body: TargetUpdate,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Update a deployment target."""
    session = SessionLocal()
    try:
        target = require_resource(session, principal, DeploymentTarget, target_id)
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")

        if body.name is not None:
            target.name = body.name
        if body.location_label is not None:
            target.location_label = body.location_label
        if body.target_type is not None:
            target.target_type = body.target_type
        if body.model_id is not None:
            if body.model_id:
                model = require_resource(session, principal, ModelRegistry, body.model_id)
                if not model:
                    raise HTTPException(status_code=404, detail="Model not found")
            target.model_id = model.id if body.model_id else None
        if body.config is not None:
            target.config = body.config
        if body.is_active is not None:
            target.is_active = body.is_active

        session.commit()
        return {"status": "updated", "id": target_id}
    finally:
        session.close()


@router.delete("/targets/{target_id}")
def delete_target(
    target_id: str,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Delete a deployment target."""
    session = SessionLocal()
    try:
        target = require_resource(session, principal, DeploymentTarget, target_id)
        if not target:
            raise HTTPException(status_code=404, detail="Target not found")
        session.delete(target)
        session.commit()
        return {"status": "deleted", "id": target_id}
    finally:
        session.close()


# ── Endpoint-based inference ─────────────────────────────────────
# External apps connect to these URLs: POST /v1/endpoints/{slug}/predict


@router.post("/endpoints/{slug}/predict", response_model=ImagePredictionResponse)
async def predict_via_endpoint(
    slug: str,
    file: UploadFile = File(...),
    conf: float | None = Query(None),
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Run inference through a named endpoint. Each endpoint serves a specific model.

    External devices/frontends connect to their assigned endpoint URL.
    Example: curl -X POST http://host/api/v1/endpoints/package-detector/predict -F file=@img.jpg
    """
    import cv2
    import numpy as np

    session = SessionLocal()
    try:
        target = (
            scope_resources(session.query(DeploymentTarget), DeploymentTarget, principal)
            .filter_by(slug=slug, is_active=True)
            .first()
        )
        if not target:
            raise HTTPException(status_code=404, detail=f"Endpoint '{slug}' not found or not active")
        if not target.model_id:
            raise HTTPException(status_code=503, detail=f"Endpoint '{slug}' has no model assigned")

        model_id = str(require_resource(session, principal, ModelRegistry, target.model_id).id)
        config = target.config or {}
        confidence = conf if conf is not None else config.get("confidence", 0.25)
        class_filter = config.get("classes")
    finally:
        session.close()

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image")

    engine = get_pool().get_model(model_id)

    def _run():
        return engine.predict_image(image, conf=confidence, class_filter=class_filter)

    try:
        detections = await asyncio.to_thread(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Log inference
    try:
        log = InferenceLog(
            workspace_id=principal.workspace_id,
            model_id=model_id,
            target_id=target.id if target else None,
            request_type="image",
            latency_ms=0,  # TODO: measure
            detection_count=len(detections),
            avg_confidence=sum(d.confidence for d in detections) / max(1, len(detections)) if detections else None,
            classes_detected=list(set(d.class_name for d in detections)),
            input_resolution=f"{image.shape[1]}x{image.shape[0]}",
        )
        s = SessionLocal()
        s.add(log)
        s.commit()
        s.close()
    except Exception:
        pass

    dets_out = [DetectionOut(**asdict(d)) for d in detections]
    return ImagePredictionResponse(detections=dets_out, model_id=model_id, count=len(dets_out))


@router.get("/endpoints/{slug}/status")
def endpoint_status(
    slug: str,
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Get status of a named endpoint — model info, config, and whether it's loaded."""
    session = SessionLocal()
    try:
        target = (
            scope_resources(session.query(DeploymentTarget), DeploymentTarget, principal).filter_by(slug=slug).first()
        )
        if not target:
            raise HTTPException(status_code=404, detail=f"Endpoint '{slug}' not found")

        model_info = None
        is_loaded = False
        if target.model_id:
            model = require_resource(session, principal, ModelRegistry, target.model_id)
            if model:
                model_info = {
                    "id": str(model.id),
                    "name": model.name,
                    "variant": model.model_variant,
                    "task_type": model.task_type,
                    "class_names": model.class_names,
                }
            pool = get_pool()
            is_loaded = str(target.model_id) in pool.loaded_model_ids()

        return {
            "slug": slug,
            "name": target.name,
            "is_active": target.is_active,
            "is_loaded": is_loaded,
            "model": model_info,
            "config": target.config or {},
            "endpoint_url": f"/api/v1/endpoints/{slug}/predict",
        }
    finally:
        session.close()


# ── Inference metrics API ───────────────────────────────────────


@router.get("/metrics/summary")
def metrics_summary(
    window: str = Query("1h", pattern="^(1h|24h|7d)$"),
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Aggregate inference metrics for the monitoring dashboard."""

    window_map = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}
    interval = window_map[window]

    session = SessionLocal()
    try:
        # The `interval` and `bucket` values below come from server-controlled
        # allowlists (window_map), never from user input — the S608 warnings on
        # the f-strings in this function are silenced via per-file-ignores.
        from sqlalchemy import bindparam, text

        rows = session.execute(
            text(f"""
            SELECT
                count(*) as total_requests,
                coalesce(avg(latency_ms), 0) as avg_latency,
                coalesce(percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms), 0) as p50_latency,
                coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms), 0) as p95_latency,
                coalesce(avg(avg_confidence), 0) as avg_confidence,
                coalesce(avg(detection_count), 0) as avg_detections,
                count(CASE WHEN error_code IS NOT NULL THEN 1 END) as error_count
            FROM inference_logs
            WHERE workspace_id = :workspace_id AND created_at >= now() - interval '{interval}'
        """).bindparams(bindparam("workspace_id", type_=InferenceLog.workspace_id.type)),
            {"workspace_id": principal.workspace_id},
        ).fetchone()

        # Per-model breakdown
        model_rows = session.execute(
            text(f"""
            SELECT
                il.model_id,
                mr.name as model_name,
                count(*) as request_count,
                coalesce(avg(il.latency_ms), 0) as avg_latency,
                coalesce(avg(il.avg_confidence), 0) as avg_confidence
            FROM inference_logs il
            LEFT JOIN model_registry mr ON mr.id = il.model_id
            WHERE il.workspace_id = :workspace_id AND il.created_at >= now() - interval '{interval}'
            GROUP BY il.model_id, mr.name
            ORDER BY request_count DESC
            LIMIT 10
        """).bindparams(bindparam("workspace_id", type_=InferenceLog.workspace_id.type)),
            {"workspace_id": principal.workspace_id},
        ).fetchall()

        # Per-class breakdown
        class_rows = session.execute(
            text(f"""
            SELECT
                cls.value::text as class_name,
                count(*) as detection_count
            FROM inference_logs il,
                 jsonb_array_elements_text(il.classes_detected::jsonb) as cls(value)
            WHERE il.workspace_id = :workspace_id AND il.created_at >= now() - interval '{interval}'
              AND il.classes_detected IS NOT NULL
              AND il.classes_detected::text != '[]'
            GROUP BY cls.value
            ORDER BY detection_count DESC
            LIMIT 20
        """).bindparams(bindparam("workspace_id", type_=InferenceLog.workspace_id.type)),
            {"workspace_id": principal.workspace_id},
        ).fetchall()

        # Per-target breakdown
        target_rows = session.execute(
            text(f"""
            SELECT
                il.target_id,
                dt.name as target_name,
                dt.location_label,
                count(*) as request_count,
                coalesce(avg(il.latency_ms), 0) as avg_latency,
                coalesce(avg(il.avg_confidence), 0) as avg_confidence,
                max(il.created_at) as last_seen
            FROM inference_logs il
            LEFT JOIN deployment_targets dt ON dt.id = il.target_id
            WHERE il.workspace_id = :workspace_id AND il.created_at >= now() - interval '{interval}'
              AND il.target_id IS NOT NULL
            GROUP BY il.target_id, dt.name, dt.location_label
            ORDER BY request_count DESC
        """).bindparams(bindparam("workspace_id", type_=InferenceLog.workspace_id.type)),
            {"workspace_id": principal.workspace_id},
        ).fetchall()

        # Time series (bucket by appropriate interval)
        bucket = "5 minutes" if window == "1h" else "1 hour" if window == "24h" else "6 hours"
        timeseries_rows = session.execute(
            text(f"""
            SELECT
                date_trunc('minute', date_bin(interval '{bucket}', created_at, '2020-01-01')) as bucket,
                count(*) as requests,
                coalesce(avg(latency_ms), 0) as avg_latency,
                coalesce(avg(avg_confidence), 0) as avg_confidence,
                coalesce(avg(detection_count), 0) as avg_detections
            FROM inference_logs
            WHERE workspace_id = :workspace_id AND created_at >= now() - interval '{interval}'
            GROUP BY bucket
            ORDER BY bucket
        """).bindparams(bindparam("workspace_id", type_=InferenceLog.workspace_id.type)),
            {"workspace_id": principal.workspace_id},
        ).fetchall()

        return {
            "window": window,
            "summary": {
                "total_requests": rows[0] if rows else 0,
                "avg_latency_ms": round(rows[1], 1) if rows else 0,
                "p50_latency_ms": round(rows[2], 1) if rows else 0,
                "p95_latency_ms": round(rows[3], 1) if rows else 0,
                "avg_confidence": round(rows[4], 4) if rows else 0,
                "avg_detections": round(rows[5], 1) if rows else 0,
                "error_count": rows[6] if rows else 0,
            },
            "by_model": [
                {
                    "model_id": str(r[0]) if r[0] else None,
                    "model_name": r[1],
                    "request_count": r[2],
                    "avg_latency_ms": round(r[3], 1),
                    "avg_confidence": round(r[4], 4),
                }
                for r in model_rows
            ],
            "by_class": [{"class_name": r[0], "detection_count": r[1]} for r in class_rows],
            "by_target": [
                {
                    "target_id": str(r[0]) if r[0] else None,
                    "target_name": r[1],
                    "location_label": r[2],
                    "request_count": r[3],
                    "avg_latency_ms": round(r[4], 1),
                    "avg_confidence": round(r[5], 4),
                    "last_seen": r[6].isoformat() if r[6] else None,
                }
                for r in target_rows
            ],
            "timeseries": [
                {
                    "timestamp": r[0].isoformat() if r[0] else None,
                    "requests": r[1],
                    "avg_latency_ms": round(r[2], 1),
                    "avg_confidence": round(r[3], 4),
                    "avg_detections": round(r[4], 1),
                }
                for r in timeseries_rows
            ],
        }
    finally:
        session.close()


# ── Model promotion & aliases ───────────────────────────────────


@router.post("/models/{model_id}/promote")
def promote_model(
    model_id: str,
    alias: str = Query("champion"),
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Promote a model to a named alias (champion, challenger, staging).

    Setting alias=champion also sets is_active=True for backward compatibility
    and clears champion from any other model.
    """
    if alias not in ("champion", "challenger", "staging"):
        raise HTTPException(status_code=400, detail="Alias must be champion, challenger, or staging")

    session = SessionLocal()
    try:
        model = require_resource(session, principal, ModelRegistry, model_id)
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")

        # Clear this alias from any other model
        scope_resources(session.query(ModelRegistry), ModelRegistry, principal).filter(
            ModelRegistry.alias == alias,
            ModelRegistry.id != model.id,
        ).update({"alias": None}, synchronize_session=False)

        model.alias = alias

        # Champion = active model (backward compat)
        if alias == "champion":
            scope_resources(session.query(ModelRegistry), ModelRegistry, principal).update(
                {"is_active": False}, synchronize_session=False
            )
            model.is_active = True
            # Hot-reload in pool
            pool = get_pool()
            pool.reload_model(model_id)

        session.commit()
        return {"status": "promoted", "model_id": model_id, "alias": alias, "name": model.name}
    finally:
        session.close()


# ── Deployment experiments (blue-green) ─────────────────────────


class ExperimentCreate(BaseModel):
    name: str
    champion_model_id: str
    challenger_model_id: str
    split_pct: int = 20  # % to challenger
    target_id: str | None = None  # null = global


class ExperimentOut(BaseModel):
    id: str
    name: str
    champion_model_id: str
    champion_name: str | None
    challenger_model_id: str
    challenger_name: str | None
    split_pct: int
    status: str
    target_id: str | None
    started_at: str | None
    completed_at: str | None
    winner: str | None


@router.get("/experiments")
def list_experiments(
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """List all deployment experiments."""
    session = SessionLocal()
    try:
        exps = (
            scope_resources(session.query(DeploymentExperiment), DeploymentExperiment, principal)
            .order_by(DeploymentExperiment.created_at.desc())
            .all()
        )
        result = []
        for e in exps:
            champ = require_resource(session, principal, ModelRegistry, e.champion_model_id)
            chall = require_resource(session, principal, ModelRegistry, e.challenger_model_id)
            result.append(
                ExperimentOut(
                    id=str(e.id),
                    name=e.name,
                    champion_model_id=str(e.champion_model_id),
                    champion_name=champ.name if champ else None,
                    challenger_model_id=str(e.challenger_model_id),
                    challenger_name=chall.name if chall else None,
                    split_pct=e.split_pct,
                    status=e.status,
                    target_id=str(e.target_id) if e.target_id else None,
                    started_at=e.started_at.isoformat() if e.started_at else None,
                    completed_at=e.completed_at.isoformat() if e.completed_at else None,
                    winner=e.winner,
                )
            )
        return result
    finally:
        session.close()


@router.post("/experiments")
def create_experiment(
    body: ExperimentCreate,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Start a blue-green deployment experiment."""
    session = SessionLocal()
    try:
        champ = require_resource(session, principal, ModelRegistry, body.champion_model_id)
        chall = require_resource(session, principal, ModelRegistry, body.challenger_model_id)
        target = require_resource(session, principal, DeploymentTarget, body.target_id) if body.target_id else None

        # Cancel any existing running experiment for the same target
        existing = scope_resources(session.query(DeploymentExperiment), DeploymentExperiment, principal).filter_by(
            status="running"
        )
        if body.target_id:
            from sqlalchemy import or_

            existing = existing.filter(
                or_(
                    DeploymentExperiment.target_id == target.id,
                    DeploymentExperiment.target_id.is_(None),
                )
            )
        else:
            existing = existing.filter(DeploymentExperiment.target_id.is_(None))

        for e in existing.all():
            e.status = "cancelled"

        # Set aliases
        champ = require_resource(session, principal, ModelRegistry, body.champion_model_id)
        chall = require_resource(session, principal, ModelRegistry, body.challenger_model_id)
        if champ:
            champ.alias = "champion"
        if chall:
            chall.alias = "challenger"

        exp = DeploymentExperiment(
            name=body.name,
            champion_model_id=champ.id,
            challenger_model_id=chall.id,
            split_pct=body.split_pct,
            target_id=target.id if target else None,
        )
        session.add(exp)
        session.commit()
        session.refresh(exp)

        # Pre-warm both models in the pool
        pool = get_pool()
        pool.get_model(body.champion_model_id)
        pool.get_model(body.challenger_model_id)

        return {
            "id": str(exp.id),
            "status": "running",
            "champion": champ.name if champ else None,
            "challenger": chall.name if chall else None,
            "split_pct": exp.split_pct,
        }
    finally:
        session.close()


@router.post("/experiments/{experiment_id}/complete")
def complete_experiment(
    experiment_id: str,
    winner: str = Query(..., pattern="^(champion|challenger)$"),
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """End an experiment and optionally promote the winner."""
    from datetime import datetime

    session = SessionLocal()
    try:
        exp = require_resource(session, principal, DeploymentExperiment, experiment_id)
        if not exp:
            raise HTTPException(status_code=404, detail="Experiment not found")
        if exp.status != "running":
            raise HTTPException(status_code=400, detail=f"Experiment is {exp.status}, not running")

        exp.status = "completed"
        exp.completed_at = datetime.utcnow()
        exp.winner = winner

        # If challenger wins, promote it to champion
        if winner == "challenger":
            # Clear old champion alias
            scope_resources(session.query(ModelRegistry), ModelRegistry, principal).filter(
                ModelRegistry.alias == "champion"
            ).update({"alias": None, "is_active": False}, synchronize_session=False)
            chall = require_resource(session, principal, ModelRegistry, exp.challenger_model_id)
            if chall:
                chall.alias = "champion"
                chall.is_active = True
                pool = get_pool()
                pool.reload_model(str(chall.id))

        # Clear challenger alias
        scope_resources(session.query(ModelRegistry), ModelRegistry, principal).filter(
            ModelRegistry.alias == "challenger"
        ).update({"alias": None}, synchronize_session=False)

        session.commit()
        return {"status": "completed", "winner": winner}
    finally:
        session.close()


# ── Edge devices ────────────────────────────────────────────────


class EdgeDeviceCreate(BaseModel):
    name: str
    device_type: str  # jetson_orin, jetson_nano, pi5_tpu
    location_label: str | None = None
    target_id: str | None = None
    model_id: str | None = None
    hardware_info: dict = {}


class EdgeDeviceOut(BaseModel):
    id: str
    name: str
    device_type: str
    location_label: str | None
    target_id: str | None
    model_id: str | None
    model_version: int | None
    hardware_info: dict
    status: str
    last_heartbeat: str | None
    last_sync: str | None
    ip_address: str | None


@router.get("/devices")
def list_devices(
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """List all registered edge devices."""
    session = SessionLocal()
    try:
        devices = (
            scope_resources(session.query(EdgeDevice), EdgeDevice, principal)
            .order_by(EdgeDevice.created_at.desc())
            .all()
        )
        return [
            EdgeDeviceOut(
                id=str(d.id),
                name=d.name,
                device_type=d.device_type,
                location_label=d.location_label,
                target_id=str(d.target_id) if d.target_id else None,
                model_id=str(d.model_id) if d.model_id else None,
                model_version=d.model_version,
                hardware_info=d.hardware_info or {},
                status=d.status,
                last_heartbeat=d.last_heartbeat.isoformat() if d.last_heartbeat else None,
                last_sync=d.last_sync.isoformat() if d.last_sync else None,
                ip_address=d.ip_address,
            )
            for d in devices
        ]
    finally:
        session.close()


@router.post("/devices")
def register_device(
    body: EdgeDeviceCreate,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Register a new edge device."""
    session = SessionLocal()
    try:
        target = require_resource(session, principal, DeploymentTarget, body.target_id) if body.target_id else None
        model = require_resource(session, principal, ModelRegistry, body.model_id) if body.model_id else None
        device = EdgeDevice(
            workspace_id=principal.workspace_id,
            name=body.name,
            device_type=body.device_type,
            location_label=body.location_label,
            target_id=target.id if target else None,
            model_id=model.id if model else None,
            hardware_info=body.hardware_info,
        )
        session.add(device)
        session.commit()
        session.refresh(device)
        return {"id": str(device.id), "status": "registered"}
    finally:
        session.close()


@router.post("/devices/{device_id}/heartbeat")
def device_heartbeat(
    device_id: str,
    ip: str | None = Query(None),
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Edge device phones home — updates status and last_heartbeat."""
    from datetime import datetime

    session = SessionLocal()
    try:
        device = require_resource(session, principal, EdgeDevice, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        device.status = "online"
        device.last_heartbeat = datetime.utcnow()
        if ip:
            device.ip_address = ip
        session.commit()

        # Return the model the device should be running
        assigned_model = None
        if device.model_id:
            model = require_resource(session, principal, ModelRegistry, device.model_id)
            if model:
                assigned_model = {
                    "model_id": str(model.id),
                    "name": model.name,
                    "version": model.version,
                    "weights_key": model.weights_minio_key,
                }

        return {"status": "ok", "assigned_model": assigned_model}
    finally:
        session.close()


@router.post("/devices/{device_id}/sync-logs")
async def sync_device_logs(
    device_id: str,
    file: UploadFile = File(...),
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Upload inference logs from an offline edge device.

    Expects a JSON file with an array of log entries:
    [{"timestamp": "...", "latency_ms": ..., "detection_count": ..., "avg_confidence": ..., "classes_detected": [...], "input_resolution": "...", "error_code": null}, ...]
    """
    import json
    from datetime import datetime

    session = SessionLocal()
    try:
        device = require_resource(session, principal, EdgeDevice, device_id)
        if not device:
            raise HTTPException(status_code=404, detail="Device not found")

        contents = await file.read()
        try:
            entries = json.loads(contents)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Invalid JSON file")

        if not isinstance(entries, list):
            raise HTTPException(status_code=400, detail="Expected a JSON array of log entries")

        count = 0
        for entry in entries:
            log = InferenceLog(
                workspace_id=principal.workspace_id,
                model_id=device.model_id,
                target_id=device.target_id,
                request_type=entry.get("request_type", "image"),
                latency_ms=entry.get("latency_ms", 0),
                detection_count=entry.get("detection_count", 0),
                avg_confidence=entry.get("avg_confidence"),
                classes_detected=entry.get("classes_detected", []),
                input_resolution=entry.get("input_resolution"),
                error_code=entry.get("error_code"),
            )
            # Use the device's timestamp if provided
            if entry.get("timestamp"):
                try:
                    log.created_at = datetime.fromisoformat(entry["timestamp"])
                except (ValueError, TypeError):
                    pass
            session.add(log)
            count += 1

        device.last_sync = datetime.utcnow()
        device.status = "online"
        session.commit()

        return {"status": "synced", "entries_imported": count, "device_id": device_id}
    finally:
        session.close()


# ── Comparison runs (benchmarking history) ──────────────────────


class ComparisonSave(BaseModel):
    name: str
    file_name: str
    is_video: bool = False
    sam_prompts: list[str] | None = None
    confidence_threshold: float = 0.25
    model_a_id: str | None = None
    model_a_name: str
    model_a_detections: int = 0
    model_a_avg_confidence: float | None = None
    model_a_latency_ms: float = 0
    model_b_id: str | None = None
    model_b_name: str
    model_b_detections: int = 0
    model_b_avg_confidence: float | None = None
    model_b_latency_ms: float = 0
    notes: str | None = None


class ComparisonOut(BaseModel):
    id: str
    name: str
    file_name: str
    is_video: bool
    sam_prompts: list[str] | None
    confidence_threshold: float
    model_a_id: str | None
    model_a_name: str
    model_a_detections: int
    model_a_avg_confidence: float | None
    model_a_latency_ms: float
    model_b_id: str | None
    model_b_name: str
    model_b_detections: int
    model_b_avg_confidence: float | None
    model_b_latency_ms: float
    notes: str | None
    created_at: str


@router.get("/comparisons")
def list_comparisons(
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """List saved comparison runs, newest first."""
    session = SessionLocal()
    try:
        runs = (
            scope_resources(session.query(ComparisonRun), ComparisonRun, principal)
            .order_by(ComparisonRun.created_at.desc())
            .limit(50)
            .all()
        )
        return [
            ComparisonOut(
                id=str(r.id),
                name=r.name,
                file_name=r.file_name,
                is_video=r.is_video or False,
                sam_prompts=r.sam_prompts,
                confidence_threshold=r.confidence_threshold or 0.25,
                model_a_id=r.model_a_id,
                model_a_name=r.model_a_name,
                model_a_detections=r.model_a_detections or 0,
                model_a_avg_confidence=r.model_a_avg_confidence,
                model_a_latency_ms=r.model_a_latency_ms or 0,
                model_b_id=r.model_b_id,
                model_b_name=r.model_b_name,
                model_b_detections=r.model_b_detections or 0,
                model_b_avg_confidence=r.model_b_avg_confidence,
                model_b_latency_ms=r.model_b_latency_ms or 0,
                notes=r.notes,
                created_at=r.created_at.isoformat() if r.created_at else "",
            )
            for r in runs
        ]
    finally:
        session.close()


@router.post("/comparisons")
def save_comparison(
    body: ComparisonSave,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Save a comparison run for future reference."""
    session = SessionLocal()
    try:
        _authorize_comparison_models(principal, body.model_a_id, body.model_b_id)
        run = ComparisonRun(
            workspace_id=principal.workspace_id,
            name=body.name,
            file_name=body.file_name,
            is_video=body.is_video,
            sam_prompts=body.sam_prompts,
            confidence_threshold=body.confidence_threshold,
            model_a_id=body.model_a_id,
            model_a_name=body.model_a_name,
            model_a_detections=body.model_a_detections,
            model_a_avg_confidence=body.model_a_avg_confidence,
            model_a_latency_ms=body.model_a_latency_ms,
            model_b_id=body.model_b_id,
            model_b_name=body.model_b_name,
            model_b_detections=body.model_b_detections,
            model_b_avg_confidence=body.model_b_avg_confidence,
            model_b_latency_ms=body.model_b_latency_ms,
            notes=body.notes,
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return {"id": str(run.id), "status": "saved"}
    finally:
        session.close()


@router.delete("/comparisons/{comparison_id}")
def delete_comparison(
    comparison_id: str,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Delete a saved comparison."""
    session = SessionLocal()
    try:
        run = require_resource(session, principal, ComparisonRun, comparison_id)
        if not run:
            raise HTTPException(status_code=404, detail="Comparison not found")
        session.delete(run)
        session.commit()
        return {"status": "deleted"}
    finally:
        session.close()


# ── Background comparison task ──────────────────────────────────


class CompareRequest(BaseModel):
    model_a_id: str  # UUID or "sam3.1"
    model_b_id: str
    confidence: float = 0.25
    sam_prompts: list[str] | None = None


@router.post("/comparisons/run")
async def run_comparison(
    file: UploadFile = File(...),
    model_a_id: str = Query(...),
    model_b_id: str = Query(...),
    conf: float = Query(0.25),
    sam_prompts: str | None = Query(None),
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Upload a file and kick off a background comparison between two models.

    Returns a session_id to poll for results.
    """
    import uuid as _u

    _authorize_comparison_models(principal, model_a_id, model_b_id)
    session_id = str(_u.uuid4())
    contents = await file.read()
    video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
    is_video = bool(
        (file.content_type and file.content_type.startswith("video/"))
        or Path(file.filename or "").suffix.lower() in video_exts
    )
    prompts = [p.strip() for p in sam_prompts.split(",") if p.strip()] if sam_prompts else None
    from lib.tasks import compare_models_task

    task_id = str(_uuid.uuid4())
    register_task_owner(session_id, principal)
    register_task_owner(task_id, principal)
    input_key = await _store_inference_input(contents, file.filename, principal, session_id)
    try:
        _prepare_inference_task("compare", session_id, task_id)
        task = compare_models_task.apply_async(
            args=[session_id, input_key, is_video, model_a_id, model_b_id, conf, prompts],
            task_id=task_id,
            expires=86400,
        )
        return {"session_id": session_id, "celery_task_id": task.id, "file_name": file.filename, "is_video": is_video}
    except Exception:
        await _remove_inference_input(input_key)
        raise


@router.get("/comparisons/result/{session_id}")
def get_comparison_result(
    session_id: str,
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Poll for comparison results. Returns results if ready, 202 if still running."""
    return _owned_inference_result("compare", session_id, principal)
