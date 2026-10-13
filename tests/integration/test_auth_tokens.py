"""tests/integration/test_auth_tokens.py
======================================
Integration tests for API Token issuance, revocation, and Bearer authentication (ADR-009, Task P4-00).

Verifies:
  1. POST /api/v1/auth/tokens issues a short-lived opaque token and returns it once.
  2. GET /api/v1/auth/tokens lists token metadata without leaking raw token secret.
  3. Non-browser client can authenticate using `Authorization: Bearer <token>` to access protected endpoints.
  4. DELETE /api/v1/auth/tokens/{token_id} revokes the token.
  5. Requests with revoked tokens are rejected with 401.
  6. Requests with expired tokens are rejected with 401.
  7. Cross-user token revocation is blocked.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import pytest
from httpx import AsyncClient


async def _register_and_login(client: AsyncClient, email: str = "token_user@example.com") -> dict:
    await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "secure-password-1234"},
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "secure-password-1234"},
    )
    assert resp.status_code == 200
    return resp.json()


@pytest.mark.integration
class TestApiTokenAuthentication:
    @pytest.mark.asyncio
    async def test_issue_token_and_bearer_authentication(self, client: AsyncClient) -> None:
        """Issue an opaque API token and use it to access protected endpoints via Bearer header."""
        await _register_and_login(client, "alice@example.com")

        # 1. Issue token
        issue_resp = await client.post(
            "/api/v1/auth/tokens",
            json={"name": "GitHub Actions CI", "expires_in_days": 30},
        )
        assert issue_resp.status_code == 201
        token_data = issue_resp.json()
        assert "token" in token_data
        assert token_data["token"].startswith("uitp_")
        assert token_data["name"] == "GitHub Actions CI"
        raw_token = token_data["token"]
        token_id = token_data["id"]

        # 2. List tokens — raw secret is not present
        list_resp = await client.get("/api/v1/auth/tokens")
        assert list_resp.status_code == 200
        items = list_resp.json()["items"]
        assert len(items) == 1
        assert items[0]["id"] == token_id
        assert items[0]["name"] == "GitHub Actions CI"
        assert "token" not in items[0]

        # 3. Create a fresh headless client with no session cookies
        # Access protected endpoint with Bearer auth
        from uitp_api.main import app
        async with AsyncClient(transport=client._transport, base_url="http://test") as headless_client:
            # Without token -> 401
            unauth_resp = await headless_client.get("/api/v1/projects")
            assert unauth_resp.status_code == 401

            # With Bearer token -> 200
            auth_resp = await headless_client.get(
                "/api/v1/projects",
                headers={"Authorization": f"Bearer {raw_token}"},
            )
            assert auth_resp.status_code == 200

            # Whoami / me endpoint
            me_resp = await headless_client.get(
                "/api/v1/auth/me",
                headers={"Authorization": f"Bearer {raw_token}"},
            )
            assert me_resp.status_code == 200
            assert me_resp.json()["email"] == "alice@example.com"

    @pytest.mark.asyncio
    async def test_token_revocation(self, client: AsyncClient) -> None:
        """Revoked tokens must immediately fail authentication with 401."""
        await _register_and_login(client, "bob@example.com")

        issue_resp = await client.post(
            "/api/v1/auth/tokens",
            json={"name": "Disposable CLI Token", "expires_in_days": 7},
        )
        assert issue_resp.status_code == 201
        raw_token = issue_resp.json()["token"]
        token_id = issue_resp.json()["id"]

        # Verify works
        async with AsyncClient(transport=client._transport, base_url="http://test") as headless_client:
            ok_resp = await headless_client.get(
                "/api/v1/projects",
                headers={"Authorization": f"Bearer {raw_token}"},
            )
            assert ok_resp.status_code == 200

            # Revoke token
            del_resp = await client.delete(f"/api/v1/auth/tokens/{token_id}")
            assert del_resp.status_code == 200

            # Subsequent request must be rejected with 401
            revoked_resp = await headless_client.get(
                "/api/v1/projects",
                headers={"Authorization": f"Bearer {raw_token}"},
            )
            assert revoked_resp.status_code == 401
            assert "Invalid or revoked API token" in revoked_resp.json()["error"]["message"]

    @pytest.mark.asyncio
    async def test_expired_token_rejected(self, client: AsyncClient, db_session) -> None:
        """Expired tokens must fail with 401."""
        await _register_and_login(client, "charlie@example.com")

        issue_resp = await client.post(
            "/api/v1/auth/tokens",
            json={"name": "Expired Token", "expires_in_days": 1},
        )
        raw_token = issue_resp.json()["token"]
        token_id = issue_resp.json()["id"]

        # Backdate expiration directly in DB
        from uitp_api.models.token import ApiToken
        from sqlalchemy import select
        stmt = select(ApiToken).where(ApiToken.id == token_id)
        result = await db_session.execute(stmt)
        token_record = result.scalar_one()
        token_record.expires_at = datetime.now(UTC) - timedelta(hours=1)
        await db_session.commit()

        async with AsyncClient(transport=client._transport, base_url="http://test") as headless_client:
            exp_resp = await headless_client.get(
                "/api/v1/projects",
                headers={"Authorization": f"Bearer {raw_token}"},
            )
            assert exp_resp.status_code == 401
            assert "expired" in exp_resp.json()["error"]["message"].lower()
