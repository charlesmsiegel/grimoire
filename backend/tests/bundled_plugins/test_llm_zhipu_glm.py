"""Tests for the bundled `llm-zhipu-glm` plugin.

HTTP traffic is intercepted with `httpx.MockTransport`. Tests cover plan
endpoint selection, the OpenAI-compatible request shape, streaming, and
the subscription-pricing behavior.
"""

from __future__ import annotations

import json

import httpx
import pytest

from grimoire.types.common import HealthLevel
from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.types.plugins import PluginKind

from .conftest import load_bundled


def _install_mock_transport(provider, handler) -> list[httpx.Request]:
    requests: list[httpx.Request] = []

    def _capture(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    provider._client = httpx.AsyncClient(
        base_url=provider._base_url,
        headers={"Authorization": f"Bearer {provider._api_key}"},
        transport=httpx.MockTransport(_capture),
    )
    return requests


def test_manifest_loads_and_protocol_satisfied() -> None:
    result = load_bundled("llm-zhipu-glm", config={"api_key": "x"})
    assert result.ok, result.errors
    manifest = result.manifest
    assert manifest is not None
    assert PluginKind.LLM_PROVIDER in manifest.implements
    provider = result.instances[0].instance
    assert provider.id == "zhipu-glm"


def test_pay_as_you_go_endpoint(zhipu_glm_module) -> None:
    provider = zhipu_glm_module.ZhipuGLMLLMProvider(
        config={"api_key": "k", "plan": "pay-as-you-go"}
    )
    assert provider._base_url == "https://open.bigmodel.cn/api/paas/v4"


def test_coding_plan_endpoint(zhipu_glm_module) -> None:
    provider = zhipu_glm_module.ZhipuGLMLLMProvider(
        config={"api_key": "k", "plan": "coding-plan"}
    )
    assert provider._base_url == "https://api.z.ai/api/coding/paas/v4"


def test_glm_coding_plan_endpoint(zhipu_glm_module) -> None:
    provider = zhipu_glm_module.ZhipuGLMLLMProvider(
        config={"api_key": "k", "plan": "glm-coding-plan"}
    )
    assert provider._base_url == "https://api.z.ai/api/coding/paas/v4"


def test_custom_base_url_overrides_plan(zhipu_glm_module) -> None:
    provider = zhipu_glm_module.ZhipuGLMLLMProvider(
        config={
            "api_key": "k",
            "plan": "coding-plan",
            "base_url": "http://my-proxy/v4/",
        }
    )
    assert provider._base_url == "http://my-proxy/v4"


@pytest.mark.asyncio
async def test_complete_returns_text_and_usage(zhipu_glm_module) -> None:
    provider = zhipu_glm_module.ZhipuGLMLLMProvider(
        config={"api_key": "k", "default_model": "glm-4.6"}
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "glm-4.6"
        return httpx.Response(
            200,
            json={
                "model": "glm-4.6",
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "Ni hao."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 2,
                    "total_tokens": 6,
                },
            },
        )

    _install_mock_transport(provider, _handler)
    response = await provider.complete(
        CompletionRequest(
            model="glm-4.6",
            messages=[Message(role=MessageRole.USER, content="hi")],
        )
    )
    assert response.text == "Ni hao."
    assert response.usage.total_tokens == 6
    # Pay-as-you-go has fallback per-token pricing → cost is reported.
    assert response.cost_estimate_usd is not None
    assert response.cost_estimate_usd > 0


@pytest.mark.asyncio
async def test_subscription_plan_reports_zero_cost(zhipu_glm_module) -> None:
    provider = zhipu_glm_module.ZhipuGLMLLMProvider(
        config={"api_key": "k", "plan": "coding-plan", "default_model": "glm-4.6"}
    )
    _install_mock_transport(
        provider,
        lambda r: httpx.Response(
            200,
            json={
                "model": "glm-4.6",
                "choices": [
                    {"message": {"content": "ok"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        ),
    )
    response = await provider.complete(
        CompletionRequest(
            model="glm-4.6",
            messages=[Message(role=MessageRole.USER, content="hi")],
        )
    )
    assert response.cost_estimate_usd == 0.0


@pytest.mark.asyncio
async def test_stream_parses_sse(zhipu_glm_module) -> None:
    provider = zhipu_glm_module.ZhipuGLMLLMProvider(config={"api_key": "k"})
    body = b"\n".join(
        [
            b'data: {"choices":[{"delta":{"content":"hello"}}]}',
            b'data: {"choices":[{"delta":{"content":" world"}}]}',
            b"data: [DONE]",
            b"",
        ]
    )
    _install_mock_transport(
        provider,
        lambda r: httpx.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        ),
    )
    chunks = [
        c
        async for c in provider.stream(
            CompletionRequest(
                model="glm-4.6",
                messages=[Message(role=MessageRole.USER, content="hi")],
            )
        )
    ]
    assert [c.delta for c in chunks if not c.is_final] == ["hello", " world"]
    assert chunks[-1].is_final


@pytest.mark.asyncio
async def test_health_check_unconfigured_without_key(zhipu_glm_module) -> None:
    provider = zhipu_glm_module.ZhipuGLMLLMProvider()
    status = await provider.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_list_models_falls_back_when_endpoint_missing(zhipu_glm_module) -> None:
    provider = zhipu_glm_module.ZhipuGLMLLMProvider(config={"api_key": "k"})
    _install_mock_transport(provider, lambda r: httpx.Response(404, text="nope"))
    models = await provider.list_models()
    ids = {m.id for m in models}
    assert "glm-4.6" in ids
