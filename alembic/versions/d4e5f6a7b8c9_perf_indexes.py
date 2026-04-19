"""perf indexes — composite indexes for hot query paths

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-04-19

"""

from collections.abc import Sequence

from alembic import op
from lib.migration_helpers import has_index

revision: str = "d4e5f6a7b8c9"  # pragma: allowlist secret
down_revision: str | None = "c3d4e5f6a7b8"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # labeling_jobs: filter by status + project_id (list_jobs, dataset overview)
    if not has_index("ix_labeling_jobs_status_project"):
        op.create_index(
            "ix_labeling_jobs_status_project",
            "labeling_jobs",
            ["status", "project_id"],
        )

    # annotations: filter by job_id + frame_id (review list, overview per-frame)
    if not has_index("ix_annotations_job_frame"):
        op.create_index(
            "ix_annotations_job_frame",
            "annotations",
            ["job_id", "frame_id"],
        )

    # frames: order / filter by video_id + frame_number (list_frames pagination)
    if not has_index("ix_frames_video_frame_number"):
        op.create_index(
            "ix_frames_video_frame_number",
            "frames",
            ["video_id", "frame_number"],
        )

    # training_runs: filter by status + project_id (dashboard, monitoring)
    if not has_index("ix_training_runs_status_project"):
        op.create_index(
            "ix_training_runs_status_project",
            "training_runs",
            ["status", "project_id"],
        )

    # inference_logs: analytics queries scoped to a model over time
    if not has_index("ix_inference_logs_model_created"):
        op.create_index(
            "ix_inference_logs_model_created",
            "inference_logs",
            ["model_id", "created_at"],
        )

    # videos: list videos per project ordered by created_at
    if not has_index("ix_videos_project_created"):
        op.create_index(
            "ix_videos_project_created",
            "videos",
            ["project_id", "created_at"],
        )


def downgrade() -> None:
    op.drop_index("ix_videos_project_created", "videos")
    op.drop_index("ix_inference_logs_model_created", "inference_logs")
    op.drop_index("ix_training_runs_status_project", "training_runs")
    op.drop_index("ix_frames_video_frame_number", "frames")
    op.drop_index("ix_annotations_job_frame", "annotations")
    op.drop_index("ix_labeling_jobs_status_project", "labeling_jobs")
