"""
tests/integration/test_artifacts.py
===================================
Integration tests for Evidence Storage and Project-Authorized Artifact Retrieval (Task P1-06B).

Verifies:
  1. Storing and retrieving valid evidence artifacts generated during test run execution.
  2. Artifact listing returns safe metadata and never leaks internal storage paths (object_key).
  3. Artifact download endpoint supports metadata + inline base64 content, and ?download=true raw binary streaming.
  4. Cross-project artifact access returns 404 (tenant-isolation masking; never reveals existence).
  5. Cross-project retrieval with valid artifact ID is rejected with 404.
  6. Unauthenticated requests return 401.
  7. Expiry enforcement: expired artifacts return 404, while run and step-attempt history are preserved.
  8. Missing or corrupted artifact storage payloads return safe 404 without leaking stack traces.
  9. Sensitive data redaction in stored logs (Authorization headers, tokens, cookies).
  10. ARTIFACT_ACCESSED audit event is recorded without payload or secrets.
"""
from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from tests.fixtures.mock_http_api import create_mock_api_app
from uitp_api.database import get_async_session
from uitp_api.models.audit import AuditEvent
from uitp_api.models.evidence import EvidenceArtifact
from uitp_contracts.enums import AuditEventType


async def _register_and_login(client: AsyncClient, email: str) -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "correct-horse-battery-artifact"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "correct-horse-battery-artifact"},
    )
    return resp.json()


async def _setup_project_and_env(client: AsyncClient, project_name: str = "Artifact Proj") -> tuple[str, str]:
    proj_resp = await client.post("/api/v1/projects", json={"name": project_name})
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

    env_resp = await client.post(
        f"/api/v1/projects/{project_id}/environments",
        json={
            "name": "Local Mock Env",
            "base_url": "http://mock-api.local",
            "allowlist": ["mock-api.local"],
        },
    )
    assert env_resp.status_code == 201
    env_id = env_resp.json()["id"]

    return project_id, env_id


def _valid_step_payload(step_id: str = "step_1", path: str = "/ok", headers: dict | None = None) -> dict:
    inputs = {"path": path}
    if headers:
        inputs["headers"] = headers
    return {
        "step_id": step_id,
        "adapter_type": "http_api",
        "action": "get",
        "inputs": inputs,
        "assertions": [{"type": "status_code", "value": 200}],
        "timeout_seconds": 10.0,
        "retry_policy": {"max_attempts": 1},
        "idempotency_class": "safe",
        "risk_level": "low",
        "description": f"Step {step_id}",
    }


@pytest.fixture(autouse=True)
def wire_mock_api_transport(app):
    """Wire in-process mock HTTP API into FastAPI app state for tests."""
    mock_app = create_mock_api_app()
    app.state.custom_adapter_transport = httpx.ASGITransport(app=mock_app)
    yield
    if hasattr(app.state, "custom_adapter_transport"):
        del app.state.custom_adapter_transport


@pytest.mark.integration
class TestArtifactRetrievalAndAuthorization:
    @pytest.mark.asyncio
    async def test_run_creates_and_retrieves_evidence_artifact(self, client: AsyncClient) -> None:
        await _register_and_login(client, "art_owner@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Evidence Project")

        # 1. Create and approve plan with sensitive headers in step
        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={
                "name": "Evidence Plan",
                "adapter_type": "http_api",
                "steps": [
                    _valid_step_payload(
                        "step_with_secret",
                        "/ok",
                        headers={"Authorization": "Bearer sensitive-secret-token", "X-Custom": "safe-val"},
                    )
                ],
            },
        )
        assert plan_resp.status_code == 201
        plan_id = plan_resp.json()["plan_id"]

        appr_resp = await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})
        assert appr_resp.status_code == 200

        # 2. Execute run
        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        assert run_resp.status_code == 201
        run_id = run_resp.json()["id"]

        # 3. List artifacts for run
        list_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts")
        assert list_resp.status_code == 200
        artifacts = list_resp.json()
        assert len(artifacts) >= 1

        artifact = artifacts[0]
        assert artifact["run_id"] == run_id
        assert artifact["type"] == "response_log"
        assert artifact["checksum"] is not None
        assert artifact["size_bytes"] > 0
        assert artifact["redaction_status"] in ("redacted", "not_required")

        # Critical: object_key must never be exposed to clients
        assert "object_key" not in artifact

        artifact_id = artifact["id"]

        # 4. Retrieve artifact metadata and inline content
        get_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}")
        assert get_resp.status_code == 200
        body = get_resp.json()
        assert body["artifact"]["id"] == artifact_id
        assert body["inline_content"] is not None

        # Verify inline base64 content decodes and shows sensitive header is REDACTED
        decoded_bytes = base64.b64decode(body["inline_content"])
        payload_data = json.loads(decoded_bytes.decode("utf-8"))
        assert payload_data["step_id"] == "step_with_secret"
        assert "sensitive-secret-token" not in str(payload_data)
        assert payload_data["request"]["headers"]["Authorization"] == "[REDACTED]"
        assert payload_data["request"]["headers"]["X-Custom"] == "safe-val"

        # 5. Retrieve raw binary payload using ?download=true
        download_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}?download=true")
        assert download_resp.status_code == 200
        assert "attachment" in download_resp.headers.get("content-disposition", "")
        assert download_resp.content == decoded_bytes

    @pytest.mark.asyncio
    async def test_cross_project_artifact_access_denied_with_404(self, client: AsyncClient) -> None:
        # User A setup
        await _register_and_login(client, "user_a@test.com")
        proj_a, env_a = await _setup_project_and_env(client, "Project A")

        plan_resp = await client.post(
            f"/api/v1/projects/{proj_a}/plans",
            json={"name": "Plan A", "adapter_type": "http_api", "steps": [_valid_step_payload()]},
        )
        plan_a_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_a_id}/approve", json={"version": 1})

        run_resp = await client.post(
            f"/api/v1/plans/{plan_a_id}/runs",
            json={"environment_id": env_a, "plan_version": 1},
        )
        run_a_id = run_resp.json()["id"]

        list_resp = await client.get(f"/api/v1/runs/{run_a_id}/artifacts")
        artifact_a_id = list_resp.json()[0]["id"]

        # User B setup (different user, different project)
        await _register_and_login(client, "user_b@test.com")
        proj_b, _ = await _setup_project_and_env(client, "Project B")

        # User B attempts to list artifacts of User A's run
        cross_list_resp = await client.get(f"/api/v1/runs/{run_a_id}/artifacts")
        # Tenant isolation: 404 masks existence of cross-project run
        assert cross_list_resp.status_code == 404

        # User B attempts to retrieve User A's artifact directly by ID
        cross_get_resp = await client.get(f"/api/v1/runs/{run_a_id}/artifacts/{artifact_a_id}")
        assert cross_get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unauthenticated_request_rejected(self, client: AsyncClient) -> None:
        # Fresh unauthenticated client (no session cookie)
        list_resp = await client.get("/api/v1/runs/dummy-run-id/artifacts")
        assert list_resp.status_code == 401

        get_resp = await client.get("/api/v1/runs/dummy-run-id/artifacts/dummy-artifact-id")
        assert get_resp.status_code == 401

    @pytest.mark.asyncio
    async def test_expired_artifact_returns_404_and_preserves_run_history(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        await _register_and_login(client, "expiry_user@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Expiry Project")

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={"name": "Expiry Plan", "adapter_type": "http_api", "steps": [_valid_step_payload()]},
        )
        plan_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        run_id = run_resp.json()["id"]

        list_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts")
        artifact_id = list_resp.json()[0]["id"]

        # Manually expire the artifact in database
        past_time = datetime.now(UTC) - timedelta(days=31)
        stmt = (
            update(EvidenceArtifact)
            .where(EvidenceArtifact.id == artifact_id)
            .values(retention_until=past_time)
        )
        await db_session.execute(stmt)
        await db_session.commit()

        # 1. Retrieval of expired artifact returns 404
        get_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}")
        assert get_resp.status_code == 404
        assert "expired" in get_resp.text.lower()

        # 2. Run record and step attempts MUST still be preserved!
        run_check = await client.get(f"/api/v1/runs/{run_id}")
        assert run_check.status_code == 200
        run_data = run_check.json()
        assert run_data["id"] == run_id
        assert len(run_data["step_attempts"]) >= 1

    @pytest.mark.asyncio
    async def test_missing_or_corrupt_payload_handled_safely(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        await _register_and_login(client, "corrupt_user@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Corrupt Payload Project")

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={"name": "Corrupt Plan", "adapter_type": "http_api", "steps": [_valid_step_payload()]},
        )
        plan_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        run_id = run_resp.json()["id"]

        list_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts")
        artifact_id = list_resp.json()[0]["id"]

        # Tamper with checksum in database to simulate corrupted file
        stmt = (
            update(EvidenceArtifact)
            .where(EvidenceArtifact.id == artifact_id)
            .values(checksum="bad-tampered-checksum-value")
        )
        await db_session.execute(stmt)
        await db_session.commit()

        # Retrieval fails safely with 404 without crashing server or 500
        get_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}")
        assert get_resp.status_code == 404

    @pytest.mark.asyncio
    async def test_audit_event_recorded_on_artifact_access(
        self,
        client: AsyncClient,
        db_session: AsyncSession,
    ) -> None:
        await _register_and_login(client, "audit_user@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Audit Artifact Project")

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={"name": "Audit Plan", "adapter_type": "http_api", "steps": [_valid_step_payload()]},
        )
        plan_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        run_id = run_resp.json()["id"]

        list_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts")
        artifact_id = list_resp.json()[0]["id"]

        # Access the artifact
        get_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}")
        assert get_resp.status_code == 200

        # Query audit_events table to verify ARTIFACT_ACCESSED was recorded
        stmt = (
            select(AuditEvent)
            .where(
                AuditEvent.event_type == AuditEventType.ARTIFACT_ACCESSED.value,
                AuditEvent.resource_id == artifact_id,
            )
        )
        result = await db_session.execute(stmt)
        event = result.scalar_one_or_none()
        assert event is not None
        assert event.project_id == project_id
        assert event.resource_type == "artifact"
        # Verify no artifact body or secrets in audit metadata
        assert event.metadata_json is not None
        meta_str = str(event.metadata_json)
        assert "password" not in meta_str
        assert "token" not in meta_str
        assert "body" not in meta_str

    @pytest.mark.asyncio
    async def test_safe_response_headers_and_content_type_sanitization(
        self,
        client: AsyncClient,
    ) -> None:
        """Verify binary artifact downloads include strict security headers and prevent active content."""
        await _register_and_login(client, "headers_user@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Headers Project")

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={"name": "Headers Plan", "adapter_type": "http_api", "steps": [_valid_step_payload()]},
        )
        plan_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        run_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        run_id = run_resp.json()["id"]

        list_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts")
        artifact_id = list_resp.json()[0]["id"]

        # Raw binary download
        dl_resp = await client.get(f"/api/v1/runs/{run_id}/artifacts/{artifact_id}?download=true")
        assert dl_resp.status_code == 200

        # Safe security headers
        assert dl_resp.headers.get("x-content-type-options") == "nosniff"
        assert dl_resp.headers.get("content-security-policy") == "default-src 'none'; sandbox"
        assert dl_resp.headers.get("x-frame-options") == "DENY"
        assert "no-store" in dl_resp.headers.get("cache-control", "")
        assert "attachment" in dl_resp.headers.get("content-disposition", "")
        assert f"{artifact_id}.json" in dl_resp.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_artifact_ownership_swapped_id_denied_with_404(
        self,
        client: AsyncClient,
    ) -> None:
        """Verifies that accessing Run A with an artifact ID belonging to Run B returns 404."""
        await _register_and_login(client, "swap_user@test.com")
        project_id, env_id = await _setup_project_and_env(client, "Swap Project")

        plan_resp = await client.post(
            f"/api/v1/projects/{project_id}/plans",
            json={"name": "Swap Plan", "adapter_type": "http_api", "steps": [_valid_step_payload()]},
        )
        plan_id = plan_resp.json()["plan_id"]
        await client.post(f"/api/v1/plans/{plan_id}/approve", json={"version": 1})

        # Run 1
        r1_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        run1_id = r1_resp.json()["id"]
        r1_art_id = (await client.get(f"/api/v1/runs/{run1_id}/artifacts")).json()[0]["id"]

        # Run 2
        r2_resp = await client.post(
            f"/api/v1/plans/{plan_id}/runs",
            json={"environment_id": env_id, "plan_version": 1},
        )
        run2_id = r2_resp.json()["id"]

        # Attempt to access Run 1's artifact via Run 2's URL
        swapped_resp = await client.get(f"/api/v1/runs/{run2_id}/artifacts/{r1_art_id}")
        assert swapped_resp.status_code == 404

        # Unknown artifact ID
        unknown_resp = await client.get(f"/api/v1/runs/{run1_id}/artifacts/00000000-0000-0000-0000-000000000000")
        assert unknown_resp.status_code == 404
