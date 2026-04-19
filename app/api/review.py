import tempfile
import uuid as _uuid
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import distinct, func, or_, text

from lib.auth import get_current_user
from lib.db import Annotation, Frame, LabelingJob, SessionLocal, Video
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
):
    _validate_uuid(job_id, "job_id")
    if frame_id:
        _validate_uuid(frame_id, "frame_id")
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        query = session.query(Annotation).filter_by(job_id=job_id)
        if status:
            query = query.filter_by(status=status)
        if frame_id:
            query = query.filter_by(frame_id=frame_id)

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
                )
            )

        return results
    finally:
        session.close()


@router.patch("/annotations/{annotation_id}", response_model=AnnotationOut)
def update_annotation(annotation_id: str, update: AnnotationUpdate):
    _validate_uuid(annotation_id, "annotation_id")
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


class JobUpdate(BaseModel):
    name: str | None = None


@router.patch("/jobs/{job_id}")
def update_job(job_id: str, update: JobUpdate):
    """Update a labeling job's metadata (e.g. rename)."""
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if update.name is not None:
            job.name = update.name.strip() or None
        session.commit()
        return {"status": "updated", "job_id": job_id, "name": job.name}
    finally:
        session.close()


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    """Delete a labeling job, its annotations, and any associated training runs/models."""
    _validate_uuid(job_id, "job_id")
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


class AddClassRequest(BaseModel):
    class_name: str
    prompt: str | None = None


@router.post("/jobs/{job_id}/add-class")
def add_class_to_dataset(job_id: str, req: AddClassRequest):
    """Add a new class to an existing dataset by labeling its videos with a new prompt."""
    _validate_uuid(job_id, "job_id")
    from lib.tasks import label_video

    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
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
        )
        session.add(child)
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
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        original = session.query(LabelingJob).filter_by(id=job_id).first()
        if not original:
            raise HTTPException(status_code=404, detail="Job not found")

        # Compute next version in the lineage
        root_id = original.parent_id or original.id
        max_version = (
            session.query(LabelingJob).filter((LabelingJob.parent_id == root_id) | (LabelingJob.id == root_id)).count()
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
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
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
def delete_class(job_id: str, class_name: str):
    """Delete all annotations of a specific class from a dataset."""
    _validate_uuid(job_id, "job_id")
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
def get_dataset_overview(job_id: str):
    """Rich dataset overview with sample frame thumbnails and annotation stats."""
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
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
            .order_by(func.count().desc())
            .limit(30)
            .all()
        )
        sample_frame_ids = [fc[0] for fc in frame_counts]
        frame_count_map = {str(fc[0]): fc[1] for fc in frame_counts}

        # Batch-load Frame objects for the sample
        frames_batch = (
            {str(f.id): f for f in session.query(Frame).filter(Frame.id.in_(sample_frame_ids)).all()}
            if sample_frame_ids
            else {}
        )

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

        # Count related labeling jobs still in progress:
        # 1. Auto-label jobs (same project + same prompt)
        # 2. Add-class child jobs (parent_id points to this job)
        labeling_in_progress_q = session.query(LabelingJob).filter(
            LabelingJob.status.notin_(["completed", "failed"]),
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
def get_job_stats(job_id: str):
    _validate_uuid(job_id, "job_id")
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Total annotation count via SQL
        total_annotations = (session.query(func.count(Annotation.id)).filter(Annotation.job_id == job_id).scalar()) or 0

        # Annotated frames count via SQL
        annotated_frames = (
            session.query(func.count(distinct(Annotation.frame_id))).filter_by(job_id=job_id).scalar()
        ) or 0

        # Count actual frames in the DB for this job's videos/project
        if job.project_id:
            # Select only Video.id to avoid loading full ORM objects just for IDs
            video_ids = [row[0] for row in session.query(Video.id).filter_by(project_id=job.project_id).all()]
            total_frames = (
                session.query(Frame).filter(Frame.video_id.in_(video_ids)).count() if video_ids else annotated_frames
            )
        elif job.video_id:
            total_frames = session.query(Frame).filter_by(video_id=job.video_id).count()
        else:
            total_frames = annotated_frames

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


def _annotation_to_label_line(ann: Annotation, fmt: str) -> str | None:
    """Convert a DB annotation to a YOLO label line in the requested format."""
    if fmt == "segment":
        if not ann.polygon or len(ann.polygon) < 6:
            return None
        coords = " ".join(f"{v:.6f}" for v in ann.polygon)
        return f"{ann.class_index} {coords}"

    elif fmt == "detect":
        if ann.bbox and len(ann.bbox) == 4:
            cx, cy, w, h = ann.bbox
            return f"{ann.class_index} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"
        if not ann.polygon or len(ann.polygon) < 6:
            return None
        xs = ann.polygon[0::2]
        ys = ann.polygon[1::2]
        cx = (min(xs) + max(xs)) / 2
        cy = (min(ys) + max(ys)) / 2
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        return f"{ann.class_index} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"

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
        return f"{ann.class_index} {coords}"

    return None


@router.post("/jobs/{job_id}/export")
def export_dataset(job_id: str, req: ExportRequest):
    """Re-export a dataset from DB annotations in the requested YOLO format."""
    _validate_uuid(job_id, "job_id")
    fmt = req.format
    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=400, detail=f"Unsupported format: {fmt}. Use one of {EXPORT_FORMATS}")

    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        annotations = session.query(Annotation).filter_by(job_id=job_id).all()
        if not annotations:
            raise HTTPException(status_code=400, detail="No annotations to export")

        # Group annotations by frame
        frame_anns: dict[str, list[Annotation]] = {}
        for a in annotations:
            frame_anns.setdefault(str(a.frame_id), []).append(a)

        # Load frame metadata
        frame_ids = list(frame_anns.keys())
        frames = session.query(Frame).filter(Frame.id.in_(frame_ids)).all()
        frames_map = {str(f.id): f for f in frames}

        # Class name → index mapping
        class_names = sorted(set(a.class_name for a in annotations))
        class_to_idx = {name: i for i, name in enumerate(class_names)}

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            dataset_dir = tmpdir / "dataset"
            for split in ("train", "val"):
                (dataset_dir / "images" / split).mkdir(parents=True)
                (dataset_dir / "labels" / split).mkdir(parents=True)

            # 90/10 split
            import random

            frame_id_list = list(frame_anns.keys())
            random.shuffle(frame_id_list)
            val_count = max(1, len(frame_id_list) // 10) if len(frame_id_list) > 1 else 0
            val_set = set(frame_id_list[:val_count])

            for fid in frame_id_list:
                frame = frames_map.get(fid)
                if not frame or not frame.minio_key:
                    continue

                split = "val" if fid in val_set else "train"
                ext = Path(frame.minio_key).suffix or ".jpg"
                img_dst = dataset_dir / "images" / split / f"{fid}{ext}"
                download_file(frame.minio_key, img_dst)

                # Convert annotations to label lines
                lines = []
                for ann in frame_anns[fid]:
                    ann.class_index = class_to_idx.get(ann.class_name, 0)
                    line = _annotation_to_label_line(ann, fmt)
                    if line:
                        lines.append(line)

                label_dst = dataset_dir / "labels" / split / f"{fid}.txt"
                label_dst.write_text("\n".join(lines) + "\n" if lines else "")

            # data.yaml
            import yaml

            data_yaml = {
                "path": ".",
                "train": "images/train",
                "val": "images/val",
                "nc": len(class_names),
                "names": {i: name for i, name in enumerate(class_names)},
            }
            (dataset_dir / "data.yaml").write_text(yaml.safe_dump(data_yaml, default_flow_style=False, sort_keys=False))

            # Zip
            zip_path = tmpdir / "dataset.zip"
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for file in dataset_dir.rglob("*"):
                    if file.is_file():
                        zf.write(file, file.relative_to(dataset_dir))

            result_key = f"results/{job_id}/dataset-{fmt}.zip"
            upload_file(result_key, zip_path)

        return {"status": "exported", "format": fmt, "download_url": get_download_url(result_key)}
    finally:
        session.close()
