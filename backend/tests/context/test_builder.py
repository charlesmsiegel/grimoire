"""Tests for ContextBuilderService.

The Context Builder consumes a fan of domain modules — Characters, World,
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
from grimoire.types.composition import Composition, WorldRef
from grimoire.types.mechanics import MechanicsResult, Roll, RollResult
from grimoire.types.state import ContextTier

# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #


@dataclass
class _StyleGuide:
    body: str = ""


@dataclass
class _WorldMeta:
    id: str
    name: str = ""


class StubLibrary:
    def __init__(
        self,
        composition: Composition | None = None,
        style_guide_body: str | None = None,
        worlds: dict[str, _WorldMeta] | None = None,
    ) -> None:
        self._composition = composition or Composition()
        self._style_guide_body = style_guide_body
        self._worlds = worlds or {}

    async def get_composition(self, campaign_id: str) -> Composition:
        return self._composition

    async def get_world(self, world_id: str) -> _WorldMeta:
        if world_id not in self._worlds:
            raise KeyError(world_id)
        return self._worlds[world_id]

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

    async def list_pcs(self, campaign_id: str) -> list[Any]:
        if self._active:
            return [type("_PC", (), {"character_ref": self._active})()]
        return []

    async def get_full_card(self, ref: str, campaign_id: str) -> str:
        return self._cards.get(ref, _Card()).full

    async def get_compressed_card(self, ref: str, campaign_id: str) -> str:
        return self._cards.get(ref, _Card()).compressed

    async def drift_corrective_context(self, ref: str, campaign_id: str) -> str:
        return self._cards.get(ref, _Card()).corrective


@dataclass
class _Location:
    world_id: str
    id: str
    name: str
    description: str = ""
    body: str = ""
    permanent_features: list[str] = field(default_factory=list)


@dataclass
class _Weather:
    summary: str
    kind: str = "clear"


class StubWorld:
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

    async def get_location(self, world_id: str, location_id: str) -> _Location:
        return self._locations[(world_id, location_id)]

    async def weather_for(
        self,
        world_id: str,
        location_id: str,
        when: Any,
        campaign_id: str,
        *,
        branch_id: str | None = None,
    ) -> _Weather | None:
        return self._weather

    async def adjacent_locations(
        self, world_id: str, location_id: str, campaign_id: str
    ) -> list[_Location]:
        return self._adjacent

    async def lore_for_post(self, text: str, campaign_id: str) -> list[Any]:
        return [
            lore for lore in self._lore if any(kw in text for kw in getattr(lore, "keywords", []))
        ]

    async def season_for(self, when: Any, campaign_id: str) -> Any:
        return None

    async def holiday_at(self, when: Any, campaign_id: str) -> Any:
        return None


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
    present_pc_refs: list[str] = field(default_factory=list)
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
class _Status:
    """Mimics CommitmentStatus's `.value` shape for stub commitments."""

    value: str


@dataclass
class _Commitment:
    text: str
    id: str = ""
    due_by: Any = None
    status: _Status | None = None


@dataclass
class _Fact:
    text: str
    id: str = ""


class StubContinuity:
    def __init__(
        self,
        commitments: list[_Commitment] | None = None,
        facts: list[_Fact] | None = None,
        overdue: list[_Commitment] | None = None,
        stale: list[_Commitment] | None = None,
        config: Any | None = None,
    ) -> None:
        self._commitments = commitments or []
        self._facts = facts or []
        self._overdue = overdue or []
        self._stale = stale or []
        # Mirror both registry (.config) and bare-service (._config) shapes so
        # ContextBuilder can fish the active ContinuityConfig from either.
        self.config = config
        self._config = config
        self.overdue_calls: list[Any] = []
        self.stale_calls: list[Any] = []

    async def open_commitments(self, limit: int = 20) -> list[_Commitment]:
        return list(self._commitments)

    async def facts_about(self, limit: int = 50) -> list[_Fact]:
        return list(self._facts)

    async def overdue_commitments(self, as_of: Any) -> list[_Commitment]:
        self.overdue_calls.append(as_of)
        return list(self._overdue)

    async def stale_commitments(self, threshold: Any) -> list[_Commitment]:
        self.stale_calls.append(threshold)
        return list(self._stale)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


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
        worlds=[WorldRef(world_id="wod-london", priority=1)],
    )
    library = StubLibrary(
        composition=composition,
        worlds={"wod-london": _WorldMeta(id="wod-london", name="WoD London")},
    )
    builder = _builder(library=library)
    prompt = await builder.build("scene begins", "camp")
    system = next(m for m in prompt.messages if m.role == "system")
    assert "Present-tense prose." in system.content
    assert "No minors in sexual contexts." in system.content
    assert "WoD London" in system.content


async def test_active_pc_card_in_lock_in() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/alistair": _Card(full="# Alistair\nElder Tremere.")},
        active="library:worlds/wod/characters/alistair",
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
            "library:worlds/wod/characters/alistair": _Card(full="# Alistair\nFull card"),
            "library:worlds/wod/characters/winifred": _Card(full="# winifred\nFull card"),
        },
        active="library:worlds/wod/characters/alistair",
    )
    scene = _Scene(
        present_character_refs=[
            "library:worlds/wod/characters/alistair",
            "library:worlds/wod/characters/winifred",
        ],
        present_pc_refs=["library:worlds/wod/characters/alistair"],
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
        cards={"library:worlds/wod/characters/alistair": _Card(full="x" * 4000)},
        active="library:worlds/wod/characters/alistair",
    )
    builder = _builder(characters=chars, config=config)
    with pytest.raises(LockInOverflowError):
        await builder.build("hello", "camp")


async def test_archive_retrieval_uses_gateway_and_store() -> None:
    calls: dict[str, Any] = {}

    class GW:
        async def embed(
            self, task: str, texts: list[str], campaign_id: str = "", turn_id: str | None = None
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
        location_ref="library:worlds/wod-london/locations/tower",
        mood="tense",
        present_character_refs=["library:worlds/wod-london/characters/winifred"],
    )
    builder = _builder(scenes=StubScenes(scene=scene))
    prompt = await builder.build("scene begins", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Elysium" in body
    assert "library:worlds/wod-london/locations/tower" in body
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


async def test_surface_overdue_in_context_true_tags_and_merges_overdue() -> None:
    """When surface_overdue_in_context=True, overdue_commitments(as_of) is queried
    and additional time-based overdue items are merged with [OVERDUE] tags."""
    from grimoire.continuity.config import ContinuityConfig
    from grimoire.continuity.types import InGameTime

    config = ContinuityConfig(surface_overdue_in_context=True)
    # c1 is OPEN-status, c2 has status=overdue, c3 is only in overdue_commitments(as_of).
    cont = StubContinuity(
        commitments=[
            _Commitment(text="Return the heirloom", id="c1"),
            _Commitment(text="Pay the smuggler", id="c2", status=_Status("overdue")),
        ],
        overdue=[_Commitment(text="Find the witness", id="c3", status=_Status("open"))],
        config=config,
    )
    scene = _Scene(in_game_start=InGameTime(day_count=100))
    builder = _builder(continuity=cont, scenes=StubScenes(scene=scene))
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Return the heirloom" in body
    assert "Pay the smuggler" in body
    assert "Find the witness" in body
    # Status-overdue and time-based overdue both carry the marker; the OPEN one doesn't.
    assert "Pay the smuggler" in body and "[OVERDUE]" in body
    assert "Find the witness [OVERDUE]" in body
    assert "Return the heirloom [OVERDUE]" not in body
    # `as_of` reached the service.
    assert cont.overdue_calls == [InGameTime(day_count=100)]


async def test_surface_overdue_in_context_false_hides_overdue() -> None:
    """When surface_overdue_in_context=False, status-overdue items are dropped and
    overdue_commitments(as_of) is not queried."""
    from grimoire.continuity.config import ContinuityConfig
    from grimoire.continuity.types import InGameTime

    config = ContinuityConfig(surface_overdue_in_context=False)
    cont = StubContinuity(
        commitments=[
            _Commitment(text="Return the heirloom", id="c1"),
            _Commitment(text="Pay the smuggler", id="c2", status=_Status("overdue")),
        ],
        overdue=[_Commitment(text="Find the witness", id="c3", status=_Status("open"))],
        config=config,
    )
    scene = _Scene(in_game_start=InGameTime(day_count=100))
    builder = _builder(continuity=cont, scenes=StubScenes(scene=scene))
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Return the heirloom" in body
    assert "Pay the smuggler" not in body
    assert "Find the witness" not in body
    assert "[OVERDUE]" not in body
    assert cont.overdue_calls == []


async def test_surface_stale_in_context_true_surfaces_stale() -> None:
    """When surface_stale_in_context=True, stale_commitments(threshold) is queried
    and items appear with a [STALE] marker."""
    from grimoire.continuity.config import ContinuityConfig
    from grimoire.continuity.types import Duration

    config = ContinuityConfig(surface_stale_in_context=True)
    cont = StubContinuity(
        commitments=[_Commitment(text="Return the heirloom", id="c1")],
        stale=[_Commitment(text="Confront the patriarch", id="cs1", status=_Status("stale"))],
        config=config,
    )
    builder = _builder(continuity=cont)
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Confront the patriarch [STALE]" in body
    # Threshold from config.commitment_stale_threshold flowed through.
    assert cont.stale_calls == [Duration.months(6)]


async def test_surface_stale_in_context_false_does_not_query_stale() -> None:
    """When surface_stale_in_context=False (default), stale_commitments is not called."""
    from grimoire.continuity.config import ContinuityConfig

    config = ContinuityConfig(surface_stale_in_context=False)
    cont = StubContinuity(
        commitments=[_Commitment(text="Return the heirloom", id="c1")],
        stale=[_Commitment(text="Confront the patriarch", id="cs1", status=_Status("stale"))],
        config=config,
    )
    builder = _builder(continuity=cont)
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Confront the patriarch" not in body
    assert "[STALE]" not in body
    assert cont.stale_calls == []


async def test_location_resolution_with_weather() -> None:
    location = _Location(
        world_id="wod",
        id="tower",
        name="Tower of the Tremere",
        description="A candle-lit keep.",
        permanent_features=["stained glass"],
    )
    world = StubWorld(
        locations={("wod", "tower"): location},
        weather=_Weather(summary="thin drizzle", kind="rain"),
    )
    scene = _Scene(location_ref="library:worlds/wod/locations/tower")
    builder = _builder(world=world, scenes=StubScenes(scene=scene))
    prompt = await builder.build("scene", "camp")
    spotlight = next((m for m in prompt.messages if m.content.startswith("# Spotlight")), None)
    assert spotlight is not None
    assert "Tower of the Tremere" in spotlight.content
    assert "thin drizzle" in spotlight.content


async def test_source_attribution_includes_scope() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/alistair": _Card(full="x")},
        active="library:worlds/wod/characters/alistair",
    )
    builder = _builder(characters=chars)
    prompt = await builder.build("hi", "camp")
    pc_source = next(
        s for s in prompt.sources if s.tier == ContextTier.LOCK_IN and s.kind == "character"
    )
    assert pc_source.scope == "campaign-local"


async def test_estimate_returns_per_tier_breakdown() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/alistair": _Card(full="# Alistair")},
        active="library:worlds/wod/characters/alistair",
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
            "library:worlds/wod/characters/alistair": _Card(
                full="# Alistair", corrective="Speak more formally."
            )
        },
        active="library:worlds/wod/characters/alistair",
    )
    builder = _builder(characters=chars)
    prompt = await builder.build("hi", "camp")
    system = next(m for m in prompt.messages if m.role == "system")
    assert "Speak more formally." in system.content


async def test_lore_triggers_surface_in_archive() -> None:
    @dataclass
    class _Lore:
        id: str
        world_id: str
        title: str
        body: str
        keywords: list[str]

    lore = _Lore(
        id="ancient-pact",
        world_id="wod",
        title="The Ancient Pact",
        body="Sealed in blood at the founding.",
        keywords=["pact", "ancient"],
    )
    world = StubWorld(lore=[lore])
    builder = _builder(world=world)
    prompt = await builder.build("Tell me of the ancient pact", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Ancient Pact" in body
    assert any(s.kind == "lore" for s in prompt.sources)


async def test_composition_snapshot_preserved() -> None:
    composition = Composition(
        worlds=[WorldRef(world_id="wod", priority=1)],
        mechanics="wod-mechanics",
    )
    builder = _builder(library=StubLibrary(composition=composition))
    prompt = await builder.build("scene", "camp")
    snap = prompt.composition_snapshot
    assert snap["mechanics"] == "wod-mechanics"
    assert snap["worlds"][0]["world_id"] == "wod"


async def test_background_chars_compressed_only() -> None:
    chars = StubCharacters(
        cards={
            "library:worlds/wod/characters/winifred": _Card(
                full="full-card-winifred",
                compressed="compressed-winifred",
            )
        }
    )
    scene = _Scene()
    posts = [_Post(body="library:worlds/wod/characters/winifred steps in")]
    builder = _builder(
        characters=chars,
        scenes=StubScenes(scene=scene, posts=posts),
    )
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    # The mentioned-but-absent character is compressed, not full.
    assert "compressed-winifred" in body
    assert "full-card-winifred" not in body


# --------------------------------------------------------------------------- #
# Spec context-builder-remaining tests
# --------------------------------------------------------------------------- #


class StubCharactersWithRecommend(StubCharacters):
    """Stub Characters service that honours :meth:`recommend_tiers`.

    The handful of new builder pathways (tier recommendations, voice
    anchors, relationship deltas, PC enumeration) live behind optional
    attributes so the basic :class:`StubCharacters` from above can stay
    minimal. Tests that exercise those paths opt in via this subclass.
    """

    def __init__(
        self,
        cards: dict[str, _Card] | None = None,
        active: str | None = None,
        tier_recs: dict[str, ContextTier] | None = None,
        voice: dict[str, str] | None = None,
        relationship_history: dict[tuple[str, str], list[dict]] | None = None,
        pcs: list[str] | None = None,
    ) -> None:
        super().__init__(cards=cards, active=active)
        self._tier_recs = tier_recs or {}
        self._voice = voice or {}
        self._rel = relationship_history or {}
        self._pcs = pcs or []
        self.recommend_calls: list[dict[str, Any]] = []

    async def recommend_tiers(
        self,
        scene: Any,
        campaign_id: str,
        *,
        recent_posts: list[Any] | None = None,
        commitments_targeting_pcs: set[str] | None = None,
    ) -> dict[str, ContextTier]:
        self.recommend_calls.append(
            {
                "scene": scene,
                "campaign_id": campaign_id,
                "recent_posts": list(recent_posts or []),
                "commitments_targeting_pcs": set(commitments_targeting_pcs or set()),
            }
        )
        return dict(self._tier_recs)

    async def get_voice_only(self, ref: str, campaign_id: str) -> str:
        return self._voice.get(ref, "")

    async def get_relationship_history(
        self,
        from_ref: str,
        to_ref: str,
        campaign_id: str,
        *,
        branch_id: str | None = None,
    ) -> list[dict]:
        return list(self._rel.get((from_ref, to_ref), []))

    async def list_pcs(self, campaign_id: str) -> list[Any]:
        @dataclass
        class _PC:
            character_ref: str

        return [_PC(character_ref=r) for r in self._pcs]


async def test_recommend_tiers_promotes_background_character() -> None:
    chars = StubCharactersWithRecommend(
        cards={
            "library:worlds/wod/characters/marcus": _Card(
                compressed="compressed-marcus",
            ),
        },
        tier_recs={
            # Not in present_character_refs, but Characters says BACKGROUND
            # (e.g. mentioned in recent posts).
            "library:worlds/wod/characters/marcus": ContextTier.BACKGROUND,
        },
    )
    scenes = StubScenes(scene=_Scene())
    builder = _builder(characters=chars, scenes=scenes)
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "compressed-marcus" in body
    assert chars.recommend_calls, "recommend_tiers should be called"


async def test_recommend_tiers_receives_pc_commitments() -> None:
    @dataclass
    class _Commit:
        text: str = ""
        from_id: str | None = None
        to_id: str | None = None
        id: str = ""
        due_by: Any = None

    cont = StubContinuity(
        commitments=[_Commit(text="owe pc favour", from_id="npc:alistair", to_id="pc:elena")]
    )
    chars = StubCharactersWithRecommend(
        active="pc:elena",
        cards={"pc:elena": _Card(full="# Elena")},
        pcs=["pc:elena"],
    )
    builder = _builder(
        characters=chars,
        scenes=StubScenes(scene=_Scene()),
        continuity=cont,
    )
    await builder.build("hi", "camp")
    call = chars.recommend_calls[-1]
    assert "npc:alistair" in call["commitments_targeting_pcs"]


async def test_user_tier_pin_forces_spotlight() -> None:
    chars = StubCharactersWithRecommend(
        cards={"library:worlds/wod/characters/pinned": _Card(full="full-pinned")},
        tier_recs={
            # Pin forces SPOTLIGHT even though they aren't present.
            "library:worlds/wod/characters/pinned": ContextTier.SPOTLIGHT,
        },
    )
    builder = _builder(characters=chars, scenes=StubScenes(scene=_Scene()))
    prompt = await builder.build("scene", "camp")
    spotlight = [m for m in prompt.messages if m.content.startswith("# Spotlight")]
    assert spotlight
    assert "full-pinned" in spotlight[0].content


async def test_voice_anchor_emitted_for_present_character() -> None:
    chars = StubCharactersWithRecommend(
        cards={"library:worlds/wod/characters/winifred": _Card(full="card")},
        voice={"library:worlds/wod/characters/winifred": "Speak with weight."},
    )
    scene = _Scene(present_character_refs=["library:worlds/wod/characters/winifred"])
    builder = _builder(characters=chars, scenes=StubScenes(scene=scene))
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "# Voice anchor" in body
    assert "Speak with weight." in body


async def test_voice_anchor_can_be_disabled() -> None:
    chars = StubCharactersWithRecommend(
        cards={"library:worlds/wod/characters/winifred": _Card(full="card")},
        voice={"library:worlds/wod/characters/winifred": "Speak with weight."},
    )
    scene = _Scene(present_character_refs=["library:worlds/wod/characters/winifred"])
    config = ContextBuilderConfig(enable_voice_anchor=False)
    builder = _builder(characters=chars, scenes=StubScenes(scene=scene), config=config)
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "# Voice anchor" not in body


async def test_recent_dialogue_per_speaker_emitted() -> None:
    @dataclass
    class _PostWithAuthor:
        body: str
        author_pc_ref: str | None = None
        author_npc_ref: str | None = None
        author_label: str = "narrator"

    ref = "library:worlds/wod/characters/winifred"
    chars = StubCharactersWithRecommend(
        cards={ref: _Card(full="card")},
    )
    scene = _Scene(present_character_refs=[ref])
    posts = [
        _PostWithAuthor(body="I shall not yield.", author_npc_ref=ref),
        _PostWithAuthor(body="The night is long.", author_label="narrator"),
        _PostWithAuthor(body="Bring the lantern.", author_npc_ref=ref),
    ]
    builder = _builder(characters=chars, scenes=StubScenes(scene=scene, posts=posts))
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Recent dialogue" in body
    assert "Bring the lantern." in body
    assert "I shall not yield." in body
    # Narrator post should not leak in.
    assert "The night is long." in body  # appears as a recent post, not as dialogue
    dialogue_block = next(m.content for m in prompt.messages if "Recent dialogue" in m.content)
    assert "The night is long." not in dialogue_block


async def test_cast_header_disambiguates_duplicate_names() -> None:
    a_ref = "library:worlds/world-a/characters/margaret"
    b_ref = "library:worlds/world-b/characters/margaret"
    chars = StubCharactersWithRecommend(
        cards={a_ref: _Card(full="# Margaret\nA"), b_ref: _Card(full="# Margaret\nB")},
    )
    scene = _Scene(present_character_refs=[a_ref, b_ref])
    builder = _builder(characters=chars, scenes=StubScenes(scene=scene))
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "[world:world-a]" in body
    assert "[world:world-b]" in body


async def test_recent_facts_use_compact_render() -> None:
    facts = [_Fact(text=f"fact #{i}", id=f"f-{i}") for i in range(20)]
    cont = StubContinuity(facts=facts)
    builder = _builder(continuity=cont)
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Recent facts:" in body
    assert "- fact #0" in body
    # Old per-fact "Fact:" verbose prefix should be gone.
    assert "Fact: fact #0" not in body


async def test_relationship_deltas_in_background() -> None:
    pc = "pc:elena"
    other = "library:worlds/wod/characters/winifred"
    chars = StubCharactersWithRecommend(
        active=pc,
        cards={pc: _Card(full="# Elena"), other: _Card(full="# winifred")},
        relationship_history={
            (pc, other): [
                {"summary": "orchard promise", "delta": {"trust": 2}},
            ]
        },
    )
    scene = _Scene(present_character_refs=[pc, other], present_pc_refs=[pc])
    builder = _builder(characters=chars, scenes=StubScenes(scene=scene))
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Relationship deltas" in body
    assert "trust +2" in body
    assert "orchard promise" in body


async def test_scene_refs_force_archive_inclusion() -> None:
    @dataclass
    class _PastScene:
        id: str = "scene-7"
        title: str = "First meeting"
        slug: str = "first-meeting"
        location_ref: str | None = None
        final_summary: str = "winifred and Elena first met."
        running_summary: str = ""

    class _Scenes(StubScenes):
        def __init__(self) -> None:
            super().__init__(scene=_Scene())

        async def get_scene(self, scene_id: str) -> Any:
            return _PastScene()

    builder = _builder(scenes=_Scenes())
    prompt = await builder.build("Remember scene:scene-7?", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "scene:scene-7" in body
    assert "First meeting" in body
    assert "winifred and Elena first met." in body


async def test_priority_hints_passed_to_store() -> None:
    captured: dict[str, Any] = {}

    @dataclass
    class _Hit:
        ref: str
        scope: str
        source_kind: str
        text: str
        score: float

    class GW:
        async def embed(self, task: str, texts: list[str], **kwargs: Any) -> list[list[float]]:
            return [[0.1]]

    class Store:
        async def vector_search(self, **kwargs: Any) -> list[_Hit]:
            captured["vector"] = kwargs
            return []

        async def keyword_search(self, **kwargs: Any) -> list[_Hit]:
            captured["keyword"] = kwargs
            return []

    composition = Composition(
        worlds=[
            WorldRef(world_id="wod-a", priority=1),
            WorldRef(world_id="wod-b", priority=5),
        ]
    )
    builder = _builder(
        library=StubLibrary(composition=composition),
        gateway=GW(),
        state_store=Store(),
    )
    await builder.build("query", "camp")
    assert captured["vector"]["priority_hints"] == {"wod-a": 1, "wod-b": 5}
    assert captured["keyword"]["priority_hints"] == {"wod-a": 1, "wod-b": 5}


async def test_priority_hints_disabled_when_store_lacks_kwarg() -> None:
    @dataclass
    class _Hit:
        ref: str
        scope: str
        source_kind: str
        text: str
        score: float

    captured: dict[str, Any] = {}

    class GW:
        async def embed(self, task: str, texts: list[str], **kwargs: Any) -> list[list[float]]:
            return [[0.1]]

    class OldStore:
        async def vector_search(
            self,
            *,
            query_vector: list[float],
            campaign_id: str,
            include_library: bool = True,
            top_k: int = 8,
        ) -> list[_Hit]:
            captured["vector"] = {"top_k": top_k}
            return []

        async def keyword_search(
            self,
            *,
            query: str,
            campaign_id: str,
            kinds: Any,
            top_k: int = 5,
        ) -> list[_Hit]:
            captured["keyword"] = {"top_k": top_k}
            return []

    composition = Composition(worlds=[WorldRef(world_id="wod", priority=1)])
    builder = _builder(
        library=StubLibrary(composition=composition),
        gateway=GW(),
        state_store=OldStore(),
    )
    # Should NOT raise even though the store rejects ``priority_hints``.
    prompt = await builder.build("query", "camp")
    assert prompt is not None
    assert captured["vector"] == {"top_k": 8}
    assert captured["keyword"] == {"top_k": 5}


async def test_faction_state_rendered_into_background() -> None:
    @dataclass
    class _Faction:
        asset_id: str
        name: str = ""

    @dataclass
    class _FactionState:
        current_focus: str = ""
        public_perception: str = ""
        goals: list = field(default_factory=list)
        resources: dict = field(default_factory=dict)

    class _World(StubWorld):
        def __init__(self) -> None:
            super().__init__()

        async def list_factions(self, world_id: str) -> list[_Faction]:
            return [_Faction(asset_id="court")]

        async def faction_state(
            self, ref: str, campaign_id: str, *, branch_id: str | None = None
        ) -> _FactionState:
            return _FactionState(
                current_focus="press the orchard claim",
                public_perception="cautious",
                goals=[{"text": "secure the orchard"}],
            )

    composition = Composition(worlds=[WorldRef(world_id="wod", priority=1)])
    builder = _builder(
        library=StubLibrary(composition=composition),
        world=_World(),
        scenes=StubScenes(scene=_Scene()),
    )
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Faction state" in body
    assert "press the orchard claim" in body


async def test_calendar_item_uses_time_engine() -> None:
    from datetime import datetime

    @dataclass
    class _Moment:
        moment: datetime = field(default_factory=lambda: datetime(2026, 5, 18))

    @dataclass
    class _Event:
        title: str = "Festival of the Orchard"

    class _TE:
        async def current(self, campaign_id: str, *, branch_id: str | None = None) -> _Moment:
            return _Moment()

        async def upcoming_events(
            self, campaign_id: str, *, branch_id: str | None = None
        ) -> list[_Event]:
            return [_Event()]

    class _World(StubWorld):
        async def season_for(self, when: Any, campaign_id: str) -> Any:
            @dataclass
            class _Season:
                name: str = "late spring"

            return _Season()

        async def holiday_at(self, when: Any, campaign_id: str) -> None:
            return None

    builder = _builder(
        world=_World(),
        scenes=StubScenes(scene=_Scene()),
        time_engine=_TE(),
    )
    prompt = await builder.build("scene", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Calendar" in body
    assert "late spring" in body
    assert "Festival of the Orchard" in body


async def test_cache_module_round_trip() -> None:
    from grimoire.context.cache import ContextBuilderCache, make_cache_key

    cache = ContextBuilderCache(max_entries=3)
    builder = _builder()
    prompt = await builder.build("hi", "camp")
    key = make_cache_key(
        campaign_id="camp",
        player_input="hi",
        composition_hash="abc",
        scene_id="s1",
        branch_id="main",
        pc_ref=None,
    )
    cache.put(key, prompt)
    assert cache.get(key) is prompt
    # Eviction order: oldest first.
    for i in range(4):
        cache.put(
            make_cache_key(
                campaign_id="camp",
                player_input=f"input-{i}",
                composition_hash="abc",
                scene_id="s1",
                branch_id="main",
                pc_ref=None,
            ),
            prompt,
        )
    assert cache.get(key) is None
    assert len(cache) == 3


# --------------------------------------------------------------------------- #
# PC-absent scene tests
# --------------------------------------------------------------------------- #


async def test_pc_absent_scene_skips_active_pc_card() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/alistair": _Card(full="# Alistair\nElder Tremere.")},
        active="library:worlds/wod/characters/alistair",
    )
    scene = _Scene(
        present_character_refs=["npc-winifred"],
        present_pc_refs=[],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(characters=chars, scenes=scenes)
    prompt = await builder.build("scene begins", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Elder Tremere" not in body
    assert not any(
        s.tier == ContextTier.LOCK_IN and s.kind == "character" for s in prompt.sources
    )


async def test_pc_absent_scene_includes_director_instruction() -> None:
    scene = _Scene(
        present_character_refs=["npc-winifred"],
        present_pc_refs=[],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(scenes=scenes)
    prompt = await builder.build("the NPCs argue", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "NPC-only scene" in body


async def test_pc_present_scene_includes_agency_instruction() -> None:
    scene = _Scene(
        present_character_refs=["pc-alistair", "npc-winifred"],
        present_pc_refs=["pc-alistair"],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(scenes=scenes)
    prompt = await builder.build("I bow", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Never write the player character" in body


async def test_scene_header_labels_npc_only() -> None:
    scene = _Scene(
        title="Secret Meeting",
        present_character_refs=["npc-winifred", "npc-drake"],
        present_pc_refs=[],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(scenes=scenes)
    prompt = await builder.build("NPCs talk", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "(NPC-only)" in body


async def test_pc_absent_scene_adds_absent_pc_background_cards() -> None:
    chars = StubCharactersWithRecommend(
        cards={
            "pc-alistair": _Card(full="# Alistair\nFull", compressed="Alistair (compressed)"),
            "npc-winifred": _Card(full="# winifred\nFull"),
        },
        active="pc-alistair",
        pcs=["pc-alistair"],
    )
    scene = _Scene(
        present_character_refs=["npc-winifred"],
        present_pc_refs=[],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(characters=chars, scenes=scenes)
    prompt = await builder.build("the NPCs talk", "camp")
    body = "\n".join(m.content for m in prompt.messages)
    assert "Alistair (compressed)" in body
