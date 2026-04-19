from collections import Counter

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from lib.auth import get_current_user
from lib.db import (
    Annotation,
    Frame,
    LabelingJob,
    ModelRegistry,
    SessionLocal,
    TrainingRun,
)
from lib.storage import get_download_url
from lib.tasks import export_model_task, train_model
from trainer.train_manager import AUGMENTATION_PRESETS, DEFAULT_HYPERPARAMS, TASK_TO_DEFAULT_VARIANT, VARIANTS

router = APIRouter(dependencies=[Depends(get_current_user)])


def _safe_alias(model: ModelRegistry) -> str | None:
    """Safely read the alias column — returns None if column doesn't exist yet (pre-migration)."""
    try:
        return model.alias
    except Exception:
        return None


class TrainRequest(BaseModel):
    job_id: str
    name: str = ""
    model_variant: str = ""
    task_type: str = "segment"
    hyperparameters: dict = {}


class TrainResponse(BaseModel):
    run_id: str
    status: str
    celery_task_id: str


class TrainingRunStatus(BaseModel):
    run_id: str
    job_id: str | None = None
    name: str
    task_type: str
    model_variant: str
    status: str
    epoch_current: int
    total_epochs: int
    metrics: dict
    best_metrics: dict
    hyperparameters: dict = {}
    loss_history: list = []
    metric_history: list = []
    weights_url: str | None = None
    error_message: str | None = None
    celery_task_id: str | None = None
    tags: list[str] = []
    notes: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class ModelOut(BaseModel):
    id: str
    name: str
    task_type: str
    model_variant: str
    version: int
    metrics: dict
    export_formats: dict
    weights_url: str | None = None
    is_active: bool
    alias: str | None = None


class ExportRequest(BaseModel):
    format: str


class VariantsResponse(BaseModel):
    variants: dict[str, str]
    defaults: dict[str, str]
    hyperparams: dict
    augmentation_presets: dict[str, list[str]] = {}


class ClassBalance(BaseModel):
    name: str
    count: int
    share: float  # 0-1 fraction of total annotations


class DatasetStats(BaseModel):
    job_id: str
    job_name: str | None
    task_type: str
    total_frames: int
    annotated_frames: int
    empty_frames: int
    total_annotations: int
    class_count: int
    classes: list[ClassBalance]
    min_class_count: int
    max_class_count: int
    imbalance_ratio: float  # max / max(1, min); 1.0 = perfectly balanced
    small_object_ratio: float  # fraction of annotations with bbox area <1% of frame
    avg_bbox_area: float  # 0-1 normalized
    recommended_variant: str
    recommended_epochs: int
    recommended_batch: int
    recommended_imgsz: int
    recommended_augmentation: str
    warnings: list[str]


def _compute_dataset_stats(session, job: LabelingJob) -> DatasetStats:
    annotations = (
        session.query(Annotation, Frame)
        .join(Frame, Annotation.frame_id == Frame.id)
        .filter(Annotation.job_id == job.id)
        .all()
    )
    class_counter: Counter[str] = Counter()
    frame_ids: set = set()
    small_object_count = 0
    bbox_area_total = 0.0
    bbox_area_samples = 0

    for ann, frame in annotations:
        class_counter[ann.class_name] += 1
        frame_ids.add(frame.id)
        if ann.bbox and frame.width and frame.height:
            # bbox = [x1, y1, x2, y2] in pixel coords
            try:
                x1, y1, x2, y2 = ann.bbox[:4]
                w_norm = max(0.0, (x2 - x1) / max(1, frame.width))
                h_norm = max(0.0, (y2 - y1) / max(1, frame.height))
                area = w_norm * h_norm
                bbox_area_total += area
                bbox_area_samples += 1
                if area < 0.01:
                    small_object_count += 1
            except Exception:
                pass

    # Count frames that belong to this job's dataset (whether annotated or not)
    if job.project_id:
        total_frames = (
            session.query(func.count(Frame.id)).join(Frame.video).filter_by(project_id=job.project_id).scalar() or 0
        )
    elif job.video_id:
        total_frames = session.query(func.count(Frame.id)).filter_by(video_id=job.video_id).scalar() or 0
    else:
        total_frames = len(frame_ids)
    total_frames = max(total_frames, len(frame_ids))

    total_ann = sum(class_counter.values())
    classes_sorted = class_counter.most_common()
    class_balance = [
        ClassBalance(name=n, count=c, share=(c / total_ann) if total_ann else 0.0) for n, c in classes_sorted
    ]
    min_c = min(class_counter.values()) if class_counter else 0
    max_c = max(class_counter.values()) if class_counter else 0
    imbalance = (max_c / max(1, min_c)) if class_counter else 1.0
    small_ratio = (small_object_count / total_ann) if total_ann else 0.0
    avg_area = (bbox_area_total / bbox_area_samples) if bbox_area_samples else 0.0

    # Recommend a model variant + training schedule based on dataset size.
    # Heuristic: more data → bigger model + more epochs. Tiny datasets need
    # more epochs but a tiny model to avoid overfit. Small objects get bumped
    # to imgsz=1280 so detail survives the backbone.
    annotated = len(frame_ids)
    task = job.task_type or "segment"
    seg_suffix = "-seg" if task == "segment" else ""

    if annotated < 50:
        rec_variant = f"yolo26n{seg_suffix}"
        rec_epochs = 150
        rec_batch = 8
        rec_aug = "aggressive"
    elif annotated < 200:
        rec_variant = f"yolo26n{seg_suffix}"
        rec_epochs = 100
        rec_batch = 16
        rec_aug = "standard"
    elif annotated < 1000:
        rec_variant = f"yolo26s{seg_suffix}"
        rec_epochs = 80
        rec_batch = 16
        rec_aug = "standard"
    else:
        rec_variant = f"yolo26m{seg_suffix}"
        rec_epochs = 60
        rec_batch = 16
        rec_aug = "standard"

    rec_imgsz = 1280 if small_ratio > 0.25 else 640

    # Warnings the UI will surface — one line each.
    warnings: list[str] = []
    if annotated < 50:
        warnings.append(f"Only {annotated} annotated frames. Aim for 100+ per class for reliable training.")
    if class_counter and imbalance >= 5:
        warnings.append(
            f"Class imbalance {imbalance:.0f}× between '{classes_sorted[0][0]}' and '{classes_sorted[-1][0]}'. "
            "Consider collecting more examples of the rare class or using class weights."
        )
    if small_ratio >= 0.25:
        warnings.append(
            f"{small_ratio * 100:.0f}% of objects are smaller than 1% of the frame. "
            "Training at 1280px resolution is recommended so detail survives the backbone."
        )
    if class_counter and min_c < 10:
        warnings.append(f"Rarest class has only {min_c} instance(s). YOLO needs at least ~10–20 per class to learn it.")
    if total_ann == 0:
        warnings.append("No annotations on this job — run a labeling pass before training.")

    return DatasetStats(
        job_id=str(job.id),
        job_name=job.name or job.text_prompt,
        task_type=task,
        total_frames=total_frames,
        annotated_frames=annotated,
        empty_frames=max(0, total_frames - annotated),
        total_annotations=total_ann,
        class_count=len(class_counter),
        classes=class_balance,
        min_class_count=min_c,
        max_class_count=max_c,
        imbalance_ratio=imbalance,
        small_object_ratio=small_ratio,
        avg_bbox_area=avg_area,
        recommended_variant=rec_variant,
        recommended_epochs=rec_epochs,
        recommended_batch=rec_batch,
        recommended_imgsz=rec_imgsz,
        recommended_augmentation=rec_aug,
        warnings=warnings,
    )


@router.get("/train/dataset-stats/{job_id}", response_model=DatasetStats)
def dataset_stats(job_id: str):
    """Pre-flight dataset quality report — class histogram, imbalance score,
    small-object ratio, and recommended hyperparameters. The TrainPage shows
    this above the config form so users can fix data problems before burning
    GPU time."""
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Labeling job not found")
        return _compute_dataset_stats(session, job)
    finally:
        session.close()


@router.get("/train/variants", response_model=VariantsResponse)
def get_variants():
    return VariantsResponse(
        variants=VARIANTS,
        defaults=TASK_TO_DEFAULT_VARIANT,
        hyperparams=DEFAULT_HYPERPARAMS,
        augmentation_presets={k: list(v.keys()) for k, v in AUGMENTATION_PRESETS.items()},
    )


@router.post("/train", status_code=202, response_model=TrainResponse)
def start_training(req: TrainRequest):
    session = SessionLocal()
    try:
        # joinedload video so job.video.project_id doesn't trigger a lazy SELECT
        job = session.query(LabelingJob).filter_by(id=req.job_id).options(joinedload(LabelingJob.video)).first()
        if not job:
            raise HTTPException(status_code=404, detail="Labeling job not found")
        if job.status != "completed":
            raise HTTPException(status_code=400, detail=f"Job not completed (status: {job.status})")

        variant = req.model_variant or TASK_TO_DEFAULT_VARIANT.get(req.task_type, "yolo11n-seg")
        name = req.name or f"{job.text_prompt or 'exemplar'}_{variant}"

        project_id = job.project_id or (job.video.project_id if job.video else None)
        if not project_id:
            raise HTTPException(status_code=400, detail="Job has no associated project")

        run = TrainingRun(
            project_id=project_id,
            job_id=job.id,
            name=name,
            task_type=req.task_type,
            model_variant=variant,
            hyperparameters={**DEFAULT_HYPERPARAMS, **req.hyperparameters},
            total_epochs=req.hyperparameters.get("epochs", DEFAULT_HYPERPARAMS["epochs"]),
            dataset_minio_key=job.result_minio_key,
        )
        session.add(run)
        session.commit()

        task = train_model.delay(str(run.id))
        run.celery_task_id = task.id
        session.commit()

        return TrainResponse(run_id=str(run.id), status=run.status, celery_task_id=task.id)
    finally:
        session.close()


@router.get("/train/{run_id}", response_model=TrainingRunStatus)
def get_training_status(run_id: str):
    session = SessionLocal()
    try:
        run = session.query(TrainingRun).filter_by(id=run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Training run not found")

        weights_url = None
        if run.best_weights_minio_key:
            weights_url = get_download_url(run.best_weights_minio_key)

        return TrainingRunStatus(
            run_id=str(run.id),
            job_id=str(run.job_id) if run.job_id else None,
            name=run.name,
            task_type=run.task_type,
            model_variant=run.model_variant,
            status=run.status,
            epoch_current=run.epoch_current or 0,
            total_epochs=run.total_epochs or 100,
            metrics=run.metrics or {},
            best_metrics=run.best_metrics or {},
            hyperparameters=run.hyperparameters or {},
            loss_history=run.loss_history or [],
            metric_history=run.metric_history or [],
            weights_url=weights_url,
            error_message=run.error_message,
            celery_task_id=run.celery_task_id,
            tags=run.tags or [],
            notes=run.notes,
            started_at=run.started_at.isoformat() if run.started_at else None,
            completed_at=run.completed_at.isoformat() if run.completed_at else None,
        )
    finally:
        session.close()


@router.get("/train", response_model=list[TrainingRunStatus])
def list_training_runs(project_id: str | None = Query(None)):
    session = SessionLocal()
    try:
        query = session.query(TrainingRun)
        if project_id:
            query = query.filter_by(project_id=project_id)
        runs = query.order_by(TrainingRun.created_at.desc()).all()

        results = []
        for run in runs:
            weights_url = None
            if run.best_weights_minio_key:
                weights_url = get_download_url(run.best_weights_minio_key)
            results.append(
                TrainingRunStatus(
                    run_id=str(run.id),
                    job_id=str(run.job_id) if run.job_id else None,
                    name=run.name,
                    task_type=run.task_type,
                    model_variant=run.model_variant,
                    status=run.status,
                    epoch_current=run.epoch_current or 0,
                    total_epochs=run.total_epochs or 100,
                    metrics=run.metrics or {},
                    best_metrics=run.best_metrics or {},
                    loss_history=run.loss_history or [],
                    metric_history=run.metric_history or [],
                    weights_url=weights_url,
                    error_message=run.error_message,
                    celery_task_id=run.celery_task_id,
                )
            )
        return results
    finally:
        session.close()


class RunUpdate(BaseModel):
    tags: list[str] | None = None
    notes: str | None = None


@router.patch("/train/{run_id}")
def update_training_run(run_id: str, update: RunUpdate):
    """Update tags or notes on a training run."""
    session = SessionLocal()
    try:
        run = session.query(TrainingRun).filter_by(id=run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        if update.tags is not None:
            run.tags = update.tags
        if update.notes is not None:
            run.notes = update.notes or None
        session.commit()
        return {"status": "updated", "run_id": run_id}
    finally:
        session.close()


@router.delete("/train/{run_id}")
def delete_training_run(run_id: str):
    """Delete a training run and its associated model."""
    session = SessionLocal()
    try:
        run = session.query(TrainingRun).filter_by(id=run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Training run not found")
        if run.status in ("training", "preparing"):
            raise HTTPException(status_code=400, detail="Cannot delete a running training job")
        # Delete associated models
        models = session.query(ModelRegistry).filter_by(training_run_id=run.id).all()
        for m in models:
            session.delete(m)
        session.delete(run)
        session.commit()
        return {"status": "deleted", "run_id": run_id}
    finally:
        session.close()


@router.post("/train/{run_id}/stop")
def stop_training(run_id: str):
    """Request early stopping for a training run."""
    from trainer.metrics_streamer import request_stop

    session = SessionLocal()
    try:
        run = session.query(TrainingRun).filter_by(id=run_id).first()
        if not run:
            raise HTTPException(status_code=404, detail="Training run not found")
        if run.status not in ("training", "preparing"):
            raise HTTPException(status_code=400, detail=f"Run is {run.status}, cannot stop")
        request_stop(run_id)
        return {"status": "stop_requested", "run_id": run_id}
    finally:
        session.close()


@router.get("/models", response_model=list[ModelOut])
def list_models():
    session = SessionLocal()
    try:
        models = session.query(ModelRegistry).order_by(ModelRegistry.created_at.desc()).all()
        return [
            ModelOut(
                id=str(m.id),
                name=m.name,
                task_type=m.task_type,
                model_variant=m.model_variant,
                version=m.version or 1,
                metrics=m.metrics or {},
                export_formats=m.export_formats or {},
                weights_url=get_download_url(m.weights_minio_key) if m.weights_minio_key else None,
                is_active=m.is_active or False,
                alias=_safe_alias(m),
            )
            for m in models
        ]
    finally:
        session.close()


@router.post("/models/{model_id}/export", status_code=202)
def export_model(model_id: str, req: ExportRequest):
    session = SessionLocal()
    try:
        model = session.query(ModelRegistry).filter_by(id=model_id).first()
        if not model:
            raise HTTPException(status_code=404, detail="Model not found")

        task = export_model_task.delay(model_id, req.format)
        return {"task_id": task.id, "format": req.format}
    finally:
        session.close()
