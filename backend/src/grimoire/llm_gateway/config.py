"""Configuration dataclasses for the LLM Gateway.

Mirrors the YAML structure in spec 05 §Configuration. Defaults are picked
so a fresh install with the `llm-anthropic` and `embed-sentence-transformers`
plugins works without extra setup, but the gateway is happy with empty
routes (everything will then raise `RouteNotFoundError` until the user
configures one).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetryConfig:
    max_retries: int = 3
    initial_delay_ms: int = 500
    backoff_factor: float = 2.0


@dataclass(frozen=True)
class TimeoutConfig:
    total_seconds: float = 120.0
    first_token_seconds: float = 30.0


@dataclass(frozen=True)
class EmbeddingCacheConfig:
    enabled: bool = True
    max_entries: int = 100_000


@dataclass(frozen=True)
class ObservabilityConfig:
    log_all_requests: bool = True
    log_response_text: bool = False  # privacy default
    response_excerpt_chars: int = 200


@dataclass(frozen=True)
class GatewayConfig:
    default_routes: dict[str, str] = field(default_factory=dict)
    fallback_routes: dict[str, str] = field(default_factory=dict)
    retry: RetryConfig = field(default_factory=RetryConfig)
    timeout: TimeoutConfig = field(default_factory=TimeoutConfig)
    embedding_cache: EmbeddingCacheConfig = field(default_factory=EmbeddingCacheConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
