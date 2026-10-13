"""
uitp_policy.transport
=====================
SSRF-safe, DNS-rebinding-resistant HTTP transport for adapter execution (Task P3-07).

Defends against Time-of-Check to Time-of-Use (TOCTOU) DNS rebinding attacks:
  - Resolves and validates all A/AAAA records through SSRFValidator before connection.
  - Pins the outgoing socket connection to the validated safe IP address.
  - Preserves the original Host header and TLS SNI hostname.
  - Re-evaluates every intermediate request and redirect.
  - Rejects connection if destination host or any resolved IP violates policy.
"""
from __future__ import annotations

import logging
from typing import Sequence
from urllib.parse import urlparse, urlunparse

import httpx

from uitp_policy.exceptions import PolicyBlockedError, TargetNotAllowlistedError
from uitp_policy.validator import SSRFValidator

logger = logging.getLogger(__name__)


class RebindingSafeTransport(httpx.AsyncBaseTransport):
    """Async HTTP transport that pins connections to verified, SSRF-safe IP addresses."""

    def __init__(
        self,
        validator: SSRFValidator | None = None,
        allowlist: Sequence[str] | None = None,
        underlying_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.validator: SSRFValidator = validator or SSRFValidator()
        self.allowlist: list[str] | None = list(allowlist) if allowlist is not None else None
        self.underlying_transport: httpx.AsyncBaseTransport = (
            underlying_transport or httpx.AsyncHTTPTransport()
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url_str = str(request.url)

        # 1. Validate target destination and resolve IPs through SSRFValidator
        target, resolved_ips = await self.validator.validate_url(
            url_str,
            allowlist=self.allowlist,
        )

        # If underlying transport is an ASGITransport (in-memory mock app for tests),
        # validation has succeeded, delegate directly without physical socket connection.
        if isinstance(self.underlying_transport, httpx.ASGITransport):
            return await self.underlying_transport.handle_async_request(request)

        # 2. TOCTOU DNS Rebinding Defense: Pin connection to verified IP
        safe_ip = str(resolved_ips[0])
        parsed = urlparse(url_str)

        # Ensure original Host header is preserved
        original_host = target.host_port
        request.headers["host"] = original_host

        # If target is a domain name, rewrite request target host to the verified IP
        if target.host != safe_ip:
            pinned_netloc = f"{safe_ip}:{target.port}" if target.port else safe_ip
            if ":" in safe_ip and not safe_ip.startswith("["):
                pinned_netloc = f"[{safe_ip}]:{target.port}" if target.port else f"[{safe_ip}]"

            pinned_url = urlunparse((
                parsed.scheme,
                pinned_netloc,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            ))
            pinned_request = httpx.Request(
                method=request.method,
                url=pinned_url,
                headers=request.headers,
                stream=request.stream,
                extensions=dict(request.extensions),
            )
            if parsed.scheme.lower() == "https":
                pinned_request.extensions["sni_hostname"] = target.host

            logger.debug(
                "DNS Rebinding defense: Pinned connection for %s -> IP %s",
                target.host,
                safe_ip,
            )
            return await self.underlying_transport.handle_async_request(pinned_request)

        return await self.underlying_transport.handle_async_request(request)

    async def aclose(self) -> None:
        await self.underlying_transport.aclose()
