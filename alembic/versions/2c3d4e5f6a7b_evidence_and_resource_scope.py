"""Retain observation identity/coverage and explicit resource ownership.

Legacy resource ownership stays unknown. Operators must assign it explicitly;
request handlers must not make unowned resources visible to every workspace.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "2c3d4e5f6a7b"
down_revision = "1b2c3d4e5f6a"
branch_labels = None
depends_on = None

SCOPED_TABLES = ("edge_devices", "comparison_runs", "deployment_targets", "inference_logs", "demo_feedback")


def upgrade() -> None:
    op.add_column("annotations", sa.Column("track_id", sa.Integer(), nullable=True))
    op.add_column("labeling_jobs", sa.Column("processing_summary", sa.JSON(), nullable=True))
    for table in SCOPED_TABLES:
        op.add_column(table, sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=True))
        op.create_foreign_key(f"fk_{table}_workspace_id", table, "workspaces", ["workspace_id"], ["id"])
        op.create_index(f"ix_{table}_workspace_id", table, ["workspace_id"])
    op.create_index("ix_saved_workflows_workspace_id", "saved_workflows", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("ix_saved_workflows_workspace_id", table_name="saved_workflows")
    for table in reversed(SCOPED_TABLES):
        op.drop_index(f"ix_{table}_workspace_id", table_name=table)
        op.drop_constraint(f"fk_{table}_workspace_id", table, type_="foreignkey")
        op.drop_column(table, "workspace_id")
    op.drop_column("labeling_jobs", "processing_summary")
    op.drop_column("annotations", "track_id")
