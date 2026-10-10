"""Separate installation administration from workspace membership.

Existing users default to unprivileged, regardless of their email or workspace
role. An installation operator must explicitly grant the privilege using
scripts/reset_admin.py after upgrading an existing installation.
"""

import sqlalchemy as sa

from alembic import op

revision = "0a1b2c3d4e5f"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("is_platform_admin", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("users", "is_platform_admin")
