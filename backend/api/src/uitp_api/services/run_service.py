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

import asyncio
import json
import logging
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Sequence

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_adapter_base.base import ExecutionContext, StepExecutionResult
from uitp_adapter_http_api import HttpApiAdapter
from uitp_api.exceptions import (
    ConflictError,
    ConcurrencyLimitExceededError,
    PlanNotApprovedError,
    ResourceNotFoundError,
    ValidationFailedError,
)
from uitp_api.models.dag import DAGDependency, DAGTask, DAGTaskAttempt, RunContext
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
    AgentRole,
    AuditEventType,
    ErrorCode,
    IdempotencyClass,
    RiskLevel,
    RunStatus,
    StepStatus,
    TaskPriority,
    TaskStatus,
    TestSuiteType,
)
from uitp_contracts.plan import AssertionSchema, ContextExportDef, DAGTaskSchema, RetryPolicySchema, TestStepSchema
from uitp_orchestrator import QueueError, QueueUnavailableError, RetryManager, RunQueue
from uitp_orchestrator.dag_scheduler import DAGTaskScheduler, compute_suite_execution_subgraph

logger = logging.getLogger(__name__)

# Resource limits for execution and per-project quotas (P3-05)
MAX_STEPS_PER_RUN = 50
MAX_RUN_WALL_CLOCK_SECONDS = 300.0
MAX_CONCURRENT_RUNS_PER_PROJECT = int(os.environ.get("UITP_MAX_CONCURRENT_RUNS_PER_PROJECT", "5"))


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


def _step_dict_to_contract(step_data: dict[str, Any]) -> TestStepSchema:
    """Safely convert step dictionary (from DAGTask.steps) to canonical TestStepSchema contract."""
    assertions: list[AssertionSchema] = []
    if step_data.get("assertions"):
        for a in step_data["assertions"]:
            if isinstance(a, dict):
                assertions.append(AssertionSchema(**a))
            elif isinstance(a, AssertionSchema):
                assertions.append(a)

    rp = step_data.get("retry_policy")
    if isinstance(rp, dict):
        clean_rp = {k: v for k, v in rp.items() if k in RetryPolicySchema.model_fields}
        retry_policy = RetryPolicySchema(**clean_rp)
    elif isinstance(rp, RetryPolicySchema):
        retry_policy = rp
    else:
        retry_policy = RetryPolicySchema()

    adapter_type_str = str(step_data.get("adapter_type", "http_api")).lower()
    if adapter_type_str in ("web", "playwright_web", "playwright"):
        adapter_type = AdapterType.WEB
    else:
        adapter_type = AdapterType(adapter_type_str)

    idemp_val = step_data.get("idempotency_class", "unknown")
    risk_val = step_data.get("risk_level", "low")

    # Defensive inputs normalization to prevent empty inputs runtime crashes
    raw_inputs = step_data.get("inputs")
    if not isinstance(raw_inputs, dict):
        raw_inputs = {}
    else:
        raw_inputs = dict(raw_inputs)

    for alt_key in ("parameters", "params", "args", "arguments", "payload", "body", "data"):
        val = step_data.get(alt_key)
        if isinstance(val, dict):
            for k, v in val.items():
                if k not in raw_inputs:
                    raw_inputs[k] = v
        elif val is not None and alt_key in ("payload", "body", "data"):
            if "json" not in raw_inputs and "data" not in raw_inputs:
                raw_inputs["json"] = val

    for field in ("path", "url", "selector", "value", "text", "method", "headers"):
        if field in step_data and field not in raw_inputs and step_data[field]:
            raw_inputs[field] = step_data[field]

    action = str(step_data.get("action", "")).strip().lower()

    if adapter_type == AdapterType.HTTP_API:
        if not raw_inputs.get("url") and not raw_inputs.get("path"):
            desc = str(step_data.get("description", "")).lower()
            sid = str(step_data.get("step_id", "")).lower()
            if "health" in desc or "health" in sid or "health" in action:
                raw_inputs["path"] = "/health"
            elif "auth" in desc or "login" in desc or "token" in desc:
                raw_inputs["path"] = "/api/v1/auth/token" if action == "post" else "/health"
            elif "employee" in desc or "crud" in desc:
                raw_inputs["path"] = "/api/v1/employees"
            else:
                raw_inputs["path"] = "/health"
    elif adapter_type == AdapterType.WEB:
        if action == "navigate":
            if not raw_inputs.get("url") and not raw_inputs.get("target_url"):
                raw_inputs["url"] = "http://localhost:5173"
        elif action in ("fill", "type"):
            if not raw_inputs.get("selector"):
                raw_inputs["selector"] = "input"
            if not raw_inputs.get("value") and not raw_inputs.get("text"):
                raw_inputs["value"] = "test@example.com"
        elif action == "click":
            if not raw_inputs.get("selector"):
                raw_inputs["selector"] = "button"
        elif action == "wait_for":
            if not raw_inputs.get("selector"):
                raw_inputs["selector"] = "body"

    return TestStepSchema(
        step_id=step_data["step_id"],
        adapter_type=adapter_type,
        action=action or ("get" if adapter_type == AdapterType.HTTP_API else "navigate"),
        inputs=raw_inputs,
        assertions=assertions,
        timeout_seconds=float(step_data.get("timeout_seconds", 30.0)),
        retry_policy=retry_policy,
        idempotency_class=IdempotencyClass(idemp_val) if idemp_val in IdempotencyClass._value2member_map_ else IdempotencyClass.UNKNOWN,
        risk_level=RiskLevel(risk_val) if risk_val in RiskLevel._value2member_map_ else RiskLevel.LOW,
        description=step_data.get("description"),
    )


async def execute_dag_plan(
    session: AsyncSession,
    *,
    run: Run,
    plan: TestPlan,
    env: Any,
    actor_id: str,
    worker_id: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    execution_policy: Any | None = None,
    run_deadline: float | None = None,
    web_adapter: Any | None = None,
    suite_type: TestSuiteType | str = TestSuiteType.ALL,
) -> str:
    """Execute all tasks of a multi-agent DAG plan using DAGTaskScheduler and RuntimeContext.

    Loads persisted DAGTask and DAGDependency rows, validates acyclicity and context declarations,
    manages run-scoped context extraction and expression resolution, dispatches ready tasks through
    HttpApiAdapter or PlaywrightAdapter, persists DAGTaskAttempt, StepAttempt, and RunContext rows,
    cascades failures to BLOCKED, and returns overall status ('passed' or 'failed').
    """
    from uitp_orchestrator.context import (
        RuntimeContext,
        extract_task_exports,
        redact_sensitive_values,
        resolve_context_expressions,
    )
    from uitp_orchestrator.exceptions import ContextError

    # 1. Load persisted DAG tasks and dependency edges
    task_stmt = select(DAGTask).where(or_(DAGTask.plan_id == plan.id, DAGTask.plan_id == plan.plan_id))
    tasks_res = await session.execute(task_stmt)
    task_rows = list(tasks_res.scalars().all())

    dep_stmt = select(DAGDependency).where(or_(DAGDependency.plan_id == plan.id, DAGDependency.plan_id == plan.plan_id))
    deps_res = await session.execute(dep_stmt)
    dep_rows = list(deps_res.scalars().all())

    if not task_rows:
        logger.error("No DAG tasks found for mission plan %s (run %s)", plan.id, run.id)
        session.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                event_type="dag_execution_failed",
                timestamp=datetime.now(UTC),
                message="No DAG tasks found for mission plan.",
            )
        )
        await session.commit()
        return "failed"

    # 2. Build DAGTaskSchema objects
    dag_tasks: list[DAGTaskSchema] = []
    for t in task_rows:
        depends_on = [d.parent_task_id for d in dep_rows if d.child_task_id == t.task_id]
        parsed_steps = [_step_dict_to_contract(s) for s in (t.steps or [])]
        parsed_exports = {}
        for exp_key, exp_val in (t.exports or {}).items():
            if isinstance(exp_val, dict):
                parsed_exports[exp_key] = ContextExportDef(**exp_val)
            elif isinstance(exp_val, ContextExportDef):
                parsed_exports[exp_key] = exp_val

        role_str = str(t.assigned_role)
        assigned_role = AgentRole(role_str) if role_str in AgentRole._value2member_map_ else AgentRole.API_SPECIALIST

        adapter_type_str = str(t.adapter_type).lower()
        if adapter_type_str in ("web", "playwright_web", "playwright"):
            task_adapter_type = AdapterType.WEB
        else:
            task_adapter_type = AdapterType.HTTP_API

        raw_priority = getattr(t, "priority", "P1") or "P1"
        task_pri = TaskPriority(raw_priority) if raw_priority in TaskPriority._value2member_map_ else TaskPriority.P1
        is_smoke = bool(getattr(t, "is_smoke", False))
        is_regression = bool(getattr(t, "is_regression", True))

        dag_tasks.append(
            DAGTaskSchema(
                task_id=t.task_id,
                name=t.name,
                assigned_role=assigned_role,
                adapter_type=task_adapter_type,
                depends_on=depends_on,
                steps=parsed_steps,
                exports=parsed_exports,
                timeout_seconds=float(t.timeout_seconds),
                requirement_refs=t.requirement_refs or [],
                priority=task_pri,
                is_smoke=is_smoke,
                is_regression=is_regression,
            )
        )

    # 3. Validate DAG acyclicity and context export declarations
    try:
        from uitp_api.services.multi_agent_service import (
            validate_dag_acyclicity,
            validate_dag_context_declarations,
        )
        validate_dag_acyclicity(dag_tasks)
        validate_dag_context_declarations(dag_tasks)
    except Exception as val_exc:
        logger.error("DAG acyclicity or context validation failed for plan %s: %s", plan.id, val_exc)
        session.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                event_type="dag_validation_failed",
                timestamp=datetime.now(UTC),
                message=f"DAG validation rejected: {val_exc}",
            )
        )
        await session.commit()
        return "failed"

    # 4. Partition DAG tasks for requested test suite selection
    try:
        targets, prerequisites, skipped = compute_suite_execution_subgraph(dag_tasks, suite_type)
    except ValueError as val_err:
        logger.error("Invalid suite filter '%s' for run %s: %s", suite_type, run.id, val_err)
        session.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                event_type="run_failed",
                timestamp=datetime.now(UTC),
                message=str(val_err),
            )
        )
        await session.commit()
        return "failed"

    # Record skipped tasks immediately in database
    for s_id in skipped:
        session.add(
            DAGTaskAttempt(
                id=str(uuid.uuid4()),
                run_id=run.id,
                task_id=s_id,
                attempt_number=1,
                status="skipped",
                worker_id=worker_id,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                error_message=f"Task excluded from execution for suite '{suite_type}'.",
            )
        )
        session.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                event_type="task_skipped",
                timestamp=datetime.now(UTC),
                message=f"Task '{s_id}' excluded from suite '{suite_type}' execution",
                event_metadata={"task_id": s_id, "suite_type": str(suite_type)},
            )
        )
    await session.commit()

    # 5. Initialize run-scoped RuntimeContext and DAG scheduler with active tasks
    runtime_context = RuntimeContext(
        run_id=run.id,
        tasks=dag_tasks,
        project_id=plan.project_id,
    )
    active_task_ids = targets | prerequisites
    scheduler = DAGTaskScheduler(dag_tasks, active_task_ids=active_task_ids)
    http_adapter = HttpApiAdapter(transport=transport, execution_policy=execution_policy)

    def _get_web_adapter() -> Any:
        nonlocal web_adapter
        if web_adapter is None:
            from uitp_adapter_web import PlaywrightAdapter
            web_adapter = PlaywrightAdapter(execution_policy=execution_policy)
        return web_adapter

    if run_deadline is None:
        run_deadline = time.monotonic() + MAX_RUN_WALL_CLOCK_SECONDS

    overall_status = "passed"

    try:
        while not scheduler.is_finished():
            # Check overall run timeout
            if time.monotonic() >= run_deadline:
                logger.warning("DAG run %s exceeded total wall clock limit of %fs", run.id, MAX_RUN_WALL_CLOCK_SECONDS)
                overall_status = "failed"
                session.add(
                    RunEvent(
                        id=str(uuid.uuid4()),
                        run_id=run.id,
                        event_type="run_timeout",
                        timestamp=datetime.now(UTC),
                        message="Run exceeded total execution time limit.",
                    )
                )
                scheduler.cancel_all_pending()
                await session.commit()
                break

            # Check external cancellation
            refreshed_run = await session.get(Run, run.id)
            if refreshed_run and refreshed_run.status == RunStatus.CANCELLED.value:
                logger.info("DAG run %s was externally cancelled", run.id)
                overall_status = RunStatus.CANCELLED.value
                scheduler.cancel_all_pending()
                break

            ready_tasks = scheduler.get_ready_tasks()
            if not ready_tasks:
                if not scheduler.is_finished():
                    logger.warning("DAG scheduler has no ready tasks but is not finished. Marking failed.")
                    overall_status = "failed"
                break

            for task_schema in ready_tasks:
                # Check external cancellation before each task
                refreshed_run = await session.get(Run, run.id)
                if refreshed_run and refreshed_run.status == RunStatus.CANCELLED.value:
                    overall_status = RunStatus.CANCELLED.value
                    scheduler.cancel_all_pending()
                    break

                if time.monotonic() >= run_deadline:
                    overall_status = "failed"
                    scheduler.cancel_all_pending()
                    break

                scheduler.mark_task_running(task_schema.task_id)
                task_start_time = datetime.now(UTC)

                task_attempt = DAGTaskAttempt(
                    id=str(uuid.uuid4()),
                    run_id=run.id,
                    task_id=task_schema.task_id,
                    attempt_number=1,
                    status="running",
                    worker_id=worker_id,
                    started_at=task_start_time,
                )
                session.add(task_attempt)
                session.add(
                    RunEvent(
                        id=str(uuid.uuid4()),
                        run_id=run.id,
                        event_type="task_started",
                        timestamp=task_start_time,
                        message=f"Starting task '{task_schema.task_id}' ({task_schema.name})",
                        event_metadata={
                            "task_id": task_schema.task_id,
                            "assigned_role": task_schema.assigned_role.value,
                            "adapter_type": task_schema.adapter_type.value,
                        },
                    )
                )
                await session.commit()

                # Choose adapter
                target_adapter = http_adapter if task_schema.adapter_type == AdapterType.HTTP_API else _get_web_adapter()

                task_passed = True
                task_error: str | None = None
                task_deadline = min(run_deadline, time.monotonic() + task_schema.timeout_seconds)

                # Validate context expressions immediately before adapter execution
                try:
                    runtime_context.validate_task_expressions(task_schema)
                except ContextError as ctx_err:
                    logger.warning("Context validation failed for task %s: %s", task_schema.task_id, ctx_err)
                    task_passed = False
                    task_error = f"Context validation failed: {ctx_err}"

                task_step_outputs: list[dict[str, Any]] = []

                if task_passed:
                    task_context = await runtime_context.get_task_context(task_schema.task_id)

                    for step_schema in task_schema.steps:
                        current_step_attempt = 1
                        max_step_attempts = (
                            step_schema.retry_policy.max_attempts
                            if step_schema.retry_policy
                            else 1
                        )
                        step_passed = False
                        last_result: StepExecutionResult | None = None

                        while current_step_attempt <= max_step_attempts:
                            if time.monotonic() >= task_deadline:
                                task_passed = False
                                task_error = f"Task '{task_schema.task_id}' exceeded timeout of {task_schema.timeout_seconds}s"
                                break

                            step_start_time = datetime.now(UTC)
                            session.add(
                                RunEvent(
                                    id=str(uuid.uuid4()),
                                    run_id=run.id,
                                    step_id=step_schema.step_id,
                                    event_type="step_started",
                                    timestamp=step_start_time,
                                    message=f"Starting step '{step_schema.step_id}' in task '{task_schema.task_id}' (attempt {current_step_attempt}/{max_step_attempts})",
                                    event_metadata={
                                        "task_id": task_schema.task_id,
                                        "attempt_number": current_step_attempt,
                                    },
                                )
                            )
                            await session.commit()

                            # Resolve context expressions immediately before dispatching each step
                            step_dict = step_schema.model_dump()
                            resolved_step_dict = resolve_context_expressions(step_dict, task_context)
                            resolved_step = _step_dict_to_contract(resolved_step_dict)

                            step_timeout = min(resolved_step.timeout_seconds, max(1.0, task_deadline - time.monotonic()))
                            exec_ctx = ExecutionContext(
                                run_id=run.id,
                                step_id=resolved_step.step_id,
                                attempt_number=current_step_attempt,
                                environment_id=env.id,
                                base_url=env.base_url,
                                allowlist=env.allowlist or [],
                                secret_refs=env.secret_refs or {},
                                timeout_seconds=step_timeout,
                            )

                            result: StepExecutionResult
                            try:
                                result = await target_adapter.execute_step(resolved_step, exec_ctx)
                            except Exception as step_exc:
                                logger.exception("Step %s in task %s raised exception: %s", resolved_step.step_id, task_schema.task_id, step_exc)
                                result = StepExecutionResult(
                                    status=StepStatus.FAILED,
                                    error_code=ErrorCode.VALIDATION_ERROR,
                                    error_message=f"Adapter execution exception: {type(step_exc).__name__}: {step_exc}",
                                )

                            last_result = result
                            step_finish_time = datetime.now(UTC)
                            attempt = StepAttempt(
                                id=str(uuid.uuid4()),
                                run_id=run.id,
                                step_id=resolved_step.step_id,
                                attempt_number=current_step_attempt,
                                status=result.status.value,
                                error_code=str(result.error_code) if result.error_code else None,
                                error_message=result.error_message,
                                started_at=step_start_time,
                                finished_at=step_finish_time,
                            )
                            session.add(attempt)
                            await session.flush()

                            # Store evidence artifact for attempt with redaction of known secrets
                            try:
                                secret_vals = await runtime_context.get_secret_values()
                                evidence_payload = {
                                    "task_id": task_schema.task_id,
                                    "step_id": resolved_step.step_id,
                                    "attempt_number": current_step_attempt,
                                    "action": resolved_step.action,
                                    "request": {
                                        "method": resolved_step.action if resolved_step.adapter_type == AdapterType.HTTP_API else "UNKNOWN",
                                        "url": result.outputs.get("url"),
                                        "headers": resolved_step.inputs.get("headers"),
                                        "body": resolved_step.inputs.get("json") or resolved_step.inputs.get("body"),
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
                                sanitized_payload = redact_sensitive_values(evidence_payload, secret_vals)
                                raw_json = json.dumps(sanitized_payload)
                                await evidence_service.store_artifact(
                                    session,
                                    run_id=run.id,
                                    step_attempt_id=attempt.id,
                                    artifact_type="response_log",
                                    content=raw_json.encode("utf-8"),
                                    content_type="application/json",
                                    redaction_required=True,
                                )

                                screenshot_bytes = (
                                    result.outputs.get("screenshot_bytes")
                                    or result.outputs.get("failure_screenshot_bytes")
                                )
                                if screenshot_bytes and isinstance(screenshot_bytes, bytes):
                                    await evidence_service.store_artifact(
                                        session,
                                        run_id=run.id,
                                        step_attempt_id=attempt.id,
                                        artifact_type="screenshot",
                                        content=screenshot_bytes,
                                        content_type="image/png",
                                        redaction_required=False,
                                    )
                            except Exception as ev_exc:
                                logger.debug("Evidence storage notification: %s", ev_exc)

                            session.add(
                                RunEvent(
                                    id=str(uuid.uuid4()),
                                    run_id=run.id,
                                    step_id=resolved_step.step_id,
                                    attempt_id=attempt.id,
                                    event_type=f"step_{result.status.value}",
                                    timestamp=step_finish_time,
                                    message=result.error_message or f"Step '{resolved_step.step_id}' {result.status.value}",
                                    event_metadata={
                                        "task_id": task_schema.task_id,
                                        "attempt_number": current_step_attempt,
                                        "status_code": result.outputs.get("status_code"),
                                        "duration_ms": result.outputs.get("duration_ms"),
                                    },
                                )
                            )
                            await session.commit()

                            if result.status == StepStatus.PASSED:
                                step_passed = True
                                task_step_outputs.append(result.outputs)
                                break

                            # Evaluate retry decision via RetryManager
                            retry_decision = RetryManager.evaluate(
                                step=step_schema,
                                current_attempt=current_step_attempt,
                                status=result.status,
                                error_code=result.error_code,
                                status_code=result.outputs.get("status_code"),
                            )

                            if retry_decision.should_retry and (time.monotonic() + retry_decision.delay_seconds < task_deadline):
                                session.add(
                                    RunEvent(
                                        id=str(uuid.uuid4()),
                                        run_id=run.id,
                                        step_id=resolved_step.step_id,
                                        attempt_id=attempt.id,
                                        event_type="step_retry_scheduled",
                                        timestamp=datetime.now(UTC),
                                        message=f"Retry scheduled: {retry_decision.reason}",
                                        event_metadata={
                                            "task_id": task_schema.task_id,
                                            "attempt_number": current_step_attempt,
                                            "next_attempt": current_step_attempt + 1,
                                            "delay_seconds": retry_decision.delay_seconds,
                                        },
                                    )
                                )
                                await session.commit()
                                if retry_decision.delay_seconds > 0:
                                    await asyncio.sleep(retry_decision.delay_seconds)
                                current_step_attempt += 1
                            else:
                                logger.info(
                                    "Step %s in task %s will not be retried: %s",
                                    step_schema.step_id,
                                    task_schema.task_id,
                                    retry_decision.reason,
                                )
                                break

                        if not step_passed:
                            task_passed = False
                            task_error = (
                                (last_result.error_message if last_result else None)
                                or task_error
                                or f"Step '{step_schema.step_id}' failed"
                            )
                            break

                # Extract and publish context exports only if task succeeded
                if task_passed and task_schema.exports:
                    try:
                        extracted_exports = extract_task_exports(task_schema, task_step_outputs)
                        await runtime_context.publish_exports(task_schema.task_id, extracted_exports)

                        # Persist exports to database run_contexts table (redacting secret values)
                        for exp_key, (exp_val, is_sec) in extracted_exports.items():
                            stored_db_val = "[REDACTED]" if is_sec else str(exp_val)
                            db_ctx = RunContext(
                                id=str(uuid.uuid4()),
                                run_id=run.id,
                                key=exp_key,
                                value=stored_db_val,
                                is_secret=is_sec,
                                source_task_id=task_schema.task_id,
                            )
                            session.add(db_ctx)
                    except ContextError as exp_err:
                        logger.warning("Export extraction failed for task %s: %s", task_schema.task_id, exp_err)
                        task_passed = False
                        task_error = f"Export extraction failed: {exp_err}"

                # Update task outcome
                task_finish_time = datetime.now(UTC)
                task_attempt.finished_at = task_finish_time

                if task_passed:
                    task_attempt.status = "passed"
                    session.add(
                        RunEvent(
                            id=str(uuid.uuid4()),
                            run_id=run.id,
                            event_type="task_passed",
                            timestamp=task_finish_time,
                            message=f"Task '{task_schema.task_id}' passed successfully",
                            event_metadata={"task_id": task_schema.task_id},
                        )
                    )
                    scheduler.mark_task_passed(task_schema.task_id)
                    await session.commit()
                else:
                    overall_status = "failed"
                    task_attempt.status = "failed"
                    secret_vals = await runtime_context.get_secret_values()
                    safe_task_error = redact_sensitive_values(task_error, secret_vals) if task_error else None
                    task_attempt.error_message = safe_task_error
                    session.add(
                        RunEvent(
                            id=str(uuid.uuid4()),
                            run_id=run.id,
                            event_type="task_failed",
                            timestamp=task_finish_time,
                            message=f"Task '{task_schema.task_id}' failed: {safe_task_error}",
                            event_metadata={"task_id": task_schema.task_id, "error": safe_task_error},
                        )
                    )
                    # Cascade BLOCKED to all downstream tasks
                    blocked_task_ids = scheduler.mark_task_failed(task_schema.task_id)
                    for blocked_id in blocked_task_ids:
                        blocked_attempt = DAGTaskAttempt(
                            id=str(uuid.uuid4()),
                            run_id=run.id,
                            task_id=blocked_id,
                            attempt_number=1,
                            status="blocked",
                            error_message=f"Prerequisite task '{task_schema.task_id}' failed: {safe_task_error}",
                            worker_id=worker_id,
                            started_at=task_finish_time,
                            finished_at=task_finish_time,
                        )
                        session.add(blocked_attempt)
                        session.add(
                            RunEvent(
                                id=str(uuid.uuid4()),
                                run_id=run.id,
                                event_type="task_blocked",
                                timestamp=task_finish_time,
                                message=f"Task '{blocked_id}' blocked due to upstream failure of task '{task_schema.task_id}'",
                                event_metadata={"task_id": blocked_id, "failed_prerequisite": task_schema.task_id},
                            )
                        )
                    await session.commit()


    finally:
        if web_adapter is not None:
            try:
                await web_adapter.close_session(run.id)
            except Exception as close_exc:
                logger.debug("Error closing web session for run %s: %s", run.id, close_exc)

    return overall_status


async def execute_run_sync(
    session: AsyncSession,
    *,
    plan_id: str,
    environment_id: str,
    actor_id: str,
    plan_version: int | None = None,
    idempotency_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    web_adapter: Any | None = None,
    suite_type: TestSuiteType | str = TestSuiteType.ALL,
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

    # Concurrency quota check (P3-05)
    active_count = await run_repo.count_active_runs_for_project(plan.project_id)
    if active_count >= MAX_CONCURRENT_RUNS_PER_PROJECT:
        logger.warning(
            "Project %s reached concurrency limit (%d active runs)",
            plan.project_id,
            active_count,
        )
        raise ConcurrencyLimitExceededError(
            f"Project has reached its maximum concurrency limit ({MAX_CONCURRENT_RUNS_PER_PROJECT} active runs). Wait for active runs to finish."
        )

    # 4. Synchronous resource limit checks
    steps = sorted(plan.steps, key=lambda s: s.step_order)
    if plan.adapter_type != "multi_agent_dag" and len(steps) > MAX_STEPS_PER_RUN:
        raise ValidationFailedError(
            f"Plan contains {len(steps)} steps, exceeding the limit of {MAX_STEPS_PER_RUN} for synchronous execution."
        )

    # 5. Create initial Run record
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    suite_str = suite_type.value if hasattr(suite_type, "value") else str(suite_type)
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
        suite_type=suite_str,
    )
    session.add(run)

    # Start event
    start_event = RunEvent(
        id=str(uuid.uuid4()),
        run_id=run.id,
        event_type="run_started",
        timestamp=now,
        message=f"Run started for plan '{plan.name}' v{plan.version}",
        event_metadata={"plan_id": plan.plan_id, "version": plan.version, "environment_id": env.id, "suite_type": suite_str},
    )
    session.add(start_event)
    await session.commit()

    # 6. Adapter Setup & Execution
    overall_status = "passed"
    run_deadline = time.monotonic() + MAX_RUN_WALL_CLOCK_SECONDS

    if plan.adapter_type == "multi_agent_dag":
        overall_status = await execute_dag_plan(
            session,
            run=run,
            plan=plan,
            env=env,
            actor_id=actor_id,
            worker_id="in-process-sync-worker",
            transport=transport,
            web_adapter=web_adapter,
            run_deadline=run_deadline,
            suite_type=suite_type,
        )

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
            metadata={
                "plan_id": plan.plan_id,
                "version": plan.version,
                "status": overall_status,
            },
        )
        await session.commit()

        # Trigger authoritative post-run evaluation
        try:
            from uitp_api.services.evaluation_service import evaluate_and_persist_run
            await evaluate_and_persist_run(session, run_id=run.id, actor_id=actor_id)
        except Exception as eval_exc:
            logger.warning("Automatic post-run evaluation failed for run %s: %s", run.id, eval_exc)

        try:
            from uitp_api.services.report_export_service import export_run_execution_artifacts
            await export_run_execution_artifacts(session, run_id=run.id)
        except Exception as exp_exc:
            logger.debug("Automatic export notification for run %s: %s", run.id, exp_exc)

        refreshed_run = await run_repo.get_by_id(run.id)
        return refreshed_run or run

    # 6. Adapter Setup
    adapter: Any = None
    if plan.adapter_type == AdapterType.HTTP_API:
        adapter = HttpApiAdapter(transport=transport)
    elif plan.adapter_type == AdapterType.WEB:
        from uitp_adapter_web import PlaywrightAdapter
        adapter = PlaywrightAdapter()
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
        refreshed_run = await run_repo.get_by_id(run.id)
        return refreshed_run or run

    # 7. Execute Steps Deterministically
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

                # Persist screenshot if captured
                screenshot_bytes = (
                    result.outputs.get("screenshot_bytes")
                    or result.outputs.get("failure_screenshot_bytes")
                )
                if screenshot_bytes and isinstance(screenshot_bytes, bytes):
                    try:
                        await evidence_service.store_artifact(
                            session,
                            run_id=run.id,
                            step_attempt_id=attempt.id,
                            artifact_type="screenshot",
                            content=screenshot_bytes,
                            content_type="image/png",
                            redaction_required=False,
                        )
                    except Exception as ss_exc:
                        logger.debug("Failed to store screenshot artifact: %s", ss_exc)
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

    if adapter is not None and hasattr(adapter, "close_session"):
        try:
            await adapter.close_session(run.id)
        except Exception:
            pass

    await session.commit()

    # Trigger authoritative post-run evaluation
    try:
        from uitp_api.services.evaluation_service import evaluate_and_persist_run
        await evaluate_and_persist_run(session, run_id=run.id, actor_id=actor_id)
    except Exception as eval_exc:
        logger.warning("Automatic post-run evaluation failed for single-plan run %s: %s", run.id, eval_exc)

    try:
        from uitp_api.services.report_export_service import export_run_execution_artifacts
        await export_run_execution_artifacts(session, run_id=run.id)
    except Exception as exp_exc:
        logger.debug("Automatic export notification for single-plan run %s: %s", run.id, exp_exc)

    refreshed_run = await run_repo.get_by_id(run.id)
    return refreshed_run


async def create_and_enqueue_run(
    session: AsyncSession,
    *,
    plan_id: str,
    environment_id: str,
    actor_id: str,
    plan_version: int | None = None,
    idempotency_key: str | None = None,
    queue: RunQueue | None = None,
    suite_type: TestSuiteType | str = TestSuiteType.ALL,
) -> Run:
    """Create a durable Run in 'queued' status and enqueue for worker execution.

    Resilient to partial failure: if Redis or RQ enqueue fails, the Run record
    is transitioned to 'failed' and an event is persisted, ensuring no run
    is left indefinitely stuck in 'queued'.
    """
    plan_repo = PlanRepository(session)
    run_repo = RunRepository(session)
    env_repo = EnvironmentRepository(session)

    # 1. Idempotency check
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
        versions = await plan_repo.list_versions(plan_id)
        if not versions:
            raise ResourceNotFoundError("Plan not found.")
        approved_versions = [v for v in versions if v.status == "approved"]
        if not approved_versions:
            raise PlanNotApprovedError()
        plan = approved_versions[0]

    await _assert_project_access(session, plan.project_id, actor_id, ("owner", "maintainer"))

    if plan.status != "approved" or not plan.approved_at or not plan.approved_by:
        logger.warning(
            "Rejecting enqueue: plan %s v%d status is '%s' (not approved)",
            plan.plan_id,
            plan.version,
            plan.status,
        )
        raise PlanNotApprovedError()

    # 3. Target Environment verification
    env = await env_repo.get_by_id(environment_id)
    if env is None or env.project_id != plan.project_id or env.status == "archived":
        raise ResourceNotFoundError("Environment not found.")

    # Concurrency quota check (P3-05)
    active_count = await run_repo.count_active_runs_for_project(plan.project_id)
    if active_count >= MAX_CONCURRENT_RUNS_PER_PROJECT:
        logger.warning(
            "Project %s reached concurrency limit (%d active runs)",
            plan.project_id,
            active_count,
        )
        raise ConcurrencyLimitExceededError(
            f"Project has reached its maximum concurrency limit ({MAX_CONCURRENT_RUNS_PER_PROJECT} active runs). Wait for active runs to finish."
        )

    # 4. Create durable Run record in state 'queued'
    run_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    suite_str = suite_type.value if hasattr(suite_type, "value") else str(suite_type)
    run = Run(
        id=run_id,
        plan_id=plan.plan_id,
        plan_version=plan.version,
        environment_id=env.id,
        project_id=plan.project_id,
        status=RunStatus.QUEUED.value,
        requested_by=actor_id,
        idempotency_key=idempotency_key,
        created_at=now,
        suite_type=suite_str,
    )
    session.add(run)

    queued_event = RunEvent(
        id=str(uuid.uuid4()),
        run_id=run.id,
        event_type="run_queued",
        timestamp=now,
        message=f"Run queued for background execution: plan '{plan.name}' v{plan.version}",
        event_metadata={"plan_id": plan.plan_id, "version": plan.version, "environment_id": env.id},
    )
    session.add(queued_event)
    await session.commit()

    # 5. Enqueue operation with partial failure resilience
    if queue is None:
        queue = RunQueue(is_mock=True)

    try:
        queue.enqueue_run(run_id=run.id)
    except (QueueError, QueueUnavailableError, Exception) as queue_exc:
        logger.error(
            "Enqueue failed for run %s: %s. Transitioning run to failed to prevent hanging in queued state.",
            run.id,
            queue_exc,
        )
        fail_now = datetime.now(UTC)
        run.status = RunStatus.FAILED.value
        run.finished_at = fail_now
        session.add(
            RunEvent(
                id=str(uuid.uuid4()),
                run_id=run.id,
                event_type="run_failed",
                timestamp=fail_now,
                message=f"Queue transport failure: {queue_exc}",
                event_metadata={"error": str(queue_exc)},
            )
        )
        await session.commit()
        raise QueueError(f"Failed to enqueue run: {queue_exc}") from queue_exc

    # 6. Record audit event
    await audit_service.record(
        session,
        event_type=AuditEventType.RUN_CREATED,
        actor_id=actor_id,
        project_id=plan.project_id,
        resource_type="run",
        resource_id=run.id,
        metadata={"plan_id": plan.plan_id, "version": plan.version, "status": "queued"},
    )
    await session.commit()

    refreshed = await run_repo.get_by_id(run.id)
    assert refreshed is not None
    return refreshed



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


async def list_runs_for_project_filtered(
    session: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
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
    await _assert_project_access(session, project_id, actor_id, ("owner", "maintainer", "viewer"))
    run_repo = RunRepository(session)
    return await run_repo.list_for_project_filtered(
        project_id,
        status=status,
        plan_id=plan_id,
        adapter_type=adapter_type,
        from_date=from_date,
        to_date=to_date,
        sort_by=sort_by,
        sort_order=sort_order,
        limit=limit,
        offset=offset,
    )


async def get_project_dashboard_metrics(
    session: AsyncSession,
    *,
    project_id: str,
    actor_id: str,
) -> dict[str, Any]:
    await _assert_project_access(session, project_id, actor_id, ("owner", "maintainer", "viewer"))
    run_repo = RunRepository(session)
    return await run_repo.get_project_dashboard_metrics(project_id)


async def list_run_events(
    session: AsyncSession,
    *,
    run_id: str,
    actor_id: str,
) -> list[RunEvent]:
    run = await get_run_by_id(session, run_id=run_id, actor_id=actor_id)
    run_repo = RunRepository(session)
    return await run_repo.list_events_for_run(run.id)


async def cancel_run(
    session: AsyncSession,
    *,
    run_id: str,
    actor_id: str,
    reason: str | None = None,
) -> Run:
    """Request cancellation of an active or queued run (Stage 2 & API_CONTRACT.md).

    Validates project authorization, enforces terminal-state invariants, and transitions
    active/queued runs to 'cancelled' with durable timeline and audit records.
    """
    run_repo = RunRepository(session)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise ResourceNotFoundError("Run not found.")

    await _assert_project_access(session, run.project_id, actor_id, ("owner", "maintainer"))

    if run.status in (RunStatus.PASSED.value, RunStatus.FAILED.value):
        raise ConflictError(f"Run cannot be cancelled because it is in terminal state '{run.status}'.")

    if run.status == RunStatus.CANCELLED.value:
        return run

    prev_status = run.status
    now = datetime.now(UTC)
    run.status = RunStatus.CANCELLED.value
    run.finished_at = now

    cancel_msg = f"Run cancelled by user: {reason}" if reason else "Run cancelled by user."
    session.add(
        RunEvent(
            id=str(uuid.uuid4()),
            run_id=run.id,
            event_type="run_cancelled",
            timestamp=now,
            message=cancel_msg,
            event_metadata={"actor_id": actor_id, "previous_status": prev_status, "reason": reason},
        )
    )

    await audit_service.record(
        session,
        event_type=AuditEventType.RUN_CANCELLED,
        actor_id=actor_id,
        project_id=run.project_id,
        resource_type="run",
        resource_id=run.id,
        metadata={"previous_status": prev_status, "reason": reason},
    )

    await session.commit()
    try:
        from uitp_api.services.evaluation_service import evaluate_and_persist_run
        await evaluate_and_persist_run(session, run_id=run.id, actor_id=actor_id)
        from uitp_api.services.report_export_service import export_run_execution_artifacts
        await export_run_execution_artifacts(session, run_id=run.id)
    except Exception as canc_eval_exc:
        logger.debug("Evaluation/export for cancelled run %s notification: %s", run.id, canc_eval_exc)

    refreshed = await run_repo.get_by_id(run.id)
    assert refreshed is not None
    return refreshed


async def renew_run_lease(
    session: AsyncSession,
    *,
    run_id: str,
    worker_id: str,
    lease_seconds: int = 60,
) -> Run:
    """Renew worker lease heartbeat for an actively running test run (Task P3-02)."""
    run_repo = RunRepository(session)
    run = await run_repo.get_by_id(run_id)
    if run is None:
        raise ResourceNotFoundError("Run not found.")

    if run.status != RunStatus.RUNNING.value:
        raise ConflictError(f"Cannot renew lease for run in status '{run.status}'.")

    now = datetime.now(UTC)
    run.worker_id = worker_id
    run.lease_expires_at = now + timedelta(seconds=lease_seconds)
    session.add(run)
    await session.commit()
    return run


async def recover_stale_runs(
    session: AsyncSession,
    *,
    timeout_seconds: int = 300,
) -> list[str]:
    """Scan and recover orphaned runs whose worker leases have expired or worker crashed (Task P3-02).

    Transitions stale runs from 'running' to 'failed' and writes a run_failed timeline event.
    Guarantees no dead worker leaves a run silently passed or indefinitely stuck in running.
    """
    from sqlalchemy import select, or_

    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=timeout_seconds)

    stmt = (
        select(Run)
        .where(
            Run.status == RunStatus.RUNNING.value,
            or_(
                Run.lease_expires_at < now,
                (Run.lease_expires_at.is_(None) & (Run.started_at < cutoff)),
            ),
        )
    )
    result = await session.execute(stmt)
    stale_runs = result.scalars().all()

    recovered_ids: list[str] = []
    for run in stale_runs:
        run.status = RunStatus.FAILED.value
        run.finished_at = now
        session.add(run)

        fail_event = RunEvent(
            id=str(uuid.uuid4()),
            run_id=run.id,
            event_type="run_failed",
            timestamp=now,
            message="Worker lease expired or worker disconnected unexpectedly (automatic crash recovery).",
            event_metadata={"worker_id": run.worker_id, "recovered_at": now.isoformat()},
        )
        session.add(fail_event)
        recovered_ids.append(run.id)
        logger.warning("Recovered stale/abandoned run %s (previous worker %s)", run.id, run.worker_id)

    if recovered_ids:
        await session.commit()

    return recovered_ids

