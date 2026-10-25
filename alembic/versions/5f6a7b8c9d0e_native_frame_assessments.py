"""Retain original native sample assessments, including empty samples.

Revision ID: 5f6a7b8c9d0e
Revises: 4e5f6a7b8c9d
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "5f6a7b8c9d0e"
down_revision = "4e5f6a7b8c9d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "native_frame_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("labeling_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "video_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("videos.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_frame_index", sa.Integer(), nullable=False),
        sa.Column("timestamp_s", sa.Float(53), nullable=False),
        sa.Column("timestamp_method", sa.String(32), nullable=False),
        sa.Column("frame_duration_s", sa.Float(53), nullable=True),
        sa.Column("source_width", sa.Integer(), nullable=False),
        sa.Column("source_height", sa.Integer(), nullable=False),
        sa.Column("detection_count", sa.Integer(), nullable=False),
        sa.UniqueConstraint("job_id", "video_id", "source_frame_index", name="uq_native_assessment_source"),
        sa.CheckConstraint("source_frame_index >= 0", name="ck_native_assessment_index"),
        sa.CheckConstraint(
            "timestamp_s >= -1.7976931348623157e308 AND timestamp_s <= 1.7976931348623157e308",
            name="ck_native_assessment_time",
        ),
        sa.CheckConstraint("timestamp_method IN ('source_pts', 'frame_index/fps')", name="ck_native_assessment_method"),
        sa.CheckConstraint(
            "frame_duration_s IS NULL OR (frame_duration_s > 0 AND frame_duration_s <= 1.7976931348623157e308)",
            name="ck_native_assessment_duration",
        ),
        sa.CheckConstraint("source_width > 0 AND source_height > 0", name="ck_native_assessment_dimensions"),
        sa.CheckConstraint("detection_count >= 0", name="ck_native_assessment_count"),
    )
    op.create_index("ix_native_frame_assessments_video_id", "native_frame_assessments", ["video_id"])


def downgrade() -> None:
    raise RuntimeError("Native assessments retain evidence: revert application code only; retain this schema revision")
