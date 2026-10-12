"""
uitp_adapter_base.exceptions
============================
Typed exceptions for adapter capability negotiation, registration, and execution.
"""
from __future__ import annotations

from uitp_contracts.enums import ErrorCode


class AdapterError(Exception):
    """Base exception for all adapter-related errors."""

    def __init__(self, message: str, code: ErrorCode | str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code or ErrorCode.UNSUPPORTED_ACTION


class UnsupportedActionError(AdapterError):
    """Raised when an adapter or plan step attempts an action that is not supported.

    Matches ErrorCode.UNSUPPORTED_ACTION in uitp_contracts.enums.
    """

    def __init__(
        self,
        action: str,
        adapter_id: str,
        message: str | None = None,
    ) -> None:
        self.action = action
        self.adapter_id = adapter_id
        msg = message or f"Adapter '{adapter_id}' does not support action '{action}'."
        super().__init__(msg, code=ErrorCode.UNSUPPORTED_ACTION)


class AdapterNotFoundError(AdapterError):
    """Raised when an operation requests an adapter identifier that is not registered."""

    def __init__(self, adapter_id: str, message: str | None = None) -> None:
        self.adapter_id = adapter_id
        msg = message or f"Adapter '{adapter_id}' is not registered."
        super().__init__(msg, code=ErrorCode.RESOURCE_NOT_FOUND)


class DuplicateAdapterError(AdapterError):
    """Raised when registering an adapter whose adapter_id is already in the registry."""

    def __init__(self, adapter_id: str, message: str | None = None) -> None:
        self.adapter_id = adapter_id
        msg = message or f"Adapter '{adapter_id}' is already registered."
        super().__init__(msg, code=ErrorCode.CONFLICT)


class InvalidCapabilityError(AdapterError):
    """Raised when an adapter declares malformed, empty, or invalid capabilities."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code=ErrorCode.VALIDATION_ERROR)
