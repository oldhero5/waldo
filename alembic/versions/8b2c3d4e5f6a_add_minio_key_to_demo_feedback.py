"""add minio_key to demo_feedback

Revision ID: 8b2c3d4e5f6a
Revises: 7a1b2c3d4e5f
Create Date: 2026-04-06

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8b2c3d4e5f6a"
down_revision: str | None = "7a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("demo_feedback", sa.Column("minio_key", sa.String(1024), nullable=True))


def downgrade() -> None:
    op.drop_column("demo_feedback", "minio_key")
