"""Tests for the scene-ledger greeting backfill helpers (issue #472)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from grimoire.api.campaigns.helpers import (
    _backfill_ledger_from_greetings,
    _greeting_applies,
    _pc_role_tag_union,
)
from grimoire.scenes.ledger import SceneLedger
from grimoire.storage import Database, apply_migrations
from grimoire.types.composition import Greeting


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "test.sqlite", pool_size=2)
    await database.connect()
    await apply_migrations(database)
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def ledger(db):
    return SceneLedger(db)


def _greeting(gid: str, *, name: str = "", role_tags: list[str] | None = None) -> Greeting:
    return Greeting(
        id=gid,
        world_id="w1",
        name=name or gid,
        starting_location="Harbor",
        starting_time=None,
        role_tags=role_tags or [],
    )


def _world_ref(world_id: str) -> object:
    class _Ref:
        pass

    ref = _Ref()
    ref.world_id = world_id
    return ref


def _fakes(*, greetings_by_world: dict[str, list[Greeting]], pc_role_tags: list[list[str]]):
    import json

    library = AsyncMock()
    library.list_greetings = AsyncMock(side_effect=lambda wid: greetings_by_world.get(wid, []))
    state_store = AsyncMock()
    state_store.list_pcs = AsyncMock(
        return_value=[{"role_tags": json.dumps(tags)} for tags in pc_role_tags]
    )
    return library, state_store


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_pc_role_tag_union_parses_json_strings() -> None:
    rows = [{"role_tags": '["hero", "mage"]'}, {"role_tags": '["mage", "noble"]'}]
    assert _pc_role_tag_union(rows) == {"hero", "mage", "noble"}


def test_pc_role_tag_union_tolerates_bad_values() -> None:
    rows = [{"role_tags": None}, {"role_tags": "not json"}, {"role_tags": ["raw", "list"]}, {}]
    assert _pc_role_tag_union(rows) == {"raw", "list"}


def test_greeting_applies_universal_when_no_role_tags() -> None:
    assert _greeting_applies([], set()) is True
    assert _greeting_applies([], {"hero"}) is True


def test_greeting_applies_requires_match_when_tagged() -> None:
    assert _greeting_applies(["hero"], {"hero", "mage"}) is True
    assert _greeting_applies(["villain"], {"hero", "mage"}) is False
    # tagged greeting + no PC tags => excluded
    assert _greeting_applies(["hero"], set()) is False


# --------------------------------------------------------------------------- #
# Backfill orchestration
# --------------------------------------------------------------------------- #


async def test_backfill_excludes_opening_and_non_applicable(ledger: SceneLedger) -> None:
    greetings = [
        _greeting("gr-open", name="Opening"),  # the opening greeting (excluded)
        _greeting("gr-universal", name="Universal"),  # no tags -> applies
        _greeting("gr-hero", name="Hero hook", role_tags=["hero"]),  # matches PC
        _greeting("gr-villain", name="Villain hook", role_tags=["villain"]),  # no match
    ]
    library, state_store = _fakes(
        greetings_by_world={"w1": greetings},
        pc_role_tags=[["hero"]],
    )

    added = await _backfill_ledger_from_greetings(
        campaign_id="c1",
        library=library,
        state_store=state_store,
        ledger=ledger,
        world_refs=[_world_ref("w1")],
        exclude_greeting_ids={"gr-open"},
    )

    assert len(added) == 2
    items = await ledger.list_active("c1")
    ids = {i["greeting_id"] for i in items}
    assert ids == {"gr-universal", "gr-hero"}
    assert all(i["source"] == "greeting" for i in items)


async def test_backfill_is_idempotent(ledger: SceneLedger) -> None:
    greetings = [_greeting("gr-universal"), _greeting("gr-hero", role_tags=["hero"])]
    library, state_store = _fakes(greetings_by_world={"w1": greetings}, pc_role_tags=[["hero"]])

    first = await _backfill_ledger_from_greetings(
        campaign_id="c1",
        library=library,
        state_store=state_store,
        ledger=ledger,
        world_refs=[_world_ref("w1")],
    )
    second = await _backfill_ledger_from_greetings(
        campaign_id="c1",
        library=library,
        state_store=state_store,
        ledger=ledger,
        world_refs=[_world_ref("w1")],
    )

    assert len(first) == 2
    assert second == []
    assert len(await ledger.list_all("c1")) == 2


async def test_backfill_spans_multiple_worlds(ledger: SceneLedger) -> None:
    library, state_store = _fakes(
        greetings_by_world={
            "w1": [_greeting("gr-a")],
            "w2": [_greeting("gr-b", role_tags=["mage"])],
        },
        pc_role_tags=[["mage"]],
    )

    added = await _backfill_ledger_from_greetings(
        campaign_id="c1",
        library=library,
        state_store=state_store,
        ledger=ledger,
        world_refs=[_world_ref("w1"), _world_ref("w2")],
    )

    assert len(added) == 2
    ids = {i["greeting_id"] for i in await ledger.list_active("c1")}
    assert ids == {"gr-a", "gr-b"}
