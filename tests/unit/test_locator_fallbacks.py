"""
tests/unit/test_locator_fallbacks.py
====================================
Comprehensive tests for UI Locator Fallbacks & Resilience (Task 7):
  1. Conservative fallback strategy parsing (ranked test_id -> role -> label -> placeholder -> text -> css).
  2. Strict rejection of ambiguous locators (AmbiguousLocatorError on count > 1) without blind clicking.
  3. Safe fallback resolution when primary locator is absent (count == 0) and fallback is uniquely matched.
  4. Preservation of post-action assertions: element recovery never masks failed business assertions.
  5. Failure diagnostics: failure screenshot capture and DOM snapshot sanitization (redacting passwords & secrets).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from uitp_adapter_base import ExecutionContext
from uitp_adapter_web import (
    AmbiguousLocatorError,
    ElementNotFoundError,
    InvalidLocatorError,
    LocatorResolver,
    LocatorSpec,
    PlaywrightAdapter,
)
from uitp_contracts.enums import (
    AdapterType,
    AssertionType,
    ErrorCode,
    StepStatus,
)
from uitp_contracts.plan import AssertionSchema, TestStepSchema


# ============================================================================
# 1. Parsing & Fallback Spec Extraction Tests
# ============================================================================


def test_parse_fallback_specs_from_explicit_list() -> None:
    inputs = {
        "test_id": "submit-btn",
        "fallbacks": [
            {"role": "button", "name": "Submit Form"},
            {"text": "Submit Form"},
            {"css": "button.submit-primary"},
            {"css": "extra-ignored"},  # Bounded to max 3 fallbacks
        ],
    }
    primary, fallbacks = LocatorResolver.parse_fallback_specs(inputs)
    assert primary.strategy == "test_id"
    assert primary.value == "submit-btn"
    assert len(fallbacks) == 3
    assert fallbacks[0].strategy == "role"
    assert fallbacks[1].strategy == "text"
    assert fallbacks[2].strategy == "css"


def test_parse_fallback_specs_from_top_level_keys() -> None:
    inputs = {
        "test_id": "username-input",
        "label": "Username or Email",
        "placeholder": "Enter username",
        "css": "input[name='user']",
    }
    primary, fallbacks = LocatorResolver.parse_fallback_specs(inputs)
    assert primary.strategy == "test_id"
    assert primary.value == "username-input"
    # Should extract top-level keys in ranked conservative order
    strategies = [fb.strategy for fb in fallbacks]
    assert "label" in strategies
    assert "placeholder" in strategies
    assert "css" in strategies


def test_parse_fallback_specs_ignores_invalid_strategy() -> None:
    inputs = {
        "css": "div.container",
        "fallbacks": [
            {"unsupported_strategy": "foo"},
            {"text": "Valid text"},
        ],
    }
    primary, fallbacks = LocatorResolver.parse_fallback_specs(inputs)
    assert primary.strategy == "css"
    assert len(fallbacks) == 1
    assert fallbacks[0].strategy == "text"


# ============================================================================
# 2. Locator Resolution & Ambiguity Tests
# ============================================================================


@pytest.mark.asyncio
async def test_resolve_primary_when_unique_match() -> None:
    mock_page = MagicMock()
    mock_locator = MagicMock()
    mock_locator.count = AsyncMock(return_value=1)
    mock_page.get_by_test_id = MagicMock(return_value=mock_locator)

    primary = LocatorSpec(strategy="test_id", value="unique-btn")
    loc, resolved_spec, is_fallback, fb_strategy = await LocatorResolver.resolve_with_fallback(mock_page, primary, [])

    assert not is_fallback
    assert resolved_spec.strategy == "test_id"
    assert loc is mock_locator
    assert fb_strategy is None


@pytest.mark.asyncio
async def test_resolve_rejects_ambiguous_primary_match() -> None:
    """Never click blindly when count > 1."""
    mock_page = MagicMock()
    mock_locator = MagicMock()
    mock_locator.count = AsyncMock(return_value=2)
    mock_page.locator = MagicMock(return_value=mock_locator)

    primary = LocatorSpec(strategy="css", value=".action-btn")
    fallback = LocatorSpec(strategy="text", value="Action")

    with pytest.raises(AmbiguousLocatorError) as exc_info:
        await LocatorResolver.resolve_with_fallback(mock_page, primary, [fallback])

    assert exc_info.value.match_count == 2
    assert "matched 2 elements" in str(exc_info.value)


@pytest.mark.asyncio
async def test_resolve_recovers_via_first_unique_fallback() -> None:
    mock_page = MagicMock()

    # Primary matches 0
    mock_primary_locator = MagicMock()
    mock_primary_locator.count = AsyncMock(return_value=0)
    mock_page.get_by_test_id = MagicMock(return_value=mock_primary_locator)

    # Fallback matches 1
    mock_fb_locator = MagicMock()
    mock_fb_locator.count = AsyncMock(return_value=1)
    mock_page.get_by_text = MagicMock(return_value=mock_fb_locator)

    primary = LocatorSpec(strategy="test_id", value="changed-btn")
    fallback = LocatorSpec(strategy="text", value="Submit")

    loc, resolved_spec, is_fallback, fb_strategy = await LocatorResolver.resolve_with_fallback(mock_page, primary, [fallback])
    assert is_fallback is True
    assert fb_strategy == "text"
    assert resolved_spec.strategy == "text"
    assert loc is mock_fb_locator


@pytest.mark.asyncio
async def test_resolve_skips_ambiguous_fallback_to_next_unique() -> None:
    mock_page = MagicMock()

    # Primary matches 0
    mock_primary = MagicMock()
    mock_primary.count = AsyncMock(return_value=0)
    mock_page.get_by_test_id = MagicMock(return_value=mock_primary)

    # Fallback 1 matches 3 (ambiguous, must skip)
    mock_fb1 = MagicMock()
    mock_fb1.count = AsyncMock(return_value=3)
    mock_page.locator = MagicMock(return_value=mock_fb1)

    # Fallback 2 matches 1 (unique, must accept)
    mock_fb2 = MagicMock()
    mock_fb2.count = AsyncMock(return_value=1)
    mock_page.get_by_text = MagicMock(return_value=mock_fb2)

    primary = LocatorSpec(strategy="test_id", value="btn")
    fb1 = LocatorSpec(strategy="css", value="button")
    fb2 = LocatorSpec(strategy="text", value="Save")

    loc, resolved_spec, is_fallback, fb_strategy = await LocatorResolver.resolve_with_fallback(mock_page, primary, [fb1, fb2])
    assert is_fallback is True
    assert fb_strategy == "text"
    assert resolved_spec.strategy == "text"
    assert loc is mock_fb2


@pytest.mark.asyncio
async def test_resolve_returns_primary_when_all_locators_exhausted() -> None:
    mock_page = MagicMock()

    mock_loc = MagicMock()
    mock_loc.count = AsyncMock(return_value=0)
    mock_page.get_by_test_id = MagicMock(return_value=mock_loc)
    mock_page.locator = MagicMock(return_value=mock_loc)

    primary = LocatorSpec(strategy="test_id", value="missing")
    fb = LocatorSpec(strategy="css", value="#missing")

    loc, resolved_spec, is_fallback, fb_strategy = await LocatorResolver.resolve_with_fallback(mock_page, primary, [fb])
    assert is_fallback is False
    assert fb_strategy is None
    assert resolved_spec.strategy == "test_id"
    assert loc is mock_loc


# ============================================================================
# 3. Adapter Action Recovery & Telemetry Tests
# ============================================================================


@pytest.mark.asyncio
async def test_playwright_adapter_records_fallback_telemetry() -> None:
    adapter = PlaywrightAdapter()
    mock_page = MagicMock()
    mock_page.goto = AsyncMock()
    mock_page.url = "http://127.0.0.1:8000/form"
    mock_page.title = AsyncMock(return_value="Form Page")

    # Primary missing, fallback matches 1
    mock_primary = MagicMock()
    mock_primary.count = AsyncMock(return_value=0)
    mock_page.get_by_test_id = MagicMock(return_value=mock_primary)

    mock_fb = MagicMock()
    mock_fb.count = AsyncMock(return_value=1)
    mock_fb.click = AsyncMock()
    mock_page.locator = MagicMock(return_value=mock_fb)

    # Attach mock session
    mock_router = MagicMock()
    mock_router.blocked_requests = []
    adapter.get_or_create_session = AsyncMock(return_value=MagicMock(page=mock_page, router=mock_router))

    step = TestStepSchema(
        step_id="step-fallback-click",
        task_id="task-1",
        adapter_type=AdapterType.WEB,
        action="click",
        inputs={
            "test_id": "old-btn-id",
            "fallbacks": [{"css": "button.submit"}],
        },
    )
    ctx = ExecutionContext(
        run_id="test-run",
        step_id="step-fallback-click",
        attempt_number=1,
        environment_id="env-1",
        base_url="http://127.0.0.1:8000",
        allowlist=["127.0.0.1"],
    )

    result = await adapter.execute_step(step, ctx)
    assert result.status == StepStatus.PASSED
    assert result.outputs.get("recovered_via_fallback") is True
    assert result.outputs.get("fallback_strategy") == "css"
    assert result.outputs.get("locator_strategy") == "css"
    mock_fb.click.assert_awaited_once()


@pytest.mark.asyncio
async def test_playwright_adapter_preserves_assertion_failure_on_recovery() -> None:
    """Even if locator recovers via fallback, a failing business assertion MUST fail the step."""
    adapter = PlaywrightAdapter()
    mock_page = MagicMock()
    mock_page.url = "http://127.0.0.1:8000/wrong-page"
    mock_page.title = AsyncMock(return_value="Wrong Page")

    mock_primary = MagicMock()
    mock_primary.count = AsyncMock(return_value=0)
    mock_page.get_by_test_id = MagicMock(return_value=mock_primary)

    mock_fb = MagicMock()
    mock_fb.count = AsyncMock(return_value=1)
    mock_fb.click = AsyncMock()
    mock_page.locator = MagicMock(return_value=mock_fb)

    mock_router = MagicMock()
    mock_router.blocked_requests = []
    adapter.get_or_create_session = AsyncMock(return_value=MagicMock(page=mock_page, router=mock_router))

    step = TestStepSchema(
        step_id="step-assert-fail",
        task_id="task-1",
        adapter_type=AdapterType.WEB,
        action="click",
        inputs={
            "test_id": "old-btn",
            "fallbacks": [{"css": "button.submit"}],
        },
        assertions=[
            AssertionSchema(
                type=AssertionType.URL_EQUALS,
                target="url",
                expected="http://127.0.0.1:8000/success",
            )
        ],
    )
    ctx = ExecutionContext(
        run_id="test-run-assert",
        step_id="step-assert-fail",
        attempt_number=1,
        environment_id="env-1",
        base_url="http://127.0.0.1:8000",
        allowlist=["127.0.0.1"],
    )

    result = await adapter.execute_step(step, ctx)
    assert result.status == StepStatus.FAILED
    assert result.error_code == ErrorCode.ASSERTION_FAILED
    # Locators did recover, but business assertion properly failed
    assert result.outputs.get("recovered_via_fallback") is True


# ============================================================================
# 4. Failure Diagnostics & DOM Sanitization Tests
# ============================================================================


@pytest.mark.asyncio
async def test_failure_diagnostics_sanitizes_passwords_and_secrets() -> None:
    adapter = PlaywrightAdapter()
    raw_html = """
    <html>
      <body>
        <input type="password" name="pwd" value="SuperSecretP@ssword!" />
        <input type="text" name="api_token" value="ey1234567890abcdef" />
        <input type="text" name="secret_key" value="sec-val-987" />
        <input type="text" name="username" value="valid_user" />
        <button id="submit">Login</button>
      </body>
    </html>
    """
    mock_page = MagicMock()
    mock_page.content = AsyncMock(return_value=raw_html)
    mock_page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\nfake-screenshot")

    diagnostics = await adapter.capture_failure_diagnostics(mock_page)

    assert "failure_screenshot_bytes" in diagnostics
    assert diagnostics["failure_screenshot_bytes"] == b"\x89PNG\r\n\x1a\nfake-screenshot"

    dom_snapshot = diagnostics.get("dom_snapshot", "")
    assert "SuperSecretP@ssword!" not in dom_snapshot
    assert "ey1234567890abcdef" not in dom_snapshot
    assert "sec-val-987" not in dom_snapshot
    assert "[REDACTED]" in dom_snapshot
    assert "valid_user" in dom_snapshot
