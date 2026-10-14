"""Frozen retrieval contracts (09-C1); raw signals never become confidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from .. import paths

RELATIONS = frozenset({"shared_cast", "actor", "location", "thread", "relationship", "group"})
NARRATOR = "narrator"
#: An NPC's perspective (11-C2): ``actor:characters:<id>``. The same spelling
#: as `store/context/epistemic.Perspective.to_string`, checked here because
#: history may not import compose.
ACTOR_PREFIX = "actor:"


def actor_of(perspective: str) -> str | None:
    """The ``characters:`` ref an actor perspective names, None for the
    narrator; ValueError for anything else."""
    if perspective == NARRATOR:
        return None
    ref = perspective[len(ACTOR_PREFIX):] if perspective.startswith(ACTOR_PREFIX) else ""
    kind, _, rid = ref.partition(":")
    if kind != "characters" or not paths.safe_id(rid):
        raise ValueError(f"not a history perspective: {perspective!r}")
    return ref


@dataclass(frozen=True)
class Query:
    texts: tuple[str, ...]
    terms: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    scopes: frozenset[str] = RELATIONS
    max_scenes: int | None = None
    perspective: str = NARRATOR
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
class RoundRecord:
    """What one retrieval round left selected (10-C2, a stable field): the
    plan it ran (empty for `E0`, which ran on the turn window), and the scene
    keys and 09-C1 evidence ids of the evidence as merged after it, so
    consecutive records say exactly what each round added."""

    questions: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    subjects: tuple[str, ...] = ()
    scene_keys: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    @classmethod
    def of(cls, items: tuple[EvidenceItem, ...], *, questions: tuple[str, ...] = (),
           terms: tuple[str, ...] = (), subjects: tuple[str, ...] = ()) -> RoundRecord:
        return cls(questions, terms, subjects, tuple(i.scene.key for i in items),
                   tuple(e for i in items for e in i.evidence_ids))

    def projection(self) -> dict:
        return {"questions": list(self.questions), "terms": list(self.terms),
                "subjects": list(self.subjects), "scene_keys": list(self.scene_keys),
                "evidence_ids": list(self.evidence_ids)}


@dataclass(frozen=True)
class Step:
    """One planning step, diagnostic only: its task, how it ended, its wall
    time and the ledger rows it filed."""

    task: str
    outcome: str
    ms: int = 0
    rows: int = 0


#: `PlanTrace.terminal` values without a reason suffix; `not_run:` and
#: `skipped:` carry one.
TERMINALS = ("declined", "unusable", "planned", "unrepaired", "repair_redundant", "repaired")


@dataclass(frozen=True)
class PlanTrace:
    """Where retrieval planning stopped and why (10-C2).

    `terminal`, `trigger`, `sufficiency`, `sufficiency_source`,
    `cheap_retrieval_failed`, `e0` and `rounds` are STABLE: 12's RP trigger
    reads them, and renaming or re-meaning one is a contract change, never a
    refactor. `steps`, `plans` and `gain` are diagnostic."""

    terminal: str
    trigger: str = ""
    sufficiency: str = "not_asked"
    sufficiency_source: str = "not_asked"
    cheap_retrieval_failed: bool = False
    e0: RoundRecord = field(default_factory=RoundRecord)
    rounds: tuple[RoundRecord, ...] = ()
    steps: tuple[Step, ...] = ()
    #: Each parsed plan as `plan.projection` shows it (refs as names).
    plans: tuple[Mapping, ...] = ()
    #: Selected scene keys the final evidence carries that `E0` did not.
    gain: tuple[str, ...] = ()
    #: Each retrieval's semantic status, `E0`'s first and then each planned
    #: round's: a merge reads `mixed`, so this is what says how many query
    #: embeddings the phase sent (`history_recall.embeds`).
    semantic: tuple[str, ...] = ()

    def projection(self) -> dict:
        return {
            "terminal": self.terminal, "trigger": self.trigger,
            "sufficiency": self.sufficiency, "sufficiency_source": self.sufficiency_source,
            "cheap_retrieval_failed": self.cheap_retrieval_failed,
            "e0": self.e0.projection(), "rounds": [r.projection() for r in self.rounds],
            "steps": [{"task": s.task, "outcome": s.outcome, "ms": s.ms, "rows": s.rows}
                      for s in self.steps],
            "plans": [dict(p) for p in self.plans], "gain": list(self.gain),
            "semantic": list(self.semantic),
        }

    @classmethod
    def from_projection(cls, data: object) -> PlanTrace | None:
        """`projection`'s trace again (a frozen history view's, 11's
        `to_record`), or None for anything that does not read as one: a
        trace is diagnostic, so an unreadable one is dropped, never fatal."""
        try:
            if not isinstance(data, dict):
                return None

            def strings(value) -> tuple[str, ...]:
                return tuple(str(v) for v in value) if isinstance(value, list) else ()

            def record(value) -> RoundRecord:
                value = value if isinstance(value, dict) else {}
                return RoundRecord(*(strings(value.get(k)) for k in (
                    "questions", "terms", "subjects", "scene_keys", "evidence_ids")))
            return cls(
                str(data["terminal"]), trigger=str(data.get("trigger", "")),
                sufficiency=str(data.get("sufficiency", "not_asked")),
                sufficiency_source=str(data.get("sufficiency_source", "not_asked")),
                cheap_retrieval_failed=data.get("cheap_retrieval_failed") is True,
                e0=record(data.get("e0")),
                rounds=tuple(record(r) for r in data.get("rounds") or ()),
                steps=tuple(Step(str(s["task"]), str(s["outcome"]), int(s.get("ms", 0)),
                                 int(s.get("rows", 0)))
                            for s in data.get("steps") or () if isinstance(s, dict)),
                plans=tuple(dict(p) for p in data.get("plans") or () if isinstance(p, dict)),
                gain=strings(data.get("gain")), semantic=strings(data.get("semantic")))
        except (KeyError, TypeError, ValueError):
            return None


@dataclass(frozen=True)
class Evidence:
    items: tuple[EvidenceItem, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    coverage: Coverage = field(default_factory=lambda: Coverage("exhausted"))
    ceiling: int = 0
    query_digest: str = ""
    #: The selection's own depth -- `Query.max_scenes` when it set one below
    #: the setting -- so composition re-fits each item to the share retrieval
    #: fitted it to. 0 for hand-built evidence: the setting's depth stands.
    depth: int = 0
    #: 10-C2's trace when the planner was in play (`history_plan` on, or a
    #: forced gather), else None -- and then `detail` carries no `plan` key,
    #: so a turn with planning off has exactly 09's row.
    plan: PlanTrace | None = None

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
        } | ({"plan": self.plan.projection()} if self.plan is not None else {})
