"""
uitp_api.services.rate_limiter
==============================
Distributed-ready rate limiting service for FastAPI endpoints.

Architecture & Policies:
  - Counting window: 60 seconds (sliding window).
  - Production backend: Redis (Redis 7 sorted set sliding window log via Lua script).
  - Single-process / Testing backend: Thread-safe in-memory sliding window log.
  - Fail-safe policy: When Redis is configured but unavailable, the limiter logs
    an error and degrades to an in-memory sliding window (fail-open degraded mode)
    so legitimate users are not locked out during an infrastructure glitch.
  - Keying strategy: Client IP (honouring X-Forwarded-For if present).
  - Secret safety: Keys contain ONLY the client IP, never passwords, tokens, or PII.
  - Authentication fairness: Successful authentication resets the client IP's
    counter so legitimate users are not penalized for mistyped credentials.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from typing import Any, Protocol, runtime_checkable

from fastapi import Depends, Request

from uitp_api.config import Settings, get_settings
from uitp_api.exceptions import RateLimitExceededError

logger = logging.getLogger(__name__)


# ── Client IP Helper ───────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    """Extract client IP, honouring X-Forwarded-For if available."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"


# ── Backend Protocol ──────────────────────────────────────────────────────────

@runtime_checkable
class RateLimiterBackend(Protocol):
    async def check_and_increment(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """Check if action is allowed.

        Returns (allowed: bool, retry_after_seconds: int).
        """
        ...

    async def reset(self, key: str) -> None:
        """Clear counter for key (e.g. after successful login)."""
        ...

    async def close(self) -> None:
        """Release any open resources."""
        ...


# ── In-Memory Backend (Testing & Single-Process Dev) ──────────────────────────

class InMemoryRateLimiterBackend:
    """Thread-safe in-memory sliding window rate limiter."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._entries: dict[str, list[float]] = {}

    async def check_and_increment(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        now = time.time()
        clear_before = now - window_seconds

        async with self._lock:
            timestamps = [ts for ts in self._entries.get(key, []) if ts > clear_before]
            if len(timestamps) >= limit:
                oldest = timestamps[0]
                retry_after = max(1, math.ceil(oldest + window_seconds - now))
                self._entries[key] = timestamps
                return False, retry_after

            timestamps.append(now)
            self._entries[key] = timestamps
            return True, 0

    async def reset(self, key: str) -> None:
        async with self._lock:
            self._entries.pop(key, None)

    async def close(self) -> None:
        async with self._lock:
            self._entries.clear()


# ── Redis Backend (Multi-Process / Production) ────────────────────────────────

class RedisRateLimiterBackend:
    """Distributed Redis-backed sliding window rate limiter.

    Uses an atomic Lua script to clean expired entries, evaluate the count,
    record the attempt, and set the key TTL in a single roundtrip.
    """

    LUA_SLIDING_WINDOW = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local clear_before = now - window

    redis.call('ZREMRANGEBYSCORE', key, 0, clear_before)
    local current_count = redis.call('ZCARD', key)

    if current_count >= limit then
        local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
        local retry_after = 1
        if oldest and oldest[2] then
            retry_after = math.max(1, math.ceil(tonumber(oldest[2]) + window - now))
        end
        return {0, retry_after}
    else
        local member = tostring(now) .. '-' .. tostring(math.random(100000, 999999))
        redis.call('ZADD', key, now, member)
        redis.call('EXPIRE', key, math.ceil(window))
        return {1, 0}
    end
    """

    def __init__(self, redis_url: str) -> None:
        self.redis_url = redis_url
        self._client: Any = None
        self._fallback = InMemoryRateLimiterBackend()

    async def _get_client(self) -> Any:
        if self._client is None:
            try:
                import redis.asyncio as aioredis  # type: ignore[import-untyped]
                self._client = aioredis.from_url(
                    self.redis_url,
                    encoding="utf-8",
                    decode_responses=True,
                )
            except ImportError:
                logger.warning(
                    "The 'redis' package is not installed. Falling back to in-memory rate limiting."
                )
                raise
        return self._client

    async def check_and_increment(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        try:
            client = await self._get_client()
            now = time.time()
            res = await client.eval(
                self.LUA_SLIDING_WINDOW,
                1,
                key,
                str(now),
                str(window_seconds),
                str(limit),
            )
            allowed = bool(res[0] == 1)
            retry_after = int(res[1])
            return allowed, retry_after
        except Exception as exc:
            logger.error(
                "Redis rate limiter error: %s. Falling back to in-memory limiter.",
                exc,
            )
            return await self._fallback.check_and_increment(key, limit, window_seconds)

    async def reset(self, key: str) -> None:
        try:
            client = await self._get_client()
            await client.delete(key)
        except Exception as exc:
            logger.error("Redis rate limiter reset error: %s.", exc)
        await self._fallback.reset(key)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:
                logger.debug("Error closing Redis client: %s", exc)
            self._client = None
        await self._fallback.close()


# ── High-Level RateLimiter Service ────────────────────────────────────────────

class RateLimiter:
    """Manages backend selection and rate limit checks."""

    def __init__(self, backend: RateLimiterBackend) -> None:
        self.backend = backend

    @classmethod
    def from_settings(cls, settings: Settings) -> "RateLimiter":
        if settings.redis_url:
            logger.info("Using Redis rate limiter backend: %s", settings.redis_url)
            return cls(RedisRateLimiterBackend(settings.redis_url))
        logger.info("No redis_url configured. Using in-memory rate limiter backend.")
        return cls(InMemoryRateLimiterBackend())

    async def check_and_increment(
        self, key: str, limit: int, window_seconds: int = 60
    ) -> tuple[bool, int]:
        return await self.backend.check_and_increment(key, limit, window_seconds)

    async def reset(self, key: str) -> None:
        await self.backend.reset(key)

    async def close(self) -> None:
        await self.backend.close()


# ── Global Singleton & FastAPI Dependencies ───────────────────────────────────

_limiter_instance: RateLimiter | None = None


def get_rate_limiter() -> RateLimiter:
    global _limiter_instance
    if _limiter_instance is None:
        settings = get_settings()
        _limiter_instance = RateLimiter.from_settings(settings)
    return _limiter_instance


def set_rate_limiter(limiter: RateLimiter | None) -> None:
    """Override singleton for testing."""
    global _limiter_instance
    _limiter_instance = limiter


async def check_login_rate_limit(
    request: Request,
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI route dependency to rate-limit login attempts by client IP."""
    ip = get_client_ip(request)
    key = f"ratelimit:login:{ip}"
    allowed, retry_after = await rate_limiter.check_and_increment(
        key=key,
        limit=settings.rate_limit_login,
        window_seconds=60,
    )
    if not allowed:
        logger.warning(
            "Login rate limit exceeded for client IP %s (limit=%s/60s, retry_after=%ss)",
            ip,
            settings.rate_limit_login,
            retry_after,
        )
        raise RateLimitExceededError(
            message=f"Too many login attempts. Please try again in {retry_after} seconds.",
            retry_after=retry_after,
        )


async def check_ai_generate_rate_limit(
    request: Request,
    project_id: str,
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI route dependency to rate-limit AI plan generation attempts by client IP and project."""
    ip = get_client_ip(request)
    key = f"ratelimit:ai_generate:{project_id}:{ip}"
    allowed, retry_after = await rate_limiter.check_and_increment(
        key=key,
        limit=settings.rate_limit_ai_generate,
        window_seconds=60,
    )
    if not allowed:
        logger.warning(
            "AI generation rate limit exceeded for client IP %s on project %s (limit=%s/60s, retry_after=%ss)",
            ip,
            project_id,
            settings.rate_limit_ai_generate,
            retry_after,
        )
        raise RateLimitExceededError(
            message=f"Too many AI generation requests. Please try again in {retry_after} seconds.",
            retry_after=retry_after,
        )

