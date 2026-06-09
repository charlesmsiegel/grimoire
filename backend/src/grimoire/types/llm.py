"""LLM Gateway types: completion requests, chunks, capabilities."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from .common import Json


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    role: MessageRole
    content: str
    name: str | None = None  # for tool messages
    # Provider-neutral prompt-cache hint. When True, providers that support
    # explicit caching (e.g. Anthropic) mark a cache breakpoint at this
    # message so the stable prefix up to and including it is cached across
    # turns. Providers without explicit caching (most OpenAI-compatible
    # endpoints cache automatically) ignore it. Defaults off.
    cache: bool = False
    metadata: Json = Field(default_factory=dict)


class ModelParams(BaseModel):
    temperature: float = 1.0
    max_tokens: int = 4096
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] = Field(default_factory=list)
    seed: int | None = None
    extra: Json = Field(default_factory=dict)


class ProviderCapabilities(BaseModel):
    streaming: bool = False
    tools: bool = False
    vision: bool = False
    max_context: int = 0
    embeddings: bool = False


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    # Prompt-caching breakdown (providers that report it). ``input_tokens``
    # counts only the *uncached* prompt tokens; cache reads/writes are
    # surfaced separately so cost accounting and the observability view can
    # show how much caching saved. Both default to 0 when unsupported.
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


class ModelInfo(BaseModel):
    id: str
    name: str
    context_window: int = 0
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    capabilities: ProviderCapabilities | None = None
    dimensions: int | None = None


class CompletionRequest(BaseModel):
    model: str
    messages: list[Message]
    system: str | None = None
    max_tokens: int = 4096
    temperature: float = 1.0
    stop_sequences: list[str] = Field(default_factory=list)
    # Some providers honor ``seed`` for deterministic replay (often-but-
    # not-always — surface that caveat in the replay UI). The gateway
    # forwards this verbatim to the provider; providers that don't
    # support it ignore it.
    seed: int | None = None
    metadata: Json = Field(default_factory=dict)


class CompletionResponse(BaseModel):
    text: str
    model: str
    finish_reason: str  # 'stop', 'length', 'content_filter', 'tool_use'
    usage: TokenUsage = Field(default_factory=TokenUsage)
    raw: Json = Field(default_factory=dict)
    cost_estimate_usd: float | None = None
    latency_ms: int = 0


class CompletionChunk(BaseModel):
    delta: str
    is_final: bool = False
    usage: TokenUsage | None = None
    # Actual charge for the whole call (USD) as reported by the provider
    # (e.g. OpenRouter usage accounting), set on the final chunk when known.
    # None means "not reported" — the gateway then estimates from catalog
    # pricing, mirroring how `CompletionResponse.cost_estimate_usd` works.
    cost_estimate_usd: float | None = None


class RetryPolicy(BaseModel):
    max_retries: int = 3
    initial_delay_ms: int = 500
    backoff_factor: float = 2.0
    retry_on: list[str] = Field(
        default_factory=lambda: ["TimeoutError", "RateLimitError", "TransientError"]
    )


class TimeoutPolicy(BaseModel):
    total_seconds: float = 120
    first_token_seconds: float = 30


class LLMCallRecord(BaseModel):
    """Audit record for one LLM call. Persisted by Observability."""

    id: str
    task: str
    provider_id: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float = 0.0
    latency_ms: int
    finish_reason: str
    campaign_id: str | None = None
    turn_id: str | None = None
    error: str | None = None
