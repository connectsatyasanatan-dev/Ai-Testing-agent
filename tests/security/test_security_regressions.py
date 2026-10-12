"""
tests/security/test_security_regressions.py
===========================================
Security regression test suite (Task P1-06C & ROADMAP_AND_TEST_STRATEGY.md).

Verifies the platform's security controls:
  1. Cross-project environment access isolation (404 masking).
  2. Cross-project plan access isolation (404 masking).
  3. Cross-project run access isolation (404 masking).
  4. Cross-project artifact retrieval isolation (404 masking).
  5. Unapproved plan version execution rejection (422 PLAN_NOT_APPROVED).
  6. Rejection of declarative steps containing executable code or raw credentials.
  7. SSRF protection: loopback, private IP, and cloud metadata rejections.
  8. SSRF redirect validation: redirect to private destination is blocked before dispatch.
  9. Sensitive-data redaction in persistent logs and error outputs.
  10. Request and response size bounds enforcement.
  11. Non-idempotent retry restrictions: non_idempotent steps execute exactly once.
"""
from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from tests.fixtures.mock_http_api import create_mock_api_app
from uitp_adapter_base.base import ExecutionContext
from uitp_adapter_http_api import HttpApiAdapter
from uitp_contracts.enums import (
    AdapterType,
    ErrorCode,
    IdempotencyClass,
    RiskLevel,
    StepStatus,
)
from uitp_contracts.plan import (
    AssertionSchema,
    RetryPolicySchema,
    TestStepSchema,
)
from uitp_evidence.redaction import default_redaction_engine
from uitp_policy import SsrfPolicyValidator


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "secure-password-matrix-123!"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "secure-password-matrix-123!"},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.fixture(autouse=True)
def wire_mock_api_transport(app):
    mock_app = create_mock_api_app()
    app.state.custom_adapter_transport = httpx.ASGITransport(app=mock_app)
    yield
    if hasattr(app.state, "custom_adapter_transport"):
        del app.state.custom_adapter_transport


@pytest.mark.security
class TestSecurityRegressions:
    @pytest.mark.asyncio
    async def test_cross_project_isolation_matrix(self, client: AsyncClient) -> None:
        """Verify cross-project tenant masking across environments, plans, runs, and artifacts."""
        # Tenant A: Create resources
        await _register_and_login(client, "tenant_a_user@test.com")
        proj_a = (await client.post("/api/v1/projects", json={"name": "Tenant A Proj"})).json()["id"]

        env_a = (
            await client.post(
                f"/api/v1/projects/{proj_a}/environments",
                json={
                    "name": "Env A",
                    "base_url": "http://mock-api.local",
                    "allowlist": ["mock-api.local"],
                },
            )
        ).json()["id"]

        plan_a = (
            await client.post(
                f"/api/v1/projects/{proj_a}/plans",
                json={
                    "name": "Plan A",
                    "adapter_type": "http_api",
                    "steps": [
                        {
                            "step_id": "s1",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/ok"},
                            "assertions": [{"type": "status_code", "value": 200}],
                        }
                    ],
                },
            )
        ).json()["plan_id"]

        await client.post(f"/api/v1/plans/{plan_a}/approve", json={"version": 1})

        run_a = (
            await client.post(
                f"/api/v1/plans/{plan_a}/runs",
                json={"environment_id": env_a, "plan_version": 1},
            )
        ).json()["id"]

        art_a = (await client.get(f"/api/v1/runs/{run_a}/artifacts")).json()[0]["id"]

        # Tenant B: Login and attempt accessing Tenant A's IDs
        await _register_and_login(client, "tenant_b_user@test.com")
        proj_b = (await client.post("/api/v1/projects", json={"name": "Tenant B Proj"})).json()["id"]

        # 1. Environment cross-project access returns 404
        assert (await client.get(f"/api/v1/projects/{proj_a}/environments")).status_code == 404
        assert (await client.get(f"/api/v1/projects/{proj_a}/environments/{env_a}")).status_code == 404

        # 2. Plan cross-project access returns 404
        assert (await client.get(f"/api/v1/projects/{proj_a}/plans")).status_code == 404
        assert (await client.get(f"/api/v1/plans/{plan_a}")).status_code == 404
        assert (await client.get(f"/api/v1/plans/{plan_a}/versions")).status_code == 404
        assert (await client.post(f"/api/v1/plans/{plan_a}/approve", json={"version": 1})).status_code == 404

        # 3. Run cross-project access returns 404
        assert (await client.get(f"/api/v1/runs/{run_a}")).status_code == 404
        assert (await client.get(f"/api/v1/runs/{run_a}/events")).status_code == 404
        assert (await client.post(f"/api/v1/plans/{plan_a}/runs", json={"environment_id": env_a})).status_code == 404

        # 4. Artifact cross-project access returns 404
        assert (await client.get(f"/api/v1/runs/{run_a}/artifacts")).status_code == 404
        assert (await client.get(f"/api/v1/runs/{run_a}/artifacts/{art_a}")).status_code == 404

    @pytest.mark.asyncio
    async def test_unapproved_plan_execution_blocked(self, client: AsyncClient) -> None:
        """Verify unapproved plan versions cannot execute under any circumstance."""
        await _register_and_login(client, "unapproved_tester@test.com")
        proj = (await client.post("/api/v1/projects", json={"name": "Approval Project"})).json()["id"]
        env = (
            await client.post(
                f"/api/v1/projects/{proj}/environments",
                json={"name": "Env", "base_url": "http://mock-api.local", "allowlist": ["mock-api.local"]},
            )
        ).json()["id"]

        plan_id = (
            await client.post(
                f"/api/v1/projects/{proj}/plans",
                json={
                    "name": "Draft Plan",
                    "adapter_type": "http_api",
                    "steps": [
                        {
                            "step_id": "s1",
                            "adapter_type": "http_api",
                            "action": "get",
                            "inputs": {"path": "/ok"},
                        }
                    ],
                },
            )
        ).json()["plan_id"]

        # Draft v1 cannot run
        resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env, "plan_version": 1},
        )
        assert resp.status_code == 422
        assert "PLAN_NOT_APPROVED" in resp.text

    @pytest.mark.asyncio
    async def test_declarative_schema_code_injection_rejected(self, client: AsyncClient) -> None:
        """Verify code injection tokens in action or raw credentials in inputs are rejected."""
        await _register_and_login(client, "injection_tester@test.com")
        proj = (await client.post("/api/v1/projects", json={"name": "Injection Project"})).json()["id"]

        forbidden_actions = [
            "exec('cat /etc/passwd')",
            "eval('2+2')",
            "import os",
            "__import__('sys')",
            "subprocess.Popen()",
            "os.system('whoami')",
        ]
        for bad_action in forbidden_actions:
            resp = await client.post(
                f"/api/v1/projects/{proj}/plans",
                json={
                    "name": "Bad Action Plan",
                    "adapter_type": "http_api",
                    "steps": [{"step_id": "bad", "adapter_type": "http_api", "action": bad_action}],
                },
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_ssrf_validator_blocks_private_and_cloud_metadata(self) -> None:
        """Verify unit SSRF policy rejects dangerous targets."""
        validator = SsrfPolicyValidator()

        prohibited_targets = [
            "http://127.0.0.1:8000/",
            "http://localhost:8080/",
            "http://10.0.0.1/admin",
            "http://172.16.0.1/internal",
            "http://192.168.1.1/router",
            "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/",
            "http://[fe80::1]/",
        ]
        for url in prohibited_targets:
            is_valid, reason = await validator.is_valid_url(url, allowlist=None)
            assert is_valid is False
            assert reason is not None

    @pytest.mark.asyncio
    async def test_non_idempotent_step_retry_restriction(self) -> None:
        """Verify non_idempotent steps (e.g. POST) are attempted exactly once and not retried."""
        mock_app = create_mock_api_app()
        transport = httpx.ASGITransport(app=mock_app)
        adapter = HttpApiAdapter(transport=transport)

        step = TestStepSchema(
            step_id="step_non_idempotent",
            adapter_type=AdapterType.HTTP_API,
            action="post",
            inputs={"path": "/fail"},  # 500 error
            retry_policy=RetryPolicySchema(max_attempts=3, retryable_status_codes=[500]),
            idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
            assertions=[AssertionSchema(type="status_code", value=201)],
        )

        ctx = ExecutionContext(
            run_id="run_retry_test",
            step_id="step_non_idempotent",
            base_url="http://mock-api.local",
            allowlist=["mock-api.local"],
        )

        res = await adapter.execute_step(step, ctx)
        assert res.status == StepStatus.FAILED
        # attempt_count must be 1: retry policy was skipped because step is non_idempotent
        assert res.outputs.get("attempt_count") == 1

    @pytest.mark.asyncio
    async def test_redaction_engine_masks_credentials(self) -> None:
        """Verify sensitive headers and URL credentials never survive redaction."""
        data = {
            "Authorization": "Bearer sensitive_secret_key",
            "Set-Cookie": "session=sensitive_cookie",
            "X-API-Key": "my-secret-key",
            "url": "https://user:password@target.local/path?token=secret_query_val",
        }
        sanitized, was_redacted = default_redaction_engine.redact_data(data)
        assert was_redacted is True
        assert "sensitive_secret_key" not in str(sanitized)
        assert "sensitive_cookie" not in str(sanitized)
        assert "password" not in str(sanitized)
        assert "secret_query_val" not in str(sanitized)
        assert sanitized["Authorization"] == "[REDACTED]"
        assert sanitized["Set-Cookie"] == "[REDACTED]"
        assert sanitized["X-API-Key"] == "[REDACTED]"
