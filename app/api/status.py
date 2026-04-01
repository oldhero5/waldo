from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from lib.db import LabelingJob, SessionLocal
from lib.storage import get_download_url

router = APIRouter()


class JobStatus(BaseModel):
    job_id: str
    video_id: str
    text_prompt: str
    status: str
    progress: float
    total_frames: int
    processed_frames: int
    result_url: str | None = None
    error_message: str | None = None
    celery_task_id: str | None = None


def _job_to_response(job: LabelingJob) -> JobStatus:
    result_url = None
    if job.status == "completed" and job.result_minio_key:
        result_url = get_download_url(job.result_minio_key)

    return JobStatus(
        job_id=str(job.id),
        video_id=str(job.video_id),
        text_prompt=job.text_prompt,
        status=job.status,
        progress=job.progress or 0.0,
        total_frames=job.total_frames or 0,
        processed_frames=job.processed_frames or 0,
        result_url=result_url,
        error_message=job.error_message,
        celery_task_id=job.celery_task_id,
    )


@router.get("/status/{job_id}", response_model=JobStatus)
def get_job_status(job_id: str):
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_to_response(job)
    finally:
        session.close()


@router.get("/status", response_model=list[JobStatus])
def list_jobs(
    video_id: str | None = Query(None),
    limit: int = Query(500, ge=1, le=2000),
):
    session = SessionLocal()
    try:
        query = session.query(LabelingJob)
        if video_id:
            query = query.filter_by(video_id=video_id)
        jobs = query.order_by(LabelingJob.created_at.desc()).limit(limit).all()
        return [_job_to_response(j) for j in jobs]
    finally:
        session.close()
