"""The optional bounded ``decision`` call (sprint 045): one NPC picks among what its policy allows.

039's deterministic policy (``grimoire.goals.choose``) is the default and costs nothing. When the
turn's policy enables the ``decision`` stage, one capped call may instead pick which of the NPC's
*possible and permitted* approaches it takes now. This module renders that call's messages and reads
its reply; it calls nothing. The options are only the brief's intention's ``feasible`` approaches:
one a condition rules out or a limit forbids is never offered, so no reply can reach it.

The call sees the NPC's own brief (its knowledge, card and voice, built by ``npc_brief`` from its own
view) and the options. An option's text is the director's authoring, so it is screened like the
intention it extends: one stating a secret the NPC has not learned is refused (``option_leak``)
before any call is made.
"""

from __future__ import annotations

import re

from grimoire.goals import Choice

from .brief import NpcBrief, leaks
from .errors import GenerationError

#: The most options one call is offered; 039's caps (16 goals of 8 approaches) allow more.
MAX_OPTIONS = 9
#: A pick is the option's number and nothing else (a closing stop is tolerated).
_PICK = re.compile(r"\s*([1-9])\s*\.?\s*")


def _one_line(text: str) -> str:
    return " ".join(text.split())


def decision_options(brief: NpcBrief) -> tuple[tuple[str, str], ...]:
    """The (goal id, approach id) pairs the decision call offers, in the policy's order.

    ``no_intention`` without a 039 choice on the brief; ``nothing_to_decide`` with fewer than two
    possible approaches (one option is no decision, so no call is worth making); ``too_many_options``
    over ``MAX_OPTIONS``.
    """
    if not isinstance(brief, NpcBrief):
        raise GenerationError("bad_brief", "a decision needs the NPC's NpcBrief")
    choice = brief.intention
    if type(choice) is not Choice:
        raise GenerationError("no_intention", f"the brief for {brief.actor} carries no 039 choice to decide on")
    if choice.selection is not None:
        raise GenerationError("already_decided", f"the choice for {brief.actor} was already selected")
    options = tuple((entry.goal_id, entry.approach_id) for entry in choice.feasible)
    if len(options) < 2:
        raise GenerationError("nothing_to_decide", f"{brief.actor} has fewer than two possible approaches now")
    if len(options) > MAX_OPTIONS:
        raise GenerationError("too_many_options", f"{brief.actor} has more than {MAX_OPTIONS} possible approaches")
    return options


def decision_messages(brief: NpcBrief) -> tuple[tuple[str, str], ...]:
    """The (role, content) messages of one NPC's decision call: its brief, then its numbered options.

    Refused as ``decision_options`` refuses, and ``option_leak`` for an option whose aim or action
    states a secret hidden from the NPC.
    """
    options = decision_options(brief)
    choice: Choice = brief.intention  # type: ignore[assignment]
    lines = []
    for number, pair in enumerate(options, start=1):
        goal, approach = choice.agenda.approach(*pair)
        text = f"{_one_line(goal.aim)}: {_one_line(approach.action)}"
        leak = leaks(text, brief.hidden, brief.exposed)
        if leak is not None:
            raise GenerationError("option_leak", f"an option for {brief.actor} states secret {leak}, "
                                  "which it has not learned")
        lines.append(f"{number}. {text}")
    ask = (f"Do not write {brief.name}'s reply yet. First decide what {brief.name} does now, as {brief.name} "
           f"would, from what {brief.name} knows and wants. These are the only choices:\n" + "\n".join(lines)
           + "\n\nAnswer with the number of one choice and nothing else.")
    return (("system", brief.system), ("user", ask))


def read_pick(reply: object, options: tuple[tuple[str, str], ...]) -> tuple[str, str] | None:
    """The option ``reply`` picks, or ``None`` when it is not exactly one listed number."""
    if not isinstance(reply, str):
        return None
    found = _PICK.fullmatch(reply)
    if found is None:
        return None
    number = int(found.group(1))
    return options[number - 1] if number <= len(options) else None
