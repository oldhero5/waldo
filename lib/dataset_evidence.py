"""Frame counts derive from a labeling run, never all frames in its project."""

from sqlalchemy import update

from lib.db import LabelingJob


def invalidate_current_export(session, job_id) -> int:
    """Invalidate a job's current dataset in the evidence-change transaction.

    A forced SQL update matters even when the current key is already NULL:
    concurrent exports must see that evidence changed while they were building.
    """
    statement = (
        update(LabelingJob)
        .where(LabelingJob.id == job_id)
        .values(
            evidence_revision=LabelingJob.evidence_revision + 1,
            result_minio_key=None,
        )
        .returning(LabelingJob.evidence_revision)
        .execution_options(synchronize_session="fetch")
    )
    revision = session.execute(statement).scalar_one()
    return revision


def publish_current_export(session, job_id, expected_revision: int, object_key: str) -> bool:
    """Publish an uploaded snapshot only if its source evidence is unchanged."""
    statement = (
        update(LabelingJob)
        .where(LabelingJob.id == job_id, LabelingJob.evidence_revision == expected_revision)
        .values(result_minio_key=object_key)
        .returning(LabelingJob.id)
        .execution_options(synchronize_session="fetch")
    )
    return session.execute(statement).scalar_one_or_none() is not None


def assessed_frame_count(job, annotated_frames: int) -> int:
    summary = job.processing_summary or {}
    videos = summary.get("videos")
    if isinstance(videos, list):
        assessed = sum(
            entry.get("sampled_frames", 0)
            for entry in videos
            if isinstance(entry, dict) and isinstance(entry.get("sampled_frames"), int) and entry["sampled_frames"] >= 0
        )
    else:
        assessed = job.total_frames or 0
    return max(assessed, annotated_frames)
