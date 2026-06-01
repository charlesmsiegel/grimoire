"""In-memory reference implementations of the Continuity persistence seams.

These are used by tests and as a default until the State Store (task #8)
provides backed implementations. They are intentionally simple and
single-process.
"""

from __future__ import annotations

import asyncio
import math
import re
from collections.abc import Iterable

from grimoire.continuity.protocols import (
    ContinuityStore,
    ContradictionJudge,
    FactSearchIndex,
)
from grimoire.continuity.types import (
    Commitment,
    CommitmentId,
    CommitmentStatus,
    ContradictionCandidate,
    ContradictionReport,
    ContradictionReportId,
    ContradictionVerdict,
    Fact,
    FactId,
    KnowledgeEntry,
)
from grimoire.types.common import TurnId

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str, *, min_len: int = 1, case_insensitive: bool = True) -> set[str]:
    raw = _WORD_RE.findall(text)
    if case_insensitive:
        raw = [t.lower() for t in raw]
    return {t for t in raw if len(t) >= min_len}


class InMemoryContinuityStore(ContinuityStore):
    """Thread-safe (asyncio-safe) in-memory store."""

    def __init__(self) -> None:
        self._facts: dict[FactId, Fact] = {}
        self._commitments: dict[CommitmentId, Commitment] = {}
        self._knowledge: dict[tuple[str, FactId], KnowledgeEntry] = {}
        self._contradictions: dict[ContradictionReportId, ContradictionReport] = {}
        self._lock = asyncio.Lock()

    async def put_fact(self, fact: Fact) -> None:
        async with self._lock:
            self._facts[fact.id] = fact

    async def get_fact(self, fact_id: FactId) -> Fact | None:
        return self._facts.get(fact_id)

    async def list_facts(self, *, include_retired: bool = False) -> list[Fact]:
        return [f for f in self._facts.values() if include_retired or not f.retired]

    async def put_commitment(self, commitment: Commitment) -> None:
        async with self._lock:
            self._commitments[commitment.id] = commitment

    async def get_commitment(self, cid: CommitmentId) -> Commitment | None:
        return self._commitments.get(cid)

    async def list_commitments(
        self, *, statuses: Iterable[CommitmentStatus] | None = None
    ) -> list[Commitment]:
        if statuses is None:
            return list(self._commitments.values())
        wanted = set(statuses)
        return [c for c in self._commitments.values() if c.status in wanted]

    async def delete_commitment(self, cid: CommitmentId) -> None:
        async with self._lock:
            self._commitments.pop(cid, None)

    async def put_knowledge(self, entry: KnowledgeEntry) -> None:
        async with self._lock:
            self._knowledge[(entry.character_id, entry.fact_id)] = entry

    async def get_knowledge(self, character_id: str, fact_id: FactId) -> KnowledgeEntry | None:
        return self._knowledge.get((character_id, fact_id))

    async def knowledge_for_character(self, character_id: str) -> list[KnowledgeEntry]:
        return [entry for (cid, _), entry in self._knowledge.items() if cid == character_id]

    async def put_contradiction_report(self, report: ContradictionReport) -> None:
        async with self._lock:
            self._contradictions[report.id] = report

    async def get_contradiction_report(
        self, report_id: ContradictionReportId
    ) -> ContradictionReport | None:
        return self._contradictions.get(report_id)

    async def list_contradiction_reports(
        self,
        *,
        resolved: bool | None = None,
        limit: int = 50,
    ) -> list[ContradictionReport]:
        rows = list(self._contradictions.values())
        if resolved is not None:
            rows = [r for r in rows if r.resolved == resolved]
        return rows[:limit]


class KeywordFactSearchIndex(FactSearchIndex):
    """Top-K search by Jaccard overlap on text tokens + stored keywords.

    Reads facts from a `ContinuityStore` rather than maintaining its own
    copy. When sqlite-vec is wired up the same protocol can be satisfied by
    a vector index; callers don't change.
    """

    def __init__(self, store: ContinuityStore, *, min_keyword_length: int = 1) -> None:
        self._store = store
        self._min_keyword_length = min_keyword_length

    def _fact_tokens(self, fact: Fact) -> set[str]:
        toks = _tokens(fact.text, min_len=self._min_keyword_length)
        toks.update(k.lower() for k in fact.keywords if k)
        return toks

    async def search(
        self, query: str, top_k: int, *, include_retired: bool = False
    ) -> list[tuple[Fact, float]]:
        q_toks = _tokens(query, min_len=self._min_keyword_length)
        if not q_toks:
            return []
        facts = await self._store.list_facts(include_retired=include_retired)
        scored: list[tuple[Fact, float]] = []
        for fact in facts:
            f_toks = self._fact_tokens(fact)
            if not f_toks:
                continue
            overlap = len(q_toks & f_toks)
            if overlap == 0:
                continue
            jaccard = overlap / len(q_toks | f_toks)
            # Cosine-style boost: rewards facts that share more proportionally.
            score = jaccard * (1.0 + math.log1p(overlap))
            scored.append((fact, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:top_k]


class StubContradictionJudge(ContradictionJudge):
    """Default judge that never finds a conflict.

    Used until the LLM Gateway (task #13) is wired up. Tests use a more
    interesting deterministic judge.
    """

    async def judge(
        self,
        candidate: Fact,
        existing: Fact,
        *,
        turn_id: TurnId | None = None,
    ) -> ContradictionCandidate:
        return ContradictionCandidate(
            existing_fact=existing,
            similarity=0.0,
            verdict=ContradictionVerdict.UNCERTAIN,
            confidence=0.0,
            rationale="stub judge: no LLM available",
        )


__all__ = [
    "InMemoryContinuityStore",
    "KeywordFactSearchIndex",
    "StubContradictionJudge",
]
