"""
uitp_api.services.run_service
=============================
Synchronous test run execution pipeline (Task P1-06A).

Responsibilities:
  - Exact-version approval gate enforcement (TOCTOU defense: only approved plan versions can execute).
  - Project-level authorization and tenant isolation.
  - Creation of durable Run, StepAttempt, and RunEvent records.
  - Bounded synchronous execution of plan steps through the adapter base interface.
  - Evaluation of assertions and recording of structured failure telemetry.
  - Failure recovery and transactional consistency (partial progress is preserved; unexpected failures never produce a false pass).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Sequence

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_adapter_base.base import ExecutionContext, StepExecutionResult
from uitp_adapter_http_api import HttpApiAdapter
from uitp_api.exceptions import (
    PlanNotApprovedError,
    ResourceNotFoundError,
    ValidationFailedError,
)
from uitp_api.models.environment import Environment
from uitp_api.models.plan import TestPlan, TestStep
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.repositories.environment_repo import EnvironmentRepository
from uitp_api.repositories.plan_repo import PlanRepository
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.repositories.run_repo import RunRepository
from uitp_api.services import audit_service, evidence_service
from uitp_contracts.enums import (
    AdapterType,
    AuditEventType,
    ErrorCode,
    IdempotencyClass,
    RiskLevel,
    StepStatus,
)
from uitp_contracts.plan import AssertionSchema, RetryPolicySchema, TestStepSchema

logger = logging.getLogger(__name__)

# Resource limits for Milestone 1 synchronous execution
MAX_STEPS_PER_RUN = 50
MAX_RUN_WALL_CLOCK_SECONDS = 300.0


async def _assert_project_access(
    session: AsyncSession,
    project_id: str,
    actor_id: str,
    allowed_roles: tuple[str, ...],
) -> None:
    project_repo = ProjectRepository(session)
    project = await project_repo.get_by_id(project_id)
    if project is None or project.status == "archived":
        raise ResourceNotFoundError("Project not found.")

    membership = await project_repo.get_membership(project_id, actor_id)
    if membership is None:
        raise ResourceNotFoundError("Project not found.")

    if membership.role not in allowed_roles:
        from uitp_api.exceptions import PermissionDeniedError

        raise PermissionDeniedError(
            f"Insufficient permissions: requires one of {', '.join(allowed_roles)}."
        )


def _step_model_to_contract(step: TestStep) -> TestStepSchema:
    """Safely convert database TestStep model to canonical TestStepSchema contract."""
    assertions: list[AssertionSchema] = []
    if step.assertions:
        for a in step.assertions:
            if isinstance(a, dict):
                assertions.append(AssertionSchema(**a))

    retry_policy = RetryPolicySchema(**(step.retry_policy or {}))

    return TestStepSchema(
        step_id=step.step_id,
        adapter_type=AdapterType(step.adapter_type),
        action=step.action,
        inputs=step.inputs or {},
        assertions=assertions,
        timeout_seconds=step.timeout_seconds,
        retry_policy=retry_policy,
        idempotency_class=IdempotencyClass(step.idempotency_class),
        risk_level=RiskLevel(step.risk_level),
        description=step.description,
    )


async def execute_run_sync(
    session: AsyncSession,
    *,
    plan_id: str,
    environment_id: str,
    actor_id: str,
    plan_version: int | None = None,
    idempotency_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> Run:
    """Execute a test run synchronously.

    # MILESTONE-1-SYNC-MODE: This execution runs in-process synchronously.
    Durable async orchestration via Redis/RQ queue is scheduled for P3-01.
    """
    plan_repo = PlanRepository(session)
    run_repo = RunRepository(session)
    env_repo = EnvironmentRepository(session)

    # 1. Concurrency / Idempotency key check (P3-03 preview)
    if idempotency_key:
        existing_run = await run_repo.get_by_idempotency_key(idempotency_key)
        if existing_run is not None:
            logger.info("Returning existing run %s for idempotency key %s", existing_run.id, idempotency_key)
            return existing_run

    # 2. Exact-version approval gate
    if plan_version is not None:
        plan = await plan_repo.get_specific_version(plan_id, plan_version)
        if plan is None:
            raise ResourceNotFoundError("Plan version not found.")
    else:
        # If version not specified, retrieve all versions and find currently approved version
        versions = await plan_repo.list_versions(plan_id)
        if not versions:
            raise ResourceNotFoundError("Plan not found.")
        approved_versions = [v for v in versions if v.status == "approved"]
        if not approved_versions:
            raise PlanNotApprovedError()
        # Most recent approved version
        plan = approved_versions[0]

    # Verify project access
    await _assert_project_access(session, plan.project_id, actor_id, ("owner", "maintainer"))

    # STRICT APPROVAL GATE: The exact version must be in 'approved' status
    if plan.status != "approved" or not plan.approved_at or not plan.approved_by:
        logger.warning(
            "Rejecting execution: plan %s v%d status is '%s' (not approved)",
            plan.plan_id,
            plan.version,
            plan.status,
        )
        raise PlanNotApprovedError()

    # 3. Target Environment verification
    env = await env_repo.get_by_id(environment_id)
    if env is None or env.project_id != plan.project_id or env.status == "archived":
        raise ResourceNotFoundError("Environment not found.")

    # 4. Synchronous resource limit checks
    steps = sorted(plan.steps, key=lambda s: s.step_order)
    if len(steps) > MAX_STEPS_PER_RUN:
        raise ValidationFailedError(
            f"Plan contains {len(steps)} steps, exceeding the limit of {MAX_STEPS_PER_RUN} for synchronous execution."
        )

    # 5. Create initial Run record
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    run = Run(
        id=run_id,
        plan_id=plan.plan_id,
        plan_version=plan.version,
        environment_id=env.id,
        project_id=plan.project_id,
        status="running",
        requested_by=actor_id,
        worker_id="in-process-sync-worker",
        idempotency_key=idempotency_key,
        started_at=now,
    )
    session.add(run)

    # Start event
    start_event = RunEvent(
        id=str(uuid.uuid4()),
        run_id=run.id,
        event_type="run_started",
        timestamp=now,
        message=f"Run started for plan '{plan.name}' v{plan.version}",
        event_metadata={"plan_id": plan.plan_id, "version": plan.version, "environment_id": env.id},
    )
    session.add(start_event)
    await session.commit()

    # 6. Adapter Setup
    adapter: Any = None
    if plan.adapter_type == AdapterType.HTTP_API:
        adapter = HttpApiAdapter(transport=transport)
    else:
        # Mark run failed if unsupported adapter
        run.status = "failed"
        run.finished_at = datetime.now(UTC)
        fail_event = RunEvent(
            id=str(uuid.uuid4()),
            run_id=run.id,
            event_type="run_failed",
            timestamp=run.finished_at,
            message=f"Unsupported adapter type '{plan.adapter_type}' for synchronous execution.",
        )
        session.add(fail_event)
        await session.commit()
        return run

    # 7. Execute Steps Deterministically
    overall_status = "passed"
    run_deadline = time.monotonic() + MAX_RUN_WALL_CLOCK_SECONDS

    try:
        for step_model in steps:
            # Check overall run timeout
            if time.monotonic() >= run_deadline:
                logger.warning("Run %s exceeded total wall clock limit of %fs", run.id, MAX_RUN_WALL_CLOCK_SECONDS)
                overall_status = "failed"
                timeout_event = RunEvent(
                    id=str(uuid.uuid4()),
                    run_id=run.id,
                    step_id=step_model.step_id,
                    event_type="run_timeout",
                    timestamp=datetime.now(UTC),
                    message="Run exceeded total execution time limit.",
                )
                session.add(timeout_event)
                break

            step_schema = _step_model_to_contract(step_model)
            step_start_time = datetime.now(UTC)

            # Record step_started timeline event
            step_start_event = RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                step_id=step_model.step_id,
                event_type="step_started",
                timestamp=step_start_time,
                message=f"Starting step '{step_model.step_id}' ({step_model.action})",
            )
            session.add(step_start_event)
            await session.commit()

            # Execute step through adapter contract
            step_timeout = min(step_schema.timeout_seconds, max(1.0, run_deadline - time.monotonic()))
            exec_ctx = ExecutionContext(
                run_id=run.id,
                step_id=step_model.step_id,
                attempt_number=1,
                environment_id=env.id,
                base_url=env.base_url,
                allowlist=env.allowlist or [],
                secret_refs=env.secret_refs or {},
                timeout_seconds=step_timeout,
            )

            result: StepExecutionResult
            try:
                result = await adapter.execute_step(step_schema, exec_ctx)
            except Exception as step_exc:
                logger.exception("Unexpected exception executing step %s: %s", step_model.step_id, step_exc)
                result = StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=f"Unhandled adapter exception: {type(step_exc).__name__}",
                )

            step_finish_time = datetime.now(UTC)

            # Record StepAttempt
            attempt = StepAttempt(
                id=str(uuid.uuid4()),
                run_id=run.id,
                step_id=step_model.step_id,
                attempt_number=1,
                status=result.status.value,
                error_code=str(result.error_code) if result.error_code else None,
                error_message=result.error_message,
                started_at=step_start_time,
                finished_at=step_finish_time,
            )
            session.add(attempt)
            await session.flush()

            # Store evidence artifact for attempt
            try:
                evidence_payload = {
                    "step_id": step_model.step_id,
                    "action": step_model.action,
                    "request": {
                        "method": step_schema.action if step_schema.adapter_type == AdapterType.HTTP_API else "UNKNOWN",
                        "url": result.outputs.get("url"),
                        "headers": step_schema.inputs.get("headers"),
                        "body": step_schema.inputs.get("json") or step_schema.inputs.get("body"),
                    },
                    "response": {
                        "status_code": result.outputs.get("status_code"),
                        "headers": result.outputs.get("headers"),
                        "body": result.outputs.get("body"),
                        "content_type": result.outputs.get("content_type"),
                        "duration_ms": result.outputs.get("duration_ms"),
                    },
                    "assertions": result.outputs.get("assertions"),
                }
                raw_json = json.dumps(evidence_payload)
                await evidence_service.store_artifact(
                    session,
                    run_id=run.id,
                    step_attempt_id=attempt.id,
                    artifact_type="response_log",
                    content=raw_json.encode("utf-8"),
                    content_type="application/json",
                    redaction_required=True,
                )
            except Exception as ev_exc:
                logger.exception("Failed to store step evidence for step %s: %s", step_model.step_id, ev_exc)
                session.add(
                    RunEvent(
                        id=str(uuid.uuid4()),
                        run_id=run.id,
                        step_id=step_model.step_id,
                        attempt_id=attempt.id,
                        event_type="evidence_storage_failed",
                        timestamp=datetime.now(UTC),
                        message=f"Evidence storage failed: {type(ev_exc).__name__}",
                    )
                )
                if result.status == StepStatus.PASSED:
                    result = StepExecutionResult(
                        status=StepStatus.FAILED,
                        error_code=ErrorCode.VALIDATION_ERROR,
                        error_message="Step succeeded but evidence storage failed",
                        outputs=result.outputs,
                    )
                    attempt.status = result.status.value
                    attempt.error_code = str(result.error_code)
                    attempt.error_message = result.error_message

            # Record step timeline completion event
            step_finish_event = RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                step_id=step_model.step_id,
                attempt_id=attempt.id,
                event_type=f"step_{result.status.value}",
                timestamp=step_finish_time,
                message=result.error_message or f"Step '{step_model.step_id}' {result.status.value}",
                event_metadata={
                    "status_code": result.outputs.get("status_code"),
                    "duration_ms": result.outputs.get("duration_ms"),
                },
            )
            session.add(step_finish_event)
            await session.commit()

            if result.status != StepStatus.PASSED:
                overall_status = "failed" if result.status == StepStatus.FAILED else result.status.value
                logger.info(
                    "Step %s finished with status %s; stopping run execution.",
                    step_model.step_id,
                    result.status,
                )
                break

    except Exception as run_exc:
        logger.exception("Unhandled error during run orchestration: %s", run_exc)
        overall_status = "failed"
        session.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                event_type="run_error",
                timestamp=datetime.now(UTC),
                message=f"Internal execution error: {type(run_exc).__name__}",
            )
        )

    # 8. Complete Run
    run.status = overall_status
    run.finished_at = datetime.now(UTC)

    session.add(
        RunEvent(
            id=str(uuid.uuid4()),
            run_id=run.id,
            event_type=f"run_{overall_status}",
            timestamp=run.finished_at,
            message=f"Run completed with status {overall_status}",
        )
    )

    await audit_service.record(
        session,
        event_type=AuditEventType.RUN_CREATED,
        actor_id=actor_id,
        project_id=plan.project_id,
        resource_type="run",
        resource_id=run.id,
        metadata={"plan_id": plan.plan_id, "version": plan.version, "status": overall_status},
    )

    await session.commit()
    refreshed_run = await run_repo.get_by_id(run.id)
    assert refreshed_run is not None
    return refreshed_run


async def get_run_by_id(
    session: AsyncSession,
    *,
    run_id: str,
    actor_id: str,
) -> Run:
    run_repo = RunRepository(session)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise ResourceNotFoundError("Run not found.")
    await _assert_project_access(session, run.project_id, actor_id, ("owner", "maintainer", "viewer"))
    return run


async def list_runs_for_project(
    session: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[Run]:
    await _assert_project_access(session, project_id, actor_id, ("owner", "maintainer", "viewer"))
    run_repo = RunRepository(session)
    return await run_repo.list_for_project(project_id, limit=limit, offset=offset)


async def list_run_events(
    session: AsyncSession,
    *,
    run_id: str,
    actor_id: str,
) -> list[RunEvent]:
    run = await get_run_by_id(session, run_id=run_id, actor_id=actor_id)
    run_repo = RunRepository(session)
    return await run_repo.list_events_for_run(run.id)
