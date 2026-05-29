"""Orchestrator cast-change resolution helper (#464)."""

from __future__ import annotations

from grimoire.characters.service import CastRef
from grimoire.orchestrator.service import resolve_cast_changes
from grimoire.types.extraction import ExtractionResult
from grimoire.types.scene import CastChange, CastChangeProposal


class _FakeCharacters:
    def __init__(self, mapping):
        self._mapping = mapping  # query -> CastRef | None

    async def find_cast_ref(self, campaign_id, query):
        return self._mapping.get(query)


class _RecordingScenes:
    def __init__(self):
        self.queued = []

    async def queue_cast_change(
        self, scene_id, *, character_ref, change, is_pc, evidence="", confidence=0.0, turn_id=None
    ):
        self.queued.append((character_ref, change, is_pc))
        return f"cc-{len(self.queued)}"


class _Scene:
    def __init__(self, present=None, pcs=None):
        self.id = "s1"
        self.present_character_refs = present or []
        self.present_pc_refs = pcs or []


async def test_known_character_queued_unknown_becomes_candidate():
    chars = _FakeCharacters({"reyes": CastRef("library:worlds/w/characters/reyes", False, "Reyes")})
    scenes = _RecordingScenes()
    extraction = ExtractionResult(
        cast_changes=[
            CastChangeProposal(character_ref="reyes", change=CastChange.ENTER, confidence=0.9),
            CastChangeProposal(character_ref="stranger", change=CastChange.ENTER, confidence=0.6),
        ]
    )
    queued = await resolve_cast_changes(
        extraction=extraction,
        scene=_Scene(),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes,
    )
    assert scenes.queued == [("library:worlds/w/characters/reyes", CastChange.ENTER, False)]
    assert len(queued) == 1
    assert any(c.proposed_name == "stranger" for c in extraction.candidates)


async def test_pc_arrival_flagged_is_pc():
    chars = _FakeCharacters({"hero": CastRef("campaign:emergent/character/hero", True, "Hero")})
    scenes = _RecordingScenes()
    extraction = ExtractionResult(
        cast_changes=[
            CastChangeProposal(character_ref="hero", change=CastChange.ENTER, confidence=0.9)
        ]
    )
    await resolve_cast_changes(
        extraction=extraction,
        scene=_Scene(),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes,
    )
    assert scenes.queued[0][2] is True


async def test_noop_enter_already_present_is_dropped():
    chars = _FakeCharacters({"reyes": CastRef("ref:reyes", False, "Reyes")})
    scenes = _RecordingScenes()
    extraction = ExtractionResult(
        cast_changes=[
            CastChangeProposal(character_ref="reyes", change=CastChange.ENTER, confidence=0.9)
        ]
    )
    queued = await resolve_cast_changes(
        extraction=extraction,
        scene=_Scene(present=["ref:reyes"]),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes,
    )
    assert queued == []
    assert scenes.queued == []


async def test_noop_leave_not_present_is_dropped():
    chars = _FakeCharacters({"reyes": CastRef("ref:reyes", False, "Reyes")})
    scenes = _RecordingScenes()
    extraction = ExtractionResult(
        cast_changes=[
            CastChangeProposal(character_ref="reyes", change=CastChange.LEAVE, confidence=0.9)
        ]
    )
    queued = await resolve_cast_changes(
        extraction=extraction,
        scene=_Scene(present=[]),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes,
    )
    assert queued == []


async def test_unknown_name_deduped_against_existing_candidate():
    from grimoire.types.common import EntityKind
    from grimoire.types.extraction import EntityCandidate

    chars = _FakeCharacters({})
    scenes = _RecordingScenes()
    extraction = ExtractionResult(
        candidates=[
            EntityCandidate(
                kind=EntityKind.CHARACTER, proposed_id="stranger", proposed_name="stranger"
            )
        ],
        cast_changes=[CastChangeProposal(character_ref="stranger", change=CastChange.ENTER)],
    )
    await resolve_cast_changes(
        extraction=extraction,
        scene=_Scene(),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes,
    )
    assert sum(1 for c in extraction.candidates if c.proposed_name == "stranger") == 1
