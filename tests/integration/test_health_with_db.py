"""
tests/integration/test_health_with_db.py
=========================================
Integration tests for /health and /ready with a real (test) DB.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.integration
class TestHealthWithDB:
    @pytest.mark.asyncio
    async def test_health_200(self, client: AsyncClient) -> None:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_ready_200_when_db_ok(self, client: AsyncClient) -> None:
        resp = await client.get("/ready")
        assert resp.status_code == 200
        checks = resp.json()["checks"]
        assert checks["database"] == "ok"
