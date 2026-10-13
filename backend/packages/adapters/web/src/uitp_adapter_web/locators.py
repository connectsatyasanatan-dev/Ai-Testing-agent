"""
uitp_adapter_web.locators
=========================
Safe locator resolution and validation for browser automation (Task P2-02).

Design & Security Constraints:
  - Strict allowlist of locator strategies: css, text, test_id, role, label, placeholder.
  - Reject arbitrary code strings, eval patterns, JavaScript pseudo-selectors,
    and event handler injections.
  - Return strongly-typed Playwright Locator objects.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from uitp_adapter_web.exceptions import InvalidLocatorError

logger = logging.getLogger(__name__)

# Patterns that suggest code injection or arbitrary JavaScript execution
PROHIBITED_LOCATOR_TOKENS = (
    "javascript:",
    "eval(",
    "function(",
    "function ",
    "() =>",
    "=>",
    "<script",
    "document.",
    "window.",
    "__proto__",
    "prototype",
    ":eval(",
    ":expr(",
    "process.",
    "require(",
    "import(",
)

MAX_SELECTOR_LENGTH = 1024


@dataclass(frozen=True)
class LocatorSpec:
    """Parsed and validated locator specification."""

    strategy: str
    value: str
    name: str | None = None
    exact: bool = False


class LocatorResolver:
    """Validates and resolves safe locators on a Playwright Page."""

    SUPPORTED_STRATEGIES = frozenset(
        {"css", "text", "test_id", "role", "label", "placeholder"}
    )

    @classmethod
    def validate_raw_string(cls, selector: str) -> None:
        """Validate that a selector string is safe and bounded."""
        if not selector or not isinstance(selector, str):
            raise InvalidLocatorError("Selector must be a non-empty string.")

        if len(selector) > MAX_SELECTOR_LENGTH:
            raise InvalidLocatorError(
                f"Selector exceeds maximum length of {MAX_SELECTOR_LENGTH} characters."
            )

        lower_selector = selector.lower()
        for token in PROHIBITED_LOCATOR_TOKENS:
            if token in lower_selector:
                raise InvalidLocatorError(
                    f"Prohibited construct detected in selector: '{token}'. "
                    "Arbitrary JavaScript and dynamic evaluation are strictly disallowed.",
                    selector=selector,
                )

    @classmethod
    def parse_spec(cls, inputs: dict[str, Any]) -> LocatorSpec:
        """Extract and validate LocatorSpec from step inputs."""
        # 1. Explicit locator dict: {"locator": {"strategy": "role", "value": "button", "name": "Submit"}}
        raw_locator = inputs.get("locator")
        if isinstance(raw_locator, dict):
            strategy = str(raw_locator.get("strategy", "css")).strip().lower()
            value = str(raw_locator.get("value", "")).strip()
            name = raw_locator.get("name")
            exact = bool(raw_locator.get("exact", False))

            if strategy not in cls.SUPPORTED_STRATEGIES:
                raise InvalidLocatorError(
                    f"Unsupported locator strategy '{strategy}'. "
                    f"Supported strategies: {sorted(cls.SUPPORTED_STRATEGIES)}"
                )
            if not value and strategy != "role":
                raise InvalidLocatorError(f"Locator value cannot be empty for strategy '{strategy}'.")

            cls.validate_raw_string(value)
            if name:
                cls.validate_raw_string(str(name))

            return LocatorSpec(
                strategy=strategy,
                value=value,
                name=str(name) if name else None,
                exact=exact,
            )

        # 2. Strategy-specific shortcut keys in inputs
        if "role" in inputs:
            role_val = str(inputs["role"]).strip().lower()
            name_val = inputs.get("name") or inputs.get("role_name")
            cls.validate_raw_string(role_val)
            if name_val:
                cls.validate_raw_string(str(name_val))
            return LocatorSpec(
                strategy="role",
                value=role_val,
                name=str(name_val) if name_val else None,
                exact=bool(inputs.get("exact", False)),
            )

        if "test_id" in inputs or "data_testid" in inputs:
            tid = str(inputs.get("test_id") or inputs.get("data_testid")).strip()
            cls.validate_raw_string(tid)
            return LocatorSpec(strategy="test_id", value=tid)

        if "text" in inputs:
            text_val = str(inputs["text"]).strip()
            cls.validate_raw_string(text_val)
            return LocatorSpec(
                strategy="text",
                value=text_val,
                exact=bool(inputs.get("exact", False)),
            )

        if "label" in inputs:
            label_val = str(inputs["label"]).strip()
            cls.validate_raw_string(label_val)
            return LocatorSpec(
                strategy="label",
                value=label_val,
                exact=bool(inputs.get("exact", False)),
            )

        if "placeholder" in inputs:
            ph_val = str(inputs["placeholder"]).strip()
            cls.validate_raw_string(ph_val)
            return LocatorSpec(
                strategy="placeholder",
                value=ph_val,
                exact=bool(inputs.get("exact", False)),
            )

        if "css" in inputs:
            css_val = str(inputs["css"]).strip()
            cls.validate_raw_string(css_val)
            return LocatorSpec(strategy="css", value=css_val)

        # 3. Direct selector string
        selector = inputs.get("selector")
        if selector and isinstance(selector, str):
            selector_str = selector.strip()
            cls.validate_raw_string(selector_str)

            # Detect text= prefix
            if selector_str.startswith("text="):
                return LocatorSpec(strategy="text", value=selector_str[5:].strip())

            # Detect data-testid attribute in CSS
            match_tid = re.match(r'^\[data-testid=["\']?([^"\'\]]+)["\']?\]$', selector_str)
            if match_tid:
                return LocatorSpec(strategy="test_id", value=match_tid.group(1))

            return LocatorSpec(strategy="css", value=selector_str)

        raise InvalidLocatorError(
            "Step inputs must specify a valid target element using 'selector', "
            "'locator', 'css', 'text', 'role', 'test_id', 'label', or 'placeholder'."
        )

    @classmethod
    def resolve(cls, page: Any, spec: LocatorSpec) -> Any:
        """Resolve a validated LocatorSpec into a Playwright Locator."""
        if spec.strategy == "role":
            kwargs: dict[str, Any] = {"exact": spec.exact}
            if spec.name:
                kwargs["name"] = spec.name
            return page.get_by_role(spec.value, **kwargs)

        if spec.strategy == "text":
            return page.get_by_text(spec.value, exact=spec.exact)

        if spec.strategy == "test_id":
            return page.get_by_test_id(spec.value)

        if spec.strategy == "label":
            return page.get_by_label(spec.value, exact=spec.exact)

        if spec.strategy == "placeholder":
            return page.get_by_placeholder(spec.value, exact=spec.exact)

        # Default strategy: CSS
        return page.locator(spec.value)
