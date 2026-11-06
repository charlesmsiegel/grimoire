"""Synthetic agenda for the 039 NPC-goal proofs: Oda, the guild's counting-house merchant, at the dinner.

Oda wants the guild's debts collected before the ledger closes. The easiest money is Bellamy's offer
for the guest ledger, which a narrative planner would love (a confrontation over it), but Oda will
never deal with anyone it believes carries tales to the river lords. Whether Oda believes that of
Bellamy depends only on what Oda was told: Quill's warning, or nothing at all. No real library content.
"""

from __future__ import annotations

from generation_fixtures import BELLAMY, ODA, QUILL, WREN
from voice_fixtures import CAMPAIGN

from grimoire.core import Belief, Knowledge, Moment, Proposition, Report, Ruling, Subject, actor_knowledge
from grimoire.core.authority import DIRECTOR
from grimoire.goals import Agenda, Approach, Goal, Holds, Limit, Present

TALES = "tales"  # neutral id: ids are never secrecy-safe text (KNOWLEDGE.md)
TALES_TEXT = "Bellamy carries tales to the river lords"
COLLECT_AIM = "Collect what the guild is owed before the ledger closes tonight"
SELL_ACTION = "Sell Bellamy the guest ledger for the price Bellamy offered"
PRESS_ACTION = "Press Wren to settle the unpaid tally from last winter"
RULE = "Never deal with anyone you believe carries tales to the river lords"
SELL = ("collect", "sell-ledger")  # what a narrative planner wishes for: a fight over the ledger

AGENDA = Agenda(CAMPAIGN, ODA, goals=(
    Goal("collect", COLLECT_AIM, 50, (
        Approach("sell-ledger", SELL_ACTION, needs=(Present(BELLAMY),), tags=frozenset({"deal-bellamy"})),
        Approach("press-wren", PRESS_ACTION, needs=(Present(WREN),), tags=frozenset({"collect"})),
    )),
    Goal("count", "Keep the counting-house books straight", 10, (
        Approach("tally", "Tally the evening's takings at the counter"),
    )),
), limits=(
    Limit("no-informants", RULE, forbids=frozenset({"deal-bellamy"}), when=(Holds(TALES),)),
))

PRESENT = (ODA, BELLAMY, QUILL, WREN)
_PROPOSITION = Proposition(CAMPAIGN, TALES, TALES_TEXT, Moment(1), "director",
                           about=(Subject("instance", str(BELLAMY)),))
_WARNED = Report(CAMPAIGN, "quill-warns-oda", ODA, TALES, "told", Moment(2), "scene:dinner/t1", confirmed=True,
                 sender=QUILL, attributed_to=QUILL)


def ledger(*, warned: bool = False, stance: str | None = None, true: bool = False) -> Knowledge:
    """Oda's campaign: warned by Quill or not, with a belief of ``stance`` or none.

    ``true`` adds the director's ruling that Bellamy really is an informant: canon, which Oda's
    choice must never see.
    """
    evidence = [_WARNED] if warned else []
    beliefs = ([Belief(CAMPAIGN, "oda-on-bellamy", ODA, TALES, stance, Moment(2), DIRECTOR, ("quill-warns-oda",))]
               if stance is not None else [])
    rulings = [Ruling(CAMPAIGN, "tales-true", TALES, "established", Moment(1), "director",
                      grounds=(Subject("instance", str(BELLAMY)),))] if true else []
    return Knowledge(CAMPAIGN, propositions=[_PROPOSITION], rulings=rulings, evidence=evidence, beliefs=beliefs)


def oda_knows(**kw):
    return actor_knowledge(ledger(**kw), ODA, as_of=2)
