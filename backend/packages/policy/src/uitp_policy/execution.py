"""
uitp_policy.execution
=====================
Centralized execution policy and limits configuration for test execution (Task P3-07).

Defines authoritative limits across all adapters (HTTP, Browser/Playwright) and
worker runners, including:
  - Bounded wall-clock timeouts and step execution timeouts.
  - Bounded request and response payload byte limits.
  - Maximum redirect depths and retry caps.
  - Total per-run evidence byte and artifact count quotas.
  - Per-run temporary scratch directory storage limits.
  - Allowed URL schemes and strict SSRF policy enforcement.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

from uitp_policy.exceptions import (
    EvidenceQuotaExceededError,
    InvalidTargetUrlError,
    PayloadLimitExceededError,
)
from uitp_policy.redirect import RedirectValidator
from uitp_policy.validator import SSRFValidator

logger = logging.getLogger(__name__)

DEFAULT_WALL_CLOCK_TIMEOUT_SECONDS = 300.0   # 5 minutes
MAX_WALL_CLOCK_TIMEOUT_SECONDS = 1800.0       # 30 minutes
MIN_WALL_CLOCK_TIMEOUT_SECONDS = 1.0

DEFAULT_STEP_TIMEOUT_SECONDS = 30.0
MAX_STEP_TIMEOUT_SECONDS = 120.0
MIN_STEP_TIMEOUT_SECONDS = 0.5

DEFAULT_MAX_REQUEST_BYTES = 5 * 1024 * 1024    # 5 MB
DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024   # 5 MB
DEFAULT_MAX_REDIRECTS = 5
MAX_REDIRECTS_UPPER_BOUND = 10

DEFAULT_MAX_ATTEMPTS_PER_STEP = 3
MAX_ATTEMPTS_UPPER_BOUND = 5

DEFAULT_MAX_EVIDENCE_BYTES_PER_RUN = 50 * 1024 * 1024  # 50 MB
DEFAULT_MAX_ARTIFACTS_PER_RUN = 100

DEFAULT_MAX_TEMP_DIR_BYTES = 100 * 1024 * 1024  # 100 MB
DEFAULT_MAX_CONCURRENCY = 4

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


@dataclass(frozen=True)
class ExecutionLimits:
    """Immutable execution boundaries enforced across adapters and worker runners."""

    wall_clock_timeout_seconds: float = DEFAULT_WALL_CLOCK_TIMEOUT_SECONDS
    step_timeout_seconds: float = DEFAULT_STEP_TIMEOUT_SECONDS
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_redirects: int = DEFAULT_MAX_REDIRECTS
    max_attempts_per_step: int = DEFAULT_MAX_ATTEMPTS_PER_STEP
    max_total_evidence_bytes_per_run: int = DEFAULT_MAX_EVIDENCE_BYTES_PER_RUN
    max_artifacts_per_run: int = DEFAULT_MAX_ARTIFACTS_PER_RUN
    max_temp_dir_bytes: int = DEFAULT_MAX_TEMP_DIR_BYTES
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY
    allowed_schemes: tuple[str, ...] = ("http", "https")
    enforce_ssrf: bool = True
    strip_sensitive_headers_on_redirect: bool = True
    redact_secrets_in_logs: bool = True

    def __post_init__(self) -> None:
        if not (MIN_WALL_CLOCK_TIMEOUT_SECONDS <= self.wall_clock_timeout_seconds <= MAX_WALL_CLOCK_TIMEOUT_SECONDS):
            raise ValueError(
                f"wall_clock_timeout_seconds must be between {MIN_WALL_CLOCK_TIMEOUT_SECONDS} "
                f"and {MAX_WALL_CLOCK_TIMEOUT_SECONDS}, got {self.wall_clock_timeout_seconds}"
            )
        if not (MIN_STEP_TIMEOUT_SECONDS <= self.step_timeout_seconds <= MAX_STEP_TIMEOUT_SECONDS):
            raise ValueError(
                f"step_timeout_seconds must be between {MIN_STEP_TIMEOUT_SECONDS} "
                f"and {MAX_STEP_TIMEOUT_SECONDS}, got {self.step_timeout_seconds}"
            )
        if self.max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be strictly positive")
        if self.max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be strictly positive")
        if not (0 <= self.max_redirects <= MAX_REDIRECTS_UPPER_BOUND):
            raise ValueError(f"max_redirects must be between 0 and {MAX_REDIRECTS_UPPER_BOUND}")
        if not (1 <= self.max_attempts_per_step <= MAX_ATTEMPTS_UPPER_BOUND):
            raise ValueError(f"max_attempts_per_step must be between 1 and {MAX_ATTEMPTS_UPPER_BOUND}")
        if self.max_total_evidence_bytes_per_run <= 0:
            raise ValueError("max_total_evidence_bytes_per_run must be positive")
        if self.max_artifacts_per_run <= 0:
            raise ValueError("max_artifacts_per_run must be positive")
        if self.max_temp_dir_bytes <= 0:
            raise ValueError("max_temp_dir_bytes must be positive")
        if self.max_concurrency <= 0:
            raise ValueError("max_concurrency must be positive")


class ExecutionPolicy:
    """Evaluates and applies execution limits, URL schemes, and security boundaries."""

    def __init__(
        self,
        limits: ExecutionLimits | None = None,
        ssrf_validator: SSRFValidator | None = None,
        redirect_validator: RedirectValidator | None = None,
    ) -> None:
        self.limits: ExecutionLimits = limits or ExecutionLimits()
        self.ssrf_validator: SSRFValidator = ssrf_validator or SSRFValidator()
        self.redirect_validator: RedirectValidator = (
            redirect_validator
            or RedirectValidator(
                validator=self.ssrf_validator,
                max_redirects=self.limits.max_redirects,
            )
        )

    def compute_step_timeout(
        self,
        requested_timeout: float | None = None,
        run_remaining_time: float | None = None,
    ) -> float:
        """Calculate bounded step timeout respecting step cap and run remaining deadline."""
        base_timeout = self.limits.step_timeout_seconds
        if requested_timeout is not None and requested_timeout > 0:
            base_timeout = min(requested_timeout, self.limits.step_timeout_seconds)

        if run_remaining_time is not None:
            bounded = min(base_timeout, max(0.1, run_remaining_time))
            return max(0.1, bounded)

        return max(0.1, base_timeout)

    def validate_request_payload(self, size_bytes: int) -> None:
        """Validate request payload size against max_request_bytes."""
        if size_bytes > self.limits.max_request_bytes:
            raise PayloadLimitExceededError(
                f"Request payload size of {size_bytes} bytes exceeds maximum limit "
                f"of {self.limits.max_request_bytes} bytes."
            )

    def validate_response_payload(self, size_bytes: int) -> None:
        """Validate response payload size against max_response_bytes."""
        if size_bytes > self.limits.max_response_bytes:
            raise PayloadLimitExceededError(
                f"Response body size of {size_bytes} bytes exceeds maximum limit "
                f"of {self.limits.max_response_bytes} bytes."
            )

    def validate_url_scheme(self, url: str) -> None:
        """Validate that the target URL scheme is explicitly in allowed_schemes."""
        parsed = urlparse(url)
        scheme = parsed.scheme.lower()
        if scheme not in self.limits.allowed_schemes:
            raise InvalidTargetUrlError(
                f"URL scheme '{scheme}' is prohibited by execution policy. "
                f"Allowed schemes: {sorted(self.limits.allowed_schemes)}"
            )

    def check_evidence_quota(
        self,
        current_total_bytes: int,
        new_artifact_bytes: int,
        current_artifact_count: int,
    ) -> None:
        """Verify adding artifact does not exceed per-run evidence limits."""
        if current_artifact_count + 1 > self.limits.max_artifacts_per_run:
            raise EvidenceQuotaExceededError(
                f"Run exceeded maximum artifact count limit of {self.limits.max_artifacts_per_run}."
            )
        if current_total_bytes + new_artifact_bytes > self.limits.max_total_evidence_bytes_per_run:
            raise EvidenceQuotaExceededError(
                f"Run exceeded maximum total evidence size quota of "
                f"{self.limits.max_total_evidence_bytes_per_run} bytes (attempted: "
                f"{current_total_bytes + new_artifact_bytes} bytes)."
            )
