"""Tests for context_pins integration in the Context Builder."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grimoire.context import ContextBuilderConfig, ContextBuilderService, TierBudget
from grimoire.types.inclusion_reasons import InclusionReason
from grimoire.types.state import ContextTier

from .test_builder import (
    StubCharacters,
    StubContinuity,
    StubLibrary,
    StubScenes,
    StubWorld,
    _Card,
    _Scene,
)


@dataclass
class StubPinStore:
    pins: list[dict] = field(default_factory=list)

    async def list_active_context_pins(
        self,
        *,
        campaign_id: str,
        current_turn_id: str | None = None,
    ) -> list[dict]:
        return [p for p in self.pins if p["campaign_id"] == campaign_id]

    async def vector_search(self, **kwargs: Any) -> list[Any]:
        return []

    async def keyword_search(self, **kwargs: Any) -> list[Any]:
        return []


def _builder(**overrides: Any) -> ContextBuilderService:
    defaults: dict[str, Any] = {
        "library": StubLibrary(),
        "characters": StubCharacters(),
        "world": StubWorld(),
        "scenes": StubScenes(),
        "continuity": StubContinuity(),
        "state_store": None,
        "gateway": None,
    }
    defaults.update(overrides)
    return ContextBuilderService(**defaults)


async def test_pin_emits_pinned_by_user_reason() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/henry": _Card(full="# Henry", compressed="# Henry")},
    )
    scene = _Scene(present_character_refs=["library:worlds/wod/characters/henry"])
    scenes = StubScenes(scene=scene)
    store = StubPinStore(
        pins=[
            {
                "id": "ctx_pin_1",
                "campaign_id": "camp",
                "kind": "pin",
                "target_kind": "entity",
                "target_source_id": None,
                "target_entity_kind": "character",
                "target_entity_id": "library:worlds/wod/characters/henry",
                "cleared_at": None,
                "expires_at_turn_id": None,
            }
        ],
    )
    builder = _builder(characters=chars, scenes=scenes, state_store=store)
    prompt = await builder.build("hello", "camp")
    henry = next(
        s
        for s in prompt.sources
        if s.kind == "character" and s.owner_id == "library:worlds/wod/characters/henry"
    )
    assert InclusionReason.PINNED_BY_USER in henry.inclusion_reasons


async def test_excluded_entity_dropped_from_candidates() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/henry": _Card(full="# Henry", compressed="# Henry")},
    )
    scene = _Scene(present_character_refs=["library:worlds/wod/characters/henry"])
    scenes = StubScenes(scene=scene)
    store = StubPinStore(
        pins=[
            {
                "id": "ctx_pin_x",
                "campaign_id": "camp",
                "kind": "exclude",
                "target_kind": "entity",
                "target_source_id": None,
                "target_entity_kind": "character",
                "target_entity_id": "library:worlds/wod/characters/henry",
                "cleared_at": None,
                "expires_at_turn_id": None,
            }
        ],
    )
    builder = _builder(characters=chars, scenes=scenes, state_store=store)
    prompt = await builder.build("hello", "camp")
    henry_sources = [
        s for s in prompt.sources if s.owner_id == "library:worlds/wod/characters/henry"
    ]
    assert henry_sources == []


async def test_pinned_entity_survives_budget_truncation() -> None:
    """Henry would not fit the background budget — pinning protects him."""
    chars = StubCharacters(
        cards={
            "library:worlds/wod/characters/henry": _Card(
                full="# Henry " + "x" * 4000,
                compressed="# Henry " + "x" * 4000,
            ),
        },
    )
    # Henry is mentioned in recent posts → background tier candidate
    from .test_builder import _Post

    posts = [_Post(body="They spoke of library:worlds/wod/characters/henry that night.")]
    scene = _Scene(present_character_refs=[])
    scenes = StubScenes(scene=scene, posts=posts)
    # Make background tier impossibly tight.
    config = ContextBuilderConfig(
        tiers={
            ContextTier.LOCK_IN: TierBudget(max_tokens=8000),
            ContextTier.SPOTLIGHT: TierBudget(max_tokens=40000),
            ContextTier.BACKGROUND: TierBudget(max_tokens=10),
            ContextTier.ARCHIVE: TierBudget(max_tokens=20000),
        }
    )
    store_no_pin = StubPinStore(pins=[])
    b_unpinned = _builder(characters=chars, scenes=scenes, state_store=store_no_pin, config=config)
    p_unpinned = await b_unpinned.build("hello", "camp")
    background_msgs = [m for m in p_unpinned.messages if m.content.startswith("# Background")]
    # Without pinning the long Henry card is dropped from the tight tier.
    if background_msgs:
        assert "Henry" not in background_msgs[0].content or len(background_msgs[0].content) < 200

    store_with_pin = StubPinStore(
        pins=[
            {
                "id": "ctx_pin_h",
                "campaign_id": "camp",
                "kind": "pin",
                "target_kind": "entity",
                "target_source_id": None,
                "target_entity_kind": "character",
                "target_entity_id": "library:worlds/wod/characters/henry",
                "cleared_at": None,
                "expires_at_turn_id": None,
            }
        ],
    )
    b_pinned = _builder(characters=chars, scenes=scenes, state_store=store_with_pin, config=config)
    p_pinned = await b_pinned.build("hello", "camp")
    background_msgs_pinned = [m for m in p_pinned.messages if m.content.startswith("# Background")]
    assert any("Henry" in m.content for m in background_msgs_pinned)


async def test_pin_does_not_reorder_tier() -> None:
    """A pinned character stays in its naturally-assigned tier."""
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/henry": _Card(compressed="# Henry compact")},
    )
    from .test_builder import _Post

    posts = [_Post(body="They spoke of library:worlds/wod/characters/henry yesterday.")]
    scene = _Scene(present_character_refs=[])
    scenes = StubScenes(scene=scene, posts=posts)
    store = StubPinStore(
        pins=[
            {
                "id": "ctx_pin_h",
                "campaign_id": "camp",
                "kind": "pin",
                "target_kind": "entity",
                "target_source_id": None,
                "target_entity_kind": "character",
                "target_entity_id": "library:worlds/wod/characters/henry",
                "cleared_at": None,
                "expires_at_turn_id": None,
            }
        ],
    )
    builder = _builder(characters=chars, scenes=scenes, state_store=store)
    prompt = await builder.build("hello", "camp")
    henry = next(s for s in prompt.sources if s.owner_id == "library:worlds/wod/characters/henry")
    assert henry.tier == ContextTier.BACKGROUND


async def test_pin_load_failure_raises_instead_of_silently_leaking() -> None:
    """Privacy guard: a DB error during pin loading must fail the build
    rather than silently leak user-excluded content into the prompt."""
    import pytest

    @dataclass
    class _BrokenStore:
        async def list_active_context_pins(self, **kwargs: Any) -> list[dict]:
            raise RuntimeError("simulated DB outage")

        async def vector_search(self, **kwargs: Any) -> list[Any]:
            return []

        async def keyword_search(self, **kwargs: Any) -> list[Any]:
            return []

    builder = _builder(state_store=_BrokenStore())
    with pytest.raises(RuntimeError, match="simulated DB outage"):
        await builder.build("hello", "camp")
