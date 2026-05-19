"""Tests for ``StateStore`` context pin / exclude rows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


async def _seed_turn_audit(
    store,
    *,
    turn_id: str,
    created_at: datetime,
    campaign_id: str = "camp",
    branch_id: str = "camp:main",
) -> None:
    """Minimal turn_audits row so TTL elapsed-turn counting has data to read."""
    await store.db.execute(
        """
        INSERT INTO turn_audits (
            turn_id, campaign_id, branch_id, created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (turn_id, campaign_id, branch_id, created_at.isoformat()),
    )


async def test_write_and_list_active_pin(store) -> None:
    pin_id = await store.write_context_pin(
        campaign_id="camp",
        branch_id="camp:main",
        kind="pin",
        target_entity_kind="character",
        target_entity_id="char_florence",
    )
    assert pin_id.startswith("ctx_pin_")
    active = await store.list_active_context_pins(campaign_id="camp", branch_id="camp:main")
    assert len(active) == 1
    assert active[0]["kind"] == "pin"
    assert active[0]["target_kind"] == "entity"
    assert active[0]["target_entity_id"] == "char_florence"


async def test_cleared_pin_not_listed(store) -> None:
    pin_id = await store.write_context_pin(
        campaign_id="camp",
        branch_id="camp:main",
        kind="pin",
        target_entity_kind="character",
        target_entity_id="char_florence",
    )
    await store.mark_context_pin_cleared(pin_id=pin_id)
    active = await store.list_active_context_pins(campaign_id="camp", branch_id="camp:main")
    assert active == []


async def test_pin_with_ttl_expires_after_n_turns(store) -> None:
    """Turn ids are random hex — TTL must compute elapsed turns via
    ``turn_audits``, not lexicographic comparison."""
    base = datetime(2026, 1, 1, tzinfo=UTC)
    # Five sequential canonical turns, non-monotonic random hex ids.
    turn_ids = ["t_a1b2c3", "t_99ffaa", "t_011234", "t_deadbe", "t_ef0099"]
    for i, tid in enumerate(turn_ids):
        await _seed_turn_audit(store, turn_id=tid, created_at=base + timedelta(minutes=i))

    await store.write_context_pin(
        campaign_id="camp",
        branch_id="camp:main",
        kind="pin",
        target_entity_kind="character",
        target_entity_id="char_florence",
        created_at_turn_id=turn_ids[0],
        ttl_turns=3,
    )
    # Same turn → 0 elapsed → active.
    active = await store.list_active_context_pins(
        campaign_id="camp", branch_id="camp:main", current_turn_id=turn_ids[0]
    )
    assert len(active) == 1
    # Next sequential turn (1 elapsed) → still active even though `+` < digits
    # would lexicographically claim "expired".
    active = await store.list_active_context_pins(
        campaign_id="camp", branch_id="camp:main", current_turn_id=turn_ids[1]
    )
    assert len(active) == 1, "TTL must use turn count, not lexicographic compare"
    # 2 elapsed → still under TTL of 3.
    active = await store.list_active_context_pins(
        campaign_id="camp", branch_id="camp:main", current_turn_id=turn_ids[2]
    )
    assert len(active) == 1
    # 3 elapsed → TTL exhausted.
    active = await store.list_active_context_pins(
        campaign_id="camp", branch_id="camp:main", current_turn_id=turn_ids[3]
    )
    assert active == []
    # 4 elapsed → still gone.
    active = await store.list_active_context_pins(
        campaign_id="camp", branch_id="camp:main", current_turn_id=turn_ids[4]
    )
    assert active == []


async def test_pin_with_ttl_requires_positive_value(store) -> None:
    import pytest

    with pytest.raises(ValueError):
        await store.write_context_pin(
            campaign_id="camp",
            branch_id="camp:main",
            kind="pin",
            target_entity_kind="character",
            target_entity_id="char_x",
            ttl_turns=0,
        )


async def test_pin_without_audit_history_treated_as_active(store) -> None:
    """If turn_audits has no record of the creation turn, we must not
    silently evict the pin — fail-closed on missing audit data."""
    await store.write_context_pin(
        campaign_id="camp",
        branch_id="camp:main",
        kind="pin",
        target_entity_kind="character",
        target_entity_id="char_x",
        created_at_turn_id="t_never_audited",
        ttl_turns=1,
    )
    active = await store.list_active_context_pins(
        campaign_id="camp", branch_id="camp:main", current_turn_id="t_also_missing"
    )
    assert len(active) == 1


async def test_exclude_round_trip(store) -> None:
    await store.write_context_pin(
        campaign_id="camp",
        branch_id="camp:main",
        kind="exclude",
        target_source_id="src_abcdef",
    )
    rows = await store.list_active_context_pins(campaign_id="camp", branch_id="camp:main")
    assert len(rows) == 1
    assert rows[0]["kind"] == "exclude"
    assert rows[0]["target_kind"] == "source"
    assert rows[0]["target_source_id"] == "src_abcdef"


async def test_branch_isolation(store) -> None:
    await store.write_context_pin(
        campaign_id="camp",
        branch_id="camp:main",
        kind="pin",
        target_entity_kind="character",
        target_entity_id="char_x",
    )
    other = await store.list_active_context_pins(
        campaign_id="camp", branch_id="camp:experiment"
    )
    assert other == []
