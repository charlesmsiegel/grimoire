"""Pydantic-settings model for the ``llm_gateway:`` config block.

Mirrors :class:`~grimoire.llm_gateway.config.GatewayConfig` so the app can
read gateway configuration from environment variables (``GRIMOIRE_LLM_GATEWAY_*``)
or from the application's YAML/TOML settings file.

Usage::

    from grimoire.llm_gateway.settings import GatewaySettings

    s = GatewaySettings(default_routes={"main": "anthropic.claude-opus-4-7"})
    config: GatewayConfig = s.to_gateway_config()

The settings class intentionally uses plain dicts and primitive types so that
environment-variable injection (which delivers everything as strings or JSON)
works without extra parsing gymnastics.  ``to_gateway_config()`` converts to
the frozen dataclass hierarchy.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from grimoire.llm_gateway.config import (
    EmbeddingCacheConfig,
    GatewayConfig,
    ObservabilityConfig,
    PricingOverride,
)
from grimoire.types.llm import RetryPolicy, TimeoutPolicy


class _RetrySettings(BaseModel):
    max_retries: int = 3
    initial_delay_ms: int = 500
    backoff_factor: float = 2.0
    retry_on: list[str] = Field(
        default_factory=lambda: ["TimeoutError", "RateLimitError", "TransientError"]
    )

    def to_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_retries=self.max_retries,
            initial_delay_ms=self.initial_delay_ms,
            backoff_factor=self.backoff_factor,
            retry_on=list(self.retry_on),
        )


class _TimeoutSettings(BaseModel):
    total_seconds: float = 120.0
    first_token_seconds: float = 30.0

    def to_policy(self) -> TimeoutPolicy:
        return TimeoutPolicy(
            total_seconds=self.total_seconds,
            first_token_seconds=self.first_token_seconds,
        )


class _EmbeddingCacheSettings(BaseModel):
    enabled: bool = True
    max_entries: int = 100_000

    def to_dataclass(self) -> EmbeddingCacheConfig:
        return EmbeddingCacheConfig(
            enabled=self.enabled,
            max_entries=self.max_entries,
        )


class _ObservabilitySettings(BaseModel):
    log_all_requests: bool = True
    log_response_text: bool = False
    response_excerpt_chars: int = 200

    def to_dataclass(self) -> ObservabilityConfig:
        return ObservabilityConfig(
            log_all_requests=self.log_all_requests,
            log_response_text=self.log_response_text,
            response_excerpt_chars=self.response_excerpt_chars,
        )


class _PricingOverrideSettings(BaseModel):
    input_cost_per_1k: float
    output_cost_per_1k: float


class GatewaySettings(BaseModel):
    """Pydantic representation of the ``llm_gateway:`` settings block.

    Fields mirror :class:`~grimoire.llm_gateway.config.GatewayConfig` but use
    plain Python types suitable for env-var / YAML injection.
    """

    default_routes: dict[str, str] = {}
    fallback_routes: dict[str, str] = {}
    retry: _RetrySettings = _RetrySettings()
    timeout: _TimeoutSettings = _TimeoutSettings()
    embedding_cache: _EmbeddingCacheSettings = _EmbeddingCacheSettings()
    observability: _ObservabilitySettings = _ObservabilitySettings()
    pricing_overrides: dict[str, _PricingOverrideSettings] = {}

    model_config = {"populate_by_name": True}

    def to_gateway_config(self) -> GatewayConfig:
        """Convert to the frozen :class:`~grimoire.llm_gateway.config.GatewayConfig`."""
        return GatewayConfig(
            default_routes=dict(self.default_routes),
            fallback_routes=dict(self.fallback_routes),
            retry=self.retry.to_policy(),
            timeout=self.timeout.to_policy(),
            embedding_cache=self.embedding_cache.to_dataclass(),
            observability=self.observability.to_dataclass(),
            pricing_overrides={
                model: PricingOverride(
                    input_cost_per_1k=p.input_cost_per_1k,
                    output_cost_per_1k=p.output_cost_per_1k,
                )
                for model, p in self.pricing_overrides.items()
            },
        )
