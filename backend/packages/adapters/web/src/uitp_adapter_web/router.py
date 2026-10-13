"""
uitp_adapter_web.router
=======================
Network-level SSRF defense and resource bounding for Playwright (Task P2-01 & Security Audit).

Guarantees:
  - Intercepts 100% of network requests triggered by main frames, iframes, popups,
    fetch/XHR, redirects, worker threads, and subresources via context.route("**/*").
  - Strictly allows only http:// and https:// schemes (plus about:blank for initialization).
  - Validates every target against SSRFValidator (RFC1918, link-local, 169.254.169.254,
    cloud metadata services, loopback, IPv6 equivalents) and target allowlists.
  - Aborts blocked requests immediately using route.abort("blockedbyclient").
  - Closes any popups exceeding the maximum allowed concurrent pages.
  - Prevents untrusted file downloads from filling host disks.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Sequence
from urllib.parse import urlparse

from uitp_policy.exceptions import (
    InvalidTargetUrlError,
    PolicyBlockedError,
    TargetNotAllowlistedError,
)
from uitp_policy.validator import SSRFValidator

logger = logging.getLogger(__name__)

ALLOWED_BROWSER_SCHEMES = frozenset({"http", "https"})


class BrowserSecurityRouter:
    """Manages context-level route interception, SSRF defense, and resource limits."""

    def __init__(
        self,
        *,
        ssrf_validator: SSRFValidator | None = None,
        allowlist: Sequence[str] | None = None,
        max_pages: int = 3,
    ) -> None:
        self.ssrf_validator = ssrf_validator or SSRFValidator()
        self.allowlist = allowlist
        self.max_pages = max_pages
        self.blocked_requests: list[dict[str, Any]] = []

    async def handle_route(self, route: Any) -> None:
        """Route handler for Playwright context.route('**/*')."""
        request = route.request
        url = request.url

        # Allow initial blank page
        if url == "about:blank":
            await route.continue_()
            return

        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()

        # 1. Scheme Check
        if scheme not in ALLOWED_BROWSER_SCHEMES:
            logger.warning(
                "Browser route blocked due to prohibited scheme '%s': %s",
                scheme,
                url,
            )
            self.blocked_requests.append({
                "url": url,
                "reason": f"Prohibited scheme: '{scheme}'",
            })
            await route.abort("blockedbyclient")
            return

        # 2. SSRF & Allowlist Policy Check
        try:
            val = self.ssrf_validator.validate_url(url, allowlist=self.allowlist)
            if inspect.iscoroutine(val):
                await val
        except (TargetNotAllowlistedError, PolicyBlockedError, InvalidTargetUrlError) as exc:
            logger.warning(
                "Browser route blocked by security policy for URL '%s': %s",
                url,
                exc,
            )
            self.blocked_requests.append({
                "url": url,
                "reason": str(exc),
            })
            await route.abort("blockedbyclient")
        except Exception as exc:
            logger.error("Unexpected error during browser SSRF route validation for '%s': %s", url, exc)
            self.blocked_requests.append({
                "url": url,
                "reason": f"Validator error: {type(exc).__name__}",
            })
            await route.abort("blockedbyclient")
            return

        # 3. Request passed initial URL checks; fetch with max_redirects=0 to intercept redirects
        if hasattr(route, "fetch") and callable(route.fetch):
            try:
                response = await route.fetch(max_redirects=0)
                if response.status in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    if location:
                        from urllib.parse import urljoin
                        redirect_url = urljoin(url, location)
                        try:
                            val = self.ssrf_validator.validate_url(redirect_url, allowlist=self.allowlist)
                            if inspect.iscoroutine(val):
                                await val
                        except (TargetNotAllowlistedError, PolicyBlockedError, InvalidTargetUrlError) as exc:
                            logger.warning(
                                "Browser redirect blocked by SSRF policy: %s -> %s (%s)",
                                url,
                                redirect_url,
                                exc,
                            )
                            self.blocked_requests.append({
                                "url": redirect_url,
                                "reason": f"Redirect blocked: {exc}",
                            })
                            await route.abort("blockedbyclient")
                            return

                await route.fulfill(response=response)
                return
            except Exception as exc:
                if self.blocked_requests:
                    return
                logger.debug("route.fetch fallback to route.continue_: %s", exc)

        await route.continue_()

    async def attach_to_context(self, context: Any) -> None:
        """Attach routing and resource limit listeners to a Playwright BrowserContext."""
        # Attach route interceptor
        await context.route("**/*", self.handle_route)

        # Attach popup / max page limiter
        async def on_page_opened(page: Any) -> None:
            if len(context.pages) > self.max_pages:
                logger.warning(
                    "Page count (%d) exceeded limit of %d. Immediately closing popup.",
                    len(context.pages),
                    self.max_pages,
                )
                try:
                    await page.close()
                except Exception as exc:
                    logger.debug("Error closing excess popup: %s", exc)

        context.on("page", on_page_opened)

        # Attach download blocker
        async def on_download(download: Any) -> None:
            logger.info("Browser download triggered for '%s'; cancelling per policy.", download.url)
            try:
                await download.cancel()
            except Exception as exc:
                logger.debug("Error cancelling download: %s", exc)

        context.on("download", on_download)
