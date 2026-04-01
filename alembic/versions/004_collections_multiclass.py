"""Collections & multiclass support

Revision ID: 004
Revises: 003
Create Date: 2026-03-16

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Labeling jobs: collection + multiclass support
    op.add_column("labeling_jobs", sa.Column("class_prompts", sa.JSON, nullable=True))
    op.add_column(
        "labeling_jobs",
        sa.Column("project_id", UUID(as_uuid=True), sa.ForeignKey("projects.id"), nullable=True),
    )
    op.alter_column("labeling_jobs", "video_id", existing_type=UUID(as_uuid=True), nullable=True)

    # Model registry: store class names
    op.add_column("model_registry", sa.Column("class_names", sa.JSON, nullable=True))


def downgrade() -> None:
    op.drop_column("model_registry", "class_names")
    op.alter_column("labeling_jobs", "video_id", existing_type=UUID(as_uuid=True), nullable=False)
    op.drop_column("labeling_jobs", "project_id")
    op.drop_column("labeling_jobs", "class_prompts")
