"""
tests/unit/test_health.py
=========================
Unit tests for /health (liveness) endpoint.
/ready is tested in tests/integration/test_health_with_db.py because
it now performs a real database ping.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Set required env vars BEFORE importing anything from uitp_api.
os.environ.setdefault("UITP_SECRET_KEY", "unit-test-secret-key-exactly-32-chars!!")
os.environ.setdefault("UITP_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("UITP_APP_ENV", "test")

# Patch init_engine so the unit test never tries to connect to a database.
with patch("uitp_api.database.init_engine", return_value=AsyncMock()):
    from uitp_api.main import create_app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    app = create_app()
    return TestClient(app, raise_server_exceptions=True)


@pytest.mark.unit
class TestHealthEndpoint:
    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/health").status_code == 200

    def test_status_is_ok(self, client: TestClient) -> None:
        assert client.get("/health").json()["status"] == "ok"

    def test_returns_version_string(self, client: TestClient) -> None:
        body = client.get("/health").json()
        assert isinstance(body["version"], str)
        assert len(body["version"]) > 0

    def test_no_auth_required(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code not in (401, 403)
