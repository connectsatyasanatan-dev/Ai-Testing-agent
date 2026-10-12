"""
uitp_api.services.plan_service
==============================
Business logic for declarative test plan creation, immutable versioning,
and concurrency-safe approval gates.
"""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.exceptions import (
    ConflictError,
    PermissionDeniedError,
    ResourceNotFoundError,
    ValidationError,
)
from uitp_api.models.plan import TestPlan, TestStep
from uitp_api.repositories.plan_repo import PlanRepository
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.services import audit_service
from uitp_contracts.enums import AdapterType, AuditEventType
from uitp_contracts.plan import TestStepSchema

logger = logging.getLogger(__name__)

# Keys that indicate raw credential embedding in step inputs instead of secret references
RAW_CREDENTIAL_KEYS = frozenset(
    {"password", "password_hash", "client_secret", "secret_key", "raw_secret"}
)


def _validate_step_inputs(steps: Sequence[TestStepSchema]) -> None:
    """Validate that plan steps do not embed raw credentials in inputs."""
    for step in steps:
        if step.inputs:
            for key in step.inputs.keys():
                if key.lower() in RAW_CREDENTIAL_KEYS:
                    raise ValidationError(
                        f"Step '{step.step_id}' contains raw credential field '{key}' in inputs. "
                        "Credentials must be referenced via environment secret_refs identifiers (SECURITY.md §4)."
                    )


async def _assert_project_access(
    session: AsyncSession,
    project_id: str,
    actor_id: str,
    allowed_roles: tuple[str, ...],
) -> None:
    """Verify tenant isolation and role permissions.

    Non-members receive 404 to avoid leaking project existence.
    Members with insufficient roles receive 403.
    """
    project_repo = ProjectRepository(session)
    project = await project_repo.get_by_id(project_id)
    if project is None or project.status == "archived":
        raise ResourceNotFoundError("Project not found.")

    membership = await project_repo.get_membership(project_id, actor_id)
    if membership is None:
        raise ResourceNotFoundError("Project not found.")

    if membership.role not in allowed_roles:
        raise PermissionDeniedError(
            f"Insufficient permissions: requires one of {', '.join(allowed_roles)}."
        )


async def create_plan(
    session: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
    name: str,
    adapter_type: AdapterType | str,
    steps: Sequence[TestStepSchema],
    description: str | None = None,
    ip_address: str | None = None,
) -> TestPlan:
    """Create a new plan draft (version 1).

    Requires Owner or Maintainer role on the owning project.
    """
    await _assert_project_access(session, project_id, actor_id, ("owner", "maintainer"))
    _validate_step_inputs(steps)

    plan_root_id = str(uuid.uuid4())
    plan_row_id = str(uuid.uuid4())

    plan = TestPlan(
        id=plan_row_id,
        plan_id=plan_root_id,
        project_id=project_id,
        name=name,
        version=1,
        adapter_type=str(adapter_type),
        status="draft",
        source="manual",
        description=description,
        requires_approval=True,
        created_by=actor_id,
    )
    session.add(plan)

    # Persist steps
    for idx, s in enumerate(steps):
        step_model = TestStep(
            id=str(uuid.uuid4()),
            plan_id=plan.id,
            step_id=s.step_id,
            step_order=idx + 1,
            adapter_type=str(s.adapter_type),
            action=s.action,
            inputs=s.inputs,
            assertions=[a.model_dump(by_alias=True) for a in s.assertions],
            timeout_seconds=s.timeout_seconds,
            retry_policy=s.retry_policy.model_dump(),
            idempotency_class=str(s.idempotency_class),
            risk_level=str(s.risk_level),
            description=s.description,
        )
        session.add(step_model)

    await audit_service.record(
        session,
        event_type=AuditEventType.PLAN_CREATED,
        actor_id=actor_id,
        project_id=project_id,
        resource_type="plan",
        resource_id=plan_root_id,
        ip_address=ip_address,
        metadata={
            "plan_id": plan_root_id,
            "version": 1,
            "name": name,
            "step_count": len(steps),
        },
    )

    await session.commit()
    await session.refresh(plan, ["steps"])
    return plan


async def create_plan_version(
    session: AsyncSession,
    *,
    plan_id: str,
    actor_id: str,
    steps: Sequence[TestStepSchema],
    name: str | None = None,
    description: str | None = None,
    ip_address: str | None = None,
) -> TestPlan:
    """Create a new version for an existing plan.

    Immutable versioning: does not overwrite previous versions.
    """
    repo = PlanRepository(session)
    latest = await repo.get_latest_version(plan_id, include_archived=True)
    if latest is None:
        raise ResourceNotFoundError("Plan not found.")

    await _assert_project_access(session, latest.project_id, actor_id, ("owner", "maintainer"))
    _validate_step_inputs(steps)

    max_v = await repo.get_max_version_number(plan_id)
    new_version_num = max_v + 1

    new_row = TestPlan(
        id=str(uuid.uuid4()),
        plan_id=plan_id,
        project_id=latest.project_id,
        name=name or latest.name,
        version=new_version_num,
        adapter_type=latest.adapter_type,
        status="draft",
        source="manual",
        description=description or latest.description,
        requires_approval=True,
        created_by=actor_id,
    )
    session.add(new_row)

    for idx, s in enumerate(steps):
        step_model = TestStep(
            id=str(uuid.uuid4()),
            plan_id=new_row.id,
            step_id=s.step_id,
            step_order=idx + 1,
            adapter_type=str(s.adapter_type),
            action=s.action,
            inputs=s.inputs,
            assertions=[a.model_dump(by_alias=True) for a in s.assertions],
            timeout_seconds=s.timeout_seconds,
            retry_policy=s.retry_policy.model_dump(),
            idempotency_class=str(s.idempotency_class),
            risk_level=str(s.risk_level),
            description=s.description,
        )
        session.add(step_model)

    await audit_service.record(
        session,
        event_type=AuditEventType.PLAN_CREATED,
        actor_id=actor_id,
        project_id=latest.project_id,
        resource_type="plan",
        resource_id=plan_id,
        ip_address=ip_address,
        metadata={
            "plan_id": plan_id,
            "version": new_version_num,
            "name": new_row.name,
            "step_count": len(steps),
        },
    )

    await session.commit()
    await session.refresh(new_row, ["steps"])
    return new_row


async def get_plan_latest(
    session: AsyncSession,
    *,
    plan_id: str,
    actor_id: str,
) -> TestPlan:
    """Retrieve the latest version of a plan."""
    repo = PlanRepository(session)
    plan = await repo.get_latest_version(plan_id, include_archived=False)
    if plan is None:
        raise ResourceNotFoundError("Plan not found.")

    await _assert_project_access(session, plan.project_id, actor_id, ("owner", "maintainer", "viewer"))
    return plan


async def get_plan_version(
    session: AsyncSession,
    *,
    plan_id: str,
    version: int,
    actor_id: str,
) -> TestPlan:
    """Retrieve a specific immutable version of a plan."""
    repo = PlanRepository(session)
    plan = await repo.get_specific_version(plan_id, version)
    if plan is None:
        raise ResourceNotFoundError("Plan version not found.")

    await _assert_project_access(session, plan.project_id, actor_id, ("owner", "maintainer", "viewer"))
    return plan


async def list_plans_for_project(
    session: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
) -> list[TestPlan]:
    """List all plans in a project (showing latest version of each)."""
    await _assert_project_access(session, project_id, actor_id, ("owner", "maintainer", "viewer"))
    repo = PlanRepository(session)
    return await repo.list_latest_plans_for_project(project_id, include_archived=False)


async def list_plan_versions(
    session: AsyncSession,
    *,
    plan_id: str,
    actor_id: str,
) -> tuple[str, list[TestPlan]]:
    """List version history of a plan."""
    repo = PlanRepository(session)
    versions = await repo.list_versions(plan_id)
    if not versions:
        raise ResourceNotFoundError("Plan not found.")

    project_id = versions[0].project_id
    await _assert_project_access(session, project_id, actor_id, ("owner", "maintainer", "viewer"))
    return plan_id, versions


async def approve_plan(
    session: AsyncSession,
    *,
    plan_id: str,
    version: int,
    actor_id: str,
    ip_address: str | None = None,
) -> TestPlan:
    """Concurrently safe approval of an exact immutable plan version.

    TOCTOU Defense:
      - Uses atomic conditional UPDATE.
      - Fails with ConflictError if the plan was modified, already approved, or archived.
    """
    repo = PlanRepository(session)
    existing = await repo.get_specific_version(plan_id, version)
    if existing is None:
        raise ResourceNotFoundError(f"Plan version {version} not found.")

    await _assert_project_access(session, existing.project_id, actor_id, ("owner", "maintainer"))

    if existing.status == "approved":
        raise ConflictError(f"Plan version {version} is already approved.")
    if existing.status == "archived":
        raise ConflictError(f"Cannot approve archived plan version {version}.")

    now = datetime.now(UTC)
    rows_updated = await repo.atomic_approve(
        plan_id=plan_id,
        version=version,
        approved_by=actor_id,
        approved_at=now,
    )

    if rows_updated == 0:
        raise ConflictError(
            f"Failed to approve plan version {version}: version mismatch or concurrent modification."
        )

    await audit_service.record(
        session,
        event_type=AuditEventType.PLAN_APPROVED,
        actor_id=actor_id,
        project_id=existing.project_id,
        resource_type="plan",
        resource_id=plan_id,
        ip_address=ip_address,
        metadata={
            "plan_id": plan_id,
            "version": version,
            "name": existing.name,
        },
    )

    await session.commit()
    # Refresh to return updated approved entity
    refreshed = await repo.get_specific_version(plan_id, version)
    assert refreshed is not None
    return refreshed
