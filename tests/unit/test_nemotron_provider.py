"""
tests/unit/test_nemotron_provider.py
====================================
Unit tests for NVIDIA Nemotron provider configuration and structured completions.
"""
from __future__ import annotations

import json
import httpx
import pytest

from uitp_api.config import Settings
from uitp_api.services.ai_provider import (
    NvidiaNemotronProvider,
    get_ai_provider,
    set_ai_provider,
)
from uitp_contracts.plan import TokenUsage


@pytest.mark.asyncio
async def test_nemotron_provider_initialization() -> None:
    provider = NvidiaNemotronProvider(
        api_key="nvapi-test-key",
        model="nvidia/nemotron-4-340b-instruct",
        base_url="https://integrate.api.nvidia.com/v1",
        timeout_seconds=45.0,
        max_tokens=4096,
    )
    assert provider.model == "nvidia/nemotron-4-340b-instruct"
    assert provider.base_url == "https://integrate.api.nvidia.com/v1"
    assert provider.timeout_seconds == 45.0
    assert provider.max_tokens == 4096


@pytest.mark.asyncio
async def test_nemotron_complete_structured_parses_usage(monkeypatch) -> None:
    provider = NvidiaNemotronProvider(
        api_key="nvapi-test-key",
        base_url="http://mock-nemotron.local/v1",
    )

    mock_response_json = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": json.dumps({"mission_id": "m1", "status": "draft"}),
                }
            }
        ],
        "usage": {
            "prompt_tokens": 120,
            "completion_tokens": 85,
            "total_tokens": 205,
        },
    }

    class MockTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            assert request.headers["authorization"] == "Bearer nvapi-test-key"
            body = json.loads(request.content.decode())
            assert body["model"] == "nvidia/nemotron-4-340b-instruct"
            assert body["response_format"] == {"type": "json_object"}
            return httpx.Response(200, json=mock_response_json, request=request)

    transport = MockTransport()

    # Monkeypatch AsyncClient to use custom transport
    orig_client = httpx.AsyncClient

    def _mocked_client(*args, **kwargs):
        kwargs["transport"] = transport
        return orig_client(*args, **kwargs)

    monkeypatch.setattr("httpx.AsyncClient", _mocked_client)

    result, usage = await provider.complete_structured(
        system_prompt="You are Supervisor.",
        user_prompt="Decompose requirements.",
    )

    assert result == {"mission_id": "m1", "status": "draft"}
    assert isinstance(usage, TokenUsage)
    assert usage.prompt_tokens == 120
    assert usage.completion_tokens == 85
    assert usage.total_tokens == 205


def test_get_ai_provider_resolves_nemotron() -> None:
    set_ai_provider(None)
    from pydantic import SecretStr

    settings = Settings(
        secret_key=SecretStr("a" * 32),
        ai_provider="nemotron",
        ai_api_key=SecretStr("nvapi-test-key"),
        ai_model="nvidia/nemotron-4-340b-instruct",
        ai_base_url="https://integrate.api.nvidia.com/v1",
    )

    provider = get_ai_provider(settings)
    assert isinstance(provider, NvidiaNemotronProvider)
    assert provider.model == "nvidia/nemotron-4-340b-instruct"
