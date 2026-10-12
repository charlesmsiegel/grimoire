"""History relevance, as a decide kit (roadmap 02-C5b, for 09 and 10).

How much a recalled scene bears on the current turn, asked after retrieval
has found it. This module is the KIT: the item builders and the mappings.
The `history_check` route, the `history-rerank` task's policy row (off), the
registered eval case and the capture land with 09's call site, which names
its own ceiling (`RERANK_CEILING`).

Two forms. `build_items` scores each candidate alone on `LEVELS`;
`build_items_ranked` (01e's `Rank`) asks one item ordering up to
`MAX_RANKED` candidates, so the model compares them. 09 chooses which to
call.

**Ungraded is not irrelevant.** An item nobody graded is `Grade(None, None)`
(or `None` in a ranking), never level 0, and 09 ranks it by its own signals:
the cost rule ("a price nobody reported is never rendered as zero") one
domain over. No escalation and no sampling: relevance runs on the turn path,
where a second hop is latency the player waits for, and a low margin is a
middling grade, not a failure.

Pure: it renders templates, so a caller runs it in the threadpool.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .. import decisions, prompts
from . import routing

RELEVANCE_ID = "relevance"
LEVELS: tuple[str, ...] = ("unrelated", "background only", "related",
                           "bears directly on this turn")
RANK_ID = "ranking"
#: Candidates one ranked item may order: one structured chunk's worth, the
#: turn-path bound 09 hands over (§9.3). Argued from structure, as
#: `decisions.MAX_ITEMS_PER_CALL` is.
MAX_RANKED = decisions.MAX_ITEMS_PER_CALL

#: The task 09's call site names, on the `history_check` route it lands.
TASK = "history-rerank"

#: The route entry 09 adds to `routing.ROUTES` with its call site (§9.3), to
#: which 10 later adds `history-sufficiency`. Not registered here. Its policy
#: row, when 09 lands it, is the default: no escalation, no sampling.
ROUTE = routing.Route(
    "history_check", "History checks",
    "Whether a recalled scene bears on the current turn, after retrieval has found it.",
    (TASK,), True, operation="decide", default_role="decision", legacy=routing.NO_LEGACY)


@dataclass(frozen=True)
class Candidate:
    #: 09's evidence id.
    ref: str
    #: Bounded, already through the prompt-phase regex view.
    excerpt: str
    #: An in-fiction date label, or "".
    when: str = ""


@dataclass(frozen=True)
class Grade:
    #: An index into `LEVELS`; None = ungraded, never 0.
    level: int | None
    #: Only as a backend reported it (native); None otherwise.
    distribution: dict[str, float] | None = None


UNGRADED = Grade(None, None)


def relevance_question() -> decisions.Score:
    return decisions.Score(RELEVANCE_ID, prompts.render("history_rerank/question.j2").strip(),
                           LEVELS)


def build_items(query: str, candidates: Sequence[Candidate]) -> tuple[decisions.Item, ...]:
    """One item per candidate, in order: the query, the excerpt and its date
    as context, and `relevance_question()`."""
    question = relevance_question()
    return tuple(
        decisions.Item(prompts.render("history_rerank/item.j2", query=query,
                                      excerpt=c.excerpt, when=c.when), (question,))
        for c in candidates)


def grades_of(candidates: Sequence[Candidate],
              results: Sequence[decisions.ItemResult]) -> dict[str, Grade]:
    """`ref -> Grade`: the level read (with its distribution only as
    reported), or `UNGRADED` for every item not read -- abstained, refused,
    unreadable, an error, or one the reply never reached."""
    out: dict[str, Grade] = {}
    for index, c in enumerate(candidates):
        grade = UNGRADED
        if index < len(results):
            answer = results[index].answers.get(RELEVANCE_ID)
            level = answer.answer if answer is not None else None
            if (answer is not None and isinstance(level, int) and not isinstance(level, bool)
                    and 0 <= level < len(LEVELS)):
                grade = Grade(level, dict(answer.distribution) if answer.distribution
                              else None)
        out[c.ref] = grade
    return out


def build_items_ranked(query: str, candidates: Sequence[Candidate]) -> decisions.Item:
    """ONE item ranking the candidates (01e's `Rank`), best first, each
    described by its excerpt (and date); a native stage asks
    `history_rerank/pointwise.j2` of each instead. Refuses more than
    `MAX_RANKED` candidates or fewer than two -- a single candidate has
    nothing to be compared with, and 09 scores it with `build_items`."""
    if not 2 <= len(candidates) <= MAX_RANKED:
        raise ValueError(f"a ranked rerank takes 2 to {MAX_RANKED} candidates, "
                         f"not {len(candidates)}")
    options = tuple(decisions.Option(c.ref, f"{c.excerpt} ({c.when})" if c.when else c.excerpt)
                    for c in candidates)
    return decisions.Item(
        prompts.render("history_rerank/rank_context.j2", query=query),
        (decisions.Rank(RANK_ID, prompts.render("history_rerank/rank_question.j2").strip(),
                        options, pointwise=prompts.render("history_rerank/pointwise.j2").strip()),))


def positions_of(candidates: Sequence[Candidate],
                 result: decisions.ItemResult) -> dict[str, int | None]:
    """`ref -> tier index` (0 is best; tied candidates share one), or None for
    a candidate the ranking left unranked -- and for every candidate when the
    ranking was not read. Never a last place for one nobody ranked."""
    answer = result.answers.get(RANK_ID)
    ranking = answer.answer if answer is not None else None
    if not isinstance(ranking, decisions.Ranking):
        return {c.ref: None for c in candidates}
    return {c.ref: ranking.position(c.ref) for c in candidates}
