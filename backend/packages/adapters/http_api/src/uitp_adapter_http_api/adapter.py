"""
uitp_adapter_http_api.adapter
=============================
HTTP API testing adapter for the Universal Autonomous AI Testing Platform (Task P1-04).

Design and Security Constraints:
  - Strict application-layer SSRF policy and target allowlist enforcement (P1-02).
  - Manual redirect resolution with per-redirect SSRF and allowlist re-validation.
  - Stripping sensitive credentials and cookies on cross-origin redirects.
  - Deterministic idempotency enforcement: non-idempotent steps are never automatically retried.
  - Bounded request/response payload size, bounded redirect count, and execution timeouts.
  - Safe logging and response representation without credential or secret leakage.
  - Independent of FastAPI routers, SQLAlchemy models, and Playwright.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

from uitp_adapter_base.base import BaseAdapter, ExecutionContext, StepExecutionResult
from uitp_contracts.adapter import AdapterCapabilities
from uitp_contracts.enums import (
    AdapterType,
    ErrorCode,
    IdempotencyClass,
    StepStatus,
)
from uitp_contracts.plan import TestStepSchema
from uitp_policy.exceptions import (
    InvalidTargetUrlError,
    PolicyBlockedError,
    TargetNotAllowlistedError,
)
from uitp_policy.redirect import RedirectValidator
from uitp_policy.validator import SSRFValidator
from uitp_adapter_http_api.assertions import HttpAssertionEngine

logger = logging.getLogger(__name__)

# Control characters prohibited in header names and values to prevent CRLF injection
CRLF_PATTERN = re.compile(r"[\r\n\x00]")

# Default limits
DEFAULT_MAX_REDIRECTS = 5
DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_MAX_REQUEST_BYTES = 5 * 1024 * 1024   # 5 MB

# Supported methods
VALID_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
)


HTTP_TRANSPORT_ERRORS = (
    (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError)
    if httpx is not None
    else ()
)


class HttpApiAdapter(BaseAdapter):
    """Adapter for executing secure, bounded HTTP/REST API test steps."""

    ADAPTER_ID = "http_api_v1"
    VERSION = "0.1.0"
    SUPPORTED_ACTIONS = [
        "http_request",
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
    ]
    LIMITATIONS = [
        "No WebSocket support in v1",
        "Target URLs must strictly pass SSRF allowlist policy (P1-02)",
        "Application-layer SSRF validation only; network egress policy required in container worker (P3-07)",
    ]

    def __init__(
        self,
        *,
        max_redirects: int = DEFAULT_MAX_REDIRECTS,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
        ssrf_validator: SSRFValidator | None = None,
        redirect_validator: RedirectValidator | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        assertion_engine: HttpAssertionEngine | None = None,
    ) -> None:
        self.max_redirects = max_redirects
        self.max_response_bytes = max_response_bytes
        self.max_request_bytes = max_request_bytes
        self.ssrf_validator: SSRFValidator = ssrf_validator or SSRFValidator()
        self.redirect_validator: RedirectValidator = (
            redirect_validator
            or RedirectValidator(validator=self.ssrf_validator, max_redirects=self.max_redirects)
        )
        self.transport = transport
        self.assertion_engine: HttpAssertionEngine = assertion_engine or HttpAssertionEngine()

    def metadata(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_id=self.ADAPTER_ID,
            adapter_type=AdapterType.HTTP_API,
            version=self.VERSION,
            supported_actions=list(self.SUPPORTED_ACTIONS),
            limitations=list(self.LIMITATIONS),
            conformance_version="1.0",
        )

    async def health_check(self) -> bool:
        """Readiness check. Confirms HTTPX dependency is operational."""
        return httpx is not None

    async def execute_step(
        self,
        step: TestStepSchema,
        context: ExecutionContext,
    ) -> StepExecutionResult:
        """Execute a single bounded HTTP step with full security controls."""
        # 1. Action validation
        self.validate_step(step)

        # 2. Method resolution
        method = self._resolve_method(step)
        if method not in VALID_HTTP_METHODS:
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message=f"Unsupported HTTP method: {method}",
            )

        # 3. URL construction & resolution
        target_url, err_res = self._resolve_url(step, context)
        if err_res is not None:
            return err_res

        # 4. Header validation (CRLF injection prevention)
        headers, err_res = self._validate_headers(step.inputs.get("headers"))
        if err_res is not None:
            return err_res

        # 5. Request body construction & size bounding
        body_kwargs, err_res = self._prepare_body(step.inputs)
        if err_res is not None:
            return err_res

        params = step.inputs.get("query_params") or step.inputs.get("params")

        # 6. SSRF & Allowlist Pre-Validation Gate
        allowlist = context.allowlist or step.inputs.get("allowlist")
        try:
            await self.ssrf_validator.validate_url(target_url, allowlist=allowlist)
        except TargetNotAllowlistedError as e:
            logger.warning("Target not allowlisted for step %s: %s", step.step_id, e)
            return StepExecutionResult(
                status=StepStatus.BLOCKED,
                error_code=ErrorCode.TARGET_NOT_ALLOWLISTED,
                error_message=str(e),
                outputs={"url": target_url, "attempt_count": 0},
            )
        except PolicyBlockedError as e:
            logger.warning("SSRF policy blocked target for step %s: %s", step.step_id, e)
            return StepExecutionResult(
                status=StepStatus.BLOCKED,
                error_code=ErrorCode.POLICY_BLOCKED,
                error_message=str(e),
                outputs={"url": target_url, "attempt_count": 0},
            )
        except InvalidTargetUrlError as e:
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message=str(e),
                outputs={"url": target_url, "attempt_count": 0},
            )

        # 7. Idempotency & Retry Eligibility
        max_attempts, retryable_status_codes, backoff_seconds = (
            self._determine_retry_rules(step)
        )

        # 8. Timeout bounding
        timeout = min(step.timeout_seconds, context.timeout_seconds)
        deadline = time.monotonic() + timeout

        # 9. Execution & Retry Loop
        attempt_count = 0
        last_error_message: str | None = None
        last_error_code: ErrorCode | str | None = None

        for attempt in range(1, max_attempts + 1):
            attempt_count = attempt
            remaining_time = deadline - time.monotonic()
            if remaining_time <= 0:
                return StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=f"Step execution timed out after {timeout:.1f}s",
                    outputs={"url": target_url, "attempt_count": attempt_count},
                )

            attempt_timeout = min(remaining_time, timeout)
            start_time = time.monotonic()

            try:
                result = await self._execute_http_attempt(
                    method=method,
                    target_url=target_url,
                    headers=headers,
                    params=params,
                    body_kwargs=body_kwargs,
                    timeout=attempt_timeout,
                    allowlist=allowlist,
                    attempt=attempt,
                )

                if result.status == StepStatus.BLOCKED:
                    # Policy violations are terminal and never retried
                    return result

                # Check if status code is in explicit retryable list
                status_code = result.outputs.get("status_code")
                if (
                    status_code in retryable_status_codes
                    and attempt < max_attempts
                ):
                    wait_time = min(backoff_seconds, max(0.0, deadline - time.monotonic()))
                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                    continue

                if result.status == StepStatus.PASSED and step.assertions:
                    outcome = self.assertion_engine.evaluate(result.outputs, step.assertions)
                    updated_outputs = dict(result.outputs)
                    updated_outputs["assertions"] = outcome.model_dump()
                    if not outcome.passed:
                        return StepExecutionResult(
                            status=StepStatus.FAILED,
                            error_code=ErrorCode.ASSERTION_FAILED,
                            error_message="; ".join(outcome.failure_reasons),
                            outputs=updated_outputs,
                        )
                    return StepExecutionResult(
                        status=StepStatus.PASSED,
                        outputs=updated_outputs,
                    )

                return result

            except HTTP_TRANSPORT_ERRORS as exc:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                last_error_message = f"Transport error during {method} {target_url}: {type(exc).__name__}"
                last_error_code = ErrorCode.VALIDATION_ERROR
                logger.warning(
                    "HTTP attempt %d failed for step %s: %s (%.1fms)",
                    attempt,
                    step.step_id,
                    last_error_message,
                    elapsed_ms,
                )

                if attempt < max_attempts:
                    wait_time = min(backoff_seconds, max(0.0, deadline - time.monotonic()))
                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                    continue
                else:
                    return StepExecutionResult(
                        status=StepStatus.FAILED,
                        error_code=last_error_code,
                        error_message=last_error_message,
                        outputs={"url": target_url, "attempt_count": attempt_count},
                    )

        return StepExecutionResult(
            status=StepStatus.FAILED,
            error_code=last_error_code or ErrorCode.VALIDATION_ERROR,
            error_message=last_error_message or "Execution failed after maximum attempts",
            outputs={"url": target_url, "attempt_count": attempt_count},
        )

    # -----------------------------------------------------------------------
    # Helper methods
    # -----------------------------------------------------------------------

    def _resolve_method(self, step: TestStepSchema) -> str:
        if step.action.lower() == "http_request":
            return str(step.inputs.get("method", "GET")).upper()
        return step.action.upper()

    def _resolve_url(
        self,
        step: TestStepSchema,
        context: ExecutionContext,
    ) -> tuple[str, StepExecutionResult | None]:
        url_or_path = step.inputs.get("url") or step.inputs.get("path")
        if not url_or_path or not isinstance(url_or_path, str) or not url_or_path.strip():
            return "", StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message="Step inputs must specify a non-empty 'url' or 'path'.",
            )

        url_or_path = url_or_path.strip()
        if CRLF_PATTERN.search(url_or_path):
            return "", StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message="Target URL contains prohibited control characters.",
            )

        if url_or_path.startswith(("http://", "https://")):
            return url_or_path, None

        base_url = context.base_url or step.inputs.get("base_url")
        if not base_url or not isinstance(base_url, str):
            return "", StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message="Relative path provided without base_url in ExecutionContext or step inputs.",
            )

        base_url = base_url.strip()
        if not base_url.endswith("/"):
            base_url += "/"
        resolved = urljoin(base_url, url_or_path.lstrip("/"))
        return resolved, None

    def _validate_headers(
        self,
        raw_headers: Any,
    ) -> tuple[dict[str, str], StepExecutionResult | None]:
        if raw_headers is None:
            return {}, None

        if not isinstance(raw_headers, Mapping):
            return {}, StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message="Headers must be a key-value mapping.",
            )

        validated: dict[str, str] = {}
        for k, v in raw_headers.items():
            if not isinstance(k, str) or not isinstance(v, (str, int, float, bool)):
                return {}, StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=f"Header key and value must be strings: {k}",
                )
            k_str = str(k).strip()
            v_str = str(v).strip()
            if CRLF_PATTERN.search(k_str) or CRLF_PATTERN.search(v_str):
                return {}, StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=f"Header '{k_str}' contains prohibited CRLF control characters.",
                )
            validated[k_str] = v_str

        return validated, None

    def _prepare_body(
        self,
        inputs: Mapping[str, Any],
    ) -> tuple[dict[str, Any], StepExecutionResult | None]:
        json_data = inputs.get("json")
        body_data = inputs.get("body") if "body" in inputs else inputs.get("data")

        if json_data is not None and body_data is not None:
            return {}, StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message="Step inputs cannot contain both 'json' and 'body'/'data'.",
            )

        kwargs: dict[str, Any] = {}
        if json_data is not None:
            try:
                encoded = json.dumps(json_data)
                if len(encoded.encode("utf-8")) > self.max_request_bytes:
                    return {}, StepExecutionResult(
                        status=StepStatus.FAILED,
                        error_code=ErrorCode.VALIDATION_ERROR,
                        error_message=f"Request JSON body exceeds size limit of {self.max_request_bytes} bytes.",
                    )
            except (TypeError, ValueError) as e:
                return {}, StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=f"Request JSON body serialization failed: {e}",
                )
            kwargs["json"] = json_data

        elif body_data is not None:
            if isinstance(body_data, str):
                encoded_bytes = body_data.encode("utf-8")
            elif isinstance(body_data, bytes):
                encoded_bytes = body_data
            else:
                return {}, StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message="Request body must be a string or bytes.",
                )

            if len(encoded_bytes) > self.max_request_bytes:
                return {}, StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=f"Request body exceeds size limit of {self.max_request_bytes} bytes.",
                )
            kwargs["content"] = encoded_bytes

        return kwargs, None

    def _determine_retry_rules(
        self,
        step: TestStepSchema,
    ) -> tuple[int, set[int], float]:
        """Determine allowed attempts and backoff based on step idempotency_class.

        Authoritative rule:
          - SAFE and IDEMPOTENT steps may retry up to retry_policy.max_attempts.
          - NON_IDEMPOTENT and UNKNOWN steps are attempted exactly once.
            If retry_policy.max_attempts > 1, retries are disabled and a warning is logged.
        """
        retry_policy = step.retry_policy
        idempotency_class = step.idempotency_class

        is_eligible = idempotency_class in (
            IdempotencyClass.SAFE,
            IdempotencyClass.IDEMPOTENT,
        )

        if not is_eligible:
            if retry_policy.max_attempts > 1:
                logger.warning(
                    "Step '%s' has idempotency_class '%s' with retry_policy.max_attempts=%d. "
                    "Automatic retries are not permitted for non-idempotent steps; capping at 1 attempt.",
                    step.step_id,
                    idempotency_class,
                    retry_policy.max_attempts,
                )
            return 1, set(), 0.0

        max_attempts = max(1, retry_policy.max_attempts)
        retryable_codes = set(retry_policy.retryable_status_codes)
        backoff_seconds = max(0.0, retry_policy.backoff_seconds)
        return max_attempts, retryable_codes, backoff_seconds

    async def _execute_http_attempt(
        self,
        *,
        method: str,
        target_url: str,
        headers: dict[str, str],
        params: Any,
        body_kwargs: dict[str, Any],
        timeout: float,
        allowlist: Sequence[str] | None,
        attempt: int,
    ) -> StepExecutionResult:
        """Execute single attempt with manual redirect re-validation loop."""
        if httpx is None:
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message="HTTPX dependency is not installed on worker.",
                outputs={"url": target_url, "attempt_count": attempt},
            )

        current_url = target_url
        current_method = method
        current_headers = dict(headers)
        current_body_kwargs = dict(body_kwargs)
        redirect_history: list[str] = []

        start_time = time.monotonic()

        async with httpx.AsyncClient(
            transport=self.transport,
            timeout=timeout,
            follow_redirects=False,
        ) as client:
            while True:
                resp = await client.request(
                    current_method,
                    current_url,
                    headers=current_headers,
                    params=params if not redirect_history else None,
                    **current_body_kwargs,
                )

                if (
                    resp.status_code in (301, 302, 303, 307, 308)
                    and "location" in resp.headers
                ):
                    location = resp.headers["location"]
                    redirect_history.append(current_url)

                    # Re-validate redirect target through SSRF & allowlist gate
                    try:
                        next_url, _ = await self.redirect_validator.validate_redirect(
                            current_url=current_url,
                            redirect_location=location,
                            redirect_history=redirect_history,
                            allowlist=allowlist,
                        )
                    except PolicyBlockedError as e:
                        logger.warning("SSRF redirect blocked: %s -> %s (%s)", current_url, location, e)
                        return StepExecutionResult(
                            status=StepStatus.BLOCKED,
                            error_code=ErrorCode.POLICY_BLOCKED,
                            error_message=str(e),
                            outputs={
                                "url": current_url,
                                "redirect_target": location,
                                "redirect_count": len(redirect_history),
                                "attempt_count": attempt,
                            },
                        )
                    except TargetNotAllowlistedError as e:
                        logger.warning("Redirect target not allowlisted: %s -> %s (%s)", current_url, location, e)
                        return StepExecutionResult(
                            status=StepStatus.BLOCKED,
                            error_code=ErrorCode.TARGET_NOT_ALLOWLISTED,
                            error_message=str(e),
                            outputs={
                                "url": current_url,
                                "redirect_target": location,
                                "redirect_count": len(redirect_history),
                                "attempt_count": attempt,
                            },
                        )

                    # Sanitize sensitive headers across origins
                    current_headers = RedirectValidator.sanitize_redirect_headers(
                        current_headers, current_url, next_url
                    )

                    # RFC 7231 / 9110 redirect method semantics
                    if resp.status_code == 303 or (
                        resp.status_code in (301, 302) and current_method == "POST"
                    ):
                        current_method = "GET"
                        current_body_kwargs = {}

                    current_url = next_url
                    continue
                else:
                    break

            elapsed_ms = (time.monotonic() - start_time) * 1000

            # Bounded response size check
            content_length = resp.headers.get("content-length")
            if content_length and content_length.isdigit():
                if int(content_length) > self.max_response_bytes:
                    return StepExecutionResult(
                        status=StepStatus.FAILED,
                        error_code=ErrorCode.VALIDATION_ERROR,
                        error_message=f"Response Content-Length exceeds maximum size of {self.max_response_bytes} bytes",
                        outputs={"attempt_count": attempt, "url": str(resp.url)},
                    )

            body_bytes = resp.content
            if len(body_bytes) > self.max_response_bytes:
                return StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=f"Response body exceeds maximum size of {self.max_response_bytes} bytes",
                    outputs={"attempt_count": attempt, "url": str(resp.url)},
                )

            # Response body parsing
            content_type = resp.headers.get("content-type", "")
            parsed_body: Any = None
            if "application/json" in content_type.lower():
                try:
                    parsed_body = resp.json()
                except Exception:
                    # Invalid JSON: return raw text representation safely
                    parsed_body = resp.text
            else:
                parsed_body = resp.text

            outputs = {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": parsed_body,
                "content_type": content_type,
                "duration_ms": elapsed_ms,
                "attempt_count": attempt,
                "url": str(resp.url),
                "redirect_count": len(redirect_history),
            }

            return StepExecutionResult(
                status=StepStatus.PASSED,
                outputs=outputs,
            )
