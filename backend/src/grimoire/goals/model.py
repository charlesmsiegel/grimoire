"""An NPC's authored goals, approaches and limits (sprint 039, first slice).

An ``Agenda`` is what the director authors for one NPC in one campaign: what it is after (``Goal``),
the concrete things it might do about each goal (``Approach``), and what it will not do
(``Limit``). Every condition an approach or limit depends on is typed: an actor present in the scene
(``Present``) or a proposition the NPC itself holds (``Holds``). Nothing here is prose the policy has
to read: the aim, action and rule texts are only shown, to the NPC's own call and to the director.

The values are campaign-scoped on purpose: a ``Holds`` names a proposition of this campaign's
knowledge ledger (012/020), which a reusable source definition cannot. Pure and in memory; nothing
here is stored yet.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.identity import SEGMENT_MAX, InstanceRef, validate_segment

#: How an NPC may hold a proposition. The first three are its current belief's stance; ``aware``
#: is holding it at all: a report received or sent, or a belief of any stance, current or earlier.
STANCES = frozenset({"believed", "suspected", "rejected", "aware"})
#: The default for ``Holds``: the NPC takes it to be true, or suspects it.
CREDITED = frozenset({"believed", "suspected"})

#: Authored texts are short: each is one line shown to one NPC's call.
TEXT_CHARS = 400
PRIORITY_MIN, PRIORITY_MAX = 1, 100
MAX_GOALS = 16
MAX_APPROACHES = 8
MAX_LIMITS = 16
MAX_CONDITIONS = 8
MAX_TAGS = 8
#: Control characters (line breaks and tabs included), surrogates and line or paragraph separators:
#: an authored text is one line of the NPC's brief, and a line break could fake a heading of its own.
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})
#: Bidi embeddings, overrides and isolates, and the byte-order mark, which can disguise what the director
#: reads. Other format characters (zero-width joiners in emoji, ZWNJ in Persian) are ordinary text.
_UNSAFE_FORMAT = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff")


class GoalError(ValueError):
    """An invalid agenda or a refused choice; ``reason`` is machine-readable.

    Messages cite ids and reasons, never authored text, so they are safe to log.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(f"{reason}: {message}")
        self.reason = reason
        self.message = message

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.message))


def _invalid(message: str) -> GoalError:
    return GoalError("invalid_field", message)


def _segment(value: object, label: str) -> str:
    if type(value) is not str:  # a str subclass could redefine equality
        raise _invalid(f"{label} must be plain text")
    try:
        validate_segment(value, label)
    except ValueError:
        # Not the value itself: a mistyped id can hold authored text, and messages are safe to log.
        raise _invalid(f"{label} must be a lowercase id segment of at most {SEGMENT_MAX} characters") from None
    return value


def _text(value: object, label: str) -> str:
    if type(value) is not str or not value.strip():
        raise _invalid(f"{label} must be nonblank text")
    if len(value) > TEXT_CHARS:
        raise _invalid(f"{label} is longer than {TEXT_CHARS} characters")
    if any(char in _UNSAFE_FORMAT or unicodedata.category(char) in _UNSAFE_CATEGORIES for char in value):
        raise _invalid(f"{label} may not hold a line break, control, bidi-override or separator character")
    return value


def _sequence(values: object, label: str, limit: int) -> tuple:
    """``values`` as a tuple: a real collection, never one string or a mapping, at most ``limit`` long."""
    if isinstance(values, (str, bytes, Mapping)):
        raise _invalid(f"{label} must be a collection, not {type(values).__name__}")
    try:
        values = tuple(values)  # type: ignore[arg-type]
    except TypeError:
        raise _invalid(f"{label} must be a collection") from None
    if len(values) > limit:
        raise _invalid(f"{label} holds more than {limit} items")
    return values


def _sequence_any(values: object, label: str) -> tuple:
    """``values`` as a tuple of any length: a real collection, never one string or a mapping."""
    if isinstance(values, (str, bytes, Mapping)):
        raise _invalid(f"{label} must be a collection, not {type(values).__name__}")
    try:
        return tuple(values)  # type: ignore[arg-type]
    except TypeError:
        raise _invalid(f"{label} must be a collection") from None


def _tags(values: object, label: str) -> frozenset[str]:
    tags = _sequence(values, label, MAX_TAGS)
    return frozenset(_segment(tag, label) for tag in tags)


@dataclass(frozen=True, slots=True)
class Present:
    """``actor`` is in the scene as the NPC knows it: the cast the caller gives ``choose`` (see GOALS.md)."""

    actor: InstanceRef

    def __post_init__(self) -> None:
        if not isinstance(self.actor, InstanceRef):
            raise _invalid("Present needs an InstanceRef")

    def describe(self) -> str:
        return f"{self.actor} present"


@dataclass(frozen=True, slots=True)
class Holds:
    """The NPC itself holds ``proposition_id`` in one of ``stances`` (see ``STANCES``)."""

    proposition_id: str
    stances: frozenset[str] = CREDITED

    def __post_init__(self) -> None:
        _segment(self.proposition_id, "proposition_id")
        stances = _sequence(self.stances, "stances", len(STANCES))
        if not stances or not all(type(stance) is str and stance in STANCES for stance in stances):
            raise _invalid(f"stances must be a nonempty subset of {sorted(STANCES)}")
        object.__setattr__(self, "stances", frozenset(stances))

    def describe(self) -> str:
        return f"holds {self.proposition_id} as {' or '.join(sorted(self.stances))}"


Condition = Present | Holds


def _conditions(values: object, label: str) -> tuple[Condition, ...]:
    conditions = _sequence(values, label, MAX_CONDITIONS)
    # Exact types: a subclass could redefine what "holds" means.
    if not all(type(condition) in (Present, Holds) for condition in conditions):
        raise _invalid(f"{label} must be Present or Holds values")
    if len(set(conditions)) != len(conditions):
        raise _invalid(f"{label} repeats a condition")
    return conditions


@dataclass(frozen=True, slots=True)
class Approach:
    """One concrete thing the NPC might do about a goal, possible only when every ``needs`` holds.

    ``tags`` say what kind of act it is (``trade``, ``threat``...), so a ``Limit`` can forbid it.
    """

    approach_id: str
    action: str
    needs: tuple[Condition, ...] = ()
    tags: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        _segment(self.approach_id, "approach_id")
        _text(self.action, "action")
        object.__setattr__(self, "needs", _conditions(self.needs, "needs"))
        object.__setattr__(self, "tags", _tags(self.tags, "tags"))


@dataclass(frozen=True, slots=True)
class Goal:
    """What the NPC is after, how much it matters (higher first) and its approaches, in preference order."""

    goal_id: str
    aim: str
    priority: int
    approaches: tuple[Approach, ...]

    def __post_init__(self) -> None:
        _segment(self.goal_id, "goal_id")
        _text(self.aim, "aim")
        if type(self.priority) is not int or not PRIORITY_MIN <= self.priority <= PRIORITY_MAX:
            raise _invalid(f"priority must be a whole number from {PRIORITY_MIN} to {PRIORITY_MAX}")
        approaches = _sequence(self.approaches, "approaches", MAX_APPROACHES)
        if not approaches or not all(type(approach) is Approach for approach in approaches):
            raise _invalid(f"goal {self.goal_id} needs one or more Approach values")
        ids = [approach.approach_id for approach in approaches]
        if len(set(ids)) != len(ids):
            raise GoalError("duplicate_id", f"goal {self.goal_id} repeats an approach id")
        object.__setattr__(self, "approaches", approaches)


@dataclass(frozen=True, slots=True)
class Limit:
    """Something the NPC will not do: any approach tagged with one of ``forbids``, whenever every
    ``when`` condition holds (always, when there is none)."""

    limit_id: str
    rule: str
    forbids: frozenset[str]
    when: tuple[Condition, ...] = ()

    def __post_init__(self) -> None:
        _segment(self.limit_id, "limit_id")
        _text(self.rule, "rule")
        forbids = _tags(self.forbids, "forbids")
        if not forbids:
            raise _invalid(f"limit {self.limit_id} forbids nothing")
        object.__setattr__(self, "forbids", forbids)
        object.__setattr__(self, "when", _conditions(self.when, "when"))


@dataclass(frozen=True, slots=True)
class Agenda:
    """One NPC's goals and limits in one campaign, as the director authored them."""

    campaign_id: str
    actor: InstanceRef
    goals: tuple[Goal, ...]
    limits: tuple[Limit, ...] = ()

    def __post_init__(self) -> None:
        _segment(self.campaign_id, "campaign_id")
        if not isinstance(self.actor, InstanceRef):
            raise _invalid("an agenda's actor must be an InstanceRef")
        if self.actor.campaign_id != self.campaign_id:
            raise GoalError("campaign_mismatch", f"{self.actor} is not in campaign {self.campaign_id}")
        goals = _sequence(self.goals, "goals", MAX_GOALS)
        if not goals or not all(type(goal) is Goal for goal in goals):
            raise _invalid("an agenda needs one or more Goal values")
        limits = _sequence(self.limits, "limits", MAX_LIMITS)
        if not all(type(limit) is Limit for limit in limits):
            raise _invalid("limits must be Limit values")
        for family, ids in (("goal", [goal.goal_id for goal in goals]),
                            ("limit", [limit.limit_id for limit in limits])):
            if len(set(ids)) != len(ids):
                raise GoalError("duplicate_id", f"the agenda of {self.actor} repeats a {family} id")
        approaches = [approach for goal in goals for approach in goal.approaches]
        tags = frozenset(tag for approach in approaches for tag in approach.tags)
        for limit in limits:
            # A forbidden tag no approach has is a mistyped tag, so part of a safety rule would lapse
            # silently. Ids in ``when`` are checked against the ledger by ``check_agenda``.
            if limit.forbids - tags:
                raise GoalError("unused_limit", f"limit {limit.limit_id} forbids a tag no approach has")
        needs = [condition for approach in approaches for condition in approach.needs]
        for condition in needs + [condition for limit in limits for condition in limit.when]:
            if isinstance(condition, Present):
                if condition.actor.campaign_id != self.campaign_id:
                    raise GoalError("campaign_mismatch", f"{condition.actor} is not in campaign {self.campaign_id}")
                if condition.actor == self.actor:
                    raise _invalid(f"{self.actor} cannot need itself present")
        object.__setattr__(self, "goals", goals)
        object.__setattr__(self, "limits", limits)

    def approach(self, goal_id: str, approach_id: str) -> tuple[Goal, Approach]:
        """The goal and approach so named, or ``unknown_approach``."""
        for goal in self.goals:
            if goal.goal_id == goal_id:
                for approach in goal.approaches:
                    if approach.approach_id == approach_id:
                        return goal, approach
        raise GoalError("unknown_approach", f"{self.actor} has no approach {goal_id}/{approach_id}")



def conditions_of(agenda: Agenda) -> tuple[Condition, ...]:
    """Every condition the agenda names, in approaches' needs and limits' ``when``, in authored order."""
    found = [condition for goal in agenda.goals for approach in goal.approaches for condition in approach.needs]
    found += [condition for limit in agenda.limits for condition in limit.when]
    return tuple(dict.fromkeys(found))


def check_agenda(agenda: Agenda, *, propositions: Iterable[str], actors: Iterable[InstanceRef]) -> None:
    """Refuse an agenda naming a proposition or an actor the campaign does not have.

    A mistyped id in a ``Holds`` or ``Present`` never holds, so an approach needing it is never possible
    and a limit gated on it never applies: a safety rule would lapse silently. ``propositions`` are the
    campaign ledger's proposition ids (director-side) and ``actors`` its actor instances. Run it where an
    agenda is authored, stored or loaded; ``choose`` cannot, as it sees only the NPC's projection.
    Raises ``unknown_proposition`` or ``unknown_actor``, citing the id.
    """
    if type(agenda) is not Agenda:
        raise GoalError("bad_agenda", "check_agenda needs an Agenda")
    known = frozenset(_sequence_any(propositions, "propositions"))
    cast = frozenset(_sequence_any(actors, "actors"))
    if not all(isinstance(ref, InstanceRef) for ref in cast):
        raise _invalid("actors must be InstanceRef values")
    for condition in conditions_of(agenda):
        if isinstance(condition, Holds) and condition.proposition_id not in known:
            raise GoalError("unknown_proposition", f"{agenda.actor} names unknown proposition {condition.proposition_id}")
        if isinstance(condition, Present) and condition.actor not in cast:
            raise GoalError("unknown_actor", f"{agenda.actor} names unknown actor {condition.actor}")
