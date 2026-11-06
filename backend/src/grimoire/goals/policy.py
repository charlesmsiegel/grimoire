"""Choose what one NPC pursues now, deterministically, from its own perspective (sprint 039).

``choose`` is the NPC's policy: its agenda, its own ``ActorKnowledge`` and who is in the scene go in,
and one ``Choice`` comes out. It calls no model and reads no director data, so it costs nothing at
turn time: the choice reaches the NPC's one ``npc_generation`` call through its brief (027), and no
``planning`` or ``decision`` call is ever made for it (docs/INFERENCE.md).

Credibility comes from three rules:

* **No omniscience.** A ``Holds`` condition reads only the NPC's own projection (012): what it was
  told, what it noticed and what it concluded. What is true, or what the director knows, never
  changes a choice. A false rumour the NPC believes moves it; a truth it never heard does not.
* **Limits are not negotiable.** An approach tagged with a tag an active ``Limit`` forbids is never
  chosen, however much its goal matters or however much a plot would like it.
* **Order is authored.** Goals are tried by priority (higher first, ties in authored order), each
  goal's approaches in authored order; the first approach that is possible and permitted is chosen.

A narrative planner's wish (``desired``) is recorded and explained, never obeyed: milestone 2's
"policy-based choice conflicting with a desired plot outcome".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import InitVar, dataclass

from grimoire.core.authority import DIRECTOR, AuthorityError, controller_of
from grimoire.core.identity import InstanceRef
from grimoire.core.model import ControlAssignment
from grimoire.core.perspective import ActorKnowledge

from .model import Agenda, Approach, Condition, Goal, GoalError, Holds, Limit, Present

#: Why an approach was or was not chosen, best first.
OUTCOMES = ("chosen", "outranked", "forbidden", "unmet")
_SEAL = object()


@dataclass(frozen=True, slots=True)
class Considered:
    """One approach as the policy saw it.

    ``unmet`` are the conditions that did not hold (it could not be done now); ``forbidden_by`` the
    limits that forbid it now. ``outcome`` is ``unmet`` if anything is unmet, else ``forbidden`` if any
    limit forbids it, else ``chosen`` for the one chosen and ``outranked`` for the rest.
    """

    goal_id: str
    approach_id: str
    outcome: str
    unmet: tuple[Condition, ...] = ()
    forbidden_by: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Desire:
    """A planner's wished-for approach and what became of it: ``outcome`` is its ``Considered`` outcome."""

    goal_id: str
    approach_id: str
    outcome: str

    @property
    def followed(self) -> bool:
        return self.outcome == "chosen"


@dataclass(frozen=True, slots=True)
class Choice:
    """What one NPC pursues now, and why. Only ``choose`` builds one.

    ``knowledge`` is the very projection the choice was made from: a brief built from another one (a
    later moment, or a reload that quarantined a record) refuses it. ``present`` is the cast it was
    made for, as the caller gave it: the actors this NPC knows to be in the scene (GOALS.md).
    """

    agenda: Agenda
    knowledge: ActorKnowledge
    present: frozenset[InstanceRef]
    considered: tuple[Considered, ...]
    desire: Desire | None = None
    _seal: InitVar[object] = None

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _SEAL:
            raise GoalError("unsealed_choice", "build choices with choose()")

    @property
    def actor(self) -> InstanceRef:
        return self.agenda.actor

    @property
    def as_of(self) -> int:
        return self.knowledge.as_of

    def _chosen(self) -> Considered | None:
        return next((entry for entry in self.considered if entry.outcome == "chosen"), None)

    @property
    def acts(self) -> bool:
        """Whether the NPC has something to pursue now; a scene rule may give it the turn (035)."""
        return self._chosen() is not None

    @property
    def goal(self) -> Goal | None:
        chosen = self._chosen()
        return self.agenda.approach(chosen.goal_id, chosen.approach_id)[0] if chosen else None

    @property
    def approach(self) -> Approach | None:
        chosen = self._chosen()
        return self.agenda.approach(chosen.goal_id, chosen.approach_id)[1] if chosen else None

    @property
    def refusals(self) -> tuple[Limit, ...]:
        """The limits that forbid something the NPC could otherwise do now, in authored order."""
        active = {limit_id for entry in self.considered if entry.outcome == "forbidden"
                  for limit_id in entry.forbidden_by}
        return tuple(limit for limit in self.agenda.limits if limit.limit_id in active)


def held(knowledge: ActorKnowledge) -> frozenset[str]:
    """Propositions the actor holds at all: reports it received or sent, and its beliefs, current or
    earlier (027's ``known_ids``: the same rule decides what it has learned)."""
    beliefs = (*knowledge.current, *knowledge.history, *(knowledge.inherited or ()),
               *(knowledge.inherited_history or ()))
    return frozenset({item.proposition_id for item in knowledge.evidence if item.proposition_id is not None}
                     | {report.proposition_id for report in knowledge.sent}
                     | {belief.proposition_id for belief in beliefs})


def _holds(condition: Present | Holds, knowledge: ActorKnowledge, present: frozenset[InstanceRef],
           aware: frozenset[str]) -> bool:
    if isinstance(condition, Present):
        return condition.actor in present
    if "aware" in condition.stances and condition.proposition_id in aware:
        return True
    # One current belief per proposition (012's chain rule); only the NPC's own, never inherited ones.
    return any(belief.proposition_id == condition.proposition_id and belief.stance in condition.stances
               for belief in knowledge.current)


def _present(present: object, campaign_id: str) -> frozenset[InstanceRef]:
    if isinstance(present, (str, bytes, Mapping)):
        raise GoalError("bad_present", "present must be a collection of InstanceRef values")
    try:
        present = frozenset(present)  # type: ignore[arg-type]
    except TypeError:
        raise GoalError("bad_present", "present must be a collection of InstanceRef values") from None
    if not all(isinstance(ref, InstanceRef) for ref in present):
        raise GoalError("bad_present", "present must hold InstanceRef values")
    if any(ref.campaign_id != campaign_id for ref in present):
        raise GoalError("campaign_mismatch", f"a present actor is not in campaign {campaign_id}")
    return present


def _check_control(actor: InstanceRef, controls: object) -> None:
    if isinstance(controls, (str, bytes, Mapping)):
        raise GoalError("bad_controls", "controls must be ControlAssignment values")
    try:
        controls = tuple(controls)  # type: ignore[arg-type]
    except TypeError:
        raise GoalError("bad_controls", "controls must be ControlAssignment values") from None
    if not all(isinstance(control, ControlAssignment) for control in controls):
        raise GoalError("bad_controls", "controls must be ControlAssignment values")
    try:
        found = controller_of(actor.campaign_id, actor.instance_id, controls)
    except AuthorityError:
        raise GoalError("ambiguous_control", f"{actor} has more than one controller") from None
    if found is None:
        # Fail closed: an actor nobody controls could be a player character waiting for its player.
        raise GoalError("unassigned_actor", f"{actor} has no controller")
    if found != DIRECTOR:
        # Player sovereignty: a player character's intentions are its player's, never a policy's.
        raise GoalError("player_actor", f"{actor} is a player character")


def choose(agenda: Agenda, knowledge: ActorKnowledge, *, present: Iterable[InstanceRef],
           controls: Iterable[ControlAssignment], desired: tuple[str, str] | None = None) -> Choice:
    """What ``agenda``'s NPC pursues now, from its own ``knowledge`` and the scene's cast ``present``.

    Refusals (``GoalError``): ``bad_agenda``; ``foreign_knowledge`` (not exactly an ``ActorKnowledge``,
    or another actor's); ``bad_present`` and ``campaign_mismatch`` for the cast; ``bad_controls``,
    ``ambiguous_control``, ``unassigned_actor`` and ``player_actor`` (only a director-controlled NPC has
    a policy); ``absent_actor`` (the NPC is not in the scene, so it can pursue nothing in it); and
    ``unknown_approach`` for a ``desired`` (goal id, approach id) the agenda does not have.
    """
    if type(agenda) is not Agenda:
        raise GoalError("bad_agenda", "choose needs an Agenda")
    actor = agenda.actor
    if type(knowledge) is not ActorKnowledge:
        # Director knowledge, a player view or a look-alike would let truth the NPC never learned decide.
        raise GoalError("foreign_knowledge", f"{actor} must choose from its own ActorKnowledge")
    if knowledge.actor != actor:
        raise GoalError("foreign_knowledge", f"{actor} was given the knowledge of {knowledge.actor}")
    cast = _present(present, agenda.campaign_id)
    _check_control(actor, controls)
    if actor not in cast:
        raise GoalError("absent_actor", f"{actor} is not in the scene")
    if desired is not None:
        if not (isinstance(desired, tuple) and len(desired) == 2 and all(type(part) is str for part in desired)):
            raise GoalError("unknown_approach", "desired must be a (goal id, approach id) pair")
        agenda.approach(*desired)

    aware = held(knowledge)

    def unmet(conditions: tuple[Condition, ...]) -> tuple[Condition, ...]:
        return tuple(condition for condition in conditions if not _holds(condition, knowledge, cast, aware))

    active = [limit for limit in agenda.limits if not unmet(limit.when)]
    ranked = sorted(enumerate(agenda.goals), key=lambda pair: (-pair[1].priority, pair[0]))
    considered: list[Considered] = []
    chosen = False
    for _index, goal in ranked:
        for approach in goal.approaches:
            missing = unmet(approach.needs)
            forbidden = tuple(limit.limit_id for limit in active if limit.forbids & approach.tags)
            if missing:
                outcome = "unmet"
            elif forbidden:
                outcome = "forbidden"
            elif not chosen:
                outcome, chosen = "chosen", True
            else:
                outcome = "outranked"
            considered.append(Considered(goal.goal_id, approach.approach_id, outcome, missing, forbidden))
    desire = None
    if desired is not None:
        entry = next(entry for entry in considered if (entry.goal_id, entry.approach_id) == desired)
        desire = Desire(entry.goal_id, entry.approach_id, entry.outcome)
    return Choice(agenda, knowledge, cast, tuple(considered), desire, _SEAL)


def explain(choice: Choice) -> tuple[str, ...]:
    """The choice in plain lines, for the director and the table's tools; never for an NPC prompt.

    It names propositions and actors by id, so it is director-side text.
    """
    if not isinstance(choice, Choice):
        raise GoalError("bad_choice", "explain needs a Choice from choose()")
    lines = []
    goal, approach = choice.goal, choice.approach
    if goal is not None and approach is not None:
        lines.append(f"{choice.actor} pursues {goal.goal_id} ({goal.aim}) by {approach.approach_id}: {approach.action}")
    else:
        lines.append(f"{choice.actor} has nothing it can pursue now.")
    desire = choice.desire
    if desire is not None:
        wished, wanted = choice.agenda.approach(desire.goal_id, desire.approach_id)
        name = f"the desired {wished.goal_id}/{wanted.approach_id} ({wanted.action})"
        entry = next(entry for entry in choice.considered
                     if (entry.goal_id, entry.approach_id) == (desire.goal_id, desire.approach_id))
        if desire.followed:
            lines.append(f"It follows {name}.")
        elif desire.outcome == "unmet":
            lines.append(f"It cannot take {name} now; unmet: " + "; ".join(c.describe() for c in entry.unmet) + ".")
        elif desire.outcome == "forbidden":
            rules = [limit for limit in choice.agenda.limits if limit.limit_id in entry.forbidden_by]
            lines.append(f"It refuses {name}: " + "; ".join(f"{limit.limit_id} ({limit.rule})" for limit in rules))
        else:
            lines.append(f"It prefers another approach to {name}: a higher goal, or an earlier approach, comes first.")
    return tuple(lines)
