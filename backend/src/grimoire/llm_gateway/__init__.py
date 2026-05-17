"""LLM Gateway: routes tasks to LLM and embedding providers.

The gateway lives between consumer modules (Context Builder, Extractor,
Continuity, Time Engine, etc.) and the per-provider plugins discovered by
the Plugins module. It owns routing, retries, fallback, the embedding
cache, and the audit log written to `llm_requests`.
"""

from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
)
from grimoire.llm_gateway.errors import (
    AuthenticationError,
    ContentFilterError,
    GatewayError,
    InvalidRequestError,
    PermanentError,
    ProviderNotFoundError,
    RateLimitError,
    RouteNotFoundError,
    TransientError,
)
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.llm_gateway.routing import Route, RouteResolver
from grimoire.types.llm import RetryPolicy, TimeoutPolicy

__all__ = [
    "AuthenticationError",
    "ContentFilterError",
    "EmbeddingCacheConfig",
    "GatewayConfig",
    "GatewayError",
    "InvalidRequestError",
    "LLMGatewayService",
    "ObservabilityConfig",
    "PermanentError",
    "ProviderNotFoundError",
    "RateLimitError",
    "RetryPolicy",
    "Route",
    "RouteNotFoundError",
    "RouteResolver",
    "TimeoutPolicy",
    "TransientError",
]
