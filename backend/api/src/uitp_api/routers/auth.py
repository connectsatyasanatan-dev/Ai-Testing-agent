"""
uitp_api.routers.auth
======================
POST /api/v1/auth/login   — exchange credentials for a session cookie
POST /api/v1/auth/logout  — clear the session cookie
GET  /api/v1/auth/me      — return the current authenticated user
POST /api/v1/auth/register — create a new account (development / pilot)

Rate-limiting:
Enforced on POST /login via distributed Redis sliding window log (or in-memory fallback),
keying on client IP with a 60-second window. Successful login resets the counter.

NOTE (ADR-009 / P4-00):
API token issuance (e.g. POST /api/v1/auth/tokens) is deferred to Milestone 2,
implemented ahead of worker agent execution (task P4-01).
"""
from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.ext.asyncio import AsyncSession

from uitp_api.database import get_async_session
from uitp_api.exceptions import AuthenticationFailedError, ConflictError
from uitp_api.middleware.auth import SESSION_USER_KEY, get_current_user
from uitp_api.models.user import User
from uitp_api.repositories.user_repo import UserRepository
from uitp_api.services import audit_service, auth_service
from uitp_api.services.rate_limiter import (
    RateLimiter,
    check_login_rate_limit,
    get_rate_limiter,
)
from uitp_contracts.enums import AuditEventType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])


# ── Request / response schemas ────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: str | None
    is_active: bool

    model_config = {"from_attributes": True}


class MessageResponse(BaseModel):
    message: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _client_ip(request: Request) -> str | None:
    """Extract real client IP, honouring X-Forwarded-For if trusted."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/register", response_model=UserResponse, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
) -> UserResponse:
    """Create a new user account.

    In production this endpoint should be disabled or protected by an invite
    system. Available during pilot/development.
    """
    repo = UserRepository(session)

    if await repo.email_exists(body.email):
        raise ConflictError("An account with this email already exists.")

    hashed = auth_service.hash_password(body.password)
    user = User(
        id=str(uuid.uuid4()),
        email=body.email.lower().strip(),
        password_hash=hashed,
        full_name=body.full_name,
        is_active=True,
    )
    session.add(user)

    # Audit event — do NOT include the password or hash.
    await audit_service.record(
        session,
        event_type=AuditEventType.USER_LOGIN,   # closest event; extend taxonomy later
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=_client_ip(request),
        metadata={"email": user.email},
    )

    await session.commit()
    await session.refresh(user)
    logger.info("New user registered: %s", user.id)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    request: Request,
    session: AsyncSession = Depends(get_async_session),
    _: None = Depends(check_login_rate_limit),
    rate_limiter: RateLimiter = Depends(get_rate_limiter),
) -> UserResponse:
    """Authenticate with email + password, creating a session cookie.

    Deliberately vague error message on failure to prevent user enumeration.
    Rate-limited by client IP across distributed instances (or in-memory fallback).
    """
    ip = _client_ip(request)
    repo = UserRepository(session)
    user = await repo.get_by_email(body.email)

    # Always run password verification (even if user is None) to avoid
    # timing-based user enumeration via fast early-return.
    dummy_hash = auth_service.hash_password("dummy-constant-to-prevent-timing-attack")
    candidate_hash = user.password_hash if user else dummy_hash

    password_ok = auth_service.verify_password(body.password, candidate_hash)

    if not password_ok or user is None or not user.is_active:
        await audit_service.record(
            session,
            event_type=AuditEventType.USER_LOGIN_FAILED,
            actor_id=user.id if user else None,
            resource_type="user",
            ip_address=ip,
            metadata={"email": body.email},
        )
        await session.commit()
        raise AuthenticationFailedError("Invalid email or password.")

    # Reset rate-limit counter for this client IP upon successful login so that
    # legitimate users with occasional typos are never locked out.
    if ip:
        await rate_limiter.reset(f"ratelimit:login:{ip}")

    # Optionally rehash if params have been upgraded.
    if auth_service.needs_rehash(user.password_hash):
        user.password_hash = auth_service.hash_password(body.password)  # type: ignore[assignment]
        session.add(user)

    # Write session — only store the user ID, never the hash or profile data.
    request.session[SESSION_USER_KEY] = user.id

    await audit_service.record(
        session,
        event_type=AuditEventType.USER_LOGIN,
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip,
        metadata={"email": user.email},
    )
    await session.commit()
    logger.info("User logged in: %s", user.id)
    return UserResponse.model_validate(user)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> MessageResponse:
    """Clear the session cookie."""
    request.session.clear()
    logger.info("User logged out: %s", current_user.id)
    return MessageResponse(message="Logged out successfully.")


@router.get("/me", response_model=UserResponse)
async def me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """Return the authenticated user's profile."""
    return UserResponse.model_validate(current_user)


# ── API Tokens (ADR-009 / P4-00) ───────────────────────────────────────────────

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from sqlalchemy import select
from uitp_api.exceptions import ResourceNotFoundError
from uitp_api.models.token import ApiToken


class CreateTokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


class CreatedTokenResponse(BaseModel):
    id: str
    name: str
    token: str
    token_prefix: str
    expires_at: datetime | None
    created_at: datetime


class TokenSummaryResponse(BaseModel):
    id: str
    name: str
    token_prefix: str
    expires_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime
    is_revoked: bool

    model_config = {"from_attributes": True}


class TokenListResponse(BaseModel):
    items: list[TokenSummaryResponse]


@router.post("/tokens", response_model=CreatedTokenResponse, status_code=201)
async def create_api_token(
    body: CreateTokenRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> CreatedTokenResponse:
    """Issue a new opaque API token for CI/CLI authentication (P4-00).

    The raw token string is returned exactly once upon creation.
    Only its SHA-256 hash is persisted.
    """
    random_part = secrets.token_urlsafe(32)
    raw_token = f"uitp_{random_part}"
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    token_prefix = f"{raw_token[:10]}..."

    now = datetime.now(UTC)
    expires_at = now + timedelta(days=body.expires_in_days) if body.expires_in_days else None

    token_obj = ApiToken(
        id=str(uuid.uuid4()),
        user_id=current_user.id,
        name=body.name,
        token_hash=token_hash,
        token_prefix=token_prefix,
        expires_at=expires_at,
        is_revoked=False,
    )
    session.add(token_obj)
    await session.commit()
    await session.refresh(token_obj)

    logger.info("API token issued: %s (%s) for user %s", token_obj.id, body.name, current_user.id)
    return CreatedTokenResponse(
        id=token_obj.id,
        name=token_obj.name,
        token=raw_token,
        token_prefix=token_obj.token_prefix,
        expires_at=token_obj.expires_at,
        created_at=token_obj.created_at,
    )


@router.get("/tokens", response_model=TokenListResponse)
async def list_api_tokens(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> TokenListResponse:
    """List active API tokens for the authenticated user."""
    stmt = (
        select(ApiToken)
        .where(ApiToken.user_id == current_user.id, ApiToken.is_revoked.is_(False))
        .order_by(ApiToken.created_at.desc())
    )
    result = await session.execute(stmt)
    tokens = result.scalars().all()
    return TokenListResponse(items=[TokenSummaryResponse.model_validate(t) for t in tokens])


@router.delete("/tokens/{token_id}", response_model=MessageResponse)
async def revoke_api_token(
    token_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
) -> MessageResponse:
    """Revoke an API token immediately."""
    stmt = select(ApiToken).where(ApiToken.id == token_id, ApiToken.user_id == current_user.id)
    result = await session.execute(stmt)
    token = result.scalar_one_or_none()

    if token is None:
        raise ResourceNotFoundError("API token not found.")

    token.is_revoked = True
    session.add(token)
    await session.commit()

    logger.info("API token revoked: %s by user %s", token_id, current_user.id)
    return MessageResponse(message="API token revoked successfully.")
