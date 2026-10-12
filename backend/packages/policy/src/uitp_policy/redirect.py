"""
uitp_policy.redirect
====================
Secure redirect validation, loop detection, and cross-origin header sanitization.
"""
from __future__ import annotations

import logging
from urllib.parse import urljoin
from typing import Mapping, Sequence

from uitp_policy.exceptions import PolicyBlockedError
from uitp_policy.normalizer import ParsedTarget, normalize_url
from uitp_policy.validator import SSRFValidator

logger = logging.getLogger(__name__)

# Sensitive headers that must be stripped on cross-origin redirect
SENSITIVE_REDIRECT_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "proxy-authorization",
    }
)


class RedirectValidator:
    """Manages redirect re-validation, loop prevention, and origin boundary security."""

    def __init__(
        self,
        validator: SSRFValidator | None = None,
        max_redirects: int = 5,
        allow_cross_origin: bool = True,
    ) -> None:
        self.validator: SSRFValidator = validator or SSRFValidator()
        self.max_redirects = max_redirects
        self.allow_cross_origin = allow_cross_origin

    async def validate_redirect(
        self,
        current_url: str,
        redirect_location: str,
        redirect_history: Sequence[str],
        allowlist: Sequence[str] | None = None,
    ) -> tuple[str, ParsedTarget]:
        """Validate and resolve an HTTP redirect target.

        Guarantees:
          - Relative locations are joined safely against current_url.
          - Maximum redirect depth is strictly enforced (prevents resource exhaustion).
          - Circular redirect loops are detected and rejected.
          - Cross-origin restrictions are checked if configured.
          - Every redirect destination is re-validated through SSRFValidator
            (preventing redirects to private IPs or metadata addresses).

        Returns:
          (resolved_absolute_url: str, target: ParsedTarget)

        Raises:
          PolicyBlockedError: if redirect exceeds depth, loops, or targets prohibited IP/metadata.
        """
        if len(redirect_history) >= self.max_redirects:
            logger.warning("Redirect block: maximum redirect depth of %d exceeded", self.max_redirects)
            raise PolicyBlockedError(
                message="Redirect blocked: maximum redirect depth exceeded.",
                internal_reason="Maximum redirects exceeded",
            )

        # Resolve relative target against current URL
        resolved_url = urljoin(current_url, redirect_location)
        current_target = normalize_url(current_url)
        next_target = normalize_url(resolved_url)

        # Loop detection
        if resolved_url in redirect_history or next_target.url in redirect_history:
            logger.warning("Redirect block: circular redirect loop detected for '%s'", resolved_url)
            raise PolicyBlockedError(
                message="Redirect blocked: circular redirect loop detected.",
                internal_reason="Redirect loop detected",
            )

        # Cross-origin policy check
        is_same_origin = (
            current_target.scheme == next_target.scheme
            and current_target.host == next_target.host
            and current_target.port == next_target.port
        )

        if not self.allow_cross_origin and not is_same_origin:
            raise PolicyBlockedError(
                message="Redirect blocked: cross-origin redirects are not permitted.",
                internal_reason="Cross-origin redirect denied",
            )

        # CRITICAL SSRF GATE: Revalidate the redirect destination
        validated_target, _ = await self.validator.validate_url(resolved_url, allowlist=allowlist)

        return resolved_url, validated_target

    @staticmethod
    def sanitize_redirect_headers(
        headers: Mapping[str, str],
        from_url: str,
        to_url: str,
    ) -> dict[str, str]:
        """Strip sensitive credentials/cookies if redirect crosses origins."""
        t_from = normalize_url(from_url)
        t_to = normalize_url(to_url)

        is_same_origin = (
            t_from.scheme == t_to.scheme
            and t_from.host == t_to.host
            and t_from.port == t_to.port
        )

        if is_same_origin:
            return dict(headers)

        # Strip sensitive headers across origins
        return {
            k: v for k, v in headers.items()
            if k.lower() not in SENSITIVE_REDIRECT_HEADERS
        }
