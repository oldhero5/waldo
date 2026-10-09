import base64
import uuid

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from lib.auth import get_current_user
from lib.authorization import (
    WorkspacePrincipal,
    get_workspace_principal,
    require_resource,
    require_workspace_editor,
    scope_resources,
)
from lib.db import DemoFeedback, ModelRegistry, SessionLocal
from lib.storage import get_download_url, upload_bytes

router = APIRouter(dependencies=[Depends(get_current_user)])


class FeedbackIn(BaseModel):
    model_id: str | None = None
    class_name: str
    bbox: list[float]  # [x1, y1, x2, y2] in source pixels
    polygon: list | None = None
    confidence: float | None = None
    track_id: int | None = None
    frame_index: int | None = None
    timestamp_s: float | None = None
    feedback_type: str = "false_positive"
    corrected_class: str | None = None
    source_filename: str | None = None
    frame_image_b64: str | None = None  # base64-encoded JPEG frame snapshot


class FeedbackOut(BaseModel):
    id: str
    feedback_type: str
    class_name: str
    confidence: float | None = None
    bbox: list | None = None
    polygon: list | None = None
    track_id: int | None = None
    frame_index: int | None = None
    source_filename: str | None = None
    frame_url: str | None = None
    created_at: str


class FeedbackBatchIn(BaseModel):
    items: list[FeedbackIn]


def _store_frame_image(image_b64: str) -> str:
    """Decode base64 JPEG and upload to MinIO. Returns the object key."""
    data = base64.b64decode(image_b64)
    key = f"feedback/frames/{uuid.uuid4()}.jpg"
    upload_bytes(key, data, content_type="image/jpeg")
    return key


def _fb_to_out(fb: DemoFeedback) -> FeedbackOut:
    return FeedbackOut(
        id=str(fb.id),
        feedback_type=fb.feedback_type,
        class_name=fb.class_name,
        confidence=fb.confidence,
        bbox=fb.bbox,
        polygon=fb.polygon,
        track_id=fb.track_id,
        frame_index=fb.frame_index,
        source_filename=fb.source_filename,
        frame_url=get_download_url(fb.minio_key) if fb.minio_key else None,
        created_at=fb.created_at.isoformat(),
    )


@router.post("/feedback", response_model=FeedbackOut, status_code=201)
def submit_feedback(
    body: FeedbackIn,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    session = SessionLocal()
    try:
        model = require_resource(session, principal, ModelRegistry, body.model_id) if body.model_id else None
        minio_key = None
        if body.frame_image_b64:
            minio_key = _store_frame_image(body.frame_image_b64)

        fb = DemoFeedback(
            workspace_id=principal.workspace_id,
            model_id=model.id if model else None,
            class_name=body.class_name,
            bbox=body.bbox,
            polygon=body.polygon,
            confidence=body.confidence,
            track_id=body.track_id,
            frame_index=body.frame_index,
            timestamp_s=body.timestamp_s,
            feedback_type=body.feedback_type,
            corrected_class=body.corrected_class,
            source_filename=body.source_filename,
            minio_key=minio_key,
        )
        session.add(fb)
        session.commit()
        session.refresh(fb)
        return _fb_to_out(fb)
    finally:
        session.close()


@router.post("/feedback/batch", response_model=list[FeedbackOut], status_code=201)
def submit_feedback_batch(
    body: FeedbackBatchIn,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    session = SessionLocal()
    try:
        models = [
            require_resource(session, principal, ModelRegistry, item.model_id) if item.model_id else None
            for item in body.items
        ]
        results = []
        for item, model in zip(body.items, models, strict=True):
            minio_key = None
            if item.frame_image_b64:
                minio_key = _store_frame_image(item.frame_image_b64)

            fb = DemoFeedback(
                workspace_id=principal.workspace_id,
                model_id=model.id if model else None,
                class_name=item.class_name,
                bbox=item.bbox,
                polygon=item.polygon,
                confidence=item.confidence,
                track_id=item.track_id,
                frame_index=item.frame_index,
                timestamp_s=item.timestamp_s,
                feedback_type=item.feedback_type,
                corrected_class=item.corrected_class,
                source_filename=item.source_filename,
                minio_key=minio_key,
            )
            session.add(fb)
            results.append(fb)
        session.commit()
        for fb in results:
            session.refresh(fb)
        return [_fb_to_out(fb) for fb in results]
    finally:
        session.close()


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(
    model_id: str | None = Query(None),
    feedback_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    session = SessionLocal()
    try:
        query = scope_resources(session.query(DemoFeedback), DemoFeedback, principal)
        if model_id:
            model = require_resource(session, principal, ModelRegistry, model_id)
            query = query.filter_by(model_id=model.id)
        if feedback_type:
            query = query.filter_by(feedback_type=feedback_type)
        query = query.order_by(DemoFeedback.created_at.desc()).limit(limit)
        return [_fb_to_out(fb) for fb in query.all()]
    finally:
        session.close()
