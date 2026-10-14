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
from uitp_api.repositories.environment_repo import EnvironmentRepository
from uitp_api.repositories.plan_repo import PlanRepository
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.services import audit_service
from uitp_contracts.enums import AdapterType, AuditEventType
from uitp_contracts.plan import (
    PlanValidationResponse,
    TestStepSchema,
    ValidationIssue,
)

logger = logging.getLogger(__name__)

# Keys that indicate raw credential embedding in step inputs instead of secret references
RAW_CREDENTIAL_KEYS = frozenset(
    {"password", "password_hash", "client_secret", "secret_key", "raw_secret"}
)

HTTP_API_ALLOWED_ACTIONS = frozenset(
    {"http_request", "get", "post", "put", "patch", "delete", "head", "options"}
)
WEB_ALLOWED_ACTIONS = frozenset(
    {
        "navigate",
        "goto",
        "click",
        "fill",
        "select",
        "select_option",
        "wait_for",
        "wait",
        "screenshot",
        "set_cookies",
        "set_storage",
        "set_local_storage",
        "scroll",
    }
)

HTTP_API_ALLOWED_ASSERTIONS = frozenset(
    {
        "status_code",
        "json_path_equals",
        "json_path_exists",
        "json_path_matches",
        "json_schema",
        "header_equals",
        "header_exists",
        "response_time_under",
    }
)
WEB_ALLOWED_ASSERTIONS = frozenset(
    {
        "url_contains",
        "url_equals",
        "title_contains",
        "title_equals",
        "text_visible",
        "element_visible",
        "element_hidden",
        "status_code",
    }
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
    status_filter: str | None = None,
    search_query: str | None = None,
) -> list[TestPlan]:
    """List all plans in a project (showing latest version of each)."""
    await _assert_project_access(session, project_id, actor_id, ("owner", "maintainer", "viewer"))
    repo = PlanRepository(session)
    return await repo.list_latest_plans_for_project(
        project_id,
        include_archived=False,
        status_filter=status_filter,
        search_query=search_query,
    )


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


async def validate_plan_steps(
    session: AsyncSession | None = None,
    *,
    adapter_type: str,
    steps: Sequence[TestStepSchema],
    environment_id: str | None = None,
    project_id: str | None = None,
) -> PlanValidationResponse:
    """Server-side validation of action types, configuration, environment references, and limits."""
    errors: list[str] = []
    warnings: list[str] = []
    issues: list[ValidationIssue] = []

    if len(steps) == 0:
        msg = "Plan must have at least one test step."
        errors.append(msg)
        issues.append(ValidationIssue(field="steps", message=msg, severity="error"))
    elif len(steps) > 100:
        msg = f"Plan exceeds maximum limit of 100 steps (got {len(steps)})."
        errors.append(msg)
        issues.append(ValidationIssue(field="steps", message=msg, severity="error"))

    seen_ids: set[str] = set()
    for idx, step in enumerate(steps):
        prefix = f"Step {idx + 1} ('{step.step_id}')"
        if step.step_id in seen_ids:
            msg = f"Duplicate step identifier '{step.step_id}'."
            errors.append(msg)
            issues.append(ValidationIssue(step_id=step.step_id, field="step_id", message=msg, severity="error"))
        seen_ids.add(step.step_id)

        # Check action code injection
        lower_action = step.action.lower()
        forbidden_tokens = ("import ", "exec(", "eval(", "__", "subprocess", "os.system")
        for token in forbidden_tokens:
            if token in lower_action:
                msg = f"{prefix}: Action '{step.action}' contains forbidden executable code token '{token}'."
                errors.append(msg)
                issues.append(ValidationIssue(step_id=step.step_id, field="action", message=msg, severity="error"))

        # Check action allowlist
        if adapter_type == "http_api":
            if lower_action not in HTTP_API_ALLOWED_ACTIONS:
                msg = f"{prefix}: Action '{step.action}' is not supported for adapter 'http_api'. Allowed: {', '.join(sorted(HTTP_API_ALLOWED_ACTIONS))}."
                errors.append(msg)
                issues.append(ValidationIssue(step_id=step.step_id, field="action", message=msg, severity="error"))
        elif adapter_type == "web":
            if lower_action not in WEB_ALLOWED_ACTIONS:
                msg = f"{prefix}: Action '{step.action}' is not supported for adapter 'web'. Allowed: {', '.join(sorted(WEB_ALLOWED_ACTIONS))}."
                errors.append(msg)
                issues.append(ValidationIssue(step_id=step.step_id, field="action", message=msg, severity="error"))
        else:
            msg = f"Unsupported adapter type '{adapter_type}'."
            errors.append(msg)
            issues.append(ValidationIssue(field="adapter_type", message=msg, severity="error"))

        # Check inputs credentials
        if step.inputs:
            for k in step.inputs.keys():
                if k.lower() in RAW_CREDENTIAL_KEYS:
                    msg = f"{prefix}: Raw credential key '{k}' in inputs is prohibited. Use environment secret references."
                    errors.append(msg)
                    issues.append(ValidationIssue(step_id=step.step_id, field=f"inputs.{k}", message=msg, severity="error"))

        # Check adapter-specific input requirements
        if adapter_type == "http_api":
            if step.inputs and not any(k in step.inputs for k in ("path", "url", "endpoint")):
                msg = f"{prefix}: Missing target 'path' or 'url' in inputs."
                warnings.append(msg)
                issues.append(ValidationIssue(step_id=step.step_id, field="inputs.path", message=msg, severity="warning"))
        elif adapter_type == "web":
            if lower_action in ("navigate", "goto"):
                if not step.inputs or not any(k in step.inputs for k in ("url", "path")):
                    msg = f"{prefix}: Navigation action requires 'url' or 'path' in inputs."
                    errors.append(msg)
                    issues.append(ValidationIssue(step_id=step.step_id, field="inputs.url", message=msg, severity="error"))
            elif lower_action in ("click", "fill", "select", "select_option"):
                if not step.inputs or not any(k in step.inputs for k in ("locator_value", "selector", "target")):
                    msg = f"{prefix}: '{step.action}' requires locator target ('locator_value' or 'selector')."
                    errors.append(msg)
                    issues.append(ValidationIssue(step_id=step.step_id, field="inputs.locator_value", message=msg, severity="error"))
            elif lower_action == "fill":
                if not step.inputs or not any(k in step.inputs for k in ("text", "value")):
                    msg = f"{prefix}: Fill action requires 'text' or 'value' to type."
                    warnings.append(msg)
                    issues.append(ValidationIssue(step_id=step.step_id, field="inputs.text", message=msg, severity="warning"))

        # Check assertions
        for a_idx, assertion in enumerate(step.assertions):
            a_type = assertion.type.value if hasattr(assertion.type, "value") else str(assertion.type)
            if adapter_type == "http_api" and a_type not in HTTP_API_ALLOWED_ASSERTIONS:
                msg = f"{prefix} assertion #{a_idx+1}: '{a_type}' is not supported for http_api."
                errors.append(msg)
                issues.append(ValidationIssue(step_id=step.step_id, field=f"assertions[{a_idx}].type", message=msg, severity="error"))
            elif adapter_type == "web" and a_type not in WEB_ALLOWED_ASSERTIONS:
                msg = f"{prefix} assertion #{a_idx+1}: '{a_type}' is not supported for web."
                errors.append(msg)
                issues.append(ValidationIssue(step_id=step.step_id, field=f"assertions[{a_idx}].type", message=msg, severity="error"))

        # Check timeouts & retry
        if step.timeout_seconds <= 0 or step.timeout_seconds > 300:
            msg = f"{prefix}: timeout_seconds ({step.timeout_seconds}) must be between 0.1 and 300."
            errors.append(msg)
            issues.append(ValidationIssue(step_id=step.step_id, field="timeout_seconds", message=msg, severity="error"))

        if step.retry_policy:
            if step.retry_policy.max_attempts < 1 or step.retry_policy.max_attempts > 10:
                msg = f"{prefix}: retry_policy.max_attempts must be between 1 and 10."
                errors.append(msg)
                issues.append(ValidationIssue(step_id=step.step_id, field="retry_policy.max_attempts", message=msg, severity="error"))
            if str(step.idempotency_class) in ("non_idempotent", "unknown") and step.retry_policy.max_attempts > 1 and not step.retry_policy.allow_non_idempotent_retry:
                msg = f"{prefix}: Non-idempotent step with max_attempts > 1 without allow_non_idempotent_retry is unsafe."
                warnings.append(msg)
                issues.append(ValidationIssue(step_id=step.step_id, field="retry_policy", message=msg, severity="warning"))

    # Check environment if provided
    if environment_id and session:
        env_repo = EnvironmentRepository(session)
        env = await env_repo.get_by_id(environment_id)
        if env is None or env.status == "archived":
            msg = f"Referenced environment '{environment_id}' does not exist or is archived."
            errors.append(msg)
            issues.append(ValidationIssue(field="environment_id", message=msg, severity="error"))
        elif project_id and env.project_id != project_id:
            msg = f"Environment '{environment_id}' does not belong to project '{project_id}'."
            errors.append(msg)
            issues.append(ValidationIssue(field="environment_id", message=msg, severity="error"))

    return PlanValidationResponse(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        issues=issues,
    )


async def update_plan(
    session: AsyncSession,
    *,
    plan_id: str,
    actor_id: str,
    steps: Sequence[TestStepSchema],
    name: str | None = None,
    description: str | None = None,
    ip_address: str | None = None,
) -> tuple[TestPlan, bool]:
    """Edit a plan.

    If the latest version is already approved:
      Creates a new version (immutable versioning) so that approved versions and prior runs are never altered!
      Returns (new_plan_version, True).
    If the latest version is in draft, pending_review, or rejected:
      Updates the existing version in place, resetting status to 'draft'.
      Returns (updated_plan, False).
    """
    repo = PlanRepository(session)
    latest = await repo.get_latest_version(plan_id, include_archived=False)
    if latest is None:
        raise ResourceNotFoundError("Plan not found.")

    await _assert_project_access(session, latest.project_id, actor_id, ("owner", "maintainer"))
    _validate_step_inputs(steps)

    if latest.status == "approved":
        new_plan = await create_plan_version(
            session,
            plan_id=plan_id,
            actor_id=actor_id,
            steps=steps,
            name=name,
            description=description,
            ip_address=ip_address,
        )
        return new_plan, True

    if name is not None:
        latest.name = name
    if description is not None:
        latest.description = description
    latest.status = "draft"
    latest.updated_at = datetime.now(UTC)

    # Delete existing steps and recreate
    for old_s in list(latest.steps):
        await session.delete(old_s)

    for idx, s in enumerate(steps):
        step_model = TestStep(
            id=str(uuid.uuid4()),
            plan_id=latest.id,
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
            "version": latest.version,
            "name": latest.name,
            "action": "plan_draft_updated",
            "step_count": len(steps),
        },
    )

    await session.commit()
    await session.refresh(latest, ["steps"])
    return latest, False


async def update_plan_version(
    session: AsyncSession,
    *,
    plan_id: str,
    version: int,
    actor_id: str,
    steps: Sequence[TestStepSchema],
    name: str | None = None,
    description: str | None = None,
    ip_address: str | None = None,
) -> TestPlan:
    """Update a specific version. Fails with ConflictError if the version is approved or archived."""
    repo = PlanRepository(session)
    existing = await repo.get_specific_version(plan_id, version)
    if existing is None:
        raise ResourceNotFoundError(f"Plan version {version} not found.")

    await _assert_project_access(session, existing.project_id, actor_id, ("owner", "maintainer"))

    if existing.status == "approved":
        raise ConflictError(
            f"Plan version {version} is approved and immutable. Create a new version to modify this plan."
        )
    if existing.status == "archived":
        raise ConflictError(f"Cannot edit archived plan version {version}.")

    _validate_step_inputs(steps)

    if name is not None:
        existing.name = name
    if description is not None:
        existing.description = description
    existing.status = "draft"
    existing.updated_at = datetime.now(UTC)

    for old_s in list(existing.steps):
        await session.delete(old_s)

    for idx, s in enumerate(steps):
        step_model = TestStep(
            id=str(uuid.uuid4()),
            plan_id=existing.id,
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

    await session.commit()
    await session.refresh(existing, ["steps"])
    return existing


async def submit_plan_for_review(
    session: AsyncSession,
    *,
    plan_id: str,
    version: int,
    actor_id: str,
    ip_address: str | None = None,
) -> TestPlan:
    """Submit a draft or rejected plan for review."""
    repo = PlanRepository(session)
    existing = await repo.get_specific_version(plan_id, version)
    if existing is None:
        raise ResourceNotFoundError(f"Plan version {version} not found.")

    await _assert_project_access(session, existing.project_id, actor_id, ("owner", "maintainer"))

    if existing.status == "approved":
        raise ConflictError(f"Plan version {version} is already approved.")
    if existing.status == "archived":
        raise ConflictError(f"Cannot submit archived plan version {version}.")

    rows_updated = await repo.atomic_submit_review(plan_id=plan_id, version=version)
    if rows_updated == 0:
        raise ConflictError(
            f"Failed to submit plan version {version}: must be in draft or rejected status."
        )

    await audit_service.record(
        session,
        event_type=AuditEventType.PLAN_REVIEW_REQUESTED,
        actor_id=actor_id,
        project_id=existing.project_id,
        resource_type="plan",
        resource_id=plan_id,
        ip_address=ip_address,
        metadata={"plan_id": plan_id, "version": version, "name": existing.name},
    )

    await session.commit()
    refreshed = await repo.get_specific_version(plan_id, version)
    assert refreshed is not None
    return refreshed


async def reject_plan(
    session: AsyncSession,
    *,
    plan_id: str,
    version: int,
    actor_id: str,
    reason: str | None = None,
    ip_address: str | None = None,
) -> TestPlan:
    """Reject a plan under review or in draft."""
    repo = PlanRepository(session)
    existing = await repo.get_specific_version(plan_id, version)
    if existing is None:
        raise ResourceNotFoundError(f"Plan version {version} not found.")

    await _assert_project_access(session, existing.project_id, actor_id, ("owner", "maintainer"))

    if existing.status == "approved":
        raise ConflictError(f"Cannot reject an already approved plan version {version}.")
    if existing.status == "archived":
        raise ConflictError(f"Cannot reject an archived plan version {version}.")

    rows_updated = await repo.atomic_reject(plan_id=plan_id, version=version)
    if rows_updated == 0:
        raise ConflictError(
            f"Failed to reject plan version {version}: version mismatch or already approved."
        )

    await audit_service.record(
        session,
        event_type=AuditEventType.PLAN_REJECTED,
        actor_id=actor_id,
        project_id=existing.project_id,
        resource_type="plan",
        resource_id=plan_id,
        ip_address=ip_address,
        metadata={
            "plan_id": plan_id,
            "version": version,
            "name": existing.name,
            "reason": reason,
        },
    )

    await session.commit()
    refreshed = await repo.get_specific_version(plan_id, version)
    assert refreshed is not None
    return refreshed


async def archive_plan(
    session: AsyncSession,
    *,
    plan_id: str,
    actor_id: str,
    version: int | None = None,
    ip_address: str | None = None,
) -> int:
    """Soft-delete plan version or entire plan."""
    repo = PlanRepository(session)
    latest = await repo.get_latest_version(plan_id, include_archived=True)
    if latest is None:
        raise ResourceNotFoundError("Plan not found.")

    await _assert_project_access(session, latest.project_id, actor_id, ("owner", "maintainer"))

    rows_updated = await repo.atomic_archive(plan_id=plan_id, version=version)
    if rows_updated == 0:
        raise ResourceNotFoundError("Plan version not found.")

    await audit_service.record(
        session,
        event_type=AuditEventType.PLAN_ARCHIVED,
        actor_id=actor_id,
        project_id=latest.project_id,
        resource_type="plan",
        resource_id=plan_id,
        ip_address=ip_address,
        metadata={"plan_id": plan_id, "version": version},
    )

    await session.commit()
    return rows_updated
