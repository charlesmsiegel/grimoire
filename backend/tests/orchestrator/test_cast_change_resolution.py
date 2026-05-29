"""Orchestrator cast-change resolution helper (#464)."""

from __future__ import annotations

from types import SimpleNamespace

from grimoire.characters.service import CastRef
from grimoire.orchestrator.service import resolve_cast_changes
from grimoire.types.extraction import ExtractionResult
from grimoire.types.scene import CastChange, CastChangeProposal


class _FakeCharacters:
    def __init__(self, mapping, pcs=None):
        self._mapping = mapping  # query -> CastRef | None
        self._pcs = pcs or []  # registered campaign PC refs, as stored

    async def find_cast_ref(self, campaign_id, query):
        return self._mapping.get(query)

    async def list_pcs(self, campaign_id):
        return [SimpleNamespace(character_ref=r) for r in self._pcs]


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


async def test_emergent_shorthand_present_ref_normalized():
    # Scene stores the emergent shorthand; find_cast_ref returns canonical.
    # ENTER must be a no-op (already present), LEAVE must queue removal of the
    # exact stored shorthand ref so it actually comes out of the scene.
    canonical = "campaign:emergent/character/ghost"
    shorthand = "emergent/character/ghost"
    chars = _FakeCharacters({"ghost": CastRef(character_ref=canonical, is_pc=False, name="Ghost")})

    scenes_enter = _RecordingScenes()
    await resolve_cast_changes(
        extraction=ExtractionResult(
            cast_changes=[CastChangeProposal(character_ref="ghost", change=CastChange.ENTER)]
        ),
        scene=_Scene(present=[shorthand]),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes_enter,
    )
    assert scenes_enter.queued == []  # already present via shorthand

    scenes_leave = _RecordingScenes()
    await resolve_cast_changes(
        extraction=ExtractionResult(
            cast_changes=[CastChangeProposal(character_ref="ghost", change=CastChange.LEAVE)]
        ),
        scene=_Scene(present=[shorthand]),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes_leave,
    )
    assert scenes_leave.queued == [(shorthand, CastChange.LEAVE, False)]


async def test_known_character_queued_unknown_becomes_candidate():
    chars = _FakeCharacters(
        {
            "reyes": CastRef(
                character_ref="library:worlds/w/characters/reyes", is_pc=False, name="Reyes"
            )
        }
    )
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
    chars = _FakeCharacters(
        {"hero": CastRef(character_ref="campaign:emergent/character/hero", is_pc=True, name="Hero")}
    )
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


async def test_pc_enter_queues_registration_ref_not_canonical():
    # An emergent PC registered with the legacy shorthand ref: a detected ENTER
    # must queue that registration ref (not the canonical campaign: form) so
    # confirming it keys present_pc_refs / _pc_current_scene the same way the PC
    # subsystem and the frontend's submitted pc_ref do — otherwise the PC shows
    # as present but can't post ("no active scene") (#464).
    canonical = "campaign:emergent/character/hero"
    shorthand = "emergent/character/hero"
    chars = _FakeCharacters(
        {"hero": CastRef(character_ref=canonical, is_pc=True, name="Hero")},
        pcs=[shorthand],
    )
    scenes = _RecordingScenes()
    await resolve_cast_changes(
        extraction=ExtractionResult(
            cast_changes=[CastChangeProposal(character_ref="hero", change=CastChange.ENTER)]
        ),
        scene=_Scene(),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes,
    )
    assert scenes.queued == [(shorthand, CastChange.ENTER, True)]


async def test_noop_enter_already_present_is_dropped():
    chars = _FakeCharacters(
        {"reyes": CastRef(character_ref="ref:reyes", is_pc=False, name="Reyes")}
    )
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


async def test_noop_pc_present_only_in_pc_refs_is_dropped():
    # A PC seeded into present_pc_refs but not yet present_character_refs must
    # still count as present, so a redundant ENTER is dropped.
    chars = _FakeCharacters({"hero": CastRef(character_ref="ref:hero", is_pc=True, name="Hero")})
    scenes = _RecordingScenes()
    extraction = ExtractionResult(
        cast_changes=[CastChangeProposal(character_ref="hero", change=CastChange.ENTER)]
    )
    queued = await resolve_cast_changes(
        extraction=extraction,
        scene=_Scene(present=[], pcs=["ref:hero"]),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes,
    )
    assert queued == []
    assert scenes.queued == []


async def test_pc_leave_present_only_in_pc_refs_is_queued():
    chars = _FakeCharacters({"hero": CastRef(character_ref="ref:hero", is_pc=True, name="Hero")})
    scenes = _RecordingScenes()
    extraction = ExtractionResult(
        cast_changes=[CastChangeProposal(character_ref="hero", change=CastChange.LEAVE)]
    )
    queued = await resolve_cast_changes(
        extraction=extraction,
        scene=_Scene(present=[], pcs=["ref:hero"]),
        campaign_id="c",
        turn_id="t1",
        characters=chars,
        scenes=scenes,
    )
    assert len(queued) == 1
    assert scenes.queued == [("ref:hero", CastChange.LEAVE, True)]


async def test_noop_leave_not_present_is_dropped():
    chars = _FakeCharacters(
        {"reyes": CastRef(character_ref="ref:reyes", is_pc=False, name="Reyes")}
    )
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
