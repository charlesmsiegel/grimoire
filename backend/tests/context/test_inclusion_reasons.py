"""Tests that ContextBuilder emits inclusion reasons on every source."""

from __future__ import annotations

from typing import Any

from grimoire.context import ContextBuilderService
from grimoire.types.inclusion_reasons import InclusionReason

from .test_builder import (
    StubCharacters,
    StubContinuity,
    StubLibrary,
    StubScenes,
    StubWorld,
    _Card,
    _Commitment,
    _Post,
    _Scene,
)


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


async def test_active_pc_card_carries_pc_card_reason() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/alistair": _Card(full="# Alistair\nElder Tremere.")},
        active="library:worlds/wod/characters/alistair",
    )
    builder = _builder(characters=chars)
    prompt = await builder.build("hello", "camp")
    pc_sources = [s for s in prompt.sources if InclusionReason.PC_CARD in s.inclusion_reasons]
    assert len(pc_sources) == 1
    assert pc_sources[0].source_id != ""


async def test_present_character_carries_present_in_scene_reason() -> None:
    chars = StubCharacters(
        cards={
            "library:worlds/wod/characters/winifred": _Card(full="# winifred"),
        },
    )
    scene = _Scene(
        present_character_refs=["library:worlds/wod/characters/winifred"],
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(characters=chars, scenes=scenes)
    prompt = await builder.build("hello", "camp")
    winifred = next(
        s
        for s in prompt.sources
        if s.kind == "character" and s.owner_id == "library:worlds/wod/characters/winifred"
    )
    assert InclusionReason.PRESENT_IN_SCENE in winifred.inclusion_reasons


async def test_open_commitment_to_pc_reason_on_commitments_block() -> None:
    commits = [_Commitment(text="Visit Henry by dawn", id="cmt_1")]
    continuity = StubContinuity(commitments=commits)
    builder = _builder(continuity=continuity)
    prompt = await builder.build("hello", "camp")
    cmt_source = next(s for s in prompt.sources if s.kind == "commitment")
    assert InclusionReason.COMMITMENT_OPEN_TO_PC in cmt_source.inclusion_reasons


async def test_scene_anchor_on_running_summary() -> None:
    scene = _Scene(
        present_character_refs=[],
        running_summary="winifred stood in the rain.",
    )
    scenes = StubScenes(scene=scene)
    builder = _builder(scenes=scenes)
    prompt = await builder.build("hello", "camp")
    summary_src = next(s for s in prompt.sources if s.summary == "running summary")
    assert InclusionReason.SCENE_ANCHOR in summary_src.inclusion_reasons


async def test_sources_have_stable_ids() -> None:
    chars = StubCharacters(
        cards={"library:worlds/wod/characters/alistair": _Card(full="# Alistair")},
        active="library:worlds/wod/characters/alistair",
    )
    b1 = _builder(characters=chars)
    b2 = _builder(characters=chars)
    p1 = await b1.build("hello", "camp")
    p2 = await b2.build("hello", "camp")
    pc1 = next(s for s in p1.sources if InclusionReason.PC_CARD in s.inclusion_reasons)
    pc2 = next(s for s in p2.sources if InclusionReason.PC_CARD in s.inclusion_reasons)
    assert pc1.source_id == pc2.source_id
    assert pc1.source_id.startswith("src_")


async def test_compose_multi_reason_for_present_with_commitment() -> None:
    """winifred is present in scene AND owes a commitment to the active PC."""
    pc_ref = "campaign:pc_alistair"
    npc_ref = "library:worlds/wod/characters/winifred"
    chars = StubCharacters(
        cards={
            pc_ref: _Card(full="# Alistair"),
            npc_ref: _Card(full="# winifred", compressed="# winifred (compact)"),
        },
        active=pc_ref,
    )

    # Patch list_pcs onto the stub so commitments_targeting_pcs computes.
    async def list_pcs(campaign_id: str) -> list[Any]:
        class _E:
            character_ref = pc_ref

        return [_E()]

    chars.list_pcs = list_pcs  # type: ignore[attr-defined]
    scene = _Scene(present_character_refs=[npc_ref])
    scenes = StubScenes(scene=scene)
    commit = _Commitment(text="Owe Alistair a favor", id="cmt_1")
    commit.from_id = npc_ref  # type: ignore[attr-defined]
    commit.to_id = pc_ref  # type: ignore[attr-defined]
    continuity = StubContinuity(commitments=[commit])
    builder = _builder(characters=chars, scenes=scenes, continuity=continuity)
    prompt = await builder.build("hello", "camp")
    winifred = next(
        s for s in prompt.sources if s.kind == "character" and s.owner_id == npc_ref
    )
    assert InclusionReason.PRESENT_IN_SCENE in winifred.inclusion_reasons
    assert InclusionReason.COMMITMENT_OPEN_TO_PC in winifred.inclusion_reasons


async def test_mentioned_in_recent_posts_reason() -> None:
    npc_ref = "library:worlds/wod/characters/henry"
    chars = StubCharacters(
        cards={npc_ref: _Card(full="# Henry", compressed="# Henry (compact)")},
    )
    posts = [_Post(body=f"They spoke of {npc_ref} that night.")]
    scene = _Scene(present_character_refs=[])
    scenes = StubScenes(scene=scene, posts=posts)
    builder = _builder(characters=chars, scenes=scenes)
    prompt = await builder.build("hello", "camp")
    henry = next(
        (s for s in prompt.sources if s.kind == "character" and s.owner_id == npc_ref),
        None,
    )
    assert henry is not None
    assert InclusionReason.MENTIONED_IN_RECENT_POSTS in henry.inclusion_reasons
