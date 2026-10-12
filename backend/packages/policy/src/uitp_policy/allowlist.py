"""
uitp_policy.allowlist
==============
Environment target allowlist evaluation and pattern matching.
"""
from __future__ import annotations

import fnmatch
from typing import Sequence

from uitp_policy.exceptions import TargetNotAllowlistedError


def is_target_allowlisted(
    host: str,
    port: int,
    allowlist: Sequence[str] | None,
) -> bool:
    """Evaluate whether host and port match the environment allowlist.

    Rules:
      - If allowlist is None or empty: allowlist is not restricted (returns True).
      - An entry 'example.com' matches host 'example.com' on any port.
      - An entry 'example.com:443' matches host 'example.com' strictly on port 443.
      - An entry '*.example.com' matches any subdomain (e.g. 'api.example.com').
      - An entry '*.example.com:8080' matches any subdomain strictly on port 8080.
    """
    if not allowlist:
        return True

    target_host = host.lower().rstrip(".")
    target_host_port = f"{target_host}:{port}"

    for entry in allowlist:
        cleaned = entry.strip().lower().rstrip(".")
        if not cleaned:
            continue

        # Check if entry specifies a port
        if ":" in cleaned and not cleaned.startswith("["):  # not a raw IPv6 without port
            entry_host, entry_port_str = cleaned.rsplit(":", 1)
            try:
                entry_port = int(entry_port_str)
            except ValueError:
                continue

            if port == entry_port and (
                entry_host == target_host or fnmatch.fnmatch(target_host, entry_host)
            ):
                return True
        else:
            # Entry has no port specified -> matches any port on that host
            if cleaned == target_host or fnmatch.fnmatch(target_host, cleaned):
                return True

    return False


def assert_target_allowlisted(
    host: str,
    port: int,
    allowlist: Sequence[str] | None,
) -> None:
    """Enforce allowlist, raising TargetNotAllowlistedError if check fails."""
    if not is_target_allowlisted(host, port, allowlist):
        raise TargetNotAllowlistedError(f"{host}:{port}")
