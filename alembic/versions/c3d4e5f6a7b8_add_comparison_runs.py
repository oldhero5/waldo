"""add comparison_runs table

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op
from lib.migration_helpers import has_index, has_table

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not has_table("comparison_runs"):
        op.create_table(
            "comparison_runs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("file_name", sa.String(512), nullable=False),
            sa.Column("file_minio_key", sa.String(1024), nullable=True),
            sa.Column("is_video", sa.Boolean(), server_default="false"),
            sa.Column("sam_prompts", sa.JSON(), nullable=True),
            sa.Column("confidence_threshold", sa.Float(), server_default="0.25"),
            sa.Column("model_a_id", sa.String(100), nullable=True),
            sa.Column("model_a_name", sa.String(255), nullable=False),
            sa.Column("model_a_detections", sa.Integer(), server_default="0"),
            sa.Column("model_a_avg_confidence", sa.Float(), nullable=True),
            sa.Column("model_a_latency_ms", sa.Float(), server_default="0"),
            sa.Column("model_b_id", sa.String(100), nullable=True),
            sa.Column("model_b_name", sa.String(255), nullable=False),
            sa.Column("model_b_detections", sa.Integer(), server_default="0"),
            sa.Column("model_b_avg_confidence", sa.Float(), nullable=True),
            sa.Column("model_b_latency_ms", sa.Float(), server_default="0"),
            sa.Column("results_minio_key", sa.String(1024), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
    if not has_index("ix_comparison_runs_created_at"):
        op.create_index("ix_comparison_runs_created_at", "comparison_runs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_comparison_runs_created_at", "comparison_runs")
    op.drop_table("comparison_runs")
