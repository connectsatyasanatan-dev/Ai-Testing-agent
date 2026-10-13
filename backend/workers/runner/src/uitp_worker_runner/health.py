"""
uitp_worker_runner.health
=========================
Safe health and readiness diagnostics for execution workers (Task P3-07).

Inspects critical dependencies without exposing sensitive credentials,
database passwords, Redis URLs, or secret keys:
  - Database connectivity (via SELECT 1).
  - Redis connection (via PING).
  - Artifact storage filesystem writability.
  - Adapter registry readiness.
  - Execution policy bounds sanity.
"""
from __future__ import annotations

import logging
import os
import platform
import tempfile
from typing import Any
from pathlib import Path

from uitp_policy.execution import ExecutionLimits, ExecutionPolicy

logger = logging.getLogger(__name__)


async def check_worker_readiness(
    *,
    session: Any = None,
    redis_url: str | None = None,
    storage_path: str | Path | None = None,
) -> dict[str, Any]:
    """Execute non-sensitive readiness checks for worker runtime dependencies.

    Returns safe status mapping without leaking database DSNs, passwords, or tokens.
    """
    checks: dict[str, str] = {}
    is_healthy = True

    # 1. Database check
    if session is not None:
        try:
            from sqlalchemy import text
            await session.execute(text("SELECT 1"))
            checks["database"] = "healthy"
        except Exception as exc:
            logger.warning("Worker health check: database unreachable: %s", exc)
            checks["database"] = "unhealthy"
            is_healthy = False
    else:
        checks["database"] = "not_provided"

    # 2. Redis check
    if redis_url:
        try:
            import redis
            client = redis.Redis.from_url(redis_url, socket_connect_timeout=2.0)
            if client.ping():
                checks["redis"] = "healthy"
            else:
                checks["redis"] = "unhealthy"
                is_healthy = False
            client.close()
        except Exception as exc:
            logger.warning("Worker health check: redis unreachable: %s", exc)
            checks["redis"] = "unhealthy"
            is_healthy = False
    else:
        checks["redis"] = "not_configured"

    # 3. Artifact storage writability check
    target_path = Path(storage_path) if storage_path else Path(tempfile.gettempdir())
    try:
        target_path.mkdir(parents=True, exist_ok=True)
        test_probe = target_path / f".health_check_{os.getpid()}"
        test_probe.write_text("probe", encoding="utf-8")
        test_probe.unlink(missing_ok=True)
        checks["storage"] = "healthy"
    except Exception as exc:
        logger.warning("Worker health check: storage not writable: %s", exc)
        checks["storage"] = "unhealthy"
        is_healthy = False

    # 4. Adapter readiness check
    try:
        from uitp_adapter_http_api import HttpApiAdapter
        adapter = HttpApiAdapter()
        adapter_ready = await adapter.health_check()
        checks["adapter_http_api"] = "healthy" if adapter_ready else "unhealthy"
        if not adapter_ready:
            is_healthy = False
    except Exception as exc:
        logger.warning("Worker health check: http adapter error: %s", exc)
        checks["adapter_http_api"] = "unhealthy"
        is_healthy = False

    try:
        from uitp_adapter_web import PlaywrightAdapter
        web_adapter = PlaywrightAdapter()
        web_ready = await web_adapter.health_check()
        checks["adapter_web"] = "healthy" if web_ready else "unhealthy"
    except Exception as exc:
        logger.info("Worker health check: web adapter not available: %s", exc)
        checks["adapter_web"] = "not_available"

    # 5. Execution policy sanity
    try:
        limits = ExecutionLimits()
        policy = ExecutionPolicy(limits=limits)
        checks["execution_policy"] = "healthy"
    except Exception as exc:
        logger.warning("Worker health check: policy invalid: %s", exc)
        checks["execution_policy"] = "unhealthy"
        is_healthy = False

    return {
        "status": "healthy" if is_healthy else "degraded",
        "worker": {
            "os": platform.system(),
            "python_version": platform.python_version(),
            "pid": os.getpid(),
        },
        "checks": checks,
    }
