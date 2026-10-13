"""
uitp_api.services.ai_provider
=============================
Vendor-agnostic AI provider abstraction for test plan generation.
Supports structured output generation, timeouts, and local mock testing.

SECURITY:
- API keys are read strictly from server-side environment/SecretStr.
- API keys and raw authorization headers are never logged or returned.
- Inputs and outputs are strictly validated; AI output is treated as untrusted data.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

import httpx

from uitp_api.config import Settings, get_settings
from uitp_api.exceptions import AIProviderNotConfiguredError, ValidationError
from uitp_contracts.plan import TokenUsage

logger = logging.getLogger(__name__)


# ── AI Provider Protocol ─────────────────────────────────────────────────────

@runtime_checkable
class AIProvider(Protocol):
    """Protocol for AI plan generation backends."""

    async def generate_plan_draft(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        """Request structured JSON test plan draft from the model.

        Must return a parsed JSON dictionary containing at least:
        {
            "name": str,
            "description": str | None,
            "steps": list[dict[str, Any]]
        }
        """
        ...

    async def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> tuple[dict[str, Any], TokenUsage]:
        """Request structured JSON response with token telemetry."""
        ...



# ── Mock AI Provider (Deterministic, Offline Testing) ─────────────────────────

class MockAIProvider:
    """Deterministic in-memory mock provider for testing and offline environments.

    Allows tests to supply canned responses or induce simulated failures/timeouts
    without requiring external network or paid API credentials.
    """

    def __init__(
        self,
        canned_response: dict[str, Any] | None = None,
        should_fail: bool = False,
        fail_message: str = "Mock AI provider simulated error",
        timeout: bool = False,
    ) -> None:
        self.canned_response = canned_response
        self.should_fail = should_fail
        self.fail_message = fail_message
        self.timeout = timeout

    async def generate_plan_draft(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        if self.timeout:
            raise ValidationError("AI provider request timed out. Please try again.")

        if self.should_fail:
            raise ValidationError(self.fail_message)

        if self.canned_response is not None:
            return self.canned_response

        # Default mock generation based on prompt contents
        adapter_type = "web" if "web" in user_prompt.lower() else "http_api"
        if adapter_type == "web":
            return {
                "name": "Generated Web Navigation Plan",
                "description": "Mock AI generated test plan for web adapter",
                "steps": [
                    {
                        "step_id": "step_1_nav",
                        "adapter_type": "web",
                        "action": "navigate",
                        "inputs": {"url": "https://example.com/login"},
                        "assertions": [
                            {"type": "title_contains", "value": "Login"}
                        ],
                        "timeout_seconds": 15.0,
                        "retry_policy": {"max_attempts": 2, "backoff_coefficient": 1.5, "allow_non_idempotent_retry": False},
                        "idempotency_class": "idempotent",
                        "risk_level": "low",
                        "description": "Navigate to login page",
                    },
                    {
                        "step_id": "step_2_fill",
                        "adapter_type": "web",
                        "action": "fill",
                        "inputs": {"selector": "#username", "text": "testuser"},
                        "assertions": [
                            {"type": "element_visible", "value": "#username"}
                        ],
                        "timeout_seconds": 10.0,
                        "retry_policy": {"max_attempts": 1, "backoff_coefficient": 2.0, "allow_non_idempotent_retry": False},
                        "idempotency_class": "idempotent",
                        "risk_level": "low",
                        "description": "Enter test username",
                    },
                ],
            }

        return {
            "name": "Generated API Test Plan",
            "description": "Mock AI generated test plan for http_api adapter",
            "steps": [
                {
                    "step_id": "step_1_health",
                    "adapter_type": "http_api",
                    "action": "get",
                    "inputs": {"path": "/api/v1/health"},
                    "assertions": [
                        {"type": "status_code", "value": 200}
                    ],
                    "timeout_seconds": 10.0,
                    "retry_policy": {"max_attempts": 1, "backoff_coefficient": 2.0, "allow_non_idempotent_retry": False},
                    "idempotency_class": "safe",
                    "risk_level": "low",
                    "description": "Verify service health status",
                },
                {
                    "step_id": "step_2_get_items",
                    "adapter_type": "http_api",
                    "action": "get",
                    "inputs": {"path": "/api/v1/items"},
                    "assertions": [
                        {"type": "status_code", "value": 200},
                        {"type": "json_path_exists", "path": "items"},
                    ],
                    "timeout_seconds": 10.0,
                    "retry_policy": {"max_attempts": 1, "backoff_coefficient": 2.0, "allow_non_idempotent_retry": False},
                    "idempotency_class": "safe",
                    "risk_level": "low",
                    "description": "Fetch items list",
                },
            ],
        }

    async def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> tuple[dict[str, Any], TokenUsage]:
        res = await self.generate_plan_draft(system_prompt=system_prompt, user_prompt=user_prompt)
        usage = TokenUsage(prompt_tokens=250, completion_tokens=420, total_tokens=670)
        return res, usage


# ── HTTP OpenAI-Compatible Provider ───────────────────────────────────────────


class HttpOpenAICompatibleProvider:
    """Provider for OpenAI-compatible chat completion APIs (OpenAI, Azure, vLLM, Ollama)."""

    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        max_tokens: int = 2048,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens

    async def generate_plan_draft(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
    ) -> dict[str, Any]:
        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
            "temperature": 0.2,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            logger.warning("AI provider request timed out after %.1fs", self.timeout_seconds)
            raise ValidationError("AI provider request timed out. Please try again.") from exc
        except httpx.RequestError as exc:
            logger.error("AI provider network connection failed: %s", type(exc).__name__)
            raise ValidationError("Failed to connect to AI provider.") from exc

        if response.status_code != 200:
            logger.warning("AI provider returned HTTP status %d", response.status_code)
            raise ValidationError("AI provider request failed. Please check provider status or configuration.")

        try:
            data = response.json()
            choices = data.get("choices")
            if not choices or not isinstance(choices, list):
                raise ValueError("Response missing 'choices' list")
            raw_content = choices[0].get("message", {}).get("content", "")
            parsed = json.loads(raw_content)
            if not isinstance(parsed, dict):
                raise ValueError("Parsed output is not a JSON object")
            return parsed
        except Exception as exc:
            logger.warning("AI provider returned invalid JSON: %s", type(exc).__name__)
            raise ValidationError("AI provider returned malformed output. Please refine your prompt.") from exc

    async def complete_structured(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
    ) -> tuple[dict[str, Any], TokenUsage]:
        """Request structured JSON response with token telemetry."""
        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
            "temperature": temperature,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            logger.warning("AI provider request timed out after %.1fs", self.timeout_seconds)
            raise ValidationError("AI provider request timed out. Please try again.") from exc
        except httpx.RequestError as exc:
            logger.error("AI provider network connection failed: %s", type(exc).__name__)
            raise ValidationError("Failed to connect to AI provider.") from exc

        if response.status_code != 200:
            logger.warning("AI provider returned HTTP status %d", response.status_code)
            raise ValidationError("AI provider request failed. Please check provider status or configuration.")

        try:
            data = response.json()
            choices = data.get("choices")
            if not choices or not isinstance(choices, list):
                raise ValueError("Response missing 'choices' list")
            raw_content = choices[0].get("message", {}).get("content", "")
            parsed = json.loads(raw_content)
            if not isinstance(parsed, dict):
                raise ValueError("Parsed output is not a JSON object")
            usage_data = data.get("usage", {})
            usage = TokenUsage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            )
            return parsed, usage
        except Exception as exc:
            logger.warning("AI provider returned invalid JSON: %s", type(exc).__name__)
            raise ValidationError("AI provider returned malformed output. Please refine your prompt.") from exc


# ── NVIDIA Nemotron Provider ──────────────────────────────────────────────────

class NvidiaNemotronProvider(HttpOpenAICompatibleProvider):
    """NVIDIA Nemotron AI Provider (via NVIDIA NIM or hosted API catalog)."""

    def __init__(
        self,
        api_key: str,
        model: str = "nvidia/nemotron-4-340b-instruct",
        base_url: str = "https://integrate.api.nvidia.com/v1",
        timeout_seconds: float = 45.0,
        max_tokens: int = 4096,
    ) -> None:
        super().__init__(
            api_key=api_key,
            model=model,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            max_tokens=max_tokens,
        )


# ── Global Provider Lifecycle & Factory ───────────────────────────────────────

_provider_override: AIProvider | None = None


def set_ai_provider(provider: AIProvider | None) -> None:
    """Override AI provider instance (for unit and integration tests)."""
    global _provider_override
    _provider_override = provider


def get_ai_provider(settings: Settings | None = None) -> AIProvider:
    """Return active AIProvider or raise AIProviderNotConfiguredError.

    Never exposes API keys or sensitive settings.
    """
    global _provider_override
    if _provider_override is not None:
        return _provider_override

    if settings is None:
        settings = get_settings()

    if settings.ai_provider == "mock":
        return MockAIProvider()

    if not settings.ai_enabled:
        raise AIProviderNotConfiguredError()

    api_key_str = settings.ai_api_key.get_secret_value() if settings.ai_api_key else ""
    if settings.ai_provider in ("nemotron", "nvidia"):
        return NvidiaNemotronProvider(
            api_key=api_key_str,
            model=settings.ai_model or "nvidia/nemotron-4-340b-instruct",
            base_url=settings.ai_base_url or "https://integrate.api.nvidia.com/v1",
            timeout_seconds=settings.ai_timeout_seconds,
            max_tokens=settings.ai_max_tokens,
        )

    return HttpOpenAICompatibleProvider(
        api_key=api_key_str,
        model=settings.ai_model or "gpt-4o-mini",
        base_url=settings.ai_base_url or "https://api.openai.com/v1",
        timeout_seconds=settings.ai_timeout_seconds,
        max_tokens=settings.ai_max_tokens,
    )

