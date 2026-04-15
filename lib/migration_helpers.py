"""Idempotent guards for alembic migrations.

Use these in `upgrade()` so a half-applied schema (e.g. tables created out-of-band
or a previous run that crashed mid-migration) does not block a clean upgrade.
"""

import sqlalchemy as sa

from alembic import op


def has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    if not sa.inspect(bind).has_table(table):
        return False
    return any(c["name"] == column for c in sa.inspect(bind).get_columns(table))


def has_index(name: str) -> bool:
    return (
        op.get_bind().execute(sa.text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": name}).first() is not None
    )
