"""Tests for the per-campaign ContinuityRegistry (§1, §2, §3)."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.continuity import (
    ContinuityRegistry,
    ContinuityRegistryExportAdapter,
    InMemoryContinuityStore,
    resolve_continuity,
)
from grimoire.continuity.types import Fact, FactSource, FactSubject, InGameTime
from grimoire.event_bus import EventBus
from grimoire.storage import Database, apply_migrations

pytestmark = pytest.mark.asyncio


def _fact(text: str, post: str = "p-1") -> Fact:
    return Fact(
        id="",
        text=text,
        established_in_post=post,
        established_at_in_game=InGameTime(day_count=1),
        confidence=0.9,
        source=FactSource.NARRATOR,
        about=FactSubject(),
    )


async def _seed_post(db: Database, *, post_id: str, campaign_id: str) -> None:
    await db.execute(
        """
        INSERT INTO posts (id, scene_id, campaign_id, order_in_scene, author_kind)
        VALUES (?, NULL, ?, 0, 'narrator')
        """,
        (post_id, campaign_id),
    )


async def test_for_campaign_caches_per_pair(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite", pool_size=2)
    await db.connect()
    try:
        await apply_migrations(db)
        registry = ContinuityRegistry(db=db)
        a1 = registry.for_campaign("camp-a")
        a2 = registry.for_campaign("camp-a")
        b1 = registry.for_campaign("camp-b")
        assert a1 is a2  # cached
        assert a1 is not b1
    finally:
        await db.close()


async def test_facts_isolated_per_campaign(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite", pool_size=2)
    await db.connect()
    try:
        await apply_migrations(db)
        await _seed_post(db, post_id="p-1", campaign_id="camp-a")
        await _seed_post(db, post_id="p-2", campaign_id="camp-b")
        registry = ContinuityRegistry(db=db)
        a = registry.for_campaign("camp-a")
        b = registry.for_campaign("camp-b")
        await a.add_fact(_fact("apple", post="p-1"), source="user")
        await b.add_fact(_fact("banana", post="p-2"), source="user")
        a_facts = await a.facts_about(limit=50)
        b_facts = await b.facts_about(limit=50)
        a_texts = {f.text for f in a_facts}
        b_texts = {f.text for f in b_facts}
        assert "apple" in a_texts
        assert "banana" not in a_texts
        assert "banana" in b_texts
        assert "apple" not in b_texts
    finally:
        await db.close()


async def test_event_bus_propagates(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite", pool_size=2)
    await db.connect()
    try:
        await apply_migrations(db)
        bus = EventBus()
        captured = []

        async def handler(event) -> None:
            captured.append(event)

        bus.subscribe("fact_recorded", handler)
        await _seed_post(db, post_id="p-1", campaign_id="camp-a")
        registry = ContinuityRegistry(db=db, event_bus=bus)
        a = registry.for_campaign("camp-a")
        await a.add_fact(_fact("hi", post="p-1"), source="user")
        assert captured
        assert captured[0].payload["campaign_id"] == "camp-a"
    finally:
        await db.close()


async def test_store_factory_override() -> None:
    """A test passing its own store_factory should bypass the db requirement."""
    stores: dict[str, InMemoryContinuityStore] = {}

    def factory(campaign_id: str) -> InMemoryContinuityStore:
        if campaign_id not in stores:
            stores[campaign_id] = InMemoryContinuityStore()
        return stores[campaign_id]

    registry = ContinuityRegistry(store_factory=factory)
    a = registry.for_campaign("camp-a")
    await a.add_fact(_fact("memory only"), source="user")
    rows = await a.facts_about(limit=50)
    assert rows
    assert "camp-a" in stores


async def test_export_adapter_lists_facts_and_commitments(tmp_path: Path) -> None:
    from grimoire.continuity.types import Commitment, CommitmentKind

    db = Database(tmp_path / "g.sqlite", pool_size=2)
    await db.connect()
    try:
        await apply_migrations(db)
        await _seed_post(db, post_id="p-1", campaign_id="camp-a")
        registry = ContinuityRegistry(db=db)
        adapter = ContinuityRegistryExportAdapter(registry)
        a = registry.for_campaign("camp-a")
        await a.add_fact(_fact("exportable", post="p-1"), source="user")
        await a.add_commitment(
            Commitment(
                id="",
                kind=CommitmentKind.PROMISE,
                text="A vow",
                created_in_post="p-1",
                in_game_created_at=InGameTime(day_count=1),
            ),
            source="user",
        )
        facts = await adapter.list_facts("camp-a")
        commitments = await adapter.list_commitments("camp-a")
        assert any(f.text == "exportable" for f in facts)
        assert any(c.text == "A vow" for c in commitments)
    finally:
        await db.close()


async def test_judge_uses_configured_model_route() -> None:
    """`contradiction_check.model_route` must flow into `LLMContradictionJudge.task`."""
    from grimoire.continuity.config import ContinuityConfig, ContradictionCheckConfig
    from grimoire.continuity.llm_judge import LLMContradictionJudge

    class _Gateway:
        async def complete(self, task, request, *, turn_id=None):  # pragma: no cover - unused
            raise AssertionError("not invoked in this test")

    def _factory(system: str, user: str) -> object:
        return object()

    config = ContinuityConfig(
        contradiction_check=ContradictionCheckConfig(model_route="custom_route"),
    )
    registry = ContinuityRegistry(
        config=config,
        judge_gateway=_Gateway(),
        judge_request_factory=_factory,
        store_factory=lambda c: InMemoryContinuityStore(),
    )
    service = registry.for_campaign("camp-x")
    judge = service._judge
    assert isinstance(judge, LLMContradictionJudge)
    assert judge._task == "custom_route"


async def test_resolve_continuity_unwraps_registry_or_passes_service() -> None:
    """Helper accepts either a registry (uses .for_campaign) or a bare service."""

    class FakeRegistry:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def for_campaign(self, campaign_id: str):
            self.calls.append(campaign_id)
            return f"service-for-{campaign_id}"

    reg = FakeRegistry()
    assert resolve_continuity(reg, "c1") == "service-for-c1"
    assert reg.calls == ["c1"]

    sentinel = object()
    assert resolve_continuity(sentinel, "c1") is sentinel
    assert resolve_continuity(None, "c1") is None
