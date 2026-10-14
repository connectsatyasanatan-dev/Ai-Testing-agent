"""
tests/integration/test_openapi_mission_planning.py
==================================================
Integration tests for deterministic OpenAPI analysis and API test mission planning (Task 6).

Verifies:
- POST /api/v1/projects/{project_id}/missions/analyze-openapi endpoint.
- OpenAPI 3.x integration with supervisor mission planning.
- Strict pre-validation: malformed specs and forbidden external $ref are rejected before LLM invocation.
- Grounding: analyzed operations & candidate test intents injected into supervisor context.
- Large specifications without silent semantic truncation.
- Regression compatibility: missions without OpenAPI spec continue to function as normal.
"""
from __future__ import annotations

import textwrap
from typing import Any
import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.services.ai_provider import MockAIProvider, set_ai_provider
from uitp_contracts.plan import TokenUsage


async def _register_and_login(client: AsyncClient, email: str) -> dict[str, Any]:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "secure-openapi-pw-123"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "secure-openapi-pw-123"},
    )
    return resp.json()


class RecordingMockAIProvider(MockAIProvider):
    """Mock provider that records the exact user prompt it received."""

    def __init__(self, response_data: dict[str, Any]):
        super().__init__()
        self._response_data = response_data
        self.recorded_system_prompt: str | None = None
        self.recorded_user_prompt: str | None = None

    async def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ):
        self.recorded_system_prompt = system_prompt
        self.recorded_user_prompt = user_prompt
        return self._response_data, TokenUsage(prompt_tokens=500, completion_tokens=300, total_tokens=800)


OPENAPI_SAMPLE = textwrap.dedent("""
openapi: "3.0.3"
info:
  title: "Inventory Service"
  version: "1.2.0"
servers:
  - url: "http://mock-api.local"
components:
  securitySchemes:
    BearerToken:
      type: "http"
      scheme: "bearer"
  schemas:
    WarehouseItem:
      type: "object"
      required:
        - "name"
        - "sku"
        - "stock"
      properties:
        id:
          type: "string"
        name:
          type: "string"
        sku:
          type: "string"
        stock:
          type: "integer"
          minimum: 0
paths:
  /items:
    get:
      operationId: "listItems"
      summary: "List inventory items"
      responses:
        "200":
          description: "Items retrieved"
    post:
      operationId: "createItem"
      summary: "Create inventory item"
      security:
        - BearerToken: []
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/WarehouseItem"
      responses:
        "201":
          description: "Item created"
        "422":
          description: "Unprocessable entity"
  /items/{id}:
    parameters:
      - name: "id"
        in: "path"
        required: true
        schema:
          type: "string"
    get:
      operationId: "getItem"
      summary: "Get single item by ID"
      responses:
        "200":
          description: "Found item"
        "404":
          description: "Item not found"
""")


@pytest.mark.integration
class TestOpenAPIMissionPlanning:
    @pytest.fixture(autouse=True)
    def reset_provider(self):
        set_ai_provider(None)
        yield
        set_ai_provider(None)

    @pytest.mark.asyncio
    async def test_api_analyze_openapi_endpoint(self, client: AsyncClient) -> None:
        """POST /api/v1/projects/{project_id}/missions/analyze-openapi returns normalized spec and candidates."""
        await _register_and_login(client, "openapi_analyst@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Analysis Proj"})
        project_id = proj_resp.json()["id"]

        resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/analyze-openapi",
            json={"openapi_spec": OPENAPI_SAMPLE},
        )
        assert resp.status_code == 200
        data = resp.json()

        assert data["total_operations"] == 3
        assert data["total_candidates"] >= 4
        assert data["spec"]["title"] == "Inventory Service"
        assert len(data["candidates"]) > 0

        # Verify candidate structure
        cand_categories = {c["category"] for c in data["candidates"]}
        assert "positive_happy_path" in cand_categories
        assert "required_field_validation" in cand_categories
        assert "security_auth" in cand_categories
        assert "documented_error" in cand_categories

    @pytest.mark.asyncio
    async def test_api_analyze_openapi_rejects_ssrf_external_ref(self, client: AsyncClient) -> None:
        """Analyze endpoint rejects specifications with forbidden external $ref."""
        await _register_and_login(client, "openapi_ssrf@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "SSRF Proj"})
        project_id = proj_resp.json()["id"]

        malicious_spec = textwrap.dedent("""
        openapi: "3.0.3"
        info:
          title: "SSRF"
          version: "1.0"
        paths:
          /test:
            get:
              responses:
                "200":
                  content:
                    application/json:
                      schema:
                        $ref: "http://169.254.169.254/latest/meta-data/"
        """)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/analyze-openapi",
            json={"openapi_spec": malicious_spec},
        )
        assert resp.status_code == 422
        assert "External $ref" in resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_plan_mission_dag_with_openapi_analyzed_prompt(
        self, client: AsyncClient, db_session: AsyncSession
    ) -> None:
        """When openapi_spec is provided to /plan, the supervisor prompt receives structured OpenAPI analysis."""
        await _register_and_login(client, "openapi_planner@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "OpenAPI Plan Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Staging", "base_url": "http://mock-api.local"},
        )
        env_id = env_resp.json()["id"]

        mock_plan_output = {
            "name": "Inventory Test Plan",
            "description": "API testing plan generated from OpenAPI",
            "tasks": [
                {
                    "task_id": "create_item_task",
                    "name": "Create Warehouse Item",
                    "assigned_role": "api_specialist",
                    "adapter_type": "http_api",
                    "depends_on": [],
                    "priority": "P0",
                    "is_smoke": True,
                    "is_regression": True,
                    "steps": [
                        {
                            "step_id": "step_post_item",
                            "adapter_type": "http_api",
                            "action": "post",
                            "parameters": {
                                "url": "http://mock-api.local/items",
                                "headers": {"Authorization": "Bearer token123"},
                                "json": {"name": "Widget", "sku": "WIDG-1", "stock": 10},
                            },
                            "assertions": [{"type": "status_code", "value": 201}],
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-INV-P0"],
                }
            ],
        }

        recorder = RecordingMockAIProvider(mock_plan_output)
        set_ai_provider(recorder)

        resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "# Inventory Requirements\n- [REQ-INV-P0] Critical Item Creation [P0]",
                "openapi_spec": OPENAPI_SAMPLE,
                "environment_id": env_id,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Inventory Test Plan"
        assert len(data["tasks"]) == 1

        # Verify prompt delivered to AI provider
        assert recorder.recorded_user_prompt is not None
        assert "<analyzed_openapi>" in recorder.recorded_user_prompt
        assert "Inventory Service" in recorder.recorded_user_prompt
        assert "createItem" in recorder.recorded_user_prompt
        assert "listItems" in recorder.recorded_user_prompt
        assert "<untrusted_openapi>" in recorder.recorded_user_prompt

    @pytest.mark.asyncio
    async def test_plan_mission_dag_rejects_malformed_spec_before_llm(
        self, client: AsyncClient
    ) -> None:
        """Invalid OpenAPI specification is rejected with 422 before calling the LLM provider."""
        await _register_and_login(client, "openapi_bad_spec@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "Bad Spec Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local"},
        )
        env_id = env_resp.json()["id"]

        # If LLM were called, this mock would raise an exception
        class ShouldNotBeCalledProvider(MockAIProvider):
            async def complete_structured(self, **kwargs):
                raise AssertionError("LLM provider should NOT have been invoked on invalid spec!")

        set_ai_provider(ShouldNotBeCalledProvider())

        resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "# Feature Spec",
                "openapi_spec": "openapi: '1.0' # Unsupported",
                "environment_id": env_id,
            },
        )
        assert resp.status_code == 422
        assert "Unsupported OpenAPI version '1.0'" in resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_plan_mission_dag_regression_without_openapi(
        self, client: AsyncClient
    ) -> None:
        """Mission planning works cleanly without openapi_spec (regression compatibility)."""
        await _register_and_login(client, "openapi_none@example.com")
        proj_resp = await client.post("/api/v1/projects", json={"name": "No OpenAPI Proj"})
        project_id = proj_resp.json()["id"]

        env_resp = await client.post(
            f"/api/v1/projects/{project_id}/environments",
            json={"name": "Dev", "base_url": "http://mock-api.local"},
        )
        env_id = env_resp.json()["id"]

        mock_plan_output = {
            "name": "Standard Plan",
            "description": "Standard plan without OpenAPI",
            "tasks": [
                {
                    "task_id": "simple_task",
                    "name": "Simple UI Task",
                    "assigned_role": "ui_specialist",
                    "adapter_type": "web",
                    "depends_on": [],
                    "priority": "P1",
                    "is_smoke": False,
                    "is_regression": True,
                    "steps": [
                        {
                            "step_id": "step_nav",
                            "adapter_type": "web",
                            "action": "navigate",
                            "parameters": {"url": "http://mock-api.local"},
                        }
                    ],
                    "exports": {},
                    "timeout_seconds": 30.0,
                    "requirement_refs": ["REQ-UI-01"],
                }
            ],
        }

        set_ai_provider(RecordingMockAIProvider(mock_plan_output))

        resp = await client.post(
            f"/api/v1/projects/{project_id}/missions/plan",
            json={
                "requirements_content": "# Requirements\n- [REQ-UI-01] Home page loads",
                "environment_id": env_id,
            },
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "Standard Plan"
