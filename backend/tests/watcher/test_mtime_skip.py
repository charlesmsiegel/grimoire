"""mtime-based skip during scan_now."""

from __future__ import annotations

import time
from pathlib import Path

from grimoire.event_bus import EventBus
from grimoire.state_store import StateStore
from grimoire.watcher import FileWatcher


def _write_markdown(path: Path, frontmatter_yaml: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter_yaml}\n---\n{body}", encoding="utf-8")


async def test_scan_skips_unchanged_files(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """After an initial scan, a second scan should skip files whose mtime
    hasn't changed — _known_hashes is populated from the stored hash."""
    target = store.data_root / "library" / "worlds" / "w1" / "characters" / "alice.md"
    _write_markdown(target, "name: Alice", "Alice body.")

    result1 = await watcher.scan_now()
    assert result1["library_files"] == 1

    row = await store.get_library_entity("worlds/w1/characters/alice")
    assert row is not None
    assert row["name"] == "Alice"

    # Second scan — file not modified, should be skipped via mtime match.
    result2 = await watcher.scan_now()
    assert result2["library_files"] == 1
    # The row should still be there (not orphan-cleaned).
    row2 = await store.get_library_entity("worlds/w1/characters/alice")
    assert row2 is not None
    assert row2["name"] == "Alice"


async def test_scan_reindexes_when_mtime_changes(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """If a file is modified between scans, the new content should be indexed."""
    target = store.data_root / "library" / "worlds" / "w1" / "characters" / "bob.md"
    _write_markdown(target, "name: Bob", "Old body.")

    await watcher.scan_now()
    row = await store.get_library_entity("worlds/w1/characters/bob")
    assert row["body"].strip() == "Old body."

    # Modify the file — mtime changes.
    time.sleep(0.05)
    _write_markdown(target, "name: Bob", "New body.")

    await watcher.scan_now()
    row2 = await store.get_library_entity("worlds/w1/characters/bob")
    assert row2["body"].strip() == "New body."
