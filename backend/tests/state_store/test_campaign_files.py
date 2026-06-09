"""Overrides, emergent entities, sheets, image metadata, and the resolve cascade."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from grimoire.state_store import StateStore
from grimoire.state_store import store as _store_module
from grimoire.state_store.paths import (
    content_path,
    emergent_path,
    image_metadata_path,
    override_path,
    sheet_path,
)


async def _seed_campaign(store: StateStore) -> None:
    await store.upsert_campaign(campaign_id="c1", name="Test Campaign")


async def test_emergent_entity_round_trip(store: StateStore) -> None:
    await _seed_campaign(store)
    target = await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="the-bartender",
        frontmatter={"name": "The Bartender"},
        body="He keeps the books.",
        source="extractor",
    )
    assert target.exists()

    fetched = await store.get_emergent("c1", "character", "the-bartender")
    assert fetched["frontmatter"]["name"] == "The Bartender"
    assert "books" in fetched["body"]

    listed = await store.list_emergent("c1", "character")
    assert len(listed) == 1
    assert listed[0]["asset_id"] == "the-bartender"


async def test_override_falls_back_to_library_via_resolve(store: StateStore) -> None:
    await _seed_campaign(store)
    await store.write_library_file(
        library_id="worlds/wod-london/characters/winifred",
        frontmatter={"name": "winifred", "voice": "patient"},
        body="Library copy.",
        source="user",
    )
    await store.upsert_world_ref(
        campaign_id="c1",
        world_id="wod-london",
        priority=1,
        include=["character"],
        track_latest=True,
    )

    # No override yet: resolve returns the library row.
    resolved = await store.resolve_entity(
        campaign_id="c1",
        kind="character",
        asset_id="winifred",
        world_id="wod-london",
    )
    assert resolved["source"] == "library-live"
    assert resolved["frontmatter"]["voice"] == "patient"

    # Write an override; resolve merges frontmatter on top of the library row.
    await store.write_override(
        campaign_id="c1",
        library_id="worlds/wod-london/characters/winifred",
        patch={"voice": "wary", "mood": "grim"},
        source="user",
    )
    resolved = await store.resolve_entity(
        campaign_id="c1",
        kind="character",
        asset_id="winifred",
        world_id="wod-london",
    )
    assert resolved["source"] == "campaign-override"
    assert resolved["frontmatter"]["voice"] == "wary"
    assert resolved["frontmatter"]["mood"] == "grim"
    assert resolved["frontmatter"]["name"] == "winifred"  # library still wins for unchanged keys


async def test_resolve_finds_emergent_when_world_omitted(store: StateStore) -> None:
    await _seed_campaign(store)
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="the-bartender",
        frontmatter={"name": "The Bartender"},
        body="campaign-local",
        source="extractor",
    )
    resolved = await store.resolve_entity(
        campaign_id="c1",
        kind="character",
        asset_id="the-bartender",
    )
    assert resolved["source"] == "campaign-emergent"


async def test_sheet_write_round_trip(store: StateStore) -> None:
    await _seed_campaign(store)
    await store.write_sheet(
        campaign_id="c1",
        kind="character",
        entity_id="winifred",
        mechanics_id="wod",
        sheet={"clan": "Toreador", "attributes": {"dex": 4}},
        source="user",
    )
    sheet = await store.get_sheet("c1", "character", "winifred", "wod")
    assert sheet["clan"] == "Toreador"
    assert sheet["attributes"]["dex"] == 4


async def test_image_metadata_write(store: StateStore) -> None:
    await _seed_campaign(store)
    await store.write_image_metadata(
        campaign_id="c1",
        image_id="img-123",
        metadata={
            "prompt": "a foggy street",
            "scene_id": "0001",
        },
        source="imagegen",
    )
    log = await store.get_delta_log(campaign_id="c1")
    write_log = [d for d in log if d.kind == "image_metadata_write"]
    assert len(write_log) == 1


# ---------------------------------------------------------------------------
# Snapshot/restore on txn failure (#585): every campaign-content file write
# shares write_library_file's compensation, so an index/delta-log failure
# leaves the file byte-identical to before the call.
# ---------------------------------------------------------------------------

_LIB_ID = "worlds/wod-london/characters/winifred"


def _override_target(store: StateStore) -> Path:
    return override_path(store.data_root, "c1", "wod-london", "character", "winifred")


def _emergent_target(store: StateStore) -> Path:
    return emergent_path(store.data_root, "c1", "character", "the-bartender")


def _sheet_target(store: StateStore) -> Path:
    return sheet_path(store.data_root, "c1", "character", "winifred", "wod")


def _content_target(store: StateStore) -> Path:
    return content_path(store.data_root, "c1", "ritual", "warding-circle", "wod")


def _image_target(store: StateStore) -> Path:
    return image_metadata_path(store.data_root, "c1", "img-123")


async def _write_override_v1(store: StateStore) -> None:
    await store.write_override(
        campaign_id="c1", library_id=_LIB_ID, patch={"voice": "patient"}, source="user"
    )


async def _write_override_v2(store: StateStore) -> None:
    await store.write_override(
        campaign_id="c1",
        library_id=_LIB_ID,
        patch={"voice": "wary — must not survive"},
        source="user",
    )


async def _merge_override(store: StateStore) -> None:
    await store.merge_override(
        campaign_id="c1",
        world_id="wod-london",
        kind="character",
        asset_id="winifred",
        patch={"mood": "grim — must not survive"},
        source="user",
    )


async def _delete_override(store: StateStore) -> None:
    await store.delete_override(campaign_id="c1", library_id=_LIB_ID, source="user")


async def _write_emergent_v1(store: StateStore) -> None:
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="the-bartender",
        frontmatter={"name": "The Bartender"},
        body="original",
        source="extractor",
    )


async def _write_emergent_v2(store: StateStore) -> None:
    await store.write_emergent(
        campaign_id="c1",
        kind="character",
        entity_id="the-bartender",
        frontmatter={"name": "The Bartender"},
        body="modified — must not survive",
        source="extractor",
    )


async def _delete_emergent(store: StateStore) -> None:
    await store.delete_emergent(
        campaign_id="c1", kind="character", entity_id="the-bartender", source="user"
    )


async def _write_sheet_v1(store: StateStore) -> None:
    await store.write_sheet(
        campaign_id="c1",
        kind="character",
        entity_id="winifred",
        mechanics_id="wod",
        sheet={"clan": "Toreador"},
        source="user",
    )


async def _write_sheet_v2(store: StateStore) -> None:
    await store.write_sheet(
        campaign_id="c1",
        kind="character",
        entity_id="winifred",
        mechanics_id="wod",
        sheet={"clan": "Ventrue — must not survive"},
        source="user",
    )


async def _write_content_v1(store: StateStore) -> None:
    await store.write_content(
        campaign_id="c1",
        kind="ritual",
        content_id="warding-circle",
        mechanics_id="wod",
        payload={"level": 1},
        source="user",
    )


async def _write_content_v2(store: StateStore) -> None:
    await store.write_content(
        campaign_id="c1",
        kind="ritual",
        content_id="warding-circle",
        mechanics_id="wod",
        payload={"level": 2, "note": "must not survive"},
        source="user",
    )


async def _write_image_v1(store: StateStore) -> None:
    await store.write_image_metadata(
        campaign_id="c1",
        image_id="img-123",
        metadata={"prompt": "a foggy street"},
        source="imagegen",
    )


async def _write_image_v2(store: StateStore) -> None:
    await store.write_image_metadata(
        campaign_id="c1",
        image_id="img-123",
        metadata={"prompt": "modified — must not survive"},
        source="imagegen",
    )


_Seed = Callable[[StateStore], Awaitable[None]]

_ROLLBACK_CASES = [
    pytest.param(None, _write_override_v1, _override_target, id="write_override-new"),
    pytest.param(_write_override_v1, _write_override_v2, _override_target, id="write_override"),
    pytest.param(_write_override_v1, _merge_override, _override_target, id="merge_override"),
    pytest.param(_write_override_v1, _delete_override, _override_target, id="delete_override"),
    pytest.param(None, _write_emergent_v1, _emergent_target, id="write_emergent-new"),
    pytest.param(_write_emergent_v1, _write_emergent_v2, _emergent_target, id="write_emergent"),
    pytest.param(_write_emergent_v1, _delete_emergent, _emergent_target, id="delete_emergent"),
    pytest.param(None, _write_sheet_v1, _sheet_target, id="write_sheet-new"),
    pytest.param(_write_sheet_v1, _write_sheet_v2, _sheet_target, id="write_sheet"),
    pytest.param(None, _write_content_v1, _content_target, id="write_content-new"),
    pytest.param(_write_content_v1, _write_content_v2, _content_target, id="write_content"),
    pytest.param(None, _write_image_v1, _image_target, id="write_image_metadata-new"),
    pytest.param(_write_image_v1, _write_image_v2, _image_target, id="write_image_metadata"),
]


@pytest.mark.parametrize(("seed", "attempt", "target_for"), _ROLLBACK_CASES)
async def test_txn_failure_leaves_file_byte_identical(
    store: StateStore,
    monkeypatch: pytest.MonkeyPatch,
    seed: _Seed | None,
    attempt: _Seed,
    target_for: Callable[[StateStore], Path],
) -> None:
    await _seed_campaign(store)
    if seed is not None:
        await seed(store)
    target = target_for(store)
    before_bytes = target.read_bytes() if target.exists() else None
    deltas_before = await store.count_deltas("c1")

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated SQL failure")

    monkeypatch.setattr(_store_module, "insert_delta", _boom)

    with pytest.raises(RuntimeError, match="simulated SQL failure"):
        await attempt(store)

    if before_bytes is None:
        assert not target.exists()
    else:
        assert target.read_bytes() == before_bytes
    assert await store.count_deltas("c1") == deltas_before
