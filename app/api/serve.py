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
from lib.db import (
    ComparisonRun,
    DeploymentExperiment,
    DeploymentTarget,
    EdgeDevice,
    InferenceLog,
    ModelRegistry,
    SessionLocal,
)
from lib.inference_engine import get_engine, get_pool
from lib.tasks import predict_video_task

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


def _resolve_experiment_model(target_id: str | None) -> str | None:
    """If there's a running experiment, probabilistically route to champion or challenger."""
    try:
        session = SessionLocal()
        try:
            query = session.query(DeploymentExperiment).filter_by(status="running")
            if target_id:
                # Match experiment for this specific target, or global experiments (target_id=null)
                from sqlalchemy import or_

                query = query.filter(
                    or_(
                        DeploymentExperiment.target_id == target_id,
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


# ── Inference logging helper ────────────────────────────────────


def _log_inference(
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
                model_id=model_id,
                target_id=target_id,
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
):
    """Upload an image and get JSON detections back."""
    import cv2
    import numpy as np

    t0 = time.perf_counter()

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image file")

    h, w = image.shape[:2]
    resolution = f"{w}x{h}"

    # Resolve model: explicit model_id > target_id > active experiment > champion
    resolved_target_id = None
    if target_id and not model_id:
        session = SessionLocal()
        try:
            target = session.query(DeploymentTarget).filter_by(id=target_id, is_active=True).first()
            if target and target.model_id:
                model_id = str(target.model_id)
                resolved_target_id = target_id
        finally:
            session.close()

    # Blue-green: if no explicit model and there's a running experiment, route probabilistically
    if not model_id:
        model_id = _resolve_experiment_model(resolved_target_id)

    pool = get_pool()

    # Get the right engine: specific model_id or active model
    try:
        engine = pool.get_model(model_id) if model_id else pool.get_active_model()
    except Exception as e:
        _log_inference(
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
            engine.model_id, resolved_target_id, "image", latency, 0, None, [], resolution, "inference_error"
        )
        logger.exception("Inference error on image prediction")
        raise HTTPException(status_code=503, detail=f"Inference error: {e}")

    dets_out = [DetectionOut(**asdict(d)) for d in detections]

    # Log the inference
    latency = (time.perf_counter() - t0) * 1000
    avg_conf = sum(d.confidence for d in dets_out) / len(dets_out) if dets_out else None
    classes_found = list({d.class_name for d in dets_out})
    _log_inference(
        engine.model_id, resolved_target_id, "image", latency, len(dets_out), avg_conf, classes_found, resolution
    )

    return ImagePredictionResponse(
        detections=dets_out,
        model_id=engine.model_id,
        count=len(dets_out),
    )


@router.post("/predict/sam", response_model=ImagePredictionResponse)
async def predict_sam(
    file: UploadFile = File(...),
    prompts: str = Query(..., description="Comma-separated text prompts, e.g. 'person,car'"),
    conf: float = Query(0.15, ge=0.0, le=1.0),
):
    """Run SAM 3.1 (MLX) on an image with text prompts. Returns detections in the same format as YOLO predict.

    This is the teacher model — use for comparison against trained student (YOLO) models.
    """

    import cv2
    import numpy as np
    from PIL import Image as PILImage

    t0 = time.perf_counter()

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image file")

    h, w = image.shape[:2]
    resolution = f"{w}x{h}"
    prompt_list = [p.strip() for p in prompts.split(",") if p.strip()]
    if not prompt_list:
        raise HTTPException(status_code=400, detail="At least one prompt required")

    def _run_sam():
        import mlx.core as mx
        from mlx_vlm.models.sam3_1.generate import _get_backbone_features

        from labeler.sam3_optimized import detect_with_backbone_fast
        from labeler.video_labeler import _get_predictor

        predictor = _get_predictor(threshold=conf)

        # Convert to PIL for SAM — use same preprocessing as video pipeline
        pil_image = PILImage.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        inputs = predictor.processor.preprocess_image(pil_image)
        pixel_values = mx.array(inputs["pixel_values"])
        backbone_features = _get_backbone_features(predictor.model, pixel_values)

        result = detect_with_backbone_fast(
            predictor,
            backbone_features,
            prompt_list,
            image_size=pil_image.size,
            threshold=conf,
            encoder_cache={},
        )
        return result

    try:
        result = await asyncio.to_thread(_run_sam)
    except Exception as e:
        latency = (time.perf_counter() - t0) * 1000
        _log_inference(None, None, "image", latency, 0, None, [], resolution, "sam_error")
        logger.exception("SAM 3.1 inference error")
        raise HTTPException(status_code=503, detail=f"SAM 3.1 inference error: {e}")

    # Convert SAM DetectionResult to our standard DetectionOut format
    dets_out = []
    for i in range(len(result.scores)):
        bbox = result.boxes[i].tolist() if i < len(result.boxes) else [0, 0, 0, 0]
        label = result.labels[i] if result.labels and i < len(result.labels) else prompt_list[0]

        # Extract mask polygon if available
        mask_polygon = None
        if i < len(result.masks):
            mask = result.masks[i]
            mask_u8 = (mask > 0.5).astype(np.uint8) * 255
            if mask_u8.shape != (h, w):
                mask_u8 = cv2.resize(mask_u8, (w, h), interpolation=cv2.INTER_NEAREST)
            contours, _ = cv2.findContours(mask_u8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                largest = max(contours, key=cv2.contourArea)
                if cv2.contourArea(largest) > 50:
                    eps = 0.001 * cv2.arcLength(largest, True)
                    approx = cv2.approxPolyDP(largest, eps, True)
                    if len(approx) >= 3:
                        mask_polygon = [[float(px), float(py)] for px, py in approx.reshape(-1, 2)]

        dets_out.append(
            DetectionOut(
                class_name=label,
                class_index=prompt_list.index(label) if label in prompt_list else 0,
                confidence=float(result.scores[i]),
                bbox=[float(x) for x in bbox],
                mask=mask_polygon,
            )
        )

    latency = (time.perf_counter() - t0) * 1000
    avg_conf = sum(d.confidence for d in dets_out) / len(dets_out) if dets_out else None
    classes_found = list({d.class_name for d in dets_out})
    _log_inference(None, None, "image", latency, len(dets_out), avg_conf, classes_found, resolution)

    return ImagePredictionResponse(
        detections=dets_out,
        model_id="sam3.1",
        count=len(dets_out),
    )


@router.post("/predict/sam/video")
async def predict_sam_video(
    file: UploadFile = File(...),
    prompts: str = Query(..., description="Comma-separated text prompts"),
    conf: float = Query(0.35, ge=0.0, le=1.0),
):
    """Run SAM 3.1 (MLX) video-native tracking on a video. Returns per-frame detections with tracking IDs."""
    import cv2

    t0 = time.perf_counter()

    tmp_dir = Path(tempfile.mkdtemp(prefix="waldo_sam_predict_"))
    try:
        safe_name = Path(file.filename).name  # strips directory traversal
        video_path = tmp_dir / safe_name
        contents = await file.read()
        video_path.write_bytes(contents)

        cap = cv2.VideoCapture(str(video_path))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cap.release()
        resolution = f"{width}x{height}"

        prompt_list = [p.strip() for p in prompts.split(",") if p.strip()]
        if not prompt_list:
            raise HTTPException(status_code=400, detail="At least one prompt required")

        def _run_sam_video():
            """Run SAM 3.1 video tracking in a clean session."""
            import cv2 as _cv2
            import mlx.core as mx
            from mlx_vlm.generate import wired_limit
            from mlx_vlm.models.sam3.generate import SimpleTracker
            from mlx_vlm.models.sam3_1.generate import _get_backbone_features
            from PIL import Image as PILImage

            from labeler.sam3_optimized import detect_with_backbone_fast
            from labeler.video_labeler import _get_predictor, _result_to_detections

            predictor = _get_predictor(threshold=conf)
            cap = _cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                raise RuntimeError(f"Cannot open video: {video_path}")

            total = int(cap.get(_cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(_cv2.CAP_PROP_FPS) or 24.0
            W = int(cap.get(_cv2.CAP_PROP_FRAME_WIDTH))
            H = int(cap.get(_cv2.CAP_PROP_FRAME_HEIGHT))

            # Fresh tracker per run — no state leakage
            tracker = SimpleTracker()
            results = []

            with wired_limit(predictor.model):
                for fi in range(total):
                    ret, frame_bgr = cap.read()
                    if not ret:
                        break
                    # Detect every 15 frames for comparison granularity
                    if fi % 15 != 0:
                        continue

                    frame_pil = PILImage.fromarray(_cv2.cvtColor(frame_bgr, _cv2.COLOR_BGR2RGB))
                    inputs = predictor.processor.preprocess_image(frame_pil)
                    pixel_values = mx.array(inputs["pixel_values"])

                    # Fresh backbone every frame for accuracy
                    backbone = _get_backbone_features(predictor.model, pixel_values)

                    result = detect_with_backbone_fast(
                        predictor,
                        backbone,
                        prompt_list,
                        image_size=frame_pil.size,
                        threshold=conf,
                        encoder_cache={},  # no cache — clean per frame
                    )
                    result = tracker.update(result)

                    if len(result.scores) > 0:
                        results.append(
                            {
                                "frame_idx": fi,
                                "timestamp_s": fi / fps,
                                "width": W,
                                "height": H,
                                "detections": _result_to_detections(result, W, H, prompt_list),
                            }
                        )

            cap.release()
            return results

        try:
            raw_results = await asyncio.to_thread(_run_sam_video)
        except Exception as e:
            latency = (time.perf_counter() - t0) * 1000
            _log_inference(None, None, "video", latency, 0, None, [], resolution, "sam_video_error")
            logger.exception("SAM 3.1 video tracking error")
            raise HTTPException(status_code=503, detail=f"SAM 3.1 video error: {e}")

        # Convert to standard FrameResultOut format
        frames_out = []
        total_dets = 0
        total_conf = 0.0
        all_classes: set[str] = set()

        for fr in raw_results:
            dets = []
            for d in fr["detections"]:
                bbox = d.get("bbox") or [0, 0, 0, 0]
                mask_polygon = None
                raw_poly = d.get("polygon")
                if raw_poly and len(raw_poly) >= 6:
                    # polygon is [x_norm, y_norm, ...] — convert to pixel coords
                    w, h = fr["width"], fr["height"]
                    mask_polygon = [[raw_poly[i] * w, raw_poly[i + 1] * h] for i in range(0, len(raw_poly), 2)]

                det = DetectionOut(
                    class_name=d.get("label", prompt_list[0]),
                    class_index=prompt_list.index(d.get("label", prompt_list[0]))
                    if d.get("label") in prompt_list
                    else 0,
                    confidence=d.get("score", 0.0),
                    bbox=[float(x) for x in bbox],
                    track_id=d.get("track_id"),
                    mask=mask_polygon,
                )
                dets.append(det)
                total_dets += 1
                total_conf += det.confidence
                all_classes.add(det.class_name)

            frames_out.append(
                FrameResultOut(
                    frame_index=fr["frame_idx"],
                    timestamp_s=fr["timestamp_s"],
                    detections=dets,
                )
            )

        latency = (time.perf_counter() - t0) * 1000
        avg_conf = total_conf / total_dets if total_dets else None
        _log_inference(None, None, "video", latency, total_dets, avg_conf, list(all_classes), resolution)

        return JSONResponse(
            status_code=200,
            content=VideoPredictionResponse(
                frames=frames_out,
                total_frames=len(frames_out),
                model_id="sam3.1",
            ).model_dump(),
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@router.post("/predict/video")
async def predict_video(
    file: UploadFile = File(...),
    conf: float = Query(0.25, ge=0.0, le=1.0),
    classes: str | None = Query(None),
    target_id: str | None = Query(None),
    model_id: str | None = Query(None, description="Specific model ID to use"),
):
    """Upload a video for tracked prediction.

    Short videos (<=500 frames): returns full results synchronously (200).
    Long videos: dispatches Celery task and returns session_id to poll via WebSocket (202).
    """
    import uuid

    import cv2

    from lib.video_tracker import validate_video

    t0 = time.perf_counter()

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

    # Resolve model: explicit model_id > target_id > active
    resolved_model_id = model_id
    if not resolved_model_id and target_id:
        db = SessionLocal()
        try:
            target = db.query(DeploymentTarget).filter_by(id=target_id, is_active=True).first()
            if target and target.model_id:
                resolved_model_id = str(target.model_id)
        finally:
            db.close()

    if frame_count <= 500:
        try:
            from lib.video_tracker import VideoTracker

            pool = get_pool()
            try:
                engine = pool.get_model(resolved_model_id) if resolved_model_id else pool.get_active_model()
            except RuntimeError as e:
                raise HTTPException(status_code=503, detail=str(e))

            def _run_tracking():
                tracker = VideoTracker(conf=conf)
                return tracker.track_video(str(video_path))

            try:
                frame_results = await asyncio.to_thread(_run_tracking)
            except Exception as e:
                latency = (time.perf_counter() - t0) * 1000
                _log_inference(engine.model_id, target_id, "video", latency, 0, None, [], resolution, "tracking_error")
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
                engine.model_id, target_id, "video", latency, total_dets, avg_conf, list(all_classes), resolution
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

    # Long videos: dispatch Celery task (tmp_dir cleanup is the task's responsibility)
    task = predict_video_task.delay(str(video_path), conf, session_id)
    return JSONResponse(
        status_code=202,
        content={"session_id": session_id, "celery_task_id": task.id, "frame_count": frame_count},
    )


# ── Model management ────────────────────────────────────────────


@router.post("/models/{model_id}/activate")
def activate_model(model_id: str):
    """Set a model as active and trigger hot-reload in the inference engine."""
    session = SessionLocal()
    try:
        model = session.query(ModelRegistry).filter_by(id=model_id).first()
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")

        # Deactivate all other models
        session.query(ModelRegistry).update({"is_active": False})
        model.is_active = True
        session.commit()

        # Hot-reload via pool
        pool = get_pool()
        pool.reload_model(model_id)

        return {"status": "activated", "model_id": model_id, "name": model.name}
    finally:
        session.close()


@router.get("/serve/classes")
def serve_classes():
    """Return list of class names from the active model."""
    try:
        engine = get_pool().get_active_model()
    except RuntimeError:
        return {"class_names": []}
    return {"class_names": engine.model_info.get("class_names") or []}


@router.get("/serve/status", response_model=ServeStatus)
def serve_status():
    """Return info about the currently loaded model.

    On a fresh install no model is active yet — return loaded=False instead
    of 500ing, so the UI poll on the dashboard doesn't spam errors.
    """
    from lib.config import settings

    try:
        engine = get_engine()
    except RuntimeError:
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
def list_targets():
    """List all deployment targets with their assigned model info."""
    session = SessionLocal()
    try:
        targets = session.query(DeploymentTarget).order_by(DeploymentTarget.created_at.desc()).all()
        result = []
        for t in targets:
            model_name = None
            if t.model_id:
                model = session.query(ModelRegistry).filter_by(id=t.model_id).first()
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
def create_target(body: TargetCreate):
    """Create a new deployment target (camera, zone, or region)."""
    session = SessionLocal()
    try:
        # Validate model_id if provided
        if body.model_id:
            model = session.query(ModelRegistry).filter_by(id=body.model_id).first()
            if not model:
                raise HTTPException(status_code=404, detail="Model not found")

        # Auto-generate slug from name
        import re

        slug = re.sub(r"[^a-z0-9]+", "-", body.name.lower()).strip("-")[:80]
        # Ensure unique
        existing = session.query(DeploymentTarget).filter_by(slug=slug).first()
        if existing:
            slug = f"{slug}-{str(_uuid.uuid4())[:6]}"

        target = DeploymentTarget(
            name=body.name,
            slug=slug,
            location_label=body.location_label,
            target_type=body.target_type or "api",
            model_id=body.model_id,
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
def update_target(target_id: str, body: TargetUpdate):
    """Update a deployment target."""
    session = SessionLocal()
    try:
        target = session.query(DeploymentTarget).filter_by(id=target_id).first()
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
                model = session.query(ModelRegistry).filter_by(id=body.model_id).first()
                if not model:
                    raise HTTPException(status_code=404, detail="Model not found")
            target.model_id = body.model_id or None
        if body.config is not None:
            target.config = body.config
        if body.is_active is not None:
            target.is_active = body.is_active

        session.commit()
        return {"status": "updated", "id": target_id}
    finally:
        session.close()


@router.delete("/targets/{target_id}")
def delete_target(target_id: str):
    """Delete a deployment target."""
    session = SessionLocal()
    try:
        target = session.query(DeploymentTarget).filter_by(id=target_id).first()
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
):
    """Run inference through a named endpoint. Each endpoint serves a specific model.

    External devices/frontends connect to their assigned endpoint URL.
    Example: curl -X POST http://host/api/v1/endpoints/package-detector/predict -F file=@img.jpg
    """
    import cv2
    import numpy as np

    session = SessionLocal()
    try:
        target = session.query(DeploymentTarget).filter_by(slug=slug, is_active=True).first()
        if not target:
            raise HTTPException(status_code=404, detail=f"Endpoint '{slug}' not found or not active")
        if not target.model_id:
            raise HTTPException(status_code=503, detail=f"Endpoint '{slug}' has no model assigned")

        model_id = str(target.model_id)
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

    engine = get_pool().get(model_id)

    def _run():
        return engine.predict_image(image, conf=confidence, class_filter=class_filter)

    try:
        detections = await asyncio.to_thread(_run)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Log inference
    try:
        log = InferenceLog(
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
def endpoint_status(slug: str):
    """Get status of a named endpoint — model info, config, and whether it's loaded."""
    session = SessionLocal()
    try:
        target = session.query(DeploymentTarget).filter_by(slug=slug).first()
        if not target:
            raise HTTPException(status_code=404, detail=f"Endpoint '{slug}' not found")

        model_info = None
        is_loaded = False
        if target.model_id:
            model = session.query(ModelRegistry).filter_by(id=target.model_id).first()
            if model:
                model_info = {
                    "id": str(model.id),
                    "name": model.name,
                    "variant": model.model_variant,
                    "task_type": model.task_type,
                    "class_names": model.class_names,
                }
            pool = get_pool()
            is_loaded = str(target.model_id) in pool.loaded_models()

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
def metrics_summary(window: str = Query("1h", pattern="^(1h|24h|7d)$")):
    """Aggregate inference metrics for the monitoring dashboard."""

    window_map = {"1h": "1 hour", "24h": "24 hours", "7d": "7 days"}
    interval = window_map[window]

    session = SessionLocal()
    try:
        # The `interval` and `bucket` values below come from server-controlled
        # allowlists (window_map), never from user input — the S608 warnings on
        # the f-strings in this function are silenced via per-file-ignores.
        from sqlalchemy import text

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
            WHERE created_at >= now() - interval '{interval}'
        """)
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
            WHERE il.created_at >= now() - interval '{interval}'
            GROUP BY il.model_id, mr.name
            ORDER BY request_count DESC
            LIMIT 10
        """)
        ).fetchall()

        # Per-class breakdown
        class_rows = session.execute(
            text(f"""
            SELECT
                cls.value::text as class_name,
                count(*) as detection_count
            FROM inference_logs il,
                 jsonb_array_elements_text(il.classes_detected::jsonb) as cls(value)
            WHERE il.created_at >= now() - interval '{interval}'
              AND il.classes_detected IS NOT NULL
              AND il.classes_detected::text != '[]'
            GROUP BY cls.value
            ORDER BY detection_count DESC
            LIMIT 20
        """)
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
            WHERE il.created_at >= now() - interval '{interval}'
              AND il.target_id IS NOT NULL
            GROUP BY il.target_id, dt.name, dt.location_label
            ORDER BY request_count DESC
        """)
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
            WHERE created_at >= now() - interval '{interval}'
            GROUP BY bucket
            ORDER BY bucket
        """)
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
def promote_model(model_id: str, alias: str = Query("champion")):
    """Promote a model to a named alias (champion, challenger, staging).

    Setting alias=champion also sets is_active=True for backward compatibility
    and clears champion from any other model.
    """
    if alias not in ("champion", "challenger", "staging"):
        raise HTTPException(status_code=400, detail="Alias must be champion, challenger, or staging")

    session = SessionLocal()
    try:
        model = session.query(ModelRegistry).filter_by(id=model_id).first()
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")

        # Clear this alias from any other model
        session.query(ModelRegistry).filter(
            ModelRegistry.alias == alias,
            ModelRegistry.id != model_id,
        ).update({"alias": None})

        model.alias = alias

        # Champion = active model (backward compat)
        if alias == "champion":
            session.query(ModelRegistry).update({"is_active": False})
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
def list_experiments():
    """List all deployment experiments."""
    session = SessionLocal()
    try:
        exps = session.query(DeploymentExperiment).order_by(DeploymentExperiment.created_at.desc()).all()
        result = []
        for e in exps:
            champ = session.query(ModelRegistry).filter_by(id=e.champion_model_id).first()
            chall = session.query(ModelRegistry).filter_by(id=e.challenger_model_id).first()
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
def create_experiment(body: ExperimentCreate):
    """Start a blue-green deployment experiment."""
    session = SessionLocal()
    try:
        # Validate models exist
        for mid in [body.champion_model_id, body.challenger_model_id]:
            if not session.query(ModelRegistry).filter_by(id=mid).first():
                raise HTTPException(status_code=404, detail=f"Model {mid} not found")

        # Cancel any existing running experiment for the same target
        existing = session.query(DeploymentExperiment).filter_by(status="running")
        if body.target_id:
            from sqlalchemy import or_

            existing = existing.filter(
                or_(
                    DeploymentExperiment.target_id == body.target_id,
                    DeploymentExperiment.target_id.is_(None),
                )
            )
        else:
            existing = existing.filter(DeploymentExperiment.target_id.is_(None))

        for e in existing.all():
            e.status = "cancelled"

        # Set aliases
        champ = session.query(ModelRegistry).filter_by(id=body.champion_model_id).first()
        chall = session.query(ModelRegistry).filter_by(id=body.challenger_model_id).first()
        if champ:
            champ.alias = "champion"
        if chall:
            chall.alias = "challenger"

        exp = DeploymentExperiment(
            name=body.name,
            champion_model_id=body.champion_model_id,
            challenger_model_id=body.challenger_model_id,
            split_pct=body.split_pct,
            target_id=body.target_id,
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
def complete_experiment(experiment_id: str, winner: str = Query(..., pattern="^(champion|challenger)$")):
    """End an experiment and optionally promote the winner."""
    from datetime import datetime

    session = SessionLocal()
    try:
        exp = session.query(DeploymentExperiment).filter_by(id=experiment_id).first()
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
            session.query(ModelRegistry).filter(ModelRegistry.alias == "champion").update(
                {"alias": None, "is_active": False}
            )
            chall = session.query(ModelRegistry).filter_by(id=exp.challenger_model_id).first()
            if chall:
                chall.alias = "champion"
                chall.is_active = True
                pool = get_pool()
                pool.reload_model(str(chall.id))

        # Clear challenger alias
        session.query(ModelRegistry).filter(ModelRegistry.alias == "challenger").update({"alias": None})

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
def list_devices():
    """List all registered edge devices."""
    session = SessionLocal()
    try:
        devices = session.query(EdgeDevice).order_by(EdgeDevice.created_at.desc()).all()
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
def register_device(body: EdgeDeviceCreate):
    """Register a new edge device."""
    session = SessionLocal()
    try:
        device = EdgeDevice(
            name=body.name,
            device_type=body.device_type,
            location_label=body.location_label,
            target_id=body.target_id,
            model_id=body.model_id,
            hardware_info=body.hardware_info,
        )
        session.add(device)
        session.commit()
        session.refresh(device)
        return {"id": str(device.id), "status": "registered"}
    finally:
        session.close()


@router.post("/devices/{device_id}/heartbeat")
def device_heartbeat(device_id: str, ip: str | None = Query(None)):
    """Edge device phones home — updates status and last_heartbeat."""
    from datetime import datetime

    session = SessionLocal()
    try:
        device = session.query(EdgeDevice).filter_by(id=device_id).first()
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
            model = session.query(ModelRegistry).filter_by(id=device.model_id).first()
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
async def sync_device_logs(device_id: str, file: UploadFile = File(...)):
    """Upload inference logs from an offline edge device.

    Expects a JSON file with an array of log entries:
    [{"timestamp": "...", "latency_ms": ..., "detection_count": ..., "avg_confidence": ..., "classes_detected": [...], "input_resolution": "...", "error_code": null}, ...]
    """
    import json
    from datetime import datetime

    session = SessionLocal()
    try:
        device = session.query(EdgeDevice).filter_by(id=device_id).first()
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
def list_comparisons():
    """List saved comparison runs, newest first."""
    session = SessionLocal()
    try:
        runs = session.query(ComparisonRun).order_by(ComparisonRun.created_at.desc()).limit(50).all()
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
def save_comparison(body: ComparisonSave):
    """Save a comparison run for future reference."""
    session = SessionLocal()
    try:
        run = ComparisonRun(
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
def delete_comparison(comparison_id: str):
    """Delete a saved comparison."""
    session = SessionLocal()
    try:
        run = session.query(ComparisonRun).filter_by(id=comparison_id).first()
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
):
    """Upload a file and kick off a background comparison between two models.

    Returns a session_id to poll for results.
    """
    import uuid as _u

    session_id = str(_u.uuid4())
    tmp_dir = Path(tempfile.mkdtemp(prefix="waldo_compare_"))
    try:
        safe_name = Path(file.filename).name  # strips directory traversal
        file_path = tmp_dir / safe_name
        contents = await file.read()
        file_path.write_bytes(contents)

        # Detect video by content type or file extension
        video_exts = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
        name_lower = (file.filename or "").lower()
        is_video = (file.content_type and file.content_type.startswith("video/")) or any(
            name_lower.endswith(ext) for ext in video_exts
        )
        prompts = [p.strip() for p in sam_prompts.split(",")] if sam_prompts else None

        from lib.tasks import compare_models_task

        # tmp_dir cleanup is the Celery task's responsibility after this point
        task = compare_models_task.delay(
            session_id,
            str(file_path),
            bool(is_video),
            model_a_id,
            model_b_id,
            conf,
            prompts,
        )

        return {
            "session_id": session_id,
            "celery_task_id": task.id,
            "file_name": file.filename,
            "is_video": bool(is_video),
        }
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise


@router.get("/comparisons/result/{session_id}")
def get_comparison_result(session_id: str):
    """Poll for comparison results. Returns results if ready, 202 if still running."""
    import json

    import redis

    from lib.config import settings

    client = redis.Redis.from_url(settings.redis_url)
    raw = client.get(f"waldo:compare:result:{session_id}")
    if not raw:
        return JSONResponse(status_code=202, content={"status": "running", "session_id": session_id})

    results = json.loads(raw)
    return {"status": "completed", "session_id": session_id, "results": results}
