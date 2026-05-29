"""Anthropic Messages API adapter for Grimoire's LLM Gateway.

The class is imported by the plugin loader (see `grimoire.plugins.loader`)
and registered against the `LLMProvider` protocol. The `anthropic` SDK is
imported lazily so the plugin can be discovered and listed even when the
optional dependency is not installed.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

from grimoire.types.common import HealthLevel, HealthStatus
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    ModelInfo,
    ProviderCapabilities,
    TokenUsage,
)

# Per-1k-token pricing (USD) for the models we know about. Anything missing
# leaves `cost_estimate_usd` unset and the Gateway will fall back to a token
# count for budgeting.
_PRICING_PER_1K: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_KNOWN_MODELS: tuple[ModelInfo, ...] = (
    ModelInfo(
        id="claude-opus-4-7",
        name="Claude Opus 4.7",
        context_window=200_000,
        input_cost_per_1k=_PRICING_PER_1K["claude-opus-4-7"][0],
        output_cost_per_1k=_PRICING_PER_1K["claude-opus-4-7"][1],
    ),
    ModelInfo(
        id="claude-sonnet-4-6",
        name="Claude Sonnet 4.6",
        context_window=200_000,
        input_cost_per_1k=_PRICING_PER_1K["claude-sonnet-4-6"][0],
        output_cost_per_1k=_PRICING_PER_1K["claude-sonnet-4-6"][1],
    ),
    ModelInfo(
        id="claude-haiku-4-5",
        name="Claude Haiku 4.5",
        context_window=200_000,
        input_cost_per_1k=_PRICING_PER_1K["claude-haiku-4-5"][0],
        output_cost_per_1k=_PRICING_PER_1K["claude-haiku-4-5"][1],
    ),
)


def _role(role: Any) -> str:
    return role.value if hasattr(role, "value") else str(role)


class AnthropicLLMProvider:
    id = "anthropic"
    name = "Anthropic"
    capabilities = ProviderCapabilities(
        streaming=True,
        tools=True,
        vision=True,
        max_context=200_000,
        embeddings=False,
    )

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config: dict[str, Any] = dict(config or {})
        self._api_key: str | None = self.config.get("api_key")
        self._base_url: str | None = self.config.get("base_url") or "https://api.anthropic.com"
        self._default_model: str = self.config.get("default_model") or "claude-opus-4-7"
        self._timeout: float = float(self.config.get("timeout_seconds") or 120)
        self._client: Any = None

    # ------------------------------------------------------------------ #
    # Client lifecycle
    # ------------------------------------------------------------------ #

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not self._api_key:
            raise RuntimeError("anthropic provider: missing api_key in config")
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised by integration
            raise RuntimeError(
                "anthropic SDK not installed; add `anthropic` to the plugin's venv"
            ) from exc
        self._client = anthropic.AsyncAnthropic(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=self._timeout,
        )
        return self._client

    # ------------------------------------------------------------------ #
    # LLMProvider protocol
    # ------------------------------------------------------------------ #

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = self._get_client()
        kwargs = self._build_kwargs(request)
        start = time.monotonic()
        response = await client.messages.create(**kwargs)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        text = _extract_text(response)
        usage = _usage_from_response(response)
        model_id = getattr(response, "model", None) or request.model
        return CompletionResponse(
            text=text,
            model=model_id,
            finish_reason=str(getattr(response, "stop_reason", None) or "stop"),
            usage=usage,
            raw=_to_raw(response),
            cost_estimate_usd=_estimate_cost(usage, model_id),
            latency_ms=elapsed_ms,
        )

    def stream(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        return self._stream_impl(request)

    async def _stream_impl(self, request: CompletionRequest) -> AsyncIterator[CompletionChunk]:
        client = self._get_client()
        kwargs = self._build_kwargs(request)
        async with client.messages.stream(**kwargs) as stream:
            async for delta in stream.text_stream:
                if delta:
                    yield CompletionChunk(delta=delta, is_final=False)
            final = await stream.get_final_message()
        usage = _usage_from_response(final)
        yield CompletionChunk(delta="", is_final=True, usage=usage)

    async def list_models(self) -> list[ModelInfo]:
        return list(_KNOWN_MODELS)

    async def estimate_tokens(self, text: str) -> int:
        # Anthropic exposes a count_tokens beta endpoint, but it requires a
        # live API call. A char/4 heuristic is good enough for budgeting and
        # keeps token estimation offline.
        return max(1, len(text) // 4)

    async def health_check(self) -> HealthStatus:
        if not self._api_key:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=self.id,
                message="api_key not configured",
            )
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=self.id,
                message="anthropic SDK not installed",
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=self.id,
            message="api_key configured",
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _build_kwargs(self, request: CompletionRequest) -> dict[str, Any]:
        # Anthropic requires the system prompt as a top-level ``system`` param;
        # ``system``-role messages are NOT valid inside ``messages``. Hoist them
        # out, preserving order, and apply a single ``cache_control`` breakpoint
        # at the last system block that requested caching (Anthropic caches the
        # prefix up to and including the marked block).
        system_blocks: list[dict[str, Any]] = []
        cache_break_idx: int | None = None
        if request.system:
            system_blocks.append({"type": "text", "text": request.system})
        convo: list[dict[str, Any]] = []
        for m in request.messages:
            role = _role(m.role)
            if role == "system":
                system_blocks.append({"type": "text", "text": m.content})
                if m.cache:
                    cache_break_idx = len(system_blocks) - 1
                continue
            if m.cache:
                convo.append(
                    {
                        "role": role,
                        "content": [
                            {
                                "type": "text",
                                "text": m.content,
                                "cache_control": {"type": "ephemeral"},
                            }
                        ],
                    }
                )
            else:
                convo.append({"role": role, "content": m.content})

        if cache_break_idx is not None:
            system_blocks[cache_break_idx] = {
                **system_blocks[cache_break_idx],
                "cache_control": {"type": "ephemeral"},
            }

        kwargs: dict[str, Any] = {
            "model": request.model or self._default_model,
            "messages": convo,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
        }
        if system_blocks:
            # Plain-string form when there's a single uncached block (back-compat
            # and smaller payload); block-list form when caching or multi-block.
            if len(system_blocks) == 1 and "cache_control" not in system_blocks[0]:
                kwargs["system"] = system_blocks[0]["text"]
            else:
                kwargs["system"] = system_blocks
        if request.stop_sequences:
            kwargs["stop_sequences"] = list(request.stop_sequences)
        return kwargs


def _extract_text(response: Any) -> str:
    content = getattr(response, "content", None) or []
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if text:
            parts.append(str(text))
    return "".join(parts)


def _usage_from_response(response: Any) -> TokenUsage:
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
    # With prompt caching, ``input_tokens`` counts only uncached prompt tokens;
    # cache reads/writes are reported separately.
    cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    cache_creation = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens + cache_read + cache_creation,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )


def _to_raw(response: Any) -> dict[str, Any]:
    dump = getattr(response, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump())
        except Exception:  # pragma: no cover - defensive
            return {}
    if isinstance(response, dict):
        return dict(response)
    return {}


def _estimate_cost(usage: TokenUsage, model: str) -> float | None:
    pricing = _PRICING_PER_1K.get(model)
    if pricing is None:
        return None
    input_rate, output_rate = pricing
    # Anthropic prompt-cache pricing: cache writes cost 1.25x the base input
    # rate, cache reads 0.1x. Plain ``input_tokens`` already excludes cached
    # tokens, so the three terms don't overlap.
    cost = (usage.input_tokens / 1000.0) * input_rate
    cost += (usage.cache_creation_input_tokens / 1000.0) * input_rate * 1.25
    cost += (usage.cache_read_input_tokens / 1000.0) * input_rate * 0.1
    cost += (usage.output_tokens / 1000.0) * output_rate
    return cost


__all__ = ["AnthropicLLMProvider"]
