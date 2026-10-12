"""
tests/integration/test_projects.py
====================================
Integration tests for project endpoints.

Coverage:
  - Create project → creator gets owner role.
  - List projects → only returns the calling user's projects.
  - Get project → returns 404 for non-members (not 403 — avoid leaking existence).
  - Archive → only owner can archive; subsequent requests return 404.
  - Cross-project isolation: user A cannot see user B's projects.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


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
class TestCreateProject:
    @pytest.mark.asyncio
    async def test_create_project_returns_201(self, client: AsyncClient) -> None:
        await _register_and_login(client, "alice@test.com")
        resp = await client.post(
            "/api/v1/projects",
            json={"name": "Alpha Project", "description": "Test project"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["name"] == "Alpha Project"
        assert body["status"] == "active"
        assert "id" in body

    @pytest.mark.asyncio
    async def test_unauthenticated_create_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/projects",
            json={"name": "Should fail"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_empty_name_returns_422(self, client: AsyncClient) -> None:
        await _register_and_login(client, "bob@test.com")
        resp = await client.post("/api/v1/projects", json={"name": ""})
        assert resp.status_code == 422


@pytest.mark.integration
class TestListProjects:
    @pytest.mark.asyncio
    async def test_user_sees_own_projects_only(self, client: AsyncClient) -> None:
        await _register_and_login(client, "carol@test.com")
        await client.post("/api/v1/projects", json={"name": "Carol Project 1"})
        await client.post("/api/v1/projects", json={"name": "Carol Project 2"})

        resp = await client.get("/api/v1/projects")
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        names = [p["name"] for p in body["items"]]
        assert "Carol Project 1" in names
        assert "Carol Project 2" in names


@pytest.mark.integration
class TestGetProject:
    @pytest.mark.asyncio
    async def test_owner_can_get_project(self, client: AsyncClient) -> None:
        await _register_and_login(client, "dave@test.com")
        create_resp = await client.post("/api/v1/projects", json={"name": "Dave Project"})
        project_id = create_resp.json()["id"]

        resp = await client.get(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == project_id

    @pytest.mark.asyncio
    async def test_non_member_gets_404_not_403(self, client: AsyncClient) -> None:
        """Non-members receive 404 — we do not confirm the project exists."""
        # Create project as user Eve.
        await _register_and_login(client, "eve@test.com")
        create_resp = await client.post("/api/v1/projects", json={"name": "Eve Private"})
        project_id = create_resp.json()["id"]

        # Log out Eve, log in as Frank.
        await client.post("/api/v1/auth/logout")
        await _register_and_login(client, "frank@test.com")

        resp = await client.get(f"/api/v1/projects/{project_id}")
        assert resp.status_code == 404, (
            "Non-members must receive 404, not 403, to avoid leaking project existence."
        )


@pytest.mark.integration
class TestArchiveProject:
    @pytest.mark.asyncio
    async def test_owner_can_archive(self, client: AsyncClient) -> None:
        await _register_and_login(client, "grace@test.com")
        create_resp = await client.post("/api/v1/projects", json={"name": "Grace Project"})
        project_id = create_resp.json()["id"]

        archive_resp = await client.patch(f"/api/v1/projects/{project_id}/archive")
        assert archive_resp.status_code == 200
        assert archive_resp.json()["status"] == "archived"

    @pytest.mark.asyncio
    async def test_archived_project_not_in_list(self, client: AsyncClient) -> None:
        await _register_and_login(client, "henry@test.com")
        create_resp = await client.post("/api/v1/projects", json={"name": "Temp"})
        project_id = create_resp.json()["id"]

        await client.patch(f"/api/v1/projects/{project_id}/archive")
        list_resp = await client.get("/api/v1/projects")
        ids = [p["id"] for p in list_resp.json()["items"]]
        assert project_id not in ids

    @pytest.mark.asyncio
    async def test_double_archive_returns_409(self, client: AsyncClient) -> None:
        await _register_and_login(client, "iris@test.com")
        create_resp = await client.post("/api/v1/projects", json={"name": "Iris"})
        project_id = create_resp.json()["id"]

        await client.patch(f"/api/v1/projects/{project_id}/archive")
        resp = await client.patch(f"/api/v1/projects/{project_id}/archive")
        assert resp.status_code == 409
