"""
uitp_adapter_base.registry
==========================
Deterministic in-memory registry for adapters and capability negotiation.
"""
from __future__ import annotations

import logging
from typing import Sequence

from uitp_adapter_base.base import BaseAdapter
from uitp_adapter_base.exceptions import (
    AdapterNotFoundError,
    DuplicateAdapterError,
    InvalidCapabilityError,
    UnsupportedActionError,
)
from uitp_contracts.adapter import AdapterCapabilities

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """Manages adapter registration and pre-execution capability negotiation.

    Prevents arbitrary dynamic class loading by requiring explicit registration
    of BaseAdapter instances.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, BaseAdapter] = {}

    def register(self, adapter: BaseAdapter, *, overwrite: bool = False) -> None:
        """Register an adapter instance.

        Raises:
            InvalidCapabilityError: if adapter metadata is malformed or invalid.
            DuplicateAdapterError: if adapter_id is already registered and overwrite is False.
        """
        if not isinstance(adapter, BaseAdapter):
            raise InvalidCapabilityError("Registered object must inherit from BaseAdapter.")

        try:
            meta = adapter.metadata()
        except Exception as exc:
            raise InvalidCapabilityError(f"Failed to retrieve metadata from adapter: {exc}") from exc

        if not isinstance(meta, AdapterCapabilities):
            raise InvalidCapabilityError(
                f"Adapter metadata must be an AdapterCapabilities instance, got {type(meta)}."
            )

        adapter_id = meta.adapter_id
        if not adapter_id or not adapter_id.strip():
            raise InvalidCapabilityError("Adapter adapter_id cannot be empty.")

        if not meta.supported_actions:
            raise InvalidCapabilityError(
                f"Adapter '{adapter_id}' must declare at least one supported action."
            )

        if adapter_id in self._adapters and not overwrite:
            raise DuplicateAdapterError(
                f"Adapter with identifier '{adapter_id}' is already registered."
            )

        self._adapters[adapter_id] = adapter
        logger.debug("Registered adapter: %s (version=%s)", adapter_id, meta.version)

    def unregister(self, adapter_id: str) -> None:
        """Remove an adapter from the registry."""
        self._adapters.pop(adapter_id, None)

    def get(self, adapter_id: str) -> BaseAdapter:
        """Retrieve an adapter by its identifier.

        Raises:
            AdapterNotFoundError: if adapter_id is not registered.
        """
        if adapter_id not in self._adapters:
            raise AdapterNotFoundError(adapter_id)
        return self._adapters[adapter_id]

    def has(self, adapter_id: str) -> bool:
        """Check if an adapter identifier is registered."""
        return adapter_id in self._adapters

    def list_all(self) -> list[BaseAdapter]:
        """Return all registered adapter instances."""
        return list(self._adapters.values())

    def list_capabilities(self) -> list[AdapterCapabilities]:
        """Return capability documents for all registered adapters."""
        return [adapter.metadata() for adapter in self._adapters.values()]

    def get_capabilities(self, adapter_id: str) -> AdapterCapabilities:
        """Retrieve capabilities for the specified adapter identifier."""
        return self.get(adapter_id).metadata()

    def validate_action(self, adapter_id: str, action: str) -> None:
        """Negotiate whether the adapter supports the requested action.

        Raises:
            AdapterNotFoundError: if adapter_id is not registered.
            UnsupportedActionError: if the requested action is not supported.
        """
        adapter = self.get(adapter_id)
        if not adapter.supports_action(action):
            raise UnsupportedActionError(action=action, adapter_id=adapter_id)

    def clear(self) -> None:
        """Clear all registered adapters. Intended for test isolation."""
        self._adapters.clear()


# ── Global default registry ───────────────────────────────────────────────────

_default_registry = AdapterRegistry()


def get_default_registry() -> AdapterRegistry:
    """Return the global default adapter registry."""
    return _default_registry


def reset_default_registry() -> None:
    """Clear all adapters from the global default registry."""
    _default_registry.clear()
