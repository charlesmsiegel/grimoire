"""`LLMGatewayService`: the concrete `LLMGateway` implementation.

Coordinates the per-task routing resolver, the embedding cache, the
retry/timeout policy, the `llm_requests` audit log, and provider lookups
via the Plugins module.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from grimoire import events
from grimoire.event_bus import Event, EventBus
from grimoire.files.yaml_io import dump_yaml, load_yaml
from grimoire.llm_gateway.cache import EmbeddingCache
from grimoire.llm_gateway.config import GatewayConfig
from grimoire.llm_gateway.errors import (
    GatewayError,
    PermanentError,
    ProviderNotFoundError,
)
from grimoire.llm_gateway.request_log import LLMRequestLog, request_hash
from grimoire.llm_gateway.retry import resolve_retry_exceptions, run_with_retries
from grimoire.llm_gateway.routing import Route, RouteResolver
from grimoire.llm_gateway.tiers import Tier
from grimoire.observability.metrics import NULL_METRICS, MetricsRegistryProtocol
from grimoire.storage.db import Database
from grimoire.types.common import CampaignId, HealthLevel, HealthStatus, TurnId
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    ModelInfo,
    RetryPolicy,
    TimeoutPolicy,
    TokenUsage,
)
from grimoire.types.observability import HealthTarget
from grimoire.types.protocols import EmbeddingProvider, HealthMonitor, LLMProvider, Plugins

logger = logging.getLogger(__name__)


class LLMGatewayService:
    def __init__(
        self,
        plugins: Plugins,
        db: Database,
        config: GatewayConfig | None = None,
        *,
        data_root: Path | None = None,
        event_bus: EventBus | None = None,
        health_monitor: HealthMonitor | None = None,
        metrics: MetricsRegistryProtocol = NULL_METRICS,
    ) -> None:
        self._plugins = plugins
        self._db = db
        self._config = config or GatewayConfig()
        self._data_root = data_root
        self._event_bus = event_bus
        self._health_monitor = health_monitor
        self._router = RouteResolver(
            self._config.default_routes,
            self._config.fallback_routes,
        )
        self._cache = EmbeddingCache(
            db,
            max_entries=self._config.embedding_cache.max_entries,
        )
        self._log = LLMRequestLog(
            db,
            log_response_text=self._config.observability.log_response_text,
            response_excerpt_chars=self._config.observability.response_excerpt_chars,
        )
        self._loaded_campaigns: set[CampaignId] = set()
        # campaign_id → {task: "backend.model"} for imagegen tasks. Kept separate
        # from `_router` because imagegen routes don't share the LLM resolver's
        # task namespace and have their own backend kind (ImageGenBackend, not
        # LLMProvider). Consumed by ImageGenService via `imagegen_route`.
        self._imagegen_routes: dict[CampaignId, dict[str, str]] = {}
        # (provider_id, model) → ModelInfo or None (None = "no pricing available")
        self._pricing_cache: dict[tuple[str, str], ModelInfo | None] = {}
        # provider_id → last observed HealthLevel (populated by register_with_health_monitor)
        self._provider_health_levels: dict[str, HealthLevel] = {}
        # subscription handle for the health-monitor subscriber (kept to prevent GC)
        self._health_sub_id: str | None = None
        # Resolved retriable exception tuple from the configured retry policy.
        # Used in `except` clauses throughout the gateway (outside run_with_retries).
        self._retriable: tuple[type[BaseException], ...] = resolve_retry_exceptions(
            self._config.retry.retry_on
        )
        self._metrics: MetricsRegistryProtocol = metrics

    def _metrics_labels(self, task: str, request: Any) -> dict[str, Any]:
        """Best-effort labels for the metric row. Producers should never
        fail recording over a missing label, so attribute lookups are
        defensive and ``None`` is dropped from the payload."""
        provider = getattr(request, "provider_id", None) or getattr(request, "provider", None)
        model = getattr(request, "model", None)
        labels: dict[str, Any] = {"task": task}
        if provider:
            labels["provider"] = str(provider)
        if model:
            labels["model"] = str(model)
        return labels

    def capabilities_for(self, provider_id: str):
        """Return the static `ProviderCapabilities` for ``provider_id``.

        The Extractor's mode selector consults this to decide whether
        tool-use mode is available for the route picked this turn.
        """
        from grimoire.llm_gateway.capabilities import capabilities_for as _lookup

        return _lookup(provider_id)

    def resolve_route(self, task: str, campaign_id: CampaignId | None = None):
        """Return the resolved `Route` (provider_id + model) for ``task``.

        Exposes the underlying router so callers (notably the Orchestrator's
        `select_mode` pass) can know which provider will be invoked before the
        actual stream/complete call.
        """
        return self._router.resolve(task, campaign_id)

    # ------------------------------------------------------------------ #
    # Event helpers
    # ------------------------------------------------------------------ #

    async def _emit(self, event_type: str, payload: dict) -> None:
        """Emit a bus event. No-ops when no bus is configured.

        Exceptions from handlers are caught and logged so that emission never
        breaks the LLM call path.
        """
        if self._event_bus is None:
            return
        try:
            await self._event_bus.emit(Event(type=event_type, payload=payload))
        except Exception:
            logger.exception("event emission failed for event_type=%s", event_type)

    async def _emit_tier_resolved(self, task: str, campaign_id: CampaignId | None) -> None:
        """Emit a ``tier_resolved`` event after route resolution."""
        try:
            route, source = self._router.resolve_with_source(task, campaign_id)
        except Exception:
            return
        from grimoire.llm_gateway.tiers import tier_for_task

        tier = tier_for_task(task)
        await self._emit(
            events.TIER_RESOLVED,
            {
                "task": task,
                "tier": tier.value if tier is not None else None,
                "route": route.raw,
                "source": source,
                "campaign_id": campaign_id,
            },
        )

    # ------------------------------------------------------------------ #
    # Pricing cache
    # ------------------------------------------------------------------ #

    async def _get_pricing(self, provider_id: str, model: str) -> ModelInfo | None:
        """Return cached ModelInfo for (provider_id, model), or None if unavailable.

        Calls provider.list_models() on the first miss and caches the result.
        Exceptions from list_models() are swallowed; None is cached so we do not
        re-attempt on every subsequent call.
        """
        key = (provider_id, model)
        if key in self._pricing_cache:
            return self._pricing_cache[key]
        provider = self._plugins.get_llm_provider(provider_id)
        if provider is None:
            self._pricing_cache[key] = None
            return None
        try:
            models = await provider.list_models()
        except Exception:
            logger.debug(
                "llm_gateway: list_models() failed for provider=%s; pricing unavailable",
                provider_id,
            )
            self._pricing_cache[key] = None
            return None
        info = next((m for m in models if m.id == model), None)
        self._pricing_cache[key] = info
        return info

    # ------------------------------------------------------------------ #
    # Introspection
    # ------------------------------------------------------------------ #

    async def list_llm_providers(self) -> list[LLMProvider]:
        return self._plugins.llm_providers()

    async def list_embedding_providers(self) -> list[EmbeddingProvider]:
        return self._plugins.embedding_providers()

    async def list_routes(self, campaign_id: CampaignId | None = None) -> dict[str, str]:
        return self._router.routes_for(campaign_id)

    async def set_route(
        self,
        task: str,
        route: str,
        campaign_id: CampaignId | None = None,
        *,
        kind: str = "llm",
    ) -> None:
        """Apply a route and (when campaign-scoped) persist to campaign.yaml.

        ``kind`` selects which YAML block the route is written under:
          * ``"llm"``       → ``model_routing``
          * ``"embedding"`` → ``embedding_routing``
          * ``"imagegen"``  → ``imagegen_routing``

        LLM and embedding routes share the gateway's ``RouteResolver``;
        imagegen routes are stored separately (the resolver only knows
        about LLM/embedding providers).
        """
        Route.parse(route)  # validate before touching any state
        if kind == "imagegen":
            if campaign_id is not None:
                self._imagegen_routes.setdefault(campaign_id, {})[task] = route
        else:
            self._router.set_route(task, route, campaign_id)
        if campaign_id is not None and self._data_root is not None:
            self._persist_campaign_route(campaign_id, task, route, kind=kind)

    async def set_tier_route(
        self,
        campaign_id: CampaignId,
        tier: Tier,
        route: str,
    ) -> None:
        """Apply a tier route and persist to campaign.yaml.

        The route must parse via ``Route.parse``. Persistence preserves
        any other ``model_tiers`` entries and any existing routing
        blocks on the file.
        """
        Route.parse(route)  # validate before touching any state
        self._router.set_tier_route(campaign_id, tier, route)
        yaml_path = self._campaign_yaml_path(campaign_id)
        if yaml_path is not None:
            self._write_tier_block(campaign_id)

    async def clear_tier_route(
        self,
        campaign_id: CampaignId,
        tier: Tier,
    ) -> None:
        """Remove a tier route from in-memory state and campaign.yaml."""
        self._router.clear_tier_route(campaign_id, tier)
        yaml_path = self._campaign_yaml_path(campaign_id)
        if yaml_path is not None:
            self._write_tier_block(campaign_id)

    def _write_tier_block(self, campaign_id: CampaignId) -> None:
        """Serialize the resolver's tier state back to ``campaign.yaml``."""
        data = self._read_campaign_yaml_for_write(campaign_id)
        if data is None:
            return
        tiers = self._router.tiers_for(campaign_id)
        if tiers:
            data["model_tiers"] = {tier.value: route for tier, route in tiers.items()}
        else:
            data.pop("model_tiers", None)
        self._atomic_write_campaign_yaml(campaign_id, data)

    async def clear_route(
        self,
        task: str,
        campaign_id: CampaignId | None = None,
        *,
        kind: str = "llm",
    ) -> None:
        """Remove a per-campaign route entry and rewrite the YAML block."""
        if kind == "imagegen":
            if campaign_id is not None:
                self._imagegen_routes.get(campaign_id, {}).pop(task, None)
        else:
            self._router.clear_route(task, campaign_id)
        if campaign_id is not None and self._data_root is not None:
            self._delete_campaign_route(campaign_id, task, kind=kind)

    def imagegen_route(self, task: str, campaign_id: CampaignId) -> Route | None:
        """Return the per-campaign imagegen route for ``task``, if any.

        Returns ``None`` when no route is configured. The campaign must
        already have been lazy-loaded (e.g. by an earlier ``complete()``
        call or by calling :meth:`_load_campaign_routing` directly).
        """
        raw = self._imagegen_routes.get(campaign_id, {}).get(task)
        return Route.parse(raw) if raw else None

    def imagegen_routes_for(self, campaign_id: CampaignId) -> dict[str, str]:
        """Return a copy of the imagegen routing table for ``campaign_id``."""
        return dict(self._imagegen_routes.get(campaign_id, {}))

    async def ensure_campaign_loaded(self, campaign_id: CampaignId) -> None:
        """Public hook to trigger lazy YAML loading for ``campaign_id``.

        ImageGenService calls this before reading :meth:`imagegen_route` so
        routes are present even when no LLM call has happened yet for
        this campaign.
        """
        if campaign_id not in self._loaded_campaigns:
            await self._load_campaign_routing(campaign_id)

    # Well-known tasks that should "just work" once a single LLM / embedding
    # plugin is configured. Anything not listed here can still be set via
    # campaign YAML or env-var defaults — these are the ones the startup
    # wizard implicitly promises.
    _DEFAULT_LLM_TASKS: tuple[str, ...] = ("main", "library.summarize", "extractor")
    _DEFAULT_EMBEDDING_TASKS: tuple[str, ...] = ("library.embed",)

    async def register_provider_defaults(self) -> None:
        """Populate ``default_routes`` from configured plugins.

        The startup wizard saves a plugin's API key + ``active_model`` but
        never wires the plugin into the gateway's per-task routing table,
        so a wizard-configured install still raises ``RouteNotFoundError``
        on the first turn. This bridges the gap: for each kind, the first
        plugin whose saved config passes its own schema is registered as
        the default for the well-known tasks. Idempotent — a task that
        already has a default (env var, campaign YAML, prior call) is
        never overwritten.

        Routes are keyed by *plugin id* (e.g. ``llm-openrouter``), not the
        provider instance's ``.id`` field, because that is what
        :meth:`Plugins.get_llm_provider` (and the resolver's lookup) keys
        on. The two often differ — a plugin's manifest id is the unique
        install handle while the instance id is a short brand name.
        """
        existing_defaults = self._router.routes_for(None)

        try:
            manifests = await self._plugins.list_installed()
        except Exception:
            logger.exception("register_provider_defaults: list_installed failed")
            return

        async def _first_configured_for_kind(kind: str) -> tuple[str, str] | None:
            """Return (plugin_id, active_model) for the first configured plugin
            whose manifest declares ``kind`` in ``implements``."""
            for manifest in manifests:
                if kind not in [str(k) for k in getattr(manifest, "implements", [])]:
                    continue
                plugin_id = str(getattr(manifest, "id", "") or "")
                if not plugin_id:
                    continue
                try:
                    cfg = await self._plugins.get_config(plugin_id)
                except Exception:
                    continue
                if not isinstance(cfg, dict):
                    continue
                active_model = cfg.get("active_model")
                if not isinstance(active_model, str) or not active_model:
                    continue
                try:
                    validation = await self._plugins.validate_config(plugin_id, cfg)
                except Exception:
                    continue
                if not getattr(validation, "ok", False):
                    continue
                return plugin_id, active_model
            return None

        llm_pick = await _first_configured_for_kind("llm_provider")
        if llm_pick is not None:
            plugin_id, model = llm_pick
            route = f"{plugin_id}.{model}"
            for task in self._DEFAULT_LLM_TASKS:
                if existing_defaults.get(task) is None:
                    try:
                        self._router.set_route(task, route)
                    except ValueError:
                        logger.warning(
                            "llm_gateway: refusing to register default for task=%r "
                            "from plugin=%r model=%r — invalid route",
                            task,
                            plugin_id,
                            model,
                        )

        embed_pick = await _first_configured_for_kind("embedding_provider")
        if embed_pick is not None:
            plugin_id, model = embed_pick
            route = f"{plugin_id}.{model}"
            for task in self._DEFAULT_EMBEDDING_TASKS:
                if existing_defaults.get(task) is None:
                    try:
                        self._router.set_route(task, route)
                    except ValueError:
                        logger.warning(
                            "llm_gateway: refusing to register default for embedding "
                            "task=%r from plugin=%r model=%r — invalid route",
                            task,
                            plugin_id,
                            model,
                        )

    # ------------------------------------------------------------------ #
    # Per-call override resolvers
    # ------------------------------------------------------------------ #

    def _resolved_retry(self, override: RetryPolicy | None) -> RetryPolicy:
        """Return the per-call override if given, else the global config value."""
        return override if override is not None else self._config.retry

    def _resolved_timeout(self, override: TimeoutPolicy | None) -> TimeoutPolicy:
        """Return the per-call override if given, else the global config value."""
        return override if override is not None else self._config.timeout

    def _retriable_for(self, override: RetryPolicy | None) -> tuple[type[BaseException], ...]:
        """Return the retriable exception tuple for *override*, or the gateway default."""
        if override is None:
            return self._retriable
        return resolve_retry_exceptions(override.retry_on)

    @staticmethod
    def _retry_dict(cfg: RetryPolicy | None) -> dict | None:
        """Serialise a RetryPolicy to a plain dict for event payloads (None → None)."""
        if cfg is None:
            return None
        return {
            "max_retries": cfg.max_retries,
            "initial_delay_ms": cfg.initial_delay_ms,
            "backoff_factor": cfg.backoff_factor,
        }

    @staticmethod
    def _timeout_dict(cfg: TimeoutPolicy | None) -> dict | None:
        """Serialise a TimeoutPolicy to a plain dict for event payloads (None → None)."""
        if cfg is None:
            return None
        return {
            "total_seconds": cfg.total_seconds,
            "first_token_seconds": cfg.first_token_seconds,
        }

    # ------------------------------------------------------------------ #
    # Completion
    # ------------------------------------------------------------------ #

    async def complete(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
        *,
        turn_id: TurnId | None = None,
        retry: RetryPolicy | None = None,
        timeout: TimeoutPolicy | None = None,
    ) -> CompletionResponse:
        async with self._metrics.measure(
            "llm_gateway", "complete", labels=self._metrics_labels(task, request)
        ):
            return await self._complete_inner(
                task,
                request,
                campaign_id,
                turn_id=turn_id,
                retry=retry,
                timeout=timeout,
            )

    async def _complete_inner(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
        *,
        turn_id: TurnId | None = None,
        retry: RetryPolicy | None = None,
        timeout: TimeoutPolicy | None = None,
    ) -> CompletionResponse:
        if campaign_id is not None and campaign_id not in self._loaded_campaigns:
            await self._load_campaign_routing(campaign_id)
        primary = self._router.resolve(task, campaign_id)
        await self._emit_tier_resolved(task, campaign_id)
        fallback = self._router.fallback(task)
        return await self._complete_one(
            task=task,
            route=primary,
            request=request,
            campaign_id=campaign_id,
            turn_id=turn_id,
            fallback=fallback,
            retry=retry,
            timeout=timeout,
        )

    async def _complete_one(
        self,
        *,
        task: str,
        route: Route,
        request: CompletionRequest,
        campaign_id: CampaignId | None,
        turn_id: TurnId | None,
        fallback: Route | None,
        retry: RetryPolicy | None = None,
        timeout: TimeoutPolicy | None = None,
    ) -> CompletionResponse:
        resolved_retry = self._resolved_retry(retry)
        try:
            response, _retries = await self._invoke_complete(
                task=task,
                route=route,
                request=request,
                campaign_id=campaign_id,
                turn_id=turn_id,
                fallback_used=False,
                retry=retry,
                timeout=timeout,
            )
            return response
        except PermanentError as exc:
            await self._record_failure(
                task=task,
                route=route,
                request=request,
                campaign_id=campaign_id,
                turn_id=turn_id,
                error=exc,
                retries=0,
                fallback_used=False,
                retry=retry,
                timeout=timeout,
            )
            raise
        except BaseException as exc:
            retriable = self._retriable_for(retry)
            if not retriable or not isinstance(exc, retriable):
                raise
            await self._record_failure(
                task=task,
                route=route,
                request=request,
                campaign_id=campaign_id,
                turn_id=turn_id,
                error=exc,
                retries=resolved_retry.max_retries,
                fallback_used=False,
                retry=retry,
                timeout=timeout,
            )
            if fallback is None or fallback.raw == route.raw:
                raise
            logger.warning(
                "primary route %s exhausted retries for task %s; trying fallback %s",
                route.raw,
                task,
                fallback.raw,
            )
            try:
                response, _ = await self._invoke_complete(
                    task=task,
                    route=fallback,
                    request=request,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    fallback_used=True,
                    retry=retry,
                    timeout=timeout,
                )
                return response
            except PermanentError as fallback_exc:
                await self._record_failure(
                    task=task,
                    route=fallback,
                    request=request,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    error=fallback_exc,
                    retries=0,
                    fallback_used=True,
                    retry=retry,
                    timeout=timeout,
                )
                raise
            except BaseException as fallback_exc:
                if not retriable or not isinstance(fallback_exc, retriable):
                    raise
                await self._record_failure(
                    task=task,
                    route=fallback,
                    request=request,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    error=fallback_exc,
                    retries=resolved_retry.max_retries,
                    fallback_used=True,
                    retry=retry,
                    timeout=timeout,
                )
                raise

    async def _invoke_complete(
        self,
        *,
        task: str,
        route: Route,
        request: CompletionRequest,
        campaign_id: CampaignId | None,
        turn_id: TurnId | None,
        fallback_used: bool,
        retry: RetryPolicy | None = None,
        timeout: TimeoutPolicy | None = None,
    ) -> tuple[CompletionResponse, int]:
        provider = self._require_llm(route.provider_id)
        scoped = request.model_copy(update={"model": route.model})
        resolved_retry = self._resolved_retry(retry)
        resolved_timeout = self._resolved_timeout(timeout)
        timeout_seconds = resolved_timeout.total_seconds

        await self._emit(
            events.LLM_REQUEST_STARTED,
            {
                "task": task,
                "provider": route.provider_id,
                "model": route.model,
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "fallback_used": fallback_used,
                "retry_override": self._retry_dict(retry),
                "timeout_override": self._timeout_dict(timeout),
            },
        )

        async def _call() -> CompletionResponse:
            return await asyncio.wait_for(provider.complete(scoped), timeout=timeout_seconds)

        started = time.monotonic()
        response, retries = await run_with_retries(_call, policy=resolved_retry)
        latency_ms = int((time.monotonic() - started) * 1000)
        # Ensure usage totals are populated.
        usage = response.usage
        if usage.total_tokens == 0 and (usage.input_tokens or usage.output_tokens):
            usage = TokenUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.input_tokens + usage.output_tokens,
            )
            response = response.model_copy(update={"usage": usage})
        if response.latency_ms == 0:
            response = response.model_copy(update={"latency_ms": latency_ms})
        # §5: fill cost_estimate_usd from real token usage when the provider
        # did not supply it.
        if response.cost_estimate_usd is None:
            info = await self._get_pricing(route.provider_id, route.model)
            if info is not None and not (
                info.input_cost_per_1k is None and info.output_cost_per_1k is None
            ):
                cost = response.usage.input_tokens / 1000.0 * (
                    info.input_cost_per_1k or 0.0
                ) + response.usage.output_tokens / 1000.0 * (info.output_cost_per_1k or 0.0)
                response = response.model_copy(update={"cost_estimate_usd": cost})
        if self._config.observability.log_all_requests:
            await self._log.record(
                task=task,
                provider_id=route.provider_id,
                model=route.model,
                usage=response.usage,
                cost_usd=response.cost_estimate_usd,
                latency_ms=response.latency_ms or latency_ms,
                retries=retries,
                fallback_used=fallback_used,
                request_hash=request_hash(scoped),
                response_text=response.text,
                campaign_id=campaign_id,
                turn_id=turn_id,
                retry_override=self._retry_dict(retry),
                timeout_override=self._timeout_dict(timeout),
            )
        await self._emit(
            events.LLM_RESPONSE_RECEIVED,
            {
                "task": task,
                "provider": route.provider_id,
                "model": route.model,
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "latency_ms": response.latency_ms or latency_ms,
                "retries": retries,
                "fallback_used": fallback_used,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.total_tokens,
                },
                "cost_estimate_usd": response.cost_estimate_usd,
                "finish_reason": response.finish_reason,
                "params": {
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "seed": request.seed,
                },
                "retry_override": self._retry_dict(retry),
                "timeout_override": self._timeout_dict(timeout),
            },
        )
        return response, retries

    async def _record_failure(
        self,
        *,
        task: str,
        route: Route,
        request: CompletionRequest,
        campaign_id: CampaignId | None,
        turn_id: TurnId | None,
        error: BaseException,
        retries: int,
        fallback_used: bool,
        retry: RetryPolicy | None = None,
        timeout: TimeoutPolicy | None = None,
    ) -> None:
        await self._emit(
            events.LLM_REQUEST_FAILED,
            {
                "task": task,
                "provider": route.provider_id,
                "model": route.model,
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "error": f"{type(error).__name__}: {error}",
                "retries": retries,
                "fallback_used": fallback_used,
                "retry_override": self._retry_dict(retry),
                "timeout_override": self._timeout_dict(timeout),
            },
        )
        if not self._config.observability.log_all_requests:
            return
        try:
            scoped = request.model_copy(update={"model": route.model})
            await self._log.record(
                task=task,
                provider_id=route.provider_id,
                model=route.model,
                retries=retries,
                fallback_used=fallback_used,
                request_hash=request_hash(scoped),
                error=f"{type(error).__name__}: {error}",
                campaign_id=campaign_id,
                turn_id=turn_id,
                retry_override=self._retry_dict(retry),
                timeout_override=self._timeout_dict(timeout),
            )
        except Exception:
            logger.exception("failed to record llm_requests row for failed call")

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #

    async def stream(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
        *,
        turn_id: TurnId | None = None,
        retry: RetryPolicy | None = None,
        timeout: TimeoutPolicy | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        """Stream completion chunks; thin wrapper that records a metric and delegates."""
        async with self._metrics.measure(
            "llm_gateway", "stream", labels=self._metrics_labels(task, request)
        ):
            async for chunk in self._stream_inner(
                task,
                request,
                campaign_id,
                turn_id=turn_id,
                retry=retry,
                timeout=timeout,
            ):
                yield chunk

    async def _stream_inner(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
        *,
        turn_id: TurnId | None = None,
        retry: RetryPolicy | None = None,
        timeout: TimeoutPolicy | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        """Stream completion chunks, retrying / falling back on zero-chunk failures.

        §6 — Streaming retry / fallback policy:

        * Retry and fallback fire ONLY when zero chunks have been delivered to
          the caller.  Once any chunk has been yielded, the call is "committed"
          and subsequent failures propagate uncaught (mid-stream crash).
        * Zero-chunk retriable errors (``TransientError``, ``RateLimitError``,
          ``TimeoutError``) are retried up to ``config.retry.max_retries`` times
          against the same route before the fallback is consulted.
        * Zero-chunk ``PermanentError`` skips retries and goes straight to the
          fallback (if one is configured and is a different route).
        * If all attempts (primary + retries + fallback) fail, the last
          exception is re-raised.
        """
        if campaign_id is not None and campaign_id not in self._loaded_campaigns:
            await self._load_campaign_routing(campaign_id)
        primary_route = self._router.resolve(task, campaign_id)
        await self._emit_tier_resolved(task, campaign_id)
        fallback_route = self._router.fallback(task)

        # Build the ordered attempt list: primary (possibly repeated for retries)
        # followed by fallback (a single attempt with its own internal retry).
        # We drive the retry loop explicitly here because async generators don't
        # compose cleanly with run_with_retries.
        resolved_retry = self._resolved_retry(retry)
        max_retries = resolved_retry.max_retries
        delay_s = max(0, resolved_retry.initial_delay_ms) / 1000.0
        backoff = max(1.0, resolved_retry.backoff_factor)

        first_chunk_delivered = False
        last_exc: BaseException | None = None
        retriable = self._retriable_for(retry)

        # ── Primary route: initial attempt + up to max_retries retries ──────
        retries_used = 0
        while retries_used <= max_retries:
            primary_provider = self._require_llm(primary_route.provider_id)
            primary_scoped = request.model_copy(update={"model": primary_route.model})
            try:
                async for chunk in self._stream_one(
                    task=task,
                    route=primary_route,
                    provider=primary_provider,
                    request=primary_scoped,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    fallback_used=False,
                    retry=retry,
                    timeout=timeout,
                ):
                    first_chunk_delivered = True
                    yield chunk
                # Stream completed successfully on the primary route.
                return
            except PermanentError as exc:
                if first_chunk_delivered:
                    # Mid-stream permanent error — propagate uncaught.
                    raise
                # Zero-chunk permanent error: record failure, skip retries,
                # fall through to the fallback below.
                await self._record_failure(
                    task=task,
                    route=primary_route,
                    request=primary_scoped,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    error=exc,
                    retries=0,
                    fallback_used=False,
                    retry=retry,
                    timeout=timeout,
                )
                last_exc = exc
                break  # No retries for PermanentError; go straight to fallback.
            except BaseException as exc:
                if first_chunk_delivered:
                    # Mid-stream failure after at least one chunk — propagate
                    # uncaught.  No retry, no fallback (caller sees partial data).
                    raise
                if not retriable or not isinstance(exc, retriable):
                    # Non-retriable, non-Permanent exception (e.g. programming
                    # error): record and re-raise immediately.
                    await self._record_failure(
                        task=task,
                        route=primary_route,
                        request=primary_scoped,
                        campaign_id=campaign_id,
                        turn_id=turn_id,
                        error=exc,
                        retries=retries_used,
                        fallback_used=False,
                        retry=retry,
                        timeout=timeout,
                    )
                    raise
                # Zero-chunk retriable error.
                await self._record_failure(
                    task=task,
                    route=primary_route,
                    request=primary_scoped,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    error=exc,
                    retries=retries_used,
                    fallback_used=False,
                    retry=retry,
                    timeout=timeout,
                )
                last_exc = exc
                if retries_used == max_retries:
                    break  # Retries exhausted; fall through to fallback.
                # Back off before the next retry.
                if delay_s > 0:
                    await asyncio.sleep(delay_s)
                delay_s *= backoff
                retries_used += 1
                continue

        # ── Fallback route (single attempt with its own retry policy) ────────
        if (
            fallback_route is not None
            and fallback_route.raw != primary_route.raw
            and not first_chunk_delivered
        ):
            logger.warning(
                "stream primary route %s failed for task %s; trying fallback %s",
                primary_route.raw,
                task,
                fallback_route.raw,
            )
            fallback_provider = self._require_llm(fallback_route.provider_id)
            fallback_scoped = request.model_copy(update={"model": fallback_route.model})
            try:
                async for chunk in self._stream_one(
                    task=task,
                    route=fallback_route,
                    provider=fallback_provider,
                    request=fallback_scoped,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    fallback_used=True,
                    retry=retry,
                    timeout=timeout,
                ):
                    first_chunk_delivered = True
                    yield chunk
                return
            except BaseException as exc:
                if first_chunk_delivered:
                    raise
                await self._record_failure(
                    task=task,
                    route=fallback_route,
                    request=fallback_scoped,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    error=exc,
                    retries=0,
                    fallback_used=True,
                    retry=retry,
                    timeout=timeout,
                )
                last_exc = exc

        # All attempts failed with zero chunks delivered.
        assert last_exc is not None
        raise last_exc

    async def _stream_one(
        self,
        *,
        task: str,
        route: Route,
        provider: LLMProvider,
        request: CompletionRequest,
        campaign_id: CampaignId | None,
        turn_id: TurnId | None,
        fallback_used: bool = False,
        retry: RetryPolicy | None = None,
        timeout: TimeoutPolicy | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        """Attempt a single streaming call to `provider` and yield chunks.

        §6 — Streaming retry / fallback policy:

        * ``_stream_one`` raises on any error (before or during the stream).
        * The caller (``stream``) is responsible for deciding whether to retry
          or fall back.  It tracks whether any chunk has been yielded and only
          retries / falls back when zero chunks were delivered.
        * Once any chunk has been yielded, subsequent failures propagate
          uncaught — mid-stream crashes are not retried.
        * ``fallback_used`` is forwarded into emitted event payloads.
        """
        resolved_timeout = self._resolved_timeout(timeout)
        first_token_timeout = resolved_timeout.first_token_seconds
        total_timeout = resolved_timeout.total_seconds
        started = time.monotonic()

        await self._emit(
            events.LLM_REQUEST_STARTED,
            {
                "task": task,
                "provider": route.provider_id,
                "model": route.model,
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "fallback_used": fallback_used,
                "retry_override": self._retry_dict(retry),
                "timeout_override": self._timeout_dict(timeout),
            },
        )

        stream = provider.stream(request)
        usage: TokenUsage | None = None
        text_parts: list[str] = []
        first = True
        try:
            while True:
                budget = (
                    first_token_timeout if first else total_timeout - (time.monotonic() - started)
                )
                if budget <= 0:
                    raise TimeoutError(f"stream exceeded total timeout of {total_timeout}s")
                try:
                    chunk = await asyncio.wait_for(_anext(stream), timeout=budget)
                except StopAsyncIteration:
                    break
                first = False
                text_parts.append(chunk.delta)
                if chunk.usage is not None:
                    usage = chunk.usage
                yield chunk
                if chunk.is_final:
                    break
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    await close()
        latency_ms = int((time.monotonic() - started) * 1000)
        # Compute cost from usage + price book so streaming calls populate
        # cost_records on the same footing as non-streaming complete() does.
        cost_estimate_usd: float | None = None
        if usage is not None and (usage.input_tokens or usage.output_tokens):
            info = await self._get_pricing(route.provider_id, route.model)
            if info is not None and not (
                info.input_cost_per_1k is None and info.output_cost_per_1k is None
            ):
                cost_estimate_usd = usage.input_tokens / 1000.0 * (
                    info.input_cost_per_1k or 0.0
                ) + usage.output_tokens / 1000.0 * (info.output_cost_per_1k or 0.0)
        if self._config.observability.log_all_requests:
            await self._log.record(
                task=task,
                provider_id=route.provider_id,
                model=route.model,
                usage=usage,
                cost_usd=cost_estimate_usd,
                latency_ms=latency_ms,
                retries=0,
                fallback_used=fallback_used,
                request_hash=request_hash(request),
                response_text="".join(text_parts),
                campaign_id=campaign_id,
                turn_id=turn_id,
                retry_override=self._retry_dict(retry),
                timeout_override=self._timeout_dict(timeout),
            )
        await self._emit(
            events.LLM_RESPONSE_RECEIVED,
            {
                "task": task,
                "provider": route.provider_id,
                "model": route.model,
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "latency_ms": latency_ms,
                "retries": 0,
                "fallback_used": fallback_used,
                "usage": {
                    "input_tokens": usage.input_tokens if usage else 0,
                    "output_tokens": usage.output_tokens if usage else 0,
                    "total_tokens": usage.total_tokens if usage else 0,
                },
                "cost_estimate_usd": cost_estimate_usd,
                "finish_reason": "stop",
                "params": {
                    "temperature": request.temperature,
                    "max_tokens": request.max_tokens,
                    "seed": request.seed,
                    "top_p": None,
                },
                "retry_override": self._retry_dict(retry),
                "timeout_override": self._timeout_dict(timeout),
            },
        )

    # ------------------------------------------------------------------ #
    # Embeddings
    # ------------------------------------------------------------------ #

    async def embed(
        self,
        task: str,
        texts: list[str],
        campaign_id: CampaignId | None = None,
        *,
        turn_id: TurnId | None = None,
        retry: RetryPolicy | None = None,
        timeout: TimeoutPolicy | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        if campaign_id is not None and campaign_id not in self._loaded_campaigns:
            await self._load_campaign_routing(campaign_id)
        route = self._router.resolve(task, campaign_id)
        provider = self._require_embedding(route.provider_id)
        model_id = provider.model_id

        cached: dict[str, list[float]] = {}
        if self._config.embedding_cache.enabled:
            cached = await self._cache.get_many(texts, model_id)

        missing: list[str] = []
        seen: set[str] = set()
        for text in texts:
            if text in cached or text in seen:
                continue
            missing.append(text)
            seen.add(text)

        new_vectors: dict[str, list[float]] = {}
        if missing:
            resolved_retry = self._resolved_retry(retry)
            resolved_timeout = self._resolved_timeout(timeout)
            embed_retriable = self._retriable_for(retry)

            await self._emit(
                events.EMBEDDING_REQUEST_STARTED,
                {
                    "task": task,
                    "provider": route.provider_id,
                    "model": model_id,
                    "campaign_id": campaign_id,
                    "turn_id": turn_id,
                    "input_count": len(missing),
                    "retry_override": self._retry_dict(retry),
                    "timeout_override": self._timeout_dict(timeout),
                },
            )
            started = time.monotonic()

            # Determine whether to chunk into batches.
            # If the provider exposes max_batch_size and it is smaller than the
            # number of missing texts, split into chunks of that size.  Each
            # batch is retried independently; total retries reported in the
            # audit/event payload is the SUM across all batches (i.e. how many
            # extra network round-trips this request required overall).
            batch_size: int | None = getattr(provider, "max_batch_size", None)
            use_batching = batch_size is not None and batch_size > 0 and batch_size < len(missing)

            if use_batching:
                assert batch_size is not None  # narrowing for type checker
                batches = [missing[i : i + batch_size] for i in range(0, len(missing), batch_size)]
            else:
                batches = [missing]

            all_vectors: list[list[float]] = []
            total_retries = 0

            try:
                for batch in batches:
                    # Capture `batch` in a closure-safe variable to avoid the
                    # "late binding" pitfall in Python async closures.
                    _batch = batch

                    async def _call(
                        _b: list[str] = _batch,
                        _tout: float = resolved_timeout.total_seconds,
                    ) -> list[list[float]]:
                        return await asyncio.wait_for(
                            provider.embed(_b),
                            timeout=_tout,
                        )

                    batch_vectors, batch_retries = await run_with_retries(
                        _call, policy=resolved_retry
                    )
                    total_retries += batch_retries
                    all_vectors.extend(batch_vectors)
            except PermanentError as exc:
                if self._config.observability.log_all_requests:
                    await self._log.record(
                        task=task,
                        provider_id=route.provider_id,
                        model=model_id,
                        retries=total_retries,
                        error=f"{type(exc).__name__}: {exc}",
                        campaign_id=campaign_id,
                        turn_id=turn_id,
                        retry_override=self._retry_dict(retry),
                        timeout_override=self._timeout_dict(timeout),
                    )
                await self._emit(
                    events.LLM_REQUEST_FAILED,
                    {
                        "task": task,
                        "provider": route.provider_id,
                        "model": model_id,
                        "campaign_id": campaign_id,
                        "turn_id": turn_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "retries": total_retries,
                        "fallback_used": False,
                        "retry_override": self._retry_dict(retry),
                        "timeout_override": self._timeout_dict(timeout),
                    },
                )
                raise
            except BaseException as exc:
                if not embed_retriable or not isinstance(exc, embed_retriable):
                    raise
                # `total_retries` sums batches that succeeded; add the
                # exhausted-retries count for the batch that finally failed.
                final_retries = total_retries + resolved_retry.max_retries
                if self._config.observability.log_all_requests:
                    await self._log.record(
                        task=task,
                        provider_id=route.provider_id,
                        model=model_id,
                        retries=final_retries,
                        error=f"{type(exc).__name__}: {exc}",
                        campaign_id=campaign_id,
                        turn_id=turn_id,
                        retry_override=self._retry_dict(retry),
                        timeout_override=self._timeout_dict(timeout),
                    )
                await self._emit(
                    events.LLM_REQUEST_FAILED,
                    {
                        "task": task,
                        "provider": route.provider_id,
                        "model": model_id,
                        "campaign_id": campaign_id,
                        "turn_id": turn_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "retries": final_retries,
                        "fallback_used": False,
                        "retry_override": self._retry_dict(retry),
                        "timeout_override": self._timeout_dict(timeout),
                    },
                )
                raise

            # All-or-nothing: only assign if every batch succeeded.
            vectors = all_vectors
            retries = total_retries

            latency_ms = int((time.monotonic() - started) * 1000)
            if len(vectors) != len(missing):
                raise GatewayError(
                    f"embedding provider {route.provider_id!r} returned "
                    f"{len(vectors)} vectors for {len(missing)} inputs"
                )
            for text, vec in zip(missing, vectors, strict=True):
                new_vectors[text] = vec
            if self._config.embedding_cache.enabled:
                await self._cache.set_many(list(new_vectors.items()), model_id)
            if self._config.observability.log_all_requests:
                await self._log.record(
                    task=task,
                    provider_id=route.provider_id,
                    model=model_id,
                    usage=TokenUsage(input_tokens=max(1, sum(len(t) // 4 for t in missing))),
                    latency_ms=latency_ms,
                    retries=retries,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    retry_override=self._retry_dict(retry),
                    timeout_override=self._timeout_dict(timeout),
                )
            await self._emit(
                events.EMBEDDING_RESPONSE_RECEIVED,
                {
                    "task": task,
                    "provider": route.provider_id,
                    "model": model_id,
                    "campaign_id": campaign_id,
                    "turn_id": turn_id,
                    "latency_ms": latency_ms,
                    "retries": retries,
                    "vector_count": len(vectors),
                    "input_count": len(missing),
                    "dimensions": len(vectors[0]) if vectors else 0,
                    "retry_override": self._retry_dict(retry),
                    "timeout_override": self._timeout_dict(timeout),
                },
            )

        return [cached[text] if text in cached else new_vectors[text] for text in texts]

    # ------------------------------------------------------------------ #
    # Estimation
    # ------------------------------------------------------------------ #

    async def estimate_tokens(self, text: str, provider_id: str | None = None) -> int:
        if provider_id is not None:
            provider = self._plugins.get_llm_provider(provider_id)
            if provider is not None:
                fn = getattr(provider, "estimate_tokens", None)
                if fn is not None:
                    return int(await fn(text))
        # Cheap default: ~4 chars per token. Good enough for budgeting.
        return max(1, len(text) // 4)

    async def estimate_cost(
        self,
        task: str,
        request: CompletionRequest,
        campaign_id: CampaignId | None = None,
    ) -> float | None:
        route = self._router.resolve(task, campaign_id)
        provider = self._plugins.get_llm_provider(route.provider_id)
        if provider is None:
            return None
        models = []
        try:
            models = await provider.list_models()
        except Exception:
            return None
        info = next((m for m in models if m.id == route.model), None)
        if info is None or (info.input_cost_per_1k is None and info.output_cost_per_1k is None):
            return None
        prompt_chars = sum(len(m.content) for m in request.messages) + (len(request.system or ""))
        prompt_tokens = max(1, prompt_chars // 4)
        completion_tokens = request.max_tokens
        cost = 0.0
        if info.input_cost_per_1k is not None:
            cost += prompt_tokens / 1000.0 * info.input_cost_per_1k
        if info.output_cost_per_1k is not None:
            cost += completion_tokens / 1000.0 * info.output_cost_per_1k
        return cost

    # ------------------------------------------------------------------ #
    # Health
    # ------------------------------------------------------------------ #

    async def health_check(self, provider_id: str) -> HealthStatus:
        now_iso = datetime.now(UTC).isoformat()
        provider = self._plugins.get_llm_provider(
            provider_id
        ) or self._plugins.get_embedding_provider(provider_id)
        if provider is None:
            return HealthStatus(
                level=HealthLevel.UNCONFIGURED,
                target_id=provider_id,
                checked_at=now_iso,
            )
        probe = getattr(provider, "health_check", None)
        if probe is None:
            return HealthStatus(
                level=HealthLevel.HEALTHY,
                target_id=provider_id,
                checked_at=now_iso,
            )
        try:
            result = await probe()
        except Exception as exc:
            return HealthStatus(
                level=HealthLevel.UNHEALTHY,
                target_id=provider_id,
                message=f"{type(exc).__name__}: {exc}",
                checked_at=now_iso,
            )
        if isinstance(result, HealthStatus):
            return result.model_copy(
                update={
                    "target_id": provider_id,
                    "checked_at": result.checked_at or now_iso,
                }
            )
        return HealthStatus(
            level=HealthLevel.HEALTHY,
            target_id=provider_id,
            checked_at=now_iso,
        )

    async def health_check_all(self) -> dict[str, HealthStatus]:
        ids: set[str] = set()
        for p in self._plugins.llm_providers():
            ids.add(p.id)
        for p in self._plugins.embedding_providers():
            ids.add(p.id)
        results: dict[str, HealthStatus] = {}
        for pid in sorted(ids):
            results[pid] = await self.health_check(pid)
        return results

    async def register_with_health_monitor(self) -> None:
        """Register all providers with the HealthMonitor and subscribe to status changes.

        Emits ``provider_health_changed`` via the event bus whenever a provider's
        health level transitions.  The first observation for each provider always
        emits (with ``old_level=None``) so subscribers see the initial state.

        Calling this when ``health_monitor`` is None is a safe no-op.

        NOTE: does NOT auto-switch routes on UNHEALTHY — fallback remains
        request-driven to avoid flapping.
        """
        if self._health_monitor is None:
            return

        # Collect all (target, kind) pairs.
        provider_kinds: list[tuple[object, str]] = []
        for p in self._plugins.llm_providers():
            provider_kinds.append((p, "llm_provider"))
        for p in self._plugins.embedding_providers():
            provider_kinds.append((p, "embedding_provider"))

        # Register each provider with the monitor.
        # We also build a local id→kind map for use in the subscriber.
        kind_map: dict[str, str] = {}
        for provider, kind in provider_kinds:
            target = HealthTarget(id=provider.id, kind=kind)
            self._health_monitor.register_probeable(target, provider)
            kind_map[provider.id] = kind

        # Subscribe a handler that tracks level changes and emits events.
        async def _on_health(status: HealthStatus) -> None:
            target_id = status.target_id
            new_level = status.level
            old_level = self._provider_health_levels.get(target_id)

            # If the level hasn't changed (and this isn't the first observation),
            # skip emission.
            if old_level is not None and old_level == new_level:
                return

            # Update the tracked level.
            self._provider_health_levels[target_id] = new_level

            # Emit the event.
            await self._emit(
                events.PROVIDER_HEALTH_CHANGED,
                {
                    "target_id": target_id,
                    "kind": kind_map.get(target_id, ""),
                    "old_level": old_level.value if old_level is not None else None,
                    "new_level": new_level.value,
                    "message": status.message,
                    "checked_at": status.checked_at,
                },
            )

        self._health_sub_id = self._health_monitor.subscribe(_on_health)

    # ------------------------------------------------------------------ #
    # Campaign routing helpers
    # ------------------------------------------------------------------ #

    def _campaign_yaml_path(self, campaign_id: CampaignId) -> Path | None:
        """Return the campaign.yaml path for *campaign_id*, or None if no data_root."""
        if self._data_root is None:
            return None
        return self._data_root / "campaigns" / campaign_id / "campaign.yaml"

    async def _load_campaign_routing(self, campaign_id: CampaignId) -> None:
        """Lazily read routing blocks from campaign.yaml and apply them.

        Recognises four top-level blocks:
          * ``model_routing`` — applied to the LLM resolver.
          * ``embedding_routing`` — applied to the same resolver; task name
            uniqueness keeps it disjoint from LLM entries. Entries here
            override anything with the same task name in ``model_routing``.
          * ``imagegen_routing`` — stored in ``_imagegen_routes`` and
            consulted by ImageGenService via :meth:`imagegen_route`. The
            resolver here only knows LLM/embedding kinds, so a parallel
            dict holds these.
          * ``model_tiers`` — a flat dict mapping tier names ("heavy",
            "light", "embedding") to ``provider.model`` routes. Applied
            to the resolver's tier dict; used as a fallback between
            per-task overrides and app defaults.

        Always marks the campaign as loaded — even on failure — so we don't
        re-attempt on every subsequent call.
        """
        self._loaded_campaigns.add(campaign_id)
        yaml_path = self._campaign_yaml_path(campaign_id)
        if yaml_path is None or not yaml_path.is_file():
            return
        try:
            raw = load_yaml(yaml_path)
        except Exception:
            logger.warning(
                "llm_gateway: failed to parse %s; campaign routing not loaded",
                yaml_path,
            )
            return
        if not isinstance(raw, dict):
            return

        await self._apply_routing_block(
            raw.get("model_routing"),
            campaign_id,
            yaml_path,
            block_name="model_routing",
            provider_kind="llm",
        )
        await self._apply_routing_block(
            raw.get("embedding_routing"),
            campaign_id,
            yaml_path,
            block_name="embedding_routing",
            provider_kind="embedding",
        )
        await self._apply_imagegen_routing(raw.get("imagegen_routing"), campaign_id, yaml_path)
        await self._apply_tier_routing(raw.get("model_tiers"), campaign_id, yaml_path)

    async def _apply_tier_routing(
        self,
        block: object,
        campaign_id: CampaignId,
        yaml_path: Path,
    ) -> None:
        """Read the ``model_tiers`` block and populate the resolver.

        Block shape: ``{"heavy": "provider.model", "light": "...",
        "embedding": "..."}``. Unknown keys are ignored with a debug log;
        malformed route strings are skipped with a warning. The campaign
        keeps any per-task overrides loaded earlier.

        Async to match the sibling ``_apply_*_routing`` helpers, even
        though no I/O happens here today — future tier-validation work
        (e.g. checking provider availability) may need to await.
        """
        if not isinstance(block, dict):
            return
        for key, value in block.items():
            try:
                tier = Tier(str(key))
            except ValueError:
                logger.debug(
                    "llm_gateway: unknown tier %r in %s; skipping",
                    key,
                    yaml_path,
                )
                continue
            if not isinstance(value, str) or not value:
                continue
            try:
                self._router.set_tier_route(campaign_id, tier, value)
            except ValueError as exc:
                logger.warning(
                    "llm_gateway: bad route %r for tier %s in %s: %s",
                    value,
                    tier.value,
                    yaml_path,
                    exc,
                )

    async def _apply_imagegen_routing(
        self,
        routing: object,
        campaign_id: CampaignId,
        yaml_path: Path,
    ) -> None:
        """Validate and store ``imagegen_routing`` entries for later lookup."""
        if not isinstance(routing, dict):
            return
        bucket = self._imagegen_routes.setdefault(campaign_id, {})
        for task, route in routing.items():
            try:
                Route.parse(str(route))
            except ValueError:
                logger.warning(
                    "llm_gateway: skipping bad imagegen_routing entry in %s — "
                    "task=%r route=%r is not a valid 'provider.model' string",
                    yaml_path,
                    task,
                    route,
                )
                continue
            bucket[str(task)] = str(route)
            await self._warn_unknown_model(str(task), str(route), provider_kind="imagegen")

    async def _apply_routing_block(
        self,
        routing: object,
        campaign_id: CampaignId,
        yaml_path: Path,
        *,
        block_name: str,
        provider_kind: str,
    ) -> None:
        """Validate and apply a routing block to the resolver."""
        if not isinstance(routing, dict):
            return
        for task, route in routing.items():
            try:
                self._router.set_route(str(task), str(route), campaign_id)
            except ValueError:
                logger.warning(
                    "llm_gateway: skipping bad %s entry in %s — "
                    "task=%r route=%r is not a valid 'provider.model' string",
                    block_name,
                    yaml_path,
                    task,
                    route,
                )
                continue
            # Cross-check: if the referenced provider is loaded, warn when the
            # model id isn't in its advertised list. Never block on a failed
            # list_models() — providers commonly raise when unconfigured.
            await self._warn_unknown_model(str(task), str(route), provider_kind=provider_kind)

    async def _warn_unknown_model(self, task: str, route: str, *, provider_kind: str) -> None:
        """Emit a warning if route.model is not advertised by the loaded provider."""
        try:
            parsed = Route.parse(route)
        except ValueError:
            return
        if provider_kind == "llm":
            provider = self._plugins.get_llm_provider(parsed.provider_id)
        elif provider_kind == "embedding":
            provider = self._plugins.get_embedding_provider(parsed.provider_id)
        elif provider_kind == "imagegen":
            getter = getattr(self._plugins, "get_imagegen_backend", None)
            provider = getter(parsed.provider_id) if getter is not None else None
        else:
            return
        if provider is None:
            return
        list_models = getattr(provider, "list_models", None)
        if list_models is None:
            return
        try:
            models = await list_models()
        except Exception:
            # Providers commonly raise when unconfigured; skip silently.
            return
        try:
            known_ids = {m.id for m in models}
        except Exception:
            return
        if parsed.model not in known_ids:
            logger.warning(
                "llm_gateway: route task=%r references model %r on provider %r "
                "which is not in its advertised list (kind=%s); applying anyway",
                task,
                parsed.model,
                parsed.provider_id,
                provider_kind,
            )

    _BLOCK_FOR_KIND: ClassVar[dict[str, str]] = {
        "llm": "model_routing",
        "embedding": "embedding_routing",
        "imagegen": "imagegen_routing",
    }

    def _persist_campaign_route(
        self,
        campaign_id: CampaignId,
        task: str,
        route: str,
        *,
        kind: str = "llm",
    ) -> None:
        """Synchronously write a route into the right block of campaign.yaml.

        Unknown ``kind`` falls back to ``model_routing`` (back-compat).
        """
        block = self._BLOCK_FOR_KIND.get(kind, "model_routing")
        data = self._read_campaign_yaml_for_write(campaign_id)
        if data is None:
            return
        if block not in data or not isinstance(data[block], dict):
            data[block] = {}
        data[block][task] = route
        self._atomic_write_campaign_yaml(campaign_id, data)

    def _delete_campaign_route(
        self,
        campaign_id: CampaignId,
        task: str,
        *,
        kind: str = "llm",
    ) -> None:
        """Remove ``task`` from the block and rewrite the file."""
        block = self._BLOCK_FOR_KIND.get(kind, "model_routing")
        data = self._read_campaign_yaml_for_write(campaign_id)
        if data is None:
            return
        existing = data.get(block)
        if not isinstance(existing, dict) or task not in existing:
            return
        existing.pop(task, None)
        if not existing:
            data.pop(block, None)
        self._atomic_write_campaign_yaml(campaign_id, data)

    def _read_campaign_yaml_for_write(self, campaign_id: CampaignId) -> dict | None:
        """Read campaign.yaml into a dict for in-place editing.

        Returns ``None`` when there is no ``data_root`` to write to. Creates
        the parent directory as a side effect.
        """
        yaml_path = self._campaign_yaml_path(campaign_id)
        if yaml_path is None:
            return None
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        if not yaml_path.is_file():
            return {}
        try:
            raw = load_yaml(yaml_path)
        except Exception:
            logger.warning(
                "llm_gateway: could not read %s for route persistence; "
                "existing content may be overwritten",
                yaml_path,
            )
            return {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _atomic_write_campaign_yaml(self, campaign_id: CampaignId, data: dict) -> None:
        yaml_path = self._campaign_yaml_path(campaign_id)
        if yaml_path is None:
            return
        tmp_path = yaml_path.with_suffix(".yaml.tmp")
        tmp_path.write_text(dump_yaml(data), encoding="utf-8")
        os.replace(tmp_path, yaml_path)

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _require_llm(self, provider_id: str) -> LLMProvider:
        provider = self._plugins.get_llm_provider(provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id, kind="llm")
        return provider

    def _require_embedding(self, provider_id: str) -> EmbeddingProvider:
        provider = self._plugins.get_embedding_provider(provider_id)
        if provider is None:
            raise ProviderNotFoundError(provider_id, kind="embedding")
        return provider


async def _anext(iterator: AsyncIterator[CompletionChunk]) -> CompletionChunk:
    return await iterator.__anext__()


__all__ = ["LLMGatewayService"]
