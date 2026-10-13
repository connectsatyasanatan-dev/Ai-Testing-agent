"""
tests.unit.test_execution_policy
================================
Unit tests for uitp_policy.execution: ExecutionLimits and ExecutionPolicy (Task P3-07).
"""
from __future__ import annotations

import pytest

from uitp_policy.exceptions import (
    EvidenceQuotaExceededError,
    InvalidTargetUrlError,
    PayloadLimitExceededError,
)
from uitp_policy.execution import (
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_STEP_TIMEOUT_SECONDS,
    DEFAULT_WALL_CLOCK_TIMEOUT_SECONDS,
    ExecutionLimits,
    ExecutionPolicy,
)


@pytest.mark.unit
class TestExecutionLimitsValidation:
    def test_default_limits_valid(self) -> None:
        limits = ExecutionLimits()
        assert limits.wall_clock_timeout_seconds == DEFAULT_WALL_CLOCK_TIMEOUT_SECONDS
        assert limits.step_timeout_seconds == DEFAULT_STEP_TIMEOUT_SECONDS
        assert limits.max_request_bytes == DEFAULT_MAX_REQUEST_BYTES
        assert limits.max_response_bytes == DEFAULT_MAX_RESPONSE_BYTES
        assert limits.max_redirects == 5
        assert limits.max_attempts_per_step == 3
        assert limits.allowed_schemes == ("http", "https")
        assert limits.enforce_ssrf is True

    def test_custom_valid_limits(self) -> None:
        limits = ExecutionLimits(
            wall_clock_timeout_seconds=600.0,
            step_timeout_seconds=45.0,
            max_request_bytes=10 * 1024 * 1024,
            max_response_bytes=10 * 1024 * 1024,
            max_redirects=3,
            max_attempts_per_step=2,
        )
        assert limits.wall_clock_timeout_seconds == 600.0
        assert limits.step_timeout_seconds == 45.0
        assert limits.max_redirects == 3

    def test_invalid_wall_clock_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="wall_clock_timeout_seconds"):
            ExecutionLimits(wall_clock_timeout_seconds=0.0)
        with pytest.raises(ValueError, match="wall_clock_timeout_seconds"):
            ExecutionLimits(wall_clock_timeout_seconds=3600.0)

    def test_invalid_step_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match="step_timeout_seconds"):
            ExecutionLimits(step_timeout_seconds=0.1)
        with pytest.raises(ValueError, match="step_timeout_seconds"):
            ExecutionLimits(step_timeout_seconds=300.0)

    def test_invalid_payload_bytes_raises(self) -> None:
        with pytest.raises(ValueError, match="max_request_bytes"):
            ExecutionLimits(max_request_bytes=0)
        with pytest.raises(ValueError, match="max_response_bytes"):
            ExecutionLimits(max_response_bytes=-1)

    def test_invalid_redirects_and_attempts_raises(self) -> None:
        with pytest.raises(ValueError, match="max_redirects"):
            ExecutionLimits(max_redirects=20)
        with pytest.raises(ValueError, match="max_attempts_per_step"):
            ExecutionLimits(max_attempts_per_step=0)


@pytest.mark.unit
class TestExecutionPolicyOperations:
    @pytest.fixture
    def policy(self) -> ExecutionPolicy:
        return ExecutionPolicy(limits=ExecutionLimits())

    def test_compute_step_timeout_clamping(self, policy: ExecutionPolicy) -> None:
        # Default step timeout is 30s
        assert policy.compute_step_timeout(requested_timeout=15.0) == 15.0
        assert policy.compute_step_timeout(requested_timeout=60.0) == 30.0  # capped to policy step timeout
        assert policy.compute_step_timeout(requested_timeout=None) == 30.0

        # Run remaining time bounds
        assert policy.compute_step_timeout(requested_timeout=20.0, run_remaining_time=10.0) == 10.0
        assert policy.compute_step_timeout(requested_timeout=20.0, run_remaining_time=0.05) == 0.1  # min bound

    def test_validate_payload_sizes(self, policy: ExecutionPolicy) -> None:
        # Allowed sizes
        policy.validate_request_payload(1024)
        policy.validate_response_payload(1024)

        # Exceeding sizes raise PayloadLimitExceededError
        with pytest.raises(PayloadLimitExceededError, match="Request payload size"):
            policy.validate_request_payload(6 * 1024 * 1024)

        with pytest.raises(PayloadLimitExceededError, match="Response body size"):
            policy.validate_response_payload(6 * 1024 * 1024)

    def test_validate_url_schemes(self, policy: ExecutionPolicy) -> None:
        # Permitted schemes
        policy.validate_url_scheme("http://example.com/api")
        policy.validate_url_scheme("https://example.com/api")

        # Prohibited schemes
        for forbidden in [
            "file:///etc/passwd",
            "gopher://example.com",
            "ftp://example.com/file",
            "data:text/plain;base64,SGVsbG8=",
        ]:
            with pytest.raises(InvalidTargetUrlError, match="prohibited by execution policy"):
                policy.validate_url_scheme(forbidden)

    def test_check_evidence_quota(self, policy: ExecutionPolicy) -> None:
        # Valid usage
        policy.check_evidence_quota(current_total_bytes=1000, new_artifact_bytes=500, current_artifact_count=5)

        # Exceeding artifact count
        with pytest.raises(EvidenceQuotaExceededError, match="maximum artifact count limit"):
            policy.check_evidence_quota(current_total_bytes=1000, new_artifact_bytes=500, current_artifact_count=100)

        # Exceeding total byte quota
        with pytest.raises(EvidenceQuotaExceededError, match="maximum total evidence size quota"):
            policy.check_evidence_quota(
                current_total_bytes=49 * 1024 * 1024,
                new_artifact_bytes=2 * 1024 * 1024,
                current_artifact_count=10,
            )
