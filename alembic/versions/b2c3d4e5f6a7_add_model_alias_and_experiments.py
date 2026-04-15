"""add model alias column and deployment experiments table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-04-06

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op
from lib.migration_helpers import has_column, has_table

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add alias column to model_registry (champion, challenger, staging, or null)
    if not has_column("model_registry", "alias"):
        op.add_column("model_registry", sa.Column("alias", sa.String(30), nullable=True))
        # Backfill: set alias='champion' for any model currently marked is_active=True
        op.execute("UPDATE model_registry SET alias = 'champion' WHERE is_active = true")

    # Blue-green / canary experiment table
    if not has_table("deployment_experiments"):
        op.create_table(
            "deployment_experiments",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("champion_model_id", UUID(as_uuid=True), sa.ForeignKey("model_registry.id"), nullable=False),
            sa.Column("challenger_model_id", UUID(as_uuid=True), sa.ForeignKey("model_registry.id"), nullable=False),
            sa.Column("split_pct", sa.Integer(), server_default="20", nullable=False),
            sa.Column("status", sa.String(20), server_default="running"),
            sa.Column(
                "target_id",
                UUID(as_uuid=True),
                sa.ForeignKey("deployment_targets.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("started_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.Column("winner", sa.String(20), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )

    # Edge device registry
    if not has_table("edge_devices"):
        op.create_table(
            "edge_devices",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("device_type", sa.String(50), nullable=False),
            sa.Column("location_label", sa.String(255), nullable=True),
            sa.Column(
                "target_id",
                UUID(as_uuid=True),
                sa.ForeignKey("deployment_targets.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "model_id", UUID(as_uuid=True), sa.ForeignKey("model_registry.id", ondelete="SET NULL"), nullable=True
            ),
            sa.Column("model_version", sa.Integer(), nullable=True),
            sa.Column("hardware_info", sa.JSON(), server_default="{}"),
            sa.Column("status", sa.String(20), server_default="offline"),
            sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
            sa.Column("last_sync", sa.DateTime(), nullable=True),
            sa.Column("ip_address", sa.String(45), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )


def downgrade() -> None:
    op.drop_table("edge_devices")
    op.drop_table("deployment_experiments")
    op.drop_column("model_registry", "alias")
