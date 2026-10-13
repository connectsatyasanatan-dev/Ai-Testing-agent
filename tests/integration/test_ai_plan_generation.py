"""
tests/integration/test_ai_plan_generation.py
============================================
Integration tests for AI-Assisted Test Plan Generation with strict deterministic
validation, prompt injection defense, and human approval workflow (Tasks P5-00 to P5-04).
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.plan import TestPlan as TestPlanModel
from uitp_api.models.project import ProjectMembership
from uitp_api.services.ai_provider import MockAIProvider, set_ai_provider
from uitp_api.services.rate_limiter import (
    InMemoryRateLimiterBackend,
    RateLimiter,
    set_rate_limiter,
)


async def _register_and_login(client: AsyncClient, email: str) -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery"},
    )
    return resp.json()


@pytest.mark.integration
class TestAIPlanGeneration:
    @pytest.fixture(autouse=True)
    def reset_overrides(self):
        set_ai_provider(None)
        set_rate_limiter(None)
        yield
        set_ai_provider(None)
        set_rate_limiter(None)

    @pytest.mark.asyncio
    async def test_generate_plan_503_when_no_provider_configured(
        self, client: AsyncClient
    ) -> None:
        """P5-00: Returns 503 AI_PROVIDER_NOT_CONFIGURED when no provider is configured."""
        await _register_and_login(client, "ai_user_503@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "AI 503 Project"})
        project_id = proj_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/plans/generate",
            json={
                "intent": "Test user authentication flow and verify health endpoint",
                "adapter_type": "http_api",
            },
        )
        assert resp.status_code == 503
        data = resp.json()
        assert data["error"]["code"] == "AI_PROVIDER_NOT_CONFIGURED"
        assert "not available" in data["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_generate_plan_success_with_mock_provider(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """P5-01, P5-02: Generates structured draft plan, saves as draft requiring approval."""
        set_ai_provider(MockAIProvider())
        await _register_and_login(client, "ai_user_success@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "AI Success Project"})
        project_id = proj_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/plans/generate",
            json={
                "intent": "Verify health endpoint returns status 200",
                "adapter_type": "http_api",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "draft"
        assert data["requires_approval"] is True
        assert data["adapter_type"] == "http_api"
        assert len(data["steps"]) >= 1
        assert len(data["blocked_steps"]) == 0
        plan_id = data["plan_id"]

        # Check all steps have required fields
        for step in data["steps"]:
            assert step["idempotency_class"] in ("safe", "idempotent", "non_idempotent", "unknown")
            assert step["risk_level"] in ("low", "medium", "high", "critical")
            assert step["timeout_seconds"] > 0
            assert step["action"] in ("get", "post", "put", "delete", "http_request", "head", "options", "patch")

        # Verify DB persistence
        res = await db_session.execute(select(TestPlanModel).where(TestPlanModel.plan_id == plan_id))
        plan_row = res.scalars().first()
        assert plan_row is not None
        assert plan_row.source == "ai_generated"
        assert plan_row.status == "draft"
        assert plan_row.requires_approval is True

    @pytest.mark.asyncio
    async def test_unapproved_ai_generated_plan_cannot_execute(
        self, client: AsyncClient
    ) -> None:
        """P5-04: Approval gate enforcement: execution is strictly rejected before approval."""
        set_ai_provider(MockAIProvider())
        await _register_and_login(client, "ai_approval_gate@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "AI Gate Project"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Test Env", "base_url": "http://localhost:8000"},
        )
        env_id = env_resp.json()["id"]

        gen_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans/generate",
            json={
                "intent": "Verify user list pagination",
                "adapter_type": "http_api",
            },
        )
        assert gen_resp.status_code == 201
        plan_id = gen_resp.json()["plan_id"]

        # Attempt to run without approval -> 422 PLAN_NOT_APPROVED
        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert run_resp.status_code == 422
        assert run_resp.json()["error"]["code"] == "PLAN_NOT_APPROVED"

        # Explicit human approval workflow
        approve_resp = await client.post(
            f"/api/v1/plans/{plan_id}/approve",
            json={"version": 1},
        )
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "approved"

        # Now creating run is accepted
        run_ok_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1, "background": True},
        )
        assert run_ok_resp.status_code == 201
        assert run_ok_resp.json()["status"] in ("queued", "running")

    @pytest.mark.asyncio
    async def test_blocked_steps_filter_out_dangerous_or_unsupported_actions(
        self, client: AsyncClient
    ) -> None:
        """P5-03: Deterministic validation captures unsafe/unallowlisted steps in blocked_steps."""
        canned_response = {
            "name": "Mixed Safety Plan",
            "description": "Plan containing both valid and dangerous steps",
            "steps": [
                {
                    "step_id": "safe_step",
                    "adapter_type": "http_api",
                    "action": "get",
                    "inputs": {"path": "/api/v1/items"},
                    "assertions": [{"type": "status_code", "value": 200}],
                    "timeout_seconds": 10.0,
                    "retry_policy": {"max_attempts": 1, "backoff_coefficient": 2.0, "allow_non_idempotent_retry": False},
                    "idempotency_class": "safe",
                    "risk_level": "low",
                },
                {
                    "step_id": "malicious_eval_step",
                    "adapter_type": "http_api",
                    "action": "eval('import os; os.system(\"id\")')",
                    "inputs": {"code": "dangerous"},
                    "assertions": [],
                },
                {
                    "step_id": "unallowlisted_action_step",
                    "adapter_type": "http_api",
                    "action": "execute_shell_script",
                    "inputs": {"cmd": "whoami"},
                    "assertions": [],
                },
                {
                    "step_id": "raw_password_step",
                    "adapter_type": "http_api",
                    "action": "post",
                    "inputs": {"path": "/login", "password": "plaintext_password_leak"},
                    "assertions": [],
                },
            ],
        }
        set_ai_provider(MockAIProvider(canned_response=canned_response))

        await _register_and_login(client, "ai_safety@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Safety Project"})
        project_id = proj_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/plans/generate",
            json={
                "intent": "Test with dangerous inputs",
                "adapter_type": "http_api",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert len(data["steps"]) == 1
        assert data["steps"][0]["step_id"] == "safe_step"
        assert len(data["blocked_steps"]) == 3

        blocked_ids = {b["step_id"] for b in data["blocked_steps"]}
        assert "malicious_eval_step" in blocked_ids
        assert "unallowlisted_action_step" in blocked_ids
        assert "raw_password_step" in blocked_ids

    @pytest.mark.asyncio
    async def test_all_steps_blocked_rejects_plan(self, client: AsyncClient) -> None:
        """When 100% of generated steps fail validation, returns 422 VALIDATION_ERROR."""
        canned_response = {
            "name": "All Invalid Plan",
            "steps": [
                {
                    "step_id": "bad_step",
                    "action": "rm -rf /",
                    "inputs": {},
                }
            ],
        }
        set_ai_provider(MockAIProvider(canned_response=canned_response))

        await _register_and_login(client, "ai_all_blocked@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "All Blocked Project"})
        project_id = proj_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/plans/generate",
            json={"intent": "Run rm -rf", "adapter_type": "http_api"},
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"
        assert "rejected" in resp.json()["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_ai_generation_rate_limiting(self, client: AsyncClient) -> None:
        """Exceeding rate limit returns 429 RATE_LIMIT_EXCEEDED."""
        set_ai_provider(MockAIProvider())
        set_rate_limiter(RateLimiter(InMemoryRateLimiterBackend()))

        await _register_and_login(client, "ai_ratelimit@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "RateLimit Project"})
        project_id = proj_resp.json()["id"]

        # Default limit is 10 requests per minute
        for i in range(10):
            res = await client.post(
                f"/api/v1/projects/{project_id}/plans/generate",
                json={"intent": f"Test intent {i}", "adapter_type": "http_api"},
            )
            assert res.status_code == 201

        # 11th request triggers rate limit
        res_limit = await client.post(
            f"/api/v1/projects/{project_id}/plans/generate",
            json={"intent": "One too many requests", "adapter_type": "http_api"},
        )
        assert res_limit.status_code == 429
        assert res_limit.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert "Retry-After" in res_limit.headers

    @pytest.mark.asyncio
    async def test_authorization_viewer_denied(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """Viewers cannot generate plans (403 PERMISSION_DENIED); non-members get 404."""
        set_ai_provider(MockAIProvider())
        await _register_and_login(client, "owner_ai@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Auth Project"})
        project_id = proj_resp.json()["id"]

        # Create viewer user
        viewer_reg = await _register_and_login(client, "viewer_ai@test.com")
        viewer_id = viewer_reg["id"]

        # Add viewer membership to project
        db_session.add(
            ProjectMembership(
                id="mem-viewer-ai",
                project_id=project_id,
                user_id=viewer_id,
                role="viewer",
            )
        )
        await db_session.commit()

        # Viewer attempt -> 403
        resp = await client.post(
            f"/api/v1/projects/{project_id}/plans/generate",
            json={"intent": "Viewer intent", "adapter_type": "http_api"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PERMISSION_DENIED"

        # Non-member attempt -> 404
        await _register_and_login(client, "stranger_ai@test.com")
        resp_stranger = await client.post(
            f"/api/v1/projects/{project_id}/plans/generate",
            json={"intent": "Stranger intent", "adapter_type": "http_api"},
        )
        assert resp_stranger.status_code == 404
        assert resp_stranger.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_provider_timeout_handled_gracefully(self, client: AsyncClient) -> None:
        """Provider timeout returns 422 with a safe error message."""
        set_ai_provider(MockAIProvider(timeout=True))
        await _register_and_login(client, "ai_timeout@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Timeout Project"})
        project_id = proj_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/plans/generate",
            json={"intent": "Timeout intent", "adapter_type": "http_api"},
        )
        assert resp.status_code == 422
        assert "timed out" in resp.json()["error"]["message"].lower()
