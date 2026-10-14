"""
tests/integration/test_smoke_and_regression_selection.py
========================================================
Comprehensive integration tests for Requirement-Driven Smoke and Regression Test Selection (Task 5).

Covers:
1. P0 requirements map to expected smoke tests.
2. Non-P0 requirements are not accidentally treated as P0.
3. Missing priority is handled predictably (defaults to P1 non-smoke).
4. Invalid model-proposed classification is rejected or corrected deterministically (no title-based inference).
5. Prerequisite dependency closure is preserved (upstream setup runs for downstream smoke targets).
6. Smoke-only, regression-only, and full-suite runs select the exact target tasks.
7. Skipped tasks are recorded as DAGTaskAttempt(status="skipped") and RunEvent(event_type="task_skipped").
8. Requirement traceability matrix accurately reflects tested vs excluded/untested requirements.
9. Backward compatibility with existing plans lacking priority metadata.
10. Execution log and Markdown report export include test suite and task priority metadata.
11. Unsupported suite filters are rejected safely.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import pytest
from httpx import AsyncClient
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_adapter_base.base import BaseAdapter, ExecutionContext, StepExecutionResult
from uitp_api.models.dag import DAGDependency, DAGTask, DAGTaskAttempt
from uitp_api.models.plan import TestPlan
from uitp_api.models.run import Run, RunEvent
from uitp_api.services.ai_provider import MockAIProvider, set_ai_provider
from uitp_api.services.multi_agent_service import (
    extract_requirement_priorities,
    plan_mission_dag,
    reconcile_and_validate_task_classifications,
)
from uitp_api.services.report_export_service import export_run_execution_artifacts
from uitp_api.services.run_service import execute_run_sync
from uitp_contracts.adapter import AdapterCapabilities
from uitp_contracts.enums import (
    AdapterType,
    AgentRole,
    StepStatus,
    TaskPriority,
    TaskStatus,
    TestSuiteType,
)
from uitp_contracts.export import ExecutionLogSchema
from uitp_contracts.plan import (
    AssertionSchema,
    DAGTaskSchema,
    MultiAgentMissionRequest,
    TestStepSchema,
    TokenUsage,
)
from uitp_orchestrator.dag_scheduler import compute_suite_execution_subgraph

TestSuiteType.__test__ = False


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "smoke-secure-pw-123"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "smoke-secure-pw-123"},
    )
    return resp.json()


class MockCustomAIProvider(MockAIProvider):
    def __init__(self, response_data: dict[str, Any]):
        super().__init__()
        self._response_data = response_data

    async def complete_structured(self, system_prompt: str, user_prompt: str) -> tuple[dict[str, Any], TokenUsage]:
        return self._response_data, TokenUsage(prompt_tokens=100, completion_tokens=150, total_tokens=250)


# ── Unit / Classification Rules Tests ─────────────────────────────────────────

def test_extract_requirement_priorities_from_markdown_and_scenarios():
    """Verify priority extraction handles P0, P1, P2, P3, and defaults missing to P1."""
    req_md = """
    # System Requirements
    - [REQ-AUTH-01] (Priority: P0, Criticality: High) Core user authentication
    - [REQ-PAY-01] [P0] Checkout payment processing
    - [REQ-ACC-01] Priority: P1 Account profile update
    - [REQ-REP-01] Priority: P2 Weekly report generation
    - [REQ-NOTIF-01] Priority: P3 Push notifications
    - [REQ-MISC-01] Miscellaneous feature with no priority specified
    """

    scenarios_json = json.dumps([
        {
            "id": "SCN-ORDER-01",
            "name": "Order placement flow",
            "priority": "P0",
            "criticality": "critical",
            "requirement_refs": ["REQ-ORDER-01"],
        },
        {
            "id": "SCN-SEARCH-01",
            "name": "Catalog search",
            "priority": "P2",
            "criticality": "medium",
            "requirement_refs": ["REQ-SEARCH-01"],
        },
    ])

    pri_map = extract_requirement_priorities(req_md, scenarios_json)

    assert pri_map["REQ-AUTH-01"] == TaskPriority.P0
    assert pri_map["REQ-PAY-01"] == TaskPriority.P0
    assert pri_map["REQ-ACC-01"] == TaskPriority.P1
    assert pri_map["REQ-REP-01"] == TaskPriority.P2
    assert pri_map["REQ-NOTIF-01"] == TaskPriority.P3
    # Missing priority defaults predictably to P1
    assert pri_map["REQ-MISC-01"] == TaskPriority.P1
    # Scenarios mapping
    assert pri_map["REQ-ORDER-01"] == TaskPriority.P0
    assert pri_map["REQ-SEARCH-01"] == TaskPriority.P2


def test_reconciliation_rejects_model_escalation_and_title_heuristic():
    """Verify deterministic reconciliation reverts invalid model P0 proposals without P0 requirement,

    and does NOT treat a test as P0 merely because its title contains 'smoke'.
    """
    req_priorities = {
        "REQ-CORE-LOGIN": TaskPriority.P0,
        "REQ-FOOTER-LINKS": TaskPriority.P2,
        "REQ-USER-PROFILE": TaskPriority.P1,
    }

    # Model maliciously or mistakenly escalates footer check to P0 and marks is_smoke=True
    tasks = [
        DAGTaskSchema(
            task_id="task_login",
            name="Authenticate Core User",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            requirement_refs=["REQ-CORE-LOGIN"],
            priority=TaskPriority.P0,
            is_smoke=True,
            is_regression=True,
        ),
        DAGTaskSchema(
            task_id="task_footer_smoke",
            name="Smoke check for UI footer links",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            requirement_refs=["REQ-FOOTER-LINKS"],
            # Invalid escalation by model:
            priority=TaskPriority.P0,
            is_smoke=True,
            is_regression=True,
        ),
        DAGTaskSchema(
            task_id="task_profile",
            name="Update user profile",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            requirement_refs=["REQ-USER-PROFILE"],
            priority=TaskPriority.P1,
            is_smoke=False,
            is_regression=True,
        ),
    ]

    reconciled = reconcile_and_validate_task_classifications(tasks, req_priorities)

    # Core login remains P0 and smoke
    assert reconciled[0].priority == TaskPriority.P0
    assert reconciled[0].is_smoke is True

    # Footer task was downgraded from P0 -> P2, and is_smoke was reverted to False despite 'Smoke' in title!
    assert reconciled[1].priority == TaskPriority.P2
    assert reconciled[1].is_smoke is False
    assert reconciled[1].is_regression is True

    # Profile remains P1
    assert reconciled[2].priority == TaskPriority.P1
    assert reconciled[2].is_smoke is False


def test_compute_suite_execution_subgraph_preserves_prerequisites():
    """Verify transitive dependency closure: upstream setup tasks execute as prerequisites."""
    # DAG:
    # task_setup (P1) -> task_auth (P0) -> task_checkout (P0) -> task_analytics (P2)
    #                                  \-> task_profile (P1)
    tasks = [
        DAGTaskSchema(
            task_id="task_setup",
            name="Database Seeding Setup",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            depends_on=[],
            priority=TaskPriority.P1,
            is_smoke=False,
            is_regression=True,
        ),
        DAGTaskSchema(
            task_id="task_auth",
            name="Login Authentication",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            depends_on=["task_setup"],
            priority=TaskPriority.P0,
            is_smoke=True,
            is_regression=True,
        ),
        DAGTaskSchema(
            task_id="task_checkout",
            name="Payment Checkout",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            depends_on=["task_auth"],
            priority=TaskPriority.P0,
            is_smoke=True,
            is_regression=True,
        ),
        DAGTaskSchema(
            task_id="task_analytics",
            name="Audit Analytics Generation",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            depends_on=["task_checkout"],
            priority=TaskPriority.P2,
            is_smoke=False,
            is_regression=True,
        ),
        DAGTaskSchema(
            task_id="task_profile",
            name="Edit Profile Settings",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            depends_on=["task_auth"],
            priority=TaskPriority.P1,
            is_smoke=False,
            is_regression=True,
        ),
    ]

    # 1. Smoke suite
    targets, prereqs, skipped = compute_suite_execution_subgraph(tasks, TestSuiteType.SMOKE)
    # Direct smoke targets: P0 tasks
    assert targets == {"task_auth", "task_checkout"}
    # Upstream dependency outside smoke subset is preserved as a prerequisite!
    assert prereqs == {"task_setup"}
    # Downstream / non-smoke tasks are skipped
    assert skipped == {"task_analytics", "task_profile"}

    # 2. Regression suite
    reg_targets, reg_prereqs, reg_skipped = compute_suite_execution_subgraph(tasks, TestSuiteType.REGRESSION)
    assert reg_targets == {"task_setup", "task_auth", "task_checkout", "task_analytics", "task_profile"}
    assert reg_prereqs == set()
    assert reg_skipped == set()

    # 3. Full suite ('all')
    all_targets, all_prereqs, all_skipped = compute_suite_execution_subgraph(tasks, TestSuiteType.ALL)
    assert len(all_targets) == 5
    assert all_prereqs == set()
    assert all_skipped == set()

    # 4. Invalid suite filter rejects safely
    with pytest.raises(ValueError, match="Unsupported suite filter"):
        compute_suite_execution_subgraph(tasks, "chaos_testing")


# ── Full Execution Pipeline Tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_smoke_only_run_executes_targets_and_prerequisites_while_skipping_others(
    client: AsyncClient,
    db_session: AsyncSession,
    tmp_path: Path,
):
    """Integration test: Running a mission with suite_type='smoke' executes P0 targets

    and P1 prerequisite, marks others SKIPPED, and records proper attempts and export report.
    """
    user_info = await _register_and_login(client, "smoke-lead@uitp.io")

    # 1. Create project & environment
    proj_resp = await client.post(
        "/api/v1/projects",
        json={"name": "Smoke & Regression Test Suite Project"},
    )
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

    env_resp = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        json={
            "name": "Staging",
            "base_url": "http://mock-api.local",
            "allowlist": ["mock-api.local"],
        },
    )
    assert env_resp.status_code == 201
    env_id = env_resp.json()["id"]

    # 2. Plan Mission with 3 tasks:
    # - task_setup: P1 (setup auth prerequisite)
    # - task_payment: P0 (smoke target, depends on task_setup)
    # - task_export: P2 (regression, depends on task_payment)
    mission_raw = {
        "name": "Requirement-Driven Smoke Mission",
        "description": "Verifies smoke subset isolation and prerequisite handling",
        "tasks": [
            {
                "task_id": "task_setup",
                "name": "Seed Test Account via API",
                "assigned_role": "api_specialist",
                "adapter_type": "http_api",
                "depends_on": [],
                "requirement_refs": ["REQ-SETUP-01"],  # P1
                "steps": [
                    {
                        "step_id": "seed_account",
                        "adapter_type": "http_api",
                        "action": "post",
                        "inputs": {"url": "http://mock-api.local/seed"},
                        "assertions": [{"type": "status_code", "operator": "equals", "value": 201}],
                    }
                ],
                "exports": {},
            },
            {
                "task_id": "task_payment",
                "name": "Process Critical Payment",
                "assigned_role": "api_specialist",
                "adapter_type": "http_api",
                "depends_on": ["task_setup"],
                "requirement_refs": ["REQ-PAY-P0"],  # P0
                "steps": [
                    {
                        "step_id": "checkout_step",
                        "adapter_type": "http_api",
                        "action": "post",
                        "inputs": {"url": "http://mock-api.local/checkout"},
                        "assertions": [{"type": "status_code", "operator": "equals", "value": 200}],
                    }
                ],
                "exports": {},
            },
            {
                "task_id": "task_export",
                "name": "Export Audit PDF",
                "assigned_role": "api_specialist",
                "adapter_type": "http_api",
                "depends_on": ["task_payment"],
                "requirement_refs": ["REQ-AUDIT-P2"],  # P2
                "steps": [
                    {
                        "step_id": "export_step",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {"url": "http://mock-api.local/audit"},
                        "assertions": [{"type": "status_code", "operator": "equals", "value": 200}],
                    }
                ],
                "exports": {},
            },
        ],
    }

    mock_provider = MockCustomAIProvider(mission_raw)
    req_content = """
    # Requirements
    - [REQ-SETUP-01] Priority: P1 Account seeding helper
    - [REQ-PAY-P0] (Priority: P0, Criticality: High) Critical checkout transaction
    - [REQ-AUDIT-P2] Priority: P2 Audit report download
    """

    plan_req = MultiAgentMissionRequest(
        requirements_content=req_content,
        environment_id=env_id,
    )
    plan_dag = await plan_mission_dag(
        db_session,
        project_id=project_id,
        actor_id=user_info["id"],
        request=plan_req,
        provider=mock_provider,
    )

    # Verify classification during planning
    tasks_by_id = {t.task_id: t for t in plan_dag.tasks}
    assert tasks_by_id["task_setup"].priority == TaskPriority.P1
    assert tasks_by_id["task_setup"].is_smoke is False
    assert tasks_by_id["task_payment"].priority == TaskPriority.P0
    assert tasks_by_id["task_payment"].is_smoke is True
    assert tasks_by_id["task_export"].priority == TaskPriority.P2
    assert tasks_by_id["task_export"].is_smoke is False

    # 3. Approve plan
    appr_resp = await client.post(
        f"/api/v1/plans/{plan_dag.mission_id}/approve",
        json={"version": 1},
    )
    assert appr_resp.status_code == 200

    # 4. Mock transport responding with 200/201
    def handler(request: httpx.Request) -> httpx.Response:
        if "seed" in str(request.url):
            return httpx.Response(201, json={"status": "seeded"})
        if "checkout" in str(request.url):
            return httpx.Response(200, json={"status": "paid"})
        return httpx.Response(200, json={"status": "exported"})

    mock_transport = httpx.MockTransport(handler)

    # 5. Execute run with suite_type='smoke'
    run_record = await execute_run_sync(
        db_session,
        plan_id=plan_dag.mission_id,
        environment_id=env_id,
        actor_id=user_info["id"],
        transport=mock_transport,
        suite_type=TestSuiteType.SMOKE,
    )

    assert run_record.status == "passed"
    assert run_record.suite_type == "smoke"

    # 6. Verify task attempt statuses in DB
    att_stmt = select(DAGTaskAttempt).where(DAGTaskAttempt.run_id == run_record.id)
    attempts = (await db_session.execute(att_stmt)).scalars().all()
    att_map = {a.task_id: a.status for a in attempts}

    # Prerequisite task_setup ran and passed
    assert att_map["task_setup"] == "passed"
    # Target smoke task_payment ran and passed
    assert att_map["task_payment"] == "passed"
    # Unselected task_export was safely SKIPPED
    assert att_map["task_export"] == "skipped"

    # 7. Verify RunEvents
    events_stmt = select(RunEvent).where(RunEvent.run_id == run_record.id)
    events = (await db_session.execute(events_stmt)).scalars().all()
    event_types = [e.event_type for e in events]

    assert "task_started" in event_types
    assert "task_passed" in event_types
    assert "task_skipped" in event_types

    # 8. Export artifacts and verify reports
    export_res = await export_run_execution_artifacts(
        db_session,
        run_id=run_record.id,
        output_dir=tmp_path / "artifacts",
    )
    assert Path(export_res.execution_log_path).exists()
    assert Path(export_res.report_path).exists()

    with open(export_res.execution_log_path, "r", encoding="utf-8") as f:
        log_json = json.load(f)

    # Verify execution log metadata
    assert log_json["suite_type"] == "smoke"
    assert log_json["summary"]["skipped_tasks"] == 1
    assert log_json["summary"]["passed_tasks"] == 2

    # Verify task classifications in execution log
    tasks_in_log = {t["task_id"]: t for t in log_json["tasks"]}
    assert tasks_in_log["task_payment"]["priority"] == "P0"
    assert tasks_in_log["task_payment"]["is_smoke"] is True
    assert tasks_in_log["task_export"]["status"] == "skipped"

    # Verify requirement traceability in log
    trace_map = {r["requirement_id"]: r for r in log_json["requirement_traceability"]}
    assert trace_map["REQ-PAY-P0"]["status"] == "PASSED"
    assert trace_map["REQ-AUDIT-P2"]["status"] == "UNTESTED"
    assert "Excluded from 'smoke' suite execution" in trace_map["REQ-AUDIT-P2"]["notes"]

    # Verify Markdown report formatting
    report_text = Path(export_res.report_path).read_text(encoding="utf-8")
    assert "- **Test Suite:** `smoke`" in report_text
    assert "Process Critical Payment" in report_text
    assert "Excluded from 'smoke' suite execution" in report_text


@pytest.mark.asyncio
async def test_full_and_regression_suite_runs_execute_all_eligible_tasks(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """Verify regression and all suites execute all non-excluded tasks."""
    user_info = await _register_and_login(client, "regression-lead@uitp.io")

    proj_resp = await client.post(
        "/api/v1/projects",
        json={"name": "Regression Suite Run Project"},
    )
    project_id = proj_resp.json()["id"]

    env_resp = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        json={
            "name": "Staging",
            "base_url": "http://mock-api.local",
            "allowlist": ["mock-api.local"],
        },
    )
    env_id = env_resp.json()["id"]

    mission_raw = {
        "name": "Full Suite Mission",
        "tasks": [
            {
                "task_id": "task_t1",
                "name": "Task T1",
                "assigned_role": "api_specialist",
                "adapter_type": "http_api",
                "depends_on": [],
                "requirement_refs": ["REQ-REG-01"],
                "steps": [
                    {
                        "step_id": "step_t1",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {"url": "http://mock-api.local/t1"},
                        "assertions": [{"type": "status_code", "operator": "equals", "value": 200}],
                    }
                ],
            },
            {
                "task_id": "task_t2",
                "name": "Task T2",
                "assigned_role": "api_specialist",
                "adapter_type": "http_api",
                "depends_on": ["task_t1"],
                "requirement_refs": ["REQ-REG-02"],
                "steps": [
                    {
                        "step_id": "step_t2",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {"url": "http://mock-api.local/t2"},
                        "assertions": [{"type": "status_code", "operator": "equals", "value": 200}],
                    }
                ],
            },
        ],
    }

    mock_provider = MockCustomAIProvider(mission_raw)
    plan_dag = await plan_mission_dag(
        db_session,
        project_id=project_id,
        actor_id=user_info["id"],
        request=MultiAgentMissionRequest(requirements_content="REQ-REG-01\nREQ-REG-02", environment_id=env_id),
        provider=mock_provider,
    )

    appr_resp = await client.post(f"/api/v1/plans/{plan_dag.mission_id}/approve", json={"version": 1})
    assert appr_resp.status_code == 200

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    mock_transport = httpx.MockTransport(handler)

    # Execute with suite_type='regression'
    run_reg = await execute_run_sync(
        db_session,
        plan_id=plan_dag.mission_id,
        environment_id=env_id,
        actor_id=user_info["id"],
        transport=mock_transport,
        suite_type=TestSuiteType.REGRESSION,
    )

    assert run_reg.status == "passed"
    assert run_reg.suite_type == "regression"

    att_stmt = select(DAGTaskAttempt).where(DAGTaskAttempt.run_id == run_reg.id)
    attempts = (await db_session.execute(att_stmt)).scalars().all()
    assert len(attempts) == 2
    assert all(a.status == "passed" for a in attempts)


@pytest.mark.asyncio
async def test_api_route_supports_suite_type_in_run_creation(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """Verify POST /api/v1/plans/{plan_id}/runs parses suite_type and returns in RunResponse."""
    user_info = await _register_and_login(client, "api-suite@uitp.io")

    proj_resp = await client.post(
        "/api/v1/projects",
        json={"name": "API Suite Test Project"},
    )
    project_id = proj_resp.json()["id"]

    env_resp = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        json={
            "name": "Staging",
            "base_url": "http://mock-api.local",
            "allowlist": ["mock-api.local"],
        },
    )
    env_id = env_resp.json()["id"]

    mission_raw = {
        "name": "API Route Mission",
        "tasks": [
            {
                "task_id": "task_api_smoke",
                "name": "API Smoke Task",
                "assigned_role": "api_specialist",
                "adapter_type": "http_api",
                "depends_on": [],
                "requirement_refs": ["REQ-P0-API"],
                "steps": [
                    {
                        "step_id": "step_smoke",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {"url": "http://mock-api.local/smoke"},
                        "assertions": [{"type": "status_code", "operator": "equals", "value": 200}],
                    }
                ],
            }
        ],
    }

    mock_provider = MockCustomAIProvider(mission_raw)
    plan_dag = await plan_mission_dag(
        db_session,
        project_id=project_id,
        actor_id=user_info["id"],
        request=MultiAgentMissionRequest(
            requirements_content="- [REQ-P0-API] (Priority: P0) Core API endpoint",
            environment_id=env_id,
        ),
        provider=mock_provider,
    )

    appr_resp = await client.post(f"/api/v1/plans/{plan_dag.mission_id}/approve", json={"version": 1})
    assert appr_resp.status_code == 200

    # Request run with suite_type="smoke"
    run_resp = await client.post(
        f"/api/v1/plans/{plan_dag.mission_id}/runs",
        json={
            "environment_id": env_id,
            "suite_type": "smoke",
        },
    )
    assert run_resp.status_code == 201
    run_data = run_resp.json()
    assert run_data["suite_type"] == "smoke"

    # Reject unsupported suite filter safely with 422
    bad_resp = await client.post(
        f"/api/v1/plans/{plan_dag.mission_id}/runs",
        json={
            "environment_id": env_id,
            "suite_type": "non_existent_suite",
        },
    )
    assert bad_resp.status_code == 422


@pytest.mark.asyncio
async def test_backward_compatibility_with_existing_plans_lacking_priority(
    client: AsyncClient,
    db_session: AsyncSession,
):
    """Verify plans created prior to priority fields remain completely backward compatible."""
    # Test constructing DAGTaskSchema without priority, is_smoke, or is_regression
    legacy_task = DAGTaskSchema(
        task_id="legacy_task",
        name="Legacy Task Without Priority",
        assigned_role=AgentRole.API_SPECIALIST,
        adapter_type=AdapterType.HTTP_API,
        depends_on=[],
        steps=[],
    )
    assert legacy_task.priority == TaskPriority.P1
    assert legacy_task.is_smoke is False
    assert legacy_task.is_regression is True

    # Test suite subgraph calculation on legacy tasks
    targets, prereqs, skipped = compute_suite_execution_subgraph([legacy_task], TestSuiteType.SMOKE)
    assert targets == set()
    assert skipped == {"legacy_task"}

    all_targets, all_prereqs, all_skipped = compute_suite_execution_subgraph([legacy_task], TestSuiteType.ALL)
    assert all_targets == {"legacy_task"}
    assert all_skipped == set()


def test_mixed_journey_classification_and_behavior():
    """Verify behavior of mixed user journeys:

    - Single task covering both P0 and P1 requirements is marked P0 (criticality takes precedence).
    - Multi-task journey keeps P0 steps in smoke suite while non-critical steps are isolated to regression.
    """
    req_priorities = {
        "REQ-LOGIN-P0": TaskPriority.P0,
        "REQ-NOTIFICATION-P2": TaskPriority.P2,
        "REQ-PROFILE-P1": TaskPriority.P1,
    }

    # Case A: Mixed requirements inside a single atomic task
    atomic_mixed_task = DAGTaskSchema(
        task_id="task_atomic_mixed",
        name="Atomic Checkout and Optional Newsletter",
        assigned_role=AgentRole.API_SPECIALIST,
        adapter_type=AdapterType.HTTP_API,
        requirement_refs=["REQ-LOGIN-P0", "REQ-NOTIFICATION-P2"],
    )
    reconciled = reconcile_and_validate_task_classifications([atomic_mixed_task], req_priorities)
    # The presence of P0 requirement ensures the critical path is not omitted from smoke
    assert reconciled[0].priority == TaskPriority.P0
    assert reconciled[0].is_smoke is True

    # Case B: Mixed journey decomposed into separate tasks
    decomposed_journey = [
        DAGTaskSchema(
            task_id="task_auth_step",
            name="Login Step",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            requirement_refs=["REQ-LOGIN-P0"],
        ),
        DAGTaskSchema(
            task_id="task_profile_step",
            name="Update Profile Step",
            assigned_role=AgentRole.API_SPECIALIST,
            adapter_type=AdapterType.HTTP_API,
            depends_on=["task_auth_step"],
            requirement_refs=["REQ-PROFILE-P1"],
        ),
    ]
    reconciled_decomp = reconcile_and_validate_task_classifications(decomposed_journey, req_priorities)
    assert reconciled_decomp[0].priority == TaskPriority.P0
    assert reconciled_decomp[0].is_smoke is True
    assert reconciled_decomp[1].priority == TaskPriority.P1
    assert reconciled_decomp[1].is_smoke is False

    # In smoke suite, only task_auth_step is targeted, and non-critical profile update is omitted
    targets, prereqs, skipped = compute_suite_execution_subgraph(reconciled_decomp, TestSuiteType.SMOKE)
    assert targets == {"task_auth_step"}
    assert skipped == {"task_profile_step"}

