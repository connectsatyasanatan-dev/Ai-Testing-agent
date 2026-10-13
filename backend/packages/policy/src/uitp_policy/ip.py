"""
uitp_policy.ip
==============
Prohibited destination IP and cloud metadata detection.
"""
from __future__ import annotations

import ipaddress
from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network

# Explicit Cloud Metadata IP ranges and addresses
CLOUD_METADATA_NETWORKS: tuple[IPv4Network | IPv6Network, ...] = (
    ipaddress.ip_network("169.254.169.254/32"),   # AWS, GCP, Azure, OpenStack IMDS IPv4
    ipaddress.ip_network("fd00:ec2::254/128"),     # AWS IMDSv6
    ipaddress.ip_network("100.100.100.200/32"),   # Alibaba Cloud IMDS
)

PROHIBITED_METADATA_HOSTNAMES = frozenset(
    {
        "metadata.google.internal",
        "metadata.internal",
        "instance-data",
        "metadata",
    }
)


def parse_ip(address_str: str) -> IPv4Address | IPv6Address | None:
    """Parse string to IP address object, or return None if it is a domain name."""
    try:
        return ipaddress.ip_address(address_str)
    except ValueError:
        return None


def is_ip_prohibited(
    ip: IPv4Address | IPv6Address,
    *,
    allow_loopback: bool = False,
) -> tuple[bool, str]:
    """Evaluate whether an IP address belongs to a prohibited SSRF category.

    Categories blocked:
      - Cloud metadata addresses (e.g. 169.254.169.254, fd00:ec2::254)
      - Loopback (127.0.0.0/8, ::1) unless explicitly permitted (e.g. in test fixtures)
      - Private RFC-1918 / Unique-Local (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7)
      - Link-local (169.254.0.0/16, fe80::/10)
      - Multicast (224.0.0.0/4, ff00::/8)
      - Unspecified (0.0.0.0, ::)
      - Reserved

    Returns:
      (is_prohibited: bool, reason: str)
    """
    # 1. Cloud metadata check
    for net in CLOUD_METADATA_NETWORKS:
        if ip in net:
            return True, "Cloud metadata service destination"

    # 2. Loopback
    if ip.is_loopback:
        if allow_loopback:
            return False, "Loopback destination permitted"
        return True, "Loopback destination address"

    # 3. Private / RFC-1918 / ULA
    if ip.is_private:
        return True, "Private internal network address"

    # 4. Link-local
    if ip.is_link_local:
        return True, "Link-local destination address"

    # 5. Multicast
    if ip.is_multicast:
        return True, "Multicast destination address"

    # 6. Unspecified / 0.0.0.0
    if ip.is_unspecified:
        return True, "Unspecified destination address"

    # 7. Reserved
    if ip.is_reserved:
        return True, "Reserved destination address"

    return False, "Public IP permitted"


def is_hostname_prohibited(
    hostname: str,
    *,
    allow_loopback: bool = False,
) -> tuple[bool, str]:
    """Check for known prohibited hostnames (metadata or localhost names)."""
    norm = hostname.lower().rstrip(".")

    if norm == "localhost" or norm.endswith(".localhost") or norm == "local":
        if allow_loopback:
            return False, "Localhost hostname permitted"
        return True, "Localhost hostname"

    if norm in PROHIBITED_METADATA_HOSTNAMES:
        return True, f"Prohibited cloud metadata hostname '{norm}'"

    return False, "Hostname permitted"
