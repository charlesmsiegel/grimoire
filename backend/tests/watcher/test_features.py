"""Coverage for the three features that closed the spec-gap on the watcher:
embedding queue, ``library_indexed`` event, and conflict warning."""

from __future__ import annotations

import json
from pathlib import Path

from grimoire.event_bus import EventBus
from grimoire.files import content_hash
from grimoire.state_store import StateStore
from grimoire.watcher import FileWatcher

from .conftest import EventCollector


def _write_markdown(path: Path, frontmatter_yaml: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter_yaml}\n---\n{body}", encoding="utf-8")


def _write_yaml(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _index_hash(frontmatter: dict, body: str) -> str:
    serialized = json.dumps(frontmatter, sort_keys=True) + "\n" + body
    return content_hash(serialized)


# --------------------------------------------------------------------------- #
# Embedding queue
# --------------------------------------------------------------------------- #


async def test_library_entity_queues_embedding_job(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    target = store.data_root / "library" / "worlds" / "wod-london" / "characters" / "f.md"
    _write_markdown(target, "name: winifred\ntags: [vampire]", "winifred prose body.")
    await watcher.process_path(target)

    jobs = watcher.embedding_queue.drain()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.ref == "worlds/wod-london/characters/f"
    assert job.scope == "library"
    assert job.source_kind == "character"
    assert "winifred" in job.text and "prose body" in job.text


async def test_embedding_queue_skips_unchanged_content(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    target = store.data_root / "library" / "worlds" / "wod-london" / "characters" / "f.md"
    _write_markdown(target, "name: winifred", "v1")
    await watcher.process_path(target)
    watcher.embedding_queue.drain()

    # Same content again — no new job.
    _write_markdown(target, "name: winifred", "v1")
    await watcher.process_path(target)
    assert watcher.embedding_queue.pending == 0

    # Real edit re-queues.
    _write_markdown(target, "name: winifred", "v2")
    await watcher.process_path(target)
    assert watcher.embedding_queue.pending == 1


async def test_embedding_queue_skips_structured_only_files(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    # Sheets, image presets, and image metadata files have no prose to embed.
    sheet = store.data_root / "campaigns" / "c1" / "sheets" / "characters" / "f.wod.yaml"
    _write_yaml(sheet, "stats:\n  strength: 3\n")
    await watcher.process_path(sheet)

    preset = store.data_root / "library" / "image-presets" / "oil.yaml"
    _write_yaml(preset, "name: Oil\nprompt_suffix: in oil\n")
    await watcher.process_path(preset)

    img = store.data_root / "campaigns" / "c1" / "images" / "abc.yaml"
    _write_yaml(img, "prompt: a candle\nseed: 42\n")
    await watcher.process_path(img)

    assert watcher.embedding_queue.pending == 0


async def test_scene_body_queues_embedding(
    watcher: FileWatcher,
    store: StateStore,
) -> None:
    target = store.data_root / "campaigns" / "c1" / "scenes" / "0001-opening.md"
    target.parent.mkdir(parents=True)
    target.write_text("## Post 1\n\nThe candle flickered in the draft.\n", encoding="utf-8")
    await watcher.process_path(target)

    jobs = watcher.embedding_queue.drain()
    assert len(jobs) == 1
    assert jobs[0].scope == "campaign"
    assert "candle" in jobs[0].text


# --------------------------------------------------------------------------- #
# library_indexed event
# --------------------------------------------------------------------------- #


async def test_scan_now_emits_library_indexed_with_counts(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "library_indexed")
    _write_markdown(
        store.data_root / "library" / "worlds" / "wod-london" / "characters" / "a.md",
        "name: A",
        "alpha",
    )
    _write_markdown(
        store.data_root / "library" / "worlds" / "wod-london" / "lore" / "h.md",
        "name: H",
        "history",
    )
    _write_markdown(
        store.data_root / "campaigns" / "c1" / "emergent" / "characters" / "x.md",
        "name: X",
        "stranger",
    )
    await watcher.scan_now()

    events = collector.of_type("library_indexed")
    assert len(events) == 1
    payload = events[0].payload
    assert payload["library_files"] == 2
    assert payload["campaign_files"] == 1
    # All three prose entities should have been enqueued for embedding.
    assert payload["embedding_queue_depth"] == 3


async def test_scan_now_emits_event_on_empty_roots(
    watcher: FileWatcher,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "library_indexed")
    await watcher.scan_now()
    events = collector.of_type("library_indexed")
    assert len(events) == 1
    assert events[0].payload == {
        "library_files": 0,
        "campaign_files": 0,
        "embedding_queue_depth": 0,
        "summary_queue_depth": 0,
    }


# --------------------------------------------------------------------------- #
# Conflict warning (last-write-wins detection)
# --------------------------------------------------------------------------- #


async def test_expected_write_matches_no_conflict(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """When the app pre-registers its write, the watcher's reindex of that
    same content should not surface as a conflict."""
    collector = EventCollector(bus, "library_file_changed")
    target = store.data_root / "library" / "worlds" / "wod-london" / "characters" / "f.md"
    frontmatter = {"name": "winifred"}
    body = "App-written body."
    expected = _index_hash(frontmatter, body)

    watcher.register_expected_write(target, expected)
    _write_markdown(target, "name: winifred", body)
    await watcher.process_path(target)

    payload = collector.of_type("library_file_changed")[-1].payload
    assert payload["conflict"] is False
    assert payload["content_hash"] == expected


async def test_expected_write_mismatched_flags_conflict(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """If the file on disk doesn't match what the app expected to land, an
    external editor raced with it and the conflict warning fires."""
    collector = EventCollector(bus, "library_file_changed")
    target = store.data_root / "library" / "worlds" / "wod-london" / "characters" / "f.md"
    intended_frontmatter = {"name": "winifred"}
    intended_body = "What the app meant to write."
    intended_hash = _index_hash(intended_frontmatter, intended_body)

    watcher.register_expected_write(target, intended_hash)
    # ...but a racing external write lands different content.
    _write_markdown(target, "name: winifred", "External edit won.")
    await watcher.process_path(target)

    payload = collector.of_type("library_file_changed")[-1].payload
    assert payload["conflict"] is True

    # The expectation is one-shot — it should not linger and flag the next
    # legitimate edit.
    _write_markdown(target, "name: winifred", "Quiet follow-up.")
    await watcher.process_path(target)
    payload = collector.of_type("library_file_changed")[-1].payload
    assert payload["conflict"] is False


async def test_expectation_consumed_even_when_parse_fails(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """A registered write must be cleared even if parsing the file errors,
    otherwise the stale expectation poisons the next real event for that
    path."""
    collector = EventCollector(bus, "library_file_changed")
    target = store.data_root / "library" / "image-presets" / "broken.yaml"
    watcher.register_expected_write(target, "expected-hash")

    # Malformed YAML triggers a YamlError and process_path returns early.
    target.parent.mkdir(parents=True)
    target.write_text("name: : not\n  - valid\n", encoding="utf-8")
    await watcher.process_path(target)

    # Subsequent valid edit should NOT be flagged as a conflict, because the
    # expectation was consumed by the failed parse rather than leaking.
    target.write_text("name: Oil\nprompt_suffix: in oil\n", encoding="utf-8")
    await watcher.process_path(target)
    payload = collector.of_type("library_file_changed")[-1].payload
    assert payload["conflict"] is False


async def test_expectation_consumed_even_on_spurious_event(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    """A registered write must also be consumed when the resulting event is
    deduped as spurious (content unchanged)."""
    collector = EventCollector(bus, "library_file_changed")
    target = store.data_root / "library" / "worlds" / "wod-london" / "characters" / "f.md"
    _write_markdown(target, "name: winifred", "v1")
    await watcher.process_path(target)
    # Now register a write and re-process with the same content — spurious.
    watcher.register_expected_write(target, "stale-hash")
    await watcher.process_path(target)

    # Next genuine edit must NOT inherit the stale expectation.
    _write_markdown(target, "name: winifred", "v2")
    await watcher.process_path(target)
    payload = collector.of_type("library_file_changed")[-1].payload
    assert payload["conflict"] is False


async def test_clear_expected_write_drops_pending_expectation(
    watcher: FileWatcher,
    store: StateStore,
    bus: EventBus,
) -> None:
    collector = EventCollector(bus, "library_file_changed")
    target = store.data_root / "library" / "worlds" / "wod-london" / "characters" / "f.md"
    watcher.register_expected_write(target, "bogus-hash")
    watcher.clear_expected_write(target)

    _write_markdown(target, "name: winifred", "Fresh content.")
    await watcher.process_path(target)

    payload = collector.of_type("library_file_changed")[-1].payload
    assert payload["conflict"] is False
