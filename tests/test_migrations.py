"""Migration checks against an explicitly selected disposable PostgreSQL database.

Set WALDO_TEST_POSTGRES_URL. No default application database is inspected or
migrated. Once configured, an unavailable service fails the check instead of
silently skipping it. The revision-graph check runs without a database.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from alembic import command


def _postgres_dsn() -> str:
    return os.environ["WALDO_TEST_POSTGRES_URL"]


requires_postgres = pytest.mark.skipif(
    not os.environ.get("WALDO_TEST_POSTGRES_URL"),
    reason="Set WALDO_TEST_POSTGRES_URL to a disposable database to run migrations",
)


@requires_postgres
def test_alembic_upgrade_head_succeeds():
    """Running `alembic upgrade head` against the CI Postgres must not raise."""
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.attributes["database_url"] = _postgres_dsn()
    # Should complete without raising
    command.upgrade(alembic_cfg, "head")


@requires_postgres
def test_expected_tables_exist_after_migration():
    """Core tables must exist after migrating to head."""
    # The upgrade is idempotent — run again to ensure we're at head
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.attributes["database_url"] = _postgres_dsn()
    command.upgrade(alembic_cfg, "head")

    dsn = _postgres_dsn()
    engine = sqlalchemy.create_engine(dsn)
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())

        # Every ORM model in lib.db must have a corresponding table; otherwise
        # a fresh deployment crashes when a code path hits the missing table
        # (e.g. bootstrap_admin_if_empty querying `users`).
        from lib.db import Base

        expected_tables = set(Base.metadata.tables.keys())
        missing = expected_tables - existing_tables
        assert not missing, f"Tables missing after alembic upgrade head: {missing}"
    finally:
        engine.dispose()


def test_alembic_history_is_linear():
    """Alembic revision chain must be a single linear history (no branching)."""
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("script_location", "alembic")

    scripts = ScriptDirectory.from_config(alembic_cfg)
    heads = scripts.get_heads()
    assert len(heads) == 1, (
        f"Expected exactly one alembic head revision, found {len(heads)}: {heads}. "
        "This usually means two migrations were created without one revising the other."
    )


@requires_postgres
def test_alembic_current_is_head_after_upgrade():
    """After `upgrade head`, `alembic current` must report the head revision."""
    alembic_cfg = Config("alembic.ini")
    alembic_cfg.attributes["database_url"] = _postgres_dsn()
    command.upgrade(alembic_cfg, "head")

    scripts = ScriptDirectory.from_config(alembic_cfg)
    head_rev = scripts.get_current_head()

    dsn = _postgres_dsn()
    engine = sqlalchemy.create_engine(dsn)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            current_heads = ctx.get_current_heads()
        assert head_rev in current_heads, f"DB is at {current_heads} but expected head revision {head_rev}"
    finally:
        engine.dispose()
