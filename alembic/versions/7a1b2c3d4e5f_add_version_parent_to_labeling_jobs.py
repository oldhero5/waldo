"""add version and parent_id to labeling_jobs

Revision ID: 7a1b2c3d4e5f
Revises: 6898fd9aa48c
Create Date: 2026-04-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op

revision: str = "7a1b2c3d4e5f"
down_revision: str | None = "6898fd9aa48c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("labeling_jobs", sa.Column("version", sa.Integer(), server_default="1", nullable=True))
    op.add_column("labeling_jobs", sa.Column("parent_id", UUID(as_uuid=True), nullable=True))
    op.create_foreign_key("fk_labeling_job_parent", "labeling_jobs", "labeling_jobs", ["parent_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_labeling_job_parent", "labeling_jobs", type_="foreignkey")
    op.drop_column("labeling_jobs", "parent_id")
    op.drop_column("labeling_jobs", "version")
