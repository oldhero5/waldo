"""Inference serving API — image prediction, video prediction, model activation."""
import asyncio
import logging
import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from lib.db import ModelRegistry, SessionLocal
from lib.inference_engine import get_engine
from lib.tasks import predict_video_task

logger = logging.getLogger(__name__)

router = APIRouter()


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


@router.post("/predict/image", response_model=ImagePredictionResponse)
async def predict_image(
    file: UploadFile = File(...),
    conf: float = Query(0.25, ge=0.0, le=1.0),
    classes: str | None = Query(None),
):
    """Upload an image and get JSON detections back."""
    import cv2
    import numpy as np

    contents = await file.read()
    nparr = np.frombuffer(contents, np.uint8)
    image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(status_code=400, detail="Invalid image file")

    engine = get_engine()
    class_filter = [c.strip() for c in classes.split(",")] if classes else None

    def _run():
        return engine.predict_image(image, conf=conf, class_filter=class_filter)

    try:
        detections = await asyncio.to_thread(_run)
    except RuntimeError as e:
        logger.exception("Inference error on image prediction")
        raise HTTPException(status_code=503, detail=f"Inference error: {e}")

    dets_out = [DetectionOut(**asdict(d)) for d in detections]
    return ImagePredictionResponse(
        detections=dets_out,
        model_id=engine.model_id,
        count=len(dets_out),
    )


@router.post("/predict/video")
async def predict_video(
    file: UploadFile = File(...),
    conf: float = Query(0.25, ge=0.0, le=1.0),
    classes: str | None = Query(None),
):
    """Upload a video for tracked prediction.

    Short videos (<=500 frames): returns full results synchronously (200).
    Long videos: dispatches Celery task and returns session_id to poll via WebSocket (202).
    """
    import uuid

    import cv2

    from lib.video_tracker import validate_video

    # Save uploaded video to temp location
    session_id = str(uuid.uuid4())
    tmp_dir = Path(tempfile.mkdtemp(prefix="waldo_predict_"))
    video_path = tmp_dir / file.filename
    contents = await file.read()
    video_path.write_bytes(contents)

    try:
        validate_video(str(video_path))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    cap = cv2.VideoCapture(str(video_path))
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    cap.release()

    if frame_count <= 500:
        from lib.video_tracker import VideoTracker

        engine = get_engine()
        try:
            engine._ensure_loaded()
        except RuntimeError as e:
            raise HTTPException(status_code=503, detail=str(e))

        def _run_tracking():
            tracker = VideoTracker(conf=conf)
            return tracker.track_video(str(video_path))

        try:
            frame_results = await asyncio.to_thread(_run_tracking)
        except Exception as e:
            logger.exception("Video tracking failed")
            raise HTTPException(status_code=500, detail=f"Video tracking error: {e}")

        # Apply class filter if specified
        class_filter = [c.strip() for c in classes.split(",")] if classes else None
        if class_filter:
            filter_set = set(class_filter)
            for fr in frame_results:
                fr.detections = [d for d in fr.detections if d.class_name in filter_set]

        frames_out = []
        for fr in frame_results:
            frames_out.append(FrameResultOut(
                frame_index=fr.frame_index,
                timestamp_s=fr.timestamp_s,
                detections=[DetectionOut(**asdict(d)) for d in fr.detections],
            ))

        return JSONResponse(
            status_code=200,
            content=VideoPredictionResponse(
                frames=frames_out,
                total_frames=len(frames_out),
                model_id=engine.model_id,
            ).model_dump(),
        )

    # Long videos: dispatch Celery task
    task = predict_video_task.delay(str(video_path), conf, session_id)
    return JSONResponse(
        status_code=202,
        content={"session_id": session_id, "celery_task_id": task.id, "frame_count": frame_count},
    )


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

        # Hot-reload
        engine = get_engine()
        engine.reload(model_id)

        return {"status": "activated", "model_id": model_id, "name": model.name}
    finally:
        session.close()


@router.get("/serve/classes")
def serve_classes():
    """Return list of class names from the active model."""
    engine = get_engine()
    try:
        engine._ensure_loaded()
    except RuntimeError:
        return {"class_names": []}
    return {"class_names": engine.model_info.get("class_names") or []}


@router.get("/serve/status", response_model=ServeStatus)
def serve_status():
    """Return info about the currently loaded model."""
    from lib.config import settings

    engine = get_engine()
    return ServeStatus(
        loaded=engine.model is not None,
        model_id=engine.model_id,
        model_name=engine.model_info.get("name"),
        task_type=engine.model_info.get("task_type"),
        model_variant=engine.model_info.get("model_variant"),
        device=settings.device,
        class_names=engine.model_info.get("class_names"),
    )
