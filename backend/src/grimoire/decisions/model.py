"""One NPC's recorded decision for one turn (sprint 045, first slice).

039's ``choose`` decides what an NPC pursues now; nothing kept it. A ``DecisionRecord`` is that
choice written down for one scene turn: every approach the policy considered and why (its outcome,
the conditions it lacked, the limits that forbade it), the one taken, the authored order's own pick,
how it was taken (``policy``: the authored order; ``model``: a bounded ``decision`` call's pick
among the possible approaches) and the **evidence** it rests on: what the NPC held, and who it saw,
for the chosen approach and for every limit that applied.

A record is the turn's decision for good. ``replay`` rebuilds the very ``Choice`` from the agenda,
the NPC's knowledge and the scene, and refuses rather than decide again: a retried narration (a
reroll) gets the recorded choice or a refusal, never a new one. Pure and in memory; the record family
that keeps it is ``grimoire.decisionstore``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass

from grimoire.core.identity import InstanceRef, TurnRef
from grimoire.core.model import ControlAssignment
from grimoire.core.perspective import ActorKnowledge
from grimoire.goals import OUTCOMES, STANCES, Agenda, Choice, GoalError, Present, choose
from grimoire.goals.model import Condition

#: How a decision was taken: the authored order, or a bounded model pick among what it allows.
METHODS = ("policy", "model")
#: What a model-method record may say about its call when it fell back to the authored order.
_MAX_REASON = 64


class DecisionError(ValueError):
    """A malformed record or a refused replay; ``reason`` is machine-readable. Messages cite ids only."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.message))


@dataclass(frozen=True, slots=True)
class Evidence:
    """One thing the decision rests on: a proposition the NPC held (with its current stance, or
    ``aware``), or an actor it saw present. ``supports`` is the goal/approach or limit it was read for."""

    supports: str
    proposition_id: str | None = None
    stance: str | None = None
    actor: InstanceRef | None = None

    def __post_init__(self) -> None:
        if type(self.supports) is not str or not self.supports:
            raise DecisionError("invalid_field", "evidence names what it supports")
        if self.actor is not None:
            if not isinstance(self.actor, InstanceRef) or self.proposition_id is not None or self.stance is not None:
                raise DecisionError("invalid_field", "evidence of presence names one actor and nothing else")
        elif type(self.proposition_id) is not str or self.stance not in STANCES:
            raise DecisionError("invalid_field", "evidence of a belief names its proposition and a stance")


@dataclass(frozen=True, slots=True)
class Candidate:
    """One approach as the policy considered it: 039's ``Considered``, with conditions as text ids."""

    goal_id: str
    approach_id: str
    outcome: str
    unmet: tuple[str, ...] = ()
    forbidden_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One NPC's decision for one scene turn. Build it with ``record_decision``; read one back with the codec."""

    turn: TurnRef
    actor: InstanceRef
    method: str
    as_of: int
    agenda_digest: str
    candidates: tuple[Candidate, ...]
    chosen: tuple[str, str] | None
    default: tuple[str, str] | None
    evidence: tuple[Evidence, ...]
    call: str | None = None  # the decision call's id, for a model-method record
    fallback: str | None = None  # why a model-method call left the authored order's pick in place

    def __post_init__(self) -> None:
        if not isinstance(self.turn, TurnRef) or not isinstance(self.actor, InstanceRef):
            raise DecisionError("invalid_field", "a decision names a TurnRef and an InstanceRef")
        if self.actor.campaign_id != self.turn.campaign_id:
            raise DecisionError("campaign_mismatch", f"{self.actor} is not in campaign {self.turn.campaign_id}")
        if self.method not in METHODS:
            raise DecisionError("invalid_field", "method must be policy or model")
        if isinstance(self.as_of, bool) or not isinstance(self.as_of, int) or self.as_of < 0:
            raise DecisionError("invalid_field", "as_of must be a sequence number")
        if not (isinstance(self.agenda_digest, str) and self.agenda_digest.startswith("sha256:")
                and len(self.agenda_digest) == 71):
            raise DecisionError("invalid_field", "agenda_digest must be a sha256 digest")
        if not all(type(entry) is Candidate and entry.outcome in OUTCOMES for entry in self.candidates):
            raise DecisionError("invalid_field", "candidates must be Candidate values with known outcomes")
        chosen = [(entry.goal_id, entry.approach_id) for entry in self.candidates if entry.outcome == "chosen"]
        feasible = [(entry.goal_id, entry.approach_id) for entry in self.candidates
                    if entry.outcome in ("chosen", "outranked")]
        if chosen != ([self.chosen] if self.chosen is not None else []):
            raise DecisionError("invalid_field", "chosen must be the one candidate whose outcome is chosen")
        if self.default != (feasible[0] if feasible else None):
            raise DecisionError("invalid_field", "default must be the first possible and permitted candidate")
        if self.method == "policy" and (self.chosen != self.default or self.call is not None
                                        or self.fallback is not None):
            raise DecisionError("invalid_field", "a policy decision takes the authored order's pick and makes no call")
        if self.method == "model":
            if not isinstance(self.call, str) or not self.call:
                raise DecisionError("invalid_field", "a model decision names its call")
            if self.fallback is not None and (self.chosen != self.default or not isinstance(self.fallback, str)
                                              or not 0 < len(self.fallback) <= _MAX_REASON):
                raise DecisionError("invalid_field", "a fallback keeps the authored order's pick, with a short reason")
        if not all(type(item) is Evidence for item in self.evidence):
            raise DecisionError("invalid_field", "evidence must be Evidence values")

    @property
    def acts(self) -> bool:
        return self.chosen is not None

    @property
    def selected(self) -> bool:
        """Whether a model call's pick was taken (it may coincide with the authored order's)."""
        return self.method == "model" and self.fallback is None


def _condition(condition: Condition) -> dict[str, object]:
    if isinstance(condition, Present):
        return {"present": str(condition.actor)}
    return {"holds": condition.proposition_id, "stances": sorted(condition.stances)}


def agenda_digest(agenda: Agenda) -> str:
    """A digest of everything in ``agenda`` the policy reads, in a hash-seed-independent order.

    A record names the agenda it was decided under by this digest: an agenda edited since (a goal
    re-prioritised, a limit added) refuses ``replay`` rather than quietly re-deciding.
    """
    if type(agenda) is not Agenda:
        raise DecisionError("bad_agenda", "agenda_digest needs an Agenda")
    document = {
        "campaign": agenda.campaign_id, "actor": str(agenda.actor),
        "goals": [{"id": goal.goal_id, "aim": goal.aim, "priority": goal.priority,
                   "approaches": [{"id": approach.approach_id, "action": approach.action,
                                   "needs": [_condition(need) for need in approach.needs],
                                   "tags": sorted(approach.tags)} for approach in goal.approaches]}
                  for goal in agenda.goals],
        "limits": [{"id": limit.limit_id, "rule": limit.rule, "forbids": sorted(limit.forbids),
                    "when": [_condition(when) for when in limit.when]} for limit in agenda.limits],
    }
    return "sha256:" + hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()


def _canonical(value: object) -> str:
    """An unambiguous text form: length-prefixed text, keys sorted, lists in order."""
    if value is None:
        return "n"
    if type(value) is bool:
        return "t" if value else "f"
    if type(value) is int:
        return f"i{value};"
    if type(value) is str:
        return f"s{len(value)}:{value}"
    if isinstance(value, (list, tuple)):
        return "[" + "".join(_canonical(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + "".join(_canonical(key) + _canonical(value[key]) for key in sorted(value)) + "}"
    raise DecisionError("bad_agenda", f"cannot digest {type(value).__name__}")


def _evidence(condition: Condition, supports: str, knowledge: ActorKnowledge) -> Evidence:
    if isinstance(condition, Present):
        return Evidence(supports, actor=condition.actor)
    stance = next((belief.stance for belief in knowledge.current
                   if belief.proposition_id == condition.proposition_id and belief.stance in condition.stances),
                  None)
    return Evidence(supports, proposition_id=condition.proposition_id, stance=stance or "aware")


def _describe(condition: Condition) -> str:
    if isinstance(condition, Present):
        return f"present:{condition.actor}"
    return f"holds:{condition.proposition_id}:{'|'.join(sorted(condition.stances))}"


def record_decision(choice: Choice, turn: TurnRef, *, call: str | None = None,
                    fallback: str | None = None) -> DecisionRecord:
    """``choice`` written down as the decision of ``turn``.

    With no ``call`` it is a ``policy`` decision and the choice must be the authored order's
    (``bad_choice`` for a selected one). With ``call`` (the planned decision call's id) it is a
    ``model`` decision: the choice is that call's pick, or, with ``fallback`` (why the call gave none),
    the authored order's. Evidence is what the chosen approach needed and what every limit that applied
    was gated on, read from the very projection the choice was made from.
    """
    if type(choice) is not Choice:
        raise DecisionError("bad_choice", "record_decision needs a Choice from choose()")
    if not isinstance(turn, TurnRef) or turn.campaign_id != choice.agenda.campaign_id:
        raise DecisionError("campaign_mismatch", "the turn is not in the choice's campaign")
    if call is None:
        if choice.selection is not None or fallback is not None:
            raise DecisionError("bad_choice", "a selected choice or a fallback needs the decision call")
        method = "policy"
    else:
        if (choice.selection is None) != (fallback is not None):
            raise DecisionError("bad_choice", "a model decision is either the call's pick or a fallback")
        method = "model"
    knowledge = choice.knowledge
    evidence: list[Evidence] = []
    goal, approach = choice.goal, choice.approach
    if goal is not None and approach is not None:
        evidence.extend(_evidence(need, f"{goal.goal_id}/{approach.approach_id}", knowledge)
                        for need in approach.needs)
    for limit in choice.refusals:
        evidence.extend(_evidence(when, f"limit:{limit.limit_id}", knowledge) for when in limit.when)
    candidates = tuple(Candidate(entry.goal_id, entry.approach_id, entry.outcome,
                                 tuple(_describe(condition) for condition in entry.unmet), entry.forbidden_by)
                       for entry in choice.considered)
    default = choice.default
    return DecisionRecord(
        turn, choice.actor, method, choice.as_of, agenda_digest(choice.agenda), candidates,
        (goal.goal_id, approach.approach_id) if goal is not None and approach is not None else None,
        (default.goal_id, default.approach_id) if default is not None else None,
        tuple(evidence), call, fallback)


def replay(record: DecisionRecord, agenda: Agenda, knowledge: ActorKnowledge, *, present: Iterable[InstanceRef],
           controls: Iterable[ControlAssignment], unrecognised: Iterable[InstanceRef] = ()) -> Choice:
    """The recorded decision as a ``Choice`` for this turn's brief, or a refusal; never a new decision.

    Refused (``DecisionError``): ``bad_record``; ``foreign_record`` (another NPC's agenda);
    ``agenda_changed`` (the agenda was edited since); ``decision_changed`` when the NPC's knowledge or
    the scene now gives the authored order another pick, or makes the recorded approach impossible or
    forbidden; and 039's own refusals, under their own reasons. Choosing again is the director's
    explicit act on a new turn, not a reroll's.
    """
    if type(record) is not DecisionRecord:
        raise DecisionError("bad_record", "replay needs a DecisionRecord")
    if type(agenda) is not Agenda or agenda.actor != record.actor:
        raise DecisionError("foreign_record", f"the record is {record.actor}'s, not this agenda's")
    if agenda_digest(agenda) != record.agenda_digest:
        raise DecisionError("agenda_changed", f"{record.actor}'s agenda changed since {record.turn} was decided")
    selected = record.chosen if record.selected else None
    try:
        choice = choose(agenda, knowledge, present=present, controls=controls, unrecognised=unrecognised,
                        selected=selected)
    except GoalError as error:
        if error.reason == "infeasible_selection":
            raise DecisionError("decision_changed", f"{record.actor} can no longer take what it decided in "
                                f"{record.turn}") from None
        raise DecisionError(error.reason, error.message) from None
    goal, approach = choice.goal, choice.approach
    now = (goal.goal_id, approach.approach_id) if goal is not None and approach is not None else None
    if now != record.chosen:
        raise DecisionError("decision_changed", f"{record.actor} would now decide otherwise than it did in "
                            f"{record.turn}")
    return choice
