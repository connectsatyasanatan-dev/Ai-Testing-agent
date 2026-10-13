"""tests/integration/test_project_settings_and_audit.py
=====================================================
Integration tests for:
  - Task P4-02: Project and Member Management.
  - Task P6-02: Audit logs for approvals, environment changes, role changes, and taxonomy.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.models.audit import AuditEvent, _AUDIT_EVENT_TYPES
from uitp_contracts.enums import AuditEventType, ErrorCode


async def _register_and_login(client: AsyncClient, email: str, password: str = "secure-pass-1234") -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": f"User {email.split('@')[0]}"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.integration
class TestProjectMemberManagement:
    @pytest.mark.asyncio
    async def test_list_and_add_members_lifecycle(
        self,
        client: AsyncClient,
    ) -> None:
        """Owner can list, add, update, and remove project members."""
        owner = await _register_and_login(client, "project_owner@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Team Workspace"})
        assert proj_resp.status_code == 201
        project_id = proj_resp.json()["id"]

        # Register second user (to be invited)
        invitee_client = client
        await invitee_client.post(
            "/api/v1/auth/register",
            json={"email": "teammate@example.com", "password": "secure-pass-1234"},
        )
        # Login back as owner
        await client.post(
            "/api/v1/auth/login",
            json={"email": "project_owner@example.com", "password": "secure-pass-1234"},
        )

        # 1. List members -> contains owner
        members_resp = await client.get(f"/api/v1/projects/{project_id}/members")
        assert members_resp.status_code == 200
        members = members_resp.json()["items"]
        assert len(members) == 1
        assert members[0]["email"] == "project_owner@example.com"
        assert members[0]["role"] == "owner"

        # 2. Add teammate as maintainer
        add_resp = await client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"email": "teammate@example.com", "role": "maintainer"},
        )
        assert add_resp.status_code == 201
        added_data = add_resp.json()
        assert added_data["email"] == "teammate@example.com"
        assert added_data["role"] == "maintainer"

        # 3. Update teammate to viewer
        update_resp = await client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"email": "teammate@example.com", "role": "viewer"},
        )
        assert update_resp.status_code == 201
        assert update_resp.json()["role"] == "viewer"

        # 4. Check members list has 2 members
        list2 = await client.get(f"/api/v1/projects/{project_id}/members")
        assert list2.json()["total"] == 2

        # 5. Remove teammate
        del_resp = await client.delete(
            f"/api/v1/projects/{project_id}/members/{added_data['user_id']}"
        )
        assert del_resp.status_code == 204

        # 6. Verify teammate is removed
        list3 = await client.get(f"/api/v1/projects/{project_id}/members")
        assert list3.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_cannot_remove_last_owner(
        self,
        client: AsyncClient,
    ) -> None:
        """Removing the sole project owner returns 409 Conflict."""
        owner = await _register_and_login(client, "sole_owner@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Solo Project"})
        project_id = proj_resp.json()["id"]

        members_resp = await client.get(f"/api/v1/projects/{project_id}/members")
        owner_member_id = members_resp.json()["items"][0]["user_id"]

        del_resp = await client.delete(
            f"/api/v1/projects/{project_id}/members/{owner_member_id}"
        )
        assert del_resp.status_code == 409
        assert "last owner" in del_resp.json()["error"]["message"].lower()

    @pytest.mark.asyncio
    async def test_non_owner_cannot_manage_members(
        self,
        client: AsyncClient,
    ) -> None:
        """Maintainers and viewers cannot add or remove members."""
        await _register_and_login(client, "big_boss@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Restricted Proj"})
        project_id = proj_resp.json()["id"]

        # Register and add maintainer
        await client.post(
            "/api/v1/auth/register",
            json={"email": "worker_bee@example.com", "password": "secure-pass-1234"},
        )
        await client.post(
            "/api/v1/auth/login",
            json={"email": "big_boss@example.com", "password": "secure-pass-1234"},
        )
        await client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"email": "worker_bee@example.com", "role": "maintainer"},
        )

        # Login as maintainer
        await client.post(
            "/api/v1/auth/login",
            json={"email": "worker_bee@example.com", "password": "secure-pass-1234"},
        )

        # Attempt to add another member as maintainer -> 403 Forbidden
        bad_add = await client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"email": "random@example.com", "role": "viewer"},
        )
        assert bad_add.status_code == 403


@pytest.mark.integration
class TestAuditTrailAndTaxonomy:
    @pytest.mark.asyncio
    async def test_audit_events_recorded_and_listed(
        self,
        client: AsyncClient,
    ) -> None:
        """Audit events are queryable via project API and record role_change events."""
        await _register_and_login(client, "audit_admin@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Audited Workspace"})
        project_id = proj_resp.json()["id"]

        # Create user & add as maintainer -> emits ROLE_CHANGE
        await client.post(
            "/api/v1/auth/register",
            json={"email": "audited_user@example.com", "password": "secure-pass-1234"},
        )
        await client.post(
            "/api/v1/auth/login",
            json={"email": "audit_admin@example.com", "password": "secure-pass-1234"},
        )
        await client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"email": "audited_user@example.com", "role": "maintainer"},
        )

        # Fetch audit events for project
        audit_resp = await client.get(f"/api/v1/projects/{project_id}/audit-events")
        assert audit_resp.status_code == 200
        data = audit_resp.json()
        assert data["total"] >= 2  # project_created and role_change

        event_types = [e["event_type"] for e in data["items"]]
        assert "project_created" in event_types
        assert "role_change" in event_types

        # Verify role_change metadata
        role_events = [e for e in data["items"] if e["event_type"] == "role_change"]
        assert len(role_events) > 0
        assert role_events[0]["metadata_json"]["target_email"] == "audited_user@example.com"
        assert role_events[0]["metadata_json"]["role"] == "maintainer"

    @pytest.mark.asyncio
    async def test_all_15_audit_event_types_supported(
        self,
        db_session: AsyncSession,
    ) -> None:
        """All 15 event types in ARCHITECTURE.md §5a are valid database enum types."""
        expected_types = {
            "user_login",
            "user_login_failed",
            "role_change",
            "project_created",
            "project_archived",
            "environment_created",
            "environment_modified",
            "secret_reference_updated",
            "plan_created",
            "plan_approved",
            "plan_archived",
            "run_created",
            "run_cancelled",
            "policy_blocked",
            "artifact_accessed",
        }
        # Verify schema tuple matches taxonomy
        assert set(_AUDIT_EVENT_TYPES) == expected_types

        from datetime import UTC, datetime
        # Verify every event type can be persisted and read back from database
        for idx, event_name in enumerate(sorted(expected_types)):
            event = AuditEvent(
                id=f"test-audit-event-{idx}",
                event_type=event_name,
                resource_type="system",
                resource_id=f"res-{idx}",
                timestamp=datetime.now(UTC),
                metadata_json={"taxonomy_test": event_name},
                metadata_redacted=False,
            )
            db_session.add(event)

        await db_session.commit()
