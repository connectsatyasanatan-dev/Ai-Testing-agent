"""
uitp_adapter_base
=================
Abstract adapter contract, capability negotiation, and registry.
"""
from uitp_adapter_base.base import (
    BaseAdapter,
    ExecutionContext,
    StepExecutionResult,
)
from uitp_adapter_base.exceptions import (
    AdapterError,
    AdapterNotFoundError,
    DuplicateAdapterError,
    InvalidCapabilityError,
    UnsupportedActionError,
)
from uitp_adapter_base.registry import (
    AdapterRegistry,
    get_default_registry,
    reset_default_registry,
)

__all__ = [
    "BaseAdapter",
    "ExecutionContext",
    "StepExecutionResult",
    "AdapterError",
    "UnsupportedActionError",
    "AdapterNotFoundError",
    "DuplicateAdapterError",
    "InvalidCapabilityError",
    "AdapterRegistry",
    "get_default_registry",
    "reset_default_registry",
]
