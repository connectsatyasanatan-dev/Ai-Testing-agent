"""
tests/integration/test_environments.py
======================================
Integration tests for Environment Management API.

Coverage:
  1. An authorized user (owner/maintainer) can create an environment.
  2. An authorized user can list project environments.
  3. An authorized user can update supported fields.
  4. Soft-deleted environments are excluded from normal listings.
  5. Cross-project access is rejected (404 masking).
  6. Insufficient project roles are rejected (403 for viewer on create/update).
  7. Invalid payloads and malformed URLs are rejected (422).
  8. secret_refs are handled as references and are not leaked in audit data.
  9. Router registration and end-to-end client responses.
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


@pytest.mark.integration
class TestCreateEnvironment:
    @pytest.mark.asyncio
    async def test_authorized_user_can_create_environment(
        self, client: AsyncClient
    ) -> None:
        await _register_and_login(client, "owner@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Env Test Project"})
        project_id = proj_resp.json()["id"]

        payload = {
            "name": "Staging API",
            "base_url": "https://staging.api.example.com",
            "allowlist": ["staging.api.example.com:443", "auth.example.com:443"],
            "secret_refs": {"api_key": "vault://projects/envtest/keys/staging_key"},
            "description": "Staging testing target",
        }
        resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json=payload,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["name"] == "Staging API"
        assert data["base_url"] == "https://staging.api.example.com"
        assert data["project_id"] == project_id
        assert data["status"] == "active"
        assert data["allowlist"] == ["staging.api.example.com:443", "auth.example.com:443"]
        assert data["secret_refs"] == {"api_key": "vault://projects/envtest/keys/staging_key"}
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

    @pytest.mark.asyncio
    async def test_unauthenticated_request_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            f"/api/v1/projects/{uuid.uuid4()}/environments",
            json={"name": "Test", "base_url": "https://example.com"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_cross_project_create_returns_404(self, client: AsyncClient) -> None:
        # Create project under Alice
        await _register_and_login(client, "alice_cross@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Alice Proj"})
        project_id = proj_resp.json()["id"]

        # Switch to Bob (non-member)
        await client.post("/api/v1/auth/logout")
        await _register_and_login(client, "bob_cross@envtest.com")

        resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Bob Target", "base_url": "https://example.com"},
        )
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "RESOURCE_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_insufficient_role_viewer_cannot_create_environment(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        # Create project under Owner
        await _register_and_login(client, "owner_role@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Role Proj"})
        project_id = proj_resp.json()["id"]

        # Register Viewer user
        await client.post("/api/v1/auth/logout")
        viewer_login = await _register_and_login(client, "viewer_user@envtest.com")
        viewer_id = viewer_login["id"]

        # Add viewer membership to project
        membership = ProjectMembership(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id=viewer_id,
            role="viewer",
        )
        db_session.add(membership)
        await db_session.commit()

        # Viewer attempts to create environment
        resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Viewer Target", "base_url": "https://example.com"},
        )
        assert resp.status_code == 403
        assert resp.json()["error"]["code"] == "PERMISSION_DENIED"

    @pytest.mark.asyncio
    async def test_invalid_urls_rejected(self, client: AsyncClient) -> None:
        await _register_and_login(client, "url_tester@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "URL Proj"})
        project_id = proj_resp.json()["id"]

        invalid_urls = [
            "ftp://example.com",
            "not-a-url",
            "http://",
            "http://user:password@example.com",
            "",
        ]
        for bad_url in invalid_urls:
            resp = await client.post(
                f"/api/v1/projects/{project_id}/environments",
                json={"name": "Bad URL", "base_url": bad_url},
            )
            assert resp.status_code == 422, f"Expected 422 for {bad_url}, got {resp.status_code}"

    @pytest.mark.asyncio
    async def test_secret_refs_not_leaked_in_audit_records(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _register_and_login(client, "audit_tester@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Audit Proj"})
        project_id = proj_resp.json()["id"]

        secret_ref_val = "vault://secret-vault/token/topsecret-999"
        resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={
                "name": "Audit Target",
                "base_url": "https://api.example.com",
                "secret_refs": {"internal_key": secret_ref_val},
            },
        )
        assert resp.status_code == 201

        # Check audit records in database
        stmt = (
            select(AuditEvent)
            .where(AuditEvent.project_id == project_id)
            .order_by(AuditEvent.timestamp.desc())
        )
        result = await db_session.execute(stmt)
        events = list(result.scalars().all())

        env_events = [e for e in events if e.event_type == "environment_created"]
        assert len(env_events) == 1
        audit_meta = env_events[0].metadata_json or {}
        # Ensure only the key is logged, NOT the reference value
        assert audit_meta.get("secret_ref_keys") == ["internal_key"]
        assert secret_ref_val not in str(audit_meta)


@pytest.mark.integration
class TestListEnvironments:
    @pytest.mark.asyncio
    async def test_authorized_user_can_list_environments(self, client: AsyncClient) -> None:
        await _register_and_login(client, "lister@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "List Proj"})
        project_id = proj_resp.json()["id"]

        await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Env 1", "base_url": "https://env1.example.com"},
        )
        await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Env 2", "base_url": "https://env2.example.com"},
        )

        resp = await client.get(f"/api/v1/projects/{project_id}/environments")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        names = {item["name"] for item in data["items"]}
        assert names == {"Env 1", "Env 2"}

    @pytest.mark.asyncio
    async def test_soft_deleted_environment_excluded_from_list(
        self, client: AsyncClient
    ) -> None:
        await _register_and_login(client, "archiver@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Archive Proj"})
        project_id = proj_resp.json()["id"]

        e1 = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Active Env", "base_url": "https://active.example.com"},
        )
        e2 = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "To Archive", "base_url": "https://archive.example.com"},
        )
        e2_id = e2.json()["id"]

        # Soft-delete e2 via PATCH status="archived"
        patch_resp = await client.patch(
            f"/api/v1/projects/{project_id}/environments/{e2_id}",
            json={"status": "archived"},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["status"] == "archived"

        # List environments -> only Active Env returned
        list_resp = await client.get(f"/api/v1/projects/{project_id}/environments")
        assert list_resp.status_code == 200
        data = list_resp.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "Active Env"

    @pytest.mark.asyncio
    async def test_viewer_can_list_environments(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _register_and_login(client, "owner_v@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Viewer List Proj"})
        project_id = proj_resp.json()["id"]

        await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Visible Env", "base_url": "https://visible.example.com"},
        )

        await client.post("/api/v1/auth/logout")
        viewer_resp = await _register_and_login(client, "viewer_lister@envtest.com")
        viewer_id = viewer_resp["id"]

        # Add viewer role
        db_session.add(
            ProjectMembership(
                id=str(uuid.uuid4()),
                project_id=project_id,
                user_id=viewer_id,
                role="viewer",
            )
        )
        await db_session.commit()

        # Viewer can list environments
        resp = await client.get(f"/api/v1/projects/{project_id}/environments")
        assert resp.status_code == 200
        assert resp.json()["total"] == 1

    @pytest.mark.asyncio
    async def test_cross_project_list_returns_404(self, client: AsyncClient) -> None:
        await _register_and_login(client, "p1_owner@envtest.com")
        p1 = await client.post("/api/v1/projects", json={"name": "P1"})
        p1_id = p1.json()["id"]

        await client.post("/api/v1/auth/logout")
        await _register_and_login(client, "p2_owner@envtest.com")

        resp = await client.get(f"/api/v1/projects/{p1_id}/environments")
        assert resp.status_code == 404


@pytest.mark.integration
class TestGetAndUpdateEnvironment:
    @pytest.mark.asyncio
    async def test_get_single_environment(self, client: AsyncClient) -> None:
        await _register_and_login(client, "getter@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Get Proj"})
        project_id = proj_resp.json()["id"]

        c_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Get Target", "base_url": "https://get.example.com"},
        )
        env_id = c_resp.json()["id"]

        get_resp = await client.get(f"/api/v1/projects/{project_id}/environments/{env_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == env_id

    @pytest.mark.asyncio
    async def test_update_supported_fields(self, client: AsyncClient) -> None:
        await _register_and_login(client, "updater@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Update Proj"})
        project_id = proj_resp.json()["id"]

        c_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Initial Name", "base_url": "https://initial.example.com"},
        )
        env_id = c_resp.json()["id"]

        patch_resp = await client.patch(
            f"/api/v1/projects/{project_id}/environments/{env_id}",
            json={
                "name": "Updated Name",
                "base_url": "https://updated.example.com",
                "allowlist": ["updated.example.com:443"],
                "description": "Updated description",
            },
        )
        assert patch_resp.status_code == 200
        data = patch_resp.json()
        assert data["name"] == "Updated Name"
        assert data["base_url"] == "https://updated.example.com"
        assert data["allowlist"] == ["updated.example.com:443"]
        assert data["description"] == "Updated description"

    @pytest.mark.asyncio
    async def test_viewer_cannot_update_environment(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        await _register_and_login(client, "owner_up@envtest.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Patch Proj"})
        project_id = proj_resp.json()["id"]

        c_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Patch Target", "base_url": "https://patch.example.com"},
        )
        env_id = c_resp.json()["id"]

        await client.post("/api/v1/auth/logout")
        v_resp = await _register_and_login(client, "viewer_up@envtest.com")
        viewer_id = v_resp["id"]

        db_session.add(
            ProjectMembership(
                id=str(uuid.uuid4()),
                project_id=project_id,
                user_id=viewer_id,
                role="viewer",
            )
        )
        await db_session.commit()

        resp = await client.patch(
            f"/api/v1/projects/{project_id}/environments/{env_id}",
            json={"name": "Hacked"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_cross_project_patch_returns_404(self, client: AsyncClient) -> None:
        await _register_and_login(client, "user_a@envtest.com")
        p_a = await client.post("/api/v1/projects", json={"name": "Proj A"})
        p_a_id = p_a.json()["id"]

        c_resp = await client.post(
            f"/api/v1/projects/{p_a_id}/environments",
            json={"name": "Env A", "base_url": "https://a.example.com"},
        )
        env_a_id = c_resp.json()["id"]

        await client.post("/api/v1/auth/logout")
        await _register_and_login(client, "user_b@envtest.com")

        # User B attempts to patch user A's environment
        resp = await client.patch(
            f"/api/v1/projects/{p_a_id}/environments/{env_a_id}",
            json={"name": "Infiltrate"},
        )
        assert resp.status_code == 404
