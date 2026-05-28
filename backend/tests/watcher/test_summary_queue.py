"""Body-compressed auto-summary queue (§1).

The watcher enqueues a :class:`SummaryJob` for every library-scoped prose
write whose body length meets the configured threshold. A downstream
worker drains the queue, summarizes (typically via an LLM), and writes
the result through :meth:`StateStore.set_body_compressed`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.library.config import (
    LibraryConfig,
    LibraryIndexingConfig,
)
from grimoire.state_store import StateStore
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db
from grimoire.watcher import FileWatcher


def _write_md(path: Path, name: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\nname: {name}\n---\n{body}", encoding="utf-8")


@pytest.fixture
async def store(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    s = StateStore(db, data_root)
    try:
        yield s
    finally:
        await db.close()


def _make_watcher(store: StateStore, *, threshold: int = 50, enabled: bool = True) -> FileWatcher:
    cfg = LibraryConfig(
        indexing=LibraryIndexingConfig(
            summarize_on_index=enabled,
            summarize_min_body_length=threshold,
        )
    )
    return FileWatcher(
        data_root=store.data_root,
        store=store,
        bus=EventBus(),
        config=cfg,
    )


async def test_long_library_body_enqueues_summary_job(store: StateStore) -> None:
    watcher = _make_watcher(store, threshold=50)
    body = "A very long character description. " * 5  # > 50 chars
    target = store.data_root / "library" / "worlds" / "w" / "characters" / "c.md"
    _write_md(target, "C", body)

    await watcher.process_path(target)

    pending = watcher.summary_queue.peek()
    assert len(pending) == 1
    job = pending[0]
    assert job.library_id == "worlds/w/characters/c"
    assert job.source_kind == "character"
    assert job.text == body
    assert job.content_hash  # non-empty


async def test_short_body_below_threshold_is_skipped(store: StateStore) -> None:
    watcher = _make_watcher(store, threshold=500)
    target = store.data_root / "library" / "worlds" / "w" / "lore" / "short.md"
    _write_md(target, "Short", "Tiny body.")

    await watcher.process_path(target)
    assert watcher.summary_queue.pending == 0


async def test_summarize_on_index_false_disables_queueing(store: StateStore) -> None:
    watcher = _make_watcher(store, threshold=10, enabled=False)
    target = store.data_root / "library" / "worlds" / "w" / "characters" / "c.md"
    _write_md(target, "C", "A reasonably long body that easily clears the threshold.")

    await watcher.process_path(target)
    assert watcher.summary_queue.pending == 0


async def test_emergent_files_do_not_enqueue_summary(store: StateStore) -> None:
    """Emergent (campaign-scoped) bodies have no body_compressed column."""
    watcher = _make_watcher(store, threshold=10)
    target = store.data_root / "campaigns" / "c1" / "emergent" / "character" / "new.md"
    _write_md(target, "New", "A reasonably long body that clears the threshold.")

    await watcher.process_path(target)
    assert watcher.summary_queue.pending == 0


async def test_scan_now_backfills_summary_queue(store: StateStore) -> None:
    body = "A long enough body to summarize." * 3
    _write_md(
        store.data_root / "library" / "worlds" / "w" / "characters" / "c.md",
        "C",
        body,
    )
    _write_md(
        store.data_root / "library" / "worlds" / "w" / "lore" / "l.md",
        "L",
        body,
    )

    watcher = _make_watcher(store, threshold=50)
    await watcher.scan_now()
    assert watcher.summary_queue.pending == 2


async def test_set_body_compressed_writes_and_reads(store: StateStore) -> None:
    """The store can persist the worker's summary; reads see it."""
    await store.write_library_file(
        library_id="worlds/w/characters/c",
        frontmatter={"id": "c", "name": "C"},
        body="A long body needing a summary.",
        source="test",
    )
    row = await store.get_library_entity("worlds/w/characters/c")
    assert row is not None
    assert row.get("body_compressed") is None

    ok = await store.set_body_compressed("worlds/w/characters/c", "Brief.")
    assert ok is True

    row = await store.get_library_entity("worlds/w/characters/c")
    assert row is not None
    assert row["body_compressed"] == "Brief."


async def test_set_body_compressed_rejects_stale_content_hash(store: StateStore) -> None:
    await store.write_library_file(
        library_id="worlds/w/characters/c",
        frontmatter={"id": "c", "name": "C"},
        body="Original body that will be rewritten.",
        source="test",
    )

    # Pretend a worker computed the summary against an older content hash.
    ok = await store.set_body_compressed(
        "worlds/w/characters/c",
        "Stale summary.",
        expected_content_hash="not-the-current-hash",
    )
    assert ok is False
    row = await store.get_library_entity("worlds/w/characters/c")
    assert row is not None
    assert row["body_compressed"] is None
