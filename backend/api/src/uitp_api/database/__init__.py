"""
uitp_api.database
=================
Async SQLAlchemy engine, session factory, and declarative Base.

One engine is created per process (singleton via module-level variable).
The session factory is used by the `get_async_session` dependency.
"""
from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# ---------------------------------------------------------------------------
# Declarative base — all ORM models inherit from this.
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all UITP models."""


# ---------------------------------------------------------------------------
# Engine & session factory (lazily initialised)
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create and store the async engine.

    Call once at application startup (from `main.py` lifespan handler).
    `echo=True` logs all SQL — only enable in development.
    """
    global _engine, _session_factory

    _engine = create_async_engine(
        database_url,
        echo=echo,
        # Pool settings appropriate for a FastAPI app:
        pool_pre_ping=True,      # drop stale connections before use
        pool_size=10,            # max persistent connections
        max_overflow=20,         # extra connections allowed under load
        pool_recycle=3600,       # recycle connections older than 1 hour
    )
    _session_factory = async_sessionmaker(
        _engine,
        expire_on_commit=False,  # objects stay readable after commit
        class_=AsyncSession,
    )
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        raise RuntimeError(
            "Database engine has not been initialised. "
            "Call init_engine() during application startup."
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError(
            "Session factory has not been initialised. "
            "Call init_engine() during application startup."
        )
    return _session_factory


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields a single database session per request.

    The session is rolled back and closed on any exception; committed
    explicitly by the caller (router or service).

    Usage:
        async def my_route(db: AsyncSession = Depends(get_async_session)): ...
    """
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
