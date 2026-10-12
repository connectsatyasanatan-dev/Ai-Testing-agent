"""
tests/integration/test_plans.py
===============================
Integration tests for Declarative HTTP Test Plans, Versioning, and Approval (Task P1-03).
"""
from __future__ import annotations

import uuid
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.audit import AuditEvent
from uitp_api.models.project import ProjectMembership


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


def _valid_step_payload(step_id: str = "step_1", action: str = "get") -> dict:
    return {
        "step_id": step_id,
        "adapter_type": "http_api",
        "action": action,
        "inputs": {"path": "/ok", "method": "GET"},
        "assertions": [
            {"type": "status_code", "value": 200},
            {"type": "json_path_equals", "path": "$.status", "value": "ok"},
        ],
        "timeout_seconds": 30.0,
        "retry_policy": {"max_attempts": 1},
        "idempotency_class": "safe",
        "risk_level": "low",
        "description": "Verify OK endpoint",
    }


@pytest.mark.integration
class TestPlanCreation:
    @pytest.mark.asyncio
    async def test_create_valid_declarative_plan(self, client: AsyncClient) -> None:
        await _register_and_login(client, "plan_owner@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Plan Proj"})
        project_id = proj_resp.json()["id"]

        steps = [_valid_step_payload("step_1"), _valid_step_payload("step_2", action="post")]
        create_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Integration Test Plan",
                "adapter_type": "http_api",
                "steps": steps,
                "description": "Deterministic HTTP plan",
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        data = create_resp.json()
        assert data["name"] == "Integration Test Plan"
        assert data["version"] == 1
        assert data["status"] == "draft"
        assert data["requires_approval"] is True
        assert len(data["steps"]) == 2
        assert data["steps"][0]["step_id"] == "step_1"
        assert data["steps"][0]["step_order"] == 1
        assert "plan_id" in data

    @pytest.mark.asyncio
    async def test_reject_code_like_action_content(self, client: AsyncClient) -> None:
        await _register_and_login(client, "code_tester@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Code Proj"})
        project_id = proj_resp.json()["id"]

        bad_step = _valid_step_payload()
        bad_step["action"] = "exec(__import__('os').system('rm -rf /'))"

        resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Dangerous Plan",
                "adapter_type": "http_api",
                "steps": [bad_step],
            },
        )
        assert resp.status_code == 422, "Should reject code in action"

    @pytest.mark.asyncio
    async def test_reject_raw_credentials_in_inputs(self, client: AsyncClient) -> None:
        await _register_and_login(client, "secret_tester@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Secret Proj"})
        project_id = proj_resp.json()["id"]

        bad_step = _valid_step_payload()
        bad_step["inputs"] = {"password": "raw_plaintext_password"}

        resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Leaky Plan",
                "adapter_type": "http_api",
                "steps": [bad_step],
            },
        )
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.integration
class TestPlanVersioning:
    @pytest.mark.asyncio
    async def test_create_new_version_without_overwriting_prior(
        self, client: AsyncClient
    ) -> None:
        await _register_and_login(client, "versioner@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Version Proj"})
        project_id = proj_resp.json()["id"]

        # 1. Create v1
        c1 = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Plan V1",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload("s1")],
            },
        )
        plan_id = c1.json()["plan_id"]
        assert c1.json()["version"] == 1

        # 2. Create v2 via /plans/{plan_id}/versions
        c2 = await client.post(
            f"/api/v1/plans/{plan_id}/versions",
            json={
                "name": "Plan V2 Revised",
                "steps": [_valid_step_payload("s1"), _valid_step_payload("s2")],
            },
        )
        assert c2.status_code == 201
        data2 = c2.json()
        assert data2["plan_id"] == plan_id
        assert data2["version"] == 2
        assert data2["status"] == "draft"
        assert len(data2["steps"]) == 2

        # 3. Retrieve v1 and verify it was NOT overwritten
        v1_resp = await client.get(f"/api/v1/plans/{plan_id}/versions/1")
        assert v1_resp.status_code == 200
        assert v1_resp.json()["version"] == 1
        assert len(v1_resp.json()["steps"]) == 1

        # 4. Check version history list
        hist_resp = await client.get(f"/api/v1/plans/{plan_id}/versions")
        assert hist_resp.status_code == 200
        hist_data = hist_resp.json()
        assert len(hist_data["versions"]) == 2
        v_nums = [v["version"] for v in hist_data["versions"]]
        assert v_nums == [2, 1]


@pytest.mark.integration
class TestPlanApprovalAndTOCTOU:
    @pytest.mark.asyncio
    async def test_approve_exact_valid_version(self, client: AsyncClient) -> None:
        await _register_and_login(client, "approver@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Approve Proj"})
        project_id = proj_resp.json()["id"]

        create_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "To Approve",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload()],
            },
        )
        plan_id = create_resp.json()["plan_id"]

        # Approve version 1
        app_resp = await client.post(
            f"/api/v1/plans/{plan_id}/approve",
            json={"version": 1},
        )
        assert app_resp.status_code == 200
        data = app_resp.json()
        assert data["status"] == "approved"
        assert data["version"] == 1
        assert data["approved_by"] is not None
        assert data["approved_at"] is not None

    @pytest.mark.asyncio
    async def test_toctou_double_approval_or_stale_version_rejected(
        self, client: AsyncClient
    ) -> None:
        await _register_and_login(client, "toctou_tester@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "TOCTOU Proj"})
        project_id = proj_resp.json()["id"]

        create_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "TOCTOU Plan",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload()],
            },
        )
        plan_id = create_resp.json()["plan_id"]

        # 1. Approve v1 successfully
        ok_app = await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})
        assert ok_app.status_code == 200

        # 2. Attempting to approve v1 a second time must return 409 Conflict
        second_app = await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})
        assert second_app.status_code == 409
        assert second_app.json()["error"]["code"] == "CONFLICT"

        # 3. Attempting to approve non-existent v99 must return 404 Not Found
        missing_v = await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 99})
        assert missing_v.status_code == 404

    @pytest.mark.asyncio
    async def test_viewer_role_cannot_approve_plan(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _register_and_login(client, "owner_gate@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Gate Proj"})
        project_id = proj_resp.json()["id"]

        create_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Gate Plan",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload()],
            },
        )
        plan_id = create_resp.json()["plan_id"]

        # Log in as viewer
        await client.post("/api/v1/auth/logout")
        viewer_resp = await _register_and_login(client, "viewer_gate@test.com")
        viewer_id = viewer_resp["id"]

        # Add viewer membership
        db_session.add(
            ProjectMembership(
                id=str(uuid.uuid4()),
                project_id=project_id,
                user_id=viewer_id,
                role="viewer",
            )
        )
        await db_session.commit()

        # Viewer attempts approval -> 403 Forbidden
        app_resp = await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})
        assert app_resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cross_project_plan_access_returns_404(
        self, client: AsyncClient
    ) -> None:
        await _register_and_login(client, "proj_a_owner@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "A Proj"})
        p_a_id = proj_resp.json()["id"]

        c_resp = await client.post(
            f"/api/v1/projects/{p_a_id}/plans",
            json={"name": "A Plan", "adapter_type": "http_api", "steps": [_valid_step_payload()]},
        )
        plan_id = c_resp.json()["plan_id"]

        # Switch to user B (different project)
        await client.post("/api/v1/auth/logout")
        await _register_and_login(client, "proj_b_owner@test.com")

        # User B attempts to access plan A -> 404
        assert (await client.get(f"/api/v1/plans/{plan_id}")).status_code == 404
        assert (await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})).status_code == 404


@pytest.mark.integration
class TestPlanAuditEvents:
    @pytest.mark.asyncio
    async def test_creation_and_approval_are_audited(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _register_and_login(client, "audit_plan_owner@test.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Audit Plan Proj"})
        project_id = proj_resp.json()["id"]

        c_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Audit Plan",
                "adapter_type": "http_api",
                "steps": [_valid_step_payload()],
            },
        )
        plan_id = c_resp.json()["plan_id"]

        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        # Query audit table
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.project_id == project_id)
            .order_by(AuditEvent.timestamp.asc())
        )
        result = await db_session.execute(stmt)
        events = list(result.scalars().all())

        event_types = [e.event_type for e in events]
        assert "plan_created" in event_types
        assert "plan_approved" in event_types
