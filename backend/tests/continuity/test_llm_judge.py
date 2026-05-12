"""LLMContradictionJudge — parse structured verdicts from an LLM response."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from grimoire.continuity import (
    ContinuityService,
    ContradictionVerdict,
    FactSource,
    LLMContradictionJudge,
)
from tests.continuity.conftest import make_fact


@dataclass
class _Response:
    text: str


class _FakeGateway:
    """Records calls and returns scripted responses."""

    def __init__(self, replies: list[str]) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, object]] = []

    async def complete(self, task: str, request) -> _Response:
        self.calls.append((task, request))
        if not self.replies:
            raise AssertionError("FakeGateway exhausted scripted replies")
        return _Response(text=self.replies.pop(0))


def _make_request(system: str, user: str) -> dict:
    return {"system": system, "user": user}


async def test_judge_parses_conflict_verdict() -> None:
    gateway = _FakeGateway(
        ['{"verdict": "conflict", "confidence": 0.85, "rationale": "explicit clash"}']
    )
    judge = LLMContradictionJudge(gateway, _make_request)
    candidate = make_fact(fact_id="cand", text="winifred visited Sion as a child.")
    existing = make_fact(fact_id="ex", text="winifred has never left Greenwich.")

    result = await judge.judge(candidate, existing)
    assert result.verdict is ContradictionVerdict.CONFLICT
    assert result.confidence == pytest.approx(0.85)
    assert "explicit clash" in result.rationale
    assert gateway.calls[0][0] == "drift_check"


async def test_judge_parses_no_conflict() -> None:
    gateway = _FakeGateway(
        ['```json\n{"verdict": "no_conflict", "confidence": 0.7, "rationale": "ok"}\n```']
    )
    judge = LLMContradictionJudge(gateway, _make_request)
    result = await judge.judge(
        make_fact(text="winifred likes apples."),
        make_fact(text="winifred is tall."),
    )
    assert result.verdict is ContradictionVerdict.NO_CONFLICT


async def test_judge_unparseable_response_falls_back_to_uncertain() -> None:
    gateway = _FakeGateway(["I'm sorry, I can't decide."])
    judge = LLMContradictionJudge(gateway, _make_request)
    result = await judge.judge(make_fact(text="a"), make_fact(text="b"))
    assert result.verdict is ContradictionVerdict.UNCERTAIN
    assert result.confidence == 0.0


async def test_judge_gateway_exception_returns_uncertain() -> None:
    class _Boom:
        async def complete(self, task, request):
            raise RuntimeError("provider down")

    judge = LLMContradictionJudge(_Boom(), _make_request)
    result = await judge.judge(make_fact(text="a"), make_fact(text="b"))
    assert result.verdict is ContradictionVerdict.UNCERTAIN
    assert "provider down" in result.rationale or "RuntimeError" in result.rationale


async def test_judge_clamps_confidence_to_unit_interval() -> None:
    gateway = _FakeGateway(['{"verdict": "conflict", "confidence": 1.7, "rationale": "x"}'])
    judge = LLMContradictionJudge(gateway, _make_request)
    result = await judge.judge(make_fact(text="a"), make_fact(text="b"))
    assert result.confidence == 1.0


async def test_judge_defers_on_distinct_testimonies() -> None:
    """Conflicting testimony from two different characters is not auto-flagged."""
    gateway = _FakeGateway([])  # would error if called
    judge = LLMContradictionJudge(gateway, _make_request)
    cand = make_fact(
        text="I saw the lantern shine red.",
        source=FactSource.CHARACTER_TESTIMONY,
        speaker_id="julian",
    )
    existing = make_fact(
        text="I saw the lantern shine blue.",
        source=FactSource.CHARACTER_TESTIMONY,
        speaker_id="winifred",
    )
    result = await judge.judge(cand, existing)
    assert result.verdict is ContradictionVerdict.UNCERTAIN
    assert gateway.calls == []  # short-circuited before LLM call


async def test_judge_integrates_with_continuity_service() -> None:
    """End-to-end via ContinuityService."""
    gateway = _FakeGateway(
        ['{"verdict": "conflict", "confidence": 0.9, "rationale": "locations disagree"}']
    )
    judge = LLMContradictionJudge(gateway, _make_request)
    service = ContinuityService(judge=judge)

    existing = make_fact(text="winifred has never left Greenwich.")
    await service.add_fact(existing, source="extractor")

    candidate = make_fact(
        fact_id="cand",
        text="winifred has never left Greenwich.",  # same text → keyword overlap
    )
    report = await service.check_contradictions(candidate)
    assert report.conflicts, "expected the judge to surface a conflict"
    assert report.conflicts[0].verdict is ContradictionVerdict.CONFLICT
