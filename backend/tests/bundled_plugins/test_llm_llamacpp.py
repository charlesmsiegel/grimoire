"""Conformance and behavior tests for the bundled `llm-llamacpp` plugin.

`llama-cpp-python` is not a runtime dependency of the backend, so the
tests substitute a fake `llama_cpp` module via `sys.modules` to exercise
the provider's prompt assembly, streaming bridge, and health checks
without loading a real model file.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from grimoire.types.common import HealthLevel
from grimoire.types.llm import CompletionRequest, Message, MessageRole
from grimoire.types.plugins import PluginKind

from .conftest import load_bundled


def test_manifest_loads_and_protocol_satisfied(tmp_path: Path) -> None:
    model_file = tmp_path / "fake-model.gguf"
    model_file.write_bytes(b"GGUF")
    result = load_bundled("llm-llamacpp", config={"model_path": str(model_file)})
    assert result.ok, result.errors
    manifest = result.manifest
    assert manifest is not None
    assert manifest.id == "llm-llamacpp"
    assert PluginKind.LLM_PROVIDER in manifest.implements
    assert manifest.classes[PluginKind.LLM_PROVIDER.value] == "LlamaCppLLMProvider"
    assert manifest.requirements
    assert any(r.startswith("llama-cpp-python") for r in manifest.requirements)
    provider = result.instances[0].instance
    assert provider.id == "llamacpp"
    assert provider.capabilities.streaming is True
    assert provider.capabilities.max_context == 8192


@pytest.mark.asyncio
async def test_health_check_paths(tmp_path: Path) -> None:
    provider_no_path = _instantiate_class({})
    assert (await provider_no_path.health_check()).level == HealthLevel.UNCONFIGURED

    missing = tmp_path / "missing.gguf"
    provider_missing = _instantiate_class({"model_path": str(missing)})
    assert (await provider_missing.health_check()).level == HealthLevel.UNHEALTHY

    real = tmp_path / "present.gguf"
    real.write_bytes(b"GGUF")
    _install_fake_llama_cpp(record=[])
    try:
        provider_ok = _instantiate_class({"model_path": str(real)})
        status = await provider_ok.health_check()
        assert status.level == HealthLevel.HEALTHY
    finally:
        sys.modules.pop("llama_cpp", None)


@pytest.mark.asyncio
async def test_complete_passes_prompt_and_parses_usage(tmp_path: Path) -> None:
    model_file = tmp_path / "fake-model.gguf"
    model_file.write_bytes(b"GGUF")
    captured: list[dict[str, Any]] = []
    _install_fake_llama_cpp(
        record=captured,
        response={
            "choices": [{"text": "the answer is 42", "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 5, "total_tokens": 12},
        },
    )
    try:
        provider = _instantiate_class({"model_path": str(model_file), "n_ctx": 4096})
        request = CompletionRequest(
            model="local-test",
            messages=[Message(role=MessageRole.USER, content="What is the answer?")],
            system="you are a helpful oracle",
            max_tokens=32,
            temperature=0.4,
            stop_sequences=["</done>"],
        )
        response = await provider.complete(request)
    finally:
        sys.modules.pop("llama_cpp", None)

    assert response.text == "the answer is 42"
    assert response.finish_reason == "stop"
    assert response.usage.input_tokens == 7
    assert response.usage.output_tokens == 5
    assert response.usage.total_tokens == 12
    assert response.latency_ms >= 0
    assert captured, "fake llama was not called"
    call = captured[-1]
    assert call["stop"] == ["</done>"]
    assert call["max_tokens"] == 32
    assert call["temperature"] == pytest.approx(0.4)
    assert "<|system|>" in call["prompt"]
    assert "<|user|>" in call["prompt"]
    assert call["prompt"].endswith("<|assistant|>\n")


@pytest.mark.asyncio
async def test_stream_bridges_generator_chunks(tmp_path: Path) -> None:
    model_file = tmp_path / "fake-model.gguf"
    model_file.write_bytes(b"GGUF")
    pieces = [
        {"choices": [{"text": "Hello"}]},
        {"choices": [{"text": " "}]},
        {"choices": [{"text": "world"}]},
    ]
    _install_fake_llama_cpp(record=[], stream_pieces=pieces)
    try:
        provider = _instantiate_class({"model_path": str(model_file)})
        request = CompletionRequest(
            model="local-test",
            messages=[Message(role=MessageRole.USER, content="Hi")],
        )
        chunks = [c async for c in provider.stream(request)]
    finally:
        sys.modules.pop("llama_cpp", None)
    deltas = [c.delta for c in chunks if not c.is_final]
    assert deltas == ["Hello", " ", "world"]
    assert chunks[-1].is_final


@pytest.mark.asyncio
async def test_estimate_tokens_uses_tokenizer_when_loaded(tmp_path: Path) -> None:
    model_file = tmp_path / "fake-model.gguf"
    model_file.write_bytes(b"GGUF")
    _install_fake_llama_cpp(record=[], tokens_per_call=[1, 2, 3, 4])
    try:
        provider = _instantiate_class({"model_path": str(model_file)})
        count = await provider.estimate_tokens("hello world")
        assert count == 4
    finally:
        sys.modules.pop("llama_cpp", None)


@pytest.mark.asyncio
async def test_estimate_tokens_falls_back_without_path() -> None:
    provider = _instantiate_class({})
    # No model_path → heuristic only.
    assert await provider.estimate_tokens("") == 0
    assert await provider.estimate_tokens("a" * 16) >= 1


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _instantiate_class(config: dict[str, Any]) -> Any:
    """Load plugin.py in isolation and instantiate `LlamaCppLLMProvider`.

    Skips the loader's manifest validation so we can pass partial configs
    (the loader rejects configs missing `model_path` against the schema).
    """
    from .conftest import _import_plugin

    module = _import_plugin("llm-llamacpp")
    return module.LlamaCppLLMProvider(config=config)


def _install_fake_llama_cpp(
    *,
    record: list[dict[str, Any]],
    response: dict[str, Any] | None = None,
    stream_pieces: list[dict[str, Any]] | None = None,
    tokens_per_call: list[int] | None = None,
) -> None:
    response = response or {
        "choices": [{"text": "", "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

    class _FakeLlama:
        def __init__(self, **kwargs: Any) -> None:
            self.init_kwargs = kwargs

        def __call__(self, prompt: str, **kwargs: Any) -> Any:
            record.append({"prompt": prompt, **kwargs})
            if kwargs.get("stream"):
                pieces = list(stream_pieces or [])

                def _gen() -> Any:
                    yield from pieces

                return _gen()
            return response

        def tokenize(self, text: bytes) -> list[int]:
            return list(tokens_per_call or [1] * max(1, len(text) // 4))

    module = types.ModuleType("llama_cpp")
    module.Llama = _FakeLlama  # type: ignore[attr-defined]
    sys.modules["llama_cpp"] = module
