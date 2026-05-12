"""Branch forks and CoW reads."""

from __future__ import annotations

from grimoire.state_store import StateStore


async def test_fork_branch_reads_fall_back_to_parent(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="Branchy")

    await store.apply_delta(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "after": {
                "character_ref": "lib:winifred",
                "campaign_id": "c1",
                "branch_id": "c1:main",
                "emotional_state": "calm",
            },
        },
        source="seed",
    )

    fork_id = await store.fork_branch(
        campaign_id="c1",
        parent_branch_id="c1:main",
        new_label="what-if",
    )
    # No writes on the fork yet — should fall back to main's row.
    state = await store.resolve_character_state(character_ref="lib:winifred", branch_id=fork_id)
    assert state is not None
    assert state["branch_id"] == "c1:main"
    assert state["emotional_state"] == "calm"

    # Write on the fork — fork now wins.
    await store.apply_delta(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "after": {
                "character_ref": "lib:winifred",
                "campaign_id": "c1",
                "branch_id": fork_id,
                "emotional_state": "furious",
            },
        },
        source="player",
        branch_id=fork_id,
    )

    state = await store.resolve_character_state(character_ref="lib:winifred", branch_id=fork_id)
    assert state["branch_id"] == fork_id
    assert state["emotional_state"] == "furious"

    # The parent branch is unaffected.
    parent_state = await store.resolve_character_state(
        character_ref="lib:winifred", branch_id="c1:main"
    )
    assert parent_state["emotional_state"] == "calm"


async def test_branch_chain_walks_parents(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="Chain")
    fork1 = await store.fork_branch(campaign_id="c1", parent_branch_id="c1:main", new_label="b1")
    fork2 = await store.fork_branch(campaign_id="c1", parent_branch_id=fork1, new_label="b2")
    chain = await store.branch_chain(fork2)
    assert chain == [fork2, fork1, "c1:main"]
