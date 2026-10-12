"""
tests.fixtures.mock_http_api.app
================================
Deterministic mock ASGI application for HTTP adapter tests and local simulation.

Routes provided:
  - GET  /ok               -> 200 with stable JSON payload for assertions
  - POST /create           -> 201 with created resource representation; 400/422 on invalid input
  - GET  /slow             -> 200 with controlled async delay (?delay=0.1s default)
  - GET  /fail             -> 500 with safe JSON error (no stack traces or secrets)
  - GET  /schema-mismatch  -> 200 with JSON intentionally differing from standard schema
  - GET  /redirect         -> 302 redirecting to relative destination (/ok)
  - GET  /private-redirect -> 302 redirecting to private/metadata IP (does NOT contact target)
"""
from __future__ import annotations

import asyncio
from typing import Any
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


class MockApiRecorder:
    """Minimal test-safe recorder for inspecting received requests without globals."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def record(self, method: str, path: str, headers: dict[str, str], body: Any) -> None:
        self.requests.append({
            "method": method,
            "path": path,
            "headers": headers,
            "body": body,
        })

    def reset(self) -> None:
        self.requests.clear()


async def ok_handler(request: Request) -> JSONResponse:
    """GET /ok — Deterministic success response with stable JSON structure."""
    recorder: MockApiRecorder = request.app.state.recorder
    recorder.record("GET", "/ok", dict(request.headers), None)
    return JSONResponse(
        content={
            "status": "ok",
            "message": "Operation completed successfully",
            "data": {
                "id": "item_123",
                "name": "Deterministic Widget",
                "count": 42,
                "active": True,
            },
        },
        status_code=200,
        headers={"Content-Type": "application/json", "X-Mock-Api": "true"},
    )


async def create_handler(request: Request) -> JSONResponse:
    """POST /create — Deterministic creation endpoint.

    Returns 201 on valid JSON, 400 on malformed JSON, 422 if body is empty or not a dict.
    """
    recorder: MockApiRecorder = request.app.state.recorder
    try:
        body = await request.json()
    except Exception:
        recorder.record("POST", "/create", dict(request.headers), "<malformed-json>")
        return JSONResponse(
            content={"error": {"code": "INVALID_JSON", "message": "Malformed JSON payload"}},
            status_code=400,
        )

    if not isinstance(body, dict) or not body:
        recorder.record("POST", "/create", dict(request.headers), body)
        return JSONResponse(
            content={"error": {"code": "VALIDATION_ERROR", "message": "Expected non-empty JSON object"}},
            status_code=422,
        )

    recorder.record("POST", "/create", dict(request.headers), body)
    return JSONResponse(
        content={
            "id": "created_456",
            "status": "created",
            "received": body,
        },
        status_code=201,
        headers={"Location": "/items/created_456"},
    )


async def slow_handler(request: Request) -> JSONResponse:
    """GET /slow — Async latency simulation for client timeout verification.

    Delay can be controlled via ?delay=<seconds> (default 0.1s).
    """
    try:
        delay = float(request.query_params.get("delay", "0.1"))
    except ValueError:
        delay = 0.1

    # Bounded between 0 and 5 seconds to avoid hanging CI suites
    delay = max(0.0, min(5.0, delay))
    if delay > 0:
        await asyncio.sleep(delay)

    recorder: MockApiRecorder = request.app.state.recorder
    recorder.record("GET", "/slow", dict(request.headers), None)
    return JSONResponse(
        content={
            "status": "slow_response",
            "delayed_seconds": delay,
            "message": f"Completed after {delay}s delay",
        },
        status_code=200,
    )


async def fail_handler(request: Request) -> JSONResponse:
    """GET /fail — Simulated server error.

    Returns a safe error message without exposing tracebacks or secrets.
    """
    recorder: MockApiRecorder = request.app.state.recorder
    recorder.record("GET", "/fail", dict(request.headers), None)
    return JSONResponse(
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Simulated upstream server failure",
            }
        },
        status_code=500,
    )


async def schema_mismatch_handler(request: Request) -> JSONResponse:
    """GET /schema-mismatch — Valid JSON that intentionally violates standard schema.

    Returns a stable structure where expected types (e.g., list, number) are strings/mismatches.
    """
    recorder: MockApiRecorder = request.app.state.recorder
    recorder.record("GET", "/schema-mismatch", dict(request.headers), None)
    return JSONResponse(
        content={
            "unexpected_format": True,
            "items": "should_be_a_list_but_is_a_string",
            "count": "should_be_an_int_42",
            "nested": None,
        },
        status_code=200,
    )


async def redirect_handler(request: Request) -> Response:
    """GET /redirect — 302 redirect pointing to a relative allowed route (/ok).

    The fixture does not issue any outbound HTTP request.
    """
    target = request.query_params.get("target", "/ok")
    recorder: MockApiRecorder = request.app.state.recorder
    recorder.record("GET", "/redirect", dict(request.headers), None)
    return Response(
        status_code=302,
        headers={"Location": target},
    )


async def private_redirect_handler(request: Request) -> Response:
    """GET /private-redirect — 302 redirect to a private or metadata IP destination.

    Target defaults to the AWS/cloud metadata address http://169.254.169.254/latest/meta-data/.
    The fixture returns only the HTTP Location header without contacting that destination.
    """
    target = request.query_params.get("target", "http://169.254.169.254/latest/meta-data/")
    recorder: MockApiRecorder = request.app.state.recorder
    recorder.record("GET", "/private-redirect", dict(request.headers), None)
    return Response(
        status_code=302,
        headers={"Location": target},
    )


def create_mock_api_app() -> Starlette:
    """Create a new, isolated instance of the mock HTTP API ASGI application."""
    routes = [
        Route("/ok", ok_handler, methods=["GET"]),
        Route("/create", create_handler, methods=["POST"]),
        Route("/slow", slow_handler, methods=["GET"]),
        Route("/fail", fail_handler, methods=["GET"]),
        Route("/schema-mismatch", schema_mismatch_handler, methods=["GET"]),
        Route("/redirect", redirect_handler, methods=["GET"]),
        Route("/private-redirect", private_redirect_handler, methods=["GET"]),
    ]
    app = Starlette(routes=routes)
    app.state.recorder = MockApiRecorder()
    return app


# Module-level default instance for standard tests
mock_api_app = create_mock_api_app()
