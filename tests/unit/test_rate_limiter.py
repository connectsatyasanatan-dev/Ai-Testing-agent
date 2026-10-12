"""
tests/unit/test_rate_limiter.py
================================
Unit tests for the rate limiting service and backends.

Coverage:
  - InMemoryRateLimiterBackend sliding window logic, expiry, and reset.
  - RateLimiter service delegation and factory from settings.
  - RateLimitExceededError HTTP status (429), code, and Retry-After header.
  - RedisRateLimiterBackend fallback to in-memory on connection/execution failure.
  - Client IP extraction (X-Forwarded-For vs client.host).
  - Key isolation between different clients.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import Headers

from uitp_api.config import Settings
from uitp_api.exceptions import RateLimitExceededError
from uitp_api.services.rate_limiter import (
    InMemoryRateLimiterBackend,
    RateLimiter,
    RedisRateLimiterBackend,
    get_client_ip,
)
from uitp_contracts.enums import ErrorCode


@pytest.mark.unit
class TestInMemoryRateLimiterBackend:
    @pytest.mark.asyncio
    async def test_allows_up_to_limit(self) -> None:
        backend = InMemoryRateLimiterBackend()
        key = "ratelimit:login:127.0.0.1"

        for _ in range(5):
            allowed, retry_after = await backend.check_and_increment(key, limit=5, window_seconds=60)
            assert allowed is True
            assert retry_after == 0

        # 6th request should be rejected
        allowed, retry_after = await backend.check_and_increment(key, limit=5, window_seconds=60)
        assert allowed is False
        assert retry_after >= 1

    @pytest.mark.asyncio
    async def test_reset_clears_counter(self) -> None:
        backend = InMemoryRateLimiterBackend()
        key = "ratelimit:login:127.0.0.1"

        # Max out limit
        for _ in range(3):
            await backend.check_and_increment(key, limit=3, window_seconds=60)

        allowed, _ = await backend.check_and_increment(key, limit=3, window_seconds=60)
        assert allowed is False

        # Reset
        await backend.reset(key)

        # Should be allowed again
        allowed, retry_after = await backend.check_and_increment(key, limit=3, window_seconds=60)
        assert allowed is True
        assert retry_after == 0

    @pytest.mark.asyncio
    async def test_sliding_window_clears_old_entries(self) -> None:
        backend = InMemoryRateLimiterBackend()
        key = "ratelimit:login:127.0.0.1"

        # Insert 2 timestamps in the past outside window
        past_time = time.time() - 70
        backend._entries[key] = [past_time, past_time + 1]

        # Limit of 2 with window of 60 seconds should purge the past ones and allow new request
        allowed, retry_after = await backend.check_and_increment(key, limit=2, window_seconds=60)
        assert allowed is True
        assert retry_after == 0
        # Only 1 entry remains in the list now
        assert len(backend._entries[key]) == 1

    @pytest.mark.asyncio
    async def test_key_isolation(self) -> None:
        backend = InMemoryRateLimiterBackend()
        key1 = "ratelimit:login:10.0.0.1"
        key2 = "ratelimit:login:10.0.0.2"

        for _ in range(2):
            await backend.check_and_increment(key1, limit=2, window_seconds=60)

        # key1 is now blocked
        allowed1, _ = await backend.check_and_increment(key1, limit=2, window_seconds=60)
        assert allowed1 is False

        # key2 is still allowed
        allowed2, _ = await backend.check_and_increment(key2, limit=2, window_seconds=60)
        assert allowed2 is True


@pytest.mark.unit
class TestRateLimitExceededError:
    def test_error_attributes_and_headers(self) -> None:
        err = RateLimitExceededError(message="Too many login attempts.", retry_after=42)
        assert err.status_code == 429
        assert err.code == ErrorCode.RATE_LIMIT_EXCEEDED
        assert err.headers == {"Retry-After": "42"}


@pytest.mark.unit
class TestRateLimiterService:
    def test_from_settings_selects_redis_when_url_provided(self) -> None:
        settings = Settings(
            secret_key="a" * 32,
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://localhost:6379/0",
        )
        limiter = RateLimiter.from_settings(settings)
        assert isinstance(limiter.backend, RedisRateLimiterBackend)

    def test_from_settings_selects_in_memory_when_no_redis_url(self) -> None:
        settings = Settings(
            secret_key="a" * 32,
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url=None,
        )
        limiter = RateLimiter.from_settings(settings)
        assert isinstance(limiter.backend, InMemoryRateLimiterBackend)


@pytest.mark.unit
class TestRedisBackendFallback:
    @pytest.mark.asyncio
    async def test_fallback_to_in_memory_on_redis_error(self) -> None:
        backend = RedisRateLimiterBackend("redis://nonexistent:6379/0")

        # Mock _get_client to raise an exception simulating connection failure
        with patch.object(backend, "_get_client", side_effect=ConnectionError("Redis connection refused")):
            allowed, retry_after = await backend.check_and_increment(
                "ratelimit:login:127.0.0.1", limit=3, window_seconds=60
            )
            # Degraded fail-open fallback should execute via in-memory backend
            assert allowed is True
            assert retry_after == 0


@pytest.mark.unit
class TestClientIpExtraction:
    def test_prefers_x_forwarded_for(self) -> None:
        req = MagicMock()
        req.headers = {"x-forwarded-for": "203.0.113.195, 70.41.3.18"}
        assert get_client_ip(req) == "203.0.113.195"

    def test_falls_back_to_client_host(self) -> None:
        req = MagicMock()
        req.headers = {}
        req.client.host = "192.168.1.50"
        assert get_client_ip(req) == "192.168.1.50"

    def test_default_when_no_client_info(self) -> None:
        req = MagicMock()
        req.headers = {}
        req.client = None
        assert get_client_ip(req) == "127.0.0.1"
