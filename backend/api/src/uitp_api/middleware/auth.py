"""
uitp_api.middleware.auth
========================
FastAPI dependencies for extracting and validating the current user,
and RBAC helpers for project-level role checks.

Session strategy (ADR-009):
  - Browser clients use signed, HttpOnly, SameSite=Lax session cookies
    managed by Starlette's SessionMiddleware.
  - The session stores only `{"user_id": "<uuid>"}` — never the full User
    object, never the password hash.
"""
from __future__ import annotations

import logging

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.exceptions import AuthenticationFailedError, PermissionDeniedError
from uitp_api.models.user import User
from uitp_api.repositories.project_repo import ProjectRepository
from uitp_api.repositories.user_repo import UserRepository

import hashlib
from datetime import UTC, datetime
from sqlalchemy import select
from uitp_api.models.token import ApiToken

logger = logging.getLogger(__name__)

SESSION_USER_KEY = "user_id"


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """FastAPI dependency — resolve credentials to a User.

    Supports:
      1. Programmatic clients (CI/CLI): `Authorization: Bearer <token>` (ADR-009, P4-00).
      2. Browser clients: signed session cookie via SessionMiddleware (ADR-009, P0-05).

    Raises AuthenticationFailedError (401) if neither is provided, expired, or invalid.
    """
    # 1. Check for Bearer token
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.startswith("Bearer "):
        raw_token = auth_header[len("Bearer ") :].strip()
        if not raw_token:
            raise AuthenticationFailedError("API token required in authorization header.")

        token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        stmt = (
            select(ApiToken)
            .where(ApiToken.token_hash == token_hash, ApiToken.is_revoked.is_(False))
        )
        result = await session.execute(stmt)
        token = result.scalar_one_or_none()

        if token is None:
            raise AuthenticationFailedError("Invalid or revoked API token.")

        now = datetime.now(UTC)
        if token.expires_at is not None:
            exp = token.expires_at if token.expires_at.tzinfo else token.expires_at.replace(tzinfo=UTC)
            if exp <= now:
                raise AuthenticationFailedError("API token has expired.")

        # Update last used timestamp
        token.last_used_at = now
        session.add(token)

        user = token.user
        if user is None or not user.is_active:
            raise AuthenticationFailedError("User is no longer active.")

        return user

    # 2. Check for session cookie
    user_id: str | None = request.session.get(SESSION_USER_KEY)
    if not user_id:
        raise AuthenticationFailedError("Authentication required.")

    repo = UserRepository(session)
    user = await repo.get_by_id(user_id)
    if user is None or not user.is_active:
        # Invalidate stale session.
        request.session.clear()
        raise AuthenticationFailedError("Session is no longer valid.")

    return user


async def get_current_active_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_superuser:
        raise PermissionDeniedError("Superuser access required.")
    return current_user


def require_project_role(*roles: str):  # type: ignore[return]
    """Return a FastAPI dependency that checks the user has one of `roles`.

    Cross-tenant isolation:
      - Non-members receive ResourceNotFoundError (404) to avoid leaking
        project existence.
      - Members with insufficient role receive PermissionDeniedError (403).
    """

    async def _check(
        project_id: str,
        current_user: User = Depends(get_current_user),
        session: AsyncSession = Depends(get_async_session),
    ) -> None:
        repo = ProjectRepository(session)
        membership = await repo.get_membership(project_id, current_user.id)
        if membership is None:
            # Return 404 to avoid leaking project existence to non-members.
            from uitp_api.exceptions import ResourceNotFoundError

            raise ResourceNotFoundError("Project not found.")
        if membership.role not in roles:
            from uitp_api.exceptions import PermissionDeniedError

            raise PermissionDeniedError(
                f"Insufficient project role: requires {', '.join(roles)}."
            )

    return _check

