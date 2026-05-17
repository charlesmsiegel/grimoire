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
from grimoire.llm_gateway.retry import RETRIABLE_EXCEPTIONS, run_with_retries
from grimoire.llm_gateway.routing import Route, RouteResolver
from grimoire.storage.db import Database
from grimoire.types.common import CampaignId, HealthLevel, HealthStatus, TurnId
from grimoire.types.llm import (
    CompletionChunk,
    CompletionRequest,
    CompletionResponse,
    ModelInfo,
    TokenUsage,
)
from grimoire.types.protocols import EmbeddingProvider, LLMProvider, Plugins

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
    ) -> None:
        self._plugins = plugins
        self._db = db
        self._config = config or GatewayConfig()
        self._data_root = data_root
        self._event_bus = event_bus
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
        # (provider_id, model) → ModelInfo or None (None = "no pricing available")
        self._pricing_cache: dict[tuple[str, str], ModelInfo | None] = {}

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
    ) -> None:
        self._router.set_route(task, route, campaign_id)
        if campaign_id is not None and self._data_root is not None:
            self._persist_campaign_route(campaign_id, task, route)

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
    ) -> CompletionResponse:
        if campaign_id is not None and campaign_id not in self._loaded_campaigns:
            await self._load_campaign_routing(campaign_id)
        primary = self._router.resolve(task, campaign_id)
        fallback = self._router.fallback(task)
        return await self._complete_one(
            task=task,
            route=primary,
            request=request,
            campaign_id=campaign_id,
            turn_id=turn_id,
            fallback=fallback,
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
    ) -> CompletionResponse:
        try:
            response, _retries = await self._invoke_complete(
                task=task,
                route=route,
                request=request,
                campaign_id=campaign_id,
                turn_id=turn_id,
                fallback_used=False,
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
            )
            raise
        except RETRIABLE_EXCEPTIONS as exc:
            await self._record_failure(
                task=task,
                route=route,
                request=request,
                campaign_id=campaign_id,
                turn_id=turn_id,
                error=exc,
                retries=self._config.retry.max_retries,
                fallback_used=False,
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
                )
                raise
            except RETRIABLE_EXCEPTIONS as fallback_exc:
                await self._record_failure(
                    task=task,
                    route=fallback,
                    request=request,
                    campaign_id=campaign_id,
                    turn_id=turn_id,
                    error=fallback_exc,
                    retries=self._config.retry.max_retries,
                    fallback_used=True,
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
    ) -> tuple[CompletionResponse, int]:
        provider = self._require_llm(route.provider_id)
        scoped = request.model_copy(update={"model": route.model})
        timeout = self._config.timeout.total_seconds

        await self._emit(
            "llm_request_started",
            {
                "task": task,
                "provider": route.provider_id,
                "model": route.model,
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "fallback_used": fallback_used,
            },
        )

        async def _call() -> CompletionResponse:
            return await asyncio.wait_for(provider.complete(scoped), timeout=timeout)

        started = time.monotonic()
        response, retries = await run_with_retries(_call, policy=self._config.retry)
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
            )
        await self._emit(
            "llm_response_received",
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
    ) -> None:
        await self._emit(
            "llm_request_failed",
            {
                "task": task,
                "provider": route.provider_id,
                "model": route.model,
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "error": f"{type(error).__name__}: {error}",
                "retries": retries,
                "fallback_used": fallback_used,
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
    ) -> AsyncIterator[CompletionChunk]:
        if campaign_id is not None and campaign_id not in self._loaded_campaigns:
            await self._load_campaign_routing(campaign_id)
        route = self._router.resolve(task, campaign_id)
        provider = self._require_llm(route.provider_id)
        scoped = request.model_copy(update={"model": route.model})
        async for chunk in self._stream_one(
            task=task,
            route=route,
            provider=provider,
            request=scoped,
            campaign_id=campaign_id,
            turn_id=turn_id,
        ):
            yield chunk

    async def _stream_one(
        self,
        *,
        task: str,
        route: Route,
        provider: LLMProvider,
        request: CompletionRequest,
        campaign_id: CampaignId | None,
        turn_id: TurnId | None,
    ) -> AsyncIterator[CompletionChunk]:
        first_token_timeout = self._config.timeout.first_token_seconds
        total_timeout = self._config.timeout.total_seconds
        started = time.monotonic()

        await self._emit(
            "llm_request_started",
            {
                "task": task,
                "provider": route.provider_id,
                "model": route.model,
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "fallback_used": False,
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
        except (PermanentError, *RETRIABLE_EXCEPTIONS) as exc:
            await self._record_failure(
                task=task,
                route=route,
                request=request,
                campaign_id=campaign_id,
                turn_id=turn_id,
                error=exc,
                retries=0,
                fallback_used=False,
            )
            raise
        finally:
            close = getattr(stream, "aclose", None)
            if close is not None:
                with contextlib.suppress(Exception):
                    await close()
        latency_ms = int((time.monotonic() - started) * 1000)
        if self._config.observability.log_all_requests:
            await self._log.record(
                task=task,
                provider_id=route.provider_id,
                model=route.model,
                usage=usage,
                latency_ms=latency_ms,
                retries=0,
                fallback_used=False,
                request_hash=request_hash(request),
                response_text="".join(text_parts),
                campaign_id=campaign_id,
                turn_id=turn_id,
            )
        await self._emit(
            "llm_response_received",
            {
                "task": task,
                "provider": route.provider_id,
                "model": route.model,
                "campaign_id": campaign_id,
                "turn_id": turn_id,
                "latency_ms": latency_ms,
                "retries": 0,
                "fallback_used": False,
                "usage": {
                    "input_tokens": usage.input_tokens if usage else 0,
                    "output_tokens": usage.output_tokens if usage else 0,
                    "total_tokens": usage.total_tokens if usage else 0,
                },
                "cost_estimate_usd": None,
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
            await self._emit(
                "embedding_request_started",
                {
                    "task": task,
                    "provider": route.provider_id,
                    "model": model_id,
                    "campaign_id": campaign_id,
                    "turn_id": turn_id,
                    "input_count": len(missing),
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

                    async def _call(_b: list[str] = _batch) -> list[list[float]]:
                        return await asyncio.wait_for(
                            provider.embed(_b),
                            timeout=self._config.timeout.total_seconds,
                        )

                    batch_vectors, batch_retries = await run_with_retries(
                        _call, policy=self._config.retry
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
                    )
                await self._emit(
                    "llm_request_failed",
                    {
                        "task": task,
                        "provider": route.provider_id,
                        "model": model_id,
                        "campaign_id": campaign_id,
                        "turn_id": turn_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "retries": total_retries,
                        "fallback_used": False,
                    },
                )
                raise
            except RETRIABLE_EXCEPTIONS as exc:
                # `total_retries` sums batches that succeeded; add the
                # exhausted-retries count for the batch that finally failed.
                final_retries = total_retries + self._config.retry.max_retries
                if self._config.observability.log_all_requests:
                    await self._log.record(
                        task=task,
                        provider_id=route.provider_id,
                        model=model_id,
                        retries=final_retries,
                        error=f"{type(exc).__name__}: {exc}",
                        campaign_id=campaign_id,
                        turn_id=turn_id,
                    )
                await self._emit(
                    "llm_request_failed",
                    {
                        "task": task,
                        "provider": route.provider_id,
                        "model": model_id,
                        "campaign_id": campaign_id,
                        "turn_id": turn_id,
                        "error": f"{type(exc).__name__}: {exc}",
                        "retries": final_retries,
                        "fallback_used": False,
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
                )
            await self._emit(
                "embedding_response_received",
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

    # ------------------------------------------------------------------ #
    # Campaign routing helpers
    # ------------------------------------------------------------------ #

    def _campaign_yaml_path(self, campaign_id: CampaignId) -> Path | None:
        """Return the campaign.yaml path for *campaign_id*, or None if no data_root."""
        if self._data_root is None:
            return None
        return self._data_root / "campaigns" / campaign_id / "campaign.yaml"

    async def _load_campaign_routing(self, campaign_id: CampaignId) -> None:
        """Lazily read ``model_routing`` from campaign.yaml and apply routes.

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
        routing = raw.get("model_routing")
        if not isinstance(routing, dict):
            return
        for task, route in routing.items():
            try:
                self._router.set_route(str(task), str(route), campaign_id)
            except ValueError:
                logger.warning(
                    "llm_gateway: skipping bad model_routing entry in %s — "
                    "task=%r route=%r is not a valid 'provider.model' string",
                    yaml_path,
                    task,
                    route,
                )

    def _persist_campaign_route(self, campaign_id: CampaignId, task: str, route: str) -> None:
        """Synchronously write the route change back to campaign.yaml atomically."""
        yaml_path = self._campaign_yaml_path(campaign_id)
        if yaml_path is None:
            return
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        # Read existing data, if any.
        data: dict = {}
        if yaml_path.is_file():
            try:
                raw = load_yaml(yaml_path)
                if isinstance(raw, dict):
                    data = dict(raw)
            except Exception:
                logger.warning(
                    "llm_gateway: could not read %s for route persistence; "
                    "existing content may be overwritten",
                    yaml_path,
                )
        if "model_routing" not in data or not isinstance(data["model_routing"], dict):
            data["model_routing"] = {}
        data["model_routing"][task] = route
        # Atomic write: write to .tmp then rename.
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
