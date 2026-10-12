"""
uitp_policy.dns
===============
Injectable DNS resolution and record verification for SSRF / DNS rebinding defenses.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from ipaddress import IPv4Address, IPv6Address
from typing import Protocol, runtime_checkable

from uitp_policy.exceptions import PolicyBlockedError
from uitp_policy.ip import is_ip_prohibited


@runtime_checkable
class DNSResolver(Protocol):
    """Protocol for resolving hostnames to IP addresses."""

    async def resolve(self, host: str) -> list[IPv4Address | IPv6Address]:
        """Resolve a hostname to all associated IPv4 and IPv6 addresses."""
        ...


class SystemDNSResolver:
    """Production DNS resolver using asyncio and socket.getaddrinfo."""

    async def resolve(self, host: str) -> list[IPv4Address | IPv6Address]:
        loop = asyncio.get_running_loop()
        try:
            # Resolve all A and AAAA records
            results = await loop.getaddrinfo(
                host,
                None,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except socket.gaierror as exc:
            raise PolicyBlockedError(
                message="Target hostname could not be resolved.",
                internal_reason=f"DNS resolution failure: {exc}",
            ) from exc

        ips: list[IPv4Address | IPv6Address] = []
        for res in results:
            sockaddr = res[4]
            ip_str = sockaddr[0]
            try:
                ip_obj = ipaddress.ip_address(ip_str)
                if ip_obj not in ips:
                    ips.append(ip_obj)
            except ValueError:
                continue

        if not ips:
            raise PolicyBlockedError(
                message="Target hostname yielded no valid IP addresses.",
                internal_reason="Empty DNS response",
            )
        return ips


class MockDNSResolver:
    """Injectable fake DNS resolver for deterministic testing without external network calls."""

    def __init__(self, mapping: dict[str, list[str]] | None = None) -> None:
        self._mapping: dict[str, list[str]] = {}
        if mapping:
            for k, v in mapping.items():
                self._mapping[k.lower().rstrip(".")] = v

    def set_host(self, host: str, ips: list[str]) -> None:
        self._mapping[host.lower().rstrip(".")] = ips

    async def resolve(self, host: str) -> list[IPv4Address | IPv6Address]:
        norm = host.lower().rstrip(".")
        if norm not in self._mapping:
            raise PolicyBlockedError(
                message="Target hostname could not be resolved.",
                internal_reason=f"Mock DNS host '{norm}' not found",
            )

        ip_strs = self._mapping[norm]
        ips: list[IPv4Address | IPv6Address] = []
        for s in ip_strs:
            try:
                ips.append(ipaddress.ip_address(s))
            except ValueError as exc:
                raise PolicyBlockedError(
                    message="Target hostname yielded invalid IP addresses.",
                    internal_reason=f"Malformed mock IP '{s}'",
                ) from exc

        if not ips:
            raise PolicyBlockedError(
                message="Target hostname yielded no IP addresses.",
                internal_reason="Empty mock DNS mapping",
            )
        return ips


def validate_dns_records(
    resolved_ips: list[IPv4Address | IPv6Address],
) -> None:
    """Validate EVERY resolved address against prohibited IP policies.

    Rejects destination if ANY address is prohibited (e.g. mixed public/private
    records indicating DNS rebinding attacks).
    """
    if not resolved_ips:
        raise PolicyBlockedError("Target hostname yielded no IP addresses.")

    for ip in resolved_ips:
        prohibited, reason = is_ip_prohibited(ip)
        if prohibited:
            raise PolicyBlockedError(
                message="Destination address is prohibited by target security policy.",
                internal_reason=f"Resolved IP {ip} prohibited: {reason}",
            )
