"""
uitp_contracts.adapter
======================
Adapter capability declaration schema.

Every adapter's metadata() call returns an AdapterCapabilities instance.
The orchestrator uses this to validate steps before execution.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from uitp_contracts.enums import AdapterType


class AdapterCapabilities(BaseModel):
    """Machine-readable capability declaration from an adapter.

    Adapters must return stable, versioned capability documents so
    that runs can be reproduced (ADAPTERS_AND_CAPABILITIES.md §5).
    """

    model_config = ConfigDict(frozen=True)

    adapter_id: str
    """Stable, unique identifier for this adapter (e.g. 'http_api_v1')."""

    adapter_type: AdapterType
    version: str
    """Semantic version string of the adapter implementation."""

    supported_actions: list[str] = Field(..., min_length=1)
    """Allowlisted action names this adapter supports."""

    limitations: list[str] = Field(default_factory=list)
    """Human-readable list of known limitations for the plan editor."""

    conformance_version: str = "1.0"
    """Version of the conformance test suite this adapter was tested against."""
