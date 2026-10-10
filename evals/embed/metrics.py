"""Grading a ranking: recall@k and MRR, per shape and overall.

A ranking maps a query id to document ids, best first. Pure Python, no
numpy (the Android dependency set has none, and nothing here needs it).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from .corpus import SHAPES, Corpus

#: The k of recall@k.
KS: tuple[int, ...] = (1, 3, 5, 10)

#: The metric names a grade carries, in the order a table shows them.
METRICS: tuple[str, ...] = (*(f"R@{k}" for k in KS), "MRR")


def recall_at(ranking: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """The share of `relevant` in the top `k` of `ranking`."""
    top = set(ranking[:k])
    return sum(1 for doc in relevant if doc in top) / len(relevant)


def reciprocal_rank(ranking: Sequence[str], relevant: Sequence[str]) -> float:
    """1 / the rank of the first relevant document, or 0 when none is ranked."""
    wanted = set(relevant)
    for n, doc in enumerate(ranking, 1):
        if doc in wanted:
            return 1.0 / n
    return 0.0


@dataclass
class Grade:
    """A ranking graded against the corpus."""

    #: shape -> metric -> mean over its queries; "all" is every query.
    scores: dict[str, dict[str, float]] = field(default_factory=dict)
    #: Queries with no relevant document in the top `max(KS)`: the misses a
    #: report flags.
    misses: list[str] = field(default_factory=list)
    #: Queries the ranking does not answer, and document ids it names that
    #: the corpus does not have: a recording that cannot be graded whole.
    unanswered: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)

    @property
    def whole(self) -> bool:
        """Whether every query was ranked, over known documents only."""
        return not self.unanswered and not self.unknown


def grade(corpus: Corpus, rankings: Mapping[str, Sequence[str]]) -> Grade:
    """`rankings` graded against `corpus`. An unanswered query scores 0 on
    every metric (and is listed): a recording that skipped a hard query must
    not read better for it."""
    known = {d.id for d in corpus.documents}
    out = Grade()
    per: dict[str, list[dict[str, float]]] = {}
    for q in corpus.queries:
        ranking = list(rankings.get(q.id, ()))
        if q.id not in rankings:
            out.unanswered.append(q.id)
        out.unknown.extend(f"{q.id}:{doc}" for doc in ranking if doc not in known)
        row = {f"R@{k}": recall_at(ranking, q.relevant, k) for k in KS}
        row["MRR"] = reciprocal_rank(ranking, q.relevant)
        if row[f"R@{KS[-1]}"] == 0:
            out.misses.append(q.id)
        per.setdefault(q.shape, []).append(row)
        per.setdefault("all", []).append(row)
    for shape in (*SHAPES, "all"):
        rows = per.get(shape, [])
        if rows:
            out.scores[shape] = {m: sum(r[m] for r in rows) / len(rows) for m in METRICS}
    return out


def table(rows: Sequence[tuple[str, Grade]]) -> str:
    """One table: a block per graded ranking, a line per shape."""
    lines = [f"{'ranking':<28} {'shape':<9} " + " ".join(f"{m:>6}" for m in METRICS)]
    for name, g in rows:
        for shape, scores in g.scores.items():
            lines.append(f"{name:<28} {shape:<9} "
                         + " ".join(f"{scores[m]:>6.2f}" for m in METRICS))
        if g.misses:
            lines.append(f"{'':<28} misses: {', '.join(g.misses)}")
        if not g.whole:
            lines.append(f"{'':<28} NOT WHOLE: unanswered {g.unanswered or '-'}; "
                         f"unknown ids {g.unknown or '-'}")
    return "\n".join(lines)
