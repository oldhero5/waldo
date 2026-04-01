from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from lib.db import (
    LabelingJob,
    ModelRegistry,
    SessionLocal,
    TrainingRun,
)
from lib.storage import get_download_url
from lib.tasks import export_model_task, train_model
from trainer.train_manager import AUGMENTATION_PRESETS, DEFAULT_HYPERPARAMS, TASK_TO_DEFAULT_VARIANT, VARIANTS

router = APIRouter()


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
    loss_history: list = []
    metric_history: list = []
    weights_url: str | None = None
    error_message: str | None = None
    celery_task_id: str | None = None


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


class ExportRequest(BaseModel):
    format: str


class VariantsResponse(BaseModel):
    variants: dict[str, str]
    defaults: dict[str, str]
    hyperparams: dict


@router.get("/train/variants", response_model=VariantsResponse)
def get_variants():
    return VariantsResponse(
        variants=VARIANTS,
        defaults=TASK_TO_DEFAULT_VARIANT,
        hyperparams=DEFAULT_HYPERPARAMS,
        augmentation_presets={k: list(v.keys()) for k, v in AUGMENTATION_PRESETS.items()},  # type: ignore[arg-type]
    )


@router.post("/train", status_code=202, response_model=TrainResponse)
def start_training(req: TrainRequest):
    session = SessionLocal()
    try:
        job = session.query(LabelingJob).filter_by(id=req.job_id).first()
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
            loss_history=run.loss_history or [],
            metric_history=run.metric_history or [],
            weights_url=weights_url,
            error_message=run.error_message,
            celery_task_id=run.celery_task_id,
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
            results.append(TrainingRunStatus(
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
            ))
        return results
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
