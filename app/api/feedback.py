from fastapi import APIRouter, Query
from pydantic import BaseModel

from lib.db import DemoFeedback, SessionLocal

router = APIRouter()


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


class FeedbackOut(BaseModel):
    id: str
    feedback_type: str
    class_name: str
    confidence: float | None = None
    bbox: list | None = None
    track_id: int | None = None
    frame_index: int | None = None
    source_filename: str | None = None
    created_at: str


class FeedbackBatchIn(BaseModel):
    items: list[FeedbackIn]


@router.post("/feedback", response_model=FeedbackOut, status_code=201)
def submit_feedback(body: FeedbackIn):
    session = SessionLocal()
    try:
        fb = DemoFeedback(
            model_id=body.model_id,
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
        )
        session.add(fb)
        session.commit()
        session.refresh(fb)

        return FeedbackOut(
            id=str(fb.id),
            feedback_type=fb.feedback_type,
            class_name=fb.class_name,
            confidence=fb.confidence,
            bbox=fb.bbox,
            track_id=fb.track_id,
            frame_index=fb.frame_index,
            source_filename=fb.source_filename,
            created_at=fb.created_at.isoformat(),
        )
    finally:
        session.close()


@router.post("/feedback/batch", response_model=list[FeedbackOut], status_code=201)
def submit_feedback_batch(body: FeedbackBatchIn):
    session = SessionLocal()
    try:
        results = []
        for item in body.items:
            fb = DemoFeedback(
                model_id=item.model_id,
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
            )
            session.add(fb)
            results.append(fb)
        session.commit()
        for fb in results:
            session.refresh(fb)

        return [
            FeedbackOut(
                id=str(fb.id),
                feedback_type=fb.feedback_type,
                class_name=fb.class_name,
                created_at=fb.created_at.isoformat(),
            )
            for fb in results
        ]
    finally:
        session.close()


@router.get("/feedback", response_model=list[FeedbackOut])
def list_feedback(
    model_id: str | None = Query(None),
    feedback_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    session = SessionLocal()
    try:
        query = session.query(DemoFeedback)
        if model_id:
            query = query.filter_by(model_id=model_id)
        if feedback_type:
            query = query.filter_by(feedback_type=feedback_type)
        query = query.order_by(DemoFeedback.created_at.desc()).limit(limit)

        return [
            FeedbackOut(
                id=str(fb.id),
                feedback_type=fb.feedback_type,
                class_name=fb.class_name,
                created_at=fb.created_at.isoformat(),
            )
            for fb in query.all()
        ]
    finally:
        session.close()
