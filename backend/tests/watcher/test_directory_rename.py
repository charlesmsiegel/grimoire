"""Directory rename / move detection (spec §9).

A directory rename under ``data/library/`` or ``data/campaigns/`` must NOT
silently re-key every row in ``library_index`` / ``campaign_content_index``.
The watcher detects the watchdog ``on_moved`` directory event, suppresses the
per-file delete/create cascade for the moved subtree, and emits a single
``library_rename_detected`` (or ``campaign_rename_detected``) event the user
must acknowledge via ``reconcile_directory_rename``.
"""

from __future__ import annotations

from pathlib import Path

from grimoire.event_bus import EventBus
from grimoire.state_store import StateStore
from grimoire.watcher import FileWatcher

from .conftest import EventCollector


def _write_markdown(path: Path, frontmatter_yaml: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter_yaml}\n---\n{body}", encoding="utf-8")


async def _seed_library_world(watcher: FileWatcher, root: Path, world_id: str) -> None:
    """Seed a small world with two characters and a lore file."""
    base = root / "library" / "worlds" / world_id
    _write_markdown(base / "characters" / "winifred.md", "name: winifred", "winifred.")
    _write_markdown(base / "characters" / "edgar.md", "name: Edgar", "Edgar.")
    _write_markdown(base / "lore" / "history.md", "name: History", "Ancient.")
    await watcher.scan_now()


# --------------------------------------------------------------------------- #
# library_rename_detected emission + suppression
# --------------------------------------------------------------------------- #


async def test_directory_move_emits_single_event_and_lists_affected_rows(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    await _seed_library_world(watcher, store.data_root, "wod-london")

    collector = EventCollector(
        bus,
        "library_rename_detected",
        "library_file_changed",
    )
    src = store.data_root / "library" / "worlds" / "wod-london"
    dest = store.data_root / "library" / "worlds" / "london"
    src.rename(dest)

    await watcher.handle_directory_move(src, dest)

    rename_events = collector.of_type("library_rename_detected")
    assert len(rename_events) == 1
    payload = rename_events[0].payload
    assert payload["src_path"] == str(src.resolve())
    assert payload["dest_path"] == str(dest.resolve())
    assert payload["scope"] == "library"
    assert set(payload["library_ids"]) == {
        "worlds/wod-london/characters/winifred",
        "worlds/wod-london/characters/edgar",
        "worlds/wod-london/lore/history",
    }
    # No content_index ids — the rename was in the library tree.
    assert payload["content_index_ids"] == []
    # Index rows are untouched: they still point at the old library_id.
    rows = await store.db.fetchall(
        "SELECT id FROM library_index WHERE id LIKE 'worlds/wod-london/%'"
    )
    assert len(rows) == 3


async def test_per_file_events_under_moved_subtree_are_suppressed(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """While a rename is pending, per-file create/delete events for paths under
    either side of the move are dropped — they would otherwise silently re-key
    the index before the user has a chance to acknowledge."""
    await _seed_library_world(watcher, store.data_root, "wod-london")

    collector = EventCollector(bus, "library_file_changed")
    src = store.data_root / "library" / "worlds" / "wod-london"
    dest = store.data_root / "library" / "worlds" / "london"
    src.rename(dest)

    await watcher.handle_directory_move(src, dest)

    # Drain any spurious events from the seed phase before measuring.
    pre_count = len(collector.of_type("library_file_changed"))

    # Now simulate the per-file cascade watchdog produces. Both the deletes
    # (paths under the old dir, no longer exist) and the creates (paths
    # under the new dir, now exist) should be suppressed.
    await watcher.process_path(src / "characters" / "winifred.md")
    await watcher.process_path(src / "characters" / "edgar.md")
    await watcher.process_path(dest / "characters" / "winifred.md")
    await watcher.process_path(dest / "characters" / "edgar.md")
    await watcher.process_path(dest / "lore" / "history.md")

    # Zero new file-changed events while the rename is pending.
    assert len(collector.of_type("library_file_changed")) == pre_count


# --------------------------------------------------------------------------- #
# reconcile_directory_rename
# --------------------------------------------------------------------------- #


async def test_reconcile_accept_rekeys_index_to_new_paths(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    await _seed_library_world(watcher, store.data_root, "wod-london")

    src = store.data_root / "library" / "worlds" / "wod-london"
    dest = store.data_root / "library" / "worlds" / "london"
    src.rename(dest)
    await watcher.handle_directory_move(src, dest)

    await watcher.reconcile_directory_rename(src, dest, accept=True)

    # Old library_ids are gone.
    old_rows = await store.db.fetchall(
        "SELECT id FROM library_index WHERE id LIKE 'worlds/wod-london/%'"
    )
    assert old_rows == []
    # New library_ids are present, one per source file.
    new_rows = await store.db.fetchall(
        "SELECT id FROM library_index WHERE id LIKE 'worlds/london/%' ORDER BY id"
    )
    assert {r["id"] for r in new_rows} == {
        "worlds/london/characters/winifred",
        "worlds/london/characters/edgar",
        "worlds/london/lore/history",
    }


async def test_reconcile_reject_leaves_index_rows_pointing_at_old_paths(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    await _seed_library_world(watcher, store.data_root, "wod-london")

    src = store.data_root / "library" / "worlds" / "wod-london"
    dest = store.data_root / "library" / "worlds" / "london"
    src.rename(dest)
    await watcher.handle_directory_move(src, dest)

    await watcher.reconcile_directory_rename(src, dest, accept=False)

    # Reject leaves the original rows in place. The caller is on the hook to
    # either undo the rename on disk or delete the now-orphaned rows.
    rows = await store.db.fetchall(
        "SELECT id FROM library_index WHERE id LIKE 'worlds/wod-london/%' ORDER BY id"
    )
    assert {r["id"] for r in rows} == {
        "worlds/wod-london/characters/winifred",
        "worlds/wod-london/characters/edgar",
        "worlds/wod-london/lore/history",
    }
    # No rows were created at the new path.
    new_rows = await store.db.fetchall(
        "SELECT id FROM library_index WHERE id LIKE 'worlds/london/%'"
    )
    assert new_rows == []


async def test_reconcile_clears_suppression_so_subsequent_edits_flow(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """After acknowledgement, per-file events at the new paths must resume
    flowing through the normal indexing pipeline."""
    await _seed_library_world(watcher, store.data_root, "wod-london")

    src = store.data_root / "library" / "worlds" / "wod-london"
    dest = store.data_root / "library" / "worlds" / "london"
    src.rename(dest)
    await watcher.handle_directory_move(src, dest)
    await watcher.reconcile_directory_rename(src, dest, accept=True)

    collector = EventCollector(bus, "library_file_changed")
    # Now edit one of the newly-indexed files.
    target = dest / "characters" / "winifred.md"
    _write_markdown(target, "name: winifred", "Updated body.")
    await watcher.process_path(target)

    events = collector.of_type("library_file_changed")
    assert len(events) == 1
    assert events[0].payload["library_id"] == "worlds/london/characters/winifred"


# --------------------------------------------------------------------------- #
# Campaign-tree rename
# --------------------------------------------------------------------------- #


async def test_campaign_directory_rename_emits_campaign_event(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    # Seed an emergent file under campaign c1.
    target = (
        store.data_root / "campaigns" / "c1" / "emergent" / "characters" / "stranger.md"
    )
    _write_markdown(target, "name: The Stranger", "Mystery.")
    await watcher.scan_now()

    collector = EventCollector(
        bus,
        "library_rename_detected",
        "campaign_rename_detected",
    )
    src = store.data_root / "campaigns" / "c1" / "emergent"
    dest = store.data_root / "campaigns" / "c1" / "emerging"
    src.rename(dest)
    await watcher.handle_directory_move(src, dest)

    library_events = collector.of_type("library_rename_detected")
    campaign_events = collector.of_type("campaign_rename_detected")
    assert library_events == []
    assert len(campaign_events) == 1
    payload = campaign_events[0].payload
    assert payload["scope"] == "campaign"
    assert payload["library_ids"] == []
    assert any(
        cid.endswith("/emergent/characters/stranger")
        for cid in payload["content_index_ids"]
    )


# --------------------------------------------------------------------------- #
# Non-regression: single-file rename still flows through per-file logic
# --------------------------------------------------------------------------- #


async def test_single_file_rename_flows_through_per_file_logic(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """A file (non-directory) rename must NOT trigger directory-rename
    detection. The watchdog bridge handles it as the existing pair of
    delete (src) + create (dest) events, which the per-file pipeline
    already covers."""
    await _seed_library_world(watcher, store.data_root, "wod-london")

    collector = EventCollector(
        bus,
        "library_file_changed",
        "library_rename_detected",
    )
    src = store.data_root / "library" / "worlds" / "wod-london" / "characters" / "winifred.md"
    dest = (
        store.data_root / "library" / "worlds" / "wod-london" / "characters" / "winifred-d.md"
    )
    src.rename(dest)

    # No directory move was emitted (rename was of a file, not a directory):
    # the bridge would have only scheduled per-file events for src and dest.
    await watcher.process_path(src)
    await watcher.process_path(dest)

    # No rename-detected event was emitted.
    assert collector.of_type("library_rename_detected") == []
    # Old row is gone, new row is present.
    assert (
        await store.get_library_entity("worlds/wod-london/characters/winifred") is None
    )
    assert (
        await store.get_library_entity("worlds/wod-london/characters/winifred-d")
        is not None
    )
    # Both a "deleted" and a "created" event flowed through.
    types = {e.payload["change_type"] for e in collector.of_type("library_file_changed")}
    assert {"deleted", "created"}.issubset(types)


async def test_reconcile_unknown_rename_is_noop(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    """Calling reconcile_directory_rename for a rename the watcher never saw
    must not raise — reconciliation is best-effort and the caller may have
    missed the original event."""
    # Should not raise.
    await watcher.reconcile_directory_rename(
        store.data_root / "library" / "worlds" / "ghost",
        store.data_root / "library" / "worlds" / "phantom",
        accept=True,
    )
