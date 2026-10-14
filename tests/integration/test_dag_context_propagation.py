"""
tests/integration/test_dag_context_propagation.py
=================================================
Integration tests for Secure Cross-Layer Runtime Context Propagation (Task 2):
1. API task exports authentication token; dependent task receives it in headers.
2. API task exports created resource ID; subsequent API task consumes it in URL path.
3. UI task consumes an explicitly supported context value safely without code evaluation.
4. Missing exports fail with a useful structured error and cascade BLOCKED.
5. Failed producers do not publish successful context.
6. Concurrent runs cannot see one another's state.
7. Independent tasks cannot race to overwrite shared keys.
8. Secrets do not leak into logs or generated artifacts.
9. Existing single-plan execution remains unchanged.
"""
from __future__ import annotations

import json
from typing import Any
import pytest
from httpx import AsyncClient
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_adapter_base.base import BaseAdapter, ExecutionContext, StepExecutionResult
from uitp_api.models.dag import DAGTaskAttempt, RunContext
from uitp_api.models.evidence import EvidenceArtifact
from uitp_api.models.run import Run, RunEvent, StepAttempt
from uitp_api.services.ai_provider import MockAIProvider, set_ai_provider
from uitp_contracts.adapter import AdapterCapabilities
from uitp_contracts.enums import AdapterType, StepStatus
from uitp_contracts.plan import TestStepSchema, TokenUsage
from uitp_evidence.storage import LocalFilesystemEvidenceStorage
from uitp_api.services import evidence_service


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
    def __init__(self, response_data: dict[str, Any]):
        super().__init__()
        self._response_data = response_data

    async def complete_structured(self, *, system_prompt: str, user_prompt: str, temperature: float = 0.1):
        return self._response_data, TokenUsage(prompt_tokens=200, completion_tokens=400, total_tokens=600)


class MockWebAdapter(BaseAdapter):
    """Deterministic mock Playwright Web Adapter for testing UI Specialist context consumption."""

    ADAPTER_ID = "mock_web_adapter"
    VERSION = "1.0.0"

    def __init__(self) -> None:
        self.executed_steps: list[TestStepSchema] = []

    def metadata(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_id=self.ADAPTER_ID,
            adapter_type=AdapterType.WEB,
            version=self.VERSION,
            supported_actions=["navigate", "fill", "click", "wait_for"],
            limitations=[],
            conformance_version="1.0",
        )

    async def health_check(self) -> bool:
        return True

    async def execute_step(self, step: TestStepSchema, context: ExecutionContext) -> StepExecutionResult:
        self.executed_steps.append(step)
        return StepExecutionResult(
            status=StepStatus.PASSED,
            outputs={"action": step.action, "inputs": step.inputs, "url": "http://app.local/dashboard"},
        )

    async def close_session(self, run_id: str) -> None:
        pass


@pytest.mark.integration
class TestDAGContextPropagation:
    @pytest.fixture(autouse=True)
    def reset_provider(self):
        set_ai_provider(None)
        yield
        set_ai_provider(None)

    @pytest.mark.asyncio
    async def test_api_exports_auth_token_and_dependent_task_consumes_it(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """API task exports auth token; dependent API task consumes it in headers."""
        received_auth_headers: list[str] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if "/auth/login" in str(request.url):
                return httpx.Response(200, json={"token": "jwt-super-secret-12345", "user": "admin"})
            elif "/user/profile" in str(request.url):
                auth = request.headers.get("authorization", "")
                received_auth_headers.append(auth)
                return httpx.Response(200, json={"id": "usr_99", "role": "admin"})
            return httpx.Response(404)

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        mock_plan = {
            "name": "Auth and Profile Context Propagation Mission",
            "description": "API exports token and downstream consumes it",
            "tasks": [
                {
                    "task_id": "auth_task",
                    "name": "Authenticate",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "login_step",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/auth/login", "json": {"user": "admin"}},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {"auth_token": {"json_path": "$.token", "is_secret": True}},
                },
                {
                    "task_id": "profile_task",
                    "name": "Fetch Profile",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["auth_task"],
                    "steps": [
                        {
                            "step_id": "get_profile_step",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {
                                "path": "/user/profile",
                                "headers": {"Authorization": "Bearer ${context.auth_token}"},
                            },
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {},
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "ctx_auth_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Context Auth Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        # Plan and approve mission
        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "Login and fetch profile", "environment_id": env_id},
        )
        assert plan_resp.status_code == 201
        mission_id = plan_resp.json()["mission_id"]

        approve_resp = await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})
        assert approve_resp.status_code == 200

        # Execute mission
        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "passed"
        run_id = run_data["id"]

        # 1. Verify downstream task received the resolved token
        assert len(received_auth_headers) == 1
        assert received_auth_headers[0] == "Bearer jwt-super-secret-12345"

        # 2. Verify RunContext row in DB has is_secret=True and value='[REDACTED]'
        rc_rows = (await db_session.execute(select(RunContext).where(RunContext.run_id == run_id))).scalars().all()
        assert len(rc_rows) == 1
        assert rc_rows[0].key == "auth_token"
        assert rc_rows[0].is_secret is True
        assert rc_rows[0].value == "[REDACTED]"
        assert rc_rows[0].source_task_id == "auth_task"

    @pytest.mark.asyncio
    async def test_api_exports_resource_id_and_subsequent_api_consumes_it(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """API task exports resource ID; subsequent task consumes it in URL path."""
        requested_urls: list[str] = []

        def mock_handler(request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            if request.method == "POST" and "/items" in str(request.url):
                return httpx.Response(201, json={"data": {"item_id": "item_abc_999"}, "status": "created"})
            elif request.method == "GET" and "/items/item_abc_999" in str(request.url):
                return httpx.Response(200, json={"id": "item_abc_999", "name": "Widget"})
            return httpx.Response(404)

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        mock_plan = {
            "name": "Resource Creation and Retrieval DAG",
            "tasks": [
                {
                    "task_id": "create_item",
                    "name": "Create Item",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "post_item",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/items", "json": {"name": "Widget"}},
                            "assertions": [{"type": "status_code", "value": 201}],
                        }
                    ],
                    "exports": {"created_id": {"json_path": "$.data.item_id", "is_secret": False}},
                },
                {
                    "task_id": "get_item",
                    "name": "Get Item",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["create_item"],
                    "steps": [
                        {
                            "step_id": "fetch_item",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/items/${context.created_id}"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {},
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "ctx_resource_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Context Resource Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "Create item and get item", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]

        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        assert run_resp.status_code == 201
        assert run_resp.json()["status"] == "passed"
        run_id = run_resp.json()["id"]

        # Verify URL path had resolved item_id
        assert any("/items/item_abc_999" in u for u in requested_urls)

        # Verify RunContext has actual string value since is_secret=False
        rc = (await db_session.execute(select(RunContext).where(RunContext.run_id == run_id))).scalar_one()
        assert rc.key == "created_id"
        assert rc.value == "item_abc_999"
        assert rc.is_secret is False

    @pytest.mark.asyncio
    async def test_ui_task_consumes_explicitly_supported_context_value_safely(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """UI task consumes an exported context value safely through web adapter."""
        def mock_http(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"session_id": "sess_ui_safe_443"})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_http)
        mock_web = MockWebAdapter()
        app.state.custom_web_adapter = mock_web

        mock_plan = {
            "name": "Cross-Layer API to UI Context Propagation",
            "tasks": [
                {
                    "task_id": "api_setup",
                    "name": "API Setup Session",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "post_session",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/session/create"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {"session_id": {"json_path": "$.session_id", "is_secret": False}},
                },
                {
                    "task_id": "ui_verify",
                    "name": "UI Verification",
                    "assigned_role": "ui_specialist",
                    "adapter_type": "web",
                    "depends_on": ["api_setup"],
                    "steps": [
                        {
                            "step_id": "fill_session",
                            "adapter_type": "web",
                            "action": "fill",
                            "inputs": {
                                "selector": "#session_input",
                                "value": "${context.session_id}",
                            },
                        }
                    ],
                    "exports": {},
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "ctx_ui_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "UI Context Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "API setup and UI verification", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]

        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        assert run_resp.status_code == 201
        assert run_resp.json()["status"] == "passed"

        # Verify UI specialist received resolved context in inputs
        assert len(mock_web.executed_steps) == 1
        executed = mock_web.executed_steps[0]
        assert executed.action == "fill"
        assert executed.inputs["value"] == "sess_ui_safe_443"

    @pytest.mark.asyncio
    async def test_missing_exports_fail_with_useful_structured_error(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """Producer task fails with structured diagnostics when declared export is missing."""
        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "ok"})  # 'auth_token' is missing!

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        mock_plan = {
            "name": "Missing Export Mission",
            "tasks": [
                {
                    "task_id": "producer_task",
                    "name": "Producer",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "step_1",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/ok"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {"missing_key": {"json_path": "$.auth_token", "is_secret": False}},
                },
                {
                    "task_id": "consumer_task",
                    "name": "Consumer",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["producer_task"],
                    "steps": [
                        {
                            "step_id": "step_2",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/other"},
                        }
                    ],
                    "exports": {},
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "ctx_missing_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Missing Export Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "Missing export test", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]

        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "failed"
        run_id = run_data["id"]

        # Check task attempts
        attempts = (await db_session.execute(select(DAGTaskAttempt).where(DAGTaskAttempt.run_id == run_id))).scalars().all()
        att_map = {a.task_id: a for a in attempts}

        assert att_map["producer_task"].status == "failed"
        assert "missing_key" in str(att_map["producer_task"].error_message)
        assert "$.auth_token" in str(att_map["producer_task"].error_message)
        assert att_map["consumer_task"].status == "blocked"

    @pytest.mark.asyncio
    async def test_failed_producers_do_not_publish_successful_context(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """Failed producers do not publish context, and downstream tasks are BLOCKED."""
        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, json={"error": "db down", "token": "leaked_tok"})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        mock_plan = {
            "name": "Failed Producer Context Isolation",
            "tasks": [
                {
                    "task_id": "failing_producer",
                    "name": "Failing Producer",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "s1",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/fail"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {"token": {"json_path": "$.token", "is_secret": True}},
                },
                {
                    "task_id": "blocked_consumer",
                    "name": "Blocked Consumer",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["failing_producer"],
                    "steps": [
                        {
                            "step_id": "s2",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/ok", "headers": {"Authorization": "${context.token}"}},
                        }
                    ],
                    "exports": {},
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "failed_producer_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Failed Producer Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "Failing producer test", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]

        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        run_data = run_resp.json()
        assert run_data["status"] == "failed"
        run_id = run_data["id"]

        # No RunContext rows must exist!
        rc_rows = (await db_session.execute(select(RunContext).where(RunContext.run_id == run_id))).scalars().all()
        assert len(rc_rows) == 0

    @pytest.mark.asyncio
    async def test_concurrent_runs_cannot_see_one_anothers_state(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """Concurrent missions do not leak or cross-contaminate runtime context."""
        captured_tokens_per_run: dict[str, str] = {}

        def mock_handler(request: httpx.Request) -> httpx.Response:
            url_str = str(request.url)
            if "/login" in url_str:
                user = request.url.params.get("user", "default")
                return httpx.Response(200, json={"token": f"token_for_{user}"})
            elif "/verify" in url_str:
                auth = request.headers.get("authorization", "")
                user = request.url.params.get("user", "unknown")
                captured_tokens_per_run[user] = auth
                return httpx.Response(200, json={"status": "ok"})
            return httpx.Response(404)

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        def make_mission_spec(username: str):
            return {
                "name": f"Mission for {username}",
                "tasks": [
                    {
                        "task_id": "auth_task",
                        "name": "Auth",
                        "assigned_role": "api_specialist",
                        "adapter_type": "http_api",
                        "depends_on": [],
                        "steps": [
                            {
                                "step_id": "login",
                                "adapter_type": "http_api",
                                "action": "get",
                                "inputs": {"path": f"/login?user={username}"},
                                "assertions": [{"type": "status_code", "value": 200}],
                            }
                        ],
                        "exports": {"tok": {"json_path": "$.token", "is_secret": True}},
                    },
                    {
                        "task_id": "verify_task",
                        "name": "Verify",
                        "assigned_role": "api_specialist",
                        "adapter_type": "http_api",
                        "depends_on": ["auth_task"],
                        "steps": [
                            {
                                "step_id": "verify",
                                "adapter_type": "http_api",
                                "action": "get",
                                "inputs": {
                                    "path": f"/verify?user={username}",
                                    "headers": {"Authorization": "${context.tok}"},
                                },
                                "assertions": [{"type": "status_code", "value": 200}],
                            }
                        ],
                        "exports": {},
                    },
                ],
            }

        await _register_and_login(client, "concurrent_missions_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Concurrent Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        # Run 1 setup
        set_ai_provider(CustomMockAIProvider(make_mission_spec("user_one")))
        p1 = await client.post(f"/api/v1/projects/{proj_id}/missions/plan", json={"requirements_content": "User one", "environment_id": env_id})
        m1_id = p1.json()["mission_id"]
        await client.post(f"/api/v1/missions/{m1_id}/approve", json={"comment": "ok"})

        # Run 2 setup
        set_ai_provider(CustomMockAIProvider(make_mission_spec("user_two")))
        p2 = await client.post(f"/api/v1/projects/{proj_id}/missions/plan", json={"requirements_content": "User two", "environment_id": env_id})
        m2_id = p2.json()["mission_id"]
        await client.post(f"/api/v1/missions/{m2_id}/approve", json={"comment": "ok"})

        # Execute both
        r1 = await client.post(f"/api/v1/plans/{m1_id}/runs", json={"environment_id": env_id})
        r2 = await client.post(f"/api/v1/plans/{m2_id}/runs", json={"environment_id": env_id})

        assert r1.status_code == 201
        assert r2.status_code == 201

        # Each user received strictly their own token
        assert captured_tokens_per_run["user_one"] == "token_for_user_one"
        assert captured_tokens_per_run["user_two"] == "token_for_user_two"

    @pytest.mark.asyncio
    async def test_independent_tasks_cannot_race_to_overwrite_shared_keys(
        self, client: AsyncClient
    ) -> None:
        """Duplicate exports declared across tasks in a DAG are rejected at plan creation."""
        mock_plan = {
            "name": "Race Duplicate Export Plan",
            "tasks": [
                {
                    "task_id": "task_a",
                    "name": "Task A",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [],
                    "exports": {"duplicate_key": {"json_path": "$.val_a"}},
                },
                {
                    "task_id": "task_b",
                    "name": "Task B",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [],
                    "exports": {"duplicate_key": {"json_path": "$.val_b"}},
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "duplicate_export_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Dup Export Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://api.local"},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "Duplicate exports", "environment_id": env_id},
        )
        assert plan_resp.status_code == 422
        assert "Duplicate export key 'duplicate_key'" in plan_resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_secrets_do_not_leak_into_logs_or_artifacts(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """Secret tokens are redacted from response log evidence artifacts, timeline events, and database."""
        raw_secret_value = "super-secret-token-xyz-9876543210"

        def mock_handler(request: httpx.Request) -> httpx.Response:
            if "/auth" in str(request.url):
                return httpx.Response(200, json={"token": raw_secret_value})
            return httpx.Response(200, json={"status": "ok"})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        mock_plan = {
            "name": "Secret Leaks Prevention Mission",
            "tasks": [
                {
                    "task_id": "auth_task",
                    "name": "Auth",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "steps": [
                        {
                            "step_id": "login",
                            "adapter_type": "http_api",
                            "action": "post",
                            "inputs": {"path": "/auth"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {"auth_tok": {"json_path": "$.token", "is_secret": True}},
                },
                {
                    "task_id": "consume_task",
                    "name": "Consume",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": ["auth_task"],
                    "steps": [
                        {
                            "step_id": "consume",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {
                                "path": "/data",
                                "headers": {"Authorization": "Bearer ${context.auth_tok}"},
                            },
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                    "exports": {},
                },
            ],
        }
        set_ai_provider(CustomMockAIProvider(mock_plan))

        await _register_and_login(client, "leak_prevention_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Leak Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/missions/plan",
            json={"requirements_content": "Leak test", "environment_id": env_id},
        )
        mission_id = plan_resp.json()["mission_id"]
        await client.post(f"/api/v1/missions/{mission_id}/approve", json={"comment": "Approved"})

        run_resp = await client.post(f"/api/v1/plans/{mission_id}/runs", json={"environment_id": env_id})
        run_data = run_resp.json()
        assert run_data["status"] == "passed"
        run_id = run_data["id"]

        # 1. Inspect all RunEvents
        events = (await db_session.execute(select(RunEvent).where(RunEvent.run_id == run_id))).scalars().all()
        for ev in events:
            assert raw_secret_value not in ev.message
            if ev.event_metadata:
                assert raw_secret_value not in str(ev.event_metadata)

        # 2. Inspect all StepAttempts
        attempts = (await db_session.execute(select(StepAttempt).where(StepAttempt.run_id == run_id))).scalars().all()
        for att in attempts:
            if att.error_message:
                assert raw_secret_value not in att.error_message

        # 3. Inspect RunContext
        rcs = (await db_session.execute(select(RunContext).where(RunContext.run_id == run_id))).scalars().all()
        for rc in rcs:
            if rc.is_secret:
                assert rc.value == "[REDACTED]"
                assert raw_secret_value not in rc.value

        # 4. Inspect persisted evidence artifacts
        storage = evidence_service.get_evidence_storage()
        artifacts = (await db_session.execute(select(EvidenceArtifact).where(EvidenceArtifact.run_id == run_id))).scalars().all()
        assert len(artifacts) >= 2
        for art in artifacts:
            content_bytes = storage.read(art.object_key)
            content_str = content_bytes.decode("utf-8", errors="ignore")
            # Raw secret value MUST NOT appear in the stored payload!
            assert raw_secret_value not in content_str


    @pytest.mark.asyncio
    async def test_existing_single_plan_execution_remains_unchanged(
        self, client: AsyncClient, app, db_session: AsyncSession
    ) -> None:
        """Existing non-DAG test plans execute normally without modification."""
        def mock_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"status": "standard_plan_ok"})

        app.state.custom_adapter_transport = httpx.MockTransport(mock_handler)

        await _register_and_login(client, "single_plan_user@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Single Plan Proj"})
        proj_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{proj_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
        )
        env_id = env_resp.json()["id"]

        # Create standard non-DAG plan
        plan_resp = await client.post(
            f"/api/v1/projects/{proj_id}/plans",
            json={
                "name": "Standard Single Plan",
                "adapter_type": "http_api",
                "steps": [
                    {
                        "step_id": "single_step",
                        "adapter_type": "http_api",
                        "action": "get",
                        "inputs": {"path": "/ok"},
                        "assertions": [{"type": "status_code", "value": 200}],
                    }
                ],
            },
        )
        assert plan_resp.status_code == 201
        plan_id = plan_resp.json()["id"]

        # Approve version 1
        approve_resp = await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})
        assert approve_resp.status_code == 200

        # Run standard plan
        run_resp = await client.post(f"/api/v1/plans/{plan_id}/runs", json={"environment_id": env_id})
        assert run_resp.status_code == 201
        run_data = run_resp.json()
        assert run_data["status"] == "passed"
