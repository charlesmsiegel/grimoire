"""Tests for the bundled `llm-openrouter` plugin.

HTTP traffic is intercepted with `httpx.MockTransport` so no network call
is made. The handler asserts on the wire shape that OpenRouter expects
(OpenAI chat-completions) and returns canned responses.
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
    result = load_bundled("llm-openrouter", config={"api_key": "sk-or-test"})
    assert result.ok, result.errors
    manifest = result.manifest
    assert manifest is not None
    assert manifest.id == "llm-openrouter"
    assert PluginKind.LLM_PROVIDER in manifest.implements
    provider = result.instances[0].instance
    assert provider.id == "openrouter"
    assert provider.capabilities.streaming is True


def test_defaults_picked_up_from_manifest(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    assert provider._base_url == "https://openrouter.ai/api/v1"
    assert provider._active_model == "anthropic/claude-sonnet-4-6"


def test_extra_headers_and_referer_applied(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(
        config={
            "api_key": "k",
            "http_referer": "https://example.test",
            "app_title": "MyApp",
            "extra_headers": {"x-trace": "abc"},
        }
    )
    assert provider._http_referer == "https://example.test"
    assert provider._app_title == "MyApp"
    assert provider._extra_headers == {"x-trace": "abc"}


@pytest.mark.asyncio
async def test_complete_posts_chat_completions(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(
        config={"api_key": "sk-or-x", "active_model": "openai/gpt-4o"}
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content.decode("utf-8"))
        assert body["model"] == "openai/gpt-4o"
        assert body["messages"] == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
        assert body["stream"] is False
        return httpx.Response(
            200,
            json={
                "id": "gen-1",
                "model": "openai/gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Hello there."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            },
        )

    requests = _install_mock_transport(provider, _handler)
    response = await provider.complete(
        CompletionRequest(
            model="openai/gpt-4o",
            messages=[Message(role=MessageRole.USER, content="hi")],
            system="be brief",
        )
    )
    assert response.text == "Hello there."
    assert response.model == "openai/gpt-4o"
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 3
    assert response.usage.total_tokens == 10
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_complete_raises_on_http_error(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    _install_mock_transport(provider, lambda r: httpx.Response(401, text='{"error":"bad key"}'))
    with pytest.raises(RuntimeError, match="401"):
        await provider.complete(
            CompletionRequest(
                model="x",
                messages=[Message(role=MessageRole.USER, content="hi")],
            )
        )


@pytest.mark.asyncio
async def test_complete_wraps_transport_error_as_transient(openrouter_module) -> None:
    """A connection-level failure must surface as the gateway's retriable
    TransientError, not a raw httpx error the gateway treats as permanent.

    Regression: a transient httpx.ConnectError escaped unwrapped, so the
    gateway's retry/fallback machinery never engaged and a momentary network
    blip aborted the whole turn.
    """
    from grimoire.llm_gateway.errors import TransientError

    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_mock_transport(provider, _boom)
    with pytest.raises(TransientError):
        await provider.complete(
            CompletionRequest(
                model="x",
                messages=[Message(role=MessageRole.USER, content="hi")],
            )
        )


@pytest.mark.asyncio
async def test_stream_wraps_transport_error_as_transient(openrouter_module) -> None:
    """A connection failure before any chunk must surface as TransientError so
    the gateway retries it (the campaign-killing ua-harem ConnectError)."""
    from grimoire.llm_gateway.errors import TransientError

    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})

    def _boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    _install_mock_transport(provider, _boom)
    with pytest.raises(TransientError):
        async for _ in provider.stream(
            CompletionRequest(model="x", messages=[Message(role=MessageRole.USER, content="hi")])
        ):
            pass


@pytest.mark.asyncio
async def test_stream_parses_sse_chunks(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})

    usage_event = (
        b'data: {"choices":[{"delta":{}}],'
        b'"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5}}'
    )
    body = b"\n".join(
        [
            b'data: {"choices":[{"delta":{"content":"Hello"}}]}',
            b'data: {"choices":[{"delta":{"content":" there"}}]}',
            usage_event,
            b"data: [DONE]",
            b"",
        ]
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    _install_mock_transport(provider, _handler)
    chunks = [
        c
        async for c in provider.stream(
            CompletionRequest(model="x", messages=[Message(role=MessageRole.USER, content="hi")])
        )
    ]
    deltas = [c.delta for c in chunks if not c.is_final]
    assert deltas == ["Hello", " there"]
    final = chunks[-1]
    assert final.is_final
    assert final.usage is not None
    assert final.usage.total_tokens == 5


@pytest.mark.asyncio
async def test_list_models_parses_pricing(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "openai/gpt-4o",
                        "name": "GPT-4o",
                        "context_length": 128000,
                        "pricing": {"prompt": "0.0025", "completion": "0.01"},
                    },
                    {"id": "no-pricing", "name": "no", "context_length": 4096},
                ]
            },
        )

    _install_mock_transport(provider, _handler)
    models = await provider.list_models()
    by_id = {m.id: m for m in models}
    assert by_id["openai/gpt-4o"].context_window == 128000
    # OpenRouter reports per-token; provider rescales to per-1k.
    assert by_id["openai/gpt-4o"].input_cost_per_1k == pytest.approx(2.5)
    assert by_id["openai/gpt-4o"].output_cost_per_1k == pytest.approx(10.0)
    assert by_id["no-pricing"].input_cost_per_1k is None


def _provider_of(provider, model: str) -> dict:
    """Resolve the `provider` routing object that would be sent for `model`."""
    request = CompletionRequest(
        model=model, messages=[Message(role=MessageRole.USER, content="hi")]
    )
    payload = provider._build_payload(request, stream=False)
    return payload.get("provider", {})


def test_default_provider_routing_is_cost_safe(openrouter_module) -> None:
    """With no provider config, every request gets the cost-safe default:
    prefer the cheapest provider and don't silently fall back to a pricier one.
    """
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    routing = _provider_of(provider, "openai/gpt-4o")
    assert routing["sort"] == "price"
    assert routing["allow_fallbacks"] is False


def test_usage_accounting_requested_for_cost_observability(openrouter_module) -> None:
    """Requests ask OpenRouter to return real cost so provider-price variance
    can be logged and diagnosed (catalog pricing hides per-provider variance).
    """
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    request = CompletionRequest(model="x", messages=[Message(role=MessageRole.USER, content="hi")])
    payload = provider._build_payload(request, stream=False)
    assert payload["usage"] == {"include": True}


def test_builtin_max_price_guard_for_deepseek_v4_pro(openrouter_module) -> None:
    """The known cost-variance model ships with a per-million price ceiling so a
    pricier provider can't silently serve it (issue #515)."""
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    routing = _provider_of(provider, "deepseek/deepseek-v4-pro")
    assert routing["allow_fallbacks"] is False
    assert routing["max_price"] == {"prompt": 0.435, "completion": 0.87}


def test_user_provider_config_overrides_default(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(
        config={"api_key": "k", "provider": {"only": ["deepinfra"], "allow_fallbacks": False}}
    )
    routing = _provider_of(provider, "openai/gpt-4o")
    assert routing == {"only": ["deepinfra"], "allow_fallbacks": False}


def test_user_provider_overrides_apply_per_model(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(
        config={
            "api_key": "k",
            "provider_overrides": {
                "anthropic/claude-opus-4-7": {"max_price": {"prompt": 15.0, "completion": 75.0}}
            },
        }
    )
    # The targeted model merges its ceiling over the default base.
    opus = _provider_of(provider, "anthropic/claude-opus-4-7")
    assert opus["sort"] == "price"
    assert opus["max_price"] == {"prompt": 15.0, "completion": 75.0}
    # User-supplied overrides replace the builtin set, so deepseek no longer
    # carries its builtin ceiling unless re-declared.
    assert "max_price" not in _provider_of(provider, "deepseek/deepseek-v4-pro")


def test_empty_provider_config_omits_routing(openrouter_module) -> None:
    """Setting `provider: {}` alone opts out of routing constraints entirely —
    including the builtin price guards — so the payload carries no `provider`
    key (original wire shape) for any model."""
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k", "provider": {}})
    for model in ("openai/gpt-4o", "deepseek/deepseek-v4-pro"):
        request = CompletionRequest(
            model=model, messages=[Message(role=MessageRole.USER, content="hi")]
        )
        payload = provider._build_payload(request, stream=False)
        assert "provider" not in payload, model


def test_explicit_overrides_still_apply_under_opt_out(openrouter_module) -> None:
    """`provider: {}` drops the builtin guards, but a user-supplied
    `provider_overrides` is explicit config and still applies."""
    provider = openrouter_module.OpenRouterLLMProvider(
        config={
            "api_key": "k",
            "provider": {},
            "provider_overrides": {"deepseek/deepseek-v4-pro": {"max_price": {"prompt": 1.0}}},
        }
    )
    assert _provider_of(provider, "openai/gpt-4o") == {}
    assert _provider_of(provider, "deepseek/deepseek-v4-pro") == {"max_price": {"prompt": 1.0}}


def test_provider_override_deep_merges_nested_objects(openrouter_module) -> None:
    """Overriding one `max_price` limit must not silently drop the default's
    other limit — nested routing objects merge field-by-field."""
    provider = openrouter_module.OpenRouterLLMProvider(
        config={
            "api_key": "k",
            "provider": {"max_price": {"prompt": 1.0, "completion": 2.0}},
            "provider_overrides": {"openai/gpt-4o": {"max_price": {"prompt": 0.5}}},
        }
    )
    routing = _provider_of(provider, "openai/gpt-4o")
    assert routing["max_price"] == {"prompt": 0.5, "completion": 2.0}


def test_usage_accounting_can_be_disabled(openrouter_module) -> None:
    """`usage_accounting: false` restores the standard chat-completions shape
    (with `provider: {}`) for strict OpenAI-compatible gateways that reject
    OpenRouter-only request fields."""
    provider = openrouter_module.OpenRouterLLMProvider(
        config={"api_key": "k", "usage_accounting": False, "provider": {}}
    )
    request = CompletionRequest(model="x", messages=[Message(role=MessageRole.USER, content="hi")])
    payload = provider._build_payload(request, stream=False)
    assert "usage" not in payload
    assert "provider" not in payload


@pytest.mark.asyncio
async def test_complete_prefers_actual_cost_over_catalog_estimate(openrouter_module) -> None:
    """`usage.cost` is what OpenRouter actually charged; it must reach
    `cost_estimate_usd` (which the gateway preserves and persists) instead of
    the catalog estimate — otherwise provider-price variance corrupts cost
    records even when the real number is in hand (issue #515)."""
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "deepseek/deepseek-v4-pro",
                            "name": "DeepSeek",
                            "context_length": 64000,
                            # Headline rate that would *under*-estimate the call.
                            "pricing": {"prompt": "0.0000004", "completion": "0.0000008"},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "deepseek/deepseek-v4-pro",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 500,
                    "total_tokens": 1500,
                    "cost": 0.064919,
                },
            },
        )

    _install_mock_transport(provider, _handler)
    await provider.list_models()  # prime the catalog cache with headline rates
    response = await provider.complete(
        CompletionRequest(
            model="deepseek/deepseek-v4-pro",
            messages=[Message(role=MessageRole.USER, content="hi")],
        )
    )
    assert response.cost_estimate_usd == pytest.approx(0.064919)


@pytest.mark.asyncio
async def test_complete_falls_back_to_estimate_without_actual_cost(openrouter_module) -> None:
    """Without `usage.cost` in the response, the catalog estimate still fills
    `cost_estimate_usd` as before."""
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "id": "openai/gpt-4o",
                            "name": "GPT-4o",
                            "context_length": 128000,
                            "pricing": {"prompt": "0.0025", "completion": "0.01"},
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "model": "openai/gpt-4o",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
            },
        )

    _install_mock_transport(provider, _handler)
    await provider.list_models()
    response = await provider.complete(
        CompletionRequest(
            model="openai/gpt-4o",
            messages=[Message(role=MessageRole.USER, content="hi")],
        )
    )
    expected = 1000 / 1000.0 * 2.5 + 100 / 1000.0 * 10.0
    assert response.cost_estimate_usd == pytest.approx(expected)


@pytest.mark.asyncio
async def test_stream_final_chunk_carries_actual_cost(openrouter_module) -> None:
    """The streamed usage event's `cost` rides the final chunk so the gateway's
    streaming path records the actual charge instead of a catalog estimate."""
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})

    usage_event = (
        b'data: {"choices":[{"delta":{}}],'
        b'"usage":{"prompt_tokens":2,"completion_tokens":3,"total_tokens":5,"cost":0.0123}}'
    )
    body = b"\n".join(
        [
            b'data: {"choices":[{"delta":{"content":"Hi"}}]}',
            usage_event,
            b"data: [DONE]",
            b"",
        ]
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    _install_mock_transport(provider, _handler)
    chunks = [
        c
        async for c in provider.stream(
            CompletionRequest(model="x", messages=[Message(role=MessageRole.USER, content="hi")])
        )
    ]
    final = chunks[-1]
    assert final.is_final
    assert final.cost_estimate_usd == pytest.approx(0.0123)


@pytest.mark.asyncio
async def test_complete_logs_usage_and_cost(openrouter_module, caplog) -> None:
    """After a completion we log provider/tokens/cost so a 4x provider-price
    surprise is visible in the logs immediately (issue #515)."""
    import logging

    provider = openrouter_module.OpenRouterLLMProvider(
        config={"api_key": "k", "active_model": "deepseek/deepseek-v4-pro"}
    )

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "gen-xyz",
                "model": "deepseek/deepseek-v4-pro",
                "provider": "Pricey Inc",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 33542,
                    "completion_tokens": 1884,
                    "total_tokens": 35426,
                    "cost": 0.064919,
                },
            },
        )

    _install_mock_transport(provider, _handler)
    with caplog.at_level(logging.INFO, logger=openrouter_module.logger.name):
        await provider.complete(
            CompletionRequest(
                model="deepseek/deepseek-v4-pro",
                messages=[Message(role=MessageRole.USER, content="hi")],
            )
        )
    record = next(r for r in caplog.records if "usage" in r.getMessage())
    msg = record.getMessage()
    assert "Pricey Inc" in msg
    assert "gen-xyz" in msg
    assert "33542" in msg
    assert "1884" in msg
    assert "0.064919" in msg


@pytest.mark.asyncio
async def test_health_check_unconfigured_without_key(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider()
    status = await provider.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_health_check_healthy_when_models_reachable(openrouter_module) -> None:
    provider = openrouter_module.OpenRouterLLMProvider(config={"api_key": "k"})
    _install_mock_transport(provider, lambda r: httpx.Response(200, json={"data": []}))
    status = await provider.health_check()
    assert status.level == HealthLevel.HEALTHY
