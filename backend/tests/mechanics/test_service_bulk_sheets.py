"""Tests for ``MechanicsService.bulk_create_missing_sheets`` (issue #592).

The orchestration that used to live in the ``sheets`` router now lives here:
enumerate the campaign's entities per sheet-kind, initialise a sheet for any
that lack one, and skip the rest. Enumeration failures surface rather than
being silently swallowed into an empty list.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from grimoire.mechanics import MechanicsConfig, MechanicsService
from grimoire.state_store.errors import ConflictError, NotFoundError

from .conftest import write_module


class _Char:
    def __init__(self, char_id: str) -> None:
        self.id = char_id


class _Resolved:
    def __init__(self, char_id: str) -> None:
        self.character = _Char(char_id)


class FakeCharacters:
    def __init__(self, ids: list[str], *, error: Exception | None = None) -> None:
        self._ids = ids
        self._error = error

    async def list_for_campaign(self, campaign_id: str) -> list[Any]:
        if self._error is not None:
            raise self._error
        return [_Resolved(i) for i in self._ids]


class FakeWorld:
    def __init__(self, by_kind: dict[str, list[Any]] | None = None) -> None:
        self._by_kind = by_kind or {}

    async def list_for_campaign(self, campaign_id: str, kind: str) -> list[Any]:
        return self._by_kind.get(kind, [])


async def _service(mechanics_root: Path, store) -> MechanicsService:
    service = MechanicsService(config=MechanicsConfig(root=mechanics_root), state_store=store)
    await service.rescan()
    return service


async def test_creates_missing_and_skips_existing(store, mechanics_root: Path) -> None:
    write_module(mechanics_root, "vamp")
    service = await _service(mechanics_root, store)
    await store.upsert_campaign(campaign_id="c1", name="C", mechanics_module="vamp")
    # alistair already has a sheet under the bound module.
    await store.write_sheet(
        campaign_id="c1",
        kind="character",
        entity_id="alistair",
        mechanics_id="vamp",
        sheet={"name": "Alistair"},
        source="user",
    )

    result = await service.bulk_create_missing_sheets(
        "c1",
        characters=FakeCharacters(["alistair", "dorian"]),
        world=FakeWorld(),
    )

    assert [s.entity_id for s in result.skipped] == ["alistair"]
    assert [s.entity_id for s in result.created] == ["dorian"]
    # The new sheet was actually persisted.
    ids = await store.list_sheet_entity_ids(campaign_id="c1", kind="character", mechanics_id="vamp")
    assert ids == {"alistair", "dorian"}


async def test_enumerates_world_entities_for_non_character_kinds(
    store, mechanics_root: Path
) -> None:
    write_module(mechanics_root, "vamp", manifest={"sheet_kinds": ["character", "item"]})
    service = await _service(mechanics_root, store)
    await store.upsert_campaign(campaign_id="c1", name="C", mechanics_module="vamp")

    class _Entity:
        def __init__(self, asset_id: str) -> None:
            self.asset_id = asset_id

    result = await service.bulk_create_missing_sheets(
        "c1",
        characters=FakeCharacters([]),
        world=FakeWorld({"item": [_Entity("sword"), _Entity("shield")]}),
    )

    assert {s.entity_id for s in result.created} == {"sword", "shield"}
    assert all(s.kind == "item" for s in result.created)


async def test_409_when_no_module_bound(store, mechanics_root: Path) -> None:
    service = await _service(mechanics_root, store)
    await store.upsert_campaign(campaign_id="c1", name="C", mechanics_module=None)
    with pytest.raises(ConflictError):
        await service.bulk_create_missing_sheets(
            "c1", characters=FakeCharacters([]), world=FakeWorld()
        )


async def test_404_for_unknown_campaign(store, mechanics_root: Path) -> None:
    service = await _service(mechanics_root, store)
    with pytest.raises(NotFoundError):
        await service.bulk_create_missing_sheets(
            "nope", characters=FakeCharacters([]), world=FakeWorld()
        )


async def test_list_failure_surfaces_not_swallowed(store, mechanics_root: Path) -> None:
    write_module(mechanics_root, "vamp")
    service = await _service(mechanics_root, store)
    await store.upsert_campaign(campaign_id="c1", name="C", mechanics_module="vamp")

    with pytest.raises(RuntimeError, match="cast listing broke"):
        await service.bulk_create_missing_sheets(
            "c1",
            characters=FakeCharacters([], error=RuntimeError("cast listing broke")),
            world=FakeWorld(),
        )
