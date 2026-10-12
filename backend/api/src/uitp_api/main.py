"""
uitp_api.main
=============
FastAPI application factory with lifespan, CORS, session middleware,
and exception handler wiring.

Architecture rule: no business logic here — only wiring.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from uitp_api.config import get_settings
from uitp_api.database import init_engine
from uitp_api.exceptions import UITPException
from uitp_api.logging_config import configure_logging
from uitp_api.routers import artifacts, auth, environments, health, plans, projects, runs

_settings = get_settings()
configure_logging(level=_settings.log_level, fmt=_settings.log_format.value)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Startup and shutdown lifecycle.

    Initialises the database engine on startup; disposes it on shutdown.
    This is the single place where the engine URL is resolved from config.
    """
    settings = get_settings()
    engine = init_engine(
        settings.database_url.get_secret_value(),
        echo=settings.is_development,
    )
    logger.info("Database engine initialised | env=%s", settings.app_env)
    yield
    await engine.dispose()
    logger.info("Database engine disposed.")


# ── Application factory ───────────────────────────────────────────────────────

def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Universal AI Testing Platform",
        description="Adapter-driven test orchestration with AI assistance.",
        version="0.1.0",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Session middleware (signed cookies, ADR-009) ──────────────────────────
    # Must be added BEFORE CORS so the cookie is available on all requests.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        session_cookie="uitp_session",
        max_age=86400,          # 24 hours
        same_site="lax",        # protects against CSRF on cross-site navigations
        https_only=settings.is_production,  # Secure flag in production
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    # ── Exception handlers ────────────────────────────────────────────────────

    @app.exception_handler(UITPException)
    async def uitp_exception_handler(request: Request, exc: UITPException) -> JSONResponse:
        # UITPException.detail is already the JSON-safe error body.
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.detail,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled exception: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "INTERNAL_ERROR", "message": "An unexpected error occurred."}},
        )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(environments.router)
    app.include_router(plans.project_plans_router)
    app.include_router(plans.plans_router)
    app.include_router(runs.plans_runs_router)
    app.include_router(runs.runs_router)
    app.include_router(runs.project_runs_router)
    app.include_router(artifacts.router)

    logger.info("API ready | env=%s ai=%s", settings.app_env, settings.ai_enabled)
    return app


app = create_app()
