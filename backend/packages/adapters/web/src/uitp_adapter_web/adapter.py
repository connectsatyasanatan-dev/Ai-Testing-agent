"""
uitp_adapter_web.adapter
========================
Playwright UI testing adapter for the Universal Autonomous AI Testing Platform (Tasks P2-01 through P2-05).

Design and Security Constraints:
  - Adheres strictly to the BaseAdapter interface from uitp_adapter_base.
  - Strict route-level SSRF defense and allowlist enforcement via BrowserSecurityRouter.
  - Bounded timeouts for navigations and element interactions.
  - Resource isolation: max 3 pages/popups per context, downloads blocked, ephemeral profiles.
  - Safe locator parsing prohibiting arbitrary JavaScript injection, eval, or code strings.
  - Deterministic per-step results and structured failure diagnostics without secret leakage.
  - Clean browser, context, and page teardown on success, failure, timeout, and cancellation.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence
from urllib.parse import urljoin

try:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        async_playwright,
    )
    from playwright.async_api import TimeoutError as PlaywrightTimeoutError
    from playwright.async_api import Error as PlaywrightError
except ImportError:
    Browser = None  # type: ignore[assignment, misc]
    BrowserContext = None  # type: ignore[assignment, misc]
    Page = None  # type: ignore[assignment, misc]
    Playwright = None  # type: ignore[assignment, misc]
    async_playwright = None  # type: ignore[assignment, misc]
    PlaywrightTimeoutError = TimeoutError  # type: ignore[assignment, misc]
    PlaywrightError = Exception  # type: ignore[assignment, misc]

from uitp_adapter_base.base import BaseAdapter, ExecutionContext, StepExecutionResult
from uitp_adapter_web.assertions import WebAssertionEngine
from uitp_adapter_web.exceptions import (
    BlockedNavigationError,
    InvalidLocatorError,
    ResourceLimitExceededError,
    WebActionTimeoutError,
    WebAdapterError,
)
from uitp_adapter_web.locators import LocatorResolver, LocatorSpec
from uitp_adapter_web.router import BrowserSecurityRouter
from uitp_contracts.adapter import AdapterCapabilities
from uitp_contracts.enums import (
    AdapterType,
    ErrorCode,
    EvidenceType,
    StepStatus,
)
from uitp_contracts.evidence import EvidenceArtifactSchema
from uitp_contracts.plan import TestStepSchema
from uitp_policy.exceptions import (
    InvalidTargetUrlError,
    PolicyBlockedError,
    TargetNotAllowlistedError,
)
from uitp_policy.execution import ExecutionLimits, ExecutionPolicy
from uitp_policy.validator import SSRFValidator

logger = logging.getLogger(__name__)

DEFAULT_NAV_TIMEOUT_MS = 15000
DEFAULT_ACTION_TIMEOUT_MS = 5000
MAX_BROWSER_PAGES = 3


@dataclass
class BrowserSession:
    """Active Playwright session bound to a test run."""

    playwright: Any
    browser: Any
    context: Any
    page: Any
    router: BrowserSecurityRouter
    last_used: float


class PlaywrightAdapter(BaseAdapter):
    """Secure, bounded Playwright adapter for web browser testing."""

    ADAPTER_ID = "web_playwright_v1"
    VERSION = "0.1.0"
    SUPPORTED_ACTIONS = [
        "navigate",
        "goto",
        "click",
        "fill",
        "select",
        "select_option",
        "wait_for",
        "wait",
        "screenshot",
    ]
    LIMITATIONS = [
        "Network-layer and route-layer SSRF filtering enforced on all requests",
        "Arbitrary JavaScript execution, eval, and user script injection strictly prohibited",
        "Bounded to 3 concurrent browser pages; excess popups immediately closed",
        "File downloads disabled by default to protect worker disks",
        "Chromium headless execution in isolated containers",
    ]

    def __init__(
        self,
        *,
        execution_policy: ExecutionPolicy | None = None,
        ssrf_validator: SSRFValidator | None = None,
        assertion_engine: WebAssertionEngine | None = None,
        max_pages: int = MAX_BROWSER_PAGES,
        headless: bool = True,
    ) -> None:
        if execution_policy is not None:
            self.execution_policy = execution_policy
            self.ssrf_validator = execution_policy.ssrf_validator
        else:
            self.ssrf_validator = ssrf_validator or SSRFValidator()
            limits = ExecutionLimits()
            self.execution_policy = ExecutionPolicy(
                limits=limits,
                ssrf_validator=self.ssrf_validator,
            )
        self.assertion_engine = assertion_engine or WebAssertionEngine()
        self.max_pages = max_pages
        self.headless = headless
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = asyncio.Lock()

    def metadata(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_id=self.ADAPTER_ID,
            adapter_type=AdapterType.WEB,
            version=self.VERSION,
            supported_actions=list(self.SUPPORTED_ACTIONS),
            limitations=list(self.LIMITATIONS),
            conformance_version="1.0",
        )

    async def health_check(self) -> bool:
        """Confirm Playwright and Chromium dependencies are available and operational."""
        if async_playwright is None:
            return False
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                await browser.close()
            return True
        except Exception as exc:
            logger.warning("Playwright health check failed: %s", exc)
            return False

    async def get_or_create_session(self, context: ExecutionContext) -> BrowserSession:
        """Retrieve existing browser session for the run, or initialize a fresh one."""
        async with self._lock:
            session = self._sessions.get(context.run_id)
            if session is not None:
                # Confirm page and context are still open
                try:
                    if not session.page.is_closed():
                        session.last_used = time.monotonic()
                        return session
                except Exception:
                    pass
                # Previous session page closed; clean up
                await self._close_session_unlocked(context.run_id)

            if async_playwright is None:
                raise WebAdapterError("Playwright library is not installed or available.")

            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            browser_context = await browser.new_context(
                viewport={"width": 1280, "height": 720},
                accept_downloads=False,
                ignore_https_errors=False,
            )

            router = BrowserSecurityRouter(
                ssrf_validator=self.ssrf_validator,
                allowlist=context.allowlist,
                max_pages=self.max_pages,
            )
            await router.attach_to_context(browser_context)

            page = await browser_context.new_page()

            session = BrowserSession(
                playwright=p,
                browser=browser,
                context=browser_context,
                page=page,
                router=router,
                last_used=time.monotonic(),
            )
            self._sessions[context.run_id] = session
            return session

    async def close_session(self, run_id: str) -> None:
        """Explicitly shut down browser, context, and Playwright for a specific run."""
        async with self._lock:
            await self._close_session_unlocked(run_id)

    async def _close_session_unlocked(self, run_id: str) -> None:
        session = self._sessions.pop(run_id, None)
        if session is None:
            return
        try:
            if session.page and not session.page.is_closed():
                await session.page.close()
        except Exception as exc:
            logger.debug("Error closing page for run %s: %s", run_id, exc)

        try:
            if session.context:
                await session.context.close()
        except Exception as exc:
            logger.debug("Error closing browser context for run %s: %s", run_id, exc)

        try:
            if session.browser:
                await session.browser.close()
        except Exception as exc:
            logger.debug("Error closing browser for run %s: %s", run_id, exc)

        try:
            if session.playwright:
                await session.playwright.stop()
        except Exception as exc:
            logger.debug("Error stopping Playwright for run %s: %s", run_id, exc)

    async def close_all_sessions(self) -> None:
        """Close all active sessions (e.g. during worker teardown)."""
        async with self._lock:
            run_ids = list(self._sessions.keys())
            for rid in run_ids:
                await self._close_session_unlocked(rid)

    def _resolve_url(self, step: TestStepSchema, context: ExecutionContext) -> tuple[str | None, StepExecutionResult | None]:
        url_input = step.inputs.get("url") or step.inputs.get("target_url")
        if not url_input or not isinstance(url_input, str):
            return None, StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message="Step 'navigate' requires a non-empty 'url' input.",
            )

        target_url = url_input.strip()
        if not target_url.startswith(("http://", "https://")):
            if not context.base_url:
                return None, StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=f"Relative URL '{target_url}' cannot be resolved without environment base_url.",
                )
            target_url = urljoin(context.base_url, target_url)

        return target_url, None

    async def execute_step(
        self,
        step: TestStepSchema,
        context: ExecutionContext,
    ) -> StepExecutionResult:
        """Execute a single bounded browser step with full security controls."""
        start_time = time.monotonic()

        # 1. Action validation
        try:
            self.validate_step(step)
        except Exception as e:
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.UNSUPPORTED_ACTION,
                error_message=str(e),
            )

        action = step.action.lower()

        # 2. Pre-flight URL validation for navigation actions
        target_url: str | None = None
        if action in ("navigate", "goto"):
            resolved_url, err_res = self._resolve_url(step, context)
            if err_res is not None:
                return err_res
            target_url = resolved_url

            # Scheme validation
            try:
                self.execution_policy.validate_url_scheme(target_url)
            except InvalidTargetUrlError as exc:
                return StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=str(exc),
                    outputs={"url": target_url},
                )

            # SSRF & Allowlist pre-flight check
            allowlist = context.allowlist or step.inputs.get("allowlist")
            try:
                val = self.ssrf_validator.validate_url(target_url, allowlist=allowlist)
                if inspect.iscoroutine(val):
                    await val
            except TargetNotAllowlistedError as exc:
                logger.warning("Web navigation blocked: target not allowlisted (%s)", target_url)
                return StepExecutionResult(
                    status=StepStatus.BLOCKED,
                    error_code=ErrorCode.TARGET_NOT_ALLOWLISTED,
                    error_message=str(exc),
                    outputs={"url": target_url},
                )
            except PolicyBlockedError as exc:
                logger.warning("Web navigation blocked by SSRF policy (%s)", target_url)
                return StepExecutionResult(
                    status=StepStatus.BLOCKED,
                    error_code=ErrorCode.POLICY_BLOCKED,
                    error_message=str(exc),
                    outputs={"url": target_url},
                )
            except InvalidTargetUrlError as exc:
                return StepExecutionResult(
                    status=StepStatus.FAILED,
                    error_code=ErrorCode.VALIDATION_ERROR,
                    error_message=str(exc),
                    outputs={"url": target_url},
                )

        # 3. Timeout bounding
        step_timeout = min(step.timeout_seconds, context.timeout_seconds)
        action_timeout_ms = int(min(DEFAULT_ACTION_TIMEOUT_MS, step_timeout * 1000))
        nav_timeout_ms = int(min(DEFAULT_NAV_TIMEOUT_MS, step_timeout * 1000))

        # 4. Acquire browser session
        try:
            session = await self.get_or_create_session(context)
        except Exception as exc:
            logger.exception("Failed to initialize browser session for run %s: %s", context.run_id, exc)
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message=f"Browser initialization failed: {type(exc).__name__}: {exc}",
            )

        page = session.page
        router = session.router
        # Clear router's previous blocked request log for this step
        router.blocked_requests.clear()

        # 5. Dispatch Action
        action_outputs: dict[str, Any] = {}
        evidence_list: list[EvidenceArtifactSchema] = []

        try:
            if action in ("navigate", "goto"):
                assert target_url is not None
                await page.goto(target_url, timeout=nav_timeout_ms, wait_until="load")
                action_outputs["url"] = page.url
                action_outputs["title"] = await page.title()

            elif action == "click":
                spec = LocatorResolver.parse_spec(step.inputs)
                locator = LocatorResolver.resolve(page, spec)
                await locator.click(timeout=action_timeout_ms)
                action_outputs["clicked_spec"] = spec.value

            elif action == "fill":
                spec = LocatorResolver.parse_spec(step.inputs)
                locator = LocatorResolver.resolve(page, spec)
                fill_val = str(step.inputs.get("value", ""))
                await locator.fill(fill_val, timeout=action_timeout_ms)
                # Redact if password or secret reference
                is_sensitive = "password" in spec.value.lower() or "secret" in spec.value.lower() or "secret_ref" in step.inputs
                action_outputs["filled_spec"] = spec.value
                action_outputs["filled_value"] = "[REDACTED]" if is_sensitive else fill_val

            elif action in ("select", "select_option"):
                spec = LocatorResolver.parse_spec(step.inputs)
                locator = LocatorResolver.resolve(page, spec)
                val = step.inputs.get("value")
                lbl = step.inputs.get("label")
                idx = step.inputs.get("index")

                if val is not None:
                    await locator.select_option(value=str(val), timeout=action_timeout_ms)
                elif lbl is not None:
                    await locator.select_option(label=str(lbl), timeout=action_timeout_ms)
                elif idx is not None:
                    await locator.select_option(index=int(idx), timeout=action_timeout_ms)
                else:
                    return StepExecutionResult(
                        status=StepStatus.FAILED,
                        error_code=ErrorCode.VALIDATION_ERROR,
                        error_message="Action 'select' requires 'value', 'label', or 'index'.",
                    )
                action_outputs["selected_spec"] = spec.value

            elif action in ("wait_for", "wait"):
                wait_state = step.inputs.get("state")
                url_condition = step.inputs.get("url")
                time_condition = step.inputs.get("seconds") or step.inputs.get("delay")

                if url_condition:
                    await page.wait_for_url(str(url_condition), timeout=action_timeout_ms)
                    action_outputs["waited_for_url"] = str(url_condition)
                elif wait_state or "selector" in step.inputs or "locator" in step.inputs:
                    spec = LocatorResolver.parse_spec(step.inputs)
                    locator = LocatorResolver.resolve(page, spec)
                    target_state = str(wait_state or "visible").lower()
                    if target_state not in ("visible", "hidden", "attached", "detached"):
                        return StepExecutionResult(
                            status=StepStatus.FAILED,
                            error_code=ErrorCode.VALIDATION_ERROR,
                            error_message=f"Unsupported wait_for state: '{target_state}'",
                        )
                    await locator.wait_for(state=target_state, timeout=action_timeout_ms)
                    action_outputs["waited_for_state"] = target_state
                elif time_condition:
                    dur = min(float(time_condition), 10.0)
                    await asyncio.sleep(dur)
                    action_outputs["waited_seconds"] = dur
                else:
                    return StepExecutionResult(
                        status=StepStatus.FAILED,
                        error_code=ErrorCode.VALIDATION_ERROR,
                        error_message="Action 'wait_for' requires 'selector', 'url', or 'seconds'.",
                    )

            elif action == "screenshot":
                full_page = bool(step.inputs.get("full_page", False))
                png_bytes = await page.screenshot(full_page=full_page)
                checksum = hashlib.sha256(png_bytes).hexdigest()
                artifact_id = str(uuid.uuid4())
                artifact = EvidenceArtifactSchema(
                    id=artifact_id,
                    run_id=context.run_id,
                    step_attempt_id=f"{context.run_id}_{context.step_id}_{context.attempt_number}",
                    type=EvidenceType.SCREENSHOT,
                    checksum=checksum,
                    redaction_status="not_required",
                    created_at=datetime.now(UTC),
                    size_bytes=len(png_bytes),
                    content_type="image/png",
                )
                evidence_list.append(artifact)
                action_outputs["screenshot_artifact_id"] = artifact_id
                action_outputs["screenshot_checksum"] = checksum
                action_outputs["screenshot_bytes"] = png_bytes

        except InvalidLocatorError as exc:
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message=str(exc),
                outputs=action_outputs,
            )
        except (PlaywrightTimeoutError, TimeoutError, asyncio.TimeoutError) as exc:
            logger.warning("Action '%s' timed out in step %s: %s", action, step.step_id, exc)
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message=f"Action '{action}' timed out after {step_timeout:.1f}s",
                outputs=action_outputs,
            )
        except PlaywrightError as exc:
            exc_str = str(exc)
            # Check if this failure was due to a route block (e.g. net::ERR_BLOCKED_BY_CLIENT)
            if "net::ERR_BLOCKED_BY_CLIENT" in exc_str or router.blocked_requests:
                block_info = router.blocked_requests[-1] if router.blocked_requests else {"reason": "Policy Blocked"}
                logger.warning("Playwright navigation blocked by security router: %s", block_info)
                return StepExecutionResult(
                    status=StepStatus.BLOCKED,
                    error_code=ErrorCode.POLICY_BLOCKED,
                    error_message=f"Navigation blocked by security policy: {block_info.get('reason')}",
                    outputs=action_outputs,
                )
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message=f"Browser action failed: {exc_str}",
                outputs=action_outputs,
            )
        except Exception as exc:
            logger.exception("Unexpected error executing step %s action %s: %s", step.step_id, action, exc)
            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.VALIDATION_ERROR,
                error_message=f"Step execution error: {type(exc).__name__}: {exc}",
                outputs=action_outputs,
            )

        # Record standard current page state
        try:
            action_outputs["url"] = page.url
            action_outputs["title"] = await page.title()
        except Exception:
            pass

        duration_ms = (time.monotonic() - start_time) * 1000.0
        action_outputs["duration_ms"] = round(duration_ms, 2)

        # 6. Evaluate Assertions
        assertions_outcome = await self.assertion_engine.evaluate_all(page, step.assertions)
        action_outputs["assertions"] = assertions_outcome.model_dump()

        if not assertions_outcome.passed:
            failure_msg = "; ".join(assertions_outcome.failure_reasons)
            # Capture failure screenshot
            try:
                fail_png = await page.screenshot()
                fail_checksum = hashlib.sha256(fail_png).hexdigest()
                fail_artifact = EvidenceArtifactSchema(
                    id=str(uuid.uuid4()),
                    run_id=context.run_id,
                    step_attempt_id=f"{context.run_id}_{context.step_id}_{context.attempt_number}",
                    type=EvidenceType.SCREENSHOT,
                    checksum=fail_checksum,
                    redaction_status="not_required",
                    created_at=datetime.now(UTC),
                    size_bytes=len(fail_png),
                    content_type="image/png",
                )
                evidence_list.append(fail_artifact)
                action_outputs["failure_screenshot_bytes"] = fail_png
            except Exception as ss_exc:
                logger.debug("Could not capture failure screenshot: %s", ss_exc)

            return StepExecutionResult(
                status=StepStatus.FAILED,
                error_code=ErrorCode.ASSERTION_FAILED,
                error_message=f"Assertion failed: {failure_msg}",
                outputs=action_outputs,
                evidence=evidence_list,
            )

        return StepExecutionResult(
            status=StepStatus.PASSED,
            outputs=action_outputs,
            evidence=evidence_list,
        )
