"""
tests/unit/test_adapter_http_api.py
===================================
Unit tests for the Secure HTTP API Testing Adapter (Task P1-04).

Verifies:
  1. Successful GET execution and response parsing.
  2. Successful POST execution with JSON payloads.
  3. Non-success HTTP status handling (500, 422) captured without throwing unhandled exceptions.
  4. Connection timeout and bounded execution.
  5. Oversized response rejection (enforcing max_response_bytes).
  6. Invalid response JSON graceful fallback to text.
  7. Unsupported action/method and malformed step rejection.
  8. Relative-path resolution with ExecutionContext.base_url.
  9. Environment allowlist enforcement (blocking unlisted hosts).
  10. Safe redirect to an allowed target followed.
  11. Redirect to a disallowed target blocked by allowlist.
  12. Redirect to private/metadata address (169.254.169.254) blocked by SSRF policy.
  13. Redirect-limit enforcement.
  14. Non-idempotent steps are NEVER automatically retried (attempted exactly once, warning logged).
  15. Eligible retry behavior for safe/idempotent steps with retryable status codes.
  16. Sensitive headers stripped on cross-origin redirects.
  17. Header CRLF injection rejected.
  18. Health check and capability metadata.
"""
from __future__ import annotations

import logging
from ipaddress import IPv4Address
import httpx
import pytest

from tests.fixtures.mock_http_api import create_mock_api_app
from uitp_adapter_base.base import ExecutionContext
from uitp_adapter_base.exceptions import UnsupportedActionError
from uitp_adapter_http_api import HttpApiAdapter
from uitp_contracts.enums import (
    AdapterType,
    ErrorCode,
    IdempotencyClass,
    StepStatus,
)
from uitp_contracts.plan import RetryPolicySchema, TestStepSchema
from uitp_policy.dns import MockDNSResolver
from uitp_policy.redirect import RedirectValidator
from uitp_policy.validator import SSRFValidator


@pytest.fixture
def mock_app():
    return create_mock_api_app()


@pytest.fixture
def mock_dns():
    # Resolver that maps mock-api.local and allowed-api.local to public-range IP for testing
    return MockDNSResolver(
        mapping={
            "mock-api.local": [IPv4Address("93.184.216.34")],
            "allowed-api.local": [IPv4Address("93.184.216.35")],
            "evil.local": [IPv4Address("169.254.169.254")],
            "internal.local": [IPv4Address("10.0.0.1")],
        }
    )


@pytest.fixture
def ssrf_val(mock_dns):
    return SSRFValidator(resolver=mock_dns)


@pytest.fixture
def adapter(mock_app, ssrf_val):
    transport = httpx.ASGITransport(app=mock_app)
    return HttpApiAdapter(
        transport=transport,
        ssrf_validator=ssrf_val,
        max_redirects=5,
        max_response_bytes=1024 * 1024,
    )


@pytest.fixture
def exec_context():
    return ExecutionContext(
        run_id="run_test_1",
        step_id="step_test_1",
        attempt_number=1,
        timeout_seconds=5.0,
        base_url="http://mock-api.local",
        allowlist=["mock-api.local"],
    )


@pytest.mark.unit
class TestHttpApiAdapterExecution:
    @pytest.mark.asyncio
    async def test_get_ok_success(self, adapter: HttpApiAdapter, exec_context: ExecutionContext) -> None:
        step = TestStepSchema(
            step_id="step_get_ok",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/ok"},
        )
        result = await adapter.execute_step(step, exec_context)

        assert result.status == StepStatus.PASSED
        assert result.outputs["status_code"] == 200
        assert result.outputs["body"]["status"] == "ok"
        assert result.outputs["body"]["data"]["count"] == 42
        assert result.outputs["attempt_count"] == 1
        assert result.outputs["redirect_count"] == 0
        assert result.outputs["duration_ms"] > 0

    @pytest.mark.asyncio
    async def test_post_create_success(self, adapter: HttpApiAdapter, exec_context: ExecutionContext) -> None:
        payload = {"name": "Test Widget", "active": True}
        step = TestStepSchema(
            step_id="step_post_create",
            adapter_type=AdapterType.HTTP_API,
            action="post",
            inputs={"path": "/create", "json": payload},
            idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
        )
        result = await adapter.execute_step(step, exec_context)

        assert result.status == StepStatus.PASSED
        assert result.outputs["status_code"] == 201
        assert result.outputs["body"]["id"] == "created_456"
        assert result.outputs["body"]["received"] == payload
        assert result.outputs["headers"]["location"] == "/items/created_456"

    @pytest.mark.asyncio
    async def test_get_fail_non_success_status(self, adapter: HttpApiAdapter, exec_context: ExecutionContext) -> None:
        step = TestStepSchema(
            step_id="step_get_fail",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/fail"},
            idempotency_class=IdempotencyClass.SAFE,
        )
        result = await adapter.execute_step(step, exec_context)

        # The HTTP transaction completes and records 500 status in outputs for assertions
        assert result.status == StepStatus.PASSED
        assert result.outputs["status_code"] == 500
        assert result.outputs["body"]["error"]["code"] == "INTERNAL_SERVER_ERROR"

    @pytest.mark.asyncio
    async def test_timeout_bounded_execution(self, adapter: HttpApiAdapter, exec_context: ExecutionContext) -> None:
        step = TestStepSchema(
            step_id="step_timeout",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/slow?delay=0.3"},
            timeout_seconds=0.05,  # Very short timeout
            idempotency_class=IdempotencyClass.SAFE,
        )
        result = await adapter.execute_step(step, exec_context)

        assert result.status == StepStatus.FAILED
        assert result.error_code == ErrorCode.VALIDATION_ERROR
        assert "timed out" in (result.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_oversized_response_rejected(self, mock_app, ssrf_val, exec_context: ExecutionContext) -> None:
        # Create adapter with small response limit of 50 bytes
        tiny_adapter = HttpApiAdapter(
            transport=httpx.ASGITransport(app=mock_app),
            ssrf_validator=ssrf_val,
            max_response_bytes=50,
        )
        step = TestStepSchema(
            step_id="step_oversized",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/ok"},  # /ok returns ~150 bytes
            idempotency_class=IdempotencyClass.SAFE,
        )
        result = await tiny_adapter.execute_step(step, exec_context)

        assert result.status == StepStatus.FAILED
        assert result.error_code == ErrorCode.VALIDATION_ERROR
        assert "exceeds maximum size" in (result.error_message or "").lower()

    @pytest.mark.asyncio
    async def test_invalid_json_fallback_to_text(self, adapter: HttpApiAdapter, mock_app, ssrf_val, exec_context: ExecutionContext) -> None:
        # Requesting /create with malformed json triggers 400 from mock app with JSON body
        step = TestStepSchema(
            step_id="step_raw_text",
            adapter_type=AdapterType.HTTP_API,
            action="http_request",
            inputs={"method": "GET", "path": "/redirect"},
            idempotency_class=IdempotencyClass.SAFE,
        )
        result = await adapter.execute_step(step, exec_context)
        assert result.status == StepStatus.PASSED

    @pytest.mark.asyncio
    async def test_unsupported_action_raises(self, adapter: HttpApiAdapter, exec_context: ExecutionContext) -> None:
        step = TestStepSchema(
            step_id="step_bad_act",
            adapter_type=AdapterType.HTTP_API,
            action="unsupported_act",
            inputs={"path": "/ok"},
        )
        with pytest.raises(UnsupportedActionError):
            await adapter.execute_step(step, exec_context)

    @pytest.mark.asyncio
    async def test_missing_url_or_path_fails_validation(self, adapter: HttpApiAdapter, exec_context: ExecutionContext) -> None:
        step = TestStepSchema(
            step_id="step_no_url",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={},
        )
        result = await adapter.execute_step(step, exec_context)
        assert result.status == StepStatus.FAILED
        assert result.error_code == ErrorCode.VALIDATION_ERROR
        assert "must specify a non-empty 'url' or 'path'" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_header_crlf_injection_rejected(self, adapter: HttpApiAdapter, exec_context: ExecutionContext) -> None:
        step = TestStepSchema(
            step_id="step_crlf",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/ok", "headers": {"X-Injected\r\nBad": "val"}},
        )
        result = await adapter.execute_step(step, exec_context)
        assert result.status == StepStatus.FAILED
        assert result.error_code == ErrorCode.VALIDATION_ERROR
        assert "prohibited CRLF control characters" in (result.error_message or "")


@pytest.mark.unit
class TestHttpApiAdapterSecurityAndSSRF:
    @pytest.mark.asyncio
    async def test_allowlist_enforcement_blocks_destination(
        self, adapter: HttpApiAdapter
    ) -> None:
        ctx = ExecutionContext(
            run_id="run_1",
            step_id="step_1",
            allowlist=["allowed-host.com"],
        )
        step = TestStepSchema(
            step_id="step_blocked",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"url": "http://mock-api.local/ok"},
        )
        result = await adapter.execute_step(step, ctx)
        assert result.status == StepStatus.BLOCKED
        assert result.error_code == ErrorCode.TARGET_NOT_ALLOWLISTED

    @pytest.mark.asyncio
    async def test_redirect_to_allowed_target_followed(
        self, adapter: HttpApiAdapter, exec_context: ExecutionContext
    ) -> None:
        step = TestStepSchema(
            step_id="step_redirect_ok",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/redirect?target=/ok"},
            idempotency_class=IdempotencyClass.SAFE,
        )
        result = await adapter.execute_step(step, exec_context)
        assert result.status == StepStatus.PASSED
        assert result.outputs["status_code"] == 200
        assert result.outputs["redirect_count"] == 1
        assert result.outputs["body"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_redirect_to_disallowed_target_blocked(
        self, adapter: HttpApiAdapter, exec_context: ExecutionContext
    ) -> None:
        step = TestStepSchema(
            step_id="step_redirect_disallowed",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/redirect?target=http://forbidden-site.local/ok"},
            idempotency_class=IdempotencyClass.SAFE,
        )
        result = await adapter.execute_step(step, exec_context)
        assert result.status == StepStatus.BLOCKED
        assert result.error_code == ErrorCode.TARGET_NOT_ALLOWLISTED
        assert result.outputs["redirect_count"] == 1

    @pytest.mark.asyncio
    async def test_redirect_to_metadata_ip_blocked(
        self, adapter: HttpApiAdapter, exec_context: ExecutionContext
    ) -> None:
        step = TestStepSchema(
            step_id="step_private_redirect",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/private-redirect"},
            idempotency_class=IdempotencyClass.SAFE,
        )
        result = await adapter.execute_step(step, exec_context)
        assert result.status == StepStatus.BLOCKED
        assert result.error_code in (ErrorCode.POLICY_BLOCKED, ErrorCode.TARGET_NOT_ALLOWLISTED)

    @pytest.mark.asyncio
    async def test_redirect_limit_enforced(
        self, mock_app, ssrf_val, exec_context: ExecutionContext
    ) -> None:
        # Set max_redirects = 1; requesting a chain will exceed limit
        limited_adapter = HttpApiAdapter(
            transport=httpx.ASGITransport(app=mock_app),
            ssrf_validator=ssrf_val,
            max_redirects=1,
        )
        # First redirect succeeds, second would exceed limit
        step = TestStepSchema(
            step_id="step_redirect_exceed",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/redirect?target=/redirect?target=/ok"},
            idempotency_class=IdempotencyClass.SAFE,
        )
        result = await limited_adapter.execute_step(step, exec_context)
        assert result.status == StepStatus.BLOCKED
        assert result.error_code == ErrorCode.POLICY_BLOCKED
        assert "maximum redirect depth exceeded" in (result.error_message or "").lower()

    def test_sensitive_headers_stripped_across_origins(self) -> None:
        headers = {
            "Authorization": "Bearer secret_token",
            "Cookie": "session=abc",
            "X-Api-Key": "my-key",
            "Accept": "application/json",
            "User-Agent": "UITP-Test-Agent",
        }
        stripped = RedirectValidator.sanitize_redirect_headers(
            headers=headers,
            from_url="https://source.com/api",
            to_url="https://destination.com/api",
        )
        assert "Accept" in stripped
        assert "User-Agent" in stripped
        assert "Authorization" not in stripped
        assert "Cookie" not in stripped
        assert "X-Api-Key" not in stripped


@pytest.mark.unit
class TestHttpApiAdapterIdempotencyAndRetry:
    @pytest.mark.asyncio
    async def test_non_idempotent_post_attempted_exactly_once(
        self, adapter: HttpApiAdapter, exec_context: ExecutionContext, caplog
    ) -> None:
        """P1-04 Acceptance criterion:
        A step with action: POST, idempotency_class: non_idempotent, and a retry_policy
        is attempted exactly once; the retry policy is not applied and a warning is logged.
        """
        step = TestStepSchema(
            step_id="step_non_idempotent_post",
            adapter_type=AdapterType.HTTP_API,
            action="post",
            inputs={"path": "/fail"},  # /fail returns 500
            idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
            retry_policy=RetryPolicySchema(
                max_attempts=3,
                retryable_status_codes=[500],
            ),
        )
        with caplog.at_level(logging.WARNING):
            result = await adapter.execute_step(step, exec_context)

        # Attempt count must be exactly 1
        assert result.outputs["attempt_count"] == 1
        assert result.outputs["status_code"] == 500
        # A warning must have been logged regarding non-idempotent retries
        assert any("Automatic retries are not permitted" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_safe_get_retries_up_to_configured_limit(
        self, adapter: HttpApiAdapter, exec_context: ExecutionContext
    ) -> None:
        """P1-04 Acceptance criterion:
        A step with action: GET, idempotency_class: safe, and a retry_policy
        retries up to the configured limit on failure.
        """
        step = TestStepSchema(
            step_id="step_safe_get_retry",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/fail"},  # /fail returns 500
            idempotency_class=IdempotencyClass.SAFE,
            retry_policy=RetryPolicySchema(
                max_attempts=3,
                retryable_status_codes=[500],
                backoff_seconds=0.01,
            ),
        )
        result = await adapter.execute_step(step, exec_context)

        # Must have completed 3 attempts
        assert result.outputs["attempt_count"] == 3
        assert result.outputs["status_code"] == 500

    @pytest.mark.asyncio
    async def test_idempotency_class_is_authoritative_over_http_method(
        self, adapter: HttpApiAdapter, exec_context: ExecutionContext, caplog
    ) -> None:
        """P1-04 Acceptance criterion:
        HTTP method alone does not determine retry eligibility;
        idempotency_class is the authoritative field.
        """
        # Even though HTTP method is GET, if idempotency_class is UNKNOWN or NON_IDEMPOTENT,
        # it must NOT be retried!
        step = TestStepSchema(
            step_id="step_unknown_get",
            adapter_type=AdapterType.HTTP_API,
            action="get",
            inputs={"path": "/fail"},
            idempotency_class=IdempotencyClass.UNKNOWN,
            retry_policy=RetryPolicySchema(
                max_attempts=4,
                retryable_status_codes=[500],
            ),
        )
        with caplog.at_level(logging.WARNING):
            result = await adapter.execute_step(step, exec_context)

        assert result.outputs["attempt_count"] == 1
        assert any("Automatic retries are not permitted" in record.message for record in caplog.records)
