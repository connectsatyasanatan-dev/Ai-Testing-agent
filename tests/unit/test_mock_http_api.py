"""
tests/unit/test_mock_http_api.py
================================
Tests for the deterministic mock HTTP API fixture (Task P1-00).

Verifies:
  1. All seven required routes are registered and return expected behavior.
  2. The create route handles valid, malformed, and invalid payloads.
  3. The slow route can exercise timeout behavior without excessive waiting.
  4. The failure route returns expected 500 status and safe error envelope.
  5. The schema mismatch route returns valid JSON with the intentional mismatch.
  6. Redirect routes return redirect responses without issuing outbound calls.
  7. The app works in-process through httpx.ASGITransport.
  8. Test isolation and request recorder semantics.
"""
from __future__ import annotations

import httpx
import pytest

from tests.fixtures.mock_http_api import create_mock_api_app


@pytest.fixture
def mock_app():
    return create_mock_api_app()


@pytest.fixture
async def mock_client(mock_app):
    transport = httpx.ASGITransport(app=mock_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mock-api.local") as client:
        yield client


@pytest.mark.unit
class TestMockHttpApiRoutes:
    @pytest.mark.asyncio
    async def test_get_ok_returns_200_and_stable_json(self, mock_client: httpx.AsyncClient) -> None:
        resp = await mock_client.get("/ok")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/json"
        assert resp.headers["x-mock-api"] == "true"
        body = resp.json()
        assert body["status"] == "ok"
        assert body["message"] == "Operation completed successfully"
        assert body["data"]["id"] == "item_123"
        assert body["data"]["name"] == "Deterministic Widget"
        assert body["data"]["count"] == 42
        assert body["data"]["active"] is True

    @pytest.mark.asyncio
    async def test_post_create_valid_payload_returns_201(
        self, mock_client: httpx.AsyncClient, mock_app
    ) -> None:
        payload = {"name": "New Test Item", "price": 99.9}
        resp = await mock_client.post("/create", json=payload)
        assert resp.status_code == 201
        assert resp.headers["location"] == "/items/created_456"
        body = resp.json()
        assert body["id"] == "created_456"
        assert body["status"] == "created"
        assert body["received"] == payload

        # Verify request recorded
        assert len(mock_app.state.recorder.requests) == 1
        assert mock_app.state.recorder.requests[0]["method"] == "POST"

    @pytest.mark.asyncio
    async def test_post_create_malformed_json_returns_400(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        resp = await mock_client.post(
            "/create",
            content=b"{malformed json...",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"]["code"] == "INVALID_JSON"

    @pytest.mark.asyncio
    async def test_post_create_empty_or_non_dict_returns_422(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        resp1 = await mock_client.post("/create", json={})
        assert resp1.status_code == 422
        assert resp1.json()["error"]["code"] == "VALIDATION_ERROR"

        resp2 = await mock_client.post("/create", json=["list", "not", "dict"])
        assert resp2.status_code == 422

    @pytest.mark.asyncio
    async def test_get_slow_exercises_controlled_delay_and_timeout(
        self, mock_app
    ) -> None:
        transport = httpx.ASGITransport(app=mock_app)

        # 1. Successful request with small delay (0.01s)
        async with httpx.AsyncClient(transport=transport, base_url="http://mock-api.local", timeout=1.0) as client:
            resp = await client.get("/slow?delay=0.01")
            assert resp.status_code == 200
            assert resp.json()["status"] == "slow_response"
            assert resp.json()["delayed_seconds"] == 0.01

        # 2. Client timeout triggered when delay exceeds client timeout limit
        async with httpx.AsyncClient(transport=transport, base_url="http://mock-api.local", timeout=0.02) as client:
            with pytest.raises(httpx.TimeoutException):
                await client.get("/slow?delay=0.2")

    @pytest.mark.asyncio
    async def test_get_fail_returns_500_with_safe_json(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        resp = await mock_client.get("/fail")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "INTERNAL_SERVER_ERROR"
        assert body["error"]["message"] == "Simulated upstream server failure"
        # Ensure no traceback or internal leak
        assert "traceback" not in resp.text.lower()
        assert "secret" not in resp.text.lower()

    @pytest.mark.asyncio
    async def test_get_schema_mismatch_returns_200_with_intentional_mismatch(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        resp = await mock_client.get("/schema-mismatch")
        assert resp.status_code == 200
        body = resp.json()
        assert body["unexpected_format"] is True
        assert body["items"] == "should_be_a_list_but_is_a_string"
        assert body["count"] == "should_be_an_int_42"
        assert body["nested"] is None

    @pytest.mark.asyncio
    async def test_get_redirect_returns_302_without_outbound_calls(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        # Client configured with follow_redirects=False to inspect the 302 directly
        resp = await mock_client.get("/redirect", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/ok"

    @pytest.mark.asyncio
    async def test_get_redirect_with_custom_target(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        resp = await mock_client.get("/redirect?target=/custom-path", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/custom-path"

    @pytest.mark.asyncio
    async def test_get_private_redirect_returns_302_to_metadata_ip(
        self, mock_client: httpx.AsyncClient
    ) -> None:
        resp = await mock_client.get("/private-redirect", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "http://169.254.169.254/latest/meta-data/"

    @pytest.mark.asyncio
    async def test_recorder_reset(self, mock_client: httpx.AsyncClient, mock_app) -> None:
        await mock_client.get("/ok")
        assert len(mock_app.state.recorder.requests) == 1
        mock_app.state.recorder.reset()
        assert len(mock_app.state.recorder.requests) == 0
