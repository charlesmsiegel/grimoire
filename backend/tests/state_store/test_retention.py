"""Tests for the retention sweep (spec §4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from grimoire.event_bus import EventBus
from grimoire.state_store import StateStore
from grimoire.state_store.config import RetentionConfig
from grimoire.state_store.retention import RetentionSweeper, delete_expired_embeddings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=UTC)
MAX_AGE = 90 * 24 * 3600  # 90 days in seconds
CUTOFF = NOW - timedelta(seconds=MAX_AGE)  # 2024-03-03 12:00:00 UTC


async def _insert_post(store: StateStore, post_id: str, created_at: str) -> None:
    await store.db.execute(
        "INSERT INTO scenes (id, campaign_id, branch_id, ordinal, slug, file_path)"
        " VALUES ('s1', 'c1', 'c1:main', 1, 'scene-1', 'scenes/scene-1.md')"
        " ON CONFLICT DO NOTHING"
    )
    await store.db.execute(
        "INSERT INTO posts (id, scene_id, campaign_id, branch_id, order_in_scene, created_at)"
        " VALUES (?, 's1', 'c1', 'c1:main', 1, ?)",
        (post_id, created_at),
    )


async def _insert_fact(
    store: StateStore,
    fact_id: str,
    *,
    retired: int = 0,
    retired_in_post: str | None = None,
) -> None:
    await store.db.execute(
        "INSERT INTO facts"
        " (id, campaign_id, branch_id, text, retired, retired_in_post)"
        " VALUES (?, 'c1', 'c1:main', 'some fact', ?, ?)",
        (fact_id, retired, retired_in_post),
    )


async def _insert_embedding(store: StateStore, emb_id: str, ref: str, source_kind: str) -> None:
    await store.db.execute(
        "INSERT INTO embeddings (id, scope, ref, source_kind) VALUES (?, 'campaign', ?, ?)",
        (emb_id, ref, source_kind),
    )


async def _count_embeddings(store: StateStore, emb_id: str) -> int:
    row = await store.db.fetchone("SELECT COUNT(*) AS n FROM embeddings WHERE id = ?", (emb_id,))
    assert row is not None
    return int(row["n"])


# ---------------------------------------------------------------------------
# Tests for delete_expired_embeddings
# ---------------------------------------------------------------------------


async def test_expired_embedding_is_deleted(store: StateStore) -> None:
    old_post_ts = (CUTOFF - timedelta(days=1)).isoformat()
    await _insert_post(store, "post-old", old_post_ts)
    await _insert_fact(store, "fact-old", retired=1, retired_in_post="post-old")
    await _insert_embedding(store, "emb-old", "fact-old", "fact")

    deleted = await delete_expired_embeddings(store.db, max_age_seconds=MAX_AGE, now=NOW)

    assert deleted == 1
    assert await _count_embeddings(store, "emb-old") == 0


async def test_recent_retirement_embedding_survives(store: StateStore) -> None:
    new_post_ts = (CUTOFF + timedelta(days=1)).isoformat()
    await _insert_post(store, "post-new", new_post_ts)
    await _insert_fact(store, "fact-new", retired=1, retired_in_post="post-new")
    await _insert_embedding(store, "emb-new", "fact-new", "fact")

    deleted = await delete_expired_embeddings(store.db, max_age_seconds=MAX_AGE, now=NOW)

    assert deleted == 0
    assert await _count_embeddings(store, "emb-new") == 1


async def test_active_fact_embedding_survives(store: StateStore) -> None:
    await _insert_fact(store, "fact-active", retired=0)
    await _insert_embedding(store, "emb-active", "fact-active", "fact")

    deleted = await delete_expired_embeddings(store.db, max_age_seconds=MAX_AGE, now=NOW)

    assert deleted == 0
    assert await _count_embeddings(store, "emb-active") == 1


async def test_non_fact_source_kind_is_never_touched(store: StateStore) -> None:
    old_post_ts = (CUTOFF - timedelta(days=1)).isoformat()
    await _insert_post(store, "post-lib", old_post_ts)
    await _insert_fact(store, "fact-lib", retired=1, retired_in_post="post-lib")
    await _insert_embedding(store, "emb-lib", "fact-lib", "library")

    deleted = await delete_expired_embeddings(store.db, max_age_seconds=MAX_AGE, now=NOW)

    assert deleted == 0
    assert await _count_embeddings(store, "emb-lib") == 1


# ---------------------------------------------------------------------------
# Tests for RetentionSweeper
# ---------------------------------------------------------------------------


async def test_sweeper_none_retention_is_noop(store: StateStore) -> None:
    old_post_ts = (CUTOFF - timedelta(days=1)).isoformat()
    await _insert_post(store, "post-x", old_post_ts)
    await _insert_fact(store, "fact-x", retired=1, retired_in_post="post-x")
    await _insert_embedding(store, "emb-x", "fact-x", "fact")

    config = RetentionConfig(embeddings_for_retired_facts_seconds=None)
    sweeper = RetentionSweeper(db=store.db, config=config, clock=lambda: NOW)
    deleted = await sweeper.sweep_once()

    assert deleted == 0
    assert await _count_embeddings(store, "emb-x") == 1


async def test_sweeper_emits_retention_sweep_completed(store: StateStore) -> None:
    old_post_ts = (CUTOFF - timedelta(days=1)).isoformat()
    await _insert_post(store, "post-ev", old_post_ts)
    await _insert_fact(store, "fact-ev", retired=1, retired_in_post="post-ev")
    await _insert_embedding(store, "emb-ev", "fact-ev", "fact")

    bus = EventBus()
    received: list = []
    bus.subscribe("retention_sweep_completed", lambda e: received.append(e))

    config = RetentionConfig(embeddings_for_retired_facts_seconds=MAX_AGE)
    sweeper = RetentionSweeper(db=store.db, config=config, bus=bus, clock=lambda: NOW)
    await sweeper.sweep_once()

    assert len(received) == 1
    assert received[0].payload["deleted_embeddings"] == 1


async def test_sweeper_no_bus_does_not_raise(store: StateStore) -> None:
    config = RetentionConfig(embeddings_for_retired_facts_seconds=MAX_AGE)
    sweeper = RetentionSweeper(db=store.db, config=config, clock=lambda: NOW)
    deleted = await sweeper.sweep_once()
    assert deleted == 0


async def test_sweeper_start_stop(store: StateStore) -> None:
    config = RetentionConfig(
        embeddings_for_retired_facts_seconds=MAX_AGE,
        sweep_interval_seconds=3600,
    )
    sweeper = RetentionSweeper(db=store.db, config=config, clock=lambda: NOW)
    await sweeper.start()
    assert sweeper._task is not None
    await sweeper.stop()
    assert sweeper._task is None
