"""Delta-set first-class helpers on :class:`StateStore`.

See ``docs/superpowers/specs/2026-05-19-swipes-alternates-design.md``.
"""

from __future__ import annotations

import pytest

from grimoire.state_store import StateStore
from grimoire.state_store.errors import StateStoreError

CAMPAIGN_ID = "c1"


def _char_delta(mood: str, drift: float = 0.0) -> dict:
    return {
        "kind": "character_state_update",
        "target_scope": "campaign-sqlite",
        "target_table": "character_state",
        "target_id": "lib:winifred",
        "after": {
            "character_ref": "lib:winifred",
            "campaign_id": CAMPAIGN_ID,
            "emotional_state": mood,
            "drift_score": drift,
        },
    }


async def _seed(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id=CAMPAIGN_ID, name="Test")


async def test_apply_delta_set_tags_every_delta(store: StateStore) -> None:
    await _seed(store)
    records = await store.apply_delta_set(
        deltas=[_char_delta("guarded"), _char_delta("guarded", drift=0.1)],
        delta_set_id="ds_abc",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
        source="orchestrator:regenerate",
    )
    assert len(records) == 2
    assert all(r.delta_set_id == "ds_abc" for r in records)


async def test_rewind_delta_set_lifo(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta_set(
        deltas=[_char_delta("a"), _char_delta("b")],
        delta_set_id="ds_1",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
        source="test",
    )
    state = await store.resolve_character_state(
        character_ref="lib:winifred", campaign_id=CAMPAIGN_ID
    )
    assert state["emotional_state"] == "b"

    reversed_records = await store.rewind_delta_set("ds_1", campaign_id=CAMPAIGN_ID)
    assert len(reversed_records) == 2
    # LIFO: the second applied is reversed first
    assert reversed_records[0].after["emotional_state"] == "b"
    assert reversed_records[1].after["emotional_state"] == "a"
    # All reversed
    assert all(r.reversed_at is not None for r in reversed_records)
    # Underlying state reverted entirely
    after = await store.resolve_character_state(
        character_ref="lib:winifred", campaign_id=CAMPAIGN_ID
    )
    assert after is None or after.get("emotional_state") not in {"a", "b"}


async def test_swap_delta_set_fresh_apply_atomic(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta_set(
        deltas=[_char_delta("calm")],
        delta_set_id="ds_orig",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
        source="test",
    )
    result = await store.swap_delta_set(
        rewind_set_id="ds_orig",
        apply_deltas=[_char_delta("furious", drift=0.5)],
        apply_set_id="ds_new",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_2",
        source="orchestrator:switch-primary",
    )
    assert len(result.rewound) == 1
    assert len(result.applied) == 1
    assert result.applied[0].delta_set_id == "ds_new"
    state = await store.resolve_character_state(
        character_ref="lib:winifred", campaign_id=CAMPAIGN_ID
    )
    assert state["emotional_state"] == "furious"


async def test_swap_delta_set_reactivate_existing(store: StateStore) -> None:
    await _seed(store)
    # Apply two distinct sets in sequence; rewind one, then swap back.
    await store.apply_delta_set(
        deltas=[_char_delta("calm")],
        delta_set_id="ds_a",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
        source="test",
    )
    await store.apply_delta_set(
        deltas=[_char_delta("anxious", drift=0.4)],
        delta_set_id="ds_b",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_2",
        source="test",
    )
    # Rewind ds_b so it lives in the deltas table but is currently inactive.
    await store.rewind_delta_set("ds_b", campaign_id=CAMPAIGN_ID)
    # Now swap: rewind ds_a, re-activate ds_b.
    result = await store.swap_delta_set(
        rewind_set_id="ds_a",
        apply_deltas=None,
        apply_set_id="ds_b",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_3",
        source="orchestrator:switch-primary",
    )
    assert len(result.applied) == 1
    state = await store.resolve_character_state(
        character_ref="lib:winifred", campaign_id=CAMPAIGN_ID
    )
    assert state["emotional_state"] == "anxious"


async def test_swap_atomic_rollback_on_apply_failure(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta_set(
        deltas=[_char_delta("calm")],
        delta_set_id="ds_orig",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
        source="test",
    )
    # Construct a delta whose apply will fail: campaign-sqlite without target_table.
    bad = {
        "kind": "character_state_update",
        "target_scope": "campaign-sqlite",
        "target_id": "lib:winifred",
        "after": {"character_ref": "lib:winifred"},
    }
    with pytest.raises(StateStoreError):
        await store.swap_delta_set(
            rewind_set_id="ds_orig",
            apply_deltas=[bad],
            apply_set_id="ds_bad",
            campaign_id=CAMPAIGN_ID,
            turn_id="t_2",
            source="test",
        )
    # Original still in effect — rewind rolled back too.
    state = await store.resolve_character_state(
        character_ref="lib:winifred", campaign_id=CAMPAIGN_ID
    )
    assert state["emotional_state"] == "calm"


async def test_current_delta_set_for_post(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta_set(
        deltas=[_char_delta("x")],
        delta_set_id="ds_p",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
        source="test",
    )
    await store.set_current_alternate_delta_set(
        campaign_id=CAMPAIGN_ID,
        post_id="p_1",
        delta_set_id="ds_p",
    )
    assert await store.current_delta_set_for(post_id="p_1", campaign_id=CAMPAIGN_ID) == "ds_p"
    # set_id form
    assert (
        await store.current_delta_set_for(
            post_id=None,
            campaign_id=CAMPAIGN_ID,
            set_id="ds_p",
        )
        == "ds_p"
    )
    # unknown post returns None
    assert await store.current_delta_set_for(post_id="nope", campaign_id=CAMPAIGN_ID) is None
