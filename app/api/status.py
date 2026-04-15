import uuid as _uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func

from lib.auth import get_current_user
from lib.db import Annotation, LabelingJob, SessionLocal
from lib.storage import get_download_url


def _validate_uuid(value: str, name: str = "ID") -> None:
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: {value}")


router = APIRouter(dependencies=[Depends(get_current_user)])


class JobStatus(BaseModel):
    job_id: str
    name: str | None = None
    video_id: str
    text_prompt: str
    status: str
    progress: float
    total_frames: int
    processed_frames: int
    result_url: str | None = None
    error_message: str | None = None
    celery_task_id: str | None = None
    annotation_count: int | None = None
    class_count: int | None = None
    version: int = 1
    parent_id: str | None = None


def _job_to_response(
    job: LabelingJob,
    annotation_count: int | None = None,
    class_count: int | None = None,
) -> JobStatus:
    result_url = None
    if job.status == "completed" and job.result_minio_key:
        result_url = get_download_url(job.result_minio_key)

    return JobStatus(
        job_id=str(job.id),
        name=job.name,
        video_id=str(job.video_id),
        text_prompt=job.text_prompt,
        status=job.status,
        progress=job.progress or 0.0,
        total_frames=job.total_frames or 0,
        processed_frames=job.processed_frames or 0,
        result_url=result_url,
        error_message=job.error_message,
        celery_task_id=job.celery_task_id,
        annotation_count=annotation_count,
        class_count=class_count,
        version=job.version or 1,
        parent_id=str(job.parent_id) if job.parent_id else None,
    )


@router.get("/status/{job_id}", response_model=JobStatus)
def get_job_status(job_id: str):
    _validate_uuid(job_id, "job_id")
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

        # Batch-query annotation counts and class counts per job
        job_ids = [j.id for j in jobs]
        ann_stats: dict[str, tuple[int, int]] = {}
        if job_ids:
            rows = (
                session.query(
                    Annotation.job_id,
                    func.count(Annotation.id),
                    func.count(func.distinct(Annotation.class_name)),
                )
                .filter(Annotation.job_id.in_(job_ids))
                .group_by(Annotation.job_id)
                .all()
            )
            for job_id, ann_count, cls_count in rows:
                ann_stats[str(job_id)] = (ann_count, cls_count)

        results = []
        for j in jobs:
            counts = ann_stats.get(str(j.id), (0, 0))
            results.append(_job_to_response(j, annotation_count=counts[0], class_count=counts[1]))
        return results
    finally:
        session.close()
