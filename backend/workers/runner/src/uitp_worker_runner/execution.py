"""
uitp_worker_runner.execution
============================
Worker execution engine for durable test runs (Tasks P1-06A & P3-01).

Responsibilities:
  - Database-backed claims to prevent duplicate execution from redundant job deliveries.
  - Strict exact-version approval gate enforcement before any execution begins.
  - Step iteration respecting idempotency rules and bounded retry policies.
  - Continuous persistence of step attempts, timeline events, and evidence artifacts.
  - Graceful crash recovery: unexpected failures are recorded and never leave runs
    stuck in 'queued' or 'running'.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from uitp_adapter_base.base import ExecutionContext, StepExecutionResult
from uitp_adapter_http_api import HttpApiAdapter
from uitp_api.exceptions import PlanNotApprovedError, ResourceNotFoundError
from uitp_api.models.plan import TestPlan, TestStep
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.repositories.environment_repo import EnvironmentRepository
from uitp_api.repositories.plan_repo import PlanRepository
from uitp_api.repositories.run_repo import RunRepository
from uitp_api.services import audit_service, evidence_service
from uitp_contracts.enums import (
    AdapterType,
    AuditEventType,
    ErrorCode,
    IdempotencyClass,
    RiskLevel,
    RunStatus,
    StepStatus,
)
from uitp_contracts.plan import AssertionSchema, RetryPolicySchema, TestStepSchema
from uitp_orchestrator.exceptions import (
    DuplicateExecutionError,
    RunTimeoutError,
    WorkerExecutionError,
)
from uitp_orchestrator.retry import RetryManager
from uitp_orchestrator.state_machine import RunStateMachine

logger = logging.getLogger(__name__)

DEFAULT_WALL_CLOCK_LIMIT_SECONDS = 300.0


def _step_model_to_contract(step: TestStep) -> TestStepSchema:
    """Convert database TestStep model into canonical TestStepSchema contract."""
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


async def async_execute_run_job(
    run_id: str,
    *,
    worker_id: str | None = None,
    session: AsyncSession | None = None,
    database_url: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    wall_clock_timeout_seconds: float = DEFAULT_WALL_CLOCK_LIMIT_SECONDS,
) -> dict[str, Any]:
    """Execute a single test run asynchronously within a worker context.

    Prevents duplicate execution by verifying the run is in 'queued' state
    and claiming it before beginning step dispatch.
    """
    if worker_id is None:
        worker_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"

    # Manage session lifecycle if not passed externally
    close_session_at_end = False
    engine_to_dispose = None

    if session is None:
        db_url = database_url or os.environ.get("DATABASE_URL", "sqlite+aiosqlite:///./uitp_dev.db")
        kwargs: dict[str, Any] = {}
        if "sqlite" in db_url.lower():
            kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_async_engine(db_url, **kwargs)
        session_maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        session = session_maker()
        close_session_at_end = True
        engine_to_dispose = engine

    try:
        return await _do_execute_run(
            session=session,
            run_id=run_id,
            worker_id=worker_id,
            transport=transport,
            wall_clock_timeout_seconds=wall_clock_timeout_seconds,
        )
    finally:
        if close_session_at_end and session is not None:
            await session.close()
        if engine_to_dispose is not None:
            await engine_to_dispose.dispose()


async def _do_execute_run(
    session: AsyncSession,
    run_id: str,
    worker_id: str,
    transport: httpx.AsyncBaseTransport | None,
    wall_clock_timeout_seconds: float,
) -> dict[str, Any]:
    run_repo = RunRepository(session)
    plan_repo = PlanRepository(session)
    env_repo = EnvironmentRepository(session)

    # 1. Fetch Run Record
    run = await run_repo.get_by_id(run_id)
    if run is None:
        logger.error("Run %s not found in database.", run_id)
        raise ResourceNotFoundError(f"Run {run_id} not found.")

    # 2. Duplicate Execution Prevention (Database-backed claim)
    if run.status != RunStatus.QUEUED:
        logger.warning(
            "Run %s is in state '%s' (expected '%s'). Refusing duplicate execution.",
            run.id,
            run.status,
            RunStatus.QUEUED,
        )
        raise DuplicateExecutionError(
            run_id=run.id,
            current_status=run.status,
            worker_id=run.worker_id,
        )

    # 3. Claim the Run Record
    claim_time = datetime.now(UTC)
    RunStateMachine.assert_valid_transition(run.status, RunStatus.RUNNING)
    run.status = RunStatus.RUNNING.value
    run.worker_id = worker_id
    run.started_at = claim_time

    start_event = RunEvent(
        id=str(uuid.uuid4()),
        run_id=run.id,
        event_type="run_started",
        timestamp=claim_time,
        message=f"Run claimed and started by worker '{worker_id}'",
        event_metadata={"worker_id": worker_id, "plan_id": run.plan_id, "version": run.plan_version},
    )
    session.add(start_event)
    await session.commit()

    overall_status = RunStatus.PASSED.value
    run_deadline = time.monotonic() + wall_clock_timeout_seconds

    try:
        # 4. Strict Version Approval Gate Verification
        plan = await plan_repo.get_specific_version(run.plan_id, run.plan_version)
        if plan is None or plan.status != "approved" or not plan.approved_at or not plan.approved_by:
            logger.warning(
                "Execution gate failed for run %s: plan %s v%d is not approved.",
                run.id,
                run.plan_id,
                run.plan_version,
            )
            run.status = RunStatus.FAILED.value
            run.finished_at = datetime.now(UTC)
            session.add(
                RunEvent(
                    id=str(uuid.uuid4()),
                    run_id=run.id,
                    event_type="run_failed",
                    timestamp=run.finished_at,
                    message="Approval gate rejected execution: plan version is not approved.",
                )
            )
            await session.commit()
            return {"run_id": run.id, "status": run.status, "error": "Plan not approved"}

        # 5. Environment Verification
        env = await env_repo.get_by_id(run.environment_id)
        if env is None or env.project_id != run.project_id or env.status == "archived":
            logger.warning("Target environment %s for run %s is unavailable.", run.environment_id, run.id)
            run.status = RunStatus.FAILED.value
            run.finished_at = datetime.now(UTC)
            session.add(
                RunEvent(
                    id=str(uuid.uuid4()),
                    run_id=run.id,
                    event_type="run_failed",
                    timestamp=run.finished_at,
                    message="Target environment is unavailable or invalid.",
                )
            )
            await session.commit()
            return {"run_id": run.id, "status": run.status, "error": "Environment unavailable"}

        # 6. Adapter Instantiation
        adapter: Any = None
        if plan.adapter_type == AdapterType.HTTP_API:
            adapter = HttpApiAdapter(transport=transport)
        else:
            logger.error("Unsupported adapter type '%s' for run %s", plan.adapter_type, run.id)
            run.status = RunStatus.FAILED.value
            run.finished_at = datetime.now(UTC)
            session.add(
                RunEvent(
                    id=str(uuid.uuid4()),
                    run_id=run.id,
                    event_type="run_failed",
                    timestamp=run.finished_at,
                    message=f"Unsupported adapter type: '{plan.adapter_type}'",
                )
            )
            await session.commit()
            return {"run_id": run.id, "status": run.status, "error": f"Unsupported adapter '{plan.adapter_type}'"}

        # 7. Execute Steps with Retry Management
        steps = sorted(plan.steps, key=lambda s: s.step_order)

        for step_model in steps:
            # Check execution timeout
            if time.monotonic() >= run_deadline:
                logger.warning("Run %s exceeded total wall-clock limit of %.1fs", run.id, wall_clock_timeout_seconds)
                overall_status = RunStatus.FAILED.value
                session.add(
                    RunEvent(
                        id=str(uuid.uuid4()),
                        run_id=run.id,
                        step_id=step_model.step_id,
                        event_type="run_timeout",
                        timestamp=datetime.now(UTC),
                        message=f"Run exceeded wall-clock timeout limit ({wall_clock_timeout_seconds}s)",
                    )
                )
                break

            step_schema = _step_model_to_contract(step_model)
            step_passed = False
            current_attempt = 1
            max_attempts = step_schema.retry_policy.max_attempts if step_schema.retry_policy else 1

            # Non-idempotent steps must never be retried
            if step_schema.idempotency_class == IdempotencyClass.NON_IDEMPOTENT:
                max_attempts = 1

            while current_attempt <= max_attempts:
                if time.monotonic() >= run_deadline:
                    break

                step_start_time = datetime.now(UTC)
                session.add(
                    RunEvent(
                        id=str(uuid.uuid4()),
                        run_id=run.id,
                        step_id=step_model.step_id,
                        event_type="step_started",
                        timestamp=step_start_time,
                        message=f"Starting step '{step_model.step_id}' (attempt {current_attempt}/{max_attempts})",
                        event_metadata={"attempt_number": current_attempt},
                    )
                )
                await session.commit()

                step_timeout = min(step_schema.timeout_seconds, max(1.0, run_deadline - time.monotonic()))
                exec_ctx = ExecutionContext(
                    run_id=run.id,
                    step_id=step_model.step_id,
                    attempt_number=current_attempt,
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
                    logger.exception("Step %s execution raised unhandled exception: %s", step_model.step_id, step_exc)
                    result = StepExecutionResult(
                        status=StepStatus.FAILED,
                        error_code=ErrorCode.VALIDATION_ERROR,
                        error_message=f"Adapter execution error: {type(step_exc).__name__}",
                    )

                step_finish_time = datetime.now(UTC)

                # Persist step attempt
                attempt = StepAttempt(
                    id=str(uuid.uuid4()),
                    run_id=run.id,
                    step_id=step_model.step_id,
                    attempt_number=current_attempt,
                    status=result.status.value,
                    error_code=str(result.error_code) if result.error_code else None,
                    error_message=result.error_message,
                    started_at=step_start_time,
                    finished_at=step_finish_time,
                )
                session.add(attempt)
                await session.flush()

                # Persist evidence artifact
                try:
                    evidence_payload = {
                        "step_id": step_model.step_id,
                        "action": step_model.action,
                        "attempt_number": current_attempt,
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
                    logger.exception("Failed to store evidence for step %s: %s", step_model.step_id, ev_exc)
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

                # Persist step completion event
                session.add(
                    RunEvent(
                        id=str(uuid.uuid4()),
                        run_id=run.id,
                        step_id=step_model.step_id,
                        attempt_id=attempt.id,
                        event_type=f"step_{result.status.value}",
                        timestamp=step_finish_time,
                        message=result.error_message or f"Step '{step_model.step_id}' {result.status.value}",
                        event_metadata={
                            "attempt_number": current_attempt,
                            "status_code": result.outputs.get("status_code"),
                            "duration_ms": result.outputs.get("duration_ms"),
                        },
                    )
                )
                await session.commit()

                if result.status == StepStatus.PASSED:
                    step_passed = True
                    break

                # Evaluate Retry Policy via RetryManager
                retry_decision = RetryManager.evaluate(
                    step=step_schema,
                    current_attempt=current_attempt,
                    status=result.status,
                    error_code=result.error_code,
                )

                if retry_decision.should_retry:
                    session.add(
                        RunEvent(
                            id=str(uuid.uuid4()),
                            run_id=run.id,
                            step_id=step_model.step_id,
                            attempt_id=attempt.id,
                            event_type="step_retry_scheduled",
                            timestamp=datetime.now(UTC),
                            message=f"Retry scheduled: {retry_decision.reason}",
                            event_metadata={
                                "attempt_number": current_attempt,
                                "next_attempt": current_attempt + 1,
                                "delay_seconds": retry_decision.delay_seconds,
                            },
                        )
                    )
                    await session.commit()
                    if retry_decision.delay_seconds > 0:
                        await asyncio.sleep(retry_decision.delay_seconds)
                    current_attempt += 1
                else:
                    logger.info("Step %s will not be retried: %s", step_model.step_id, retry_decision.reason)
                    break

            if not step_passed:
                overall_status = RunStatus.FAILED.value
                logger.info("Step %s failed; stopping run execution.", step_model.step_id)
                break

    except Exception as exc:
        logger.exception("Worker crash or unhandled error during run execution: %s", exc)
        overall_status = RunStatus.FAILED.value
        if not RunStateMachine.is_terminal(run.status):
            run.status = RunStatus.FAILED.value
            run.finished_at = datetime.now(UTC)
        session.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                event_type="run_error",
                timestamp=datetime.now(UTC),
                message=f"Worker execution crash: {type(exc).__name__}",
            )
        )
        await session.commit()
        raise WorkerExecutionError(f"Worker crashed during run execution: {exc}") from exc

    finally:
        # Complete Run and record final state if not already terminal
        if not RunStateMachine.is_terminal(run.status):
            RunStateMachine.assert_valid_transition(run.status, overall_status)
            run.status = overall_status
            run.finished_at = datetime.now(UTC)

            session.add(
                RunEvent(
                    id=str(uuid.uuid4()),
                    run_id=run.id,
                    event_type=f"run_{overall_status}",
                    timestamp=run.finished_at,
                    message=f"Run completed with status '{overall_status}'",
                )
            )

            try:
                await audit_service.record(
                    session,
                    event_type=AuditEventType.RUN_CREATED,
                    actor_id=run.requested_by,
                    project_id=run.project_id,
                    resource_type="run",
                    resource_id=run.id,
                    metadata={"status": overall_status, "worker_id": worker_id},
                )
            except Exception as audit_exc:
                logger.warning("Failed to record audit event for run %s: %s", run.id, audit_exc)

            await session.commit()

    return {"run_id": run.id, "status": run.status, "worker_id": run.worker_id}


def execute_run_job(
    run_id: str,
    worker_id: str | None = None,
    database_url: str | None = None,
) -> dict[str, Any]:
    """Synchronous entry point called by RQ Worker."""
    return asyncio.run(
        async_execute_run_job(
            run_id=run_id,
            worker_id=worker_id,
            database_url=database_url,
        )
    )
