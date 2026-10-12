"""
uitp_adapter_base.base
======================
Core abstract adapter contract, execution context, and result types.

Design constraints:
  - Independent of FastAPI, SQLAlchemy, HTTPX request instances, and Playwright.
  - Strongly typed using shared contracts from uitp_contracts.
  - No side effects on import.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from uitp_adapter_base.exceptions import UnsupportedActionError
from uitp_contracts.adapter import AdapterCapabilities
from uitp_contracts.enums import AdapterType, ErrorCode, StepStatus
from uitp_contracts.evidence import EvidenceArtifactSchema
from uitp_contracts.plan import TestStepSchema


class ExecutionContext(BaseModel):
    """Runtime context passed to an adapter during step execution."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    step_id: str
    attempt_number: int = Field(default=1, ge=1)
    environment_id: str | None = None
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    secret_refs: dict[str, str] = Field(default_factory=dict)
    """Secret manager reference identifiers — not raw credential values."""
    base_url: str | None = None
    """Environment base URL to resolve relative paths against."""
    allowlist: list[str] | None = None
    """Allowlist host/port/pattern rules for the environment."""


class StepExecutionResult(BaseModel):
    """Structured outcome returned by an adapter after step execution."""

    model_config = ConfigDict(frozen=True)

    status: StepStatus
    error_code: ErrorCode | str | None = None
    error_message: str | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)
    evidence: list[EvidenceArtifactSchema] = Field(default_factory=list)


class BaseAdapter(ABC):
    """Abstract base class for all test execution adapters.

    Every concrete adapter must implement:
      - metadata(): capability declaration including supported actions and version.
      - execute_step(): async step execution.
      - health_check(): readiness verification of external tooling/dependencies.
    """

    @abstractmethod
    def metadata(self) -> AdapterCapabilities:
        """Return the stable, versioned capability declaration of this adapter."""
        ...

    @property
    def adapter_id(self) -> str:
        """Unique adapter identifier (e.g. 'http_api_v1')."""
        return self.metadata().adapter_id

    @property
    def adapter_type(self) -> AdapterType:
        """Target type category for this adapter."""
        return self.metadata().adapter_type

    def supports_action(self, action: str) -> bool:
        """Return True if the specified action is in declared supported_actions."""
        return action in self.metadata().supported_actions

    def validate_step(self, step: TestStepSchema) -> None:
        """Validate that the step's action is supported by this adapter.

        Raises:
            UnsupportedActionError: if the step action is not supported.
        """
        if not self.supports_action(step.action):
            raise UnsupportedActionError(
                action=step.action,
                adapter_id=self.adapter_id,
            )

    @abstractmethod
    async def execute_step(
        self,
        step: TestStepSchema,
        context: ExecutionContext,
    ) -> StepExecutionResult:
        """Execute a single bounded step against the target environment."""
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check adapter dependencies and worker readiness."""
        ...
