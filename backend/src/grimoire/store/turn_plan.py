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

from collections.abc import Mapping

from .. import prompts

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
