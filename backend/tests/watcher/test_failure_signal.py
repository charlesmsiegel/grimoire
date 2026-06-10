"""Watcher failures surface as a counter + ``watcher_error`` events (#587).

A dropped change (parse error, processing crash) means the SQLite index has
drifted from disk. Log lines alone aren't a signal callers can react to, so
every failure increments ``FileWatcher.failure_count`` and emits a
``watcher_error`` event; scans also report the failures they incurred.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.state_store import StateStore
from grimoire.watcher import FileWatcher

from .conftest import EventCollector


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def test_parse_failure_counts_and_emits_watcher_error(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "watcher_error", "library_file_changed")
    target = store.data_root / "library" / "worlds" / "wod-london" / "characters" / "bad.md"
    # Opening fence without a closing fence → FrontmatterError on parse.
    _write(target, "---\nname: Broken\nno closing fence")

    await watcher.process_path(target)

    assert watcher.failure_count == 1
    assert watcher.failure_counts() == {"parse": 1}
    errors = collector.of_type("watcher_error")
    assert len(errors) == 1
    payload = errors[0].payload
    assert payload["stage"] == "parse"
    assert payload["path"] == str(target)
    assert payload["failure_count"] == 1
    assert "FrontmatterError" in payload["error"]
    # Library-scoped failure: no campaign_id, so the stream bridge broadcasts.
    assert "campaign_id" not in payload
    # The dropped change never reached the index, so no change event either.
    assert collector.of_type("library_file_changed") == []


async def test_campaign_parse_failure_carries_campaign_id(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """A campaign-scoped failure routes to that campaign's WS channel —
    the payload must carry campaign_id or the bridge broadcasts it."""
    collector = EventCollector(bus, "watcher_error")
    target = store.data_root / "campaigns" / "c1" / "emergent" / "characters" / "bad.md"
    _write(target, "---\nname: Broken\nno closing fence")

    await watcher.process_path(target)

    errors = collector.of_type("watcher_error")
    assert len(errors) == 1
    assert errors[0].payload["campaign_id"] == "c1"


async def test_scan_now_reports_failures_in_summary_and_indexed_event(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "library_indexed", "watcher_error")
    root = store.data_root / "library" / "worlds" / "wod-london" / "characters"
    _write(root / "good.md", "---\nname: Good\n---\nFine prose.")
    _write(root / "bad.md", "---\nname: Broken\nno closing fence")

    report = await watcher.scan_now()

    assert report["failures"] == 1
    indexed = collector.of_type("library_indexed")
    assert len(indexed) == 1
    assert indexed[0].payload["failures"] == 1
    assert len(collector.of_type("watcher_error")) == 1
    # The good file still indexed despite its sibling failing.
    assert await store.get_library_entity("worlds/wod-london/characters/good") is not None

    # A follow-up scan with the corruption fixed reports zero new failures
    # (the summary counts per-scan, not cumulative).
    _write(root / "bad.md", "---\nname: Fixed\n---\nNow fine.")
    report2 = await watcher.scan_now()
    assert report2["failures"] == 0
    assert watcher.failure_count == 1


async def test_scan_failures_are_scan_local_not_counter_deltas(
    watcher: FileWatcher,
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scan report counts only its own dropped changes — failures
    recorded concurrently (live events, overlapping scans) don't bleed in."""
    root = store.data_root / "library" / "worlds" / "wod-london" / "characters"
    _write(root / "good.md", "---\nname: Good\n---\nFine prose.")

    # Simulate a concurrent live-event failure landing mid-scan by bumping
    # the process-wide counter from under the scan.
    original = watcher._mtime_skip

    def mtime_skip_with_concurrent_failure(path, mtime_cache):
        watcher._failure_counts["process"] += 1
        return original(path, mtime_cache)

    monkeypatch.setattr(watcher, "_mtime_skip", mtime_skip_with_concurrent_failure)

    report = await watcher.scan_now(scope="library")
    assert report["failures"] == 0
    assert watcher.failure_counts()["process"] >= 1


async def test_inventory_rebuild_skips_feed_scan_failures(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skipped holder loses its derived inventory rows — the scan must
    report it, not just log it (#587 review)."""
    collector = EventCollector(bus, "watcher_error")

    async def rebuild_with_skips() -> int:
        return 2

    monkeypatch.setattr(store, "rebuild_inventory_holdings_from_files", rebuild_with_skips)

    report = await watcher.scan_now(scope="campaigns")

    assert report["failures"] == 2
    assert watcher.failure_counts() == {"inventory_rebuild": 2}
    errors = collector.of_type("watcher_error")
    assert len(errors) == 1
    payload = errors[0].payload
    assert payload["stage"] == "inventory_rebuild"
    assert payload["count"] == 2
    assert payload["failure_count"] == 2


async def test_safe_process_records_processing_failure(
    watcher: FileWatcher,
    bus: EventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = EventCollector(bus, "watcher_error")

    async def boom(path: Path) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(watcher, "process_path", boom)
    await watcher._safe_process(Path("/data/library/x.md"))

    assert watcher.failure_counts() == {"process": 1}
    errors = collector.of_type("watcher_error")
    assert len(errors) == 1
    assert errors[0].payload["stage"] == "process"
    assert "kaboom" in errors[0].payload["error"]


async def test_safe_directory_move_records_failure(
    watcher: FileWatcher,
    bus: EventBus,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collector = EventCollector(bus, "watcher_error")

    async def boom(src: Path, dest: Path) -> None:
        raise RuntimeError("move kaboom")

    monkeypatch.setattr(watcher, "handle_directory_move", boom)
    await watcher._safe_handle_directory_move(Path("/data/library/a"), Path("/data/library/b"))

    assert watcher.failure_counts() == {"directory_move": 1}
    errors = collector.of_type("watcher_error")
    assert len(errors) == 1
    assert errors[0].payload["stage"] == "directory_move"
