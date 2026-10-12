"""
uitp_policy.normalizer
======================
Safe URL parsing, normalization, and canonical representation.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit
from pydantic import BaseModel, ConfigDict

from uitp_policy.exceptions import InvalidTargetUrlError


class ParsedTarget(BaseModel):
    """Canonical parsed and normalized target representation."""

    model_config = ConfigDict(frozen=True)

    url: str
    scheme: str
    host: str
    port: int
    path: str
    query: str | None = None

    @property
    def host_port(self) -> str:
        """Returns host:port string."""
        return f"{self.host}:{self.port}"


def normalize_url(url_str: str) -> ParsedTarget:
    """Parse and normalize candidate URL safely.

    Enforces:
      - Scheme strictly in ('http', 'https')
      - No embedded credentials (username:password)
      - Non-empty host with valid characters
      - Valid port range (1 - 65535)
      - Normalized lowercase hostname
      - Removal of fragment
    """
    if not isinstance(url_str, str) or not url_str.strip():
        raise InvalidTargetUrlError("Target URL cannot be empty.")

    cleaned = url_str.strip()
    try:
        parsed = urlsplit(cleaned)
    except Exception as exc:
        raise InvalidTargetUrlError(f"Malformed URL: {exc}") from exc

    scheme = parsed.scheme.lower()
    if scheme not in ("http", "https"):
        raise InvalidTargetUrlError(f"Unsupported scheme '{scheme}'. Only http and https are allowed.")

    if parsed.username or parsed.password:
        raise InvalidTargetUrlError("Embedded credentials in target URL are prohibited.")

    if not parsed.hostname:
        raise InvalidTargetUrlError("Target URL must contain a valid hostname.")

    # Normalize host
    host = parsed.hostname.lower().rstrip(".")
    if not host:
        raise InvalidTargetUrlError("Target hostname cannot be empty.")

    # Resolve default port if missing
    try:
        raw_port = parsed.port
    except ValueError:
        port_str = parsed.netloc.split(":")[-1] if ":" in parsed.netloc else "out of range"
        raise InvalidTargetUrlError(f"Port {port_str} is out of valid range (1-65535).")

    if raw_port is not None:
        port = raw_port
        if not (1 <= port <= 65535):
            raise InvalidTargetUrlError(f"Port {port} is out of valid range (1-65535).")
    else:
        port = 443 if scheme == "https" else 80

    path = parsed.path or "/"
    query = parsed.query if parsed.query else None

    # Reconstruct normalized URL (stripping fragments and credentials)
    netloc = f"{host}:{port}" if (scheme == "http" and port != 80) or (scheme == "https" and port != 443) else host
    normalized_full = urlunsplit((scheme, netloc, path, query or "", ""))

    return ParsedTarget(
        url=normalized_full,
        scheme=scheme,
        host=host,
        port=port,
        path=path,
        query=query,
    )
