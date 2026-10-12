"""
uitp_evidence.redaction
=======================
Sensitive data sanitization and redaction engine for test evidence and artifacts.

Guarantees:
  - Deep recursive redaction across nested dictionaries, lists, and primitives.
  - Strips credentials from URL query strings.
  - Strips sensitive headers (Authorization, Cookie, Set-Cookie, Proxy-Authorization, X-API-Key).
  - Matches and masks bearer tokens and JWTs in freeform strings.
  - Bounded size representation: truncates oversized payloads with explicit markers.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Keys that must always be masked in JSON structures and headers
SENSITIVE_FIELD_NAMES = frozenset(
    {
        "password",
        "passwd",
        "password_hash",
        "secret",
        "client_secret",
        "secret_key",
        "raw_secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "x-api-key",
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "private_key",
        "session",
        "session_id",
        "jwt",
    }
)

SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "token",
        "access_token",
        "refresh_token",
        "api_key",
        "apikey",
        "key",
        "secret",
        "password",
        "auth",
        "signature",
    }
)

REDACTED_MASK = "[REDACTED]"

# Regex for bearer tokens and JWT patterns in freeform strings
BEARER_PATTERN = re.compile(r"(?i)\b(bearer\s+)([a-zA-Z0-9_\-\.=]+)")
JWT_PATTERN = re.compile(r"\beyJ[a-zA-Z0-9_\-]+\.eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\b")


class RedactionEngine:
    """Sanitizes sensitive data from URLs, headers, and payload structures."""

    def __init__(
        self,
        extra_sensitive_keys: set[str] | None = None,
        max_string_len: int = 100_000,
    ) -> None:
        self.sensitive_keys = SENSITIVE_FIELD_NAMES.union(
            {k.lower() for k in (extra_sensitive_keys or set())}
        )
        self.max_string_len = max_string_len

    def redact_data(self, data: Any) -> tuple[Any, bool]:
        """Redact sensitive information from arbitrary objects (dict, list, str, etc.).

        Returns:
            (sanitized_data: Any, was_redacted: bool)
        """
        was_redacted = False

        if isinstance(data, Mapping):
            cleaned_dict = {}
            for k, v in data.items():
                k_str = str(k)
                if k_str.lower() in self.sensitive_keys:
                    cleaned_dict[k_str] = REDACTED_MASK
                    was_redacted = True
                else:
                    sub_val, sub_redacted = self.redact_data(v)
                    cleaned_dict[k_str] = sub_val
                    if sub_redacted:
                        was_redacted = True
            return cleaned_dict, was_redacted

        elif isinstance(data, (list, tuple, set)):
            cleaned_list = []
            for item in data:
                sub_val, sub_redacted = self.redact_data(item)
                cleaned_list.append(sub_val)
                if sub_redacted:
                    was_redacted = True
            return type(data)(cleaned_list), was_redacted

        elif isinstance(data, str):
            cleaned_str = data
            # Check if this string is a URL containing credentials
            if "://" in cleaned_str or "?" in cleaned_str:
                sanitized_url, url_redacted = self.redact_url(cleaned_str)
                if url_redacted:
                    cleaned_str = sanitized_url
                    was_redacted = True

            # Check Bearer tokens
            if BEARER_PATTERN.search(cleaned_str):
                cleaned_str = BEARER_PATTERN.sub(rf"\g<1>{REDACTED_MASK}", cleaned_str)
                was_redacted = True

            # Check JWTs
            if JWT_PATTERN.search(cleaned_str):
                cleaned_str = JWT_PATTERN.sub(REDACTED_MASK, cleaned_str)
                was_redacted = True

            # Check if string is serialized JSON
            if cleaned_str.strip().startswith(("{", "[")):
                try:
                    parsed = json.loads(cleaned_str)
                    clean_obj, json_redacted = self.redact_data(parsed)
                    if json_redacted:
                        cleaned_str = json.dumps(clean_obj)
                        was_redacted = True
                except (json.JSONDecodeError, TypeError):
                    pass

            # String length truncation bound
            if len(cleaned_str) > self.max_string_len:
                cleaned_str = (
                    cleaned_str[: self.max_string_len]
                    + f" ... [TRUNCATED {len(cleaned_str) - self.max_string_len} bytes]"
                )
                was_redacted = True

            return cleaned_str, was_redacted

        return data, was_redacted

    def redact_headers(self, headers: Mapping[str, str]) -> tuple[dict[str, str], bool]:
        """Redact sensitive HTTP headers."""
        cleaned = {}
        was_redacted = False
        for k, v in headers.items():
            if k.lower() in self.sensitive_keys:
                cleaned[k] = REDACTED_MASK
                was_redacted = True
            else:
                cleaned[k] = v
        return cleaned, was_redacted

    def redact_url(self, url: str) -> tuple[str, bool]:
        """Redact credentials from URL userInfo and sensitive query parameters."""
        try:
            parsed = urlparse(url)
        except Exception:
            return url, False

        was_redacted = False
        new_netloc = parsed.netloc

        # Strip user:password from authority
        if "@" in parsed.netloc:
            userinfo, hostport = parsed.netloc.split("@", 1)
            new_netloc = f"{REDACTED_MASK}@{hostport}"
            was_redacted = True

        # Sanitize sensitive query parameters
        if parsed.query:
            query_pairs = parse_qsl(parsed.query, keep_blank_values=True)
            sanitized_pairs = []
            for k, v in query_pairs:
                if k.lower() in SENSITIVE_QUERY_PARAMS or k.lower() in self.sensitive_keys:
                    sanitized_pairs.append((k, REDACTED_MASK))
                    was_redacted = True
                else:
                    sanitized_pairs.append((k, v))
            new_query = urlencode(sanitized_pairs)
        else:
            new_query = parsed.query

        if was_redacted:
            sanitized_url = urlunparse(
                (
                    parsed.scheme,
                    new_netloc,
                    parsed.path,
                    parsed.params,
                    new_query,
                    parsed.fragment,
                )
            )
            return sanitized_url, True

        return url, False

    def redact_content(
        self,
        content: bytes | str,
        content_type: str | None = None,
    ) -> tuple[bytes, bool]:
        """Redact sensitive data from raw artifact payload content.

        If payload is JSON or text, deep redaction is applied.
        Returns (sanitized_bytes, was_redacted).
        """
        if isinstance(content, str):
            clean_str, was_redacted = self.redact_data(content)
            return clean_str.encode("utf-8"), was_redacted

        # content is bytes
        ct = (content_type or "").lower()
        is_text_or_json = (
            "json" in ct
            or "text" in ct
            or "xml" in ct
            or "yaml" in ct
            or "csv" in ct
            or content_type is None
        )
        if is_text_or_json:
            try:
                decoded = content.decode("utf-8")
                clean_str, was_redacted = self.redact_data(decoded)
                return clean_str.encode("utf-8"), was_redacted
            except UnicodeDecodeError:
                pass
        return content, False


# Global default instance
default_redaction_engine = RedactionEngine()
