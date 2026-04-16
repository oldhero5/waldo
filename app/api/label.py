import asyncio
import tempfile
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException
from PIL import Image
from pydantic import BaseModel

from lib.auth import get_current_user
from lib.db import Annotation, Frame, LabelingJob, Project, SessionLocal, Video
from lib.storage import download_file
from lib.tasks import label_video, label_video_exemplar

router = APIRouter(dependencies=[Depends(get_current_user)])


class ClassPrompt(BaseModel):
    name: str
    prompt: str | None = None  # Single prompt (backward compat)
    prompts: list[str] | None = None  # Multiple prompt aliases for one class

    def get_prompts(self) -> list[str]:
        """Return all prompts for this class."""
        if self.prompts:
            return self.prompts
        if self.prompt:
            return [self.prompt]
        return [self.name]


class LabelRequest(BaseModel):
    video_id: str | None = None
    project_id: str | None = None
    text_prompt: str | None = None
    class_prompts: list[ClassPrompt] | None = None
    threshold: float = 0.5
    fps: float = 1.0
    task_type: str = "segment"


class ExemplarRequest(BaseModel):
    video_id: str
    frame_idx: int
    points: list[list[float]]  # [[x, y], ...]
    labels: list[int]  # 1=positive, 0=negative
    task_type: str = "segment"
    class_name: str = "object"


class LabelResponse(BaseModel):
    job_id: str
    status: str
    celery_task_id: str


@router.post("/label", status_code=202, response_model=LabelResponse)
def start_labeling(req: LabelRequest):
    session = SessionLocal()
    try:
        if not req.video_id and not req.project_id:
            raise HTTPException(status_code=400, detail="Either video_id or project_id is required")

        # Normalize class_prompts
        class_prompts = req.class_prompts
        text_prompt = req.text_prompt
        if text_prompt and not class_prompts:
            class_prompts = [ClassPrompt(name=text_prompt, prompt=text_prompt)]
        if not text_prompt and class_prompts:
            text_prompt = class_prompts[0].name

        if not class_prompts and not text_prompt:
            raise HTTPException(status_code=400, detail="Either text_prompt or class_prompts is required")

        video_id = None
        project_id = None

        if req.video_id:
            video = session.query(Video).filter_by(id=req.video_id).first()
            if not video:
                raise HTTPException(status_code=404, detail="Video not found")
            video_id = video.id

        if req.project_id:
            project = session.query(Project).filter_by(id=req.project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            project_id = project.id

        job = LabelingJob(
            video_id=video_id,
            project_id=project_id,
            text_prompt=text_prompt,
            class_prompts=[cp.model_dump() for cp in class_prompts] if class_prompts else None,
            prompt_type="text",
            task_type=req.task_type,
        )
        session.add(job)
        session.commit()

        task = label_video.delay(str(job.id))
        job.celery_task_id = task.id
        session.commit()

        return LabelResponse(job_id=str(job.id), status=job.status, celery_task_id=task.id)
    finally:
        session.close()


@router.post("/label/exemplar", status_code=202, response_model=LabelResponse)
def start_exemplar_labeling(req: ExemplarRequest):
    session = SessionLocal()
    try:
        video = session.query(Video).filter_by(id=req.video_id).first()
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        job = LabelingJob(
            video_id=video.id,
            text_prompt=req.class_name,
            prompt_type="exemplar",
            task_type=req.task_type,
            point_prompts={
                "frame_idx": req.frame_idx,
                "points": req.points,
                "labels": req.labels,
            },
        )
        session.add(job)
        session.commit()

        task = label_video_exemplar.delay(str(job.id))
        job.celery_task_id = task.id
        session.commit()

        return LabelResponse(job_id=str(job.id), status=job.status, celery_task_id=task.id)
    finally:
        session.close()


# ── Prompt preview — quick test on a few frames ──────────────────────


class PreviewRequest(BaseModel):
    video_id: str | None = None
    project_id: str | None = None
    prompts: list[str]  # One or more prompt strings to test
    max_frames: int = 5
    threshold: float = 0.35
    # Contiguous-window mode (preserves tracking) — if duration_sec is set,
    # the worker samples frames from [start_sec, start_sec + duration_sec]
    # at sample_fps samples/sec and runs SimpleTracker across them so object
    # IDs persist. Leave duration_sec null to use the legacy even-sampling.
    start_sec: float = 0.0
    duration_sec: float | None = None
    sample_fps: float = 4.0


class PreviewDetection(BaseModel):
    bbox: list[float]
    score: float
    label: str
    polygon: list[float] | None = None
    track_id: int | None = None


class PreviewFrame(BaseModel):
    frame_idx: int
    image_b64: str  # base64 JPEG — rendered inline via data: URL
    timestamp_s: float
    width: int
    height: int
    detections: list[PreviewDetection]


class PreviewResponse(BaseModel):
    frames: list[PreviewFrame]
    total_detections: int
    unique_track_count: int = 0
    fps: float = 0.0
    video_duration_s: float = 0.0
    mode: str = "sample"


@router.post("/label/preview", response_model=PreviewResponse)
async def preview_prompts(req: PreviewRequest):
    """Prompt playground — dispatches a small SAM3.1 run to the Celery worker
    and returns detections + base64 JPEGs for inline preview.

    Runs synchronously with a 90s timeout. Reuses the worker's cached MLX
    predictor so subsequent calls are ~150ms/frame after the first load.
    Nothing is persisted.
    """
    import asyncio

    session = SessionLocal()
    try:
        # Resolve the video to sample from.
        if req.video_id:
            video = session.query(Video).filter_by(id=req.video_id).first()
            if not video:
                raise HTTPException(status_code=404, detail="Video not found")
        elif req.project_id:
            project = session.query(Project).filter_by(id=req.project_id).first()
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            video = session.query(Video).filter_by(project_id=project.id).first()
            if not video:
                raise HTTPException(status_code=404, detail="No videos in project")
        else:
            raise HTTPException(status_code=400, detail="video_id or project_id required")
        video_id_str = str(video.id)
    finally:
        session.close()

    if not req.prompts:
        raise HTTPException(status_code=400, detail="prompts must be non-empty")

    from lib.tasks import app as celery_app

    def _dispatch_and_wait():
        async_result = celery_app.send_task(
            "waldo.label_playground",
            kwargs={
                "video_id": video_id_str,
                "prompts": req.prompts,
                "threshold": req.threshold,
                "frame_count": req.max_frames,
                "start_sec": req.start_sec,
                "duration_sec": req.duration_sec,
                "sample_fps": req.sample_fps,
            },
        )
        # Window mode can process up to 120 frames — give it headroom.
        return async_result.get(timeout=180)

    try:
        result = await asyncio.to_thread(_dispatch_and_wait)
    except Exception as e:
        raise HTTPException(
            status_code=504 if "timeout" in str(e).lower() else 500,
            detail=f"Playground failed: {e}",
        )

    frames_out: list[PreviewFrame] = []
    for f in result.get("frames", []):
        frames_out.append(
            PreviewFrame(
                frame_idx=f["frame_index"],
                image_b64=f["image_b64"],
                timestamp_s=f["timestamp_s"],
                width=f["width"],
                height=f["height"],
                detections=[
                    PreviewDetection(
                        bbox=d["bbox"],
                        score=d["score"],
                        label=d["label"],
                        polygon=d.get("polygon"),
                        track_id=d.get("track_id"),
                    )
                    for d in f["detections"]
                ],
            )
        )
    return PreviewResponse(
        frames=frames_out,
        total_detections=result.get("total_detections", 0),
        unique_track_count=result.get("unique_track_count", 0),
        fps=result.get("fps", 0.0),
        video_duration_s=result.get("video_duration_s", 0.0),
        mode=result.get("mode", "sample"),
    )


# ── Interactive SAM3 segmentation from click points ──────────────────


class SegmentPointsRequest(BaseModel):
    frame_id: str
    points: list[list[float]]  # [[x, y], ...] in pixel coords
    labels: list[int]  # 1=positive, 0=negative
    threshold: float = 0.3


class SegmentPointsResponse(BaseModel):
    polygons: list[list[float]]  # Each polygon as flat [x1,y1,...] normalized 0-1
    bboxes: list[list[float]]  # Each bbox as [x1,y1,x2,y2] pixels
    scores: list[float]


@router.post("/label/segment-points", response_model=SegmentPointsResponse)
async def segment_with_points(req: SegmentPointsRequest):
    """Run SAM3 on a single frame with click points. Returns polygons for preview."""
    session = SessionLocal()
    try:
        frame = session.query(Frame).filter_by(id=req.frame_id).first()
        if not frame:
            raise HTTPException(status_code=404, detail="Frame not found")
        minio_key = frame.minio_key
    finally:
        session.close()

    def _run():
        from labeler.sam3_engine import get_engine as get_sam3_engine

        with tempfile.TemporaryDirectory() as tmpdir:
            img_path = Path(tmpdir) / "frame.jpg"
            download_file(minio_key, img_path)
            img = Image.open(img_path)
            w, h = img.size

            engine = get_sam3_engine()
            results = engine.segment_frames_with_points(
                frames=[img],
                prompt_frame_idx=0,
                points=req.points,
                labels=req.labels,
                threshold=req.threshold,
            )

            if not results or results[0].masks.shape[0] == 0:
                return SegmentPointsResponse(polygons=[], bboxes=[], scores=[])

            sr = results[0]
            all_polygons = []
            all_bboxes = []
            all_scores = []

            for mask_idx in range(sr.masks.shape[0]):
                mask = sr.masks[mask_idx].astype(np.uint8) * 255
                contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

                for contour in contours:
                    if cv2.contourArea(contour) < 50:
                        continue
                    epsilon = 0.001 * cv2.arcLength(contour, True)
                    approx = cv2.approxPolyDP(contour, epsilon, True)
                    if len(approx) < 3:
                        continue

                    pts = approx.reshape(-1, 2)
                    normalized = []
                    for px, py in pts:
                        normalized.append(float(px / w))
                        normalized.append(float(py / h))
                    all_polygons.append(normalized)

                    x, y, bw, bh = cv2.boundingRect(contour)
                    all_bboxes.append([float(x), float(y), float(x + bw), float(y + bh)])
                    all_scores.append(float(sr.scores[mask_idx]) if mask_idx < len(sr.scores) else 1.0)

            return SegmentPointsResponse(polygons=all_polygons, bboxes=all_bboxes, scores=all_scores)

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"SAM3 segmentation failed: {e}")


# ── Create annotation ────────────────────────────────────────────────


class AnnotationCreateRequest(BaseModel):
    frame_id: str
    job_id: str
    class_name: str
    class_index: int = 0
    polygon: list[float]
    bbox: list[float] | None = None
    confidence: float | None = None
    status: str = "accepted"


class AnnotationCreateResponse(BaseModel):
    id: str
    class_name: str
    status: str


@router.post("/annotations", status_code=201, response_model=AnnotationCreateResponse)
def create_annotation(req: AnnotationCreateRequest):
    """Create a new annotation (e.g. from interactive SAM3 click-to-annotate)."""
    session = SessionLocal()
    try:
        frame = session.query(Frame).filter_by(id=req.frame_id).first()
        if not frame:
            raise HTTPException(status_code=404, detail="Frame not found")

        ann = Annotation(
            frame_id=req.frame_id,
            job_id=req.job_id,
            class_name=req.class_name,
            class_index=req.class_index,
            polygon=req.polygon,
            bbox=req.bbox,
            confidence=req.confidence,
            status=req.status,
        )
        session.add(ann)
        session.commit()
        session.refresh(ann)

        return AnnotationCreateResponse(
            id=str(ann.id),
            class_name=ann.class_name,
            status=ann.status,
        )
    finally:
        session.close()
