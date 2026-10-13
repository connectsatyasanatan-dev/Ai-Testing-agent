"""
uitp_api.repositories.run_repo
==============================
Async repository for Run, StepAttempt, and RunEvent models.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence
from sqlalchemy import and_, asc, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from uitp_contracts.enums import RunStatus
from uitp_api.models.plan import TestPlan
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.repositories.base import BaseRepository


class RunRepository(BaseRepository[Run]):
    model_class = Run

    async def count_active_runs_for_project(self, project_id: str) -> int:
        """Count currently queued or running executions for a project (P3-05)."""
        stmt = (
            select(func.count(Run.id))
            .where(
                and_(
                    Run.project_id == project_id,
                    Run.status.in_([RunStatus.QUEUED.value, RunStatus.RUNNING.value]),
                )
            )
        )
        result = await self._session.execute(stmt)
        return int(result.scalar_one() or 0)

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
            .options(selectinload(Run.step_attempts))
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_project_filtered(
        self,
        project_id: str,
        *,
        status: str | None = None,
        plan_id: str | None = None,
        adapter_type: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[Run], int, dict[str, tuple[str, str]]]:
        conditions = [Run.project_id == project_id]
        if status:
            conditions.append(Run.status == status)
        if plan_id:
            conditions.append(or_(Run.plan_id == plan_id, Run.id == plan_id))
        if from_date:
            conditions.append(Run.created_at >= from_date)
        if to_date:
            conditions.append(Run.created_at <= to_date)
        if adapter_type:
            plan_subquery = (
                select(TestPlan.plan_id)
                .where(TestPlan.project_id == project_id, TestPlan.adapter_type == adapter_type)
            )
            conditions.append(Run.plan_id.in_(plan_subquery))

        count_stmt = select(func.count(Run.id)).where(*conditions)
        count_res = await self._session.execute(count_stmt)
        total_count = count_res.scalar() or 0

        sort_col = getattr(Run, sort_by, Run.created_at)
        if sort_order.lower() == "desc":
            order_clause = (desc(sort_col), desc(Run.id))
        else:
            order_clause = (asc(sort_col), asc(Run.id))

        stmt = (
            select(Run)
            .where(*conditions)
            .order_by(*order_clause)
            .limit(limit)
            .offset(offset)
            .options(selectinload(Run.step_attempts))
        )
        items_res = await self._session.execute(stmt)
        runs = list(items_res.scalars().all())

        plan_map: dict[str, tuple[str, str]] = {}
        if runs:
            run_plan_ids = {r.plan_id for r in runs if r.plan_id}
            if run_plan_ids:
                plan_stmt = (
                    select(TestPlan.plan_id, TestPlan.id, TestPlan.name, TestPlan.adapter_type)
                    .where(
                        TestPlan.project_id == project_id,
                        or_(TestPlan.plan_id.in_(run_plan_ids), TestPlan.id.in_(run_plan_ids)),
                    )
                )
                plan_rows = (await self._session.execute(plan_stmt)).all()
                for p_plan_id, p_id, p_name, p_adapter in plan_rows:
                    plan_map[p_plan_id] = (p_name, p_adapter)
                    plan_map[p_id] = (p_name, p_adapter)

        return runs, total_count, plan_map

    async def get_project_dashboard_metrics(
        self,
        project_id: str,
    ) -> dict[str, Any]:
        plans_count_stmt = (
            select(func.count(func.distinct(TestPlan.plan_id)))
            .where(TestPlan.project_id == project_id, TestPlan.status != "archived")
        )
        total_plans = (await self._session.execute(plans_count_stmt)).scalar() or 0

        runs_count_stmt = select(func.count(Run.id)).where(Run.project_id == project_id)
        total_runs = (await self._session.execute(runs_count_stmt)).scalar() or 0

        status_stmt = (
            select(Run.status, func.count(Run.id))
            .where(Run.project_id == project_id)
            .group_by(Run.status)
        )
        status_rows = (await self._session.execute(status_stmt)).all()
        status_counts = {
            "passed": 0,
            "failed": 0,
            "running": 0,
            "queued": 0,
            "blocked": 0,
            "inconclusive": 0,
            "cancelled": 0,
            "skipped": 0,
        }
        for st, count in status_rows:
            status_counts[st] = count

        adapter_stmt = (
            select(TestPlan.adapter_type, func.count(func.distinct(TestPlan.plan_id)))
            .where(TestPlan.project_id == project_id, TestPlan.status != "archived")
            .group_by(TestPlan.adapter_type)
        )
        adapter_rows = (await self._session.execute(adapter_stmt)).all()
        adapter_breakdown = {atype: count for atype, count in adapter_rows}

        recent_runs_stmt = (
            select(Run)
            .where(Run.project_id == project_id)
            .order_by(desc(Run.created_at))
            .limit(10)
            .options(selectinload(Run.step_attempts))
        )
        recent_runs = list((await self._session.execute(recent_runs_stmt)).scalars().all())

        plan_map: dict[str, tuple[str, str]] = {}
        if recent_runs:
            run_plan_ids = {r.plan_id for r in recent_runs if r.plan_id}
            if run_plan_ids:
                plan_stmt = (
                    select(TestPlan.plan_id, TestPlan.id, TestPlan.name, TestPlan.adapter_type)
                    .where(
                        TestPlan.project_id == project_id,
                        or_(TestPlan.plan_id.in_(run_plan_ids), TestPlan.id.in_(run_plan_ids)),
                    )
                )
                plan_rows = (await self._session.execute(plan_stmt)).all()
                for p_plan_id, p_id, p_name, p_adapter in plan_rows:
                    plan_map[p_plan_id] = (p_name, p_adapter)
                    plan_map[p_id] = (p_name, p_adapter)

        events_stmt = (
            select(RunEvent)
            .join(Run, RunEvent.run_id == Run.id)
            .where(Run.project_id == project_id)
            .order_by(desc(RunEvent.timestamp))
            .limit(15)
        )
        recent_activity = list((await self._session.execute(events_stmt)).scalars().all())

        return {
            "project_id": project_id,
            "total_plans": total_plans,
            "total_runs": total_runs,
            "status_counts": status_counts,
            "adapter_breakdown": adapter_breakdown,
            "recent_runs": recent_runs,
            "recent_activity": recent_activity,
            "plan_map": plan_map,
        }

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
