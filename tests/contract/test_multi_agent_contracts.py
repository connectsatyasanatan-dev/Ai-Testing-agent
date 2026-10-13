"""
tests/contract/test_multi_agent_contracts.py
============================================
Validates Pydantic serialization, constraints, and contracts for
the Multi-Agent AI Orchestration DAG architecture.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from uitp_contracts.enums import (
    AdapterType,
    AgentRole,
    IdempotencyClass,
    RiskLevel,
    RunStatus,
    TaskStatus,
)
from uitp_contracts.plan import (
    AssertionSchema,
    ContextExportDef,
    DAGTaskSchema,
    MissionDAGPlan,
    MissionEvaluationReport,
    MultiAgentMissionRequest,
    RequirementCoverageStatus,
    RetryPolicySchema,
    TestStepSchema,
    TokenUsage,
)


def _sample_step(step_id: str = "step_1") -> TestStepSchema:
    return TestStepSchema(
        step_id=step_id,
        adapter_type=AdapterType.HTTP_API,
        action="get",
        inputs={"path": "/health"},
        assertions=[AssertionSchema(type="status_code", value=200)],
        timeout_seconds=10.0,
        retry_policy=RetryPolicySchema(max_attempts=1),
        idempotency_class=IdempotencyClass.SAFE,
        risk_level=RiskLevel.LOW,
    )


def test_dag_task_schema_valid() -> None:
    task = DAGTaskSchema(
        task_id="setup_auth_token",
        name="Obtain test auth token",
        assigned_role=AgentRole.API_SPECIALIST,
        adapter_type=AdapterType.HTTP_API,
        depends_on=[],
        steps=[_sample_step("step_login")],
        exports={"token": ContextExportDef(json_path="$.auth.token", is_secret=True)},
        timeout_seconds=30.0,
        requirement_refs=["REQ-AUTH-01"],
    )
    assert task.task_id == "setup_auth_token"
    assert task.assigned_role == AgentRole.API_SPECIALIST
    assert task.exports["token"].is_secret is True


def test_dag_task_schema_invalid_id() -> None:
    with pytest.raises(ValidationError):
        # Invalid uppercase or spaces in task_id
        DAGTaskSchema(
            task_id="INVALID TASK ID",
            name="Invalid",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
        )


def test_mission_dag_plan_validation() -> None:
    task1 = DAGTaskSchema(
        task_id="task_1_api",
        name="API Setup",
        assigned_role=AgentRole.API_SPECIALIST,
        adapter_type=AdapterType.HTTP_API,
        steps=[_sample_step("s1")],
    )
    task2 = DAGTaskSchema(
        task_id="task_2_ui",
        name="UI Verification",
        assigned_role=AgentRole.UI_SPECIALIST,
        adapter_type=AdapterType.WEB,
        depends_on=["task_1_api"],
        steps=[],
    )
    plan = MissionDAGPlan(
        mission_id="mission_123",
        name="E2E User Journey Mission",
        tasks=[task1, task2],
        max_parallel_tasks=4,
    )
    assert len(plan.tasks) == 2
    assert plan.requires_approval is True
    assert plan.tasks[1].depends_on == ["task_1_api"]


def test_mission_evaluation_report_schema() -> None:
    report = MissionEvaluationReport(
        mission_id="mission_123",
        run_id="run_456",
        verdict=RunStatus.PASSED,
        total_tasks=2,
        passed_tasks=2,
        failed_tasks=0,
        blocked_tasks=0,
        requirement_coverage=[
            RequirementCoverageStatus(
                requirement_id="REQ-AUTH-01",
                status="PASSED",
                tested_by_tasks=["task_1_api"],
            )
        ],
        root_cause_analysis=[],
        executive_summary="All autonomous tests passed with 100% requirement coverage.",
        agent_telemetry={"total_tokens": 1540, "latency_ms": 3200},
    )
    assert report.verdict == RunStatus.PASSED
    assert report.passed_tasks == 2
    assert report.requirement_coverage[0].status == "PASSED"
