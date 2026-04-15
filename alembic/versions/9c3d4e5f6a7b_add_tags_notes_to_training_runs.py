"""add tags and notes to training_runs

Revision ID: 9c3d4e5f6a7b
Revises: 8b2c3d4e5f6a
Create Date: 2026-04-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "9c3d4e5f6a7b"
down_revision: str | None = "8b2c3d4e5f6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("training_runs", sa.Column("tags", sa.JSON(), server_default="[]", nullable=True))
    op.add_column("training_runs", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("training_runs", "notes")
    op.drop_column("training_runs", "tags")
