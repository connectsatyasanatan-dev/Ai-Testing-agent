"""
uitp_api.routers.health
=======================
GET /health — liveness probe  (no auth, no DB)
GET /ready  — readiness probe (checks DB connectivity)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["infrastructure"])

APP_VERSION = "0.1.0"


class HealthResponse(BaseModel):
    status: str
    version: str


class ReadinessResponse(BaseModel):
    status: str
    version: str
    checks: dict[str, str]


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
async def health() -> HealthResponse:
    """Returns 200 when the process is running. No auth, no DB required."""
    return HealthResponse(status="ok", version=APP_VERSION)


@router.get(
    "/ready",
    responses={200: {"model": ReadinessResponse}, 503: {"description": "Not ready"}},
    summary="Readiness check",
)
async def ready(
    session: AsyncSession = Depends(get_async_session),
) -> JSONResponse:
    """Returns 200 when the API and its dependencies are reachable.

    Returns 503 if the database is unreachable.
    """
    checks: dict[str, str] = {}

    # Database connectivity ping.
    try:
        await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        logger.warning("Database readiness check failed: %s", exc)
        checks["database"] = "error"

    # Queue check deferred to P3-01.
    checks["queue"] = "not_required"

    all_ok = all(v in ("ok", "not_required") for v in checks.values())
    status_code = 200 if all_ok else 503

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ok" if all_ok else "degraded",
            "version": APP_VERSION,
            "checks": checks,
        },
    )
