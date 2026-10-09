"""Add the deployment target slug already required by the serving model.

Older installations may have created this column through ORM create_all.
"""

import sqlalchemy as sa

from alembic import context, op

revision = "3d4e5f6a7b8c"
down_revision = "2c3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        op.add_column("deployment_targets", sa.Column("slug", sa.String(100), nullable=True))
        op.create_unique_constraint("uq_deployment_targets_slug", "deployment_targets", ["slug"])
        return
    inspector = sa.inspect(op.get_bind())
    if "slug" not in {c["name"] for c in inspector.get_columns("deployment_targets")}:
        op.add_column("deployment_targets", sa.Column("slug", sa.String(100), nullable=True))
    if not any(c["column_names"] == ["slug"] for c in inspector.get_unique_constraints("deployment_targets")):
        op.create_unique_constraint("uq_deployment_targets_slug", "deployment_targets", ["slug"])


def downgrade() -> None:
    op.drop_column("deployment_targets", "slug")
