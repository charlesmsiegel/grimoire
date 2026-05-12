"""Contradiction detection and resolution tests."""

from __future__ import annotations

import pytest

from grimoire.continuity import (
    ContinuityConfig,
    ContinuityService,
    ContradictionCandidate,
    ContradictionCheckConfig,
    ContradictionJudge,
    ContradictionResolutionAction,
    ContradictionVerdict,
    Fact,
)


class ScriptedJudge(ContradictionJudge):
    """Returns the verdict in `scripts[(candidate_text, existing_text)]` or NO_CONFLICT."""

    def __init__(self, scripts: dict[tuple[str, str], ContradictionVerdict]) -> None:
        self._scripts = scripts
        self.calls: list[tuple[str, str]] = []

    async def judge(self, candidate: Fact, existing: Fact) -> ContradictionCandidate:
        key = (candidate.text, existing.text)
        self.calls.append(key)
        verdict = self._scripts.get(key, ContradictionVerdict.NO_CONFLICT)
        return ContradictionCandidate(
            existing_fact=existing,
            similarity=0.5,
            verdict=verdict,
            confidence=0.9 if verdict == ContradictionVerdict.CONFLICT else 0.1,
            rationale="scripted",
        )


async def test_check_contradictions_uses_top_k_search_and_judge(fact_factory):
    judge = ScriptedJudge(
        {
            (
                "winifred visited Sion as a child.",
                "winifred has never left Greenwich County.",
            ): ContradictionVerdict.CONFLICT
        }
    )
    svc = ContinuityService(judge=judge)
    existing = fact_factory(text="winifred has never left Greenwich County.")
    await svc.add_fact(existing, source="user")
    candidate = fact_factory(text="winifred visited Sion as a child.", post="post-99")
    report = await svc.check_contradictions(candidate)
    assert len(report.conflicts) == 1
    assert report.conflicts[0].existing_fact.text == "winifred has never left Greenwich County."
    assert judge.calls  # judge was consulted


async def test_check_contradictions_returns_empty_when_no_conflicts(fact_factory):
    svc = ContinuityService(judge=ScriptedJudge({}))
    await svc.add_fact(fact_factory(text="Unrelated fact about Sion."), source="x")
    candidate = fact_factory(text="julian likes apples.")
    report = await svc.check_contradictions(candidate)
    assert report.conflicts == []


async def test_check_contradictions_disabled_via_config(fact_factory):
    svc = ContinuityService(
        config=ContinuityConfig(contradiction_check=ContradictionCheckConfig(enabled=False)),
    )
    await svc.add_fact(fact_factory(text="x"), source="x")
    report = await svc.check_contradictions(fact_factory(text="y"))
    assert report.conflicts == []


OLD = "winifred has never left Greenwich County."
NEW = "winifred visited Sion when she was young."
CONFLICT_SCRIPT = {(NEW, OLD): ContradictionVerdict.CONFLICT}


async def test_resolve_keep_existing_drops_new(fact_factory):
    svc = ContinuityService(judge=ScriptedJudge(CONFLICT_SCRIPT))
    eid = await svc.add_fact(fact_factory(text=OLD), source="x")
    report = await svc.check_contradictions(fact_factory(text=NEW))
    assert report.conflicts, "fixture should surface a conflict"
    await svc.resolve_contradiction(
        report.id, {"action": ContradictionResolutionAction.KEEP_EXISTING.value}
    )
    facts = await svc.facts_about()
    assert {f.id for f in facts} == {eid}


async def test_resolve_replace_existing_retires_old_and_adds_new(fact_factory):
    svc = ContinuityService(judge=ScriptedJudge(CONFLICT_SCRIPT))
    eid = await svc.add_fact(fact_factory(text=OLD), source="x")
    candidate = fact_factory(fact_id="new-fact", text=NEW)
    report = await svc.check_contradictions(candidate)
    await svc.resolve_contradiction(
        report.id,
        {
            "action": ContradictionResolutionAction.REPLACE_EXISTING.value,
            "in_post": "post-x",
        },
    )
    old = await svc.get_fact(eid)
    assert old.retired
    new = await svc.get_fact("new-fact")
    assert eid in new.contradicts


async def test_resolve_both_true_persists_new(fact_factory):
    svc = ContinuityService(judge=ScriptedJudge(CONFLICT_SCRIPT))
    await svc.add_fact(fact_factory(text=OLD), source="x")
    candidate = fact_factory(fact_id="new-id", text=NEW)
    report = await svc.check_contradictions(candidate)
    await svc.resolve_contradiction(
        report.id, {"action": ContradictionResolutionAction.BOTH_TRUE.value}
    )
    persisted = await svc.get_fact("new-id")
    assert persisted.text == NEW


async def test_resolve_edit_existing_applies_patch(fact_factory):
    svc = ContinuityService(judge=ScriptedJudge(CONFLICT_SCRIPT))
    eid = await svc.add_fact(fact_factory(text=OLD), source="x")
    report = await svc.check_contradictions(fact_factory(text=NEW))
    await svc.resolve_contradiction(
        report.id,
        {
            "action": ContradictionResolutionAction.EDIT_EXISTING.value,
            "patch": {"text": "edited text"},
        },
    )
    edited = await svc.get_fact(eid)
    assert edited.text == "edited text"


async def test_resolving_already_resolved_raises(fact_factory):
    svc = ContinuityService(judge=ScriptedJudge(CONFLICT_SCRIPT))
    await svc.add_fact(fact_factory(text=OLD), source="x")
    report = await svc.check_contradictions(fact_factory(text=NEW))
    await svc.resolve_contradiction(
        report.id, {"action": ContradictionResolutionAction.KEEP_EXISTING.value}
    )
    with pytest.raises(ValueError):
        await svc.resolve_contradiction(
            report.id,
            {"action": ContradictionResolutionAction.KEEP_EXISTING.value},
        )
