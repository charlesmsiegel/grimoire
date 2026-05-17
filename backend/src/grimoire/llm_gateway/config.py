"""Configuration dataclasses for the LLM Gateway.

Mirrors the YAML structure in spec 05 §Configuration. Defaults are picked
so a fresh install with the `llm-anthropic` and `embed-sentence-transformers`
plugins works without extra setup, but the gateway is happy with empty
routes (everything will then raise `RouteNotFoundError` until the user
configures one).

The retry and timeout policies use the canonical pydantic types from
:mod:`grimoire.types.llm` (``RetryPolicy`` / ``TimeoutPolicy``) so that
``retry_on`` is YAML-configurable and there is a single source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from grimoire.types.llm import RetryPolicy, TimeoutPolicy


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
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    timeout: TimeoutPolicy = field(default_factory=TimeoutPolicy)
    embedding_cache: EmbeddingCacheConfig = field(default_factory=EmbeddingCacheConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
