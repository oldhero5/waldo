from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text

from lib.db import Annotation, Frame, LabelingJob, SessionLocal
from lib.storage import get_download_url

router = APIRouter()


class AnnotationOut(BaseModel):
    id: str
    frame_id: str
    class_name: str
    class_index: int
    polygon: list
    bbox: list | None = None
    confidence: float | None = None
    status: str
    frame_url: str | None = None


class AnnotationUpdate(BaseModel):
    status: str | None = None
    polygon: list | None = None
    bbox: list | None = None
    class_name: str | None = None
    class_index: int | None = None


class JobStats(BaseModel):
    total_annotations: int
    total_frames: int
    annotated_frames: int
    empty_frames: int
    by_class: list[dict]
    by_status: dict[str, int]
    annotation_density: float


@router.get("/jobs/{job_id}/annotations", response_model=list[AnnotationOut])
def list_annotations(
    job_id: str,
    status: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
):
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        query = session.query(Annotation).filter_by(job_id=job_id)
        if status:
            query = query.filter_by(status=status)

        annotations = query.offset(offset).limit(limit).all()

        # Batch-load all frames in one query instead of N+1
        frame_ids = list({ann.frame_id for ann in annotations})
        frames_map = {}
        if frame_ids:
            frames = session.query(Frame).filter(Frame.id.in_(frame_ids)).all()
            frames_map = {f.id: f for f in frames}

        results = []
        for ann in annotations:
            frame = frames_map.get(ann.frame_id)
            frame_url = get_download_url(frame.minio_key) if frame else None

            results.append(AnnotationOut(
                id=str(ann.id),
                frame_id=str(ann.frame_id),
                class_name=ann.class_name,
                class_index=ann.class_index,
                polygon=ann.polygon or [],
                bbox=ann.bbox,
                confidence=ann.confidence,
                status=ann.status or "pending",
                frame_url=frame_url,
            ))

        return results
    finally:
        session.close()


@router.patch("/annotations/{annotation_id}", response_model=AnnotationOut)
def update_annotation(annotation_id: str, update: AnnotationUpdate):
    session = SessionLocal()
    try:
        ann = session.query(Annotation).filter_by(id=annotation_id).first()
        if not ann:
            raise HTTPException(status_code=404, detail="Annotation not found")

        for field in ("status", "polygon", "bbox", "class_name", "class_index"):
            val = getattr(update, field)
            if val is not None:
                setattr(ann, field, val)

        session.commit()

        frame = session.query(Frame).filter_by(id=ann.frame_id).first()
        frame_url = get_download_url(frame.minio_key) if frame else None

        return AnnotationOut(
            id=str(ann.id),
            frame_id=str(ann.frame_id),
            class_name=ann.class_name,
            class_index=ann.class_index,
            polygon=ann.polygon or [],
            bbox=ann.bbox,
            confidence=ann.confidence,
            status=ann.status or "pending",
            frame_url=frame_url,
        )
    finally:
        session.close()


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    """Delete a labeling job, its annotations, and any associated training runs/models."""
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status in ("labeling", "extracting", "converting"):
            raise HTTPException(status_code=400, detail="Cannot delete a job that is currently running")

        # Delete annotations
        ann_count = session.query(Annotation).filter_by(job_id=job_id).delete()

        # Unlink training runs (set job_id to null so they don't block deletion)
        session.execute(
            text("UPDATE training_runs SET job_id = NULL WHERE job_id = :jid"),
            {"jid": job_id},
        )

        session.delete(job)
        session.commit()

        return {"status": "deleted", "job_id": job_id, "annotations_deleted": ann_count}
    finally:
        session.close()


class MergeClassesRequest(BaseModel):
    job_id: str
    source_class: str
    target_class: str


class AddClassRequest(BaseModel):
    job_id: str
    class_name: str


@router.post("/annotations/merge-classes")
def merge_classes(req: MergeClassesRequest):
    """Merge two class names — renames all annotations from source to target."""
    session = SessionLocal()
    try:
        result = session.execute(
            text("UPDATE annotations SET class_name = :target WHERE job_id = :job_id AND class_name = :source"),
            {"target": req.target_class, "source": req.source_class, "job_id": req.job_id},
        )
        session.commit()
        return {"status": "merged", "source": req.source_class, "target": req.target_class, "updated": result.rowcount}
    finally:
        session.close()


@router.post("/jobs/{job_id}/duplicate")
def duplicate_dataset(job_id: str):
    """Duplicate a labeling job and all its annotations into a new dataset."""
    session = SessionLocal()
    try:

        original = session.query(LabelingJob).filter_by(id=job_id).first()
        if not original:
            raise HTTPException(status_code=404, detail="Job not found")

        # Create new job
        new_job = LabelingJob(
            video_id=original.video_id,
            project_id=original.project_id,
            text_prompt=f"{original.text_prompt} (copy)",
            prompt_type=original.prompt_type,
            task_type=original.task_type,
            status="completed",
            total_frames=original.total_frames,
            processed_frames=original.processed_frames,
            result_minio_key=original.result_minio_key,
        )
        session.add(new_job)
        session.flush()

        # Copy annotations
        annotations = session.query(Annotation).filter_by(job_id=job_id).all()
        for ann in annotations:
            new_ann = Annotation(
                frame_id=ann.frame_id,
                job_id=new_job.id,
                class_name=ann.class_name,
                class_index=ann.class_index,
                polygon=ann.polygon,
                bbox=ann.bbox,
                confidence=ann.confidence,
                status=ann.status,
            )
            session.add(new_ann)

        session.commit()
        return {
            "status": "duplicated",
            "original_id": job_id,
            "new_id": str(new_job.id),
            "annotations_copied": len(annotations),
        }
    finally:
        session.close()


@router.get("/jobs/{job_id}/classes")
def list_job_classes(job_id: str):
    """List all unique class names in a dataset with their annotation counts."""
    session = SessionLocal()
    try:
        annotations = session.query(Annotation).filter_by(job_id=job_id).all()
        class_counts: dict[str, int] = {}
        for a in annotations:
            class_counts[a.class_name] = class_counts.get(a.class_name, 0) + 1
        return {"classes": [{"name": k, "count": v} for k, v in sorted(class_counts.items())]}
    finally:
        session.close()


@router.delete("/jobs/{job_id}/classes/{class_name}")
def delete_class(job_id: str, class_name: str):
    """Delete all annotations of a specific class from a dataset."""
    session = SessionLocal()
    try:
        result = session.execute(
            text("DELETE FROM annotations WHERE job_id = :job_id AND class_name = :class_name"),
            {"job_id": job_id, "class_name": class_name},
        )
        session.commit()
        return {"status": "deleted", "class_name": class_name, "deleted_count": result.rowcount}
    finally:
        session.close()


class FrameSummary(BaseModel):
    frame_id: str
    frame_number: int
    annotation_count: int
    accepted: int
    rejected: int
    pending: int
    thumbnail_url: str | None = None
    classes: list[str]


class CorrectionOut(BaseModel):
    id: str
    class_name: str
    confidence: float | None = None
    bbox: list | None = None
    feedback_type: str
    frame_index: int | None = None
    source_filename: str | None = None


class DatasetOverview(BaseModel):
    job_id: str
    prompt: str
    status: str
    total_frames: int
    labeled_frames: int
    total_annotations: int
    accepted: int
    rejected: int
    pending: int
    classes: list[str]
    sample_frames: list[FrameSummary]
    dataset_url: str | None = None
    feedback_count: int = 0
    corrections: list[CorrectionOut] = []
    labeling_in_progress: int = 0


@router.get("/jobs/{job_id}/overview", response_model=DatasetOverview)
def get_dataset_overview(job_id: str):
    """Rich dataset overview with sample frame thumbnails and annotation stats."""
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        annotations = session.query(Annotation).filter_by(job_id=job_id).all()

        # Aggregate by frame
        frame_map: dict[str, list] = {}
        for a in annotations:
            fid = str(a.frame_id)
            frame_map.setdefault(fid, []).append(a)

        # Status counts
        accepted = sum(1 for a in annotations if a.status == "accepted")
        rejected = sum(1 for a in annotations if a.status == "rejected")
        pending = sum(1 for a in annotations if (a.status or "pending") == "pending")

        # Unique classes
        classes = sorted(set(a.class_name for a in annotations))

        # Sample frames (up to 30 with thumbnails) — batch load frames
        sample_frames = []
        frame_ids = list(frame_map.keys())[:30]
        frames_batch = {str(f.id): f for f in session.query(Frame).filter(Frame.id.in_(frame_ids)).all()} if frame_ids else {}
        for fid in frame_ids:
            anns = frame_map[fid]
            frame = frames_batch.get(fid)
            frame_url = get_download_url(frame.minio_key) if frame else None
            frame_classes = sorted(set(a.class_name for a in anns))
            sample_frames.append(FrameSummary(
                frame_id=fid,
                frame_number=frame.frame_number if frame else 0,
                annotation_count=len(anns),
                accepted=sum(1 for a in anns if a.status == "accepted"),
                rejected=sum(1 for a in anns if a.status == "rejected"),
                pending=sum(1 for a in anns if (a.status or "pending") == "pending"),
                thumbnail_url=frame_url,
                classes=frame_classes,
            ))

        # Check for feedback
        from lib.db import DemoFeedback
        # Get feedback corrections with full details
        feedback_entries = session.query(DemoFeedback).order_by(DemoFeedback.created_at.desc()).limit(50).all()
        feedback_count = len(feedback_entries)
        corrections = [
            CorrectionOut(
                id=str(fb.id),
                class_name=fb.class_name,
                confidence=fb.confidence,
                bbox=fb.bbox,
                feedback_type=fb.feedback_type,
                frame_index=fb.frame_index,
                source_filename=fb.source_filename,
            )
            for fb in feedback_entries
        ]

        dataset_url = None
        if job.result_minio_key:
            dataset_url = get_download_url(job.result_minio_key)

        # Count related labeling jobs still in progress (auto-label on new videos)
        labeling_in_progress = session.query(LabelingJob).filter(
            LabelingJob.project_id == job.project_id,
            LabelingJob.text_prompt == job.text_prompt,
            LabelingJob.status.notin_(["completed", "failed"]),
        ).count() if job.project_id else 0

        return DatasetOverview(
            job_id=str(job.id),
            prompt=job.text_prompt or "Exemplar",
            status=job.status,
            total_frames=job.total_frames or 0,
            labeled_frames=len(frame_map),
            total_annotations=len(annotations),
            accepted=accepted,
            rejected=rejected,
            pending=pending,
            classes=classes,
            sample_frames=sample_frames,
            dataset_url=dataset_url,
            feedback_count=feedback_count,
            corrections=corrections,
            labeling_in_progress=labeling_in_progress,
        )
    finally:
        session.close()


@router.get("/jobs/{job_id}/stats", response_model=JobStats)
def get_job_stats(job_id: str):
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        annotations = session.query(Annotation).filter_by(job_id=job_id).all()

        total_frames = job.total_frames or 0
        annotated_frame_ids = {str(a.frame_id) for a in annotations}
        annotated_frames = len(annotated_frame_ids)
        empty_frames = max(0, total_frames - annotated_frames)

        # By class
        class_counts: dict[str, int] = {}
        for a in annotations:
            class_counts[a.class_name] = class_counts.get(a.class_name, 0) + 1
        by_class = [{"name": k, "count": v} for k, v in class_counts.items()]

        # By status
        by_status: dict[str, int] = {"pending": 0, "accepted": 0, "rejected": 0}
        for a in annotations:
            s = a.status or "pending"
            by_status[s] = by_status.get(s, 0) + 1

        density = len(annotations) / annotated_frames if annotated_frames > 0 else 0.0

        return JobStats(
            total_annotations=len(annotations),
            total_frames=total_frames,
            annotated_frames=annotated_frames,
            empty_frames=empty_frames,
            by_class=by_class,
            by_status=by_status,
            annotation_density=round(density, 2),
        )
    finally:
        session.close()
