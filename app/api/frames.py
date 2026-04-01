from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from lib.db import Annotation, Frame, SessionLocal, Video
from lib.storage import get_download_url

router = APIRouter()


class FrameOut(BaseModel):
    id: str
    video_id: str
    frame_number: int
    timestamp_s: float
    width: int | None = None
    height: int | None = None
    image_url: str


class FrameDetail(FrameOut):
    annotations: list[dict]


@router.get("/videos/{video_id}/frames", response_model=list[FrameOut])
def list_frames(
    video_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    session = SessionLocal()
    try:
        video = session.query(Video).filter_by(id=video_id).first()
        if not video:
            raise HTTPException(status_code=404, detail="Video not found")

        frames = (
            session.query(Frame)
            .filter_by(video_id=video_id)
            .order_by(Frame.frame_number)
            .offset(offset)
            .limit(limit)
            .all()
        )

        return [
            FrameOut(
                id=str(f.id),
                video_id=str(f.video_id),
                frame_number=f.frame_number,
                timestamp_s=f.timestamp_s,
                width=f.width,
                height=f.height,
                image_url=get_download_url(f.minio_key),
            )
            for f in frames
        ]
    finally:
        session.close()


@router.get("/frames/{frame_id}", response_model=FrameDetail)
def get_frame(frame_id: str):
    session = SessionLocal()
    try:
        frame = session.query(Frame).filter_by(id=frame_id).first()
        if not frame:
            raise HTTPException(status_code=404, detail="Frame not found")

        annotations = session.query(Annotation).filter_by(frame_id=frame_id).all()

        return FrameDetail(
            id=str(frame.id),
            video_id=str(frame.video_id),
            frame_number=frame.frame_number,
            timestamp_s=frame.timestamp_s,
            width=frame.width,
            height=frame.height,
            image_url=get_download_url(frame.minio_key),
            annotations=[
                {
                    "id": str(a.id),
                    "class_name": a.class_name,
                    "class_index": a.class_index,
                    "polygon": a.polygon or [],
                    "bbox": a.bbox,
                    "confidence": a.confidence,
                    "status": a.status or "pending",
                }
                for a in annotations
            ],
        )
    finally:
        session.close()
