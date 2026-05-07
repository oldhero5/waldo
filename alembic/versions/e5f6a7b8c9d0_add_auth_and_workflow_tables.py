"""add auth (workspaces, users, workspace_members, api_keys) and saved_workflows tables

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-07

These tables ship as ORM models in lib.db but were never added to the alembic
chain — `auth.bootstrap_admin_if_empty()` runs at app startup and crashes a
fresh deployment because `users`/`workspaces` don't exist. This migration
backfills them so a clean `alembic upgrade head` produces the full schema.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from alembic import op
from lib.migration_helpers import has_table

revision: str = "e5f6a7b8c9d0"  # pragma: allowlist secret
down_revision: str | None = "d4e5f6a7b8c9"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not has_table("workspaces"):
        op.create_table(
            "workspaces",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("slug", sa.String(100), nullable=False, unique=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )

    if not has_table("users"):
        op.create_table(
            "users",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("email", sa.String(255), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(255), nullable=False),
            sa.Column("display_name", sa.String(255), nullable=False),
            sa.Column("avatar_url", sa.String(1024), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("last_login", sa.DateTime(), nullable=True),
        )

    if not has_table("workspace_members"):
        op.create_table(
            "workspace_members",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column(
                "workspace_id",
                UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(20), nullable=False, server_default="annotator"),
            sa.Column("joined_at", sa.DateTime(), server_default=sa.func.now()),
        )

    if not has_table("api_keys"):
        op.create_table(
            "api_keys",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column(
                "workspace_id",
                UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "user_id",
                UUID(as_uuid=True),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("key_hash", sa.String(255), nullable=False),
            sa.Column("key_prefix", sa.String(8), nullable=False),
            sa.Column("scopes", sa.JSON(), server_default="[]"),
            sa.Column("last_used", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        )

    if not has_table("saved_workflows"):
        op.create_table(
            "saved_workflows",
            sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("slug", sa.String(255), nullable=False, unique=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("graph", sa.JSON(), nullable=False),
            sa.Column("is_deployed", sa.Boolean(), server_default="false"),
            sa.Column(
                "workspace_id",
                UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
        )

    # projects.workspace_id was added to the model later but never to the chain.
    # Backfill the column on existing databases. Stays nullable so existing rows
    # don't violate NOT NULL — bootstrap_admin_if_empty() repoints them.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("projects") and not any(c["name"] == "workspace_id" for c in insp.get_columns("projects")):
        op.add_column(
            "projects",
            sa.Column(
                "workspace_id",
                UUID(as_uuid=True),
                sa.ForeignKey("workspaces.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if insp.has_table("projects") and any(c["name"] == "workspace_id" for c in insp.get_columns("projects")):
        op.drop_column("projects", "workspace_id")
    op.drop_table("saved_workflows")
    op.drop_table("api_keys")
    op.drop_table("workspace_members")
    op.drop_table("users")
    op.drop_table("workspaces")
