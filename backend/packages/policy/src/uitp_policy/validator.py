"""
uitp_policy.validator
=====================
High-level SSRF target validator combining URL parsing, allowlist checks,
and DNS resolution verification.
"""
from __future__ import annotations

import logging
from ipaddress import IPv4Address, IPv6Address
from typing import Sequence

from uitp_policy.allowlist import assert_target_allowlisted
from uitp_policy.dns import DNSResolver, SystemDNSResolver, validate_dns_records
from uitp_policy.exceptions import PolicyBlockedError
from uitp_policy.ip import is_hostname_prohibited, is_ip_prohibited, parse_ip
from uitp_policy.normalizer import ParsedTarget, normalize_url

logger = logging.getLogger(__name__)


class SSRFValidator:
    """Validates target URLs against scheme, credential, allowlist, IP, and DNS policies."""

    def __init__(
        self,
        resolver: DNSResolver | None = None,
        *,
        allow_loopback: bool = False,
    ) -> None:
        self.resolver: DNSResolver = resolver or SystemDNSResolver()
        self.allow_loopback = allow_loopback

    async def validate_url(
        self,
        url: str,
        allowlist: Sequence[str] | None = None,
    ) -> tuple[ParsedTarget, list[IPv4Address | IPv6Address]]:
        """Validate candidate target URL.

        Steps:
          1. Safe URL parsing, normalization, scheme validation, and credential rejection.
          2. Evaluation of known prohibited hostnames (e.g. metadata, localhost).
          3. Environment allowlist pattern matching.
          4. IP literal policy evaluation (if host is an IP).
          5. Full DNS resolution and validation of all returned A/AAAA records.

        Returns:
          (target: ParsedTarget, resolved_ips: list[IPv4Address | IPv6Address])

        Raises:
          InvalidTargetUrlError: if URL syntax or scheme is invalid.
          TargetNotAllowlistedError: if host/port is not on allowlist.
          PolicyBlockedError: if target resolves to or targets a prohibited IP/metadata address.
        """
        # 1. Parse and normalize
        target = normalize_url(url)

        loopback_ok = self.allow_loopback or bool(
            allowlist and any(str(item).startswith("127.0.0.1") or str(item).startswith("localhost") for item in allowlist)
        )

        # 2. Check prohibited hostnames
        is_bad_host, host_reason = is_hostname_prohibited(target.host, allow_loopback=loopback_ok)
        if is_bad_host:
            logger.warning("SSRF block: Prohibited hostname '%s' (%s)", target.host, host_reason)
            raise PolicyBlockedError(
                message="Target destination is prohibited by security policy.",
                internal_reason=f"Prohibited hostname: {host_reason}",
            )

        # 3. Allowlist check
        assert_target_allowlisted(target.host, target.port, allowlist)

        # 4. Check literal IP vs domain name
        literal_ip = parse_ip(target.host)
        if literal_ip is not None:
            prohibited, reason = is_ip_prohibited(literal_ip, allow_loopback=loopback_ok)
            if prohibited:
                logger.warning("SSRF block: Literal IP %s (%s)", literal_ip, reason)
                raise PolicyBlockedError(
                    message="Target destination IP is prohibited by security policy.",
                    internal_reason=f"Prohibited IP: {reason}",
                )
            return target, [literal_ip]

        # 5. Domain DNS resolution
        resolved_ips = await self.resolver.resolve(target.host)
        validate_dns_records(resolved_ips, allow_loopback=loopback_ok)

        return target, resolved_ips

    async def is_valid_url(
        self,
        url: str,
        allowlist: Sequence[str] | None = None,
    ) -> tuple[bool, str | None]:
        """Convenience helper returning (is_valid, reason_str) instead of raising."""
        try:
            await self.validate_url(url, allowlist=allowlist)
            return True, None
        except Exception as exc:
            return False, str(exc)

