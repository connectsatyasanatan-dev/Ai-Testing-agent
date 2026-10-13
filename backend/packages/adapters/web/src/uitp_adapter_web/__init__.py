"""
uitp_adapter_web
================
Playwright UI testing adapter for the Universal Autonomous AI Testing Platform (Milestone 2).
"""
from uitp_adapter_web.adapter import PlaywrightAdapter
from uitp_adapter_web.assertions import WebAssertionEngine
from uitp_adapter_web.exceptions import (
    BlockedNavigationError,
    ElementNotFoundError,
    InvalidLocatorError,
    ResourceLimitExceededError,
    WebActionTimeoutError,
    WebAdapterError,
)
from uitp_adapter_web.locators import LocatorResolver, LocatorSpec
from uitp_adapter_web.router import BrowserSecurityRouter

__all__ = [
    "PlaywrightAdapter",
    "WebAssertionEngine",
    "LocatorResolver",
    "LocatorSpec",
    "BrowserSecurityRouter",
    "WebAdapterError",
    "BlockedNavigationError",
    "InvalidLocatorError",
    "ElementNotFoundError",
    "WebActionTimeoutError",
    "ResourceLimitExceededError",
]
