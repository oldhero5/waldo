import asyncio
import tempfile
import uuid
from pathlib import Path

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, Field

from lib.auth import get_current_user
from lib.authorization import (
    WorkspacePrincipal,
    register_task_owner,
    require_resource,
    require_workspace_editor,
)
from lib.dataset_evidence import invalidate_current_export
from lib.db import Annotation, Frame, LabelingJob, Project, SessionLocal, Video
from lib.preview import normalize_preview_result
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
    threshold: float = Field(0.5, ge=0, le=1, allow_inf_nan=False)
    fps: float = Field(1.0, gt=0, allow_inf_nan=False)
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
def start_labeling(
    req: LabelRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
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
            video = require_resource(session, principal, Video, req.video_id)
            if not video:
                raise HTTPException(status_code=404, detail="Video not found")
            video_id = video.id
            project_id = video.project_id

        if req.project_id:
            project = require_resource(session, principal, Project, req.project_id)
            if not project:
                raise HTTPException(status_code=404, detail="Project not found")
            project_id = project.id
            if req.video_id and video.project_id != project.id:
                raise HTTPException(status_code=400, detail="Video does not belong to the requested project")

        job = LabelingJob(
            video_id=video_id,
            project_id=project_id,
            text_prompt=text_prompt,
            class_prompts=[cp.model_dump() for cp in class_prompts] if class_prompts else None,
            prompt_type="text",
            task_type=req.task_type,
            score_threshold=req.threshold,
            sample_fps=req.fps,
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
def start_exemplar_labeling(
    req: ExemplarRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    session = SessionLocal()
    try:
        video = require_resource(session, principal, Video, req.video_id)
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        job = LabelingJob(
            video_id=video.id,
            project_id=video.project_id,
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
    source_width: int | None = None
    source_height: int | None = None
    frame_duration_s: float | None = None
    timestamp_method: str = "unknown"


class PreviewResponse(BaseModel):
    frames: list[PreviewFrame]
    total_detections: int
    unique_track_count: int = 0
    fps: float = 0.0
    video_duration_s: float = 0.0
    mode: str = "sample"


def _preview_result_to_response(result: dict) -> PreviewResponse:
    """Shape the raw Celery task return into the public PreviewResponse.

    Kept as a free function so both the sync (?wait=true) path and the polling
    endpoint produce identical bodies.
    """
    return PreviewResponse.model_validate(normalize_preview_result(result))


@router.post("/label/preview")
async def preview_prompts(
    req: PreviewRequest,
    wait: bool = Query(
        False,
        description=(
            "Deprecated. When true, blocks the request thread until the "
            "preview completes (legacy behavior). New callers should omit "
            "this flag and poll /api/v1/job/{job_id} instead — the "
            "synchronous path will be removed in a future release."
        ),
    ),
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Prompt playground — dispatches a small SAM3.1 run to the Celery worker.

    Default (async): returns 202 with `{job_id, status, result_url}`. Poll the
    `result_url` until `status == "completed"`, then read `result` for the
    `PreviewResponse` body.

    `?wait=true` (deprecated): blocks the thread on the Celery task with a
    180s timeout and returns the `PreviewResponse` body directly. Provided
    for one-release backward compat with API consumers that expected a
    synchronous shape; will be removed.
    """
    session = SessionLocal()
    try:
        # Resolve the video to sample from.
        if req.video_id:
            video = require_resource(session, principal, Video, req.video_id)
            if not video:
                raise HTTPException(status_code=404, detail="Video not found")
        elif req.project_id:
            project = require_resource(session, principal, Project, req.project_id)
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

    task_id = str(uuid.uuid4())
    register_task_owner(task_id, principal)
    async_result = celery_app.send_task(
        "waldo.label_playground",
        task_id=task_id,
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

    if not wait:
        # Default: fire-and-poll. Return immediately so this endpoint can't
        # starve the FastAPI thread pool under load.
        return JSONResponse(
            status_code=202,
            content={
                "job_id": async_result.id,
                "status": "queued",
                "result_url": f"/api/v1/job/{async_result.id}",
            },
        )

    # Legacy ?wait=true path: block on the result inside a worker thread so
    # the event loop itself stays responsive. Only kept for one release; new
    # callers should poll instead.
    def _wait_for_result():
        # Window mode can process up to 120 frames — give it headroom.
        return async_result.get(timeout=180)

    try:
        result = await asyncio.to_thread(_wait_for_result)
    except Exception as e:
        raise HTTPException(
            status_code=504 if "timeout" in str(e).lower() else 500,
            detail=f"Playground failed: {e}",
        )

    return _preview_result_to_response(result)


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
async def segment_with_points(
    req: SegmentPointsRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Run SAM3 on a single frame with click points. Returns polygons for preview."""
    session = SessionLocal()
    try:
        frame = require_resource(session, principal, Frame, req.frame_id)
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
def create_annotation(
    req: AnnotationCreateRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Create a new annotation (e.g. from interactive SAM3 click-to-annotate)."""
    session = SessionLocal()
    try:
        frame = require_resource(session, principal, Frame, req.frame_id)
        if not frame:
            raise HTTPException(status_code=404, detail="Frame not found")

        job = require_resource(session, principal, LabelingJob, req.job_id)
        video = require_resource(session, principal, Video, frame.video_id)
        if (job.video_id and job.video_id != frame.video_id) or (job.project_id and job.project_id != video.project_id):
            raise HTTPException(status_code=400, detail="Frame does not belong to the requested dataset")
        invalidate_current_export(session, job.id)
        ann = Annotation(
            frame_id=frame.id,
            job_id=job.id,
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
