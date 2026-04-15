"""add name column to labeling_jobs

Revision ID: 6898fd9aa48c
Revises: 004
Create Date: 2026-04-06 09:44:15.641486

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6898fd9aa48c"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("labeling_jobs", sa.Column("name", sa.String(255), nullable=True))


def downgrade() -> None:
    op.drop_column("labeling_jobs", "name")
