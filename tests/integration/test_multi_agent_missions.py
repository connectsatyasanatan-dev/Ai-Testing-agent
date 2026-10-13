"""
tests/integration/test_multi_agent_missions.py
==============================================
Integration tests for Autonomous Multi-Agent AI Orchestration Architecture:
- Supervisor/Planner task DAG generation and decomposition
- Topological validation and cycle rejection
- Strict code token rejection
- Database-backed task and dependency persistence
- DAG retrieval and human approval gate
- Evaluator/Reporter evidence-based synthesis
- Project tenancy isolation
"""
from __future__ import annotations

from typing import Any
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.dag import DAGDependency, DAGTask, DAGTaskAttempt
from uitp_api.models.plan import TestPlan
from uitp_api.models.run import Run
from uitp_api.services.ai_provider import MockAIProvider, set_ai_provider
from uitp_contracts.enums import AgentRole, RunStatus, TaskStatus


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "multi-agent-secure-pw"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "multi-agent-secure-pw"},
    )
    return resp.json()


class CustomMockAIProvider(MockAIProvider):
    """Mock provider with customizable structured output for testing."""

    def __init__(self, response_data: dict[str, Any]):
        super().__init__()
        self._response_data = response_data

    async def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ):
        from uitp_contracts.plan import TokenUsage
        return self._response_data, TokenUsage(prompt_tokens=300, completion_tokens=500, total_tokens=800)


@pytest.mark.integration
class TestMultiAgentMissions:
    @pytest.fixture(autouse=True)
    def reset_ai_provider(self):
        set_ai_provider(None)
        yield
        set_ai_provider(None)

    @pytest.mark.asyncio
    async def test_multi_agent_plan_503_when_no_provider(self, client: AsyncClient) -> None:
        """Returns 503 AI_PROVIDER_NOT_CONFIGURED when no AI provider is set."""
        await _register_and_login(client, "user_multi_503@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "MultiAgent 503 Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://localhost:8000"},
        )
        env_id = env_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "# Feature Requirements\n1. User logs in\n2. User fetches profile",
                "environment_id": env_id,
            },
        )
        assert resp.status_code == 503
        data = resp.json()
        assert data["error"]["code"] == "AI_PROVIDER_NOT_CONFIGURED"

    @pytest.mark.asyncio
    async def test_multi_agent_plan_generation_success(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Supervisor agent plans a multi-agent DAG with API Specialist and UI Specialist tasks."""
        mock_plan = {
            "name": "E2E Authentication and Profile Mission",
            "description": "Multi-agent test of authentication and web dashboard",
            "max_parallel_tasks": 4,
            "tasks": [
                {
                    "task_id": "api_auth",
                    "name": "API Authentication Test",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "auth_post",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/api/v1/auth/login", "json": {"user": "test", "pw": "test"}},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                            "retry_policy": {"max_attempts": 2, "backoff_coefficient": 1.5, "allow_non_idempotent_retry": False},
                            "idempotency_class": "non_idempotent",
                            "risk_level": "low",
                        }
                    ],
                    "exports": {
                        "jwt_token": {"json_path": "$.token", "is_secret": True}
                    },
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-AUTH-1"],
                },
                {
                    "task_id": "ui_profile",
                    "name": "UI Profile Verification",
                    "assigned_role": "ui_specialist",
                    "adapter_type": "web",
                    "depends_on": ["api_auth"],
                    "steps": [
                        {
                            "step_id": "nav_profile",
                            "adapter_type": "web",
                            "action": "navigate",
                            "inputs": {"url": "http://localhost:8000/profile"},
                            "assertions": [{"type": "title_contains", "value": "Profile"}],
                            "timeout_seconds": 15.0,
                            "retry_policy": {"max_attempts": 1, "backoff_coefficient": 1.0, "allow_non_idempotent_retry": False},
                            "idempotency_class": "safe",
                            "risk_level": "low",
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 45.0,
                    "requirement_refs": ["REQ-UI-1"],
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "supervisor_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Mission Orchestration Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://localhost:8000"},
        )
        env_id = env_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "# Specification\n1. Login via API\n2. Navigate UI",
                "openapi_spec": '{"openapi": "3.0.0", "paths": {}}',
                "scenarios_content": '[{"scenario": "E2E Login"}]',
                "target_urls": ["http://localhost:8000"],
                "environment_id": env_id,
                "max_parallel_tasks": 4,
            },
        )
        assert resp.status_code == 201
        data = resp.json()

        assert data["requires_approval"] is True
        assert len(data["tasks"]) == 2
        mission_id = data["mission_id"]

        task_api = next(t for t in data["tasks"] if t["task_id"] == "api_auth")
        task_ui = next(t for t in data["tasks"] if t["task_id"] == "ui_profile")
        assert task_api["assigned_role"] == AgentRole.API_SPECIALIST.value
        assert task_ui["assigned_role"] == AgentRole.UI_SPECIALIST.value
        assert "api_auth" in task_ui["depends_on"]
        assert "jwt_token" in task_api["exports"]
        assert task_api["exports"]["jwt_token"]["is_secret"] is True

        # Verify DB records
        plan_row = (await db_session.execute(select(TestPlan).where(TestPlan.id == mission_id))).scalar_one_or_none()
        assert plan_row is not None
        assert plan_row.adapter_type == "multi_agent_dag"
        assert plan_row.status == "draft"

        tasks_rows = (await db_session.execute(select(DAGTask).where(DAGTask.plan_id == mission_id))).scalars().all()
        assert len(tasks_rows) == 2

        deps_rows = (await db_session.execute(select(DAGDependency).where(DAGDependency.plan_id == mission_id))).scalars().all()
        assert len(deps_rows) == 1
        assert deps_rows[0].parent_task_id == "api_auth"
        assert deps_rows[0].child_task_id == "ui_profile"

    @pytest.mark.asyncio
    async def test_multi_agent_plan_cycle_rejection(self, client: AsyncClient) -> None:
        """Kahn's algorithm detects circular dependencies and rejects with 422 ValidationError."""
        cyclic_plan = {
            "name": "Cyclic Plan",
            "description": "Invalid cycle plan",
            "max_parallel_tasks": 2,
            "tasks": [
                {
                    "task_id": "task_a",
                    "name": "Task A",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["task_b"],
                    "steps": [],
                },
                {
                    "task_id": "task_b",
                    "name": "Task B",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["task_a"],
                    "steps": [],
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(cyclic_plan))

        await _register_and_login(client, "cycle_test_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Cycle Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://localhost:8000"},
        )
        env_id = env_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "A depends on B, B depends on A",
                "environment_id": env_id,
            },
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert "cycle" in data["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_multi_agent_plan_forbidden_tokens_rejection(self, client: AsyncClient) -> None:
        """Rejects arbitrary shell or python code execution tokens in task configuration."""
        injected_plan = {
            "name": "Malicious Plan",
            "description": "Trying code injection",
            "max_parallel_tasks": 2,
            "tasks": [
                {
                    "task_id": "task_inject",
                    "name": "Injection Task",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "step_cmd",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/health", "hook": "import os; os.system('echo pwned')"},
                        }
                    ],
                }
            ],
        }
        set_ai_provider(CustomMockAIProvider(injected_plan))

        await _register_and_login(client, "inject_test_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Inject Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://localhost:8000"},
        )
        env_id = env_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "Run tests safely",
                "environment_id": env_id,
            },
        )
        assert resp.status_code == 422
        data = resp.json()
        assert data["error"]["code"] == "VALIDATION_ERROR"
        assert "forbidden code execution token" in data["error"]["message"]

    @pytest.mark.asyncio
    async def test_get_mission_dag_and_human_approval(self, client: AsyncClient) -> None:
        """Retrieves task DAG and approves draft mission plan."""
        valid_plan = {
            "name": "Mission Ready for Approval",
            "description": "Requires human review",
            "max_parallel_tasks": 2,
            "tasks": [
                {
                    "task_id": "t1",
                    "name": "Task 1",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "s1",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/api/v1/health"},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                            "retry_policy": {"max_attempts": 1, "backoff_coefficient": 1.0, "allow_non_idempotent_retry": False},
                            "idempotency_class": "safe",
                            "risk_level": "low",
                        }
                    ],
                }
            ],
        }
        set_ai_provider(CustomMockAIProvider(valid_plan))

        await _register_and_login(client, "approver_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Approval Mission Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://localhost:8000"},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "Health test",
                "environment_id": env_id,
            },
        )
        mission_id = plan_resp.json()["mission_id"]

        # GET /missions/{mission_id}/dag
        dag_resp = await client.get(f"/api/v1/missions/{mission_id}/dag")
        assert dag_resp.status_code == 200
        dag_data = dag_resp.json()
        assert dag_data["mission_id"] == mission_id
        assert len(dag_data["tasks"]) == 1

        # POST /missions/{mission_id}/approve
        approve_resp = await client.post(
            f"/api/v1/missions/{mission_id}/approve",
            json={"comment": "Approved after reviewing task graph"},
        )
        assert approve_resp.status_code == 200
        approved_data = approve_resp.json()
        assert approved_data["status"] == "approved"

    @pytest.mark.asyncio
    async def test_evaluator_report_generation(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Evaluator agent parses task attempts and generates requirement coverage and root causes."""
        await _register_and_login(client, "evaluator_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Evaluation Proj"})
        project_id = proj_resp.json()["id"]

        # Insert a plan
        plan = TestPlan(
            id="mission-eval-test-01",
            plan_id="plan-eval-01",
            project_id=project_id,
            name="Evaluation Plan",
            version=1,
            adapter_type="multi_agent_dag",
            status="approved",
            source="ai_generated",
            requires_approval=True,
            created_by="someone",
        )
        db_session.add(plan)

        # Insert DAG tasks with requirement references
        task_1 = DAGTask(
            id="dtask-01",
            plan_id=plan.id,
            task_id="task_login",
            name="Login Task",
            assigned_role=AgentRole.API_SPECIALIST.value,
            adapter_type="http_api",
            steps=[],
            exports={},
            requirement_refs=["REQ-AUTH-01"],
        )
        task_2 = DAGTask(
            id="dtask-02",
            plan_id=plan.id,
            task_id="task_profile",
            name="Profile Task",
            assigned_role=AgentRole.UI_SPECIALIST.value,
            adapter_type="web",
            steps=[],
            exports={},
            requirement_refs=["REQ-PROFILE-01"],
        )
        db_session.add(task_1)
        db_session.add(task_2)

        # Insert Run and Task Attempts
        run = Run(
            id="run-eval-test-01",
            project_id=project_id,
            plan_id=plan.id,
            plan_version=1,
            environment_id="env-1",
            status=RunStatus.FAILED.value,
            requested_by="someone",
        )
        db_session.add(run)

        attempt_1 = DAGTaskAttempt(
            id="att-01",
            run_id=run.id,
            task_id="task_login",
            attempt_number=1,
            status=TaskStatus.PASSED.value,
        )
        attempt_2 = DAGTaskAttempt(
            id="att-02",
            run_id=run.id,
            task_id="task_profile",
            attempt_number=1,
            status=TaskStatus.FAILED.value,
            error_message="Assertion failed: status_code expected 200, got 500",
        )
        db_session.add(attempt_1)
        db_session.add(attempt_2)
        await db_session.commit()

        # Call GET /api/v1/runs/{run_id}/evaluation
        resp = await client.get(f"/api/v1/runs/{run.id}/evaluation")
        assert resp.status_code == 200
        data = resp.json()

        assert data["run_id"] == run.id
        assert data["verdict"] == RunStatus.FAILED.value
        assert data["total_tasks"] == 2
        assert data["passed_tasks"] == 1
        assert data["failed_tasks"] == 1

        # Check requirement traceability matrix
        matrix = data["requirement_coverage"]
        assert len(matrix) == 2
        req_auth = next(m for m in matrix if m["requirement_id"] == "REQ-AUTH-01")
        req_prof = next(m for m in matrix if m["requirement_id"] == "REQ-PROFILE-01")
        assert req_auth["status"] == "PASSED"
        assert req_prof["status"] == "FAILED"

        # Check root causes
        root_causes = data["root_cause_analysis"]
        assert len(root_causes) == 1
        assert root_causes[0]["task_id"] == "task_profile"
        assert "500" in root_causes[0]["diagnostic"]

    @pytest.mark.asyncio
    async def test_tenant_isolation_cross_project_rejection(self, client: AsyncClient) -> None:
        """Users from Tenant B cannot access or approve Tenant A's missions."""
        valid_plan = {
            "name": "Tenant A Mission",
            "description": "Tenant A Secret",
            "max_parallel_tasks": 2,
            "tasks": [
                {
                    "task_id": "t1",
                    "name": "Task 1",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [],
                }
            ],
        }
        set_ai_provider(CustomMockAIProvider(valid_plan))

        # Tenant A user creates mission
        await _register_and_login(client, "user_tenant_a@example.com")
        proj_a = (await client.post("/api/v1/projects", json={"name": "Tenant A Proj"})).json()
        env_a = (await client.post(f"/api/v1/projects/{proj_a['id']}/environments", json={"name": "A-Env", "base_url": "http://a.test"})).json()

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_a['id']}/missions/plan",
            json={"requirements_content": "A reqs", "environment_id": env_a["id"]},
        )
        mission_id = plan_resp.json()["mission_id"]

        # Tenant B user logs in
        await _register_and_login(client, "user_tenant_b@example.com")

        # Tenant B tries to get Tenant A's mission DAG -> 404
        get_resp = await client.get(f"/api/v1/missions/{mission_id}/dag")
        assert get_resp.status_code == 404

        # Tenant B tries to approve Tenant A's mission -> 404
        approve_resp = await client.post(
            f"/api/v1/missions/{mission_id}/approve",
            json={"comment": "Malicious approval"},
        )
        assert approve_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_approved_api_dag_execution_e2e(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """Approved multi-agent API DAG executes through real execution runtime and persists outcomes."""
        import httpx
        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok", "token": "jwt-xyz", "user_id": 42})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        mock_plan = {
            "name": "E2E Authentication and API Health DAG",
            "description": "Multi-agent test of authentication and health endpoint",
            "max_parallel_tasks": 2,
            "tasks": [
                {
                    "task_id": "api_auth",
                    "name": "API Auth Task",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "step_auth",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/api/v1/auth/login", "json": {"user": "admin"}},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                        }
                    ],
                    "exports": {"jwt_token": {"json_path": "$.token", "is_secret": True}},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-AUTH-1"],
                },
                {
                    "task_id": "api_health",
                    "name": "API Health Check Task",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["api_auth"],
                    "steps": [
                        {
                            "step_id": "step_health",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/api/v1/health"},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-HEALTH-1"],
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "dag_runner_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "DAG Run Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        # 1. Plan mission DAG
        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "# Specification\n1. Login via API\n2. Check health",
                "environment_id": env_id,
            },
        )
        assert plan_resp.status_code == 201
        mission_id = plan_resp.json()["mission_id"]

        # 2. Unapproved execution must fail with 422
        unapproved_run_resp = await client.post(
            f"/api/v1/plans/{mission_id}/runs",
            json={"environment_id": env_id},
        )
        assert unapproved_run_resp.status_code == 422
        assert unapproved_run_resp.json()["error"]["code"] == "PLAN_NOT_APPROVED"

        # 3. Approve mission DAG
        approve_resp = await client.post(
            f"/api/v1/missions/{mission_id}/approve",
            json={"comment": "Approved for integration test"},
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"

        # 4. Execute approved mission DAG
        run_resp = await client.post(
            f"/api/v1/plans/{mission_id}/runs",
            json={"environment_id": env_id},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "passed"
        run_id = run_data["id"]

        # 5. Verify DAG task attempts in database
        task_attempts = (
            await db_session.execute(
                select(DAGTaskAttempt).where(DAGTaskAttempt.run_id == run_id)
            )
        ).scalars().all()
        assert len(task_attempts) == 2
        for att in task_attempts:
            assert att.status == "passed"
            assert att.started_at is not None
            assert att.finished_at is not None

        # 6. Verify Evaluator synthesizes passing verdict
        eval_resp = await client.get(f"/api/v1/runs/{run_id}/evaluation")
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()
        assert eval_data["verdict"] == "passed"
        assert eval_data["total_tasks"] == 2
        assert eval_data["passed_tasks"] == 2
        assert eval_data["failed_tasks"] == 0
        assert eval_data["blocked_tasks"] == 0

    @pytest.mark.asyncio
    async def test_failed_prerequisite_cascades_to_blocked(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """Upstream task failure cascades BLOCKED to dependent tasks and records failure diagnostics."""
        import httpx
        def mock_failing_handler(request: httpx.Request) -> httpx.Response:
            if "fail" in str(request.url):
                return httpx.Response(500, json={"error": "server error"})
            return httpx.Response(200, json={"status": "ok"})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_failing_handler)

        mock_plan = {
            "name": "Cascade Failure DAG Mission",
            "description": "Test cascading failure from parent to child",
            "max_parallel_tasks": 2,
            "tasks": [
                {
                    "task_id": "parent_setup",
                    "name": "Parent Setup Task",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "step_fail",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/api/v1/fail"},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-PARENT"],
                },
                {
                    "task_id": "child_dependent",
                    "name": "Child Dependent Task",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["parent_setup"],
                    "steps": [
                        {
                            "step_id": "step_child",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/api/v1/child"},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-CHILD"],
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "cascade_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Cascade Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "# Specification\n1. Setup fails\n2. Child blocked",
                "environment_id": env_id,
            },
        )
        mission_id = plan_resp.json()["mission_id"]

        # Approve and execute
        await client.post(f"/api/v1/missions/{mission_id}/approve", json={})
        run_resp = await client.post(
            f"/api/v1/plans/{mission_id}/runs",
            json={"environment_id": env_id},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "failed"
        run_id = run_data["id"]

        # Verify task attempt states
        attempts = (
            await db_session.execute(
                select(DAGTaskAttempt).where(DAGTaskAttempt.run_id == run_id)
            )
        ).scalars().all()
        assert len(attempts) == 2

        parent_att = next(a for a in attempts if a.task_id == "parent_setup")
        child_att = next(a for a in attempts if a.task_id == "child_dependent")

        assert parent_att.status == "failed"
        assert child_att.status == "blocked"
        assert "Prerequisite task 'parent_setup' failed" in child_att.error_message

        # Verify Evaluation report
        eval_resp = await client.get(f"/api/v1/runs/{run_id}/evaluation")
        assert eval_resp.status_code == 200
        eval_data = eval_resp.json()
        assert eval_data["verdict"] == "failed"
        assert eval_data["passed_tasks"] == 0
        assert eval_data["failed_tasks"] == 1
        assert eval_data["blocked_tasks"] == 1

    @pytest.mark.asyncio
    async def test_worker_runner_executes_mission_dag(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Durable worker runner async_execute_run_job executes multi_agent_dag plans directly."""
        import httpx
        from uitp_orchestrator.exceptions import DuplicateExecutionError
        from uitp_worker_runner.execution import async_execute_run_job

        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "healthy"})

        mock_plan = {
            "name": "Worker Runner DAG Mission",
            "description": "Executed via worker runner directly",
            "max_parallel_tasks": 1,
            "tasks": [
                {
                    "task_id": "worker_api_task",
                    "name": "Worker API Task",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "w_step_1",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/health"},
                            "assertions": [{"type": "status_code", "value": 200}],
                            "timeout_seconds": 10.0,
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-WORKER"],
                }
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "worker_dag_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Worker DAG Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={"requirements_content": "# Worker\n1. Worker test", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]

        # Approve
        await client.post(f"/api/v1/missions/{mission_id}/approve", json={})

        # Create Run record in queued state
        import uuid
        from datetime import UTC, datetime
        run = Run(
            id=str(uuid.uuid4()),
            plan_id=mission_id,
            plan_version=1,
            environment_id=env_id,
            project_id=project_id,
            status="queued",
            requested_by="worker-tester",
            started_at=datetime.now(UTC),
        )
        db_session.add(run)
        await db_session.commit()

        # Execute through worker runner
        transport = httpx.MockTransport(mock_handler)
        result = await async_execute_run_job(
            run_id=run.id,
            session=db_session,
            worker_id="test-worker-01",
            transport=transport,
        )

        assert result["status"] == "passed"
        refreshed_run = await db_session.get(Run, run.id)
        assert refreshed_run.status == "passed"

        attempts = (
            await db_session.execute(
                select(DAGTaskAttempt).where(DAGTaskAttempt.run_id == run.id)
            )
        ).scalars().all()
        assert len(attempts) == 1
        assert attempts[0].status == "passed"

        # Duplicate delivery prevention: second attempt must fail with DuplicateExecutionError
        with pytest.raises(DuplicateExecutionError):
            await async_execute_run_job(
                run_id=run.id,
                session=db_session,
                worker_id="test-worker-02",
                transport=transport,
            )

    @pytest.mark.asyncio
    async def test_approved_ui_dag_executes_successfully(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Approved multi-agent UI DAG executes through the Playwright web adapter against local fixture."""
        from tests.fixtures.web_app import run_test_web_server

        async with run_test_web_server() as base_url:
            mock_plan = {
                "name": "Playwright UI DAG Mission",
                "description": "Multi-agent Playwright browser navigation mission",
                "max_parallel_tasks": 1,
                "tasks": [
                    {
                        "task_id": "ui_nav_task",
                        "name": "UI Navigation and Screenshot Task",
                        "assigned_role": "ui_specialist",
                        "adapter_type": "web",
                        "depends_on": [],
                        "steps": [
                            {
                                "step_id": "step_ui_nav",
                                "adapter_type": "web",
                                "action": "navigate",
                                "inputs": {"url": f"{base_url}/login"},
                                "assertions": [{"type": "title_contains", "value": "Login"}],
                                "timeout_seconds": 15.0,
                            },
                            {
                                "step_id": "step_ui_screenshot",
                                "adapter_type": "web",
                                "action": "screenshot",
                                "inputs": {"full_page": True},
                                "timeout_seconds": 10.0,
                            },
                        ],
                        "exports": {},
                        "timeout_seconds": 30.0,
                        "requirement_refs": ["REQ-UI-NAV"],
                    }
                ],
            }
            set_ai_provider(CustomMockAIProvider(mock_plan))

            await _register_and_login(client, "ui_dag_user@example.com")
            proj_resp = await client.post("/api/v1/projects", json={"name": "UI DAG Proj"})
            project_id = proj_resp.json()["id"]

            env_resp = await client.post(
                f"/api/v1/projects/{project_id}/environments",
                json={
                    "name": "Local Web Env",
                    "base_url": base_url,
                    "allowlist": ["127.0.0.1"],
                },
            )
            env_id = env_resp.json()["id"]

            plan_resp = await client.post(
                f"/api/v1/projects/{project_id}/missions/plan",
                json={"requirements_content": "# Web Flow\n1. Navigate", "environment_id": env_id},
            )
            mission_id = plan_resp.json()["mission_id"]

            # Approve mission
            await client.post(f"/api/v1/missions/{mission_id}/approve", json={})

            # Execute run
            run_resp = await client.post(
                f"/api/v1/plans/{mission_id}/runs",
                json={"environment_id": env_id},
            )
            assert run_resp.status_code == 201
            run_data = run_resp.json()
            assert run_data["status"] == "passed"
            run_id = run_data["id"]

            # Verify DAG task attempt
            attempts = (
                await db_session.execute(
                    select(DAGTaskAttempt).where(DAGTaskAttempt.run_id == run_id)
                )
            ).scalars().all()
            assert len(attempts) == 1
            assert attempts[0].status == "passed"

    @pytest.mark.asyncio
    async def test_cyclic_dag_rejection_at_runtime(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """If a corrupted cyclic DAG exists in the database, runtime execution rejects it safely."""
        from datetime import UTC, datetime
        import uuid

        await _register_and_login(client, "cyclic_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Cyclic Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        mission_id = str(uuid.uuid4())
        plan = TestPlan(
            id=mission_id,
            plan_id=mission_id,
            project_id=project_id,
            name="Corrupted Cyclic Plan",
            version=1,
            adapter_type="multi_agent_dag",
            status="approved",
            approved_at=datetime.now(UTC),
            approved_by="approver-123",
            requires_approval=False,
            created_by="someone",
        )
        db_session.add(plan)

        # Create 2 cyclic tasks
        t1 = DAGTask(
            id=str(uuid.uuid4()),
            plan_id=mission_id,
            task_id="task_cycle_a",
            name="Task A",
            assigned_role="api_specialist",
            adapter_type="http_api",
            steps=[],
            exports={},
            timeout_seconds=30.0,
            requirement_refs=[],
        )
        t2 = DAGTask(
            id=str(uuid.uuid4()),
            plan_id=mission_id,
            task_id="task_cycle_b",
            name="Task B",
            assigned_role="api_specialist",
            adapter_type="http_api",
            steps=[],
            exports={},
            timeout_seconds=30.0,
            requirement_refs=[],
        )
        db_session.add(t1)
        db_session.add(t2)

        # Create cycle: A -> B and B -> A
        d1 = DAGDependency(id=str(uuid.uuid4()), plan_id=mission_id, parent_task_id="task_cycle_b", child_task_id="task_cycle_a")
        d2 = DAGDependency(id=str(uuid.uuid4()), plan_id=mission_id, parent_task_id="task_cycle_a", child_task_id="task_cycle_b")
        db_session.add(d1)
        db_session.add(d2)
        await db_session.commit()

        # Execute run
        run_resp = await client.post(
            f"/api/v1/plans/{mission_id}/runs",
            json={"environment_id": env_id},
        )
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "failed"


