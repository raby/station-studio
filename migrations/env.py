"""Alembic env — wired to the project's SQLAlchemy Base + DB URL.

Customisations vs the stock `alembic init` template:
  - target_metadata = Base.metadata so `--autogenerate` knows what to compare
    against. Imports from `engine.models` (which transitively imports every
    table — keep that file the canonical model registry).
  - The DB URL comes from STATION_STUDIO_DB_URL or the same DEFAULT_DB_URL
    `engine/database.py` uses, NOT the literal in alembic.ini. Means
    `make migrate` Just Works regardless of which env's DB the user is on,
    and tests can override via env var without touching alembic.ini.
  - SQLite-only: enable batch mode (`render_as_batch=True`) so future
    column-altering migrations don't try ALTER TABLE on SQLite (which
    rejects most ALTERs). Alembic's batch mode renames+rebuilds the table
    transparently. Safe no-op for non-SQLite if the project ever migrates
    off — Alembic ignores the flag for backends that support real ALTER.

See DESIGN.md §14.17 for the full setup rationale.
"""
from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# Make the repo importable so `from engine.models import Base` resolves
# regardless of where `alembic` is invoked from. The migrations/ directory
# is a sibling of engine/, api/, etc.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from engine.models import Base  # noqa: E402  — sys.path appended above.

# Alembic Config object, providing access to alembic.ini values.
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Resolve the DB URL the same way `engine.database.make_engine` does so
# Alembic and the app stay on the same database file.
DB_URL = (
    os.environ.get("STATION_STUDIO_DB_URL")
    or config.get_main_option("sqlalchemy.url")
    or "sqlite:///station_studio.db"
)
config.set_main_option("sqlalchemy.url", DB_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Generate SQL without a live DB connection."""
    context.configure(
        url=DB_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # render_as_batch is a no-op for offline + SQL output, but keeping
        # the flag here too means online vs offline behaviour stays aligned.
        render_as_batch=DB_URL.startswith("sqlite"),
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite rejects most ALTER TABLE flavours (drop column, alter
            # type, etc). Batch mode wraps each ALTER in a rename+rebuild
            # transaction. Critical for any future migration that isn't
            # purely additive (§16.4 trace versioning will need this; so
            # might #5 excluded-candidates per §17).
            render_as_batch=DB_URL.startswith("sqlite"),
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
