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
    # The dropped change never reached the index, so no change event either.
    assert collector.of_type("library_file_changed") == []


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
