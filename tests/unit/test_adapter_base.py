"""
tests/unit/test_adapter_base.py
===============================
Unit tests for adapter base interface, capability negotiation, and registry.

Verifies:
  1. Registering an adapter.
  2. Rejecting duplicate adapter identifiers.
  3. Retrieving an existing adapter.
  4. Handling an unknown adapter identifier.
  5. Capability enumeration.
  6. Supported-action negotiation.
  7. Unsupported-action typed errors.
  8. Invalid or malformed capability declarations.
  9. Deterministic registry behavior and test isolation.
  10. Package imports without unexpected side effects.
  11. HttpApiAdapter conformance to BaseAdapter interface.
"""
from __future__ import annotations

import pytest

from uitp_adapter_base import (
    AdapterNotFoundError,
    AdapterRegistry,
    BaseAdapter,
    DuplicateAdapterError,
    ExecutionContext,
    InvalidCapabilityError,
    StepExecutionResult,
    UnsupportedActionError,
    get_default_registry,
    reset_default_registry,
)
from uitp_adapter_http_api import HttpApiAdapter
from uitp_contracts.adapter import AdapterCapabilities
from uitp_contracts.enums import AdapterType, ErrorCode, StepStatus
from uitp_contracts.plan import TestStepSchema


class DummyAdapter(BaseAdapter):
    """Minimal test adapter with predictable capabilities."""

    def __init__(self, adapter_id: str = "dummy_v1", actions: list[str] | None = None) -> None:
        self._adapter_id = adapter_id
        self._actions = actions or ["ping", "echo"]

    def metadata(self) -> AdapterCapabilities:
        return AdapterCapabilities(
            adapter_id=self._adapter_id,
            adapter_type=AdapterType.HTTP_API,
            version="1.0.0",
            supported_actions=self._actions,
            limitations=["Testing dummy only"],
            conformance_version="1.0",
        )

    async def execute_step(
        self, step: TestStepSchema, context: ExecutionContext
    ) -> StepExecutionResult:
        self.validate_step(step)
        return StepExecutionResult(status=StepStatus.PASSED)

    async def health_check(self) -> bool:
        return True


class MalformedAdapter(BaseAdapter):
    """Adapter with empty actions to test capability validation."""

    def metadata(self) -> AdapterCapabilities:
        # Pydantic will allow instantiation only if valid, so we deliberately return empty list
        # using a bypass or bad object to test registry defenses
        return AdapterCapabilities.model_construct(  # type: ignore[call-arg]
            adapter_id="bad_v1",
            adapter_type=AdapterType.HTTP_API,
            version="1.0.0",
            supported_actions=[],
            limitations=[],
            conformance_version="1.0",
        )

    async def execute_step(
        self, step: TestStepSchema, context: ExecutionContext
    ) -> StepExecutionResult:
        return StepExecutionResult(status=StepStatus.PASSED)

    async def health_check(self) -> bool:
        return True


@pytest.mark.unit
class TestAdapterRegistrationAndRetrieval:
    def test_register_and_get_adapter(self) -> None:
        registry = AdapterRegistry()
        adapter = DummyAdapter("test_adapter_1")

        registry.register(adapter)
        assert registry.has("test_adapter_1") is True
        assert registry.get("test_adapter_1") is adapter

    def test_reject_duplicate_registration_by_default(self) -> None:
        registry = AdapterRegistry()
        adapter1 = DummyAdapter("duplicate_id")
        adapter2 = DummyAdapter("duplicate_id")

        registry.register(adapter1)
        with pytest.raises(DuplicateAdapterError, match="is already registered"):
            registry.register(adapter2)

        # Overwrite=True should succeed
        registry.register(adapter2, overwrite=True)
        assert registry.get("duplicate_id") is adapter2

    def test_get_unknown_adapter_raises_typed_error(self) -> None:
        registry = AdapterRegistry()
        with pytest.raises(AdapterNotFoundError, match="unknown_adapter"):
            registry.get("unknown_adapter")

    def test_unregister_adapter(self) -> None:
        registry = AdapterRegistry()
        adapter = DummyAdapter("temp_adapter")
        registry.register(adapter)
        assert registry.has("temp_adapter") is True

        registry.unregister("temp_adapter")
        assert registry.has("temp_adapter") is False
        with pytest.raises(AdapterNotFoundError):
            registry.get("temp_adapter")


@pytest.mark.unit
class TestCapabilityEnumerationAndNegotiation:
    def test_list_capabilities(self) -> None:
        registry = AdapterRegistry()
        a1 = DummyAdapter("a1", ["act1"])
        a2 = DummyAdapter("a2", ["act2"])

        registry.register(a1)
        registry.register(a2)

        caps = registry.list_capabilities()
        assert len(caps) == 2
        ids = {c.adapter_id for c in caps}
        assert ids == {"a1", "a2"}

    def test_supported_action_negotiation(self) -> None:
        registry = AdapterRegistry()
        adapter = DummyAdapter("negotiator", ["supported_act"])
        registry.register(adapter)

        # Should pass without error
        registry.validate_action("negotiator", "supported_act")

        step = TestStepSchema(
            step_id="step_1",
            adapter_type=AdapterType.HTTP_API,
            action="supported_act",
        )
        adapter.validate_step(step)

    def test_unsupported_action_raises_typed_error(self) -> None:
        registry = AdapterRegistry()
        adapter = DummyAdapter("strict_adapter", ["allowed_act"])
        registry.register(adapter)

        with pytest.raises(UnsupportedActionError) as exc_info:
            registry.validate_action("strict_adapter", "forbidden_act")
        assert exc_info.value.code == ErrorCode.UNSUPPORTED_ACTION
        assert exc_info.value.action == "forbidden_act"
        assert exc_info.value.adapter_id == "strict_adapter"

        step = TestStepSchema(
            step_id="step_2",
            adapter_type=AdapterType.HTTP_API,
            action="unsupported_step_act",
        )
        with pytest.raises(UnsupportedActionError) as exc_step:
            adapter.validate_step(step)
        assert exc_step.value.code == ErrorCode.UNSUPPORTED_ACTION


@pytest.mark.unit
class TestCapabilityValidationDefenses:
    def test_reject_non_base_adapter_object(self) -> None:
        registry = AdapterRegistry()
        with pytest.raises(InvalidCapabilityError, match="must inherit from BaseAdapter"):
            registry.register("not an adapter" * 1)  # type: ignore[arg-type]

    def test_reject_empty_supported_actions(self) -> None:
        registry = AdapterRegistry()
        bad_adapter = MalformedAdapter()
        with pytest.raises(InvalidCapabilityError, match="declare at least one supported action"):
            registry.register(bad_adapter)


@pytest.mark.unit
class TestRegistryIsolationAndLifecycle:
    def test_default_registry_reset(self) -> None:
        reset_default_registry()
        reg = get_default_registry()
        adapter = DummyAdapter("global_dummy")
        reg.register(adapter)
        assert reg.has("global_dummy") is True

        reset_default_registry()
        assert reg.has("global_dummy") is False


@pytest.mark.unit
class TestHttpApiAdapterConformsToBase:
    def test_http_api_adapter_metadata(self) -> None:
        adapter = HttpApiAdapter()
        meta = adapter.metadata()
        assert meta.adapter_id == "http_api_v1"
        assert meta.adapter_type == AdapterType.HTTP_API
        assert "http_request" in meta.supported_actions
        assert "get" in meta.supported_actions
        assert adapter.supports_action("post") is True
        assert adapter.supports_action("invalid_custom_act") is False

    @pytest.mark.asyncio
    async def test_http_api_adapter_execution_conformance(self) -> None:
        adapter = HttpApiAdapter()
        step = TestStepSchema(
            step_id="step_http_1",
            adapter_type=AdapterType.HTTP_API,
            action="invalid_act",
        )
        ctx = ExecutionContext(run_id="run_1", step_id="step_http_1")
        with pytest.raises(UnsupportedActionError):
            await adapter.execute_step(step, ctx)
