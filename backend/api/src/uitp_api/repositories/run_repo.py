"""
uitp_api.repositories.run_repo
==============================
Async repository for Run, StepAttempt, and RunEvent models.
"""
from __future__ import annotations

from typing import Sequence
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.repositories.base import BaseRepository


class RunRepository(BaseRepository[Run]):
    model_class = Run

    async def get_by_id(self, run_id: str) -> Run | None:
        stmt = (
            select(Run)
            .where(Run.id == run_id)
            .options(selectinload(Run.step_attempts), selectinload(Run.events))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_idempotency_key(self, idempotency_key: str) -> Run | None:
        stmt = (
            select(Run)
            .where(Run.idempotency_key == idempotency_key)
            .options(selectinload(Run.step_attempts), selectinload(Run.events))
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_for_project(
        self,
        project_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Run]:
        stmt = (
            select(Run)
            .where(Run.project_id == project_id)
            .order_by(desc(Run.created_at))
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_plan(
        self,
        plan_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Run]:
        stmt = (
            select(Run)
            .where(Run.plan_id == plan_id)
            .order_by(desc(Run.created_at))
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    def add_step_attempt(self, attempt: StepAttempt) -> StepAttempt:
        self._session.add(attempt)
        return attempt

    def add_run_event(self, event: RunEvent) -> RunEvent:
        self._session.add(event)
        return event

    async def list_events_for_run(self, run_id: str) -> list[RunEvent]:
        stmt = (
            select(RunEvent)
            .where(RunEvent.run_id == run_id)
            .order_by(RunEvent.timestamp.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_attempts_for_run(self, run_id: str) -> list[StepAttempt]:
        stmt = (
            select(StepAttempt)
            .where(StepAttempt.run_id == run_id)
            .order_by(StepAttempt.started_at.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())
