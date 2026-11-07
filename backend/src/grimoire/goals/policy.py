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

A ``selected`` approach (045's bounded decision, docs/DECISIONS.md) is different: it replaces the
authored order's pick, but only among the approaches the policy found possible and permitted now
(``chosen`` or ``outranked``). It can never reach an approach a condition rules out or a limit forbids.
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
    made for as this NPC recognises it, and ``unrecognised`` who else is in the scene: together they are
    the scene the choice is for, and ``run_turn`` refuses it in a turn with any other (GOALS.md).
    """

    agenda: Agenda
    knowledge: ActorKnowledge
    present: frozenset[InstanceRef]
    considered: tuple[Considered, ...]
    desire: Desire | None = None
    unrecognised: frozenset[InstanceRef] = frozenset()
    #: 045: the (goal id, approach id) a bounded decision picked in place of the authored order, if any.
    selection: tuple[str, str] | None = None
    _seal: InitVar[object] = None

    def __init_subclass__(cls, **kwargs: object) -> None:
        raise GoalError("unsealed_choice", "a Choice cannot be subclassed")

    def __post_init__(self, _seal: object) -> None:
        if _seal is not _SEAL:
            raise GoalError("unsealed_choice", "build choices with choose()")

    @property
    def scene(self) -> frozenset[InstanceRef]:
        """Everyone in the scene the choice was made for: the cast the NPC recognises and the rest."""
        return self.present | self.unrecognised

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
    def default(self) -> Considered | None:
        """The approach the authored order picks: the first possible and permitted one (045)."""
        return next((entry for entry in self.considered if entry.outcome in ("chosen", "outranked")), None)

    @property
    def feasible(self) -> tuple[Considered, ...]:
        """Every approach possible and permitted now, in the order tried: what a decision may pick from."""
        return tuple(entry for entry in self.considered if entry.outcome in ("chosen", "outranked"))

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


def _present(present: object, campaign_id: str, label: str = "present") -> frozenset[InstanceRef]:
    if isinstance(present, (str, bytes, Mapping)):
        raise GoalError("bad_present", f"{label} must be a collection of InstanceRef values")
    try:
        present = frozenset(present)  # type: ignore[arg-type]
    except TypeError:
        raise GoalError("bad_present", f"{label} must be a collection of InstanceRef values") from None
    if not all(isinstance(ref, InstanceRef) for ref in present):
        raise GoalError("bad_present", f"{label} must hold InstanceRef values")
    if any(ref.campaign_id != campaign_id for ref in present):
        raise GoalError("campaign_mismatch", f"an actor in {label} is not in campaign {campaign_id}")
    return present


#: The projection's sequence fields: tuples, so a choice cannot go stale under the brief's equality check.
_KNOWLEDGE_FIELDS = ("evidence", "sent", "current", "history", "inherited", "inherited_history")


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


def _pair(value: object, label: str) -> tuple[str, str]:
    if not (type(value) is tuple and len(value) == 2 and all(type(part) is str for part in value)):
        raise GoalError("unknown_approach", f"{label} must be a (goal id, approach id) pair")
    return value  # type: ignore[return-value]


def choose(agenda: Agenda, knowledge: ActorKnowledge, *, present: Iterable[InstanceRef],
           controls: Iterable[ControlAssignment], desired: tuple[str, str] | None = None,
           unrecognised: Iterable[InstanceRef] = (), selected: tuple[str, str] | None = None) -> Choice:
    """What ``agenda``'s NPC pursues now, from its own ``knowledge`` and the scene's cast ``present``.

    ``present`` is the cast as this NPC recognises it; ``unrecognised`` the rest of the scene (an actor in
    disguise, say), which no condition sees. A choice is for one scene: ``run_turn`` refuses it in a turn
    whose plan has anyone else present, or not everyone (``intention_cast_mismatch``).

    Refusals (``GoalError``): ``bad_agenda``; ``foreign_knowledge`` (not exactly an ``ActorKnowledge``,
    or another actor's); ``bad_present`` and ``campaign_mismatch`` for the cast (``bad_present`` too for
    an ``unrecognised`` actor also in ``present``, or the NPC itself); ``bad_controls``,
    ``ambiguous_control``, ``unassigned_actor`` and ``player_actor`` (only a director-controlled NPC has
    a policy); ``absent_actor`` (the NPC is not in the scene, so it can pursue nothing in it); and
    ``unknown_approach`` for a ``desired`` (goal id, approach id) the agenda does not have.

    ``selected`` (045) is a bounded decision's pick: that approach is chosen in place of the authored
    order's first, and the order's pick becomes ``outranked``. It must name an approach of the agenda
    (``unknown_approach``) that is possible and permitted now (``infeasible_selection`` when a condition
    is unmet or a limit forbids it): a decision chooses among what the policy allows, never past it.
    """
    if type(agenda) is not Agenda:
        raise GoalError("bad_agenda", "choose needs an Agenda")
    actor = agenda.actor
    if type(knowledge) is not ActorKnowledge:
        # Director knowledge, a player view or a look-alike would let truth the NPC never learned decide.
        raise GoalError("foreign_knowledge", f"{actor} must choose from its own ActorKnowledge")
    if knowledge.actor != actor:
        raise GoalError("foreign_knowledge", f"{actor} was given the knowledge of {knowledge.actor}")
    if not all(type(value) is tuple or (value is None and name.startswith("inherited"))
               for name in _KNOWLEDGE_FIELDS for value in (getattr(knowledge, name),)):
        # A list field could change after the choice, and the brief would still find the same object equal.
        raise GoalError("foreign_knowledge", f"{actor} must choose from an immutable projection")
    cast = _present(present, agenda.campaign_id)
    unseen = _present(unrecognised, agenda.campaign_id, "unrecognised")
    if unseen & cast or actor in unseen:
        raise GoalError("bad_present", "an unrecognised actor cannot also be in the cast, or be the NPC itself")
    _check_control(actor, controls)
    if actor not in cast:
        raise GoalError("absent_actor", f"{actor} is not in the scene")
    if desired is not None:
        agenda.approach(*_pair(desired, "desired"))
    if selected is not None:
        agenda.approach(*_pair(selected, "selected"))

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
            elif not chosen and (selected is None or selected == (goal.goal_id, approach.approach_id)):
                outcome, chosen = "chosen", True
            else:
                outcome = "outranked"
            considered.append(Considered(goal.goal_id, approach.approach_id, outcome, missing, forbidden))
    if selected is not None and not chosen:
        # Unmet or forbidden: a decision may reorder what the policy allows, never reach past a limit.
        raise GoalError("infeasible_selection", f"{actor} cannot take {selected[0]}/{selected[1]} now")
    desire = None
    if desired is not None:
        entry = next(entry for entry in considered if (entry.goal_id, entry.approach_id) == desired)
        desire = Desire(entry.goal_id, entry.approach_id, entry.outcome)
    return Choice(agenda, knowledge, cast, tuple(considered), desire, unseen, selected, _SEAL)


def explain(choice: Choice) -> tuple[str, ...]:
    """The choice in plain lines, for the director and the table's tools; never for an NPC prompt.

    It names propositions and actors by id, so it is director-side text.
    """
    if type(choice) is not Choice:
        raise GoalError("bad_choice", "explain needs a Choice from choose()")
    lines = []
    goal, approach = choice.goal, choice.approach
    if goal is not None and approach is not None:
        lines.append(f"{choice.actor} pursues {goal.goal_id} ({goal.aim}) by {approach.approach_id}: {approach.action}")
    else:
        lines.append(f"{choice.actor} has nothing it can pursue now.")
    default = choice.default
    if choice.selection is not None and default is not None:
        if (default.goal_id, default.approach_id) == choice.selection:
            lines.append(f"A decision picked it among {len(choice.feasible)} possible approaches; "
                         "the authored order would have picked it too.")
        else:
            lines.append(f"A decision picked it among {len(choice.feasible)} possible approaches, in place of "
                         f"{default.goal_id}/{default.approach_id}, the authored order's pick.")
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
