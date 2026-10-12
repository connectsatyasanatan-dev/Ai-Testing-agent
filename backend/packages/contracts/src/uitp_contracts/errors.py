"""
uitp_contracts.errors
=====================
Shared error response schema (API_CONTRACT.md).

All API error responses use this shape. Error codes come from ErrorCode enum.
Stack traces and secret values must never appear in these responses.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from uitp_contracts.enums import ErrorCode


class ErrorDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ErrorCode | str
    """Stable error code. Use ErrorCode enum values for known errors."""

    message: str
    """Human-readable explanation safe to show to users."""

    request_id: str | None = None
    """Correlation ID for log lookup."""

    field: str | None = None
    """Optional: which request field caused a VALIDATION_ERROR."""


class ErrorResponse(BaseModel):
    """Top-level API error response envelope."""

    model_config = ConfigDict(frozen=True)

    error: ErrorDetail
