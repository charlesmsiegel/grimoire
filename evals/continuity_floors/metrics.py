"""The continuity candidate floors, measured on the labelled pair corpus.

The filter is production's own: each pair's records become
`similarity.subject`s, `similarity.lexical` scores them, and
`similarity.admitted_by` -- with the module's floors as they stand, or
patched to a swept value for one call -- says whether the pair clears the
floors. Nothing is restated here, so a change to a floor, a signal or a clause
moves these figures. No embedding is involved: `cosine` stays None, as it
does on a store with no Embedding role, so the semantic clause never admits
and `COSINE_FLOOR` is not measured; that takes a real model.

**The pair figures are admission rates.** Clearing the floors is necessary to
reach the judge, not sufficient: both callers then keep only a record's best
few -- absorb's identity check the hub's top `IDENTITY_TOP_K`
(`similarity.nearest`), the reconcile sweep a pair in the top
`RECONCILE_TOP_K` of either end, per type (`reconcile._top_k`). A corpus pair
is scored as its own two-record ledger, where each record has one neighbour
and neither top-k binds, so for it the two are the same; in a crowded ledger
the top-k can only drop a pair that cleared the floors, so recall there is at
most this, and precision moves with whatever the ranking keeps. A crowd
(`measure_crowd`) is one such ledger, scored through those two selections
themselves.

Recall is the share of the pairs the judge should see that the filter lets
through (a miss costs a duplicate record nobody is asked about); precision is
the share of what it lets through that the judge should see (a false
positive costs a share of one `decide()` chunk -- or, in a crowd, can cost a
positive its slot). The corpus is synthetic, so these say how the floors treat
the kinds of pair it holds -- not how often a real ledger holds each kind.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable
from dataclasses import dataclass
from unittest.mock import patch

from grimoire.store.continuity import reconcile, similarity

from .corpus import RELATIONS, Corpus, Crowd

#: The two conditions every pair is scored under. `alone`: its records share
#: no actor, scene or event, so only the lexical clause can admit it, on
#: `TOKEN_FLOOR` and `CHAR_FLOOR`. `shared cast`: both records involve the
#: player's character, who is in nearly every scene -- so nearly every pair in
#: a campaign is this one -- and the structural clause can admit it too, on
#: the weak floors.
CONDITIONS: dict[str, tuple[str, ...]] = {"alone": (), "shared cast": ("pcs:seraphine",)}

#: The floors each condition's sweep moves: the ones its deciding clause reads.
SWEPT: dict[str, tuple[str, str]] = {"alone": ("TOKEN_FLOOR", "CHAR_FLOOR"),
                                     "shared cast": ("WEAK_TOKEN", "WEAK_CHAR")}

#: The sweep: this step, this many times either side of the floor in force.
STEP = 0.05
SPAN = 2


def signals(corpus: Corpus, condition: str) -> dict[str, dict]:
    """Each pair's signals under `condition`, by pair id. No floor enters a
    signal, so a sweep scores every pair once and only re-reads the clause."""
    out = {}
    for pair in corpus.pairs:
        a, b = pair.subjects(CONDITIONS[condition])
        out[pair.id] = similarity.lexical(a, b)
    return out


def admitted(scored: dict[str, dict], **floors: float) -> frozenset[str]:
    """The pairs `similarity.admitted_by` lets through, with `floors` --
    keyed by the module constant's name -- in force for the call alone."""
    with contextlib.ExitStack() as stack:
        for name, value in floors.items():
            stack.enter_context(patch.object(similarity, name, value))
        return frozenset(pid for pid, sig in scored.items()
                         if similarity.admitted_by(sig) is not None)


@dataclass(frozen=True)
class CrowdMeasure:
    """One crowd under one condition, as pair ids: what clears the floors,
    what absorb's identity check shows the judge (`similarity.nearest` from
    the hub), what the reconcile sweep keeps (`reconcile._score_pairs` and
    `_top_k` over the whole crowd), and how many pairs between two
    neighbours, which carry no label, the sweep keeps beside them."""

    crowd: Crowd
    admitted: frozenset[str]
    identity: frozenset[str]
    sweep: frozenset[str]
    between: int


def measure_crowd(crowd: Crowd, condition: str) -> CrowdMeasure:
    """`crowd` under `condition`, at the floors in force, through production's
    own selections rather than a copy of their ranking."""
    hub, spokes = crowd.subjects(CONDITIONS[condition])
    pair_of = {s.ref: p.id for s, p in zip(spokes, crowd.pairs, strict=True)}
    cleared = frozenset(pair_of[s.ref] for s in spokes
                        if similarity.admitted_by(similarity.lexical(hub, s)) is not None)
    shown = frozenset(pair_of[s.ref] for s, _ in similarity.nearest(hub, spokes))
    ledger = [hub, *spokes]
    scored, _, finished, _, _ = reconcile._score_pairs(
        [s.ref for s in ledger], ledger, {}, reconcile.RECONCILE_MAX_PAIRS, False)
    kept = reconcile._top_k(scored, {s.ref: s for s in ledger}, finished)
    swept = frozenset(pair_of[b if a == hub.ref else a] for a, b in kept if hub.ref in (a, b))
    return CrowdMeasure(crowd, cleared, shown, swept,
                        sum(hub.ref not in pair for pair in kept))


@dataclass(frozen=True)
class Measure:
    """What the filter let through, read against the corpus's labels."""

    corpus: Corpus
    admitted: frozenset[str]

    def let_through(self, pairs: Iterable) -> tuple[int, int]:
        """`(admitted, of)` over `pairs`."""
        ids = [p.id for p in pairs]
        return sum(pid in self.admitted for pid in ids), len(ids)

    @property
    def found(self) -> tuple[int, int]:
        """The positives admitted, of every positive: recall's two counts."""
        return self.let_through(p for p in self.corpus.pairs if p.positive)

    @property
    def kept(self) -> tuple[int, int]:
        """The positives admitted, of everything admitted: precision's."""
        return self.found[0], len(self.admitted)

    @property
    def recall(self) -> float:
        hit, of = self.found
        return hit / of

    @property
    def precision(self) -> float | None:
        """None when nothing was let through: a share of nothing is unmeasured,
        not 0."""
        hit, of = self.kept
        return hit / of if of else None


def measure(corpus: Corpus, condition: str, **floors: float) -> Measure:
    """The filter over `corpus` under `condition`, at the floors in force or
    with `floors` moved."""
    return Measure(corpus, admitted(signals(corpus, condition), **floors))


def _ratio(hit: int, of: int) -> str:
    return f"{hit / of:.2f} ({hit}/{of})" if of else "n/a (none let through)"


def sweep_values(name: str) -> tuple[float, ...]:
    """The floor `name` in force, and `SPAN` steps of `STEP` either side,
    above 0."""
    now = getattr(similarity, name)
    return tuple(v for v in (round(now + k * STEP, 2) for k in range(-SPAN, SPAN + 1))
                 if v > 0)


def _lines(m: Measure) -> list[str]:
    by_answer = []
    for relation in RELATIONS[:-1]:
        pairs = [p for p in m.corpus.pairs if p.relation == relation]
        hit, of = m.let_through(pairs)
        text = f"{relation} {hit}/{of}"
        para = [p for p in pairs if p.shape == "paraphrase"]
        if para:
            text += " (paraphrased {}/{})".format(*m.let_through(para))
        by_answer.append(text)
    decoys = [p for p in m.corpus.pairs if not p.positive]
    missed = [p.id for p in m.corpus.pairs if p.positive and p.id not in m.admitted]
    through = [p.id for p in decoys if p.id in m.admitted]
    return [f"  by answer: {', '.join(by_answer)}; distinct let through "
            "{}/{}".format(*m.let_through(decoys)),
            f"  missed: {', '.join(missed) or 'none'}",
            f"  decoys let through: {', '.join(through) or 'none'}"]


def _grid(corpus: Corpus, condition: str) -> list[str]:
    rows_name, cols_name = SWEPT[condition]
    scored = signals(corpus, condition)
    cols = sweep_values(cols_name)
    lines = [(f"sweep, {condition} (recall/precision through the floors; * marks the "
              "floors in force):"),
             f"  {rows_name:>12} \\ {cols_name:<10}" + "".join(f"{c:>12.2f}" for c in cols)]
    for row in sweep_values(rows_name):
        cells = []
        for col in cols:
            m = Measure(corpus, admitted(scored, **{rows_name: row, cols_name: col}))
            mark = ("*" if (row, col) == (getattr(similarity, rows_name),
                                         getattr(similarity, cols_name)) else " ")
            precision = "n/a" if m.precision is None else f"{m.precision:.2f}"
            cells.append(f"{mark}{m.recall:.2f}/{precision}".rjust(12))
        lines.append(f"  {row:>25.2f}" + "".join(cells))
    return lines


def _by_answer(crowd: Crowd, ids: frozenset[str]) -> str:
    """`ids` among `crowd`'s pairs, per answer, in `RELATIONS` order."""
    counts = []
    for relation in RELATIONS:
        pairs = [p.id for p in crowd.pairs if p.relation == relation]
        if pairs:
            counts.append(f"{relation} {sum(pid in ids for pid in pairs)}/{len(pairs)}")
    return ", ".join(counts)


def _crowd_lines(crowd: Crowd) -> list[str]:
    lines = []
    for condition in CONDITIONS:
        m = measure_crowd(crowd, condition)
        lines += [f"{crowd.id}, {condition} ({len(crowd.pairs)} neighbours):",
                  f"  through the floors: {_by_answer(crowd, m.admitted)}",
                  (f"  absorb's identity check (similarity.nearest, the hub's top "
                   f"{similarity.IDENTITY_TOP_K}): {_by_answer(crowd, m.identity)}"),
                  (f"  the reconcile sweep (reconcile._top_k, either end's top "
                   f"{reconcile.RECONCILE_TOP_K}): {_by_answer(crowd, m.sweep)}; plus "
                   f"{m.between} pairs between neighbours, unlabelled")]
    return lines


def report(corpus: Corpus) -> str:
    """The figures at the floors in force under each condition, what each
    missed and let through, a sweep of each condition's floors, and what the
    top-k does to each crowd."""
    positives = [p for p in corpus.pairs if p.positive]
    lines = [("continuity candidate floors (offline; no embedding, so COSINE_FLOOR is "
              "not measured)"),
             (f"corpus: {len(corpus.pairs)} pairs, {len(positives)} the judge should see "
              f"and {len(corpus.pairs) - len(positives)} it should not (decoys); "
              f"{len(corpus.crowds)} crowd{'' if len(corpus.crowds) == 1 else 's'}"),
             "floors in force: " + ", ".join(
                 f"{name} {getattr(similarity, name):.2f}"
                 for name in (*SWEPT["alone"], *SWEPT["shared cast"])),
             ("pairs, each its own two-record ledger -- admission rates: one neighbour "
              "a record, so no top-k binds and what clears the floors reaches the judge; "
              "in a crowded ledger the top-k drops some of it (crowds, below)")]
    for condition in CONDITIONS:
        m = measure(corpus, condition)
        lines += [(f"{condition}, through the floors: recall {_ratio(*m.found)}, "
                   f"precision {_ratio(*m.kept)}"),
                  *_lines(m)]
    for condition in CONDITIONS:
        lines += _grid(corpus, condition)
    if corpus.crowds:
        lines.append("crowds, one ledger each: a hub with more neighbours than the top-k "
                     "keeps, scored through production's own selections")
    for crowd in corpus.crowds:
        lines += _crowd_lines(crowd)
    return "\n".join(lines)
