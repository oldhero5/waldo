"""Migration checks against an explicitly selected disposable PostgreSQL database.

Set WALDO_TEST_POSTGRES_URL. No default application database is inspected or
migrated. Once configured, an unavailable service fails the check instead of
silently skipping it. The revision-graph check runs without a database.
"""

from __future__ import annotations

import os
import uuid

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


def test_evidence_revision_schema_uses_nonnull_bigint_and_database_default():
    from lib.db import LabelingJob

    column = LabelingJob.__table__.c.evidence_revision
    assert isinstance(column.type, sqlalchemy.BigInteger)
    assert column.nullable is False
    assert str(column.server_default.arg) == "0"
    assert not column.index


@requires_postgres
def test_evidence_revision_upgrade_preserves_populated_artifacts_and_database_defaults():
    """Migrate only a newly created private schema; leave other schemas at head."""
    schema = f"waldo_migration_{uuid.uuid4().hex}"
    engine = sqlalchemy.create_engine(_postgres_dsn())
    url = sqlalchemy.engine.make_url(_postgres_dsn()).update_query_dict({"options": f"-csearch_path={schema}"})
    scoped = sqlalchemy.create_engine(url)
    cfg = Config("alembic.ini")
    # ConfigParser requires literal percent escapes in SQLAlchemy URLs.
    cfg.attributes["database_url"] = url.render_as_string(hide_password=False).replace("%", "%%")
    old_job, new_job, project, training = [str(uuid.uuid4()) for _ in range(4)]
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        command.upgrade(cfg, "3d4e5f6a7b8c")
        with scoped.begin() as connection:
            connection.execute(
                sqlalchemy.text(
                    "INSERT INTO labeling_jobs(id, text_prompt, result_minio_key) VALUES (:id, 'camera', :key)"
                ),
                {"id": old_job, "key": "datasets/immutable/reviewed.zip"},
            )
            connection.execute(
                sqlalchemy.text("INSERT INTO projects(id, name) VALUES (:id, 'migration fixture')"), {"id": project}
            )
            connection.execute(
                sqlalchemy.text(
                    "INSERT INTO training_runs(id, project_id, name, task_type, model_variant, dataset_minio_key) "
                    "VALUES (:id, :project, 'migration fixture', 'detect', 'yolo26n', :key)"
                ),
                {"id": training, "project": project, "key": "datasets/immutable/training-snapshot.zip"},
            )
        command.upgrade(cfg, "head")
        with scoped.begin() as connection:
            revision = next(
                c for c in inspect(connection).get_columns("labeling_jobs") if c["name"] == "evidence_revision"
            )
            assert isinstance(revision["type"], sqlalchemy.BigInteger) and revision["nullable"] is False
            assert revision["default"] is not None
            assert (
                connection.scalar(
                    sqlalchemy.text("SELECT evidence_revision FROM labeling_jobs WHERE id = :id"), {"id": old_job}
                )
                == 0
            )
            connection.execute(
                sqlalchemy.text("INSERT INTO labeling_jobs(id, text_prompt) VALUES (:id, 'camera')"), {"id": new_job}
            )
            assert (
                connection.scalar(
                    sqlalchemy.text("SELECT evidence_revision FROM labeling_jobs WHERE id = :id"), {"id": new_job}
                )
                == 0
            )
            connection.execute(
                sqlalchemy.text("UPDATE labeling_jobs SET evidence_revision = 2147483648 WHERE id = :id"),
                {"id": old_job},
            )
            assert (
                connection.scalar(
                    sqlalchemy.text(
                        "UPDATE labeling_jobs SET evidence_revision = evidence_revision + 1 "
                        "WHERE id = :id RETURNING evidence_revision"
                    ),
                    {"id": old_job},
                )
                == 2147483649
            )
            with pytest.raises(sqlalchemy.exc.IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        sqlalchemy.text("UPDATE labeling_jobs SET evidence_revision = NULL WHERE id = :id"),
                        {"id": old_job},
                    )
        command.downgrade(cfg, "3d4e5f6a7b8c")
        with scoped.connect() as connection:
            assert "evidence_revision" not in {c["name"] for c in inspect(connection).get_columns("labeling_jobs")}
        command.upgrade(cfg, "head")
        with scoped.connect() as connection:
            assert (
                connection.scalar(
                    sqlalchemy.text("SELECT result_minio_key FROM labeling_jobs WHERE id = :id"), {"id": old_job}
                )
                == "datasets/immutable/reviewed.zip"
            )
            assert (
                connection.scalar(
                    sqlalchemy.text("SELECT dataset_minio_key FROM training_runs WHERE id = :id"), {"id": training}
                )
                == "datasets/immutable/training-snapshot.zip"
            )
    finally:
        scoped.dispose()
        with engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        engine.dispose()


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
