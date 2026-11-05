"""Typed campaign relations: what is objectively connected to what, and when (Sprint 017).

A ``Relation`` is director truth about two individuals of one campaign: a typed predicate,
directed or symmetric, valid over an interval of campaign time (012 ``Moment.sequence``).
Ending ("left the guild at 12") and retraction ("this was never true") are different,
non-destructive operations; nothing is ever deleted. A relation is never evidence: being
connected grants no actor any knowledge (see ``claims``).

Errors carry a reason and relation ids, never record text, so they are safe to log.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from grimoire.core.identity import InstanceRef, RelationRef
from grimoire.core.json_value import JsonObject
from grimoire.core.knowledge import Moment

WORLD_FACT = "world_fact"


class GraphError(ValueError):
    """A relation, claim or query that would break a graph invariant. ``reason`` is machine-readable."""

    def __init__(self, reason: str, records: Iterable[str] = ()) -> None:
        self.reason = reason
        self.records: tuple[str, ...] = tuple(records)
        super().__init__(f"{reason}: {', '.join(self.records)}" if self.records else reason)

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.records))


@dataclass(frozen=True, slots=True)
class Predicate:
    name: str
    symmetric: bool
    domain: str = WORLD_FACT


PREDICATES: Mapping[str, Predicate] = MappingProxyType({p.name: p for p in (
    Predicate("member_of", False),    # subject is a member of object (an organization, group, crew)
    Predicate("located_in", False),   # subject is in object (a place, a container)
    Predicate("owns", False),         # subject owns object
    Predicate("carries", False),      # subject is carrying object
    Predicate("knows", False),        # subject is acquainted with object; grants no knowledge of its facts
    Predicate("has_met", True),
    Predicate("kin_of", True),
    Predicate("allied_with", True),
)})


def _invalid(record: str | None = None) -> GraphError:
    return GraphError("invalid_field", (record,) if record else ())


def _text(value: object, record: str | None = None) -> None:
    if type(value) is not str or not value.strip():
        raise _invalid(record)


def _moment(value: object, record: str | None = None) -> None:
    if type(value) is not Moment:
        raise _invalid(record)


@dataclass(frozen=True, slots=True)
class Ending:
    """The relation stopped holding at ``until.sequence`` (exclusive); who said so and when."""

    until: Moment
    provenance: str
    at: str

    def __post_init__(self) -> None:
        _moment(self.until)
        _text(self.provenance)
        _text(self.at)


@dataclass(frozen=True, slots=True)
class Retraction:
    """The record was wrong: a tombstone, kept on disk and never walked."""

    provenance: str
    at: str

    def __post_init__(self) -> None:
        _text(self.provenance)
        _text(self.at)


@dataclass(frozen=True, slots=True)
class Reopening:
    """An ending that was revoked: the relation holds again as if it had never ended (021's undo).

    ``ending`` is the cancelled ending, kept verbatim; ``provenance`` and ``at`` say who reopened it and when.
    """

    ending: Ending
    provenance: str
    at: str

    def __post_init__(self) -> None:
        if type(self.ending) is not Ending:
            raise _invalid()
        _text(self.provenance)
        _text(self.at)


def _empty() -> JsonObject:
    return JsonObject({})


@dataclass(frozen=True, slots=True)
class Relation:
    """One canonical typed relation. ``provenance``, ``recorded_at``, ``since`` and endpoints never change."""

    ref: RelationRef
    predicate: str
    subject: InstanceRef
    object: InstanceRef
    since: Moment
    provenance: str
    recorded_at: str
    ending: Ending | None = None
    retraction: Retraction | None = None
    extras: JsonObject = field(default_factory=_empty)
    reopenings: tuple[Reopening, ...] = ()  # cancelled endings, oldest first (021); last so 017 calls still fit

    def __post_init__(self) -> None:
        if type(self.ref) is not RelationRef:
            raise _invalid()
        rid = self.ref.relation_id
        if type(self.predicate) is not str:
            raise _invalid(rid)
        if self.predicate not in PREDICATES:
            raise GraphError("unknown_predicate", (rid,))
        for endpoint in (self.subject, self.object):
            if type(endpoint) is not InstanceRef:
                raise _invalid(rid)
            if endpoint.campaign_id != self.ref.campaign_id:
                raise GraphError("campaign_mismatch", (rid,))
        if self.subject == self.object:
            raise GraphError("self_relation", (rid,))
        _moment(self.since, rid)
        _text(self.provenance, rid)
        _text(self.recorded_at, rid)
        if self.ending is not None:
            if type(self.ending) is not Ending:
                raise _invalid(rid)
            if self.ending.until.sequence <= self.since.sequence:
                raise GraphError("invalid_until", (rid,))
        if self.retraction is not None and type(self.retraction) is not Retraction:
            raise _invalid(rid)
        if type(self.extras) is not JsonObject:
            raise _invalid(rid)  # unknown authored fields arrive as a JsonObject (the codec builds it)
        if type(self.reopenings) is not tuple:
            raise _invalid(rid)
        if self.reopenings and "reopenings" in self.extras:  # an older file's own key: never overwritten (021)
            raise GraphError("reserved_key", (rid,))
        for reopening in self.reopenings:
            if type(reopening) is not Reopening:
                raise _invalid(rid)
            if reopening.ending.until.sequence <= self.since.sequence:
                raise GraphError("invalid_until", (rid,))

    @property
    def symmetric(self) -> bool:
        return PREDICATES[self.predicate].symmetric

    def endpoints(self) -> frozenset[InstanceRef]:
        return frozenset({self.subject, self.object})

    def interval(self) -> tuple[int, int | None]:
        return (self.since.sequence, None if self.ending is None else self.ending.until.sequence)

    def holds_at(self, sequence: int) -> bool:
        start, end = self.interval()
        return self.retraction is None and start <= sequence and (end is None or sequence < end)

    def same_fact(self, other: Relation) -> bool:
        """Same predicate and endpoints (unordered for symmetric predicates)."""
        if self.predicate != other.predicate:
            return False
        if self.symmetric:
            return self.endpoints() == other.endpoints()
        return (self.subject, self.object) == (other.subject, other.object)

    def overlaps(self, other: Relation) -> bool:
        """The same fact recorded twice over intersecting intervals, neither retracted."""
        if self.retraction is not None or other.retraction is not None or not self.same_fact(other):
            return False
        (a_start, a_end), (b_start, b_end) = self.interval(), other.interval()
        return (a_end is None or b_start < a_end) and (b_end is None or a_start < b_end)

    def ended(self, ending: Ending) -> Relation:
        if self.retraction is not None:
            raise GraphError("already_retracted", (self.ref.relation_id,))
        if self.ending is not None:
            raise GraphError("already_ended", (self.ref.relation_id,))
        return replace(self, ending=ending)

    def reopened(self, reopening: Reopening) -> Relation:
        """Cancel the current ending (keeping it in ``reopenings``); the relation holds again from ``since``."""
        if self.retraction is not None:
            raise GraphError("already_retracted", (self.ref.relation_id,))
        if self.ending is None or reopening.ending != self.ending:
            raise GraphError("not_ended", (self.ref.relation_id,))
        return replace(self, ending=None, reopenings=(*self.reopenings, reopening))

    def retracted(self, retraction: Retraction) -> Relation:
        if self.retraction is not None:
            raise GraphError("already_retracted", (self.ref.relation_id,))
        return replace(self, retraction=retraction)


@dataclass(frozen=True, slots=True)
class UnsupportedRelation:
    """A relation file whose predicate this version does not know: kept, reported, never walked or rewritten."""

    ref: RelationRef
    predicate: str
    payload: JsonObject
    endpoints: tuple[InstanceRef, ...] = ()
