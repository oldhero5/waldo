"""Waldo tools for the LangGraph agent — maps natural language to API actions."""
from langchain_core.tools import tool

from lib.db import (
    Annotation,
    LabelingJob,
    ModelRegistry,
    Project,
    SessionLocal,
    TrainingRun,
    Video,
)


@tool
def list_projects() -> str:
    """List all projects/collections in the workspace with video counts."""
    session = SessionLocal()
    try:
        projects = session.query(Project).all()
        if not projects:
            return "No projects found. Upload videos first at /upload."
        lines = []
        for p in projects:
            vid_count = session.query(Video).filter_by(project_id=p.id).count()
            lines.append(f"- {p.name}: {vid_count} videos")
        return f"Projects ({len(projects)}):\n" + "\n".join(lines)
    finally:
        session.close()


@tool
def list_datasets() -> str:
    """List all completed labeling jobs (datasets) with annotation counts."""
    session = SessionLocal()
    try:
        jobs = session.query(LabelingJob).filter_by(status="completed").order_by(LabelingJob.created_at.desc()).limit(10).all()
        if not jobs:
            return "No datasets found. Label a video first."
        lines = []
        for j in jobs:
            ann_count = session.query(Annotation).filter_by(job_id=j.id).count()
            lines.append(f"- \"{j.text_prompt}\": {ann_count} annotations, {j.total_frames} frames")
        return f"Datasets ({len(jobs)}):\n" + "\n".join(lines)
    finally:
        session.close()


@tool
def list_models() -> str:
    """List all trained models with their metrics."""
    session = SessionLocal()
    try:
        models = session.query(ModelRegistry).order_by(ModelRegistry.created_at.desc()).limit(10).all()
        if not models:
            return "No trained models. Train a model first from a dataset."
        lines = []
        for m in models:
            mAP = m.metrics.get("metrics/mAP50(B)", m.metrics.get("metrics/mAP50(M)", "?"))
            active = " (ACTIVE)" if m.is_active else ""
            lines.append(f"- {m.name} ({m.model_variant}): mAP50={mAP}{active}")
        return f"Models ({len(models)}):\n" + "\n".join(lines)
    finally:
        session.close()


@tool
def list_experiments() -> str:
    """List recent training experiments with their status and metrics."""
    session = SessionLocal()
    try:
        runs = session.query(TrainingRun).order_by(TrainingRun.created_at.desc()).limit(5).all()
        if not runs:
            return "No training experiments yet."
        lines = []
        for r in runs:
            mAP = r.best_metrics.get("metrics/mAP50(B)", "?") if r.best_metrics else "?"
            lines.append(f"- {r.name}: {r.status}, epoch {r.epoch_current}/{r.total_epochs}, mAP50={mAP}")
        return "Recent experiments:\n" + "\n".join(lines)
    finally:
        session.close()


@tool
def get_training_tips(dataset_size: int, task_type: str = "segment") -> str:
    """Get recommended training hyperparameters based on dataset size and task type.

    Args:
        dataset_size: Number of annotated frames in the dataset.
        task_type: One of 'segment', 'detect', 'classify'.
    """
    if dataset_size < 50:
        return (
            f"With only {dataset_size} frames, training will be limited.\n"
            "Recommendations:\n"
            "- Use 'Standard' augmentation to maximize variety\n"
            "- Model: yolo26n-seg (nano, fastest, won't overfit as easily)\n"
            "- Epochs: 50-100 with patience=5\n"
            "- Batch size: 4-8\n"
            "- Consider collecting more data — aim for 200+ frames per class"
        )
    elif dataset_size < 500:
        return (
            f"Good dataset size ({dataset_size} frames).\n"
            "Recommendations:\n"
            "- Use 'Standard' augmentation\n"
            "- Model: yolo26s-seg (small, good balance)\n"
            "- Epochs: 100 with patience=10\n"
            "- Batch size: 8\n"
            "- Image size: 640 (or 1280 if objects are small)"
        )
    else:
        return (
            f"Large dataset ({dataset_size} frames) — great!\n"
            "Recommendations:\n"
            "- Use 'Aggressive' augmentation for maximum robustness\n"
            "- Model: yolo26m-seg or yolo26l-seg (medium/large)\n"
            "- Epochs: 100-300 with patience=20\n"
            "- Batch size: 8-16\n"
            "- Consider fine-tuning from a previous checkpoint"
        )


WALDO_TOOLS = [list_projects, list_datasets, list_models, list_experiments, get_training_tips]
