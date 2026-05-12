"""LLM Gateway types: completion requests, chunks, capabilities."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .common import Json


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass
class Message:
    role: MessageRole
    content: str
    name: str | None = None  # for tool messages
    metadata: Json = field(default_factory=dict)


@dataclass
class ModelParams:
    temperature: float = 1.0
    max_tokens: int = 4096
    top_p: float | None = None
    top_k: int | None = None
    stop_sequences: list[str] = field(default_factory=list)
    seed: int | None = None
    extra: Json = field(default_factory=dict)


@dataclass
class ProviderCapabilities:
    streaming: bool = False
    tools: bool = False
    vision: bool = False
    max_context: int = 0
    embeddings: bool = False


@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ModelInfo:
    id: str
    name: str
    context_window: int = 0
    input_cost_per_1k: float | None = None
    output_cost_per_1k: float | None = None
    capabilities: ProviderCapabilities | None = None


@dataclass
class CompletionRequest:
    model: str
    messages: list[Message]
    system: str | None = None
    max_tokens: int = 4096
    temperature: float = 1.0
    stop_sequences: list[str] = field(default_factory=list)
    metadata: Json = field(default_factory=dict)


@dataclass
class CompletionResponse:
    text: str
    model: str
    finish_reason: str  # 'stop', 'length', 'content_filter', 'tool_use'
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw: Json = field(default_factory=dict)
    cost_estimate_usd: float | None = None
    latency_ms: int = 0


@dataclass
class CompletionChunk:
    delta: str
    is_final: bool = False
    usage: TokenUsage | None = None


@dataclass
class RetryPolicy:
    max_retries: int = 3
    initial_delay_ms: int = 500
    backoff_factor: float = 2.0
    retry_on: list[str] = field(
        default_factory=lambda: ["TimeoutError", "RateLimitError", "TransientError"]
    )


@dataclass
class TimeoutPolicy:
    total_seconds: float = 120
    first_token_seconds: float = 30


@dataclass
class LLMCallRecord:
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
