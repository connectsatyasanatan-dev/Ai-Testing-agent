"""
Alembic migration environment.

Runs in two modes:
  - offline: generates SQL without a live DB connection.
  - online:  applies migrations against a live async DB.

The database URL is read from UITP_DATABASE_URL env var (never from
alembic.ini, which is committed to the repository).
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ── Path setup ────────────────────────────────────────────────────────────────
# Add the api/src directory to sys.path so models can be imported.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "src"))

# ── Imports must come after path setup ───────────────────────────────────────
from uitp_api.database import Base  # noqa: E402
import uitp_api.models  # noqa: E402, F401 — registers all models with Base.metadata

# ── Alembic config ────────────────────────────────────────────────────────────
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

# Read the real URL from the environment (never from alembic.ini).
_db_url = os.environ.get("UITP_DATABASE_URL")
if not _db_url:
    raise RuntimeError(
        "UITP_DATABASE_URL environment variable is not set. "
        "Set it before running Alembic."
    )

# Alembic's config object accepts a sync URL; strip the async driver prefix
# for the offline mode SQL generator.
_sync_url = _db_url.replace("postgresql+asyncpg://", "postgresql://")
config.set_main_option("sqlalchemy.url", _sync_url)


# ── Offline mode ──────────────────────────────────────────────────────────────

def run_migrations_offline() -> None:
    """Generate migration SQL without a DB connection (useful for review)."""
    context.configure(
        url=_sync_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ── Online mode (async) ───────────────────────────────────────────────────────

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        {"sqlalchemy.url": _db_url},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
