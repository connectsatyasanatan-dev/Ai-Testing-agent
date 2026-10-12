"""
tests/integration/conftest.py
==============================
Integration test fixtures.

Each test function gets:
  - A fresh SQLite in-memory database (Alembic migrations applied).
  - An isolated async session.
  - An httpx AsyncClient wired to the FastAPI app.

No external services required for integration tests — SQLite simulates
the database. PostgreSQL-specific behaviour is validated in CI's
integration job against a real PostgreSQL service container.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from uitp_api.database import Base, get_async_session
from uitp_api.database import __init__ as db_module

# ── Environment setup ─────────────────────────────────────────────────────────
# Set required env vars before the app is imported.
os.environ.setdefault("UITP_SECRET_KEY", "integration-test-secret-key-32chars!!")
os.environ.setdefault("UITP_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("UITP_APP_ENV", "test")

# Import app AFTER env vars are set.
from uitp_api.main import create_app  # noqa: E402
import uitp_api.models  # noqa: E402, F401  — registers all models


# ── Database fixtures ─────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """Create a fresh in-memory SQLite engine with all tables for each test."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine):
    """Yield a single AsyncSession for use in tests or seeding."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


# ── HTTP client fixture ───────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="function")
async def client(test_engine):
    """AsyncClient targeting the FastAPI app, with the DB overridden."""
    factory = async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)

    async def override_get_session():  # type: ignore[return]
        async with factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    app = create_app()
    app.dependency_overrides[get_async_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
