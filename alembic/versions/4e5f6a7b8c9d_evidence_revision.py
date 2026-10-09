"""Track evidence changes independently of exported dataset artifact keys.

Revision ID: 4e5f6a7b8c9d
Revises: 3d4e5f6a7b8c
"""

import sqlalchemy as sa

from alembic import op

revision = "4e5f6a7b8c9d"
down_revision = "3d4e5f6a7b8c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("labeling_jobs", sa.Column("evidence_revision", sa.BigInteger(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("labeling_jobs", "evidence_revision")
