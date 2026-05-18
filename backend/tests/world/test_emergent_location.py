"""§9 LLM-driven emergent-location pipeline.

Covers both the LLM frontmatter generator and the WorldService
apply-delta path that materializes the emergent entity on disk.
"""

from __future__ import annotations

import json

import pytest

from grimoire.types.common import Scope
from grimoire.types.state import DeltaKind, StateDelta
from grimoire.world.location_generator import generate_location_frontmatter


class _FakeGateway:
    def __init__(self, response_text: str) -> None:
        self.response_text = response_text
        self.calls: list = []

    async def complete(self, task, request, *, campaign_id=None, turn_id=None):
        self.calls.append({"task": task, "request": request})
        return type("Resp", (), {"text": self.response_text})()


async def test_generator_returns_parsed_dict() -> None:
    gw = _FakeGateway(json.dumps({"id": "tavern", "name": "Old Tavern", "kind": "building"}))
    out = await generate_location_frontmatter(gateway=gw, name="tavern")
    assert out["name"] == "Old Tavern"
    assert gw.calls[0]["task"] == "world_location_generate"


async def test_generator_returns_empty_on_bad_json() -> None:
    gw = _FakeGateway("not json")
    out = await generate_location_frontmatter(gateway=gw, name="x")
    assert out == {}


async def test_generator_returns_empty_when_no_gateway() -> None:
    out = await generate_location_frontmatter(gateway=None, name="x")
    assert out == {}


async def test_apply_emergent_location_writes_to_disk(store, library, world) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    world.gateway = _FakeGateway(
        json.dumps({"name": "Old Tavern", "kind": "building", "description": "An old inn."})
    )
    delta = StateDelta(
        kind=DeltaKind.EMERGENT_CREATE,
        target_scope=Scope.CAMPAIGN_FILE,
        target_path="emergent/location/tavern",
        target_id="emergent:location:tavern",
        after={
            "campaign_id": "camp-1",
            "branch_id": "camp-1:main",
            "kind": "location",
            "name": "tavern",
            "evidence": "they entered the tavern",
        },
        confidence=0.4,
        source="extractor",
    )
    path = await world.apply_emergent_location_delta(delta)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "Old Tavern" in text


async def test_apply_emergent_location_rejects_wrong_kind(store, library, world) -> None:
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="x",
        after={},
        confidence=0.5,
        source="extractor",
    )
    with pytest.raises(ValueError, match="EMERGENT_CREATE"):
        await world.apply_emergent_location_delta(delta)


async def test_apply_emergent_location_works_without_gateway(store, library, world) -> None:
    """Without a gateway, the frontmatter is bare-minimum; still produces a file."""
    await store.upsert_campaign(campaign_id="camp-1", name="t")
    world.gateway = None
    delta = StateDelta(
        kind=DeltaKind.EMERGENT_CREATE,
        target_scope=Scope.CAMPAIGN_FILE,
        target_path="emergent/location/abandoned-keep",
        target_id="emergent:location:abandoned-keep",
        after={
            "campaign_id": "camp-1",
            "kind": "location",
            "name": "abandoned keep",
        },
        confidence=0.4,
        source="extractor",
    )
    path = await world.apply_emergent_location_delta(delta)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "abandoned keep" in text
