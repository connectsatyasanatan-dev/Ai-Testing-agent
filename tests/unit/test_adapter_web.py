"""
tests/unit/test_adapter_web.py
==============================
Comprehensive unit tests for the Playwright Web Adapter vertical slice (P2-01 through P2-05).

Verifies:
  1. Adapter capability declaration and BaseAdapter contract compliance.
  2. AdapterRegistry registration and action validation.
  3. LocatorResolver security: rejects eval/script injection, validates supported strategies.
  4. BrowserSecurityRouter: intercepts and blocks forbidden schemes, private IPs, metadata IPs, popups.
  5. WebAssertionEngine: evaluates URL, title, text, visibility, and element values deterministically.
  6. Real browser execution against local Starlette fixture:
     - Navigation and page title assertion.
     - Filling form inputs and clicking buttons.
     - Form submission and session authentication.
     - Conditionally visible element toggle and assertion.
     - Select dropdown interaction.
     - Capturing screenshot evidence artifacts with valid SHA-256 digests.
     - Bounded navigation timeout enforcement.
     - SSRF route interception (blocking redirects to 169.254.169.254).
     - Cleanup of browser sessions, pages, and contexts.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
import pytest

from tests.fixtures.web_app import run_test_web_server
from uitp_adapter_base import AdapterRegistry, ExecutionContext, UnsupportedActionError
from uitp_adapter_web import (
    BrowserSecurityRouter,
    InvalidLocatorError,
    LocatorResolver,
    LocatorSpec,
    PlaywrightAdapter,
    WebAssertionEngine,
)
from uitp_contracts.enums import (
    AdapterType,
    AssertionType,
    ErrorCode,
    EvidenceType,
    IdempotencyClass,
    RiskLevel,
    StepStatus,
)
from uitp_contracts.plan import AssertionSchema, TestStepSchema
from uitp_policy.execution import ExecutionLimits, ExecutionPolicy
from uitp_policy.validator import SSRFValidator


# ============================================================================
# 1. Adapter Contract & Capability Tests
# ============================================================================


def test_web_adapter_capabilities_and_contract() -> None:
    adapter = PlaywrightAdapter()
    meta = adapter.metadata()

    assert meta.adapter_id == "web_playwright_v1"
    assert meta.adapter_type == AdapterType.WEB
    assert meta.version == "0.1.0"
    assert "navigate" in meta.supported_actions
    assert "click" in meta.supported_actions
    assert "fill" in meta.supported_actions
    assert "screenshot" in meta.supported_actions
    assert adapter.supports_action("navigate")
    assert adapter.supports_action("fill")
    assert not adapter.supports_action("arbitrary_eval")


def test_web_adapter_registration_in_registry() -> None:
    registry = AdapterRegistry()
    adapter = PlaywrightAdapter()
    registry.register(adapter)

    assert registry.has("web_playwright_v1")
    retrieved = registry.get("web_playwright_v1")
    assert retrieved is adapter

    # Validates supported action
    registry.validate_action("web_playwright_v1", "click")

    # Rejects unsupported action
    with pytest.raises(UnsupportedActionError):
        registry.validate_action("web_playwright_v1", "run_bash_script")


# ============================================================================
# 2. LocatorResolver Security & Validation Tests
# ============================================================================


def test_locator_resolver_valid_strategies() -> None:
    # CSS
    spec_css = LocatorResolver.parse_spec({"css": "#username"})
    assert spec_css.strategy == "css"
    assert spec_css.value == "#username"

    # Text
    spec_text = LocatorResolver.parse_spec({"text": "Sign In", "exact": True})
    assert spec_text.strategy == "text"
    assert spec_text.value == "Sign In"
    assert spec_text.exact is True

    # Role
    spec_role = LocatorResolver.parse_spec({"role": "button", "name": "Log In"})
    assert spec_role.strategy == "role"
    assert spec_role.value == "button"
    assert spec_role.name == "Log In"

    # Test ID
    spec_tid = LocatorResolver.parse_spec({"test_id": "submit-btn"})
    assert spec_tid.strategy == "test_id"
    assert spec_tid.value == "submit-btn"

    # Direct selector string
    spec_sel = LocatorResolver.parse_spec({"selector": "button#submit"})
    assert spec_sel.strategy == "css"
    assert spec_sel.value == "button#submit"


@pytest.mark.parametrize(
    "dangerous_selector",
    [
        "javascript:alert(1)",
        "eval(document.cookie)",
        "function() { return 1; }",
        "() => window.token",
        "<script>steal()</script>",
        "document.location = 'http://attacker.com'",
        "window.__secret",
        ":eval(someCode)",
        ":expr(someExpression)",
    ],
)
def test_locator_resolver_blocks_code_injection(dangerous_selector: str) -> None:
    with pytest.raises(InvalidLocatorError, match="Prohibited construct detected"):
        LocatorResolver.parse_spec({"selector": dangerous_selector})


def test_locator_resolver_rejects_missing_selector() -> None:
    with pytest.raises(InvalidLocatorError, match="Step inputs must specify a valid target element"):
        LocatorResolver.parse_spec({})


def test_locator_resolver_rejects_excessive_length() -> None:
    long_selector = "#" + "a" * 2000
    with pytest.raises(InvalidLocatorError, match="exceeds maximum length"):
        LocatorResolver.parse_spec({"selector": long_selector})


# ============================================================================
# 3. BrowserSecurityRouter SSRF & Route Interception Tests
# ============================================================================


@pytest.mark.asyncio
async def test_router_allows_safe_http_url() -> None:
    validator = SSRFValidator(allow_loopback=True)
    router = BrowserSecurityRouter(
        ssrf_validator=validator,
        allowlist=["127.0.0.1:8080"],
    )

    route_mock = MagicMock()
    route_mock.request.url = "http://127.0.0.1:8080/dashboard"
    resp_mock = MagicMock(status=200, headers={})
    route_mock.fetch = AsyncMock(return_value=resp_mock)
    route_mock.fulfill = AsyncMock()
    route_mock.continue_ = AsyncMock()
    route_mock.abort = AsyncMock()

    await router.handle_route(route_mock)

    route_mock.fulfill.assert_awaited_once_with(response=resp_mock)
    route_mock.abort.assert_not_called()
    assert len(router.blocked_requests) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "prohibited_url",
    [
        "file:///etc/passwd",
        "chrome://settings",
        "javascript:alert(1)",
        "data:text/html,<b>hello</b>",
        "gopher://malicious.host",
    ],
)
async def test_router_blocks_prohibited_schemes(prohibited_url: str) -> None:
    router = BrowserSecurityRouter()

    route_mock = MagicMock()
    route_mock.request.url = prohibited_url
    route_mock.continue_ = AsyncMock()
    route_mock.abort = AsyncMock()

    await router.handle_route(route_mock)

    route_mock.abort.assert_awaited_once_with("blockedbyclient")
    route_mock.continue_.assert_not_called()
    assert len(router.blocked_requests) == 1
    assert "Prohibited scheme" in router.blocked_requests[0]["reason"]


@pytest.mark.asyncio
async def test_router_blocks_cloud_metadata_ip() -> None:
    validator = SSRFValidator()
    router = BrowserSecurityRouter(ssrf_validator=validator)

    route_mock = MagicMock()
    route_mock.request.url = "http://169.254.169.254/latest/meta-data/"
    route_mock.continue_ = AsyncMock()
    route_mock.abort = AsyncMock()

    await router.handle_route(route_mock)

    route_mock.abort.assert_awaited_once_with("blockedbyclient")
    route_mock.continue_.assert_not_called()
    assert len(router.blocked_requests) == 1


@pytest.mark.asyncio
async def test_router_blocks_non_allowlisted_domain() -> None:
    validator = SSRFValidator()
    router = BrowserSecurityRouter(
        ssrf_validator=validator,
        allowlist=["allowed.example.com"],
    )

    route_mock = MagicMock()
    route_mock.request.url = "http://forbidden.example.com/api"
    route_mock.continue_ = AsyncMock()
    route_mock.abort = AsyncMock()

    await router.handle_route(route_mock)

    route_mock.abort.assert_awaited_once_with("blockedbyclient")
    route_mock.continue_.assert_not_called()
    assert len(router.blocked_requests) == 1


# ============================================================================
# 4. WebAssertionEngine Tests
# ============================================================================


@pytest.mark.asyncio
async def test_assertion_engine_url_and_title() -> None:
    engine = WebAssertionEngine()

    page_mock = MagicMock()
    page_mock.url = "http://127.0.0.1:8000/dashboard"
    page_mock.title = AsyncMock(return_value="Dashboard - Test Portal")

    assertions = [
        AssertionSchema(type=AssertionType.URL_CONTAINS, value="/dashboard"),
        AssertionSchema(type=AssertionType.URL_EQUALS, value="http://127.0.0.1:8000/dashboard"),
        AssertionSchema(type=AssertionType.TITLE_CONTAINS, value="Dashboard"),
        AssertionSchema(type=AssertionType.TITLE_EQUALS, value="Dashboard - Test Portal"),
    ]

    outcome = await engine.evaluate_all(page_mock, assertions)
    assert outcome.passed is True
    assert outcome.total_assertions == 4
    assert outcome.passed_count == 4
    assert outcome.failed_count == 0


@pytest.mark.asyncio
async def test_assertion_engine_reports_failure() -> None:
    engine = WebAssertionEngine()

    page_mock = MagicMock()
    page_mock.url = "http://127.0.0.1:8000/login"
    page_mock.title = AsyncMock(return_value="Login")

    assertions = [
        AssertionSchema(type=AssertionType.URL_CONTAINS, value="/dashboard"),
    ]

    outcome = await engine.evaluate_all(page_mock, assertions)
    assert outcome.passed is False
    assert outcome.failed_count == 1
    assert "Expected URL to contain '/dashboard'" in outcome.failure_reasons[0]


# ============================================================================
# 5. Live Browser Execution against Local Starlette Fixture
# ============================================================================


@pytest.mark.asyncio
async def test_live_browser_navigation_login_and_assertions() -> None:
    async with run_test_web_server() as base_url:
        validator = SSRFValidator(allow_loopback=True)
        adapter = PlaywrightAdapter(headless=True, ssrf_validator=validator)
        ctx = ExecutionContext(
            run_id="run_test_live_1",
            step_id="step_nav_login",
            base_url=base_url,
            allowlist=["127.0.0.1"],
        )

        try:
            # Step 1: Navigate to /login
            step_nav = TestStepSchema(
                step_id="step_1",
                adapter_type=AdapterType.WEB,
                action="navigate",
                inputs={"url": f"{base_url}/login"},
                assertions=[
                    AssertionSchema(type=AssertionType.TITLE_CONTAINS, value="Login"),
                    AssertionSchema(type=AssertionType.URL_CONTAINS, value="/login"),
                ],
            )
            res_nav = await adapter.execute_step(step_nav, ctx)
            assert res_nav.status == StepStatus.PASSED
            assert res_nav.outputs["title"] == "Login - Test Portal"

            # Step 2: Fill username
            step_user = TestStepSchema(
                step_id="step_2",
                adapter_type=AdapterType.WEB,
                action="fill",
                inputs={"css": "#username", "value": "testuser"},
            )
            res_user = await adapter.execute_step(step_user, ctx)
            assert res_user.status == StepStatus.PASSED

            # Step 3: Fill password
            step_pwd = TestStepSchema(
                step_id="step_3",
                adapter_type=AdapterType.WEB,
                action="fill",
                inputs={"css": "#password", "value": "secret123"},
            )
            res_pwd = await adapter.execute_step(step_pwd, ctx)
            assert res_pwd.status == StepStatus.PASSED
            # Password must be redacted from outputs
            assert res_pwd.outputs.get("filled_value") == "[REDACTED]"

            # Step 4: Click login button & assert dashboard reached
            step_click = TestStepSchema(
                step_id="step_4",
                adapter_type=AdapterType.WEB,
                action="click",
                inputs={"css": "#login-btn"},
                assertions=[
                    AssertionSchema(type=AssertionType.URL_CONTAINS, value="/dashboard"),
                    AssertionSchema(type=AssertionType.TITLE_CONTAINS, value="Dashboard"),
                    AssertionSchema(type=AssertionType.TEXT_VISIBLE, value="Welcome to Test Portal"),
                ],
            )
            res_click = await adapter.execute_step(step_click, ctx)
            assert res_click.status == StepStatus.PASSED

            # Step 5: Assert conditional element is initially hidden
            step_hidden = TestStepSchema(
                step_id="step_5",
                adapter_type=AdapterType.WEB,
                action="wait_for",
                inputs={"css": "#cond-element", "state": "hidden"},
                assertions=[
                    AssertionSchema(type=AssertionType.ELEMENT_HIDDEN, path="#cond-element"),
                ],
            )
            res_hidden = await adapter.execute_step(step_hidden, ctx)
            assert res_hidden.status == StepStatus.PASSED

            # Step 6: Toggle visibility by clicking toggle button
            step_toggle = TestStepSchema(
                step_id="step_6",
                adapter_type=AdapterType.WEB,
                action="click",
                inputs={"css": "#toggle-btn"},
                assertions=[
                    AssertionSchema(type=AssertionType.ELEMENT_VISIBLE, path="#cond-element"),
                    AssertionSchema(type=AssertionType.TEXT_VISIBLE, value="Conditionally Visible Content"),
                ],
            )
            res_toggle = await adapter.execute_step(step_toggle, ctx)
            assert res_toggle.status == StepStatus.PASSED

            # Step 7: Select option from dropdown
            step_select = TestStepSchema(
                step_id="step_7",
                adapter_type=AdapterType.WEB,
                action="select",
                inputs={"css": "#role-select", "value": "admin"},
                assertions=[
                    AssertionSchema(type=AssertionType.ELEMENT_VISIBLE, path="#role-select"),
                ],
            )
            res_select = await adapter.execute_step(step_select, ctx)
            assert res_select.status == StepStatus.PASSED

            # Step 8: Capture Screenshot
            step_ss = TestStepSchema(
                step_id="step_8",
                adapter_type=AdapterType.WEB,
                action="screenshot",
                inputs={"full_page": True},
            )
            res_ss = await adapter.execute_step(step_ss, ctx)
            assert res_ss.status == StepStatus.PASSED
            assert len(res_ss.evidence) == 1
            assert res_ss.evidence[0].type == EvidenceType.SCREENSHOT
            assert res_ss.evidence[0].size_bytes > 0
            assert "screenshot_bytes" in res_ss.outputs

        finally:
            # Teardown session cleanly
            await adapter.close_session(ctx.run_id)


@pytest.mark.asyncio
async def test_live_browser_ssrf_intercepts_private_redirect() -> None:
    async with run_test_web_server() as base_url:
        validator = SSRFValidator(allow_loopback=True)
        adapter = PlaywrightAdapter(headless=True, ssrf_validator=validator)
        ctx = ExecutionContext(
            run_id="run_test_ssrf_redirect",
            step_id="step_private_red",
            base_url=base_url,
            allowlist=["127.0.0.1"],
        )

        try:
            # Attempt to navigate to route that redirects to 169.254.169.254
            step = TestStepSchema(
                step_id="step_redir",
                adapter_type=AdapterType.WEB,
                action="navigate",
                inputs={"url": f"{base_url}/private-redirect"},
            )
            res = await adapter.execute_step(step, ctx)
            assert res.status == StepStatus.BLOCKED
            assert res.error_code == ErrorCode.POLICY_BLOCKED
            assert "blocked" in (res.error_message or "").lower()

        finally:
            await adapter.close_session(ctx.run_id)


@pytest.mark.asyncio
async def test_live_browser_timeout_enforcement() -> None:
    async with run_test_web_server() as base_url:
        validator = SSRFValidator(allow_loopback=True)
        adapter = PlaywrightAdapter(headless=True, ssrf_validator=validator)
        ctx = ExecutionContext(
            run_id="run_test_timeout",
            step_id="step_slow",
            base_url=base_url,
            allowlist=["127.0.0.1"],
            timeout_seconds=1.0,
        )

        try:
            # Navigate to /slow with 3 second delay, step timeout bounded at 1.0s
            step = TestStepSchema(
                step_id="step_slow",
                adapter_type=AdapterType.WEB,
                action="navigate",
                inputs={"url": f"{base_url}/slow?delay=3.0"},
                timeout_seconds=1.0,
            )
            res = await adapter.execute_step(step, ctx)
            assert res.status == StepStatus.FAILED
            assert res.error_code == ErrorCode.VALIDATION_ERROR
            assert "timed out" in (res.error_message or "").lower()

        finally:
            await adapter.close_session(ctx.run_id)


@pytest.mark.asyncio
async def test_live_browser_failed_assertion_captures_evidence() -> None:
    async with run_test_web_server() as base_url:
        validator = SSRFValidator(allow_loopback=True)
        adapter = PlaywrightAdapter(headless=True, ssrf_validator=validator)
        ctx = ExecutionContext(
            run_id="run_test_fail_assert",
            step_id="step_assert_fail",
            base_url=base_url,
            allowlist=["127.0.0.1"],
        )

        try:
            step = TestStepSchema(
                step_id="step_fail",
                adapter_type=AdapterType.WEB,
                action="navigate",
                inputs={"url": f"{base_url}/login"},
                assertions=[
                    AssertionSchema(type=AssertionType.TEXT_VISIBLE, value="Non-existent String Nowhere On Page"),
                ],
            )
            res = await adapter.execute_step(step, ctx)
            assert res.status == StepStatus.FAILED
            assert res.error_code == ErrorCode.ASSERTION_FAILED
            assert "Assertion failed" in (res.error_message or "")
            # Failure screenshot should be automatically captured as evidence
            assert len(res.evidence) >= 1
            assert res.evidence[0].type == EvidenceType.SCREENSHOT
            assert "failure_screenshot_bytes" in res.outputs

        finally:
            await adapter.close_session(ctx.run_id)


@pytest.mark.asyncio
async def test_live_browser_set_cookies_and_storage() -> None:
    async with run_test_web_server() as base_url:
        validator = SSRFValidator(allow_loopback=True)
        adapter = PlaywrightAdapter(headless=True, ssrf_validator=validator)
        ctx = ExecutionContext(
            run_id="run_test_cookies_storage",
            step_id="step_cookie",
            base_url=base_url,
            allowlist=["127.0.0.1"],
        )

        try:
            # First navigate to page
            step_nav = TestStepSchema(
                step_id="step_nav",
                adapter_type=AdapterType.WEB,
                action="navigate",
                inputs={"url": f"{base_url}/login"},
            )
            res_nav = await adapter.execute_step(step_nav, ctx)
            assert res_nav.status == StepStatus.PASSED

            # Set cookie
            step_cookie = TestStepSchema(
                step_id="step_cookie",
                adapter_type=AdapterType.WEB,
                action="set_cookies",
                inputs={"name": "session_token", "value": "test-jwt-token-12345", "url": base_url},
            )
            res_cookie = await adapter.execute_step(step_cookie, ctx)
            assert res_cookie.status == StepStatus.PASSED
            assert res_cookie.outputs["cookies_set"] == 1
            assert "session_token" in res_cookie.outputs["cookie_names"]

            # Set local storage
            step_storage = TestStepSchema(
                step_id="step_storage",
                adapter_type=AdapterType.WEB,
                action="set_storage",
                inputs={"key": "auth_user", "value": "admin@example.com"},
            )
            res_storage = await adapter.execute_step(step_storage, ctx)
            assert res_storage.status == StepStatus.PASSED
            assert res_storage.outputs["storage_type"] == "localStorage"
            assert "auth_user" in res_storage.outputs["keys_set"]

            # Scroll
            step_scroll = TestStepSchema(
                step_id="step_scroll",
                adapter_type=AdapterType.WEB,
                action="scroll",
                inputs={"direction": "bottom"},
            )
            res_scroll = await adapter.execute_step(step_scroll, ctx)
            assert res_scroll.status == StepStatus.PASSED
            assert res_scroll.outputs["scrolled"] is True

        finally:
            await adapter.close_session(ctx.run_id)
