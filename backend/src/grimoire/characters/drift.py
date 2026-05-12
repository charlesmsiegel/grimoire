"""Voice-drift detection helpers.

Drift detection compares a character's recent dialogue against the canonical
voice anchor. The default :class:`HeuristicDriftChecker` does a cheap
text-similarity pass so the service can compute a drift score without an LLM
in the loop. Real deployments inject an :class:`LLMDriftChecker` that calls
the gateway's ``drift_check`` task.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from grimoire.types.characters import Character, DriftReport
from grimoire.types.scene import Post


@dataclass(frozen=True)
class DriftInput:
    """What a drift checker is asked to evaluate."""

    character: Character
    recent_posts: list[Post]
    window: int


class DriftChecker(Protocol):
    """Pluggable drift evaluator. Defaults to the heuristic implementation."""

    async def evaluate(self, payload: DriftInput) -> DriftReport: ...


_TOKEN_RE = re.compile(r"[a-z']+")


def _tokens(text: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) > 2}


class HeuristicDriftChecker:
    """Default checker: Jaccard distance between sample vocabulary and recent posts.

    Detects forbidden words (from ``donts``) appearing in recent dialogue and
    rewards overlap with sample lines / ``dos``. Cheap, deterministic, and
    surprisingly informative for catching obvious drift.
    """

    def __init__(self, *, drift_threshold: float = 0.4) -> None:
        self.drift_threshold = drift_threshold

    async def evaluate(self, payload: DriftInput) -> DriftReport:
        voice = payload.character.voice
        sample_text = " ".join(voice.samples + voice.dos + [voice.summary]).strip()
        recent_text = "\n".join(
            p.body for p in payload.recent_posts if p.author_npc_ref == payload.character.id
        )
        if not recent_text.strip():
            # No screen time → no drift, no evidence.
            return DriftReport(
                character_ref=payload.character.id,
                window=payload.window,
                drift_score=0.0,
                evidence=[],
                corrective_context="",
            )

        sample_vocab = _tokens(sample_text)
        recent_vocab = _tokens(recent_text)

        if not sample_vocab:
            overlap_score = 0.5  # no anchor to compare against
        else:
            intersection = sample_vocab & recent_vocab
            union = sample_vocab | recent_vocab
            jaccard = len(intersection) / len(union) if union else 0.0
            overlap_score = max(0.0, 1.0 - jaccard * 4.0)  # boost since vocab is sparse

        evidence: list[str] = []
        for forbidden in voice.donts:
            if forbidden.strip() and forbidden.lower() in recent_text.lower():
                evidence.append(f"recent post contains forbidden phrase {forbidden!r}")
        drift_score = min(1.0, overlap_score + 0.25 * len(evidence))
        drift_score = round(drift_score, 4)
        corrective = ""
        if drift_score >= self.drift_threshold:
            corrective = _corrective_text(payload.character, evidence)
        return DriftReport(
            character_ref=payload.character.id,
            window=payload.window,
            drift_score=drift_score,
            evidence=evidence,
            corrective_context=corrective,
        )


def _corrective_text(character: Character, evidence: list[str]) -> str:
    voice = character.voice
    pieces: list[str] = [f"Voice reminder for {character.name}:"]
    if voice.summary:
        pieces.append(voice.summary.strip())
    if voice.dos:
        pieces.append("Must: " + "; ".join(voice.dos))
    if voice.donts:
        pieces.append("Avoid: " + "; ".join(voice.donts))
    if voice.samples:
        pieces.append("Canonical: " + " | ".join(f'"{s}"' for s in voice.samples[:3]))
    if evidence:
        pieces.append("Detected: " + "; ".join(evidence))
    return "\n".join(pieces)


LLMCallable = Callable[[Character, list[Post], int], Awaitable[DriftReport]]


class CallableDriftChecker:
    """Adapter wrapping any async callable into the :class:`DriftChecker` protocol.

    Useful for tests and for injecting an LLM-backed drift check without
    declaring a class.
    """

    def __init__(self, fn: LLMCallable) -> None:
        self._fn = fn

    async def evaluate(self, payload: DriftInput) -> DriftReport:
        return await self._fn(payload.character, payload.recent_posts, payload.window)
