"""Migration sanity tests — verify alembic can upgrade to head without error.

SQLite compatibility note:
  The Waldo schema uses `sqlalchemy.dialects.postgresql.UUID` columns in every
  migration file (e.g. 001_initial_schema.py uses `UUID(as_uuid=True)`).
  SQLite does not understand the PostgreSQL UUID dialect type, so running
  alembic migrations against an in-memory SQLite database is NOT supported.

  These tests therefore require a live PostgreSQL connection.  They are skipped
  automatically when the POSTGRES_HOST env var is absent or when psycopg2
  cannot reach the configured host, so local development (no running Postgres)
  does not break `uv run pytest`.  In CI the Postgres service is always present.
"""

from __future__ import annotations

import os

import pytest
import sqlalchemy
from sqlalchemy import inspect, text


def _postgres_dsn() -> str:
    """Build DSN from env vars, falling back to the lib.config defaults."""
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")
    user = os.environ.get("POSTGRES_USER", "waldo")
    password = os.environ.get("POSTGRES_PASSWORD", "waldo")
    db = os.environ.get("POSTGRES_DB", "waldo")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


def _postgres_available() -> bool:
    """Return True if we can connect to the configured Postgres instance."""
    try:
        import psycopg2  # noqa: F401
    except ImportError:
        return False
    dsn = _postgres_dsn()
    try:
        engine = sqlalchemy.create_engine(dsn, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL not reachable — skipping migration tests (set POSTGRES_HOST to enable)",
)


@requires_postgres
def test_alembic_upgrade_head_succeeds():
    """Running `alembic upgrade head` against the CI Postgres must not raise."""
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    # Override the DB URL to use CI env vars
    alembic_cfg.set_main_option("sqlalchemy.url", _postgres_dsn())
    # Should complete without raising
    command.upgrade(alembic_cfg, "head")


@requires_postgres
def test_expected_tables_exist_after_migration():
    """Core tables must exist after migrating to head."""
    # The upgrade is idempotent — run again to ensure we're at head
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", _postgres_dsn())
    command.upgrade(alembic_cfg, "head")

    dsn = _postgres_dsn()
    engine = sqlalchemy.create_engine(dsn)
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())

        expected_tables = {
            "projects",
            "videos",
            "workspaces",
            "users",
            "workspace_members",
            "api_keys",
        }
        missing = expected_tables - existing_tables
        assert not missing, f"Tables missing after alembic upgrade head: {missing}"
    finally:
        engine.dispose()


@requires_postgres
def test_alembic_history_is_linear():
    """Alembic revision chain must be a single linear history (no branching)."""
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", _postgres_dsn())
    alembic_cfg.set_main_option("script_location", "alembic")

    from alembic.script import ScriptDirectory

    scripts = ScriptDirectory.from_config(alembic_cfg)
    heads = scripts.get_heads()
    assert len(heads) == 1, (
        f"Expected exactly one alembic head revision, found {len(heads)}: {heads}. "
        "This usually means two migrations were created without one revising the other."
    )


@requires_postgres
def test_alembic_current_is_head_after_upgrade():
    """After `upgrade head`, `alembic current` must report the head revision."""
    from alembic import command
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", _postgres_dsn())
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
