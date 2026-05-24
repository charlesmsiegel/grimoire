"""Per-(campaign, branch) factory for :class:`ContinuityService`.

The shipped design (`2026-05-12-continuity-design.md`) used one shared
``ContinuityService`` for the whole process, which forced API routes to
over-fetch and filter by ``campaign_id`` client-side. The registry hands
out one ``ContinuityService`` per ``(campaign_id, branch_id)`` pair,
constructing a :class:`SqliteContinuityStore` and matching
:class:`HybridFactSearchIndex` lazily on first use so reads and writes
naturally scope to one timeline.

Services that previously took a single ``Continuity`` (Time Engine,
Context Builder, Export, Orchestrator) now take the registry and resolve
the per-campaign service when they have a ``campaign_id`` in hand.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from grimoire.continuity.config import ContinuityConfig
from grimoire.continuity.hybrid_search import HybridFactSearchIndex, QueryEmbedder
from grimoire.continuity.llm_judge import LLMContradictionJudge
from grimoire.continuity.protocols import (
    ContinuityStore,
    ContradictionJudge,
    FactSearchIndex,
)
from grimoire.continuity.service import ContinuityService
from grimoire.continuity.sqlite_store import SqliteContinuityStore
from grimoire.event_bus import EventBus
from grimoire.storage import Database


def _default_branch(campaign_id: str, branch_id: str | None) -> str:
    return branch_id or f"{campaign_id}:main"


class ContinuityRegistry:
    """Lazy per-(campaign, branch) factory of :class:`ContinuityService`."""

    def __init__(
        self,
        *,
        db: Database | None = None,
        config: ContinuityConfig | None = None,
        embedder: QueryEmbedder | None = None,
        judge_gateway: Any | None = None,
        judge_request_factory: Callable[[str, str], Any] | None = None,
        event_bus: EventBus | None = None,
        store_factory: Callable[[str, str], ContinuityStore] | None = None,
        search_factory: Callable[[ContinuityStore, str, str], FactSearchIndex] | None = None,
    ) -> None:
        self._db = db
        self._config = config or ContinuityConfig()
        self._embedder = embedder
        self._judge_gateway = judge_gateway
        self._judge_request_factory = judge_request_factory
        self._event_bus = event_bus
        self._store_factory = store_factory
        self._search_factory = search_factory
        self._services: dict[tuple[str, str], ContinuityService] = {}

    def set_embedder(self, embedder: QueryEmbedder | None) -> None:
        self._embedder = embedder

    def set_judge(
        self,
        gateway: Any,
        request_factory: Callable[[str, str], Any] | None,
    ) -> None:
        self._judge_gateway = gateway
        self._judge_request_factory = request_factory

    @property
    def config(self) -> ContinuityConfig:
        return self._config

    @property
    def event_bus(self) -> EventBus | None:
        return self._event_bus

    def for_campaign(
        self,
        campaign_id: str,
        *,
        branch_id: str | None = None,
    ) -> ContinuityService:
        branch = _default_branch(campaign_id, branch_id)
        key = (campaign_id, branch)
        existing = self._services.get(key)
        if existing is not None:
            return existing

        store = self._build_store(campaign_id, branch)
        search = self._build_search(store, campaign_id, branch)
        judge: ContradictionJudge | None = self._build_judge()

        service = ContinuityService(
            store=store,
            search_index=search,
            judge=judge,
            config=self._config,
            event_bus=self._event_bus,
            campaign_id=campaign_id,
            branch_id=branch,
        )
        self._services[key] = service
        return service

    def cached_services(self) -> list[ContinuityService]:
        return list(self._services.values())

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_store(self, campaign_id: str, branch: str) -> ContinuityStore:
        if self._store_factory is not None:
            return self._store_factory(campaign_id, branch)
        if self._db is None:
            raise RuntimeError(
                "ContinuityRegistry requires a Database when no store_factory is supplied"
            )
        return SqliteContinuityStore(self._db, campaign_id=campaign_id, branch_id=branch)

    def _build_search(
        self, store: ContinuityStore, campaign_id: str, branch: str
    ) -> FactSearchIndex | None:
        if self._search_factory is not None:
            return self._search_factory(store, campaign_id, branch)
        if self._db is None:
            return None
        return HybridFactSearchIndex(
            store,
            self._db,
            campaign_id=campaign_id,
            branch_id=branch,
            embedder=self._embedder,
        )

    def _build_judge(self) -> ContradictionJudge | None:
        if self._judge_gateway is None or self._judge_request_factory is None:
            return None
        return LLMContradictionJudge(
            self._judge_gateway,
            self._judge_request_factory,
            task=self._config.contradiction_check.model_route,
        )


class ContinuityRegistryExportAdapter:
    """Adapts :class:`ContinuityRegistry` to export's ``ContinuitySource``.

    The export pipeline (``grimoire.export.snapshot``) wants
    ``list_facts(campaign_id)`` / ``list_commitments(campaign_id)``; the
    registry exposes per-campaign services with different signatures.
    """

    def __init__(self, registry: ContinuityRegistry) -> None:
        self._registry = registry

    async def list_facts(self, campaign_id: str) -> list[Any]:
        service = self._registry.for_campaign(campaign_id)
        return await service.facts_about(limit=10_000, include_retired=False)

    async def list_commitments(self, campaign_id: str) -> list[Any]:
        service = self._registry.for_campaign(campaign_id)
        return await service.all_commitments()


def resolve_continuity(target: Any, campaign_id: str, *, branch_id: str | None = None) -> Any:
    """Return a per-campaign ``Continuity`` from either a registry or a
    plain service.

    The shipped wiring used to pass a single ``ContinuityService`` to
    consumers (Context Builder, Time Engine, Orchestrator). The
    registry-based wiring passes the registry instead. Consumers should
    funnel every per-campaign call through this helper so they accept
    both shapes — keeps backwards compat with the older tests/
    fixtures that hand-construct a service.
    """
    if target is None:
        return None
    for_campaign = getattr(target, "for_campaign", None)
    if callable(for_campaign):
        if branch_id is None:
            return for_campaign(campaign_id)
        return for_campaign(campaign_id, branch_id=branch_id)
    return target


def make_judge_request_factory(model_route: str = "drift_check") -> Callable[[str, str], Any]:
    """Build a callable that produces ``CompletionRequest`` instances."""

    from grimoire.types.llm import CompletionRequest, Message

    def factory(system: str, user: str) -> Any:
        return CompletionRequest(
            model=model_route,
            system=system,
            messages=[Message(role="user", content=user)],
            max_tokens=512,
            temperature=0.0,
        )

    return factory


__all__ = [
    "ContinuityRegistry",
    "ContinuityRegistryExportAdapter",
    "make_judge_request_factory",
    "resolve_continuity",
]
