"""A player character's action against a 040 obstacle, in play (035 milestone 2).

**Labelling.** A press may name an obstacle (``label(chat, by, obstacle=...)``). The labeller is then shown the
obstacle's goal and suggested approaches and chooses one, or a novel approach (``NOVEL``); it never decides.
The frame's obstacle part is the author's, added by rule: for a suggestion, exactly the facts the author said
bear on it, with the author's bearing, and exactly its risks as stakes; for a novel approach, every fact that
bears on every approach (``"*"``), with the labeller's own stakes as its risks. The labeller adds only its own
factors, cited from the acting character's facts, as for any action.

**Confirming** frames the attempt through the runner's ``ObstacleLibrary``, which links it to the obstacle and
then records the 029 frame (``play/adjudicate.py``). Deciding stays 029's.

**Effects.** Once decided, the consequences the obstacle's state says this attempt brought (``due()``: the stakes
that came true and, on a success, the approach's costs) carry their authored structured changes. Every effect
proposed for the turn holds them as 021 operations and holdings lines, ahead of anything the proposer adds,
with the acting character for ``ACTOR``; an acceptance checks they are still there (``due_missing``), so a
turn's effect never leaves out what the obstacle made due. No model proposes them, and they are accepted only
with every key, as any effect. Which obstacle a turn attempts is read from the obstacle ledger's own link and
checked against the turn's ``against`` (``obstacle_link_mismatch``). A due change the world cannot take (the
relation holds already, the actor cannot pay, the character changed hands) is waived by the director, by its id
and with a reason, on the director's own proposal (``Waiver``); the waivers are part of what is keyed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from grimoire.adjudication import MAX_FACTORS, PROSE_MAX, Factor, Stake
from grimoire.core import DIRECTOR
from grimoire.effects import ActedLine, EffectError, EffectProposal, Op
from grimoire.holdings import HoldingsError
from grimoire.labelling import NOVEL, LabelRefused
from grimoire.labelling import Obstacle as Offered
from grimoire.obstacles import ACTOR, Approach, Attempt, ObstacleState, RelationChange, required_factors
from grimoire.play.adjudicate import _029
from grimoire.play.scene import _refuse
from grimoire.play.tracing import span
from grimoire.scene import Against, Label, TurnRecord, due_change_id

#: Ids the labelling itself hands out (its cited facts ``f<n>`` and its own stakes ``s<n>``): an obstacle fact or
#: stake with one would be indistinguishable from the labeller's in the frame.
_LABELLING_IDS = re.compile(r"[fs][0-9]+")


def _library(runner):
    if runner.obstacles is None:
        raise _refuse("no_obstacles", "no obstacle library is configured")
    return runner.obstacles


def open_obstacle(runner, campaign_id: str, obstacle_id: object) -> ObstacleState:
    """The named obstacle as it stands, if an attempt may be labelled against it now."""
    if type(obstacle_id) is not str:
        raise _refuse("invalid_field", "an obstacle is named by its id")
    state = _029(_library(runner).state, campaign_id, obstacle_id)  # unknown_obstacle, or a ledger failure
    if state.status == "overcome":
        raise _refuse("already_overcome", "this obstacle is overcome already")
    if state.status == "diverged":
        raise _refuse("obstacle_diverged", f"this obstacle no longer agrees with its attempts ({state.problem})")
    pending = state.pending  # as the library refuses a frame then (attempt_pending), before any call is spent
    if pending is not None and pending.frame is not None and _029(
            _library(runner).adjudication.undecidable, campaign_id, pending.attempt.attempt_id) is None:
        raise _refuse("attempt_pending", f"attempt {pending.attempt.attempt_id} at this obstacle is undecided; "
                                         "the director decides it first")
    obstacle = state.obstacle
    clashing = sorted({each for each in (*(fact.fact_id for fact in state.facts),
                                         *(stake.stake_id for stake in obstacle.stakes))
                       if _LABELLING_IDS.fullmatch(each)} | ({NOVEL} if obstacle.approach(NOVEL) else set()))
    if clashing:
        raise _refuse("obstacle_ids_clash", "the obstacle uses ids labelling hands out: " + ", ".join(clashing)[:120])
    return state


def check_moment(state: ObstacleState, sequence: int) -> None:
    """An attempt comes after every authored fact and after the obstacle's latest attempt (040)."""
    if sequence <= state.obstacle.opens:
        raise _refuse("premature", "the obstacle's facts are established after this action's moment")
    if state.after is not None and sequence <= state.after:
        raise _refuse("out_of_order", "the obstacle was attempted after this action's moment")


def offered(state: ObstacleState, fixed: Attempt | None = None) -> Offered:
    """What the labeller is told of the obstacle: its goal and suggestions, never its facts. With ``fixed`` (the
    attempt the ledger already links this action to), only the approach it took, and the reply is told to
    take it: for a novel one, no suggestion, and how many stakes it lists (its risks are their ids)."""
    obstacle = state.obstacle
    if fixed is None:
        return Offered(obstacle.goal, tuple((a.approach_id, a.text) for a in obstacle.approaches))
    if not fixed.listed:  # a novel approach risks the labelling's own stakes by id: s1..sN, so N is fixed too
        return Offered(obstacle.goal, (), NOVEL, len(fixed.approach.risks))
    kept = tuple((a.approach_id, a.text) for a in obstacle.approaches if a.approach_id == fixed.approach.approach_id)
    return Offered(obstacle.goal, kept, fixed.approach.approach_id)


def fixed_attempt(state: ObstacleState, record: TurnRecord) -> Attempt | None:
    """The attempt the obstacle ledger links ``record``'s action to while it stands (not void), or ``None``.

    Its link was written with its approach (a confirm cut short before 029 framed it), and a frame for it is a
    revision that keeps that approach (``approach_changed``), so a relabel of the action keeps it too."""
    return next((each.attempt for each in state.attempts
                 if each.attempt.attempt_id == record.turn_id and each.status != "void"), None)


def novel_approach(intent: str, stakes) -> Approach:
    """The approach a novel attempt takes: the action itself, risking exactly the labelling's own stakes."""
    return Approach(NOVEL, intent[:PROSE_MAX], frozenset(stake.stake_id for stake in stakes))


def attempt_labels(state: ObstacleState, chosen: str, sequence: int, intent: str, factors, stakes
                   ) -> tuple[Against, tuple[Factor, ...], tuple[Stake, ...], Approach, bool]:
    """The labelling's frame against the obstacle: the author's part added to the labeller's own factors. A frame
    with more factors than 029 records is ``too_many_factors`` (refused like any reply over the limit)."""
    obstacle = state.obstacle
    if chosen == NOVEL:
        approach, listed = novel_approach(intent, stakes), False
    else:
        approach, listed = obstacle.approach(chosen), True  # type: ignore[assignment]
        stakes = tuple(obstacle.stake(risk).stake for risk in sorted(approach.risks))  # type: ignore[union-attr]
    combined = tuple(sorted((*required_factors(state.facts, approach.approach_id, sequence), *factors),
                            key=lambda factor: factor.factor_id))
    if len(combined) > MAX_FACTORS:
        raise LabelRefused("too_many_factors", f"the obstacle's facts and the labeller's exceed {MAX_FACTORS}")
    return Against(obstacle.obstacle_id, approach.approach_id, not listed), combined, tuple(stakes), approach, listed


def approach_of(record: TurnRecord, label: Label) -> str | Approach:
    """The approach a framed labelling takes, as ``ObstacleLibrary.frame`` names it."""
    against = label.against
    assert against is not None and against.approach_id is not None
    if not against.novel:
        return against.approach_id
    return Approach(against.approach_id, record.post.text[:PROSE_MAX],  # type: ignore[union-attr]
                    frozenset(stake.stake_id for stake in label.stakes))


# -- effects ----------------------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Due:
    """One change the turn's obstacle attempt made due: its id (``due_change_id``) and the 021 operation or the
    holdings line it is, with the acting character in place of ``ACTOR``; or, where 021 or 023 refuses it for this
    actor (a relation of the actor with themself), neither, with that refusal's reason as ``problem``."""

    change_id: str
    op: Op | None = None
    line: ActedLine | None = None
    problem: str | None = None


def _linked_obstacle(runner, record: TurnRecord) -> str | None:
    """The obstacle the turn's action attempts, by the obstacle ledger's own link, checked against the turn's
    record of it: a turn file edited to drop or change ``against`` never hides what the attempt made due."""
    label = record.framed
    against = None if label is None else label.against
    if runner.obstacles is None:
        if against is not None:
            raise _refuse("no_obstacles", "no obstacle library is configured")
        return None  # no obstacle ledger to read (the runner's configuration)
    if against is None:  # a void attempt (superseded) makes nothing due: the turn attempts nothing
        if live_link(runner, record.chat.campaign_id, record.turn_id) is not None:
            raise _refuse("obstacle_link_mismatch", "the obstacle ledger links this action to an obstacle the "
                                                    "turn does not name")
        return None
    linked = _029(runner.obstacles.linked, record.chat.campaign_id).get(record.turn_id)
    if against.obstacle_id != linked:
        raise _refuse("obstacle_link_mismatch", "the turn and the obstacle ledger disagree on what this action "
                                                "attempts")
    return linked


def live_link(runner, campaign_id: str, turn_id: str) -> str | None:
    """The obstacle the ledger links ``turn_id``'s attempt to, unless that attempt is void (a later one
    superseded it); ``None`` without an obstacle library."""
    if runner.obstacles is None:
        return None
    linked = _029(runner.obstacles.linked, campaign_id).get(turn_id)
    if linked is None:
        return None
    state = _029(runner.obstacles.state, campaign_id, linked)
    found = next((each for each in state.attempts if each.attempt.attempt_id == turn_id), None)
    return None if found is not None and found.status == "void" else linked


def refuse_void_attempt(runner, record: TurnRecord) -> None:
    """Refuse deciding ``record``'s framed attempt at an obstacle when the obstacle ledger holds that attempt
    ``void`` (``attempt_withdrawn``): withdrawn when its turn passed while 029 could not decide it (a turn write
    cut short after the withdrawal leaves the turn open), or superseded. A turn whose framed labelling names no
    obstacle is a plain action, decided as before, even over a void link (one whose frame was never written,
    relabelled plain); a turn that names an obstacle with no obstacle library is ``no_obstacles``."""
    against = None if record.framed is None else record.framed.against
    if against is None:
        return
    if runner.obstacles is None:
        raise _refuse("no_obstacles", "no obstacle library is configured")
    campaign_id = record.chat.campaign_id
    linked = _029(runner.obstacles.linked, campaign_id).get(record.turn_id)
    if linked is not None and live_link(runner, campaign_id, record.turn_id) is None:
        raise _refuse("attempt_withdrawn", f"this action's attempt at obstacle {linked} was withdrawn or "
                                           "superseded; it is never decided")


def refuse_live_link(runner, record: TurnRecord, obstacle_id: str | None = None) -> None:
    """Refuse labelling or framing ``record``'s action against anything but the obstacle its live attempt is
    linked to (``obstacle_attempt_live``): a plain frame of a linked attempt would leave its obstacle diverged."""
    linked = live_link(runner, record.chat.campaign_id, record.turn_id)
    if linked is not None and linked != obstacle_id:
        raise _refuse("obstacle_attempt_live", f"this action is an attempt at obstacle {linked}; label it against "
                                               "that obstacle")


def pass_attempt(runner, record: TurnRecord) -> None:
    """Let ``record``'s undecided obstacle attempt pass with its turn, or refuse (``attempt_undecided``).

    An attempt 029 could still decide is refused: decide is the open turn's, so passed it never would be, and its
    obstacle would take no other attempt while it stood pending. One 029 cannot decide now (its actor changed
    hands) passes, and is first withdrawn in the obstacle ledger (``ObstacleLibrary.withdraw``), so it stays void
    whatever control does later; so is one diverged by its own frame, which nothing could revise once passed. The
    withdrawal is written before the turn: if it fails nothing passes, and if the turn write fails after it the turn
    stays open with its attempt withdrawn, which is then never decided (``refuse_void_attempt``), and a retry
    finds the attempt void already and only passes the turn.
    """
    campaign_id = record.chat.campaign_id
    linked = None if runner.obstacles is None else _029(runner.obstacles.linked, campaign_id).get(record.turn_id)
    found = None
    if linked is not None:
        state = _029(runner.obstacles.state, campaign_id, linked)
        found = next((each for each in state.attempts if each.attempt.attempt_id == record.turn_id), None)
        if found is not None and found.status == "void":
            return  # superseded or withdrawn already: nothing of it stands
    if _029(runner.adjudication.undecidable, campaign_id, record.turn_id) is None:
        raise _refuse("attempt_undecided", "this action's attempt at an obstacle is undecided; the director "
                                           "decides it (or the player confirms it) first")
    if found is not None and (found.status == "pending"
                              or (found.status == "diverged" and found.problem != "attempt_out_of_turn")):
        with span("write.obstacle"):
            _029(runner.obstacles.withdraw, campaign_id, linked, record.turn_id)


def due_changes(runner, scene, record: TurnRecord) -> tuple[Due, ...]:
    """The changes the turn's obstacle attempt made due, in the order ``due()`` lists them, each with its id;
    none for a turn whose action attempts no obstacle, or an attempt a later one superseded (void)."""
    obstacle_id = _linked_obstacle(runner, record)
    if obstacle_id is None:
        return ()
    label = record.framed
    assert label is not None and label.against is not None
    state = _029(_library(runner).state, record.chat.campaign_id, obstacle_id)
    found = next((each for each in state.attempts if each.attempt.attempt_id == record.turn_id), None)
    if found is None:
        raise _refuse("not_linked", "the obstacle holds no attempt for this action")
    if (found.attempt.approach.approach_id, not found.attempt.listed) != (label.against.approach_id,
                                                                          label.against.novel):
        raise _refuse("obstacle_link_mismatch", "the turn and the obstacle ledger disagree on the approach taken")
    if found.status == "diverged":
        raise _refuse("obstacle_diverged", f"this attempt no longer agrees with its obstacle ({found.problem})")
    if found.status == "pending":
        raise _refuse("undecided_attempt", "this action is framed and not yet decided; decide it first")
    actor = record.post.actor  # type: ignore[union-attr]
    # the actor's own act is backed by the post (its contribution id is the turn id) while a player plays it
    backed = record.turn_id if scene.controller(actor) not in (None, DIRECTOR) else None
    due: list[Due] = []
    for consequence in found.consequences:
        for index, change in enumerate(consequence.changes, start=1):
            change_id = due_change_id(consequence.kind, consequence.consequence_id, index)
            agent = actor if change.agent == ACTOR else change.agent
            backing = backed if agent == actor else None
            try:
                if type(change) is RelationChange:
                    due.append(Due(change_id, op=Op(change.kind, change.predicate,
                                                    actor if change.subject == ACTOR else change.subject,
                                                    actor if change.object == ACTOR else change.object,
                                                    agent, backing)))
                else:
                    due.append(Due(change_id, line=ActedLine(change.line(actor), agent, backing)))
            except (EffectError, HoldingsError) as error:  # the actor is one of the instances it names
                due.append(Due(change_id, problem=error.reason))
    return tuple(due)


def applied(due: tuple[Due, ...], waived: frozenset[str]) -> tuple[tuple[Op, ...], tuple[ActedLine, ...]]:
    """The due operations and holdings lines an effect applies: every due change but those ``waived``. A change
    021 or 023 refuses for this actor, and not waived, refuses the effect with that reason."""
    unknown = sorted(waived - {item.change_id for item in due})
    if unknown:
        raise _refuse("unknown_due_change", "a waiver names no change this turn's attempt made due: "
                                            + ", ".join(unknown)[:120])
    kept = [item for item in due if item.change_id not in waived]
    for item in kept:
        if item.problem is not None:
            raise _refuse(item.problem, f"the due change {item.change_id} cannot be applied to this actor; the "
                                        "director may waive it")
    return (tuple(item.op for item in kept if item.op is not None),
            tuple(item.line for item in kept if item.line is not None))


def _shape(op: Op) -> tuple:
    return op.kind, op.fact(), op.agent


def holds_due(proposal: EffectProposal, due: tuple[Due, ...], waived: frozenset[str]) -> bool:
    """Whether ``proposal`` carries every due change it does not waive: each such operation, and each such
    holdings line first, in order. A waiver of a change that was never due does not hold."""
    if waived - {item.change_id for item in due}:
        return False
    kept = [item for item in due if item.change_id not in waived]
    if any(item.problem is not None for item in kept):
        return False
    held = {_shape(op) for op in proposal.operations}
    given = () if proposal.holdings is None else proposal.holdings.lines
    lines = [item.line for item in kept if item.line is not None]
    return all(_shape(item.op) in held for item in kept if item.op is not None) and [
        (item.line, item.agent) for item in given[:len(lines)]] == [(item.line, item.agent) for item in lines]


__all__ = ["Due", "applied", "approach_of", "attempt_labels", "check_moment", "due_changes", "holds_due", "live_link",
           "novel_approach", "offered", "open_obstacle", "refuse_live_link"]
