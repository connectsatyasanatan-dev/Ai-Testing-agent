"""
uitp_adapter_web.assertions
===========================
Deterministic assertion engine for evaluating browser assertions against Playwright state (Task P2-03).

Supported assertion types:
  - url_contains: Page URL contains expected substring.
  - url_equals: Page URL exactly equals expected URL.
  - title_contains: Page title contains expected substring.
  - title_equals: Page title exactly equals expected string.
  - text_visible: Page displays specified text prominently.
  - element_visible: Element matching locator is visible in DOM within timeout.
  - element_hidden: Element matching locator is hidden or detached.
  - element_text_equals: Element's text content matches expected value.
  - element_value_equals: Input/select element's value matches expected value.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Sequence

from uitp_adapter_web.exceptions import InvalidLocatorError
from uitp_adapter_web.locators import LocatorResolver, LocatorSpec
from uitp_contracts.enums import AssertionType, ErrorCode
from uitp_contracts.plan import (
    AssertionResultSchema,
    AssertionSchema,
    StepAssertionsOutcome,
)

logger = logging.getLogger(__name__)

DEFAULT_ASSERTION_TIMEOUT_MS = 3000
MAX_ASSERTIONS_PER_STEP = 50


class WebAssertionEngine:
    """Evaluates declarative UI assertions against an active Playwright Page."""

    def __init__(self, default_timeout_ms: int = DEFAULT_ASSERTION_TIMEOUT_MS) -> None:
        self.default_timeout_ms = default_timeout_ms

    async def evaluate_all(
        self,
        page: Any,
        assertions: Sequence[AssertionSchema],
    ) -> StepAssertionsOutcome:
        """Evaluate all assertions in sequence and aggregate outcomes."""
        if not assertions:
            return StepAssertionsOutcome(
                passed=True,
                total_assertions=0,
                passed_count=0,
                failed_count=0,
                results=[],
                failure_reasons=[],
            )

        if len(assertions) > MAX_ASSERTIONS_PER_STEP:
            return StepAssertionsOutcome(
                passed=False,
                total_assertions=len(assertions),
                passed_count=0,
                failed_count=1,
                results=[
                    AssertionResultSchema(
                        type="count_limit",
                        passed=False,
                        error_code=ErrorCode.VALIDATION_ERROR,
                        message=f"Too many assertions per step: {len(assertions)} exceeds limit of {MAX_ASSERTIONS_PER_STEP}",
                    )
                ],
                failure_reasons=[f"Assertion count {len(assertions)} exceeded {MAX_ASSERTIONS_PER_STEP}"],
            )

        results: list[AssertionResultSchema] = []
        failure_reasons: list[str] = []

        for assertion in assertions:
            start_t = time.monotonic()
            res = await self._evaluate_single(page, assertion)
            duration_ms = (time.monotonic() - start_t) * 1000.0

            result_with_duration = AssertionResultSchema(
                type=res.type,
                passed=res.passed,
                message=res.message,
                expected=res.expected,
                actual=res.actual,
                path=res.path,
                error_code=res.error_code,
                duration_ms=round(duration_ms, 2),
            )
            results.append(result_with_duration)
            if not res.passed:
                reason = res.message or f"Assertion {res.type} failed"
                failure_reasons.append(reason)

        passed_count = sum(1 for r in results if r.passed)
        failed_count = sum(1 for r in results if not r.passed)

        return StepAssertionsOutcome(
            passed=(failed_count == 0),
            total_assertions=len(results),
            passed_count=passed_count,
            failed_count=failed_count,
            results=results,
            failure_reasons=failure_reasons,
        )

    async def _evaluate_single(
        self, page: Any, assertion: AssertionSchema
    ) -> AssertionResultSchema:
        atype = str(assertion.type).lower()

        try:
            if atype in (AssertionType.URL_CONTAINS.value, "url_contains"):
                return self._eval_url_contains(page, assertion)

            if atype in (AssertionType.URL_EQUALS.value, "url_equals"):
                return self._eval_url_equals(page, assertion)

            if atype in (AssertionType.TITLE_CONTAINS.value, "title_contains"):
                return await self._eval_title_contains(page, assertion)

            if atype in (AssertionType.TITLE_EQUALS.value, "title_equals"):
                return await self._eval_title_equals(page, assertion)

            if atype in (AssertionType.TEXT_VISIBLE.value, "text_visible"):
                return await self._eval_text_visible(page, assertion)

            if atype in (AssertionType.ELEMENT_VISIBLE.value, "element_visible"):
                return await self._eval_element_visible(page, assertion)

            if atype in (AssertionType.ELEMENT_HIDDEN.value, "element_hidden"):
                return await self._eval_element_hidden(page, assertion)

            if atype == "element_text_equals":
                return await self._eval_element_text_equals(page, assertion)

            if atype == "element_value_equals":
                return await self._eval_element_value_equals(page, assertion)

            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.UNSUPPORTED_ASSERTION,
                message=f"Unsupported web assertion type: '{assertion.type}'",
            )
        except Exception as exc:
            logger.exception("Unexpected error evaluating assertion %s: %s", assertion.type, exc)
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.ASSERTION_FAILED,
                message=f"Assertion evaluation error: {type(exc).__name__}: {exc}",
            )

    def _eval_url_contains(self, page: Any, assertion: AssertionSchema) -> AssertionResultSchema:
        expected = str(assertion.value or "")
        actual = page.url
        if expected in actual:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                expected=expected,
                actual=actual,
            )
        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            error_code=ErrorCode.ASSERTION_FAILED,
            expected=expected,
            actual=actual,
            message=f"Expected URL to contain '{expected}', but was '{actual}'",
        )

    def _eval_url_equals(self, page: Any, assertion: AssertionSchema) -> AssertionResultSchema:
        expected = str(assertion.value or "")
        actual = page.url
        # Normalization: match directly or with/without trailing slash
        if actual == expected or actual.rstrip("/") == expected.rstrip("/"):
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                expected=expected,
                actual=actual,
            )
        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            error_code=ErrorCode.ASSERTION_FAILED,
            expected=expected,
            actual=actual,
            message=f"Expected URL '{expected}', but was '{actual}'",
        )

    async def _eval_title_contains(self, page: Any, assertion: AssertionSchema) -> AssertionResultSchema:
        expected = str(assertion.value or "")
        actual = await page.title()
        if expected in actual:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                expected=expected,
                actual=actual,
            )
        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            error_code=ErrorCode.ASSERTION_FAILED,
            expected=expected,
            actual=actual,
            message=f"Expected title to contain '{expected}', but was '{actual}'",
        )

    async def _eval_title_equals(self, page: Any, assertion: AssertionSchema) -> AssertionResultSchema:
        expected = str(assertion.value or "")
        actual = await page.title()
        if actual == expected:
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                expected=expected,
                actual=actual,
            )
        return AssertionResultSchema(
            type=assertion.type,
            passed=False,
            error_code=ErrorCode.ASSERTION_FAILED,
            expected=expected,
            actual=actual,
            message=f"Expected title '{expected}', but was '{actual}'",
        )

    async def _eval_text_visible(self, page: Any, assertion: AssertionSchema) -> AssertionResultSchema:
        expected = str(assertion.value or "")
        if not expected:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="text_visible requires a non-empty string value",
            )

        locator = page.get_by_text(expected)
        try:
            # Bounded wait for element to become visible
            await locator.first.wait_for(state="visible", timeout=self.default_timeout_ms)
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                expected=expected,
                actual=expected,
            )
        except Exception:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.ASSERTION_FAILED,
                expected=expected,
                message=f"Text '{expected}' is not visible on page within {self.default_timeout_ms}ms",
            )

    async def _eval_element_visible(self, page: Any, assertion: AssertionSchema) -> AssertionResultSchema:
        selector_or_path = str(assertion.path or assertion.value or "")
        if not selector_or_path:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="element_visible requires a target selector via 'path' or 'value'",
            )

        try:
            spec = LocatorResolver.parse_spec({"selector": selector_or_path})
            locator = LocatorResolver.resolve(page, spec)
            await locator.first.wait_for(state="visible", timeout=self.default_timeout_ms)
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                path=selector_or_path,
                expected="visible",
                actual="visible",
            )
        except InvalidLocatorError as e:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=selector_or_path,
                error_code=ErrorCode.VALIDATION_ERROR,
                message=str(e),
            )
        except Exception:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=selector_or_path,
                error_code=ErrorCode.ASSERTION_FAILED,
                expected="visible",
                actual="not_visible",
                message=f"Element '{selector_or_path}' was not visible within {self.default_timeout_ms}ms",
            )

    async def _eval_element_hidden(self, page: Any, assertion: AssertionSchema) -> AssertionResultSchema:
        selector_or_path = str(assertion.path or assertion.value or "")
        if not selector_or_path:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="element_hidden requires a target selector via 'path' or 'value'",
            )

        try:
            spec = LocatorResolver.parse_spec({"selector": selector_or_path})
            locator = LocatorResolver.resolve(page, spec)
            await locator.first.wait_for(state="hidden", timeout=self.default_timeout_ms)
            return AssertionResultSchema(
                type=assertion.type,
                passed=True,
                path=selector_or_path,
                expected="hidden",
                actual="hidden",
            )
        except InvalidLocatorError as e:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=selector_or_path,
                error_code=ErrorCode.VALIDATION_ERROR,
                message=str(e),
            )
        except Exception:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=selector_or_path,
                error_code=ErrorCode.ASSERTION_FAILED,
                expected="hidden",
                actual="visible",
                message=f"Element '{selector_or_path}' remained visible",
            )

    async def _eval_element_text_equals(self, page: Any, assertion: AssertionSchema) -> AssertionResultSchema:
        selector = str(assertion.path or "")
        expected = str(assertion.value or "")
        if not selector:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="element_text_equals requires a 'path' selector",
            )

        try:
            spec = LocatorResolver.parse_spec({"selector": selector})
            locator = LocatorResolver.resolve(page, spec)
            actual = (await locator.first.text_content() or "").strip()
            if actual == expected.strip():
                return AssertionResultSchema(
                    type=assertion.type,
                    passed=True,
                    path=selector,
                    expected=expected,
                    actual=actual,
                )
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=selector,
                error_code=ErrorCode.ASSERTION_FAILED,
                expected=expected,
                actual=actual,
                message=f"Element text '{actual}' does not match expected '{expected}'",
            )
        except Exception as exc:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=selector,
                error_code=ErrorCode.ASSERTION_FAILED,
                message=f"Failed to read element text: {exc}",
            )

    async def _eval_element_value_equals(self, page: Any, assertion: AssertionSchema) -> AssertionResultSchema:
        selector = str(assertion.path or "")
        expected = str(assertion.value or "")
        if not selector:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                error_code=ErrorCode.VALIDATION_ERROR,
                message="element_value_equals requires a 'path' selector",
            )

        try:
            spec = LocatorResolver.parse_spec({"selector": selector})
            locator = LocatorResolver.resolve(page, spec)
            actual = await locator.first.input_value()
            if actual == expected:
                return AssertionResultSchema(
                    type=assertion.type,
                    passed=True,
                    path=selector,
                    expected=expected,
                    actual=actual,
                )
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=selector,
                error_code=ErrorCode.ASSERTION_FAILED,
                expected=expected,
                actual=actual,
                message=f"Element value '{actual}' does not match expected '{expected}'",
            )
        except Exception as exc:
            return AssertionResultSchema(
                type=assertion.type,
                passed=False,
                path=selector,
                error_code=ErrorCode.ASSERTION_FAILED,
                message=f"Failed to read element value: {exc}",
            )
