"""Phase 2: annotation review, exemplar mode, task types

Revision ID: 002
Revises: 001
Create Date: 2026-03-15

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Annotation review status and bbox
    op.add_column("annotations", sa.Column("status", sa.String(20), server_default="pending"))
    op.add_column("annotations", sa.Column("bbox", sa.JSON, nullable=True))

    # Labeling job: prompt type, point prompts, task type
    op.add_column("labeling_jobs", sa.Column("prompt_type", sa.String(20), server_default="text"))
    op.add_column("labeling_jobs", sa.Column("point_prompts", sa.JSON, nullable=True))
    op.add_column("labeling_jobs", sa.Column("task_type", sa.String(20), server_default="segment"))

    # Make text_prompt nullable for exemplar jobs
    op.alter_column("labeling_jobs", "text_prompt", existing_type=sa.Text, nullable=True)


def downgrade() -> None:
    op.alter_column("labeling_jobs", "text_prompt", existing_type=sa.Text, nullable=False)
    op.drop_column("labeling_jobs", "task_type")
    op.drop_column("labeling_jobs", "point_prompts")
    op.drop_column("labeling_jobs", "prompt_type")
    op.drop_column("annotations", "bbox")
    op.drop_column("annotations", "status")
