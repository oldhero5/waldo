"""add minio_key to demo_feedback

Revision ID: 8b2c3d4e5f6a
Revises: 7a1b2c3d4e5f
Create Date: 2026-04-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op
from lib.migration_helpers import has_column, has_table

revision: str = "8b2c3d4e5f6a"
down_revision: str | None = "7a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # demo_feedback was historically created via Base.metadata.create_all() in
    # an earlier dev workflow, so this migration originally only added the new
    # column. On a fresh database the table doesn't exist yet — create it here
    # with the full current schema so `alembic upgrade head` works end-to-end.
    if not has_table("demo_feedback"):
        op.create_table(
            "demo_feedback",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column(
                "model_id", UUID(as_uuid=True), sa.ForeignKey("model_registry.id", ondelete="SET NULL"), nullable=True
            ),
            sa.Column("class_name", sa.String(255), nullable=False),
            sa.Column("bbox", sa.JSON(), nullable=False),
            sa.Column("polygon", sa.JSON(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("track_id", sa.Integer(), nullable=True),
            sa.Column("frame_index", sa.Integer(), nullable=True),
            sa.Column("timestamp_s", sa.Float(), nullable=True),
            sa.Column("feedback_type", sa.String(30), server_default="false_positive"),
            sa.Column("corrected_class", sa.String(255), nullable=True),
            sa.Column("source_filename", sa.String(512), nullable=True),
            sa.Column("minio_key", sa.String(1024), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )
        return

    if not has_column("demo_feedback", "minio_key"):
        op.add_column("demo_feedback", sa.Column("minio_key", sa.String(1024), nullable=True))


def downgrade() -> None:
    if has_column("demo_feedback", "minio_key"):
        op.drop_column("demo_feedback", "minio_key")
