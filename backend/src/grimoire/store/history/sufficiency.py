"""The evidence-sufficiency predicate (roadmap 10-C3), as a decide kit.

One closed question about recalled history: does it hold what the next reply
needs to stay consistent with what happened earlier? A yes/no question is a
`decide` call, never prose parsed afterwards, so this module builds the one
`decisions.Item` and maps the decision back to a verdict; the call site
(`routes/history_recall.sufficient`) resolves, bounds and captures it.

**No rationale is asked for.** Nothing stands on one (a status word stands on
its evidence), and handing a model's rationale to the repair planner would
make one model's prose another's instruction: the planner is told only that
the check said insufficient.

**A non-answer is never `insufficient`.** An abstention, a native refusal, an
unread reply or a failed call is `unknown`, which stops planning exactly as
`sufficient` does: spending the repair hop on "the checker could not say"
would turn a non-answer into more spend.

Pure apart from rendering templates, so a caller runs it in a worker. Its
text arrives already through the prompt view (09's window, 08's excerpts).
"""

from __future__ import annotations

from ... import decisions, prompts
from . import model
from . import plan as plan_module

QUESTION_ID = "sufficient"
#: The task the call site names, on the `history_check` route.
TASK = "history-sufficiency"

SUFFICIENT = "sufficient"
INSUFFICIENT = "insufficient"
UNKNOWN = "unknown"
#: `PlanTrace.sufficiency` before any check ran.
NOT_ASKED = "not_asked"
#: `PlanTrace.sufficiency_source`: the check's own answer, an answer only
#: after an 01d escalation hop, or a non-answer that stayed one.
ANSWER = "answer"
ESCALATED = "escalated"

#: One excerpt line, in characters.
EXCERPT_CHARS = 240


def question() -> decisions.Predicate:
    return decisions.Predicate(QUESTION_ID, prompts.render("history_check/question.j2").strip())


def _first_line(text: str) -> str:
    line = next((ln.strip() for ln in str(text or "").splitlines() if ln.strip()), "")
    return plan_module.clip_words(line, EXCERPT_CHARS)


def item(turn_window: str, evidence: model.Evidence) -> decisions.Item:
    """The one item: the turn window (clipped as the planner's) and the
    evidence as it would render -- each item's header, one-line summary and
    the first line of its first excerpt, at most the selection's depth."""
    depth = evidence.depth or len(evidence.items)
    shown = []
    for found in evidence.items[:max(depth, 0)]:
        summary = found.summary if found.summary and not found.summary_omission else ""
        shown.append({"title": plan_module.one_line(found.title, plan_module.SUMMARY_CHARS),
                      "date": plan_module.one_line(found.date, 60),
                      "summary": plan_module.one_line(summary, plan_module.SUMMARY_CHARS),
                      "excerpt": _first_line(found.excerpts[0].text) if found.excerpts
                      else ""})
    context = prompts.render("history_check/item.j2",
                             window=plan_module.tail_bytes(turn_window, plan_module.WINDOW_BYTES),
                             evidence=shown).strip()
    return decisions.Item(context, (question(),))


def verdict_of(decision: decisions.Decision) -> tuple[str, str]:
    """`(verdict, source)` for the one item: `sufficient` or `insufficient`
    from a read True or False, else `unknown`; the source says whether the
    answer came only from an escalation hop."""
    if not decision.items:
        return UNKNOWN, UNKNOWN
    answer = decision.items[0].answers.get(QUESTION_ID)
    said = answer.answer if answer is not None else None
    if not isinstance(said, bool):
        return UNKNOWN, UNKNOWN
    escalated = any(e.index == 0 and e.outcome == "answered" for e in decision.escalations)
    return (SUFFICIENT if said else INSUFFICIENT), (ESCALATED if escalated else ANSWER)
