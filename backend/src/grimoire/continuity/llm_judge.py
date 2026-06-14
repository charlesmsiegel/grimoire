"""LLM-backed contradiction judgment.

The judge takes a candidate fact and an existing fact, asks a small
model whether they contradict, and parses the structured response into a
:class:`ContradictionCandidate`. Used by `ContinuityService` instead of
the always-uncertain :class:`StubContradictionJudge` once an LLM Gateway
is wired up.
"""

from __future__ import annotations

import logging
from typing import Protocol

from grimoire.continuity.protocols import ContradictionJudge
from grimoire.continuity.types import (
    ContradictionCandidate,
    ContradictionVerdict,
    Fact,
    FactSource,
)
from grimoire.templates import render as render_template
from grimoire.types.common import TurnId
from grimoire.util import extract_json_object

logger = logging.getLogger(__name__)


class JudgeLLM(Protocol):
    """Minimal seam over the LLM Gateway's ``complete`` call.

    A real implementation accepts a `CompletionRequest`-shaped object and
    returns something with a ``.text`` attribute. The seam keeps this
    module independent of the gateway's pydantic types.
    """

    async def complete(self, task: str, request, *, turn_id: TurnId | None = None) -> object: ...


class LLMContradictionJudge(ContradictionJudge):
    """LLM-backed implementation of :class:`ContradictionJudge`.

    Parameters
    ----------
    gateway:
        Anything implementing ``complete(task, request) -> response``
        where ``response.text`` is a string. The real `LLMGatewayService`
        satisfies this.
    request_factory:
        Callable taking ``(system, user)`` strings and returning a
        gateway-shaped request object. Kept abstract so this module
        doesn't import pydantic models from the gateway.
    task:
        LLM Gateway task name (route) to use. Defaults to ``"drift_check"``
        which the spec earmarks as a small/cheap model.
    """

    def __init__(
        self,
        gateway: JudgeLLM,
        request_factory,
        *,
        task: str = "drift_check",
    ) -> None:
        self._gateway = gateway
        self._make_request = request_factory
        self._task = task

    async def judge(
        self,
        candidate: Fact,
        existing: Fact,
        *,
        turn_id: TurnId | None = None,
    ) -> ContradictionCandidate:
        # Two facts established by the same character speaking in-fiction
        # aren't comparable on the same "is it true" axis — testimony is
        # subjective. Don't flag them.
        if (
            candidate.source is FactSource.CHARACTER_TESTIMONY
            and existing.source is FactSource.CHARACTER_TESTIMONY
            and candidate.speaker_id != existing.speaker_id
        ):
            return ContradictionCandidate(
                existing_fact=existing,
                similarity=0.0,
                verdict=ContradictionVerdict.UNCERTAIN,
                confidence=0.0,
                rationale="distinct testimonies — judgement deferred",
            )

        user_prompt = render_template(
            "continuity_judge_user",
            existing_post=existing.established_in_post or "?",
            existing_text=_clip(existing.text),
            candidate_post=candidate.established_in_post or "?",
            candidate_text=_clip(candidate.text),
        )
        system_prompt = render_template("continuity_judge_system")
        request = self._make_request(system_prompt, user_prompt)
        try:
            response = await self._gateway.complete(self._task, request, turn_id=turn_id)
        except Exception as exc:
            logger.warning("contradiction judge LLM call failed: %s", exc)
            return ContradictionCandidate(
                existing_fact=existing,
                similarity=0.0,
                verdict=ContradictionVerdict.UNCERTAIN,
                confidence=0.0,
                rationale=f"judge unavailable: {type(exc).__name__}",
            )

        raw = getattr(response, "text", None)
        if not isinstance(raw, str):
            return _uncertain(existing, "empty judge response")

        parsed = extract_json_object(raw)
        if parsed is None:
            return _uncertain(existing, "unparseable judge response")

        verdict_raw = str(parsed.get("verdict") or "uncertain").lower()
        try:
            verdict = ContradictionVerdict(verdict_raw)
        except ValueError:
            verdict = ContradictionVerdict.UNCERTAIN
        confidence_raw = parsed.get("confidence", 0.0)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.0
        rationale = str(parsed.get("rationale") or "")[:280]

        return ContradictionCandidate(
            existing_fact=existing,
            similarity=0.0,
            verdict=verdict,
            confidence=confidence,
            rationale=rationale,
        )


def _uncertain(existing: Fact, rationale: str) -> ContradictionCandidate:
    return ContradictionCandidate(
        existing_fact=existing,
        similarity=0.0,
        verdict=ContradictionVerdict.UNCERTAIN,
        confidence=0.0,
        rationale=rationale,
    )


def _clip(text: str, limit: int = 500) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


__all__ = ["JudgeLLM", "LLMContradictionJudge"]
