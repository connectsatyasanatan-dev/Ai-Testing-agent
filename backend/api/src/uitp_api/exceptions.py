"""
uitp_api.exceptions
===================
Typed HTTP exceptions that map directly to the API error codes in
API_CONTRACT.md. Routers raise these; the global handler serialises them.

IMPORTANT: Never include secret values, stack traces, or internal paths
in the `message` field — it is returned verbatim to the client.
"""
from __future__ import annotations

from fastapi import HTTPException
from uitp_contracts.enums import ErrorCode


class UITPException(HTTPException):
    """Base class for all UITP HTTP exceptions.

    Adds a stable `error_code` alongside the HTTP status so clients can
    distinguish machine-readable error types from HTTP status codes.
    """

    def __init__(
        self,
        status_code: int,
        error_code: ErrorCode | str,
        message: str,
        request_id: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.error_code = error_code
        self.request_id = request_id
        super().__init__(
            status_code=status_code,
            detail={
                "error": {
                    "code": str(error_code),
                    "message": message,
                    **({"request_id": request_id} if request_id else {}),
                }
            },
            headers=headers,
        )

    @property
    def code(self) -> ErrorCode | str:
        return self.error_code


class AuthenticationFailedError(UITPException):
    def __init__(self, message: str = "Authentication required.") -> None:
        super().__init__(401, ErrorCode.AUTHENTICATION_FAILED, message)


class PermissionDeniedError(UITPException):
    def __init__(self, message: str = "You do not have permission to perform this action.") -> None:
        super().__init__(403, ErrorCode.PERMISSION_DENIED, message)


class ResourceNotFoundError(UITPException):
    def __init__(self, message: str = "Resource not found.") -> None:
        # Return 404 for missing AND unauthorised-but-existing resources
        # to avoid leaking existence (SECURITY_AND_AI_SAFETY.md §6).
        super().__init__(404, ErrorCode.RESOURCE_NOT_FOUND, message)


class ConflictError(UITPException):
    def __init__(self, message: str = "Conflict.") -> None:
        super().__init__(409, ErrorCode.CONFLICT, message)


class ValidationFailedError(UITPException):
    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(422, ErrorCode.VALIDATION_ERROR, message)
        self.field = field


# Alias for compatibility
ValidationError = ValidationFailedError


class PlanNotApprovedError(UITPException):
    def __init__(self) -> None:
        super().__init__(
            422,
            ErrorCode.PLAN_NOT_APPROVED,
            "The requested plan version is not approved for execution.",
        )


class PolicyBlockedError(UITPException):
    def __init__(self, message: str) -> None:
        super().__init__(422, ErrorCode.POLICY_BLOCKED, message)


class AIProviderNotConfiguredError(UITPException):
    def __init__(self) -> None:
        super().__init__(
            503,
            ErrorCode.AI_PROVIDER_NOT_CONFIGURED,
            "AI plan generation is not available — no provider is configured.",
        )


class RateLimitExceededError(UITPException):
    """Raised when an endpoint rate limit is exceeded (HTTP 429)."""

    def __init__(
        self,
        message: str = "Too many requests. Please try again later.",
        retry_after: int | None = None,
    ) -> None:
        headers = {"Retry-After": str(retry_after)} if retry_after is not None else None
        super().__init__(
            status_code=429,
            error_code=ErrorCode.RATE_LIMIT_EXCEEDED,
            message=message,
            headers=headers,
        )
        self.retry_after = retry_after
