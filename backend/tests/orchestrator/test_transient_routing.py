"""Orchestrator routes ExtractionResult.transient_updates and emits audit
fragments."""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.state_store import StateStore
from grimoire.storage import Database, apply_migrations
from grimoire.transient_state import TransientStateService
from grimoire.transient_state.config import TransientStateConfig
from grimoire.types.transient import (
    EntityKind,
    TransientUpdateProposal,
)


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    try:
        yield StateStore(db, data_root)
    finally:
        await db.close()


@pytest.fixture
def service(store: StateStore) -> TransientStateService:
    return TransientStateService(store, config=TransientStateConfig())


@pytest.fixture
async def seeded_campaign(store: StateStore) -> str:
    await store.upsert_campaign(campaign_id="c_test", name="Test campaign")
    return "c_test"


async def test_route_transient_updates_emits_fragment(
    service: TransientStateService, seeded_campaign: str
):
    """Simulate the orchestrator post-extract block: call the helper, then
    emit the fragment shape the orchestrator emits, and confirm the
    payload carries writes/conflicts under the expected keys.
    """
    from grimoire.transient_state.routing import route_transient_updates

    bus = EventBus()
    captured: list[Event] = []

    async def listener(event: Event) -> None:
        captured.append(event)

    bus.subscribe("turn_audit_fragment", listener)

    proposal = TransientUpdateProposal(
        entity_kind=EntityKind.CHARACTER,
        entity_id="char_x",
        field="mood",
        value="guarded",
        confidence=0.95,
        evidence="...",
    )
    summary = await route_transient_updates(
        campaign_id=seeded_campaign,
        proposals=[proposal],
        transient_state=service,
        source_post_id="p_1",
    )
    if summary.writes or summary.conflicts:
        await bus.emit(
            Event(
                type="turn_audit_fragment",
                payload={
                    "turn_id": "t1",
                    "campaign_id": seeded_campaign,
                    "transient_state_writes": summary.writes,
                    "transient_state_conflicts": summary.conflicts,
                },
            )
        )
    assert len(captured) == 1
    payload = captured[0].payload
    assert payload["turn_id"] == "t1"
    assert payload["transient_state_writes"][0]["field"] == "mood"
