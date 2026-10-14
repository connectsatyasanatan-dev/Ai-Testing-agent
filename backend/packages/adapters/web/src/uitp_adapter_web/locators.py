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

from uitp_adapter_web.exceptions import AmbiguousLocatorError, InvalidLocatorError

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

    @classmethod
    def parse_fallback_specs(cls, inputs: dict[str, Any]) -> tuple[LocatorSpec, list[LocatorSpec]]:
        """Extract primary locator and ordered conservative fallback candidates from inputs.

        Precedence for fallback ranking:
          1. Explicit 'fallbacks' list in inputs
          2. test_id (highest stability)
          3. role (accessible semantic name)
          4. label (form accessibility label)
          5. placeholder
          6. text (visible string)
          7. css (DOM selector)
        """
        primary = cls.parse_spec(inputs)
        fallbacks: list[LocatorSpec] = []
        seen = {(primary.strategy, primary.value, primary.name)}

        # 1. Parse explicit fallbacks if provided
        explicit_fbs = inputs.get("fallbacks")
        if isinstance(explicit_fbs, list):
            for fb in explicit_fbs:
                if isinstance(fb, dict):
                    try:
                        if "strategy" in fb:
                            spec = cls.parse_spec({"locator": fb})
                        else:
                            spec = cls.parse_spec(fb)
                        key = (spec.strategy, spec.value, spec.name)
                        if key not in seen:
                            seen.add(key)
                            fallbacks.append(spec)
                    except Exception as exc:
                        logger.warning("Ignoring invalid explicit fallback spec %s: %s", fb, exc)

        # 2. Extract implicit fallbacks from alternative keys provided in inputs
        for strat in ("test_id", "role", "label", "placeholder", "text", "css"):
            if strat == primary.strategy:
                continue
            spec_candidate: LocatorSpec | None = None
            try:
                if strat == "test_id" and ("test_id" in inputs or "data_testid" in inputs):
                    spec_candidate = cls.parse_spec({"test_id": inputs.get("test_id") or inputs.get("data_testid")})
                elif strat == "role" and "role" in inputs:
                    spec_candidate = cls.parse_spec({"role": inputs["role"], "name": inputs.get("name") or inputs.get("role_name")})
                elif strat == "label" and "label" in inputs:
                    spec_candidate = cls.parse_spec({"label": inputs["label"]})
                elif strat == "placeholder" and "placeholder" in inputs:
                    spec_candidate = cls.parse_spec({"placeholder": inputs["placeholder"]})
                elif strat == "text" and "text" in inputs:
                    spec_candidate = cls.parse_spec({"text": inputs["text"]})
                elif strat == "css" and "css" in inputs:
                    spec_candidate = cls.parse_spec({"css": inputs["css"]})
            except Exception:
                spec_candidate = None

            if spec_candidate is not None:
                key = (spec_candidate.strategy, spec_candidate.value, spec_candidate.name)
                if key not in seen:
                    seen.add(key)
                    fallbacks.append(spec_candidate)

        # Bound maximum fallbacks to 3 to prevent unbounded traversal
        return primary, fallbacks[:3]

    @classmethod
    async def resolve_with_fallback(
        cls,
        page: Any,
        primary_spec: LocatorSpec,
        fallback_specs: list[LocatorSpec],
    ) -> tuple[Any, LocatorSpec, bool, str | None]:
        """Resolve locator with conservative fallback and unambiguous match validation.

        Returns:
            (locator, resolved_spec, is_fallback, fallback_strategy_name)

        Raises:
            AmbiguousLocatorError: If a locator matches multiple elements.
        """
        primary_loc = cls.resolve(page, primary_spec)

        try:
            primary_count = await primary_loc.count()
        except Exception:
            primary_count = 0

        if primary_count == 1:
            return primary_loc, primary_spec, False, None

        if primary_count > 1:
            raise AmbiguousLocatorError(
                f"Primary locator '{primary_spec.strategy}:{primary_spec.value}' matched {primary_count} elements. "
                "Unambiguous match is required to prevent unintended side effects.",
                selector=primary_spec.value,
                match_count=primary_count,
            )

        # primary_count == 0: primary failed in a recoverable way. Try conservative fallbacks.
        for fb_spec in fallback_specs:
            fb_loc = cls.resolve(page, fb_spec)
            try:
                fb_count = await fb_loc.count()
            except Exception:
                fb_count = 0

            if fb_count == 1:
                logger.info(
                    "Primary locator '%s:%s' failed (0 matches); successfully recovered using fallback '%s:%s'",
                    primary_spec.strategy,
                    primary_spec.value,
                    fb_spec.strategy,
                    fb_spec.value,
                )
                return fb_loc, fb_spec, True, fb_spec.strategy
            elif fb_count > 1:
                logger.warning(
                    "Fallback locator '%s:%s' matched %d elements (ambiguous); skipping.",
                    fb_spec.strategy,
                    fb_spec.value,
                    fb_count,
                )

        # If fallbacks exhausted or none succeeded, return primary to proceed with standard timeout/failure reporting
        return primary_loc, primary_spec, False, None
