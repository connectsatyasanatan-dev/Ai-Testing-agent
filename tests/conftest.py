"""
Root conftest.py — shared pytest fixtures and configuration.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
src_dirs = [
    ROOT / "backend" / "api" / "src",
    ROOT / "backend" / "packages" / "contracts" / "src",
    ROOT / "backend" / "packages" / "evidence" / "src",
    ROOT / "backend" / "packages" / "policy" / "src",
    ROOT / "backend" / "packages" / "adapters" / "base" / "src",
    ROOT / "backend" / "packages" / "adapters" / "http_api" / "src",
    ROOT / "backend" / "packages" / "orchestrator" / "src",
    ROOT / "backend" / "workers" / "runner" / "src",
]
for p in src_dirs:
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

os.environ.setdefault("UITP_SECRET_KEY", "integration-test-secret-key-32chars!!")
os.environ.setdefault("UITP_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("UITP_APP_ENV", "test")

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from uitp_api.database import Base, get_async_session
from uitp_api.main import create_app
import uitp_api.models  # noqa: F401


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


@pytest.fixture(scope="function")
def app(test_engine):
    """FastAPI app instance with the test database overridden."""
    application = create_app()
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

    application.dependency_overrides[get_async_session] = override_get_session
    yield application
    application.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client(app):
    """AsyncClient targeting the FastAPI app, with the DB overridden."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
