"""Phase 3: training runs, model registry

Revision ID: 003
Revises: 002
Create Date: 2026-03-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "training_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("job_id", UUID(as_uuid=True), sa.ForeignKey("labeling_jobs.id"), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("task_type", sa.String(20), nullable=False, server_default="segment"),
        sa.Column("model_variant", sa.String(100), nullable=False),
        sa.Column("hyperparameters", sa.JSON, server_default="{}"),
        sa.Column("status", sa.String(50), server_default="queued"),
        sa.Column("epoch_current", sa.Integer, server_default="0"),
        sa.Column("total_epochs", sa.Integer, server_default="100"),
        sa.Column("metrics", sa.JSON, server_default="{}"),
        sa.Column("best_metrics", sa.JSON, server_default="{}"),
        sa.Column("dataset_minio_key", sa.String(1024)),
        sa.Column("checkpoint_minio_key", sa.String(1024)),
        sa.Column("best_weights_minio_key", sa.String(1024)),
        sa.Column("error_message", sa.Text),
        sa.Column("celery_task_id", sa.String(255)),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime),
        sa.Column("completed_at", sa.DateTime),
    )

    op.create_table(
        "model_registry",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=False),
        sa.Column("training_run_id", UUID(as_uuid=True), sa.ForeignKey("training_runs.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("task_type", sa.String(20), nullable=False),
        sa.Column("model_variant", sa.String(100), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("weights_minio_key", sa.String(1024), nullable=False),
        sa.Column("metrics", sa.JSON, server_default="{}"),
        sa.Column("export_formats", sa.JSON, server_default="{}"),
        sa.Column("is_active", sa.Boolean, server_default="false"),
        sa.Column("created_at", sa.DateTime, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("model_registry")
    op.drop_table("training_runs")
