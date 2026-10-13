"""
uitp_adapter_web.exceptions
===========================
Exceptions raised by the Playwright Web Adapter (Task P2-01).
"""
from __future__ import annotations

from uitp_adapter_base.exceptions import AdapterError


class WebAdapterError(AdapterError):
    """Base exception for all web adapter errors."""


class BlockedNavigationError(WebAdapterError):
    """Navigation was blocked by SSRF policy, target allowlist, or prohibited scheme."""

    def __init__(self, message: str, url: str | None = None) -> None:
        super().__init__(message)
        self.url = url


class InvalidLocatorError(WebAdapterError):
    """Locator syntax is invalid, contains prohibited constructs, or is unsupported."""

    def __init__(self, message: str, selector: str | None = None) -> None:
        super().__init__(message)
        self.selector = selector


class ElementNotFoundError(WebAdapterError):
    """Target element matching locator was not found within timeout."""

    def __init__(self, message: str, selector: str | None = None) -> None:
        super().__init__(message)
        self.selector = selector


class WebActionTimeoutError(WebAdapterError):
    """Action or navigation exceeded bounded timeout."""


class ResourceLimitExceededError(WebAdapterError):
    """Browser resource boundary (pages, downloads, popups) exceeded."""
