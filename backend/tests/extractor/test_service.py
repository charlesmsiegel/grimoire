"""Integration tests over `ExtractorService`."""

from __future__ import annotations

import pytest

from grimoire.extractor import ExtractorConfig, ExtractorService
from grimoire.extractor.routing import route_deltas
from grimoire.extractor.service import _delta_is_about
from grimoire.types.common import Scope, ValidationResult
from grimoire.types.extraction import FlagLevel
from grimoire.types.scene import Scene
from grimoire.types.state import CharacterState, DeltaKind, StateDelta, StateSnapshot

from .conftest import FakeContradictionChecker, FakeGateway, FakeMechanics


@pytest.mark.asyncio
async def test_extract_runs_all_strategies_and_merges(scene: Scene, snapshot: StateSnapshot):
    gateway = FakeGateway(
        queue=[
            {
                "facts": [
                    {
                        "text": "winifred wrote to her uncle",
                        "confidence": 0.9,
                        "about": {"character_ids": ["winifred"]},
                    }
                ],
                "time_advances": [{"delta": "PT2H", "confidence": 0.95, "evidence": "two hours"}],
            }
        ]
    )
    service = ExtractorService(gateway=gateway, config=ExtractorConfig(max_new_entities_per_turn=5))
    result = await service.extract(
        "winifred wrote to her uncle. Two hours passed. Margaux brought the tea.",
        scene,
        "camp-1",
        snapshot,
    )
    assert set(result.extraction_strategies_run) == {
        "rule_based",
        "structured_llm",
        "heuristic_flags",
    }
    # rule_based and structured_llm both yield a time-advance for "two hours";
    # the merge collapses them to a single delta.
    times = [d for d in result.deltas if d.kind == DeltaKind.TIME_ADVANCE]
    assert len(times) == 1
    # Fact from the LLM strategy.
    assert any(d.kind == DeltaKind.FACT_ADD for d in result.deltas)
    # Margaux was introduced — heuristics should propose her as a candidate.
    assert any(c.proposed_name == "Margaux" for c in result.candidates)


@pytest.mark.asyncio
async def test_extract_respects_disabled_llm_strategy(scene: Scene, snapshot: StateSnapshot):
    config = ExtractorConfig(parallel_strategies=("rule_based", "heuristic_flags"))
    service = ExtractorService(config=config)
    result = await service.extract("winifred picked up the silver ring.", scene, "camp-1", snapshot)
    assert "structured_llm" not in result.extraction_strategies_run
    assert any(d.kind == DeltaKind.INVENTORY_CHANGE for d in result.deltas)


@pytest.mark.asyncio
async def test_extract_without_gateway_emits_info_flag(scene: Scene, snapshot: StateSnapshot):
    service = ExtractorService(config=ExtractorConfig())
    result = await service.extract("just some narration", scene, "camp-1", snapshot)
    assert any(f.code == "llm_strategy_disabled" for f in result.flags)


@pytest.mark.asyncio
async def test_extract_clamps_player_authority_on_other_subjects(
    scene: Scene, snapshot: StateSnapshot
):
    gateway = FakeGateway(
        queue=[
            {
                "facts": [
                    {
                        "text": "winifred is sad",
                        "confidence": 0.95,
                        "about": {"character_ids": ["winifred"]},
                    },
                    {
                        "text": "julian feels guilty",
                        "confidence": 0.95,
                        "about": {"character_ids": ["julian"]},
                    },
                ]
            }
        ]
    )
    service = ExtractorService(
        gateway=gateway,
        config=ExtractorConfig(player_other_subject_confidence_cap=0.5),
    )
    result = await service.extract_from_user_text(
        "I write a long monologue about winifred and myself.",
        scene,
        "camp-1",
        snapshot=snapshot,
        player_pc_ref="julian",
    )
    by_subject = {
        tuple(d.after["about"].get("character_ids", [])): d.confidence
        for d in result.deltas
        if d.kind == DeltaKind.FACT_ADD
    }
    # Player's own PC keeps full confidence; the other subject is clamped.
    assert by_subject.get(("julian",)) == 0.95
    assert by_subject.get(("winifred",)) == 0.5


@pytest.mark.parametrize("field", ["character_id", "actor_ref", "from", "subject"])
def test_delta_is_about_rejects_empty_string_subject(field: str):
    """Regression for #31: empty subject must not match via `pc_ref.endswith("")`."""
    delta = StateDelta(
        kind=DeltaKind.FACT_ADD,
        target_scope=Scope.CAMPAIGN_SQLITE,
        target_id="fact:x",
        after={field: "", "about": {"character_ids": ["winifred"]}},
        confidence=0.95,
    )
    assert _delta_is_about(delta, pc_ref="julian") is False


@pytest.mark.asyncio
async def test_extract_player_authority_does_not_match_by_suffix(
    scene: Scene, snapshot: StateSnapshot
):
    # Regression for #32: pc_ref="julian" must NOT be classified as "about the
    # player's PC" when an unrelated subject merely shares a suffix (e.g.
    # "her", "crasher"). Without the separator guard, both deltas would slip
    # past the player-authority cap.
    gateway = FakeGateway(
        queue=[
            {
                "character_updates": [
                    {
                        "character_id": "her",
                        "field": "mood",
                        "after": "thoughtful",
                        "confidence": 0.95,
                        "evidence": "she felt thoughtful",
                    },
                    {
                        "character_id": "crasher",
                        "field": "mood",
                        "after": "smug",
                        "confidence": 0.95,
                        "evidence": "the crasher looked smug",
                    },
                ]
            }
        ]
    )
    service = ExtractorService(
        gateway=gateway,
        config=ExtractorConfig(player_other_subject_confidence_cap=0.5),
    )
    result = await service.extract_from_user_text(
        "I narrate something involving her and a party crasher.",
        scene,
        "camp-1",
        snapshot=snapshot,
        player_pc_ref="julian",
    )
    by_subject = {
        d.after["character_id"]: d.confidence
        for d in result.deltas
        if d.kind == DeltaKind.CHARACTER_STATE_UPDATE
    }
    # Both subjects are unrelated to "julian" and should hit the non-PC cap.
    assert by_subject.get("her") == 0.5
    assert by_subject.get("crasher") == 0.5


@pytest.mark.asyncio
async def test_extract_player_authority_allows_namespaced_pc_ref(
    scene: Scene, snapshot: StateSnapshot
):
    # A namespaced ref like "character:julian" should still be recognized as
    # the player's own PC when pc_ref is the bare "julian" form (and vice
    # versa). The `:` separator distinguishes this from accidental suffixes.
    gateway = FakeGateway(
        queue=[
            {
                "character_updates": [
                    {
                        "character_id": "character:julian",
                        "field": "mood",
                        "after": "resolved",
                        "confidence": 0.95,
                        "evidence": "he steadied himself",
                    },
                ]
            }
        ]
    )
    service = ExtractorService(
        gateway=gateway,
        config=ExtractorConfig(player_other_subject_confidence_cap=0.5),
    )
    result = await service.extract_from_user_text(
        "I steady myself.",
        scene,
        "camp-1",
        snapshot=snapshot,
        player_pc_ref="julian",
    )
    update = next(d for d in result.deltas if d.kind == DeltaKind.CHARACTER_STATE_UPDATE)
    assert update.confidence == 0.95


@pytest.mark.asyncio
async def test_extract_downgrades_facts_on_contradiction(scene: Scene, snapshot: StateSnapshot):
    gateway = FakeGateway(
        queue=[
            {
                "facts": [
                    {
                        "text": "winifred is in Paris",
                        "confidence": 0.95,
                        "about": {"character_ids": ["winifred"]},
                    }
                ]
            }
        ]
    )
    checker = FakeContradictionChecker(
        conflicts_for={"winifred is in Paris": ["fact_201: winifred is in Sion"]}
    )
    service = ExtractorService(
        gateway=gateway,
        contradictions=checker,
        config=ExtractorConfig(contradiction_confidence_penalty=0.3),
    )
    result = await service.extract("winifred appeared in Paris.", scene, "camp-1", snapshot)
    fact = next(d for d in result.deltas if d.kind == DeltaKind.FACT_ADD)
    assert pytest.approx(fact.confidence, rel=1e-6) == 0.65
    routing = route_deltas(result.deltas, config=ExtractorConfig())
    assert fact in routing.review
    assert any(f.level == FlagLevel.CONTRADICTION for f in result.flags)


@pytest.mark.asyncio
async def test_extract_applies_speaker_authority_penalty_for_testimony(
    scene: Scene, snapshot: StateSnapshot
):
    # A fact whose `speaker_id` is a character (not the GM narrator) gets
    # the testimony penalty subtracted from its confidence.
    gateway = FakeGateway(
        queue=[
            {
                "facts": [
                    {
                        "text": "winifred claims she has the deed",
                        "speaker_id": "winifred",
                        "confidence": 0.9,
                        "about": {"character_ids": ["winifred"]},
                    }
                ]
            }
        ]
    )
    service = ExtractorService(
        gateway=gateway,
        config=ExtractorConfig(testimony_confidence_penalty=0.2),
    )
    result = await service.extract(
        '"I have the deed," winifred said.', scene, "camp-1", snapshot
    )
    fact = next(d for d in result.deltas if d.kind == DeltaKind.FACT_ADD)
    assert fact.confidence == pytest.approx(0.7, rel=1e-6)


@pytest.mark.asyncio
async def test_extract_no_penalty_when_narrator_speaks(
    scene: Scene, snapshot: StateSnapshot
):
    # `speaker_id` of None means GM-voice narration — no penalty.
    gateway = FakeGateway(
        queue=[
            {
                "facts": [
                    {
                        "text": "The deed sits in winifred's pocket",
                        "speaker_id": None,
                        "confidence": 0.9,
                        "about": {"character_ids": ["winifred"]},
                    }
                ]
            }
        ]
    )
    service = ExtractorService(
        gateway=gateway,
        config=ExtractorConfig(testimony_confidence_penalty=0.2),
    )
    result = await service.extract("The deed bulged from her pocket.", scene, "camp-1", snapshot)
    fact = next(d for d in result.deltas if d.kind == DeltaKind.FACT_ADD)
    assert fact.confidence == pytest.approx(0.9, rel=1e-6)


@pytest.mark.asyncio
async def test_extract_speaker_authority_penalty_floors_at_zero(
    scene: Scene, snapshot: StateSnapshot
):
    gateway = FakeGateway(
        queue=[
            {
                "facts": [
                    {
                        "text": "wild claim",
                        "speaker_id": "winifred",
                        "confidence": 0.05,
                        "about": {"character_ids": ["winifred"]},
                    }
                ]
            }
        ]
    )
    service = ExtractorService(
        gateway=gateway,
        config=ExtractorConfig(testimony_confidence_penalty=0.2),
    )
    result = await service.extract("winifred boasted improbably.", scene, "camp-1", snapshot)
    fact = next(d for d in result.deltas if d.kind == DeltaKind.FACT_ADD)
    assert fact.confidence == pytest.approx(0.0, abs=1e-9)


@pytest.mark.asyncio
async def test_extract_flags_unresolved_commitment_id_and_routes_to_review(
    scene: Scene,
):
    # The LLM hallucinates `c_4521` — there are no open commitments to match.
    snapshot = StateSnapshot(
        campaign_id="camp-1",
        branch_id="main",
        scene_id="scene-1",
        open_commitments=[{"id": "c_001", "text": "winifred owes julian the silver ring."}],
    )
    gateway = FakeGateway(
        queue=[
            {
                "commitment_resolutions": [
                    {"commitment_id": "c_4521", "outcome": "paid", "confidence": 0.95}
                ]
            }
        ]
    )
    service = ExtractorService(
        gateway=gateway,
        config=ExtractorConfig(contradiction_confidence_penalty=0.25),
    )
    result = await service.extract(
        "winifred handed back what she owed.", scene, "camp-1", snapshot
    )
    resolved = next(d for d in result.deltas if d.kind == DeltaKind.COMMITMENT_RESOLVE)
    # Penalised from 0.95 -> 0.70, which falls inside the review band [0.6, 0.85).
    assert resolved.confidence == pytest.approx(0.70, rel=1e-6)
    routing = route_deltas(result.deltas, config=ExtractorConfig())
    assert resolved in routing.review
    assert any(
        f.level == FlagLevel.CONTRADICTION and f.code == "unresolved_commitment_reference"
        for f in result.flags
    )


@pytest.mark.asyncio
async def test_extract_leaves_matching_commitment_id_alone(scene: Scene):
    snapshot = StateSnapshot(
        campaign_id="camp-1",
        branch_id="main",
        scene_id="scene-1",
        open_commitments=[{"id": "c_001", "text": "winifred owes julian the silver ring."}],
    )
    gateway = FakeGateway(
        queue=[
            {
                "commitment_resolutions": [
                    {"commitment_id": "c_001", "outcome": "paid", "confidence": 0.9}
                ]
            }
        ]
    )
    service = ExtractorService(gateway=gateway, config=ExtractorConfig())
    result = await service.extract(
        "winifred handed back the silver ring.", scene, "camp-1", snapshot
    )
    resolved = next(d for d in result.deltas if d.kind == DeltaKind.COMMITMENT_RESOLVE)
    assert resolved.confidence == pytest.approx(0.9, rel=1e-6)
    assert not any(
        f.code == "unresolved_commitment_reference" for f in result.flags
    )


@pytest.mark.asyncio
async def test_extract_skips_commitment_resolution_check_without_snapshot(scene: Scene):
    # If we have no snapshot to consult, we can't tell — emit the delta verbatim
    # and let the State Store reject at apply-time.
    gateway = FakeGateway(
        queue=[
            {
                "commitment_resolutions": [
                    {"commitment_id": "c_4521", "outcome": "paid", "confidence": 0.9}
                ]
            }
        ]
    )
    service = ExtractorService(gateway=gateway, config=ExtractorConfig())
    result = await service.extract_from_user_text(
        "winifred handed it back.",
        scene,
        "camp-1",
        snapshot=None,
    )
    # We can't tell without a snapshot, so the resolution check is skipped —
    # confidence may still be clamped by other player-text rules, but the
    # unresolved-commitment flag must NOT fire.
    assert any(d.kind == DeltaKind.COMMITMENT_RESOLVE for d in result.deltas)
    assert not any(
        f.code == "unresolved_commitment_reference" for f in result.flags
    )


@pytest.mark.asyncio
async def test_extract_invokes_mechanics_validator(scene: Scene, snapshot: StateSnapshot):
    gateway = FakeGateway(
        queue=[
            {
                "mechanical_events": [
                    {
                        "kind": "wound",
                        "character_id": "julian",
                        "amount": "heavy",
                        "confidence": 0.9,
                    }
                ]
            }
        ]
    )
    mechanics = FakeMechanics(results=[ValidationResult(valid=False, errors=["no power consumed"])])
    service = ExtractorService(
        gateway=gateway,
        mechanics=mechanics,
        config=ExtractorConfig(contradiction_confidence_penalty=0.3),
    )
    result = await service.extract(
        "julian took heavy damage in the brawl.", scene, "camp-1", snapshot
    )
    assert mechanics.seen, "expected mechanics.validate_narrated_event to be called"
    mech_delta = next(d for d in result.deltas if d.kind == DeltaKind.MECHANICAL_EVENT)
    assert mech_delta.confidence == pytest.approx(0.6, rel=1e-6)
    assert any(f.code == "mechanics_rejected" for f in result.flags)


@pytest.mark.asyncio
async def test_extract_drops_below_review_threshold_via_routing(
    scene: Scene, snapshot: StateSnapshot
):
    gateway = FakeGateway(
        queue=[
            {
                "facts": [
                    {
                        "text": "tentative",
                        "confidence": 0.4,
                        "about": {"character_ids": ["winifred"]},
                    }
                ]
            }
        ]
    )
    service = ExtractorService(gateway=gateway, config=ExtractorConfig())
    result = await service.extract("she lied that...", scene, "camp-1", snapshot)
    routing = route_deltas(result.deltas, config=ExtractorConfig())
    assert not routing.auto_apply
    assert not routing.review
    assert len(routing.dropped) == 1


@pytest.mark.asyncio
async def test_extract_records_timeout(monkeypatch, scene: Scene, snapshot: StateSnapshot):
    import asyncio as _asyncio

    async def slow_complete(*args, **kwargs):
        await _asyncio.sleep(10)
        raise AssertionError("unreachable")

    gateway = FakeGateway()
    gateway.complete = slow_complete  # type: ignore[assignment]

    service = ExtractorService(gateway=gateway, config=ExtractorConfig(timeout_seconds=0.01))
    result = await service.extract("text", scene, "camp-1", snapshot)
    assert any(f.code == "extraction_timeout" for f in result.flags)
    assert result.deltas == []


@pytest.mark.asyncio
async def test_extract_includes_known_chars_in_snapshot(
    scene: Scene,
):
    snapshot = StateSnapshot(
        campaign_id="camp-1",
        branch_id="main",
        scene_id="scene-1",
        character_states=[
            CharacterState(
                character_ref="Margaux",
                campaign_id="camp-1",
                branch_id="main",
            )
        ],
    )
    config = ExtractorConfig(parallel_strategies=("heuristic_flags",))
    service = ExtractorService(config=config)
    result = await service.extract(
        "Margaux brought the tea. Margaux smiled.", scene, "camp-1", snapshot
    )
    # Margaux is in snapshot, so no candidate for her.
    assert not any(c.proposed_name == "Margaux" for c in result.candidates)
