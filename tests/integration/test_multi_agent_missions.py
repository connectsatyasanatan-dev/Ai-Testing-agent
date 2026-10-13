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
