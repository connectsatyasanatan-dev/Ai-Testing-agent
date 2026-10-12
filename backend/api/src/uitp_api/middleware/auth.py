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

logger = logging.getLogger(__name__)

SESSION_USER_KEY = "user_id"


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> User:
    """FastAPI dependency — resolve the session cookie to a User.

    Raises AuthenticationFailedError (401) if the cookie is absent,
    the session has expired, or the user no longer exists / is inactive.
    """
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

