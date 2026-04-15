"""add deployment_targets and inference_logs tables

Revision ID: a1b2c3d4e5f6
Revises: 9c3d4e5f6a7b
Create Date: 2026-04-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op
from lib.migration_helpers import has_index, has_table

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = "9c3d4e5f6a7b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not has_table("deployment_targets"):
        op.create_table(
            "deployment_targets",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("location_label", sa.String(255), nullable=True),
            sa.Column("target_type", sa.String(20), server_default="camera"),
            sa.Column(
                "model_id", UUID(as_uuid=True), sa.ForeignKey("model_registry.id", ondelete="SET NULL"), nullable=True
            ),
            sa.Column("config", sa.JSON(), server_default="{}"),
            sa.Column("is_active", sa.Boolean(), server_default="true"),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )

    if not has_table("inference_logs"):
        op.create_table(
            "inference_logs",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column(
                "model_id", UUID(as_uuid=True), sa.ForeignKey("model_registry.id", ondelete="SET NULL"), nullable=True
            ),
            sa.Column(
                "target_id",
                UUID(as_uuid=True),
                sa.ForeignKey("deployment_targets.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("request_type", sa.String(10), nullable=False),
            sa.Column("latency_ms", sa.Float(), nullable=False),
            sa.Column("detection_count", sa.Integer(), server_default="0"),
            sa.Column("avg_confidence", sa.Float(), nullable=True),
            sa.Column("classes_detected", sa.JSON(), server_default="[]"),
            sa.Column("input_resolution", sa.String(20), nullable=True),
            sa.Column("error_code", sa.String(50), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )

    if not has_index("ix_inference_logs_created_at"):
        op.create_index("ix_inference_logs_created_at", "inference_logs", ["created_at"])
    if not has_index("ix_inference_logs_model_id"):
        op.create_index("ix_inference_logs_model_id", "inference_logs", ["model_id"])
    if not has_index("ix_inference_logs_target_id"):
        op.create_index("ix_inference_logs_target_id", "inference_logs", ["target_id"])


def downgrade() -> None:
    op.drop_index("ix_inference_logs_target_id", "inference_logs")
    op.drop_index("ix_inference_logs_model_id", "inference_logs")
    op.drop_index("ix_inference_logs_created_at", "inference_logs")
    op.drop_table("inference_logs")
    op.drop_table("deployment_targets")
