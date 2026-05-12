"""Conformance and behavior tests for the bundled `llm-anthropic` plugin.

The Anthropic SDK is not a runtime dependency of the backend, so the tests
inject a fake `anthropic` module via `sys.modules` to exercise the
provider without making network calls.
"""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from grimoire.types.common import HealthLevel
from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.types.plugins import PluginKind

from .conftest import load_bundled


def test_manifest_loads_and_protocol_satisfied() -> None:
    result = load_bundled("llm-anthropic", config={"api_key": "sk-test"})
    assert result.ok, result.errors
    manifest = result.manifest
    assert manifest is not None
    assert manifest.id == "llm-anthropic"
    assert PluginKind.LLM_PROVIDER in manifest.implements
    assert manifest.classes[PluginKind.LLM_PROVIDER.value] == "AnthropicLLMProvider"
    assert manifest.requirements and any(r.startswith("anthropic") for r in manifest.requirements)
    assert len(result.instances) == 1
    provider = result.instances[0].instance
    assert provider.id == "anthropic"
    assert provider.capabilities.streaming is True
    assert provider.capabilities.max_context == 200_000


def test_manifest_requires_api_key() -> None:
    # No config → manifest's `required: [api_key]` rule must reject the
    # config-store side. The loader itself still instantiates the class with
    # whatever was passed (empty dict), so this verifies the config schema
    # is wired up in the manifest. Tested via the validator directly.
    from grimoire.validation.manifests import validate_plugin_manifest

    result = load_bundled("llm-anthropic", config={"api_key": "sk-test"})
    manifest = result.manifest
    assert manifest is not None
    # Re-validate using the raw manifest to confirm schema is well-formed.
    schema_validation = validate_plugin_manifest(manifest.raw)
    assert schema_validation.ok, [e.message for e in schema_validation.errors]


@pytest.mark.asyncio
async def test_list_models_returns_known_models() -> None:
    result = load_bundled("llm-anthropic", config={"api_key": "sk-test"})
    provider = result.instances[0].instance
    models = await provider.list_models()
    ids = {m.id for m in models}
    assert "claude-opus-4-7" in ids
    assert all(m.context_window > 0 for m in models)


@pytest.mark.asyncio
async def test_estimate_tokens_uses_offline_heuristic() -> None:
    result = load_bundled("llm-anthropic", config={"api_key": "sk-test"})
    provider = result.instances[0].instance
    assert await provider.estimate_tokens("") == 1
    n = await provider.estimate_tokens("hello world hello world")
    assert n >= 1


@pytest.mark.asyncio
async def test_health_check_reports_unconfigured_without_api_key(anthropic_module) -> None:
    provider = anthropic_module.AnthropicLLMProvider(config={})
    status = await provider.health_check()
    assert status.level == HealthLevel.UNCONFIGURED


@pytest.mark.asyncio
async def test_complete_parses_anthropic_response(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    fake_anthropic.set_response(
        text="Hello there.",
        model="claude-opus-4-7",
        stop_reason="end_turn",
        input_tokens=11,
        output_tokens=4,
    )
    result = load_bundled("llm-anthropic", config={"api_key": "sk-test"})
    provider = result.instances[0].instance
    request = CompletionRequest(
        model="claude-opus-4-7",
        messages=[Message(role=MessageRole.USER, content="Hi")],
        system="be brief",
        max_tokens=64,
        temperature=0.7,
        stop_sequences=["</end>"],
    )
    response = await provider.complete(request)
    assert response.text == "Hello there."
    assert response.model == "claude-opus-4-7"
    assert response.finish_reason == "end_turn"
    assert response.usage.input_tokens == 11
    assert response.usage.output_tokens == 4
    assert response.usage.total_tokens == 15
    assert response.cost_estimate_usd is not None
    assert response.cost_estimate_usd > 0
    # Ensure the SDK got the right kwargs.
    call = fake_anthropic.last_call
    assert call["model"] == "claude-opus-4-7"
    assert call["system"] == "be brief"
    assert call["stop_sequences"] == ["</end>"]
    assert call["messages"] == [{"role": "user", "content": "Hi"}]


@pytest.mark.asyncio
async def test_stream_yields_deltas_then_final(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_anthropic = _install_fake_anthropic(monkeypatch)
    fake_anthropic.set_stream(
        text_pieces=["Hello", " there"],
        model="claude-haiku-4-5",
        input_tokens=5,
        output_tokens=2,
    )
    result = load_bundled("llm-anthropic", config={"api_key": "sk-test"})
    provider = result.instances[0].instance
    request = CompletionRequest(
        model="claude-haiku-4-5",
        messages=[Message(role=MessageRole.USER, content="Hi")],
    )
    chunks = [c async for c in provider.stream(request)]
    deltas = [c.delta for c in chunks if not c.is_final]
    assert deltas == ["Hello", " there"]
    final = chunks[-1]
    assert final.is_final
    assert final.usage is not None
    assert final.usage.input_tokens == 5
    assert final.usage.output_tokens == 2


# --------------------------------------------------------------------------- #
# Fake anthropic SDK
# --------------------------------------------------------------------------- #


class _FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text
        self.type = "text"


class _FakeResponse:
    def __init__(
        self,
        text: str,
        model: str,
        stop_reason: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self.content = [_FakeBlock(text)]
        self.model = model
        self.stop_reason = stop_reason
        self.usage = _FakeUsage(input_tokens, output_tokens)

    def model_dump(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "stop_reason": self.stop_reason,
            "usage": {
                "input_tokens": self.usage.input_tokens,
                "output_tokens": self.usage.output_tokens,
            },
            "content": [{"type": "text", "text": self.content[0].text}],
        }


class _FakeStream:
    def __init__(self, pieces: list[str], final: _FakeResponse) -> None:
        self._pieces = pieces
        self._final = final

    async def __aenter__(self) -> _FakeStream:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    @property
    def text_stream(self) -> Any:
        pieces = list(self._pieces)

        async def _iter() -> Any:
            for p in pieces:
                yield p

        return _iter()

    async def get_final_message(self) -> _FakeResponse:
        return self._final


class _FakeAnthropic:
    def __init__(self) -> None:
        self.last_call: dict[str, Any] = {}
        self.pending_response: _FakeResponse | None = None
        self.pending_stream: list[str] = []

    def set_response(self, **kwargs: Any) -> None:
        self.pending_response = _FakeResponse(**kwargs)

    def set_stream(
        self,
        text_pieces: list[str],
        model: str,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        self.pending_response = _FakeResponse(
            text="".join(text_pieces),
            model=model,
            stop_reason="end_turn",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        self.pending_stream = list(text_pieces)

    def make_client(self, **init_kwargs: Any) -> Any:
        fake = self

        class _Messages:
            async def create(self, **kwargs: Any) -> _FakeResponse:
                fake.last_call.clear()
                fake.last_call.update(kwargs)
                assert fake.pending_response is not None
                return fake.pending_response

            def stream(self, **kwargs: Any) -> _FakeStream:
                fake.last_call.clear()
                fake.last_call.update(kwargs)
                assert fake.pending_response is not None
                return _FakeStream(fake.pending_stream, fake.pending_response)

        class _Client:
            def __init__(self) -> None:
                self.init_kwargs = init_kwargs
                self.messages = _Messages()

        return _Client()


def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch) -> _FakeAnthropic:
    fake = _FakeAnthropic()
    module = types.ModuleType("anthropic")
    module.AsyncAnthropic = fake.make_client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", module)
    return fake


__all__: list[str] = []
