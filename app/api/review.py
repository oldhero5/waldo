import tempfile
import uuid as _uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func, or_

from lib.auth import get_current_user
from lib.authorization import (
    WorkspacePrincipal,
    get_workspace_principal,
    require_resource,
    require_workspace_editor,
    scope_resources,
)
from lib.dataset_evidence import assessed_frame_count
from lib.db import Annotation, Frame, LabelingJob, SessionLocal
from lib.storage import download_file, get_download_url, upload_file

router = APIRouter(dependencies=[Depends(get_current_user)])


def _validate_uuid(value: str, name: str = "ID") -> None:
    """Raise 400 if value is not a valid UUID."""
    try:
        _uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: {value}")


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
    track_id: int | None = None
    source_video_id: str | None = None
    timestamp_s: float | None = None
    timestamp_method: str = "unknown"


def _annotation_timestamp_method(job, frame) -> str:
    """Use matching clip provenance; merged runs can make the origin ambiguous."""
    if frame is None:
        return "unknown"
    methods = set()
    summaries = [job.processing_summary]
    while summaries:
        summary = summaries.pop()
        if not isinstance(summary, dict):
            continue
        merged_runs = summary.get("merged_runs")
        if isinstance(merged_runs, list):
            summaries.extend(run.get("processing_summary") for run in merged_runs if isinstance(run, dict))
        clips = summary.get("videos")
        if not isinstance(clips, list):
            continue
        for clip in clips:
            if not isinstance(clip, dict) or clip.get("video_id") != str(frame.video_id):
                continue
            times = clip.get("assessed_timestamps_s")
            indices = clip.get("assessed_source_frame_indices")
            if isinstance(times, list):
                matching = [
                    i
                    for i, value in enumerate(times)
                    if isinstance(value, (float, int)) and abs(value - frame.timestamp_s) <= 1e-6
                ]
                if not matching or (
                    isinstance(indices, list)
                    and not any(i < len(indices) and indices[i] == frame.frame_number for i in matching)
                ):
                    continue
            method = clip.get("timestamp_method")
            methods.add(
                method
                if isinstance(method, str) and method in {"source_pts", "frame_index/fps", "resampled_ordinal/fps"}
                else "unknown"
            )
    return methods.pop() if len(methods) == 1 else "unknown"


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
    frame_id: str | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=10000),
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    _validate_uuid(job_id, "job_id")
    if frame_id:
        _validate_uuid(frame_id, "frame_id")
    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, job_id)
        job_id = job.id
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        query = scope_resources(session.query(Annotation), Annotation, principal).filter_by(job_id=job_id)
        if status:
            query = query.filter_by(status=status)
        if frame_id:
            frame = require_resource(session, principal, Frame, frame_id)
            query = query.filter_by(frame_id=frame.id)

        annotations = (
            query.join(Frame, Annotation.frame_id == Frame.id)
            .order_by(Frame.video_id, Frame.timestamp_s, Frame.frame_number, Frame.id, Annotation.id)
            .offset(offset)
            .limit(limit)
            .all()
        )

        # Batch-load all frames in one query instead of N+1
        frame_ids = list({ann.frame_id for ann in annotations})
        frames_map = {}
        if frame_ids:
            frames = scope_resources(session.query(Frame), Frame, principal).filter(Frame.id.in_(frame_ids)).all()
            frames_map = {f.id: f for f in frames}

        results = []
        for ann in annotations:
            frame = frames_map.get(ann.frame_id)
            frame_url = get_download_url(frame.minio_key) if frame else None

            results.append(
                AnnotationOut(
                    id=str(ann.id),
                    frame_id=str(ann.frame_id),
                    class_name=ann.class_name,
                    class_index=ann.class_index,
                    polygon=ann.polygon or [],
                    bbox=ann.bbox,
                    confidence=ann.confidence,
                    status=ann.status or "pending",
                    frame_url=frame_url,
                    track_id=ann.track_id,
                    source_video_id=str(frame.video_id) if frame else None,
                    timestamp_s=frame.timestamp_s if frame else None,
                    timestamp_method=_annotation_timestamp_method(job, frame),
                )
            )

        return results
    finally:
        session.close()


@router.patch("/annotations/{annotation_id}", response_model=AnnotationOut)
def update_annotation(
    annotation_id: str,
    update: AnnotationUpdate,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    _validate_uuid(annotation_id, "annotation_id")
    session = SessionLocal()
    try:
        ann = require_resource(session, principal, Annotation, annotation_id)
        annotation_id = ann.id
        if not ann:
            raise HTTPException(status_code=404, detail="Annotation not found")

        for field in ("status", "polygon", "bbox", "class_name", "class_index"):
            val = getattr(update, field)
            if val is not None:
                setattr(ann, field, val)
                # Existing runs retain their immutable dataset_minio_key snapshot.
                require_resource(session, principal, LabelingJob, ann.job_id).result_minio_key = None

        session.commit()

        frame = require_resource(session, principal, Frame, ann.frame_id)
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
            track_id=ann.track_id,
            source_video_id=str(frame.video_id) if frame else None,
            timestamp_s=frame.timestamp_s if frame else None,
            timestamp_method=_annotation_timestamp_method(
                require_resource(session, principal, LabelingJob, ann.job_id), frame
            ),
        )
    finally:
        session.close()


class JobUpdate(BaseModel):
    name: str | None = None


@router.patch("/jobs/{job_id}")
def update_job(
    job_id: str,
    update: JobUpdate,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Update a labeling job's metadata (e.g. rename)."""
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, job_id)
        job_id = job.id
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if update.name is not None:
            job.name = update.name.strip() or None
        session.commit()
        return {"status": "updated", "job_id": job_id, "name": job.name}
    finally:
        session.close()


@router.delete("/jobs/{job_id}")
def delete_job(
    job_id: str,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Delete a labeling job, its annotations, and any associated training runs/models."""
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, job_id)
        job_id = job.id
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status in ("labeling", "extracting", "converting"):
            raise HTTPException(status_code=400, detail="Cannot delete a job that is currently running")

        # Delete annotations
        ann_count = scope_resources(session.query(Annotation), Annotation, principal).filter_by(job_id=job_id).delete()

        # Unlink training runs (set job_id to null so they don't block deletion)
        from lib.db import TrainingRun

        scope_resources(session.query(TrainingRun), TrainingRun, principal).filter_by(job_id=job.id).update(
            {"job_id": None}, synchronize_session=False
        )

        session.delete(job)
        session.commit()

        return {"status": "deleted", "job_id": job_id, "annotations_deleted": ann_count}
    finally:
        session.close()


class AddClassRequest(BaseModel):
    class_name: str
    prompt: str | None = None


@router.post("/jobs/{job_id}/add-class")
def add_class_to_dataset(
    job_id: str,
    req: AddClassRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Add a new class to an existing dataset by labeling its videos with a new prompt."""
    _validate_uuid(job_id, "job_id")
    from lib.tasks import label_video

    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, job_id)
        job_id = job.id
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if not job.project_id:
            raise HTTPException(status_code=400, detail="Dataset has no associated project/collection")

        raw_prompt = req.prompt or req.class_name
        # Support comma-separated prompt aliases
        aliases = [s.strip() for s in raw_prompt.split(",") if s.strip()]
        display_prompt = aliases[0] if aliases else req.class_name

        # Create a child job for just this class, targeting the same project
        child = LabelingJob(
            parent_id=job.id,
            project_id=job.project_id,
            text_prompt=display_prompt,
            class_prompts=[{"name": req.class_name, "prompts": aliases}]
            if len(aliases) > 1
            else [{"name": req.class_name, "prompt": display_prompt}],
            prompt_type="text",
            task_type=job.task_type or "segment",
            score_threshold=job.score_threshold,
            sample_fps=job.sample_fps,
        )
        session.add(child)
        job.result_minio_key = None
        session.commit()

        # Trigger labeling with merge_into so results merge back into the parent
        task = label_video.delay(str(child.id), merge_into=str(job.id))
        child.celery_task_id = task.id
        session.commit()

        return {
            "status": "labeling",
            "class_name": req.class_name,
            "child_job_id": str(child.id),
            "celery_task_id": task.id,
        }
    finally:
        session.close()


class MergeClassesRequest(BaseModel):
    job_id: str
    source_class: str
    target_class: str


@router.post("/annotations/merge-classes")
def merge_classes(
    req: MergeClassesRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Merge two class names — renames all annotations from source to target."""
    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, req.job_id)
        updated = (
            scope_resources(session.query(Annotation), Annotation, principal)
            .filter_by(job_id=job.id, class_name=req.source_class)
            .update({Annotation.class_name: req.target_class}, synchronize_session=False)
        )
        if updated:
            job.result_minio_key = None
        session.commit()
        return {"status": "merged", "source": req.source_class, "target": req.target_class, "updated": updated}
    finally:
        session.close()


@router.post("/jobs/{job_id}/duplicate")
def duplicate_dataset(
    job_id: str,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Duplicate a labeling job and all its annotations into a new dataset."""
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        original = require_resource(session, principal, LabelingJob, job_id)
        job_id = original.id
        if not original:
            raise HTTPException(status_code=404, detail="Job not found")

        # Compute next version in the lineage
        root_id = original.parent_id or original.id
        max_version = (
            scope_resources(session.query(LabelingJob), LabelingJob, principal)
            .filter((LabelingJob.parent_id == root_id) | (LabelingJob.id == root_id))
            .count()
        )

        # Create new job
        new_job = LabelingJob(
            name=original.name,
            version=max_version + 1,
            parent_id=original.id,
            video_id=original.video_id,
            project_id=original.project_id,
            text_prompt=original.text_prompt,
            prompt_type=original.prompt_type,
            task_type=original.task_type,
            status=original.status,
            score_threshold=original.score_threshold,
            sample_fps=original.sample_fps,
            processing_summary=original.processing_summary,
            total_frames=original.total_frames,
            processed_frames=original.processed_frames,
            result_minio_key=original.result_minio_key,
        )
        session.add(new_job)
        session.flush()

        # Copy annotations
        annotations = scope_resources(session.query(Annotation), Annotation, principal).filter_by(job_id=job_id).all()
        for ann in annotations:
            new_ann = Annotation(
                frame_id=ann.frame_id,
                job_id=new_job.id,
                class_name=ann.class_name,
                class_index=ann.class_index,
                polygon=ann.polygon,
                bbox=ann.bbox,
                confidence=ann.confidence,
                track_id=ann.track_id,
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
def list_job_classes(
    job_id: str,
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """List all unique class names in a dataset with their annotation counts."""
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, job_id)
        job_id = job.id
        results = (
            session.query(Annotation.class_name, func.count())
            .filter_by(job_id=job_id)
            .group_by(Annotation.class_name)
            .order_by(Annotation.class_name)
            .all()
        )
        return {"classes": [{"name": r[0], "count": r[1]} for r in results]}
    finally:
        session.close()


@router.delete("/jobs/{job_id}/classes/{class_name}")
def delete_class(
    job_id: str,
    class_name: str,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Delete all annotations of a specific class from a dataset."""
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, job_id)
        deleted = (
            scope_resources(session.query(Annotation), Annotation, principal)
            .filter_by(job_id=job.id, class_name=class_name)
            .delete()
        )
        if deleted:
            job.result_minio_key = None
        session.commit()
        return {"status": "deleted", "class_name": class_name, "deleted_count": deleted}
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
    name: str | None = None
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
    in_progress_classes: list[str] = []
    in_progress_details: list[dict] = []


@router.get("/jobs/{job_id}/overview", response_model=DatasetOverview)
def get_dataset_overview(
    job_id: str,
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    """Rich dataset overview with sample frame thumbnails and annotation stats."""
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, job_id)
        job_id = job.id
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Status counts via SQL aggregation
        status_counts = dict(
            session.query(Annotation.status, func.count()).filter_by(job_id=job_id).group_by(Annotation.status).all()
        )
        accepted = status_counts.get("accepted", 0)
        rejected = status_counts.get("rejected", 0)
        pending = status_counts.get("pending", 0) + status_counts.get(None, 0)
        total_annotations = sum(status_counts.values())

        # Unique classes via SQL
        classes = sorted([r[0] for r in session.query(distinct(Annotation.class_name)).filter_by(job_id=job_id).all()])

        # Frame count with annotations via SQL
        labeled_frames = (
            session.query(func.count(distinct(Annotation.frame_id))).filter_by(job_id=job_id).scalar()
        ) or 0

        # Sample frames: top 30 frames by annotation count (via SQL)
        frame_counts = (
            session.query(Annotation.frame_id, func.count().label("cnt"))
            .filter_by(job_id=job_id)
            .group_by(Annotation.frame_id)
            .order_by(func.count().desc(), Annotation.frame_id)
            .limit(30)
            .all()
        )
        sample_frame_ids = [fc[0] for fc in frame_counts]
        frame_count_map = {str(fc[0]): fc[1] for fc in frame_counts}

        # Batch-load Frame objects for the sample
        frames_batch = (
            {
                str(f.id): f
                for f in scope_resources(session.query(Frame), Frame, principal)
                .filter(Frame.id.in_(sample_frame_ids))
                .all()
            }
            if sample_frame_ids
            else {}
        )
        # Select representative frames by count, then present those samples on
        # each clip's timeline; count ranking is not playback order.
        frame_order = {
            fid: (str(frame.video_id), frame.timestamp_s, frame.frame_number, fid)
            for fid, frame in frames_batch.items()
        }
        sample_frame_ids.sort(key=lambda fid: frame_order.get(str(fid), ("\uffff", float("inf"), 0, str(fid))))

        # Per-frame status counts and classes (only for the 30 sample frames)
        frame_status_rows = (
            (
                session.query(Annotation.frame_id, Annotation.status, func.count())
                .filter(Annotation.job_id == job_id, Annotation.frame_id.in_(sample_frame_ids))
                .group_by(Annotation.frame_id, Annotation.status)
                .all()
            )
            if sample_frame_ids
            else []
        )
        frame_statuses: dict[str, dict[str, int]] = {}
        for fid, st, cnt in frame_status_rows:
            fid_str = str(fid)
            frame_statuses.setdefault(fid_str, {})
            frame_statuses[fid_str][st or "pending"] = frame_statuses[fid_str].get(st or "pending", 0) + cnt

        frame_class_rows = (
            (
                session.query(Annotation.frame_id, Annotation.class_name)
                .filter(Annotation.job_id == job_id, Annotation.frame_id.in_(sample_frame_ids))
                .distinct()
                .all()
            )
            if sample_frame_ids
            else []
        )
        frame_classes_map: dict[str, list[str]] = {}
        for fid, cname in frame_class_rows:
            frame_classes_map.setdefault(str(fid), []).append(cname)

        sample_frames = []
        for fid_uuid in sample_frame_ids:
            fid = str(fid_uuid)
            frame = frames_batch.get(fid)
            frame_url = get_download_url(frame.minio_key) if frame else None
            st = frame_statuses.get(fid, {})
            sample_frames.append(
                FrameSummary(
                    frame_id=fid,
                    frame_number=frame.frame_number if frame else 0,
                    annotation_count=frame_count_map.get(fid, 0),
                    accepted=st.get("accepted", 0),
                    rejected=st.get("rejected", 0),
                    pending=st.get("pending", 0),
                    thumbnail_url=frame_url,
                    classes=sorted(frame_classes_map.get(fid, [])),
                )
            )

        # Check for feedback
        from lib.db import DemoFeedback

        # Get feedback corrections with full details
        feedback_entries = (
            scope_resources(session.query(DemoFeedback), DemoFeedback, principal)
            .order_by(DemoFeedback.created_at.desc())
            .limit(50)
            .all()
        )
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

        # Count related labeling jobs still in progress:
        # 1. Auto-label jobs (same project + same prompt)
        # 2. Add-class child jobs (parent_id points to this job)
        labeling_in_progress_q = scope_resources(session.query(LabelingJob), LabelingJob, principal).filter(
            LabelingJob.status.notin_(["completed", "partial", "failed"]),
            LabelingJob.id != job.id,
            or_(
                (LabelingJob.project_id == job.project_id) & (LabelingJob.text_prompt == job.text_prompt)
                if job.project_id
                else False,
                LabelingJob.parent_id == job.id,
            ),
        )
        labeling_in_progress = labeling_in_progress_q.count()

        # Get details of in-progress child jobs for the UI
        in_progress_classes = []
        in_progress_details = []
        for j in labeling_in_progress_q.all():
            if j.parent_id == job.id:
                in_progress_classes.append(j.text_prompt)
                in_progress_details.append(
                    {
                        "class_name": j.text_prompt,
                        "status": j.status,
                        "processed": j.processed_frames or 0,
                        "total": j.total_frames or 0,
                        "progress": j.progress or 0.0,
                    }
                )

        return DatasetOverview(
            job_id=str(job.id),
            name=job.name,
            prompt=job.text_prompt or "Exemplar",
            status=job.status,
            total_frames=job.total_frames or 0,
            labeled_frames=labeled_frames,
            total_annotations=total_annotations,
            accepted=accepted,
            rejected=rejected,
            pending=pending,
            classes=classes,
            sample_frames=sample_frames,
            dataset_url=dataset_url,
            feedback_count=feedback_count,
            corrections=corrections,
            labeling_in_progress=labeling_in_progress,
            in_progress_classes=in_progress_classes,
            in_progress_details=in_progress_details,
        )
    finally:
        session.close()


@router.get("/jobs/{job_id}/stats", response_model=JobStats)
def get_job_stats(
    job_id: str,
    principal: WorkspacePrincipal = Depends(get_workspace_principal),
):
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, job_id)
        job_id = job.id
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Total annotation count via SQL
        total_annotations = (session.query(func.count(Annotation.id)).filter(Annotation.job_id == job_id).scalar()) or 0

        # Annotated frames count via SQL
        annotated_frames = (
            session.query(func.count(distinct(Annotation.frame_id))).filter_by(job_id=job_id).scalar()
        ) or 0

        total_frames = assessed_frame_count(job, annotated_frames)

        empty_frames = max(0, total_frames - annotated_frames)

        # By class via SQL aggregation
        by_class = [
            {"name": r[0], "count": r[1]}
            for r in session.query(Annotation.class_name, func.count())
            .filter_by(job_id=job_id)
            .group_by(Annotation.class_name)
            .all()
        ]

        # By status via SQL aggregation
        status_rows = dict(
            session.query(Annotation.status, func.count()).filter_by(job_id=job_id).group_by(Annotation.status).all()
        )
        by_status: dict[str, int] = {
            "pending": status_rows.get("pending", 0) + status_rows.get(None, 0),
            "accepted": status_rows.get("accepted", 0),
            "rejected": status_rows.get("rejected", 0),
        }

        density = total_annotations / annotated_frames if annotated_frames > 0 else 0.0

        return JobStats(
            total_annotations=total_annotations,
            total_frames=total_frames,
            annotated_frames=annotated_frames,
            empty_frames=empty_frames,
            by_class=by_class,
            by_status=by_status,
            annotation_density=round(density, 2),
        )
    finally:
        session.close()


EXPORT_FORMATS = ("segment", "detect", "obb")


class ExportRequest(BaseModel):
    format: str = "segment"


def _annotation_to_label_line(ann: Annotation, fmt: str, class_index: int | None = None) -> str | None:
    """Convert geometry without mutating the stored class index."""
    index = ann.class_index if class_index is None else class_index
    if fmt == "segment":
        if not ann.polygon or len(ann.polygon) < 6:
            return None
        coords = " ".join(f"{v:.6f}" for v in ann.polygon)
        return f"{index} {coords}"

    elif fmt == "detect":
        if ann.bbox and len(ann.bbox) == 4:
            cx, cy, w, h = ann.bbox
            return f"{index} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
        if not ann.polygon or len(ann.polygon) < 6:
            return None
        xs = ann.polygon[0::2]
        ys = ann.polygon[1::2]
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        return f"{index} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"

    elif fmt == "obb":
        if not ann.polygon or len(ann.polygon) < 6:
            return None
        import numpy as np

        pts = np.array(ann.polygon).reshape(-1, 2).astype(np.float32)
        # Scale to pixel-ish coords for minAreaRect, then normalize back
        scale = 1000.0
        pts_scaled = (pts * scale).astype(np.float32)
        import cv2

        rect = cv2.minAreaRect(pts_scaled)
        box = cv2.boxPoints(rect) / scale
        coords = " ".join(f"{box[i][0]:.6f} {box[i][1]:.6f}" for i in range(4))
        return f"{index} {coords}"

    return None


def _write_review_export(dataset_dir, source_dir, frames_map, frame_anns, class_names, fmt):
    """Export whole video groups and never silently omit a visible annotation."""
    from labeler.converters.common import write_yolo_label_dataset

    source_dir.mkdir(parents=True, exist_ok=True)
    class_to_idx = {name: i for i, name in enumerate(class_names)}
    paths, labels, groups = [], [], []
    for fid in sorted(frame_anns):
        annotations = [a for a in frame_anns[fid] if a.status != "rejected"]
        # A rejected detection does not establish that the frame is a reviewed negative.
        if not annotations:
            continue
        frame = frames_map.get(fid)
        if frame is None or not frame.minio_key:
            raise HTTPException(status_code=400, detail=f"Missing source frame {fid}")
        lines = [_annotation_to_label_line(a, fmt, class_to_idx[a.class_name]) for a in annotations]
        if any(line is None for line in lines):
            raise HTTPException(
                status_code=400,
                detail=f"Frame {fid} has geometry unavailable for {fmt}; review it or export detect format",
            )
        path = source_dir / (fid + (Path(frame.minio_key).suffix or ".jpg"))
        download_file(frame.minio_key, path)
        paths.append(path)
        labels.append(lines)
        groups.append(str(frame.video_id))
    if not paths:
        raise HTTPException(status_code=400, detail="No non-rejected annotations to export")
    write_yolo_label_dataset(dataset_dir, paths, labels, class_names, task=fmt, group_ids=groups)


@router.post("/jobs/{job_id}/export")
def export_dataset(
    job_id: str,
    req: ExportRequest,
    principal: WorkspacePrincipal = Depends(require_workspace_editor),
):
    """Re-export a dataset from DB annotations in the requested YOLO format."""
    _validate_uuid(job_id, "job_id")
    fmt = req.format
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}. Use one of {EXPORT_FORMATS}")

    session = SessionLocal()
    try:
        job = require_resource(session, principal, LabelingJob, job_id)
        job_id = job.id
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        annotations = scope_resources(session.query(Annotation), Annotation, principal).filter_by(job_id=job_id).all()
        if not annotations:
            raise HTTPException(status_code=400, detail="No annotations to export")

        # Group annotations by frame
        frame_anns: dict[str, list[Annotation]] = {}
        for a in annotations:
            frame_anns.setdefault(str(a.frame_id), []).append(a)

        # Load frame metadata
        frame_ids = [_uuid.UUID(frame_id) for frame_id in frame_anns]
        frames = scope_resources(session.query(Frame), Frame, principal).filter(Frame.id.in_(frame_ids)).all()
        frames_map = {str(f.id): f for f in frames}

        # Class name → index mapping
        class_names = sorted(set(a.class_name for a in annotations))

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            dataset_dir = tmpdir / "dataset"
            _write_review_export(dataset_dir, tmpdir / "sources", frames_map, frame_anns, class_names, fmt)

            # Zip
            zip_path = tmpdir / "dataset.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in dataset_dir.rglob("*"):
                    if file.is_file():
                        zf.write(file, file.relative_to(dataset_dir))

            # Each export is an immutable snapshot; existing training runs retain their input.
            result_key = f"results/{job_id}/exports/{_uuid.uuid4()}/dataset-{fmt}.zip"
            upload_file(result_key, zip_path)
            if fmt == (job.task_type or "segment"):
                job.result_minio_key = result_key
                session.commit()

        return {"status": "exported", "format": fmt, "download_url": get_download_url(result_key)}
    finally:
        session.close()
