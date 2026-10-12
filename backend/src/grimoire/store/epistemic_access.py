"""Epistemic access, as a decide kit (roadmap 02-C5a, for 11-C1).

Whether a character could know a recalled piece of history, asked only for
the pairs 11's deterministic rules could not settle. This module is the KIT:
the item builder, the mapping back, the vocabulary and the policy row 11
registers. It names no task in a `decide()` call: the route (`epistemic`),
the `TASK_POLICY` row, the registered eval case and the capture all land
with 11's call site, because a route may not land before the call site that
decides it (`test_operation_guard.py`).

Three rules the mapping keeps:

- **Fail closed.** Everything that is not a read class -- abstained,
  refused, unreadable, `NOT_AN_OPTION`, an error, an item the reply never
  reached -- is `UNKNOWN`, never `known`: the costly error is a character
  knowing what they should not, so every failure lands on the side that
  withholds.
- **A hop that did not answer withholds too.** Escalation (01d-C2b) leaves
  the BASE answer in `decision.items[i]` when its hop fails or is skipped,
  and says so only in `decision.escalations`. So `access_of` reads the whole
  `Decision`: an item whose escalation `outcome` is not `answered` is
  `UNKNOWN`, whatever its base answer was.
- **Never sampled, never persisted.** What a character knows is not
  behaviour, and the result is per turn.

Pure: it renders templates, so a caller runs it in the threadpool.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .. import decisions, prompts
from . import routing

#: The question's id.
ACCESS_ID = "access"
#: The classes an answer may name. A null (`allow_none`) is `UNKNOWN`.
CLASSES: tuple[str, ...] = ("known", "suspected", "experienced", "narrator_only")
#: What every failure, and every unanswered hop, maps to: the side that
#: withholds.
UNKNOWN = "unknown"
#: The answers that grant the character something, and so the only ones
#: worth a second opinion (01d-C2a's answer filter): a low-margin
#: `narrator_only` is already safe, and escalating it would only spend.
PERMISSIVE: tuple[str, ...] = ("known", "suspected", "experienced")

#: Every reading from the most withholding to the least: a pair asked twice
#: keeps the earlier of its two (`access_of`). Hearsay (`known`) withholds
#: more than having been there (`experienced`).
WITHHOLDING: tuple[str, ...] = (UNKNOWN, "narrator_only", "suspected", "known", "experienced")

#: The task 11's call site names, on the `epistemic` route it lands.
TASK = "epistemic-access"

#: The route entry 11 adds to `routing.ROUTES` with its call site (§9.2).
#: Not registered here: a route may not land before the call site that
#: decides its task.
ROUTE = routing.Route(
    "epistemic", "Character knowledge",
    "Whether a character could know a recalled piece of history, where the record "
    "alone cannot say.",
    (TASK,), True, operation="decide", default_role="decision", legacy=routing.NO_LEGACY)


def policy() -> routing.TaskPolicy:
    """The 01d-C1 row 11 registers with its call site (§9.2, §10): escalate a
    refusal or a low margin on a PERMISSIVE answer to Primary, at 01d-C3's
    starting threshold for each native endpoint kind; the role's fallback;
    never sampled. Not `abstained`: an abstention is `UNKNOWN`, already the
    safe side."""
    return routing.TaskPolicy(
        escalate_to="primary", escalate_on=("refused", "low_margin"), question=ACCESS_ID,
        margins=(("openai_compatible", routing.DEFAULT_MARGIN),
                 ("openrouter", routing.DEFAULT_MARGIN)),
        escalate_answers=PERMISSIVE)


@dataclass(frozen=True)
class AccessQuestion:
    """One (actor, evidence) pair the deterministic rules left open."""

    actor_ref: str
    actor_name: str
    #: 09's evidence id.
    evidence_ref: str
    #: Bounded, already through the prompt-phase regex view.
    excerpt: str
    #: 11's deterministic signals, e.g. ("present", "not_addressed").
    basis: tuple[str, ...] = ()


def class_description(access: str) -> str:
    return prompts.render("epistemic_access/class.j2", access=access).strip()


def access_question() -> decisions.Choice:
    """The one question: a choice over `CLASSES`, null allowed."""
    return decisions.Choice(
        ACCESS_ID, prompts.render("epistemic_access/question.j2").strip(),
        tuple(decisions.Option(c, class_description(c)) for c in CLASSES), allow_none=True)


def build_items(questions: Sequence[AccessQuestion]) -> tuple[decisions.Item, ...]:
    """One item per pair, in order: the actor, the excerpt and the basis as
    context, and `access_question()`. The kit decides no pair it was not
    handed."""
    question = access_question()
    return tuple(
        decisions.Item(prompts.render("epistemic_access/item.j2", name=q.actor_name,
                                      excerpt=q.excerpt, basis=list(q.basis)),
                       (question,))
        for q in questions)


def access_of(questions: Sequence[AccessQuestion],
              decision: decisions.Decision) -> dict[tuple[str, str], str]:
    """`(actor_ref, evidence_ref) -> class`, failing closed (module
    docstring): a read class stands only when no escalation of its item
    ended other than `answered`; everything else is `UNKNOWN`. A pair handed
    twice keeps the more withholding reading (`WITHHOLDING`)."""
    unanswered = {e.index for e in decision.escalations if e.outcome != "answered"}
    out: dict[tuple[str, str], str] = {}
    for index, q in enumerate(questions):
        access = UNKNOWN
        if index < len(decision.items) and index not in unanswered:
            answer = decision.items[index].answers.get(ACCESS_ID)
            if answer is not None and isinstance(answer.answer, str) \
                    and answer.answer in CLASSES:
                access = answer.answer
        key = (q.actor_ref, q.evidence_ref)
        held = out.get(key)
        out[key] = access if held is None else min(held, access, key=WITHHOLDING.index)
    return out
