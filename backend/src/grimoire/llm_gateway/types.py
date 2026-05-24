"""LLM gateway request objects."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GatewayCallContext:
    task: str
    campaign_id: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class LLMRequestLogEntry:
    task: str
    model: str
    campaign_id: str | None
    input_tokens: int
    output_tokens: int
    latency_ms: float
    status: str
    error: str | None = None
    provider_id: str | None = None
    route: str | None = None
    cached: bool = False
