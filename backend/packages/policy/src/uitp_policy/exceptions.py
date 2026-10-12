"""
uitp_policy.exceptions
======================
Typed exceptions for SSRF target validation and security policy enforcement.
"""
from __future__ import annotations

from uitp_contracts.enums import ErrorCode


class PolicyError(Exception):
    """Base exception for policy enforcement errors."""

    def __init__(self, message: str, code: ErrorCode | str = ErrorCode.POLICY_BLOCKED) -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class PolicyBlockedError(PolicyError):
    """Raised when a request is blocked by SSRF, prohibited IP, or security policy.

    The error message is user-safe and does not leak internal IP addresses or infrastructure.
    """

    def __init__(
        self,
        message: str = "Request blocked by target security policy.",
        internal_reason: str | None = None,
    ) -> None:
        super().__init__(message, code=ErrorCode.POLICY_BLOCKED)
        self.internal_reason = internal_reason or message


class TargetNotAllowlistedError(PolicyError):
    """Raised when a destination host/port is not on the environment's allowlist."""

    def __init__(
        self,
        target: str,
        message: str | None = None,
    ) -> None:
        msg = message or f"Target '{target}' is not on the permitted environment allowlist."
        super().__init__(msg, code=ErrorCode.TARGET_NOT_ALLOWLISTED)
        self.target = target


class InvalidTargetUrlError(PolicyError):
    """Raised when a target URL fails syntax, scheme, or port validation."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code=ErrorCode.VALIDATION_ERROR)
