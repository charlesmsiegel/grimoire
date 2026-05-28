"""Auxiliary-task suppression in the Context Builder.

When `auxiliary_task` is set on `build(...)`, the canonical tier-pack
pipeline is bypassed and the prompt is assembled from a per-task budget
plan: no mechanics, no tracker, no tool declarations, no archive tier.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from grimoire.auxiliary.types import AuxiliaryTask, TaskKind
from grimoire.context import ContextBuilderService
from grimoire.types.extraction_modes import ExtractionMode

from .test_builder import (
    StubContinuity,
    StubLibrary,
    StubWorld,
    _Card,
)


@dataclass
class _Post:
    id: str
    body: str
    author_pc_ref: str | None = None
    author_npc_ref: str | None = None
    author_label_value: str = "narrator"

    def author_label(self) -> str:
        return self.author_label_value


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
    posts: list[_Post] = field(default_factory=list)


class _AuxScenes:
    def __init__(self, scene: _Scene | None = None, posts: list[_Post] | None = None) -> None:
        self._scene = scene
        self._posts = posts or (scene.posts if scene else [])

    async def active_scene_for_campaign(self, campaign_id: str):
        return self._scene

    async def recent_posts(self, scene_id: str, n: int = 10):
        return list(self._posts[-n:])


class _AuxCharacters:
    def __init__(
        self,
        cards: dict[str, _Card] | None = None,
        active: str | None = None,
        voices: dict[str, str] | None = None,
    ) -> None:
        self._cards = cards or {}
        self._active = active
        self._voices = voices or {}

    async def active_pc(self, campaign_id: str):
        return self._active

    async def get_full_card(self, ref: str, campaign_id: str) -> str:
        return self._cards.get(ref, _Card()).full

    async def get_compressed_card(self, ref: str, campaign_id: str) -> str:
        return self._cards.get(ref, _Card()).compressed

    async def drift_corrective_context(self, ref: str, campaign_id: str) -> str:
        return self._cards.get(ref, _Card()).corrective

    async def get_voice_only(self, ref: str, campaign_id: str) -> str:
        return self._voices.get(ref, "")


def _builder(**overrides: Any) -> ContextBuilderService:
    defaults: dict[str, Any] = {
        "library": StubLibrary(),
        "characters": _AuxCharacters(),
        "world": StubWorld(),
        "scenes": _AuxScenes(scene=_Scene()),
        "continuity": StubContinuity(),
        "state_store": None,
        "gateway": None,
    }
    defaults.update(overrides)
    return ContextBuilderService(**defaults)


def _blob(prompt) -> str:
    return "\n".join(m.content for m in prompt.messages)


async def test_brainstorm_omits_pc_card_and_scene_header():
    builder = _builder(
        characters=_AuxCharacters(cards={"pc_a": _Card(full="ACTIVE_PC_CARD")}, active="pc_a"),
    )
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="ideas for next scene")
    prompt = await builder.build("ignored", "camp", auxiliary_task=task)

    blob = _blob(prompt)
    assert "ACTIVE_PC_CARD" not in blob
    assert "The Tower" not in blob  # scene header suppressed
    assert "ideas for next scene" in blob


async def test_impersonate_pc_includes_active_pc_full_card_and_scene_header():
    builder = _builder(
        characters=_AuxCharacters(cards={"pc_a": _Card(full="ACTIVE_PC_CARD_FULL")}, active="pc_a"),
    )
    task = AuxiliaryTask(kind=TaskKind.IMPERSONATE_PC, steering_hint="be bolder")
    prompt = await builder.build("ignored", "camp", auxiliary_task=task)

    blob = _blob(prompt)
    assert "ACTIVE_PC_CARD_FULL" in blob
    assert "The Tower" in blob  # scene header present
    assert "be bolder" in blob


async def test_rewrite_post_loads_original_speakers_voices():
    posts = [
        _Post(id="p_1", body="The crow lit on the wall.", author_npc_ref="npc_crow"),
        _Post(id="p_2", body="winifred shivered.", author_pc_ref="pc_florence"),
    ]
    scene = _Scene(present_character_refs=["pc_florence", "npc_crow"], posts=posts)
    chars = _AuxCharacters(
        cards={"pc_florence": _Card(full="FLORENCE_CARD")},
        active="pc_florence",
        voices={
            "npc_crow": "Voice: terse, ominous.",
            "pc_florence": "Voice: nervous, observant.",
        },
    )
    builder = _builder(characters=chars, scenes=_AuxScenes(scene=scene))

    task = AuxiliaryTask(
        kind=TaskKind.REWRITE_POST,
        target_post_id="p_1",
        edit_instruction="More menacing.",
    )
    prompt = await builder.build("ignored", "camp", auxiliary_task=task)
    blob = _blob(prompt)

    assert "Voice: terse, ominous." in blob  # original speaker
    assert "More menacing." in blob
    assert "crow lit on the wall" in blob  # original_text in prompt


async def test_continue_as_loads_target_npc_only():
    chars = _AuxCharacters(
        voices={"npc_hyde": "Voice: clipped, supercilious.", "pc_a": "Voice: warm."},
        active="pc_a",
    )
    builder = _builder(characters=chars)
    task = AuxiliaryTask(kind=TaskKind.CONTINUE_AS, target_character_ref="npc_hyde")
    prompt = await builder.build("ignored", "camp", auxiliary_task=task)
    blob = _blob(prompt)

    assert "clipped, supercilious" in blob
    # Active PC's voice anchor is NOT loaded for continue_as.
    assert "Voice: warm." not in blob


async def test_aux_task_suppresses_tool_declarations_and_tracker():
    builder = _builder()
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM, snippet="x")
    prompt = await builder.build(
        "ignored",
        "camp",
        auxiliary_task=task,
        extractor_mode=ExtractionMode.TOOL_USE,
    )
    assert prompt.tools == []
    assert "<!-- TRACKER -->" not in _blob(prompt)


async def test_translate_carries_target_language():
    builder = _builder()
    task = AuxiliaryTask(
        kind=TaskKind.TRANSLATE,
        snippet="The crow lit on the wall.",
        target_language="French",
    )
    prompt = await builder.build("ignored", "camp", auxiliary_task=task)
    blob = _blob(prompt)
    assert "French" in blob
    assert "crow lit on the wall" in blob


async def test_resolve_voice_targets_for_each_kind():
    from grimoire.auxiliary.budgets import resolve_voice_targets

    scene = _Scene(
        present_character_refs=["pc_a", "npc_b"],
        posts=[_Post(id="p_1", body="hi", author_pc_ref="pc_a", author_npc_ref=None)],
    )
    # impersonate_pc → active PC + present cast
    t = AuxiliaryTask(kind=TaskKind.IMPERSONATE_PC, extra_params={"active_pc_ref": "pc_a"})
    assert resolve_voice_targets(t, scene) == ["pc_a", "npc_b"]
    # continue_as → target char only
    t = AuxiliaryTask(kind=TaskKind.CONTINUE_AS, target_character_ref="npc_b")
    assert resolve_voice_targets(t, scene) == ["npc_b"]
    # brainstorm → none
    t = AuxiliaryTask(kind=TaskKind.BRAINSTORM)
    assert resolve_voice_targets(t, scene) == []
    # rewrite_post → original speakers
    t = AuxiliaryTask(kind=TaskKind.REWRITE_POST, target_post_id="p_1")
    assert resolve_voice_targets(t, scene) == ["pc_a"]
