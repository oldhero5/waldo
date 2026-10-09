"""Retain the requested detector threshold and sampling rate on each job."""

import sqlalchemy as sa

from alembic import op

revision = "1b2c3d4e5f6a"
down_revision = "0a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("labeling_jobs", sa.Column("score_threshold", sa.Float(), nullable=True))
    op.add_column("labeling_jobs", sa.Column("sample_fps", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("labeling_jobs", "sample_fps")
    op.drop_column("labeling_jobs", "score_threshold")
