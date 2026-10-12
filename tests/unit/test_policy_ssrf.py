"""
tests/unit/test_policy_ssrf.py
==============================
Deterministic security tests for SSRF-safe target validation and URL policy (Task P1-02).
"""
from __future__ import annotations

import pytest

from uitp_contracts.enums import ErrorCode
from uitp_policy import (
    InvalidTargetUrlError,
    MockDNSResolver,
    PolicyBlockedError,
    RedirectValidator,
    SSRFValidator,
    TargetNotAllowlistedError,
    normalize_url,
)


@pytest.fixture
def mock_dns():
    return MockDNSResolver({
        "public.example.com": ["93.184.216.34"],
        "api.good.com": ["198.51.100.10"],
        "internal.evil.com": ["10.0.0.1"],
        "localhost.evil.com": ["127.0.0.1"],
        "metadata.evil.com": ["169.254.169.254"],
        "rebinding.evil.com": ["93.184.216.34", "192.168.1.50"],  # Mixed public & private
        "ipv6.good.com": ["2606:2800:220:1:248:1893:25c8:1946"],
        "ipv6.private.com": ["fd00::1"],
        "ipv6.loopback.com": ["::1"],
    })


@pytest.fixture
def ssrf_validator(mock_dns):
    return SSRFValidator(resolver=mock_dns)


@pytest.mark.unit
class TestURLNormalizationAndSyntax:
    def test_valid_http_and_https_normalized(self) -> None:
        t1 = normalize_url("https://example.com/api/v1?foo=bar#frag")
        assert t1.scheme == "https"
        assert t1.host == "example.com"
        assert t1.port == 443
        assert t1.path == "/api/v1"
        assert t1.query == "foo=bar"
        assert "#" not in t1.url

        t2 = normalize_url("http://example.com:8080/")
        assert t2.port == 8080
        assert t2.host_port == "example.com:8080"

    def test_reject_unsupported_schemes(self) -> None:
        for bad_scheme in ["ftp://example.com", "gopher://example.com", "file:///etc/passwd"]:
            with pytest.raises(InvalidTargetUrlError, match="Unsupported scheme"):
                normalize_url(bad_scheme)

    def test_reject_embedded_credentials(self) -> None:
        with pytest.raises(InvalidTargetUrlError, match="Embedded credentials"):
            normalize_url("https://user:password@example.com/api")

    def test_reject_invalid_ports(self) -> None:
        with pytest.raises(InvalidTargetUrlError, match="Port 0"):
            normalize_url("http://example.com:0/")
        with pytest.raises(InvalidTargetUrlError, match="Port 70000"):
            normalize_url("http://example.com:70000/")


@pytest.mark.unit
class TestAllowlistEnforcement:
    @pytest.mark.asyncio
    async def test_allowed_hostname_and_port(self, ssrf_validator: SSRFValidator) -> None:
        allowlist = ["public.example.com:443", "*.good.com"]
        target, ips = await ssrf_validator.validate_url("https://public.example.com/v1", allowlist=allowlist)
        assert target.host == "public.example.com"
        assert len(ips) == 1

        target2, ips2 = await ssrf_validator.validate_url("https://api.good.com/test", allowlist=allowlist)
        assert target2.host == "api.good.com"

    @pytest.mark.asyncio
    async def test_hostname_outside_allowlist_raises_typed_error(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        allowlist = ["public.example.com:443"]
        with pytest.raises(TargetNotAllowlistedError) as exc_info:
            await ssrf_validator.validate_url("https://api.good.com/v1", allowlist=allowlist)
        assert exc_info.value.code == ErrorCode.TARGET_NOT_ALLOWLISTED


@pytest.mark.unit
class TestProhibitedIPCategories:
    @pytest.mark.asyncio
    async def test_literal_loopback_and_private_ipv4_blocked(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        prohibited = [
            "http://127.0.0.1:8000/api",
            "http://127.0.0.2/",
            "http://10.0.0.1/admin",
            "http://172.16.0.1/",
            "http://192.168.1.1/",
            "http://0.0.0.0/",
            "http://224.0.0.1/",  # Multicast
        ]
        for url in prohibited:
            with pytest.raises(PolicyBlockedError) as exc_info:
                await ssrf_validator.validate_url(url)
            assert exc_info.value.code == ErrorCode.POLICY_BLOCKED

    @pytest.mark.asyncio
    async def test_literal_cloud_metadata_ip_blocked(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        metadata_targets = [
            "http://169.254.169.254/latest/meta-data/",
            "http://100.100.100.200/latest/meta-data/",  # Alibaba
            "http://[fd00:ec2::254]/latest/meta-data/",  # AWS IPv6 IMDS
        ]
        for url in metadata_targets:
            with pytest.raises(PolicyBlockedError):
                await ssrf_validator.validate_url(url)

    @pytest.mark.asyncio
    async def test_literal_ipv6_loopback_and_private_blocked(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        ipv6_blocked = [
            "http://[::1]/",
            "http://[fc00::1]/",
            "http://[fe80::1]/",  # Link local
            "http://[ff02::1]/",  # Multicast
        ]
        for url in ipv6_blocked:
            with pytest.raises(PolicyBlockedError):
                await ssrf_validator.validate_url(url)

    @pytest.mark.asyncio
    async def test_prohibited_metadata_hostnames_blocked(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        for host in ["http://metadata.google.internal/", "http://instance-data/"]:
            with pytest.raises(PolicyBlockedError):
                await ssrf_validator.validate_url(host)


@pytest.mark.unit
class TestDNSResolutionAndRebindingDefenses:
    @pytest.mark.asyncio
    async def test_domain_resolving_to_private_ip_blocked(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        with pytest.raises(PolicyBlockedError):
            await ssrf_validator.validate_url("http://internal.evil.com/admin")

        with pytest.raises(PolicyBlockedError):
            await ssrf_validator.validate_url("http://localhost.evil.com/")

        with pytest.raises(PolicyBlockedError):
            await ssrf_validator.validate_url("http://metadata.evil.com/")

    @pytest.mark.asyncio
    async def test_domain_with_mixed_public_and_private_ips_blocked(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        # DNS rebinding mitigation: if ANY address in resolution is private, fail closed
        with pytest.raises(PolicyBlockedError):
            await ssrf_validator.validate_url("http://rebinding.evil.com/")

    @pytest.mark.asyncio
    async def test_dns_resolution_failure_fails_closed(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        with pytest.raises(PolicyBlockedError, match="could not be resolved"):
            await ssrf_validator.validate_url("http://nonexistent.domain.test/")


@pytest.mark.unit
class TestRedirectValidationAndSafety:
    @pytest.mark.asyncio
    async def test_redirect_to_allowed_public_target(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        red_val = RedirectValidator(validator=ssrf_validator)
        url, target = await red_val.validate_redirect(
            current_url="https://public.example.com/login",
            redirect_location="/dashboard",
            redirect_history=[],
        )
        assert url == "https://public.example.com/dashboard"
        assert target.path == "/dashboard"

    @pytest.mark.asyncio
    async def test_redirect_to_private_or_metadata_target_blocked(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        red_val = RedirectValidator(validator=ssrf_validator)

        # Redirect to private IP
        with pytest.raises(PolicyBlockedError):
            await red_val.validate_redirect(
                current_url="https://public.example.com/page",
                redirect_location="http://10.0.0.1/admin",
                redirect_history=[],
            )

        # Redirect to cloud metadata
        with pytest.raises(PolicyBlockedError):
            await red_val.validate_redirect(
                current_url="https://public.example.com/page",
                redirect_location="http://169.254.169.254/latest/meta-data/",
                redirect_history=[],
            )

    @pytest.mark.asyncio
    async def test_redirect_loop_and_depth_limits(
        self, ssrf_validator: SSRFValidator
    ) -> None:
        red_val = RedirectValidator(validator=ssrf_validator, max_redirects=3)

        # Depth exceeded
        with pytest.raises(PolicyBlockedError, match="maximum redirect depth"):
            await red_val.validate_redirect(
                current_url="https://public.example.com/3",
                redirect_location="/4",
                redirect_history=[
                    "https://public.example.com/0",
                    "https://public.example.com/1",
                    "https://public.example.com/2",
                ],
            )

        # Loop detected
        with pytest.raises(PolicyBlockedError, match="circular redirect loop"):
            await red_val.validate_redirect(
                current_url="https://public.example.com/b",
                redirect_location="/a",
                redirect_history=["https://public.example.com/a"],
            )

    def test_sanitize_headers_on_cross_origin_redirect(self) -> None:
        headers = {
            "Authorization": "Bearer secret-token-xyz",
            "Cookie": "session_id=123",
            "Content-Type": "application/json",
            "X-Custom-Trace": "req-1",
        }
        sanitized = RedirectValidator.sanitize_redirect_headers(
            headers=headers,
            from_url="https://auth.example.com/login",
            to_url="https://other.example.com/welcome",
        )
        assert "Authorization" not in sanitized
        assert "Cookie" not in sanitized
        assert sanitized["Content-Type"] == "application/json"
        assert sanitized["X-Custom-Trace"] == "req-1"
