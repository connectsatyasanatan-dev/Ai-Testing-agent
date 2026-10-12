"""
uitp_contracts.environment
==========================
Schemas for environment definitions and responses.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from uitp_contracts.enums import EnvironmentStatus


class EnvironmentSchema(BaseModel):
    """Summary representation of a test environment (API response shape)."""

    model_config = ConfigDict(from_attributes=True, frozen=True)

    id: str
    """UUIDv4 environment identifier."""

    project_id: str
    """UUIDv4 of the owning project."""

    name: str = Field(..., min_length=1, max_length=255)
    base_url: str = Field(..., max_length=2048)
    allowlist: list[str] | None = None
    secret_refs: dict[str, str] | None = None
    description: str | None = None
    status: EnvironmentStatus | str = EnvironmentStatus.ACTIVE

    created_at: datetime
    updated_at: datetime
