"""
uitp_policy
===========
Security policies: SSRF defenses, target allowlists, URL normalization,
and redirect protection for the Universal AI Testing Platform.
"""
from uitp_policy.allowlist import (
    assert_target_allowlisted,
    is_target_allowlisted,
)
from uitp_policy.dns import (
    DNSResolver,
    MockDNSResolver,
    SystemDNSResolver,
    validate_dns_records,
)
from uitp_policy.exceptions import (
    InvalidTargetUrlError,
    PolicyBlockedError,
    PolicyError,
    TargetNotAllowlistedError,
)
from uitp_policy.ip import (
    is_hostname_prohibited,
    is_ip_prohibited,
    parse_ip,
)
from uitp_policy.normalizer import (
    ParsedTarget,
    normalize_url,
)
from uitp_policy.redirect import (
    RedirectValidator,
)
from uitp_policy.validator import (
    SSRFValidator,
)

__all__ = [
    "PolicyError",
    "PolicyBlockedError",
    "TargetNotAllowlistedError",
    "InvalidTargetUrlError",
    "ParsedTarget",
    "normalize_url",
    "is_ip_prohibited",
    "is_hostname_prohibited",
    "parse_ip",
    "is_target_allowlisted",
    "assert_target_allowlisted",
    "DNSResolver",
    "SystemDNSResolver",
    "MockDNSResolver",
    "validate_dns_records",
    "SSRFValidator",
    "RedirectValidator",
]
