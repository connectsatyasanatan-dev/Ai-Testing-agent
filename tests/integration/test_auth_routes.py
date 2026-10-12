"""
tests/integration/test_auth_routes.py
======================================
Integration tests for authentication endpoints.

Coverage:
  - Register a new account.
  - Login with correct and incorrect credentials.
  - GET /me returns the current user.
  - Logout clears the session.
  - Cross-user isolation: unauthenticated requests are denied.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
class TestRegister:
    @pytest.mark.asyncio
    async def test_register_returns_201(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "alice@example.com", "password": "correct-horse-battery"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["email"] == "alice@example.com"
        assert "id" in body
        assert "password" not in body
        assert "password_hash" not in body

    @pytest.mark.asyncio
    async def test_duplicate_email_returns_409(self, client: AsyncClient) -> None:
        payload = {"email": "bob@example.com", "password": "correct-horse-battery"}
        await client.post("/api/v1/auth/register", json=payload)
        resp = await client.post("/api/v1/auth/register", json=payload)
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "CONFLICT"

    @pytest.mark.asyncio
    async def test_short_password_returns_422(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/register",
            json={"email": "charlie@example.com", "password": "short"},
        )
        assert resp.status_code == 422


@pytest.mark.integration
class TestLogin:
    @pytest.mark.asyncio
    async def test_login_with_correct_credentials(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "dave@example.com", "password": "correct-horse-battery"},
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "dave@example.com", "password": "correct-horse-battery"},
        )
        assert resp.status_code == 200
        assert resp.json()["email"] == "dave@example.com"

    @pytest.mark.asyncio
    async def test_login_with_wrong_password_returns_401(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "eve@example.com", "password": "correct-horse-battery"},
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "eve@example.com", "password": "wrong-password-123"},
        )
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] == "AUTHENTICATION_FAILED"

    @pytest.mark.asyncio
    async def test_login_unknown_email_returns_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "nobody@example.com", "password": "correct-horse-battery"},
        )
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_login_response_never_contains_password_hash(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "frank@example.com", "password": "correct-horse-battery"},
        )
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": "frank@example.com", "password": "correct-horse-battery"},
        )
        body_text = resp.text
        assert "password" not in body_text
        assert "$argon2" not in body_text


@pytest.mark.integration
class TestMe:
    @pytest.mark.asyncio
    async def test_me_returns_user_when_logged_in(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "grace@example.com", "password": "correct-horse-battery"},
        )
        await client.post(
            "/api/v1/auth/login",
            json={"email": "grace@example.com", "password": "correct-horse-battery"},
        )
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 200
        assert resp.json()["email"] == "grace@example.com"

    @pytest.mark.asyncio
    async def test_me_returns_401_when_not_logged_in(self, client: AsyncClient) -> None:
        resp = await client.get("/api/v1/auth/me")
        assert resp.status_code == 401


@pytest.mark.integration
class TestLogout:
    @pytest.mark.asyncio
    async def test_logout_then_me_returns_401(self, client: AsyncClient) -> None:
        await client.post(
            "/api/v1/auth/register",
            json={"email": "henry@example.com", "password": "correct-horse-battery"},
        )
        await client.post(
            "/api/v1/auth/login",
            json={"email": "henry@example.com", "password": "correct-horse-battery"},
        )
        logout_resp = await client.post("/api/v1/auth/logout")
        assert logout_resp.status_code == 200

        me_resp = await client.get("/api/v1/auth/me")
        assert me_resp.status_code == 401


@pytest.mark.integration
class TestLoginRateLimiting:
    @pytest.fixture(autouse=True)
    def fresh_rate_limiter(self) -> None:
        from uitp_api.services.rate_limiter import InMemoryRateLimiterBackend, RateLimiter, set_rate_limiter
        limiter = RateLimiter(InMemoryRateLimiterBackend())
        set_rate_limiter(limiter)
        yield
        set_rate_limiter(None)

    @pytest.mark.asyncio
    async def test_exceeding_login_limit_returns_429(self, client: AsyncClient) -> None:
        headers = {"x-forwarded-for": "192.0.2.1"}
        payload = {"email": "ratelimit_target@example.com", "password": "wrong-password-xyz"}

        # Limit is 10 requests per 60 seconds
        for _ in range(10):
            resp = await client.post("/api/v1/auth/login", json=payload, headers=headers)
            assert resp.status_code == 401

        # 11th request must receive 429 Too Many Requests
        resp = await client.post("/api/v1/auth/login", json=payload, headers=headers)
        assert resp.status_code == 429
        assert resp.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
        assert "Retry-After" in resp.headers
        assert int(resp.headers["Retry-After"]) > 0

    @pytest.mark.asyncio
    async def test_successful_login_resets_rate_limit(self, client: AsyncClient) -> None:
        headers = {"x-forwarded-for": "192.0.2.2"}
        user_payload = {"email": "reset_user@example.com", "password": "correct-horse-battery"}
        await client.post("/api/v1/auth/register", json=user_payload)

        # 5 failed attempts
        for _ in range(5):
            bad_resp = await client.post(
                "/api/v1/auth/login",
                json={"email": "reset_user@example.com", "password": "wrong-password-123"},
                headers=headers,
            )
            assert bad_resp.status_code == 401

        # Successful login resets the counter for this client IP
        good_resp = await client.post("/api/v1/auth/login", json=user_payload, headers=headers)
        assert good_resp.status_code == 200

        # After reset, can perform up to 10 more attempts without 429
        for _ in range(10):
            res = await client.post(
                "/api/v1/auth/login",
                json={"email": "reset_user@example.com", "password": "wrong-password-123"},
                headers=headers,
            )
            assert res.status_code == 401

        # Only on the 11th does it block again
        blocked = await client.post(
            "/api/v1/auth/login",
            json={"email": "reset_user@example.com", "password": "wrong-password-123"},
            headers=headers,
        )
        assert blocked.status_code == 429

    @pytest.mark.asyncio
    async def test_different_client_ip_not_blocked(self, client: AsyncClient) -> None:
        ip_a_headers = {"x-forwarded-for": "192.0.2.10"}
        ip_b_headers = {"x-forwarded-for": "192.0.2.20"}

        # Max out IP A
        for _ in range(10):
            await client.post(
                "/api/v1/auth/login",
                json={"email": "target@example.com", "password": "wrong-password"},
                headers=ip_a_headers,
            )
        resp_a = await client.post(
            "/api/v1/auth/login",
            json={"email": "target@example.com", "password": "wrong-password"},
            headers=ip_a_headers,
        )
        assert resp_a.status_code == 429

        # IP B should NOT be blocked
        resp_b = await client.post(
            "/api/v1/auth/login",
            json={"email": "target@example.com", "password": "wrong-password"},
            headers=ip_b_headers,
        )
        assert resp_b.status_code == 401  # Not 429

