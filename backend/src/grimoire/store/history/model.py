"""Frozen retrieval contracts (09-C1); raw signals never become confidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

RELATIONS = frozenset({"shared_cast", "actor", "location", "thread", "relationship", "group"})


@dataclass(frozen=True)
class Query:
    texts: tuple[str, ...]
    terms: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    scopes: frozenset[str] = RELATIONS
    max_scenes: int | None = None
    perspective: str = "narrator"
    tier_limit: int = 2
    turn_index: int | None = None


@dataclass(frozen=True)
class SceneRef:
    sid: str
    identity: str | None
    campaign: str = ""

    @property
    def key(self) -> str:
        base = self.identity or "sid:" + self.sid
        return "campaign:" + self.campaign + "/" + base if self.campaign else base


@dataclass(frozen=True)
class Relation:
    kind: str
    refs: tuple[str, ...]


@dataclass(frozen=True)
class StructuralSignal:
    relations: tuple[Relation, ...]


@dataclass(frozen=True)
class LexicalSignal:
    score: float
    matched: tuple[str, ...]


@dataclass(frozen=True)
class SemanticSignal:
    cosine: float
    query: int = 0


@dataclass(frozen=True)
class RerankSignal:
    level: str | None = None
    position: int | None = None
    backend: str = ""


@dataclass(frozen=True)
class Signals:
    structural: StructuralSignal | None = None
    lexical: LexicalSignal | None = None
    semantic: SemanticSignal | None = None
    rerank: RerankSignal | None = None


@dataclass(frozen=True)
class Candidate:
    scene: SceneRef
    rounds: tuple[Signals, ...]
    ranks: Mapping[tuple[int, str], int]
    tier: int
    admitted_by: frozenset[str]

    def __post_init__(self) -> None:
        object.__setattr__(self, "ranks", MappingProxyType(dict(self.ranks)))


@dataclass(frozen=True)
class EvidencePost:
    index: int
    key: str
    part: int | None
    speaker: str
    text: str


@dataclass(frozen=True)
class Excerpt:
    posts: tuple[EvidencePost, ...]
    text: str


@dataclass(frozen=True)
class EvidenceItem:
    scene: SceneRef
    title: str
    date: str = ""
    location: str = ""
    summary: str = ""
    excerpts: tuple[Excerpt, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    tokens: int = 0
    # Original prose survives omission, so S2 can re-evaluate its archive view.
    summary_omission: str = ""

    def __post_init__(self) -> None:
        if not self.evidence_ids:
            ids = tuple(
                self.scene.key + "@" + (e.posts[0].key or str(e.posts[0].index))
                for e in self.excerpts
                if e.posts
            )
            object.__setattr__(self, "evidence_ids", ids or (self.scene.key,))


@dataclass(frozen=True)
class Coverage:
    verdict: str
    tiers: tuple[int, ...] = (1,)
    semantic: str = "skipped:preview"
    admitted: int = 0
    agreement: int = 0
    eligible: int = 0
    rerank: str = "off"


def _signals(signals: Signals) -> dict:
    return {
        "structural": (
            [{"relation": r.kind, "refs": list(r.refs)} for r in signals.structural.relations]
            if signals.structural
            else None
        ),
        "lexical": (
            {"score": signals.lexical.score, "matched": list(signals.lexical.matched)}
            if signals.lexical
            else None
        ),
        "semantic": (
            {"cosine": signals.semantic.cosine, "query": signals.semantic.query}
            if signals.semantic
            else None
        ),
        "rerank": (
            {
                "level": signals.rerank.level,
                "position": signals.rerank.position,
                "backend": signals.rerank.backend,
            }
            if signals.rerank
            else None
        ),
    }


@dataclass(frozen=True)
class Evidence:
    items: tuple[EvidenceItem, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    coverage: Coverage = field(default_factory=lambda: Coverage("exhausted"))
    ceiling: int = 0
    query_digest: str = ""

    def detail(self) -> dict:
        """JSON-safe inspector data; no callable or fused ordering number."""
        candidates = {c.scene.key: c for c in self.candidates}
        items = []
        for item in self.items:
            c = candidates.get(item.scene.key)
            items.append(
                {
                    "scene": item.scene.sid,
                    "identity": item.scene.identity,
                    "campaign": item.scene.campaign,
                    "key": item.scene.key,
                    "title": item.title,
                    "date": item.date,
                    "location": item.location,
                    "tier": c.tier if c else 1,
                    "admitted_by": sorted(c.admitted_by) if c else [],
                    "rounds": [_signals(s) for s in c.rounds] if c else [],
                    "ranks": [
                        {"round": n, "signal": s, "rank": r}
                        for (n, s), r in sorted(c.ranks.items())
                    ]
                    if c
                    else [],
                    "excerpts": [[p.index for p in e.posts] for e in item.excerpts],
                    "evidence_ids": list(item.evidence_ids),
                    "summary": item.summary_omission or ("included" if item.summary else "none"),
                    "tokens": item.tokens,
                    "shed": False,
                }
            )
        cv = self.coverage
        return {
            "items": items,
            "candidates": [
                {
                    "scene": c.scene.sid,
                    "key": c.scene.key,
                    "tier": c.tier,
                    "admitted_by": sorted(c.admitted_by),
                    "rounds": [_signals(s) for s in c.rounds],
                    "ranks": [
                        {"round": n, "signal": s, "rank": r}
                        for (n, s), r in sorted(c.ranks.items())
                    ],
                }
                for c in self.candidates
            ],
            "coverage": {
                "verdict": cv.verdict,
                "tiers": list(cv.tiers),
                "semantic": cv.semantic,
                "admitted": cv.admitted,
                "agreement": cv.agreement,
                "eligible": cv.eligible,
                "rerank": cv.rerank,
            },
            "ceiling": self.ceiling,
            "query_digest": self.query_digest,
        }
