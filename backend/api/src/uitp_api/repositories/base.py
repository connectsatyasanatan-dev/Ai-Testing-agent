"""
uitp_api.repositories.base
===========================
Generic async repository with common CRUD helpers.
"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """Base repository providing get/list/add/delete helpers.

    Subclasses override model_class and add domain-specific queries.
    """

    model_class: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, resource_id: str) -> ModelT | None:
        result = await self._session.get(self.model_class, resource_id)
        return result

    async def list_all(self, **filters: Any) -> list[ModelT]:
        stmt = select(self.model_class)
        for col, val in filters.items():
            stmt = stmt.where(getattr(self.model_class, col) == val)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    def add(self, instance: ModelT) -> ModelT:
        self._session.add(instance)
        return instance

    async def delete(self, instance: ModelT) -> None:
        await self._session.delete(instance)

    async def flush(self) -> None:
        """Flush pending changes without committing."""
        await self._session.flush()
