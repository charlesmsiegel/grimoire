"""Character variant overlays: file gateway + resolve-cascade application.

Variants are diff-overlay files under the base character
(``characters/<base>/variants/<vid>.md``). They never enter
``library_index``; the campaign's ``campaign.yaml`` ``variants:`` map selects
one per character and :meth:`StateStore.resolve_entity` applies the diff
between the library base and any campaign override.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grimoire.event_bus import EventBus
from grimoire.files import load_yaml, read_markdown, write_yaml
from grimoire.state_store import StateStore
from grimoire.state_store.errors import NotFoundError
from grimoire.storage import Database
from grimoire.testing.db_template import stamp_migrated_db

CHAR_ID = "worlds/wod-london/characters/alistair"


async def _seed_character(store: StateStore) -> None:
    await store.write_library_file(
        library_id=CHAR_ID,
        frontmatter={
            "id": "alistair",
            "name": "Alistair",
            "age": 300,
            "extras": {"clan": "Tremere", "haven": "Chantry"},
        },
        body="An elder of the chantry.",
        source="user",
    )


def _select_variant(store: StateStore, campaign_id: str, variant_id: str | None) -> None:
    campaign_dir = store.data_root / "campaigns" / campaign_id
    campaign_dir.mkdir(parents=True, exist_ok=True)
    doc: dict = {"id": campaign_id}
    if variant_id is not None:
        doc["variants"] = {CHAR_ID: variant_id}
    write_yaml(campaign_dir / "campaign.yaml", doc)


async def _write_variant(store: StateStore, **overrides) -> dict:
    params = {
        "world_id": "wod-london",
        "base_id": "alistair",
        "variant_id": "young",
        "frontmatter": {"label": "Young Alistair", "age": 25},
        "body": "",
        "source": "user",
    }
    params.update(overrides)
    return await store.write_character_variant(**params)


# ---------------------------------------------------------------------------
# File gateway
# ---------------------------------------------------------------------------


async def test_variant_write_read_delete_round_trip(store: StateStore) -> None:
    await _seed_character(store)
    written = await _write_variant(store, body="A brash newcomer.")
    assert written["id"] == "young"
    assert written["label"] == "Young Alistair"

    on_disk = Path(written["path"])
    assert on_disk.exists()
    assert "variants" in on_disk.parts

    listed = await store.list_character_variants("wod-london", "alistair")
    assert [v["id"] for v in listed] == ["young"]

    fetched = await store.get_character_variant("wod-london", "alistair", "young")
    assert fetched is not None
    assert fetched["body"] == "A brash newcomer."
    assert fetched["character_id"] == "alistair"

    await store.delete_character_variant(
        world_id="wod-london", base_id="alistair", variant_id="young", source="user"
    )
    assert not on_disk.exists()
    assert await store.get_character_variant("wod-london", "alistair", "young") is None
    with pytest.raises(NotFoundError):
        await store.delete_character_variant(
            world_id="wod-london", base_id="alistair", variant_id="young", source="user"
        )


async def test_variant_never_enters_library_index(store: StateStore) -> None:
    await _seed_character(store)
    await _write_variant(store)
    rows = await store.db.fetchall("SELECT id FROM library_index")
    ids = {row["id"] for row in rows}
    assert ids == {CHAR_ID}


async def test_variant_writes_record_deltas_and_emit_events(tmp_path: Path) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    bus = EventBus()
    events: list = []
    bus.subscribe("library_entity_changed", events.append)
    store = StateStore(db, data_root, event_bus=bus)
    try:
        await _seed_character(store)
        await _write_variant(store)
        await store.delete_character_variant(
            world_id="wod-london", base_id="alistair", variant_id="young", source="user"
        )

        variant_events = [e for e in events if e.payload.get("kind") == "character_variant"]
        assert len(variant_events) == 2
        assert all(e.payload["variant_of"] == CHAR_ID for e in variant_events)

        deltas = await store.db.fetchall(
            "SELECT kind, target_id FROM deltas WHERE target_id LIKE '%variants/young'"
        )
        kinds = {row["kind"] for row in deltas}
        assert kinds == {"library_file_write", "library_file_delete"}
    finally:
        await db.close()


async def test_list_variants_marks_unparseable_files(store: StateStore) -> None:
    """Broken overlays stay visible as error markers — callers can tell
    "no variants" from "broken variant file"."""
    await _seed_character(store)
    await _write_variant(store)
    bad = store.data_root / "library/worlds/wod-london/characters/alistair/variants/broken.md"
    bad.write_text("---\n: not yaml [\n---\nbody\n", encoding="utf-8")
    listed = await store.list_character_variants("wod-london", "alistair")
    assert [v["id"] for v in listed] == ["broken", "young"]
    broken = listed[0]
    assert broken["error"]
    assert broken["frontmatter"] == {}
    young = listed[1]
    assert "error" not in young


# ---------------------------------------------------------------------------
# Resolve cascade: base → variant overlay → campaign override
# ---------------------------------------------------------------------------


async def test_resolve_applies_selected_variant_overlay(store: StateStore) -> None:
    await _seed_character(store)
    await _write_variant(store, body="A brash newcomer.")
    _select_variant(store, "camp-1", "young")

    resolved = await store.resolve_entity(
        campaign_id="camp-1", kind="character", asset_id="alistair", world_id="wod-london"
    )
    assert resolved is not None
    assert resolved["frontmatter"]["age"] == 25
    assert resolved["frontmatter"]["name"] == "Alistair"  # untouched key cascades
    assert resolved["body"] == "A brash newcomer."
    assert resolved["variant"] == "young"
    # Reserved variant keys never leak into the character.
    assert "label" not in resolved["frontmatter"]


async def test_resolve_variant_empty_body_keeps_base_prose(store: StateStore) -> None:
    await _seed_character(store)
    await _write_variant(store, body="")
    _select_variant(store, "camp-1", "young")

    resolved = await store.resolve_entity(
        campaign_id="camp-1", kind="character", asset_id="alistair", world_id="wod-london"
    )
    assert resolved is not None
    assert resolved["body"] == "An elder of the chantry."


async def test_resolve_override_wins_over_variant(store: StateStore) -> None:
    """Cascade order: base → variant → override (override is outermost)."""
    await _seed_character(store)
    await _write_variant(store, frontmatter={"label": "Young", "age": 25, "mood": "brash"})
    _select_variant(store, "camp-1", "young")
    await store.write_override(
        campaign_id="camp-1",
        library_id=CHAR_ID,
        patch={"age": 40},
        source="user",
    )

    resolved = await store.resolve_entity(
        campaign_id="camp-1", kind="character", asset_id="alistair", world_id="wod-london"
    )
    assert resolved is not None
    assert resolved["frontmatter"]["age"] == 40  # override beats variant
    assert resolved["frontmatter"]["mood"] == "brash"  # variant beats base
    assert resolved["source"] == "campaign-override"
    assert resolved["variant"] == "young"


async def test_resolve_variant_extras_merge_with_tombstones(store: StateStore) -> None:
    await _seed_character(store)
    await _write_variant(
        store,
        frontmatter={"label": "Young", "extras": {"haven": None, "sire": "Etrius"}},
    )
    _select_variant(store, "camp-1", "young")

    resolved = await store.resolve_entity(
        campaign_id="camp-1", kind="character", asset_id="alistair", world_id="wod-london"
    )
    assert resolved is not None
    extras = resolved["frontmatter"]["extras"]
    assert extras["clan"] == "Tremere"  # untouched key kept
    assert extras["sire"] == "Etrius"  # variant addition
    assert "haven" not in extras  # tombstoned


async def test_resolve_dangling_selection_falls_back_to_base(store: StateStore, caplog) -> None:
    await _seed_character(store)
    _select_variant(store, "camp-1", "ghost")

    resolved = await store.resolve_entity(
        campaign_id="camp-1", kind="character", asset_id="alistair", world_id="wod-london"
    )
    assert resolved is not None
    assert resolved["frontmatter"]["age"] == 300
    assert "variant" not in resolved
    # The breakage is surfaced, not silently treated as "no selection".
    assert "missing variant" in resolved["variant_error"]
    assert any("missing variant" in r.message for r in caplog.records)


async def test_resolve_marks_unreadable_campaign_yaml(store: StateStore, caplog) -> None:
    """A malformed campaign.yaml can't tell us whether a variant was selected,
    so resolution uses the base but flags the broken state."""
    await _seed_character(store)
    await _write_variant(store)
    campaign_dir = store.data_root / "campaigns" / "camp-1"
    campaign_dir.mkdir(parents=True, exist_ok=True)
    (campaign_dir / "campaign.yaml").write_text(": not yaml [\n", encoding="utf-8")

    resolved = await store.resolve_entity(
        campaign_id="camp-1", kind="character", asset_id="alistair", world_id="wod-london"
    )
    assert resolved is not None
    assert resolved["frontmatter"]["age"] == 300
    assert "unreadable campaign.yaml" in resolved["variant_error"]
    assert any("variant selection" in r.message for r in caplog.records)


async def test_resolve_without_campaign_yaml_uses_base(store: StateStore) -> None:
    await _seed_character(store)
    await _write_variant(store)

    resolved = await store.resolve_entity(
        campaign_id="camp-1", kind="character", asset_id="alistair", world_id="wod-london"
    )
    assert resolved is not None
    assert resolved["frontmatter"]["age"] == 300


# ---------------------------------------------------------------------------
# Selection map read/write (campaign.yaml is SSOT)
# ---------------------------------------------------------------------------


async def test_selection_map_round_trip(store: StateStore) -> None:
    _select_variant(store, "camp-1", None)
    assert await store.get_campaign_variant_selections("camp-1") == {}

    await store.set_campaign_variant_selections("camp-1", {CHAR_ID: "young"})
    assert await store.get_campaign_variant_selections("camp-1") == {CHAR_ID: "young"}

    yaml_path = store.data_root / "campaigns" / "camp-1" / "campaign.yaml"
    raw = load_yaml(yaml_path)
    assert raw["variants"] == {CHAR_ID: "young"}
    assert raw["id"] == "camp-1"  # unrelated keys preserved

    await store.set_campaign_variant_selections("camp-1", {})
    assert "variants" not in (load_yaml(yaml_path) or {})


async def test_selection_map_creates_missing_campaign_yaml(store: StateStore) -> None:
    """Mirrors the LLM gateway's tier persistence: a campaign whose yaml
    hasn't materialized yet can still pin variants."""
    await store.set_campaign_variant_selections("fresh", {CHAR_ID: "young"})
    yaml_path = store.data_root / "campaigns" / "fresh" / "campaign.yaml"
    raw = load_yaml(yaml_path)
    assert raw["id"] == "fresh"
    assert raw["variants"] == {CHAR_ID: "young"}


async def test_variant_file_shape_on_disk(store: StateStore) -> None:
    """The overlay is a plain markdown + frontmatter file a user can hand-edit."""
    await _seed_character(store)
    written = await _write_variant(store, body="Prose override.")
    doc = read_markdown(Path(written["path"]))
    assert doc.frontmatter == {"label": "Young Alistair", "age": 25}
    assert doc.body == "Prose override."


async def test_selection_change_emits_invalidation_event(tmp_path: Path) -> None:
    """Switching a campaign's variant must flush cached character views —
    the event fires only when the map actually changes."""
    data_root = tmp_path / "data"
    data_root.mkdir()
    db = Database(stamp_migrated_db(tmp_path / "campaigns.sqlite"), pool_size=2)
    await db.connect()
    bus = EventBus()
    events: list = []
    bus.subscribe("library_entity_changed", events.append)
    store = StateStore(db, data_root, event_bus=bus)
    try:
        _select_variant(store, "camp-1", None)
        await store.set_campaign_variant_selections("camp-1", {CHAR_ID: "young"})
        assert len(events) == 1
        assert events[0].payload["kind"] == "character_variant"
        assert events[0].payload["campaign_id"] == "camp-1"
        assert events[0].payload["library_ids"] == [CHAR_ID]

        # No-op write → no event.
        await store.set_campaign_variant_selections("camp-1", {CHAR_ID: "young"})
        assert len(events) == 1

        # Clearing is a change again.
        await store.set_campaign_variant_selections("camp-1", {})
        assert len(events) == 2
    finally:
        await db.close()


async def test_delete_base_character_cascades_to_variants(store: StateStore) -> None:
    """Deleting a character removes its overlays (each reversibly recorded),
    so re-creating the same id later doesn't resurrect stale variants."""
    await _seed_character(store)
    await _write_variant(store)
    await _write_variant(store, variant_id="old", frontmatter={"label": "Old", "age": 500})
    variants_dir = store.data_root / "library/worlds/wod-london/characters/alistair/variants"
    assert len(list(variants_dir.glob("*.md"))) == 2

    await store.delete_library_file(library_id=CHAR_ID, source="user")

    assert not variants_dir.exists()
    deltas = await store.db.fetchall(
        "SELECT target_id FROM deltas WHERE kind = 'library_file_delete'"
    )
    targets = {row["target_id"] for row in deltas}
    assert targets == {CHAR_ID, f"{CHAR_ID}/variants/young", f"{CHAR_ID}/variants/old"}

    # Re-creating the character starts variant-free.
    await _seed_character(store)
    assert await store.list_character_variants("wod-london", "alistair") == []
