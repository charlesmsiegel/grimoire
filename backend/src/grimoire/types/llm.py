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
    cost_usd: float | None
    latency_ms: int
    finish_reason: str
    campaign_id: str | None = None
    branch_id: str | None = None
    turn_id: str | None = None
    error: str | None = None
