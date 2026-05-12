"""SQLite-target deltas: apply, reverse, queue-for-review."""

from __future__ import annotations

import pytest

from grimoire.state_store import StateStore
from grimoire.state_store.errors import StateStoreError


async def _seed(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="Test")


async def test_apply_delta_writes_row_and_logs(store: StateStore) -> None:
    await _seed(store)
    delta = {
        "kind": "character_state_update",
        "target_scope": "campaign-sqlite",
        "target_table": "character_state",
        "target_id": "lib:winifred",
        "after": {
            "character_ref": "lib:winifred",
            "campaign_id": "c1",
            "branch_id": "c1:main",
            "emotional_state": "wary",
            "drift_score": 0.0,
        },
    }
    delta_id = await store.apply_delta(
        delta=delta, source="extractor", turn_id="t1", branch_id="c1:main"
    )
    assert delta_id

    state = await store.resolve_character_state(character_ref="lib:winifred", branch_id="c1:main")
    assert state["emotional_state"] == "wary"

    log = await store.get_delta_log()
    assert len(log) == 1
    assert log[0].kind == "character_state_update"
    assert log[0].before is None  # no prior row


async def test_reverse_delta_restores_prior_row(store: StateStore) -> None:
    await _seed(store)
    base = {
        "character_ref": "lib:winifred",
        "campaign_id": "c1",
        "branch_id": "c1:main",
        "emotional_state": "calm",
        "drift_score": 0.0,
    }
    await store.apply_delta(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "after": base,
        },
        source="seed",
    )
    update = dict(base, emotional_state="wary", drift_score=0.3)
    delta_id = await store.apply_delta(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "after": update,
        },
        source="extractor",
    )

    # Sanity: latest write is visible.
    state = await store.resolve_character_state(character_ref="lib:winifred", branch_id="c1:main")
    assert state["emotional_state"] == "wary"

    await store.reverse_delta(delta_id)
    rolled_back = await store.resolve_character_state(
        character_ref="lib:winifred", branch_id="c1:main"
    )
    assert rolled_back["emotional_state"] == "calm"
    assert rolled_back["drift_score"] == 0.0


async def test_reverse_delta_deletes_inserted_row(store: StateStore) -> None:
    await _seed(store)
    delta_id = await store.apply_delta(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "after": {
                "character_ref": "lib:winifred",
                "campaign_id": "c1",
                "branch_id": "c1:main",
                "emotional_state": "wary",
            },
        },
        source="extractor",
    )
    await store.reverse_delta(delta_id)
    state = await store.resolve_character_state(character_ref="lib:winifred", branch_id="c1:main")
    assert state is None


async def test_double_reversal_is_rejected(store: StateStore) -> None:
    await _seed(store)
    delta_id = await store.apply_delta(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "after": {
                "character_ref": "lib:winifred",
                "campaign_id": "c1",
                "branch_id": "c1:main",
            },
        },
        source="seed",
    )
    await store.reverse_delta(delta_id)
    with pytest.raises(StateStoreError, match="already reversed"):
        await store.reverse_delta(delta_id)


async def test_review_queue_flow(store: StateStore) -> None:
    await _seed(store)
    review_id = await store.queue_for_review(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "confidence": 0.7,
            "after": {
                "character_ref": "lib:emergent",
                "campaign_id": "c1",
                "branch_id": "c1:main",
                "emotional_state": "uncertain",
            },
        },
        campaign_id="c1",
        source="extractor",
    )
    # Queued but not yet applied:
    assert (
        await store.resolve_character_state(character_ref="lib:emergent", branch_id="c1:main")
    ) is None

    delta_id = await store.approve_review_item(review_id)
    assert delta_id

    state = await store.resolve_character_state(character_ref="lib:emergent", branch_id="c1:main")
    assert state["emotional_state"] == "uncertain"


async def test_review_rejection_marks_delta_reversed(store: StateStore) -> None:
    await _seed(store)
    review_id = await store.queue_for_review(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "confidence": 0.6,
            "after": {
                "character_ref": "lib:emergent",
                "campaign_id": "c1",
                "branch_id": "c1:main",
            },
        },
        campaign_id="c1",
    )
    await store.reject_review_item(review_id, notes="not credible")
    # Active delta log (reversed excluded) should be empty.
    active = await store.get_delta_log(include_reversed=False)
    assert active == []


async def test_get_delta_log_filters_by_turn(store: StateStore) -> None:
    await _seed(store)
    for turn in ("t1", "t1", "t2"):
        await store.apply_delta(
            delta={
                "kind": "character_state_update",
                "target_scope": "campaign-sqlite",
                "target_table": "character_state",
                "after": {
                    "character_ref": f"lib:{turn}",
                    "campaign_id": "c1",
                    "branch_id": "c1:main",
                },
            },
            source="extractor",
            turn_id=turn,
        )
    t1_log = await store.get_delta_log(turn_id="t1")
    assert len(t1_log) == 2
    t2_log = await store.get_delta_log(turn_id="t2")
    assert len(t2_log) == 1
