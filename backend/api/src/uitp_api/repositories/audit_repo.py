from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.audit import AuditEvent
from uitp_api.repositories.base import BaseRepository


class AuditRepository(BaseRepository[AuditEvent]):
    model_class = AuditEvent

    async def list_for_project(
        self,
        project_id: str,
        limit: int = 50,
        offset: int = 0,
        event_type: str | None = None,
    ) -> tuple[list[AuditEvent], int]:
        """Return paginated audit events for a project with optional event_type filter."""
        conditions = [AuditEvent.project_id == project_id]
        if event_type:
            conditions.append(AuditEvent.event_type == event_type)

        total_stmt = select(func.count(AuditEvent.id)).where(and_(*conditions))
        total_res = await self._session.execute(total_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = (
            select(AuditEvent)
            .where(and_(*conditions))
            .order_by(desc(AuditEvent.timestamp))
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total
