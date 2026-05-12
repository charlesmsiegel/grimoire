"""Tests for ContextBuilderService.

The Context Builder consumes a fan of domain modules — Characters, Setting,
Scene Manager, Continuity, Library, LLM Gateway, State Store. Wiring all of
them up for every test would be expensive; instead we hand the builder
small stubs whose surface matches what production passes. This lets us
prove pipeline behavior (tier ordering, budget enforcement, source
attribution, mechanics injection, overflow handling) without leaning on
the wider stack.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from grimoire.context import (
    ContextBuilderConfig,
    ContextBuilderService,
    LockInOverflowError,
    TierBudget,
)
from grimoire.types.composition import Composition, SettingRef
from grimoire.types.mechanics import MechanicsResult, Roll, RollResult
from grimoire.types.state import ContextTier

# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #


@dataclass
class _StyleGuide:
    body: str = ""


@dataclass
class _SettingMeta:
    id: str
    name: str = ""


class StubLibrary:
    def __init__(
        self,
        composition: Composition | None = None,
        style_guide_body: str | None = None,
        settings: dict[str, _SettingMeta] | None = None,
    ) -> None:
        self._composition = composition or Composition()
        self._style_guide_body = style_guide_body
        self._settings = settings or {}

    async def get_composition(self, campaign_id: str) -> Composition:
        return self._composition

    async def get_setting(self, setting_id: str) -> _SettingMeta:
        if setting_id not in self._settings:
            raise KeyError(setting_id)
        return self._settings[setting_id]

    async def get_style_guide(self, style_id: str) -> _StyleGuide:
        if self._style_guide_body is None:
            raise KeyError(style_id)
        return _StyleGuide(body=self._style_guide_body)


@dataclass
class _Card:
    full: str = ""
    compressed: str = ""
    corrective: str = ""


class StubCharacters:
    def __init__(
        self,
        cards: dict[str, _Card] | None = None,
        active: str | None = None,
    ) -> None:
        self._cards = cards or {}
        self._active = active

    async def active_pc(self, campaign_id: str) -> str | None:
        return self._active

    async def get_full_card(self, ref: str, campaign_id: str) -> str:
        return self._cards.get(ref, _Card()).full

    async def get_compressed_card(self, ref: str, campaign_id: str) -> str:
        return self._cards.get(ref, _Card()).compressed

    async def drift_corrective_context(self, ref: str, campaign_id: str) -> str:
        return self._cards.get(ref, _Card()).corrective


@dataclass
class _Location:
    setting_id: str
    id: str
    name: str
    description: str = ""
    body: str = ""
    permanent_features: list[str] = field(default_factory=list)


@dataclass
class _Weather:
    summary: str
    kind: str = "clear"


class StubSetting:
    def __init__(
        self,
        locations: dict[tuple[str, str], _Location] | None = None,
        weather: _Weather | None = None,
        adjacent: list[_Location] | None = None,
        lore: list[Any] | None = None,
    ) -> None:
        self._locations = locations or {}
        self._weather = weather
        self._adjacent = adjacent or []
        self._lore = lore or []

    async def get_location(self, setting_id: str, location_id: str) -> _Location:
        return self._locations[(setting_id, location_id)]

    async def weather_for(
        self,
        setting_id: str,
        location_id: str,
        when: Any,
        campaign_id: str,
        *,
        branch_id: str | None = None,
    ) -> _Weather | None:
        return self._weather

    async def adjacent_locations(
        self, setting_id: str, location_id: str, campaign_id: str
    ) -> list[_Location]:
        return self._adjacent

    async def lore_for_post(self, text: str, campaign_id: str) -> list[Any]:
        return [
            lore for lore in self._lore if any(kw in text for kw in getattr(lore, "keywords", []))
        ]


@dataclass
class _Post:
    body: str
    author_label: str = "narrator"


@dataclass
class _Scene:
    id: str = "scene-1"
    title: str = "The Tower"
    slug: str = "tower"
    location_ref: str | None = None
    in_game_start: Any = None
    mood: str = ""
    present_character_refs: list[str] = field(default_factory=list)
    running_summary: str = ""


class StubScenes:
    def __init__(self, scene: _Scene | None = None, posts: list[_Post] | None = None) -> None:
        self._scene = scene
        self._posts = posts or []

    async def active_scene_for_campaign(
        self, campaign_id: str, branch_id: str = "main"
    ) -> _Scene | None:
        return self._scene

    async def recent_posts(self, scene_id: str, n: int = 10) -> list[_Post]:
        return self._posts[-n:]


@dataclass
class _Commitment:
    text: str
    id: str = ""
    due_by: Any = None


@dataclass
class _Fact:
    text: str
    id: str = ""


class StubContinuity:
    def __init__(
        self,
        commitments: list[_Commitment] | None = None,
        facts: list[_Fact] | None = None,
    ) -> None:
        self._commitments = commitments or []
        self._facts = facts or []

    async def open_commitments(self, limit: int = 20) -> list[_Commitment]:
        return list(self._commitments)

    async def facts_about(self, limit: int = 50) -> list[_Fact]:
        return list(self._facts)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _builder(**overrides: Any) -> ContextBuilderService:
    defaults: dict[str, Any] = {
        "library": StubLibrary(),
        "characters": StubCharacters(),
        "setting": StubSetting(),
        "scenes": StubScenes(),
        "continuity": StubContinuity(),
        "state_store": None,
        "gateway": None,
    }
    defaults.update(overrides)
    return ContextBuilderService(**defaults)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


async def test_build_minimal_returns_user_message() -> None:
    builder = _builder()
    prompt = await builder.build("Hello", "campaign-1")
    assert prompt.messages[-1].role == "user"
    assert prompt.messages[-1].content == "Hello"
    # No mechanics, no scene → only the user message is guaranteed.
    assert len(prompt.messages) >= 1


async def test_system_block_includes_style_and_boundaries() -> None:
    composition = Composition(
        inline_style_guide="Present-tense prose.",
        content_boundaries="No minors in sexual contexts.",
        settings=[SettingRef(setting_id="wod-london", priority=1)],
    )
    library = StubLibrary(
        composition=composition,
        settings={"wod-london": _SettingMeta(id="wod-london", name="WoD London")},
    )
    builder = _builder(library=library)
    prompt = await builder.build("scene begins", "camp")
    system = next(m for m in prompt.messages if m.role == "system")
    assert "Present-tense prose." in system.content
    assert "No minors in sexual contexts." in system.content
    assert "WoD London" in system.content


async def test_active_pc_card_in_lock_in() -> None:
    chars = StubCharacters(
        cards={
            "library:settings/wod/characters/alistair": _Card(full="# Alistair\nElder Tremere.")
        },
        active="library:settings/wod/characters/alistair",
    )
    builder = _builder(characters=chars)
    prompt = await builder.build("scene begins", "camp")
    # Look for the active PC card content somewhere in the messages.
    body = "\n".join(m.content for m in prompt.messages)
    assert "Elder Tremere" in body
    assert any(s.tier == ContextTier.LOCK_IN and s.kind == "character" for s in prompt.sources)


async def test_present_characters_in_spotlight_only() -> None:
    chars = StubCharacters(
        cards={
            "library:settings/wod/characters/alistair": _Card(full="# Alistair\nFull card"),
            "library:settings/wod/characters/winifred": _Card(full="# winifred\nFull card"),
        },
        active="library:settings/wod/characters/alistair",
    )
    scene = _Scene(
        present_character_refs=[
            "library:settings/wod/characters/alistair",
            "library:settings/wod/characters/winifred",
        ]
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(characters=chars, scenes=scenes)
    prompt = await builder.build("hello", "camp")
    # winifred (present, not the PC) should appear in a spotlight section.
    spotlight = [m for m in prompt.messages if m.content.startswith("# Spotlight")]
    assert spotlight
    assert "winifred" in spotlight[0].content
    # Active PC isn't doubled up — its card is only in lock-in.
    assert spotlight[0].content.count("# Alistair") == 0


async def test_mechanics_results_injected() -> None:
    builder = _builder()
    roll = Roll(id="r1", kind="dice-pool", pool=5, seed=1, actor_ref="alistair")
    result = RollResult(roll_id="r1", dice=[7, 8, 9], successes=3, outcome="hit")
    mech = MechanicsResult(roll=roll, result=result, summary="solid")
    prompt = await builder.build("strike", "camp", mechanics_results=[mech])
    body = "\n".join(m.content for m in prompt.messages)
    assert "Mechanical results for this turn" in body
    assert "alistair" in body
    assert "3 successes" in body


async def test_mechanics_block_absent_when_no_results() -> None:
    builder = _builder()
    prompt = await builder.build("scene begins", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Mechanical results" not in body


async def test_lock_in_overflow_raises() -> None:
    # Force a tiny lock-in budget to trigger overflow.
    config = ContextBuilderConfig()
    config.tiers[ContextTier.LOCK_IN] = TierBudget(max_tokens=2, priority="required")
    chars = StubCharacters(
        cards={"library:settings/wod/characters/alistair": _Card(full="x" * 4000)},
        active="library:settings/wod/characters/alistair",
    )
    builder = _builder(characters=chars, config=config)
    with pytest.raises(LockInOverflowError):
        await builder.build("hello", "camp")


async def test_archive_retrieval_uses_gateway_and_store() -> None:
    calls: dict[str, Any] = {}

    class GW:
        async def embed(
            self, task: str, texts: list[str], campaign_id: str = ""
        ) -> list[list[float]]:
            calls["embed"] = (task, texts, campaign_id)
            return [[0.1, 0.2, 0.3]]

        async def estimate_tokens(self, text: str) -> int:
            return max(1, len(text) // 5)

    @dataclass
    class _Hit:
        ref: str
        scope: str
        source_kind: str
        text: str
        score: float

    class Store:
        async def vector_search(self, **kwargs: Any) -> list[_Hit]:
            calls["vector_search"] = kwargs
            return [
                _Hit(
                    ref="post:42",
                    scope="campaign-local",
                    source_kind="post",
                    text="ancient pact",
                    score=0.9,
                )
            ]

        async def keyword_search(self, **kwargs: Any) -> list[_Hit]:
            calls["keyword_search"] = kwargs
            return [
                _Hit(
                    ref="fact:1",
                    scope="campaign-local",
                    source_kind="fact",
                    text="winifred promised orchard",
                    score=0.7,
                )
            ]

    builder = _builder(gateway=GW(), state_store=Store())
    prompt = await builder.build("Tell me of the pact", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "ancient pact" in body
    assert "winifred promised orchard" in body
    # Gateway received the configured embedding task.
    assert calls["embed"][2] == "camp"
    # Sources attribution carries archive tier with refs.
    archive_sources = [s for s in prompt.sources if s.tier == ContextTier.ARCHIVE]
    assert {s.owner_id for s in archive_sources} >= {"post:42", "fact:1"}


async def test_scene_header_present_when_scene_set() -> None:
    scene = _Scene(
        title="Elysium",
        location_ref="library:settings/wod-london/locations/tower",
        mood="tense",
        present_character_refs=["library:settings/wod-london/characters/winifred"],
    )
    builder = _builder(scenes=StubScenes(scene=scene))
    prompt = await builder.build("scene begins", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Elysium" in body
    assert "library:settings/wod-london/locations/tower" in body
    assert "tense" in body


async def test_recent_posts_in_lock_in_verbatim() -> None:
    posts = [_Post(body=f"Post {i}", author_label=f"narrator:{i}") for i in range(5)]
    scenes = StubScenes(scene=_Scene(), posts=posts)
    builder = _builder(scenes=scenes)
    prompt = await builder.build("continue", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    # By default lock_in_verbatim_posts=2 → posts 3 and 4 in the lock-in.
    assert "Post 4" in body
    assert "Post 3" in body


async def test_commitments_appear_when_open() -> None:
    cont = StubContinuity(commitments=[_Commitment(text="Return the heirloom", id="c1")])
    builder = _builder(continuity=cont)
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Return the heirloom" in body
    assert any(s.kind == "commitment" for s in prompt.sources)


async def test_location_resolution_with_weather() -> None:
    location = _Location(
        setting_id="wod",
        id="tower",
        name="Tower of the Tremere",
        description="A candle-lit keep.",
        permanent_features=["stained glass"],
    )
    setting = StubSetting(
        locations={("wod", "tower"): location},
        weather=_Weather(summary="thin drizzle", kind="rain"),
    )
    scene = _Scene(location_ref="library:settings/wod/locations/tower")
    builder = _builder(setting=setting, scenes=StubScenes(scene=scene))
    prompt = await builder.build("scene", "camp")
    spotlight = next((m for m in prompt.messages if m.content.startswith("# Spotlight")), None)
    assert spotlight is not None
    assert "Tower of the Tremere" in spotlight.content
    assert "thin drizzle" in spotlight.content


async def test_source_attribution_includes_scope() -> None:
    chars = StubCharacters(
        cards={"library:settings/wod/characters/alistair": _Card(full="x")},
        active="library:settings/wod/characters/alistair",
    )
    builder = _builder(characters=chars)
    prompt = await builder.build("hi", "camp")
    pc_source = next(
        s for s in prompt.sources if s.tier == ContextTier.LOCK_IN and s.kind == "character"
    )
    assert pc_source.scope == "campaign-local"


async def test_estimate_returns_per_tier_breakdown() -> None:
    chars = StubCharacters(
        cards={"library:settings/wod/characters/alistair": _Card(full="# Alistair")},
        active="library:settings/wod/characters/alistair",
    )
    builder = _builder(characters=chars)
    est = await builder.estimate("hello", "camp")
    assert est.total_budget > 0
    assert ContextTier.LOCK_IN in est.per_tier


async def test_assembled_prompt_carries_messages_hash() -> None:
    builder = _builder()
    p1 = await builder.build("hello world", "camp")
    p2 = await builder.build("hello world", "camp")
    p3 = await builder.build("different prompt", "camp")
    assert p1.messages_hash == p2.messages_hash
    assert p1.messages_hash != p3.messages_hash


async def test_drift_corrective_injected_into_system_block() -> None:
    chars = StubCharacters(
        cards={
            "library:settings/wod/characters/alistair": _Card(
                full="# Alistair", corrective="Speak more formally."
            )
        },
        active="library:settings/wod/characters/alistair",
    )
    builder = _builder(characters=chars)
    prompt = await builder.build("hi", "camp")
    system = next(m for m in prompt.messages if m.role == "system")
    assert "Speak more formally." in system.content


async def test_lore_triggers_surface_in_archive() -> None:
    @dataclass
    class _Lore:
        id: str
        setting_id: str
        title: str
        body: str
        keywords: list[str]

    lore = _Lore(
        id="ancient-pact",
        setting_id="wod",
        title="The Ancient Pact",
        body="Sealed in blood at the founding.",
        keywords=["pact", "ancient"],
    )
    setting = StubSetting(lore=[lore])
    builder = _builder(setting=setting)
    prompt = await builder.build("Tell me of the ancient pact", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Ancient Pact" in body
    assert any(s.kind == "lore" for s in prompt.sources)


async def test_composition_snapshot_preserved() -> None:
    composition = Composition(
        settings=[SettingRef(setting_id="wod", priority=1)],
        mechanics="wod-mechanics",
    )
    builder = _builder(library=StubLibrary(composition=composition))
    prompt = await builder.build("scene", "camp")
    snap = prompt.composition_snapshot
    assert snap["mechanics"] == "wod-mechanics"
    assert snap["settings"][0]["setting_id"] == "wod"


async def test_background_chars_compressed_only() -> None:
    chars = StubCharacters(
        cards={
            "library:settings/wod/characters/winifred": _Card(
                full="full-card-winifred",
                compressed="compressed-winifred",
            )
        }
    )
    scene = _Scene()
    posts = [_Post(body="library:settings/wod/characters/winifred steps in")]
    builder = _builder(
        characters=chars,
        scenes=StubScenes(scene=scene, posts=posts),
    )
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    # The mentioned-but-absent character is compressed, not full.
    assert "compressed-winifred" in body
    assert "full-card-winifred" not in body
