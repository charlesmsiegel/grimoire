"""The structured changes an obstacle's consequence makes (040, with 035 milestone 2): authored, never proposed.

A cost or a stake is prose for people. Its ``changes`` say what it does to the world in terms 021 can accept:
a 017 relation that begins or ends (``RelationChange``), or one 023 holdings line (``HoldingsChange``). They are
written by the obstacle's author before any player chose an approach, like its facts, so the price of a
success and the weight of a failure are the author's, never a labeller's or a model's.

The acting character is not known when an obstacle is authored, so a change names it as ``ACTOR``; every
other reference must be one of the obstacle's own ``actors`` (``Obstacle`` checks it). A change's ``agent``
is whose voluntary act it is (the actor who hands over the purse), or ``None`` for something that befalls
them; 021 decides who must endorse it. Pure: nothing here reads files or knows 021's types.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from grimoire.core.identity import InstanceRef
from grimoire.graph import PREDICATES
from grimoire.holdings import HoldingsError, Line

from .errors import ObstacleError

#: Stands for the attempt's acting character in an authored change.
ACTOR = "actor"
#: The 017 relation changes and the 023 line kinds a consequence may make (an ``adjust`` needs the count seen
#: when it is applied, which an author cannot know).
RELATION_KINDS = ("begin", "end")
HOLDINGS_KINDS = ("add", "consume", "move")
#: At most this many changes per cost or stake: 021 takes 32 operations per effect, and one attempt brings
#: at most 8 stakes and 4 costs.
MAX_CHANGES = 2
#: Stand-ins for ``ACTOR`` while a holdings change is checked against 023's line rules.
_PROBES = (InstanceRef("probe", "character", "obstacle-actor"), InstanceRef("probe", "character", "obstacle-other"))


def _invalid(message: str, reason: str = "invalid_field") -> ObstacleError:
    return ObstacleError(message, reason)


def _who(value: object, label: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not ((type(value) is str and value == ACTOR) or type(value) is InstanceRef):
        raise _invalid(f"{label} is {ACTOR!r} or an InstanceRef: {value!r}")


def _text(value: object, label: str) -> None:
    if type(value) is not str:  # a str subclass could redefine equality
        raise _invalid(f"{label} must be plain text: {value!r}")


@dataclass(frozen=True, slots=True)
class RelationChange:
    """A 017 relation that begins or ends: ``subject`` ``predicate`` ``object``, each ``ACTOR`` or a reference."""

    kind: str
    predicate: str
    subject: InstanceRef | str
    object: InstanceRef | str
    agent: InstanceRef | str | None = None

    def __post_init__(self) -> None:
        _text(self.kind, "kind")
        _text(self.predicate, "predicate")
        if self.kind not in RELATION_KINDS:
            raise _invalid(f"a relation change is one of {RELATION_KINDS}: {self.kind!r}")
        if self.predicate not in PREDICATES:
            raise _invalid(f"unknown predicate {self.predicate!r}", "unknown_predicate")
        _who(self.subject, "subject")
        _who(self.object, "object")
        _who(self.agent, "agent", optional=True)
        if self.subject == self.object:
            raise _invalid("a relation joins two different instances", "self_relation")

    def refs(self) -> tuple[InstanceRef, ...]:
        """The references it names besides ``ACTOR``."""
        return tuple(value for value in (self.subject, self.object, self.agent) if type(value) is InstanceRef)


@dataclass(frozen=True, slots=True)
class HoldingsChange:
    """One 023 line: ``add`` or ``consume`` ``amount`` of ``item`` at ``holder``, or ``move`` it to ``target``."""

    kind: str
    holder: InstanceRef | str
    item: str
    amount: int
    target: InstanceRef | str | None = None
    agent: InstanceRef | str | None = None

    def __post_init__(self) -> None:
        _text(self.kind, "kind")
        _text(self.item, "item")
        if self.kind not in HOLDINGS_KINDS:
            raise _invalid(f"a holdings change is one of {HOLDINGS_KINDS}: {self.kind!r}")
        _who(self.holder, "holder")
        _who(self.target, "target", optional=self.kind != "move")
        _who(self.agent, "agent", optional=True)
        if self.kind != "move" and self.target is not None:
            raise _invalid(f"a {self.kind} change has no target")
        if self.kind == "move" and self.holder == self.target:
            raise _invalid("a move goes from one holder to another", "same_holder")
        try:  # 023's own rules on the item and amount, with stand-ins for the actor
            self.line(*_PROBES)
        except HoldingsError as error:
            raise _invalid(f"not a 023 line: {error}") from None

    def line(self, actor: InstanceRef, other: InstanceRef | None = None) -> Line:
        """The 023 line with ``actor`` for ``ACTOR`` (``other`` stands in for it as a move's target, for a probe
        whose holder is a stand-in too)."""
        holder = actor if self.holder == ACTOR else self.holder
        if self.kind == "add":
            return Line.add(holder, self.item, self.amount)  # type: ignore[arg-type]
        if self.kind == "consume":
            return Line.consume(holder, self.item, self.amount)  # type: ignore[arg-type]
        target = (other or actor) if self.target == ACTOR else self.target
        return Line.move(holder, target, self.item, self.amount)  # type: ignore[arg-type]

    def refs(self) -> tuple[InstanceRef, ...]:
        return tuple(value for value in (self.holder, self.target, self.agent) if type(value) is InstanceRef)


Change = RelationChange | HoldingsChange


def check_changes(value: object, label: str) -> tuple[Change, ...]:
    """A cost's or stake's changes as a tuple, in authored order, at most ``MAX_CHANGES``."""
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise _invalid(f"{label} must be a collection: {value!r}")
    changes = tuple(value)
    if not all(type(change) in (RelationChange, HoldingsChange) for change in changes):
        raise _invalid(f"{label} must be RelationChange or HoldingsChange values")
    if len(changes) > MAX_CHANGES:
        raise _invalid(f"at most {MAX_CHANGES} {label}")
    return changes


__all__ = ["ACTOR", "HOLDINGS_KINDS", "MAX_CHANGES", "RELATION_KINDS", "Change", "HoldingsChange", "RelationChange",
           "check_changes"]
