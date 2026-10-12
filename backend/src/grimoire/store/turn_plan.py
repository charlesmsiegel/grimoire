"""The turn intent and turn plan (roadmap 02 §6, §7): closed questions about
HOW the assigned actor answers, decided before their contribution is
composed, and the block that carries the answer into that one prompt.

Pure: it renders templates (so a caller runs it in the threadpool), and owns
no call -- `routes/character_turns` asks the decision, stores its record on
the response, and hands the record back to `store.context.compose_turn` as
`plan`, which renders `block` after the history.

A stance is a way of answering, never a script: it never says WHAT the
character says. Disclosure is about what the character already knows, so it
can make them more or less forthcoming and can never grant knowledge; tension
shapes pacing. The vocabularies are small and closed on purpose, and live in
templates so revising one is a template edit plus a recording update.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from .. import decisions, prompts
from . import tokens

#: The intent's one question, and the plan's first.
STANCE_ID = "stance"
STANCES: tuple[str, ...] = (
    "engage",    # answer or go along openly
    "press",     # push, challenge or accuse
    "deflect",   # evade, change the subject, answer another question
    "refuse",    # decline openly
    "deceive",   # say what the character believes untrue
    "withdraw",  # disengage, go quiet, leave the exchange
    "act",       # let an action carry the reply rather than words
)

#: The plan's second question: of what the character already knows, how much
#: they let out. Never drawn (§7.2).
DISCLOSURE_ID = "disclosure"
DISCLOSURES: tuple[str, ...] = ("share", "hint", "withhold")

#: The plan's third question, a three-level score. Never drawn (§7.2).
TENSION_ID = "tension"
TENSION_LEVELS: tuple[str, ...] = ("ease off", "hold steady", "raise the stakes")


#: Tokens of the actor's own card a turn decision reads. Argued structurally:
#: a closed question over a short context answers as well as over a long one,
#: and the item is re-sent per contribution, so its context is paid every
#: time. Tuned against the play gate's adherence grader later, never against
#: a real library.
INTENT_BRIEF_TOKENS = 600


def stance_description(stance: str) -> str:
    return prompts.render("turn_plan/stance.j2", stance=stance).strip()


def disclosure_description(disclosure: str) -> str:
    return prompts.render("turn_plan/disclosure.j2", disclosure=disclosure).strip()


def tension_description(level: int) -> str:
    return prompts.render("turn_plan/tension.j2", level=level).strip()


def block(plan: Mapping | None, name: str) -> str:
    """The intent block for `name`'s contribution, from the response's
    `intent` record, or "" when there is nothing to send: no record, a
    skipped one, or one whose every answer went unread. Only a value the
    vocabulary knows renders -- a hand-edited record naming something else
    sends nothing for that question rather than a guess."""
    if not plan or plan.get("skipped"):
        return ""
    stance = plan.get("stance")
    disclosure = plan.get("disclosure")
    tension = plan.get("tension")
    parts = {
        "stance": stance_description(stance) if stance in STANCES else "",
        "disclosure": disclosure_description(disclosure) if disclosure in DISCLOSURES else "",
        "tension": (tension_description(tension)
                    if isinstance(tension, int) and not isinstance(tension, bool)
                    and 0 <= tension < len(TENSION_LEVELS) else ""),
    }
    if not any(parts.values()):
        return ""
    return prompts.render("scene/turn_intent.j2", name=name, **parts).strip()


def brief_of(card: Mapping) -> str:
    """The actor's own card brief: its description, then its personality --
    the actor's OWN material, which the writer reads too, and nothing about
    anyone else -- clipped to `INTENT_BRIEF_TOKENS` (`clip`)."""
    parts = [str(card.get(key) or "").strip() for key in ("description", "personality")]
    return clip("\n\n".join(p for p in parts if p), INTENT_BRIEF_TOKENS)


def clip(text: str, limit: int) -> str:
    """`text` cut to at most `limit` tokens (`tokens.count_tokens`), at a word
    boundary where one is near: a character guess first, then shrunk until it
    counts under the limit. Never longer than `text`."""
    if limit <= 0:
        return ""
    if tokens.count_tokens(text) <= limit:
        return text
    cut = text[: limit * 4]
    while cut and tokens.count_tokens(cut) > limit:
        cut = cut[: int(len(cut) * 0.9)]
    space = cut.rfind(" ")
    if space > len(cut) // 2:
        cut = cut[:space]
    return cut.rstrip()


def stance_question() -> decisions.Choice:
    """The stance question: a choice over `STANCES`, each described by its
    template, null allowed (no particular stance is a real answer)."""
    return decisions.Choice(
        STANCE_ID, prompts.render("turn_plan/stance_question.j2").strip(),
        tuple(decisions.Option(s, stance_description(s)) for s in STANCES),
        allow_none=True)


def context(name: str, conversation: Sequence[dict], brief: str) -> str:
    """A turn decision item's context (`turn_plan/item.j2`)."""
    return prompts.render("turn_plan/item.j2", name=name, brief=brief,
                          conversation=list(conversation))


def intent_item(name: str, conversation: Sequence[dict], brief: str) -> decisions.Item:
    """The turn intent as one decision item (02-C2b): the actor's name, their
    own `brief` and the observable `conversation` (the speaker pick's window,
    through the prompt-phase regex view) as context, and one question,
    `stance`."""
    return decisions.Item(context(name, conversation, brief), (stance_question(),))


def stance_of(result: decisions.ItemResult) -> tuple[str | None, str]:
    """`(stance, reason)` from the item's result: a stance the vocabulary
    knows with reason "", or None with why -- `abstained` (no particular
    stance, a real answer), `refused`, `unreadable` or `error`. Nothing
    unread is guessed into a stance."""
    answer = result.answers.get(STANCE_ID)
    if answer is None:
        return None, "unreadable"
    if isinstance(answer.answer, str) and answer.answer in STANCES:
        return answer.answer, ""
    return None, answer.reason or "unreadable"
