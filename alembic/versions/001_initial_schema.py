"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-03-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "videos",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("filename", sa.String(512), nullable=False),
        sa.Column("minio_key", sa.String(1024), nullable=False),
        sa.Column("fps", sa.Float),
        sa.Column("duration_s", sa.Float),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
        sa.Column("frame_count", sa.Integer),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "frames",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id"), nullable=False),
        sa.Column("frame_number", sa.Integer, nullable=False),
        sa.Column("timestamp_s", sa.Float, nullable=False),
        sa.Column("minio_key", sa.String(1024), nullable=False),
        sa.Column("phash", sa.String(64)),
        sa.Column("width", sa.Integer),
        sa.Column("height", sa.Integer),
    )

    op.create_table(
        "labeling_jobs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("video_id", UUID(as_uuid=True), sa.ForeignKey("videos.id"), nullable=False),
        sa.Column("text_prompt", sa.Text, nullable=False),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("progress", sa.Float, server_default="0"),
        sa.Column("total_frames", sa.Integer, server_default="0"),
        sa.Column("processed_frames", sa.Integer, server_default="0"),
        sa.Column("result_minio_key", sa.String(1024)),
        sa.Column("error_message", sa.Text),
        sa.Column("celery_task_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime, server_default=sa.func.now()),
    )

    op.create_table(
        "annotations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("frame_id", UUID(as_uuid=True), sa.ForeignKey("frames.id"), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("labeling_jobs.id"), nullable=False),
        sa.Column("class_name", sa.String(255), nullable=False),
        sa.Column("class_index", sa.Integer, nullable=False),
        sa.Column("polygon", sa.JSON, nullable=False),
        sa.Column("confidence", sa.Float),
    )


def downgrade() -> None:
    op.drop_table("annotations")
    op.drop_table("labeling_jobs")
    op.drop_table("frames")
    op.drop_table("videos")
    op.drop_table("projects")
