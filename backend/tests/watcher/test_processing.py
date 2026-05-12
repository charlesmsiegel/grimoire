"""``FileWatcher.process_path`` reindexes the right SQLite table and emits events."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.state_store import StateStore
from grimoire.watcher import FileWatcher

from .conftest import EventCollector


def _write_markdown(path: Path, frontmatter_yaml: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter_yaml}\n---\n{body}", encoding="utf-8")


def _write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# process_path: library files
# --------------------------------------------------------------------------- #


async def test_library_create_indexes_and_emits(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "library_file_changed")
    target = store.data_root / "library" / "settings" / "wod-london" / "characters" / "f.md"
    _write_markdown(target, "name: winifred\ntags: [vampire]", "winifred prose.")

    await watcher.process_path(target)

    row = await store.get_library_entity("settings/wod-london/characters/f")
    assert row is not None
    assert row["name"] == "winifred"
    assert row["body"].strip() == "winifred prose."

    events = collector.of_type("library_file_changed")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["change_type"] == "created"
    assert payload["library_id"] == "settings/wod-london/characters/f"
    assert payload["conflict"] is False


async def test_library_modify_bumps_version_and_emits_modified(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "library_file_changed")
    target = store.data_root / "library" / "settings" / "wod-london" / "characters" / "f.md"
    _write_markdown(target, "name: winifred", "v1")
    await watcher.process_path(target)

    # Touch with same content — should be deduped (no second event).
    _write_markdown(target, "name: winifred", "v1")
    await watcher.process_path(target)
    assert len(collector.of_type("library_file_changed")) == 1

    # Real content change.
    _write_markdown(target, "name: winifred", "v2")
    await watcher.process_path(target)
    events = collector.of_type("library_file_changed")
    assert len(events) == 2
    assert events[-1].payload["change_type"] == "modified"

    row = await store.get_library_entity("settings/wod-london/characters/f")
    assert row["body"] == "v2"
    assert row["version"] == 2


async def test_library_delete_removes_row_and_emits(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "library_file_changed")
    target = store.data_root / "library" / "settings" / "wod-london" / "characters" / "f.md"
    _write_markdown(target, "name: winifred", "prose")
    await watcher.process_path(target)

    target.unlink()
    await watcher.process_path(target)

    assert await store.get_library_entity("settings/wod-london/characters/f") is None
    events = collector.of_type("library_file_changed")
    assert events[-1].payload["change_type"] == "deleted"
    assert events[-1].payload["content_hash"] is None


async def test_image_preset_yaml_is_indexed(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    target = store.data_root / "library" / "image-presets" / "oil.yaml"
    _write_yaml(target, "name: Oil painting\nprompt_suffix: in oil\n")
    await watcher.process_path(target)
    row = await store.get_library_entity("image-presets/oil")
    assert row is not None
    assert row["frontmatter"]["prompt_suffix"] == "in oil"


# --------------------------------------------------------------------------- #
# process_path: campaign-content files
# --------------------------------------------------------------------------- #


async def test_override_indexes_into_campaign_content_index(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "campaign_file_changed")
    target = (
        store.data_root
        / "campaigns"
        / "c1"
        / "overrides"
        / "settings"
        / "wod-london"
        / "characters"
        / "winifred.yaml"
    )
    _write_yaml(target, "voice: gruff\n")
    await watcher.process_path(target)

    rows = await store.db.fetchall(
        "SELECT id, kind FROM campaign_content_index WHERE campaign_id = ?",
        ("c1",),
    )
    ids = {r["id"] for r in rows}
    assert any(i.endswith("/winifred") for i in ids)
    assert {r["kind"] for r in rows} == {"override"}

    events = collector.of_type("campaign_file_changed")
    assert events[-1].payload["library_id"] == "settings/wod-london/characters/winifred"


async def test_emergent_md_indexes_and_emits(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "campaign_file_changed")
    target = store.data_root / "campaigns" / "c1" / "emergent" / "characters" / "stranger.md"
    _write_markdown(target, "name: The Stranger", "Mysterious.")
    await watcher.process_path(target)

    rows = await store.db.fetchall(
        "SELECT id, kind, entity_subkind FROM campaign_content_index WHERE kind = 'emergent'",
    )
    assert len(rows) == 1
    assert rows[0]["entity_subkind"] == "character"

    payload = collector.of_type("campaign_file_changed")[-1].payload
    assert payload["change_type"] == "created"
    assert payload["entity_kind"] == "character"
    assert payload["asset_id"] == "stranger"


async def test_sheet_emits_sheet_file_changed(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "sheet_file_changed")
    target = store.data_root / "campaigns" / "c1" / "sheets" / "characters" / "winifred.wod.yaml"
    _write_yaml(target, "stats:\n  strength: 3\n")
    await watcher.process_path(target)

    rows = await store.db.fetchall(
        "SELECT id, kind FROM campaign_content_index WHERE kind = 'sheet'"
    )
    assert len(rows) == 1
    payload = collector.of_type("sheet_file_changed")[-1].payload
    assert payload["mechanics_id"] == "wod"
    assert payload["asset_id"] == "winifred"


async def test_scene_emits_scene_file_changed_without_indexing(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "scene_file_changed")
    target = store.data_root / "campaigns" / "c1" / "scenes" / "0001-opening.md"
    target.parent.mkdir(parents=True)
    target.write_text("## Post 1 — narrator\n\nThe wind was cold.\n", encoding="utf-8")
    await watcher.process_path(target)

    # Scene files are not indexed into campaign_content_index.
    rows = await store.db.fetchall("SELECT id FROM campaign_content_index")
    assert rows == []

    payload = collector.of_type("scene_file_changed")[-1].payload
    assert payload["scene_basename"] == "0001-opening"
    assert payload["branch_id"] == "main"


async def test_image_metadata_indexes(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    target = store.data_root / "campaigns" / "c1" / "images" / "abc.yaml"
    _write_yaml(target, "prompt: a candle\nseed: 42\n")
    await watcher.process_path(target)

    rows = await store.db.fetchall(
        "SELECT id, kind, asset_id FROM campaign_content_index WHERE kind = 'image'"
    )
    assert len(rows) == 1
    assert rows[0]["asset_id"] == "abc"


# --------------------------------------------------------------------------- #
# Hash dedup / conflict tolerance
# --------------------------------------------------------------------------- #


async def test_external_overwrite_is_last_write_wins(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """If the user externally rewrites a library file, the watcher reindexes it
    rather than crashing or rejecting the change."""
    collector = EventCollector(bus, "library_file_changed")
    target = store.data_root / "library" / "settings" / "wod-london" / "characters" / "f.md"
    _write_markdown(target, "name: winifred", "first")
    await watcher.process_path(target)
    # Simulate external edit changing the content.
    _write_markdown(target, "name: winifred", "external rewrite")
    await watcher.process_path(target)

    row = await store.get_library_entity("settings/wod-london/characters/f")
    assert row["body"] == "external rewrite"
    payload = collector.of_type("library_file_changed")[-1].payload
    assert payload["change_type"] == "modified"
    # Conflict warning surface is reserved on the payload even if v1 doesn't
    # set it; the field is always present so subscribers can branch on it.
    assert "conflict" in payload


async def test_malformed_yaml_is_logged_not_raised(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    target = store.data_root / "library" / "image-presets" / "broken.yaml"
    target.parent.mkdir(parents=True)
    target.write_text("name: : not\n  - valid\n", encoding="utf-8")
    # Should not raise; the watcher logs and skips.
    await watcher.process_path(target)
    assert await store.get_library_entity("image-presets/broken") is None


# --------------------------------------------------------------------------- #
# Initial scan
# --------------------------------------------------------------------------- #


async def test_scan_now_picks_up_preexisting_files(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    # Files exist before the watcher starts.
    a = store.data_root / "library" / "settings" / "wod-london" / "characters" / "a.md"
    b = store.data_root / "library" / "settings" / "wod-london" / "lore" / "history.md"
    _write_markdown(a, "name: Alice", "Alice prose.")
    _write_markdown(b, "name: History", "Ancient times.")

    collector = EventCollector(bus, "library_file_changed", "campaign_file_changed")
    await watcher.scan_now()

    # Initial scan does not emit events (those are reserved for live edits).
    assert collector.events == []

    row_a = await store.get_library_entity("settings/wod-london/characters/a")
    row_b = await store.get_library_entity("settings/wod-london/lore/history")
    assert row_a is not None and row_a["name"] == "Alice"
    assert row_b is not None and row_b["name"] == "History"


async def test_scan_now_drops_orphan_library_rows(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    target = store.data_root / "library" / "settings" / "wod-london" / "characters" / "x.md"
    _write_markdown(target, "name: X", "")
    await watcher.scan_now()
    assert await store.get_library_entity("settings/wod-london/characters/x") is not None

    target.unlink()
    await watcher.scan_now()
    assert await store.get_library_entity("settings/wod-london/characters/x") is None


# --------------------------------------------------------------------------- #
# Live observer end-to-end
# --------------------------------------------------------------------------- #


async def test_live_observer_picks_up_filesystem_events(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "library_file_changed")
    await watcher.start(initial_scan=True)
    try:
        target = store.data_root / "library" / "settings" / "wod-london" / "characters" / "live.md"
        _write_markdown(target, "name: Live", "First version.")

        # Wait until the row appears or the timeout elapses.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            row = await store.get_library_entity("settings/wod-london/characters/live")
            if row is not None:
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("watcher never indexed the new file")

        assert row["name"] == "Live"
        assert any(
            e.payload.get("library_id") == "settings/wod-london/characters/live"
            for e in collector.of_type("library_file_changed")
        )
    finally:
        await watcher.stop()
