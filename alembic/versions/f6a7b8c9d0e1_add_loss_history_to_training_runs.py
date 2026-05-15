"""add loss_history and metric_history to training_runs

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-15

The TrainingRun ORM model in lib.db has carried `loss_history` and
`metric_history` JSON columns since training metric streaming was added,
but they were never reflected in an alembic migration — so a fresh
`alembic upgrade head` left the table missing those columns and any query
that selects them raised UndefinedColumn. This migration backfills the
schema. is-idempotent on already-migrated DBs.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from lib.migration_helpers import has_column

revision: str = "f6a7b8c9d0e1"  # pragma: allowlist secret
down_revision: str | None = "e5f6a7b8c9d0"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not has_column("training_runs", "loss_history"):
        op.add_column(
            "training_runs",
            sa.Column("loss_history", sa.JSON(), server_default="[]", nullable=True),
        )
    if not has_column("training_runs", "metric_history"):
        op.add_column(
            "training_runs",
            sa.Column("metric_history", sa.JSON(), server_default="[]", nullable=True),
        )


def downgrade() -> None:
    op.drop_column("training_runs", "metric_history")
    op.drop_column("training_runs", "loss_history")
