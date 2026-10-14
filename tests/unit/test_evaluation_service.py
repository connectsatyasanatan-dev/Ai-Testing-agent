"""
tests/unit/test_evaluation_service.py
=====================================
Unit tests for post-run evaluation and evidence-based root cause analysis (Task 8):
  1. Authoritative evaluation lifecycle:
     - Success runs (verdict=PASSED)
     - Failure runs with assertion mismatches (verdict=FAILED, cause_type=confirmed)
     - Blocked runs with dependency chain tracing (verdict=BLOCKED)
     - Cancelled and partial runs (verdict=CANCELLED)
  2. Idempotency: duplicate evaluation calls return cached results with is_idempotent_replay=True.
  3. Fault tolerance: evaluator errors never erase or alter original run.status (non-destructive).
  4. Requirement coverage: maps outcomes back to requirement IDs; tags unmapped requirements as UNTESTED.
  5. Secret redaction: passwords, auth tokens, and secret keys scrubbed from prompts and diagnostics.
  6. Optional AI summarization: attaches hypotheses without altering verdicts; falls back deterministically on failure.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from uitp_api.database import Base
from uitp_api.models.dag import DAGDependency, DAGTask, DAGTaskAttempt
from uitp_api.models.plan import TestPlan
from uitp_api.models.project import Project
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.models.user import User
from uitp_api.services.evaluation_service import (
    _classify_cause_and_diagnostic,
    _sanitize_for_prompt,
    _synthesize_optional_ai_summary,
    evaluate_and_persist_run,
)
from uitp_contracts.enums import (
    AdapterType,
    ErrorCode,
    PlanStatus,
    RunStatus,
    StepStatus,
    TaskStatus,
)
from uitp_contracts.plan import MissionEvaluationReport


@pytest.fixture
async def in_memory_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
async def setup_test_context(in_memory_session: AsyncSession) -> dict[str, Any]:
    session = in_memory_session
    user_id = str(uuid.uuid4())
    user = User(
        id=user_id,
        email="evaluator@test.com",
        password_hash="hashed_pw_test",
        full_name="Evaluator User",
        is_active=True,
    )
    project = Project(
        id=str(uuid.uuid4()),
        name="Eval Project",
        owner_id=user.id,
        status="active",
    )
    plan = TestPlan(
        id=str(uuid.uuid4()),
        project_id=project.id,
        plan_id="plan-eval-01",
        version=1,
        name="Evaluation Test Plan",
        adapter_type="multi_agent_dag",
        status=PlanStatus.APPROVED.value,
        created_by=user.id,
    )
    session.add_all([user, project, plan])
    await session.commit()
    return {"user": user, "project": project, "plan": plan}


# ============================================================================
# 1. Lifecycle & Verdict Calculation Tests
# ============================================================================


@pytest.mark.asyncio
async def test_evaluation_lifecycle_success_run(
    in_memory_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    session = in_memory_session
    ctx = setup_test_context
    run = Run(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        plan_version=1,
        environment_id=str(uuid.uuid4()),
        project_id=ctx["project"].id,
        requested_by=ctx["user"].id,
        status=RunStatus.PASSED.value,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    task1 = DAGTask(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        task_id="task_auth",
        name="Auth Task",
        assigned_role="api_tester",
        adapter_type="http_api",
        requirement_refs=["REQ-AUTH-01"],
    )
    task2 = DAGTask(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        task_id="task_data",
        name="Data Task",
        assigned_role="api_tester",
        adapter_type="http_api",
        requirement_refs=["REQ-DATA-02"],
    )
    session.add_all([run, task1, task2])
    await session.commit()

    att1 = DAGTaskAttempt(
        id=str(uuid.uuid4()),
        run_id=run.id,
        task_id="task_auth",
        status=TaskStatus.PASSED.value,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    att2 = DAGTaskAttempt(
        id=str(uuid.uuid4()),
        run_id=run.id,
        task_id="task_data",
        status=TaskStatus.PASSED.value,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add_all([att1, att2])
    await session.commit()

    report = await evaluate_and_persist_run(
        session,
        run_id=run.id,
        actor_id=ctx["user"].id,
        declared_requirements=["REQ-AUTH-01", "REQ-DATA-02", "REQ-EXTRA-03"],
    )

    assert report.verdict == RunStatus.PASSED
    assert report.total_tasks == 2
    assert report.passed_tasks == 2
    assert report.failed_tasks == 0
    assert report.blocked_tasks == 0

    # REQ-AUTH-01 and REQ-DATA-02 should be PASSED; REQ-EXTRA-03 has no tasks and is UNTESTED
    req_map = {r.requirement_id: r for r in report.requirement_coverage}
    assert req_map["REQ-AUTH-01"].status == "PASSED"
    assert req_map["REQ-DATA-02"].status == "PASSED"
    assert req_map["REQ-EXTRA-03"].status == "UNTESTED"
    assert "No executable tasks" in req_map["REQ-EXTRA-03"].notes


@pytest.mark.asyncio
async def test_evaluation_lifecycle_failed_run_with_assertions(
    in_memory_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    session = in_memory_session
    ctx = setup_test_context
    run = Run(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        plan_version=1,
        environment_id=str(uuid.uuid4()),
        project_id=ctx["project"].id,
        requested_by=ctx["user"].id,
        status=RunStatus.FAILED.value,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    task = DAGTask(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        task_id="task_failing",
        name="Failing Task",
        assigned_role="api_tester",
        adapter_type="http_api",
        requirement_refs=["REQ-AUTH-01"],
        steps=[{"step_id": "step_assert"}],
    )
    session.add_all([run, task])
    await session.commit()

    att = DAGTaskAttempt(
        id=str(uuid.uuid4()),
        run_id=run.id,
        task_id="task_failing",
        status=TaskStatus.FAILED.value,
        error_message="Assertion failed: status_code expected 200, got 500",
    )
    step_att = StepAttempt(
        id=str(uuid.uuid4()),
        run_id=run.id,
        step_id="step_assert",
        attempt_number=1,
        status=StepStatus.FAILED.value,
        error_code=ErrorCode.ASSERTION_FAILED.value,
        error_message="Assertion failed: status_code expected 200, got 500",
    )
    session.add_all([att, step_att])
    await session.commit()

    report = await evaluate_and_persist_run(session, run_id=run.id, actor_id=ctx["user"].id)

    assert report.verdict == RunStatus.FAILED
    assert report.failed_tasks == 1
    assert len(report.root_cause_analysis) == 1

    rc = report.root_cause_analysis[0]
    assert rc["task_id"] == "task_failing"
    assert rc["cause_type"] == "confirmed"
    assert "Assertion failure in step 'step_assert'" in rc["diagnostic"]
    assert len(rc["suggested_debugging_steps"]) > 0

    req_map = {r.requirement_id: r for r in report.requirement_coverage}
    assert req_map["REQ-AUTH-01"].status == "FAILED"
    assert "Failed in verifying task(s): task_failing" in req_map["REQ-AUTH-01"].notes


@pytest.mark.asyncio
async def test_evaluation_lifecycle_blocked_run_dependency_chain(
    in_memory_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    session = in_memory_session
    ctx = setup_test_context
    run = Run(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        plan_version=1,
        environment_id=str(uuid.uuid4()),
        project_id=ctx["project"].id,
        requested_by=ctx["user"].id,
        status=RunStatus.FAILED.value,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    task1 = DAGTask(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        task_id="task_parent",
        name="Parent Task",
        assigned_role="api_tester",
        adapter_type="http_api",
        requirement_refs=["REQ-AUTH-01"],
    )
    task2 = DAGTask(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        task_id="task_child",
        name="Child Task",
        assigned_role="api_tester",
        adapter_type="http_api",
        requirement_refs=["REQ-DATA-02"],
    )
    dep = DAGDependency(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        parent_task_id="task_parent",
        child_task_id="task_child",
    )
    session.add_all([run, task1, task2, dep])
    await session.commit()

    att1 = DAGTaskAttempt(
        id=str(uuid.uuid4()),
        run_id=run.id,
        task_id="task_parent",
        status=TaskStatus.FAILED.value,
        error_message="Database seed failed",
    )
    att2 = DAGTaskAttempt(
        id=str(uuid.uuid4()),
        run_id=run.id,
        task_id="task_child",
        status=TaskStatus.BLOCKED.value,
        error_message="Blocked by parent task_parent",
    )
    session.add_all([att1, att2])
    await session.commit()

    report = await evaluate_and_persist_run(session, run_id=run.id, actor_id=ctx["user"].id)

    assert report.verdict == RunStatus.FAILED
    assert report.failed_tasks == 1
    assert report.blocked_tasks == 1

    rc_map = {rc["task_id"]: rc for rc in report.root_cause_analysis}
    assert rc_map["task_child"]["cause_type"] == "confirmed"
    assert "upstream prerequisite failure in: task_parent (failed)" in rc_map["task_child"]["diagnostic"]
    assert "task_parent" in rc_map["task_child"]["dependency_chain"]


@pytest.mark.asyncio
async def test_evaluation_lifecycle_cancelled_run(
    in_memory_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    session = in_memory_session
    ctx = setup_test_context
    run = Run(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        plan_version=1,
        environment_id=str(uuid.uuid4()),
        project_id=ctx["project"].id,
        requested_by=ctx["user"].id,
        status=RunStatus.CANCELLED.value,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    task = DAGTask(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        task_id="task_cancelled",
        name="Cancelled Task",
        assigned_role="api_tester",
        adapter_type="http_api",
        requirement_refs=["REQ-AUTH-01"],
    )
    session.add_all([run, task])
    await session.commit()

    report = await evaluate_and_persist_run(session, run_id=run.id, actor_id=ctx["user"].id)

    assert report.verdict == RunStatus.CANCELLED
    assert report.cancelled_tasks == 1
    req_map = {r.requirement_id: r for r in report.requirement_coverage}
    assert req_map["REQ-AUTH-01"].status == "UNTESTED"
    assert "cancelled before task completion" in req_map["REQ-AUTH-01"].notes


# ============================================================================
# 2. Idempotency & Fault Tolerance Tests
# ============================================================================


@pytest.mark.asyncio
async def test_evaluation_idempotency_returns_cached_report(
    in_memory_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    session = in_memory_session
    ctx = setup_test_context
    run = Run(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        plan_version=1,
        environment_id=str(uuid.uuid4()),
        project_id=ctx["project"].id,
        requested_by=ctx["user"].id,
        status=RunStatus.PASSED.value,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add(run)
    await session.commit()

    # Initial evaluation
    report1 = await evaluate_and_persist_run(session, run_id=run.id, actor_id=ctx["user"].id)
    assert not report1.is_idempotent_replay

    # Second evaluation without force
    report2 = await evaluate_and_persist_run(session, run_id=run.id, actor_id=ctx["user"].id)
    assert report2.is_idempotent_replay is True
    assert report2.mission_id == report1.mission_id
    assert report2.verdict == report1.verdict


@pytest.mark.asyncio
async def test_evaluation_failure_non_destructive(
    in_memory_session: AsyncSession,
    setup_test_context: dict[str, Any],
) -> None:
    session = in_memory_session
    ctx = setup_test_context
    run = Run(
        id=str(uuid.uuid4()),
        plan_id=ctx["plan"].id,
        plan_version=1,
        environment_id=str(uuid.uuid4()),
        project_id=ctx["project"].id,
        requested_by=ctx["user"].id,
        status=RunStatus.PASSED.value,
        started_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
    )
    session.add(run)
    await session.commit()

    # If evaluation raises an unhandled error, original run status MUST be preserved
    invalid_run_id = str(uuid.uuid4())
    with pytest.raises(Exception):
        await evaluate_and_persist_run(session, run_id=invalid_run_id)

    # Original run remains completely unaffected
    reloaded_run = await session.get(Run, run.id)
    assert reloaded_run is not None
    assert reloaded_run.status == RunStatus.PASSED.value


# ============================================================================
# 3. Sanitization & Optional AI Synthesis Tests
# ============================================================================


def test_sanitize_for_prompt_scrubs_secrets() -> None:
    raw = {
        "task_id": "task_login",
        "diagnostic": "Failed with Bearer eyJhbGciOi...",
        "payload": {
            "username": "tester",
            "password": "SuperSecretPassword123!",
            "auth_token": "secret-token-xyz",
            "session_cookie": "sess-abc",
        },
    }
    sanitized = _sanitize_for_prompt(raw)
    assert sanitized["payload"]["username"] == "tester"
    assert sanitized["payload"]["password"] == "[REDACTED]"
    assert sanitized["payload"]["auth_token"] == "[REDACTED]"
    assert sanitized["payload"]["session_cookie"] == "[REDACTED]"
    assert "SuperSecretPassword123!" not in str(sanitized)


@pytest.mark.asyncio
async def test_optional_ai_summary_success() -> None:
    deterministic_causes = [
        {
            "task_id": "task_payment",
            "status": "failed",
            "diagnostic": "HTTP 502 Bad Gateway from payment service",
        }
    ]
    mock_provider = MagicMock()
    mock_provider.complete_structured = AsyncMock(
        return_value=(
            {
                "summary": "Payment gateway was unreachable during checkout test.",
                "hypotheses": [
                    {
                        "task_id": "task_payment",
                        "hypothesis": "Downstream payment microservice gateway timed out.",
                        "confidence": "high",
                    }
                ],
            },
            MagicMock(),
        )
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("uitp_api.services.evaluation_service.get_ai_provider", lambda: mock_provider)
        ai_result = await _synthesize_optional_ai_summary(deterministic_causes, 1, 1, 0)

    assert ai_result is not None
    assert ai_result["is_ai_generated"] is True
    assert "Payment gateway was unreachable" in ai_result["ai_summary"]
    assert len(ai_result["hypotheses"]) == 1


@pytest.mark.asyncio
async def test_optional_ai_summary_graceful_fallback_on_error() -> None:
    deterministic_causes = [{"task_id": "t1", "diagnostic": "Error"}]
    mock_provider = MagicMock()
    mock_provider.complete_structured = AsyncMock(side_effect=RuntimeError("AI provider quota exhausted"))

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("uitp_api.services.evaluation_service.get_ai_provider", lambda: mock_provider)
        ai_result = await _synthesize_optional_ai_summary(deterministic_causes, 1, 1, 0)

    # Should gracefully return None without raising exception
    assert ai_result is None
