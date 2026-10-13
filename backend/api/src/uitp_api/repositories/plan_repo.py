"""
uitp_api.repositories.plan_repo
===============================
Repository for TestPlan and TestStep persistence and atomic approval updates.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.plan import TestPlan, TestStep
from uitp_api.repositories.base import BaseRepository


class PlanRepository(BaseRepository[TestPlan]):
    model_class = TestPlan

    async def get_latest_version(
        self, plan_id: str, include_archived: bool = False
    ) -> TestPlan | None:
        """Fetch the latest version of a plan with steps eagerly loaded."""
        conditions = [or_(TestPlan.plan_id == plan_id, TestPlan.id == plan_id)]
        if not include_archived:
            conditions.append(TestPlan.status != "archived")

        stmt = (
            select(TestPlan)
            .options(selectinload(TestPlan.steps))
            .where(and_(*conditions))
            .order_by(TestPlan.version.desc())
            .limit(1)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_specific_version(
        self, plan_id: str, version: int
    ) -> TestPlan | None:
        """Fetch an exact version of a plan with steps eagerly loaded."""
        stmt = (
            select(TestPlan)
            .options(selectinload(TestPlan.steps))
            .where(
                and_(
                    or_(TestPlan.plan_id == plan_id, TestPlan.id == plan_id),
                    TestPlan.version == version,
                )
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_max_version_number(self, plan_id: str) -> int:
        """Return highest version number recorded for plan_id."""
        stmt = select(func.max(TestPlan.version)).where(or_(TestPlan.plan_id == plan_id, TestPlan.id == plan_id))
        result = await self._session.execute(stmt)
        val = result.scalar()
        return val if val is not None else 0

    async def list_versions(
        self, plan_id: str
    ) -> list[TestPlan]:
        """List all versions of a plan ordered by version descending."""
        stmt = (
            select(TestPlan)
            .options(selectinload(TestPlan.steps))
            .where(or_(TestPlan.plan_id == plan_id, TestPlan.id == plan_id))
            .order_by(TestPlan.version.desc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_latest_plans_for_project(
        self,
        project_id: str,
        include_archived: bool = False,
        status_filter: str | None = None,
        search_query: str | None = None,
    ) -> list[TestPlan]:
        """List the latest active plan version for each plan in the project."""
        # Find max version per plan_id
        conditions = [TestPlan.project_id == project_id]
        if not include_archived:
            conditions.append(TestPlan.status != "archived")

        subq = (
            select(TestPlan.plan_id, func.max(TestPlan.version).label("max_v"))
            .where(and_(*conditions))
            .group_by(TestPlan.plan_id)
            .subquery()
        )

        join_conditions = [
            TestPlan.plan_id == subq.c.plan_id,
            TestPlan.version == subq.c.max_v,
        ]

        stmt = (
            select(TestPlan)
            .options(selectinload(TestPlan.steps))
            .join(subq, and_(*join_conditions))
        )

        filter_conditions = []
        if status_filter:
            filter_conditions.append(TestPlan.status == status_filter)
        if search_query:
            term = f"%{search_query.strip()}%"
            filter_conditions.append(
                (TestPlan.name.ilike(term)) | (TestPlan.description.ilike(term))
            )

        if filter_conditions:
            stmt = stmt.where(and_(*filter_conditions))

        stmt = stmt.order_by(TestPlan.created_at.desc())
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def atomic_approve(
        self,
        plan_id: str,
        version: int,
        approved_by: str,
        approved_at: datetime,
    ) -> int:
        """Concurrently safe approval gate using an atomic conditional update.

        Returns:
            rowcount (1 if approved, 0 if plan is not in 'draft' or 'pending_review', or version mismatched).
        """
        stmt = (
            update(TestPlan)
            .where(
                and_(
                    or_(TestPlan.plan_id == plan_id, TestPlan.id == plan_id),
                    TestPlan.version == version,
                    TestPlan.status.in_(["draft", "pending_review"]),
                )
            )
            .values(
                status="approved",
                approved_by=approved_by,
                approved_at=approved_at,
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def atomic_submit_review(
        self,
        plan_id: str,
        version: int,
    ) -> int:
        """Concurrently safe transition from draft or rejected to pending_review."""
        stmt = (
            update(TestPlan)
            .where(
                and_(
                    or_(TestPlan.plan_id == plan_id, TestPlan.id == plan_id),
                    TestPlan.version == version,
                    TestPlan.status.in_(["draft", "rejected"]),
                )
            )
            .values(status="pending_review")
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def atomic_reject(
        self,
        plan_id: str,
        version: int,
    ) -> int:
        """Concurrently safe transition from pending_review or draft to rejected."""
        stmt = (
            update(TestPlan)
            .where(
                and_(
                    or_(TestPlan.plan_id == plan_id, TestPlan.id == plan_id),
                    TestPlan.version == version,
                    TestPlan.status.in_(["draft", "pending_review"]),
                )
            )
            .values(status="rejected")
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def atomic_archive(
        self,
        plan_id: str,
        version: int | None = None,
    ) -> int:
        """Soft-delete plan version(s)."""
        conditions = [or_(TestPlan.plan_id == plan_id, TestPlan.id == plan_id)]
        if version is not None:
            conditions.append(TestPlan.version == version)
        stmt = (
            update(TestPlan)
            .where(and_(*conditions))
            .values(status="archived")
        )
        result = await self._session.execute(stmt)
        return result.rowcount
