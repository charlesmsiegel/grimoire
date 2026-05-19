"""Tests for the campaign-level fork primitives."""

from __future__ import annotations

from grimoire.state_store import StateStore
from grimoire.state_store.fork import bulk_copy, fingerprint, replay_to_turn


async def _seed_minimal(store: StateStore, campaign_id: str = "c1") -> None:
    await store.upsert_campaign(campaign_id=campaign_id, name="Original")
    await store.apply_delta(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "after": {
                "character_ref": "lib:winifred",
                "campaign_id": campaign_id,
                "branch_id": f"{campaign_id}:main",
                "emotional_state": "calm",
                "drift_score": 0.0,
            },
        },
        source="seed",
        turn_id="t1",
        branch_id=f"{campaign_id}:main",
        campaign_id=campaign_id,
    )
    await store.apply_delta(
        delta={
            "kind": "character_state_update",
            "target_scope": "campaign-sqlite",
            "target_table": "character_state",
            "after": {
                "character_ref": "lib:julian",
                "campaign_id": campaign_id,
                "branch_id": f"{campaign_id}:main",
                "emotional_state": "wary",
                "drift_score": 0.1,
            },
        },
        source="seed",
        turn_id="t2",
        branch_id=f"{campaign_id}:main",
        campaign_id=campaign_id,
    )


async def _clone_campaign_row(store: StateStore, source: str, new: str) -> None:
    src = await store.db.fetchone("SELECT * FROM campaigns WHERE id = ?", (source,))
    assert src is not None
    cols = list(src.keys())
    values = []
    for c in cols:
        values.append(new if c == "id" else src[c])
    await store.db.execute(
        f"INSERT INTO campaigns ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
        tuple(values),
    )


async def test_bulk_copy_duplicates_state(store: StateStore) -> None:
    await _seed_minimal(store, "c1")
    await _clone_campaign_row(store, "c1", "c1-fork")

    await bulk_copy(store.db, original="c1", new="c1-fork", cutoff_iso=None)

    rows = await store.db.fetchall(
        "SELECT character_ref FROM character_state WHERE campaign_id = ? ORDER BY character_ref",
        ("c1-fork",),
    )
    assert [r["character_ref"] for r in rows] == ["lib:julian", "lib:winifred"]

    # branches rewritten and source branches preserved
    src_branches = await store.db.fetchall("SELECT id FROM branches WHERE campaign_id = ?", ("c1",))
    new_branches = await store.db.fetchall(
        "SELECT id FROM branches WHERE campaign_id = ?", ("c1-fork",)
    )
    assert "c1:main" in [r["id"] for r in src_branches]
    assert "c1-fork:main" in [r["id"] for r in new_branches]

    # deltas copied
    delta_rows = await store.db.fetchall(
        "SELECT id, branch_id FROM deltas WHERE campaign_id = ?", ("c1-fork",)
    )
    assert len(delta_rows) == 2
    assert all(r["branch_id"] == "c1-fork:main" for r in delta_rows)
    # IDs prefixed so they don't collide with source
    assert all(r["id"].startswith("c1-fork::") for r in delta_rows)

    # source state is untouched
    src_chars = await store.db.fetchall(
        "SELECT character_ref FROM character_state WHERE campaign_id = ?",
        ("c1",),
    )
    assert {r["character_ref"] for r in src_chars} == {"lib:winifred", "lib:julian"}


async def test_bulk_copy_cutoff_filters_posts(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="With posts")
    await store.db.execute(
        "INSERT INTO scenes (id, campaign_id, branch_id, ordinal, slug, file_path) "
        "VALUES ('s1','c1','c1:main',1,'one','/tmp/one.md')"
    )
    await store.db.execute(
        "INSERT INTO posts (id, scene_id, campaign_id, branch_id, order_in_scene, created_at) "
        "VALUES ('p1','s1','c1','c1:main',0, '2026-05-19T10:00:00')"
    )
    await store.db.execute(
        "INSERT INTO posts (id, scene_id, campaign_id, branch_id, order_in_scene, created_at) "
        "VALUES ('p2','s1','c1','c1:main',1, '2026-05-19T11:00:00')"
    )
    await store.db.execute(
        "INSERT INTO posts (id, scene_id, campaign_id, branch_id, order_in_scene, created_at) "
        "VALUES ('p3','s1','c1','c1:main',2, '2026-05-19T12:00:00')"
    )

    await _clone_campaign_row(store, "c1", "c1-fork")
    await bulk_copy(
        store.db,
        original="c1",
        new="c1-fork",
        cutoff_iso="2026-05-19T11:00:00",
    )

    rows = await store.db.fetchall(
        "SELECT id FROM posts WHERE campaign_id = ? ORDER BY order_in_scene",
        ("c1-fork",),
    )
    assert [r["id"] for r in rows] == ["c1-fork::p1", "c1-fork::p2"]


async def test_fingerprint_deterministic_and_ignores_campaign_id(store: StateStore) -> None:
    await _seed_minimal(store, "c1")
    fp1 = await fingerprint(store.db, "c1")
    fp2 = await fingerprint(store.db, "c1")
    assert fp1 == fp2

    await _clone_campaign_row(store, "c1", "c1-fork")
    await bulk_copy(store.db, original="c1", new="c1-fork", cutoff_iso=None)
    fp_fork = await fingerprint(store.db, "c1-fork")
    assert fp_fork == fp1


async def test_replay_to_turn_returns_delta_count(store: StateStore) -> None:
    await _seed_minimal(store, "c1")
    await store.db.execute(
        "UPDATE deltas SET applied_at = '2026-05-19T09:00:00' WHERE id IN "
        "(SELECT id FROM deltas WHERE campaign_id = ? ORDER BY id LIMIT 1)",
        ("c1",),
    )
    await _clone_campaign_row(store, "c1", "c1-fork")
    n = await replay_to_turn(
        store.db, original="c1", new="c1-fork", cutoff_iso="2026-05-19T09:00:00"
    )
    assert n >= 1
