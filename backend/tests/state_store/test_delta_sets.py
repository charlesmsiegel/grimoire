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


# ---------------------------------------------------------------------------
# swap_turn_deltas (#583): atomic reverse-and-replace of a turn's deltas
# ---------------------------------------------------------------------------


async def _mood(store: StateStore) -> str | None:
    state = await store.resolve_character_state(
        character_ref="lib:winifred", campaign_id=CAMPAIGN_ID
    )
    return state["emotional_state"] if state else None


async def test_swap_turn_deltas_replaces_turn(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta(
        delta=_char_delta("calm"), source="extractor", turn_id="t_1", campaign_id=CAMPAIGN_ID
    )
    await store.apply_delta(
        delta=_char_delta("tense"), source="extractor", turn_id="t_1", campaign_id=CAMPAIGN_ID
    )

    result = await store.swap_turn_deltas(
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
        deltas=[_char_delta("fierce")],
        source="retcon",
    )

    assert [r.after["emotional_state"] for r in result.rewound] == ["tense", "calm"]  # LIFO
    assert all(r.reversed_at is not None for r in result.rewound)
    assert len(result.applied) == 1
    assert result.applied[0].turn_id == "t_1"
    assert result.applied[0].source == "retcon"
    assert await _mood(store) == "fierce"


async def test_swap_turn_deltas_rolls_back_on_apply_failure(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta(
        delta=_char_delta("calm"), source="extractor", turn_id="t_1", campaign_id=CAMPAIGN_ID
    )

    good = _char_delta("fierce")
    bad = {
        "kind": "other",
        "target_scope": "campaign-sqlite",
        "target_table": "not_a_real_table",
        "target_id": "x",
        "after": {"id": "x"},
    }
    with pytest.raises(StateStoreError):
        await store.swap_turn_deltas(
            campaign_id=CAMPAIGN_ID,
            turn_id="t_1",
            deltas=[good, bad],
            source="retcon",
        )

    # The whole swap rolled back: the original delta is still active, the
    # partially-applied replacement is gone, and live state is untouched.
    log = await store.get_delta_log(campaign_id=CAMPAIGN_ID, include_reversed=True)
    assert [(r.source, r.reversed_at is None) for r in log] == [("extractor", True)]
    assert await _mood(store) == "calm"


async def test_swap_turn_deltas_rolls_back_on_reversal_failure(store: StateStore) -> None:
    await _seed(store)
    # First delta of the turn is irreversible (campaign-local scope); the
    # second reverses fine. The LIFO walk reverses the second, then fails on
    # the first — the second's reversal must be rolled back too.
    await store.apply_delta(
        delta={
            "kind": "other",
            "target_scope": "campaign-local",
            "target_id": "note-1",
            "after": {"text": "scribble"},
        },
        source="extractor",
        turn_id="t_1",
        campaign_id=CAMPAIGN_ID,
    )
    await store.apply_delta(
        delta=_char_delta("tense"), source="extractor", turn_id="t_1", campaign_id=CAMPAIGN_ID
    )

    with pytest.raises(StateStoreError):
        await store.swap_turn_deltas(
            campaign_id=CAMPAIGN_ID,
            turn_id="t_1",
            deltas=[_char_delta("fierce")],
            source="retcon",
        )

    log = await store.get_delta_log(campaign_id=CAMPAIGN_ID, include_reversed=True)
    assert len(log) == 2
    assert all(r.reversed_at is None for r in log)
    assert await _mood(store) == "tense"


async def test_swap_turn_deltas_restores_reversed_file_targets(store: StateStore) -> None:
    await _seed(store)
    write = await store.write_library_file(
        library_id="worlds/w1/characters/winifred",
        frontmatter={"name": "winifred"},
        body="edited during the turn",
        source="extractor",
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
    )
    on_disk_before = write.path.read_bytes()

    bad = {
        "kind": "other",
        "target_scope": "campaign-sqlite",
        "target_table": "not_a_real_table",
        "target_id": "x",
        "after": {"id": "x"},
    }
    with pytest.raises(StateStoreError):
        await store.swap_turn_deltas(
            campaign_id=CAMPAIGN_ID,
            turn_id="t_1",
            deltas=[bad],
            source="retcon",
        )

    # Reversing the file delta rewrote the file inside the failed transaction;
    # the snapshot/restore must put the post-turn bytes back.
    assert write.path.read_bytes() == on_disk_before
    log = await store.get_delta_log(campaign_id=CAMPAIGN_ID, include_reversed=True)
    assert all(r.reversed_at is None for r in log)


async def test_swap_turn_deltas_rejects_stale_pending_review_rows(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta(
        delta=_char_delta("calm"), source="extractor", turn_id="t_1", campaign_id=CAMPAIGN_ID
    )
    review_delta = _char_delta("suspicious")
    review_delta["turn_id"] = "t_1"
    review_id = await store.queue_for_review(
        delta=review_delta, source="extractor", campaign_id=CAMPAIGN_ID
    )

    result = await store.swap_turn_deltas(
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
        deltas=[_char_delta("fierce")],
        source="retcon",
    )

    # Only the applied delta was reversed (the queued row was never applied to
    # its target, so there is nothing to reverse) ...
    assert len(result.rewound) == 1
    assert result.rewound[0].after["emotional_state"] == "calm"
    # ... and the stale pending proposal — extracted from the retconned-away
    # text — was rejected rather than left approvable.
    assert result.rejected_review_ids == [review_id]
    row = await store.db.fetchone(
        "SELECT status, reviewer_notes FROM review_queue WHERE id = ?", (review_id,)
    )
    assert row is not None
    assert row["status"] == "rejected"
    assert row["reviewer_notes"] == "superseded by retcon"
    assert await store.pending_review_delta_ids(CAMPAIGN_ID) == set()
    assert await _mood(store) == "fierce"


async def test_swap_turn_deltas_queues_replacement_review_rows(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta(
        delta=_char_delta("calm"), source="extractor", turn_id="t_1", campaign_id=CAMPAIGN_ID
    )

    result = await store.swap_turn_deltas(
        campaign_id=CAMPAIGN_ID,
        turn_id="t_1",
        deltas=[_char_delta("fierce")],
        source="retcon",
        review_deltas=[_char_delta("suspicious")],
    )

    # The low-confidence replacement is queued, not applied.
    assert len(result.queued_review_ids) == 1
    pending = await store.pending_review_delta_ids(CAMPAIGN_ID)
    assert len(pending) == 1
    assert await _mood(store) == "fierce"


async def test_swap_turn_deltas_rolls_back_when_review_queueing_fails(store: StateStore) -> None:
    await _seed(store)
    await store.apply_delta(
        delta=_char_delta("calm"), source="extractor", turn_id="t_1", campaign_id=CAMPAIGN_ID
    )

    # ``None`` is rejected by the store's delta coercion, so the queueing step
    # fails after the reversal and apply succeeded — everything must roll back.
    with pytest.raises(StateStoreError):
        await store.swap_turn_deltas(
            campaign_id=CAMPAIGN_ID,
            turn_id="t_1",
            deltas=[_char_delta("fierce")],
            source="retcon",
            review_deltas=[None],
        )

    log = await store.get_delta_log(campaign_id=CAMPAIGN_ID, include_reversed=True)
    assert [(r.source, r.reversed_at is None) for r in log] == [("extractor", True)]
    assert await store.pending_review_delta_ids(CAMPAIGN_ID) == set()
    assert await _mood(store) == "calm"


async def test_swap_turn_deltas_with_no_turn_applies_atomically(store: StateStore) -> None:
    await _seed(store)
    result = await store.swap_turn_deltas(
        campaign_id=CAMPAIGN_ID,
        turn_id=None,
        deltas=[_char_delta("fierce")],
        source="retcon",
    )
    assert result.rewound == []
    assert len(result.applied) == 1
    assert result.applied[0].turn_id is None
    assert await _mood(store) == "fierce"


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
