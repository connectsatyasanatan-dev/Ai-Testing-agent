"""
tests/unit/test_exceptions.py
==============================
Unit tests for typed HTTP exceptions — status codes and error codes.
"""
from __future__ import annotations

import pytest

from uitp_api.exceptions import (
    AIProviderNotConfiguredError,
    AuthenticationFailedError,
    ConflictError,
    PermissionDeniedError,
    PlanNotApprovedError,
    PolicyBlockedError,
    ResourceNotFoundError,
    ValidationFailedError,
)
from uitp_contracts.enums import ErrorCode


@pytest.mark.unit
class TestExceptions:
    def test_auth_failed_is_401(self) -> None:
        exc = AuthenticationFailedError()
        assert exc.status_code == 401
        assert exc.error_code == ErrorCode.AUTHENTICATION_FAILED

    def test_permission_denied_is_403(self) -> None:
        exc = PermissionDeniedError()
        assert exc.status_code == 403
        assert exc.error_code == ErrorCode.PERMISSION_DENIED

    def test_not_found_is_404(self) -> None:
        exc = ResourceNotFoundError()
        assert exc.status_code == 404
        assert exc.error_code == ErrorCode.RESOURCE_NOT_FOUND

    def test_conflict_is_409(self) -> None:
        exc = ConflictError()
        assert exc.status_code == 409

    def test_plan_not_approved_is_422(self) -> None:
        exc = PlanNotApprovedError()
        assert exc.status_code == 422
        assert exc.error_code == ErrorCode.PLAN_NOT_APPROVED

    def test_policy_blocked_is_422(self) -> None:
        exc = PolicyBlockedError("Target not allowlisted.")
        assert exc.status_code == 422
        assert exc.error_code == ErrorCode.POLICY_BLOCKED

    def test_ai_unavailable_is_503(self) -> None:
        exc = AIProviderNotConfiguredError()
        assert exc.status_code == 503
        assert exc.error_code == ErrorCode.AI_PROVIDER_NOT_CONFIGURED

    def test_detail_contains_error_key(self) -> None:
        """detail is a dict with an 'error' key — consistent API shape."""
        exc = AuthenticationFailedError("Custom message.")
        assert isinstance(exc.detail, dict)
        assert "error" in exc.detail
        assert exc.detail["error"]["code"] == "AUTHENTICATION_FAILED"
        assert exc.detail["error"]["message"] == "Custom message."
