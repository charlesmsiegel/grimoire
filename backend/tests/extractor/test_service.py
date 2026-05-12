"""Integration tests over `ExtractorService`."""

from __future__ import annotations

import pytest

from grimoire.extractor import ExtractorConfig, ExtractorService
from grimoire.extractor.routing import route_deltas
from grimoire.types.common import ValidationResult
from grimoire.types.extraction import FlagLevel
from grimoire.types.scene import Scene
from grimoire.types.state import CharacterState, DeltaKind, StateSnapshot

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
