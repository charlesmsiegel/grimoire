"""Party membership and scene presence (Sprint 033): pure values over 017 relations.

Membership is two directed 017 predicates from a member to its **party anchor** (an existing
campaign instance: a group, or the player character or trainer whose party it is):

- ``in_party``: the member travels in the anchor's active party;
- ``in_reserve``: the member is stored for that party (boxed), available but not travelling.

A relation file is the canonical record, so history, taken ids, sync and 021's accepted effects
cover membership with no new record family. This module reads decoded relations and never touches
files. Nothing here grants knowledge or control: being in a party is not being in a scene, and an
owner or trainer relation never puts anyone on stage.

A **scene cast** is an explicit selection of who is present in one scene. It is built by ``stage``,
which refuses a reserved member, a member whose membership is ambiguous, an unknown individual, and
any selection while a relation file cannot be read (fail closed). Only the cast may speak or act as
present.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import InstanceRef, RelationRef, paired_instances, validate_segment
from grimoire.core.knowledge import Moment
from grimoire.graph import Relation

#: The membership predicates and the status each records. Both are directed: member -> party anchor.
MEMBERSHIP: Mapping[str, str] = MappingProxyType({"in_party": "party", "in_reserve": "reserve"})
#: The predicate that records each status.
PREDICATE_FOR: Mapping[str, str] = MappingProxyType({status: name for name, status in MEMBERSHIP.items()})
#: At most this many individuals in one scene cast.
MAX_CAST = 256
#: What ``presence`` can answer.
PRESENCES = ("present", "accompanying", "reserved", "uncertain", "absent")


class PartyRefused(ValueError):
    """A party or cast operation that cannot be made; nothing was written. ``reason`` is machine-readable."""

    def __init__(self, message: str, reason: str, members: tuple[InstanceRef, ...] = ()) -> None:
        super().__init__(message)
        self.reason = reason
        self.members = members

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (str(self), self.reason, self.members))


def _refuse(reason: str, message: str, *members: InstanceRef) -> PartyRefused:
    return PartyRefused(message, reason, tuple(members))


@dataclass(frozen=True, slots=True)
class Membership:
    """Where one member stands at one sequence: in ``party``'s active party or its reserve."""

    member: InstanceRef
    party: InstanceRef
    status: str
    relation: RelationRef
    since: Moment

    def __post_init__(self) -> None:
        if type(self.member) is not InstanceRef or type(self.party) is not InstanceRef:
            raise _refuse("invalid_field", "a membership names two InstanceRefs")
        if self.status not in PREDICATE_FOR:
            raise _refuse("invalid_field", f"unknown membership status {self.status!r}")
        if type(self.relation) is not RelationRef or type(self.since) is not Moment:
            raise _refuse("invalid_field", "a membership cites its relation and since moment")


@dataclass(frozen=True, slots=True)
class PartyProblem:
    """Reported, never repaired. ``conflicting_membership``: a member holds more than one membership relation
    at the state's sequence (a hand edit, two devices, or two accepted effects); ``relations_unreadable``: a
    relation file or folder could not be read, so membership is not fully known."""

    code: str
    member: InstanceRef | None = None
    relation_ids: tuple[str, ...] = ()


def is_membership(relation: object) -> bool:
    """A decoded 017 relation recording party or reserve membership (retracted ones included)."""
    return isinstance(relation, Relation) and relation.predicate in MEMBERSHIP


@dataclass(frozen=True)
class PartyState:
    """Every member's membership at sequence ``as_of`` in one campaign, built from its relation files.

    ``memberships`` holds only unambiguous members; a member with two or more holding membership relations
    is in ``conflicted`` instead and is never stageable. ``complete`` is false when some relation file could
    not be read: a reserve membership might be hiding in it, so ``stage`` refuses until it is repaired.
    """

    campaign_id: str
    as_of: int
    memberships: Mapping[InstanceRef, Membership]
    conflicted: frozenset[InstanceRef] = frozenset()
    problems: tuple[PartyProblem, ...] = ()
    complete: bool = True
    _members_of: Mapping[InstanceRef, tuple[Membership, ...]] = field(default_factory=dict, repr=False,
                                                                       compare=False)

    @classmethod
    def build(cls, campaign_id: str, relations: Iterable[object], *, as_of: int,
              unreadable: bool = False) -> PartyState:
        """``relations`` are the campaign's decoded relations (others are ignored); ``unreadable`` says some
        relation file or the folder could not be read."""
        validate_segment(campaign_id, "campaign_id")
        if type(as_of) is not int or as_of < 0:
            raise _refuse("invalid_field", "as_of must be a non-negative int")
        holding: dict[InstanceRef, list[Relation]] = {}
        for relation in relations:
            if not is_membership(relation):
                continue
            assert isinstance(relation, Relation)
            if relation.ref.campaign_id != campaign_id:
                raise _refuse("cross_campaign", f"{relation.ref} is not in campaign {campaign_id}")
            if relation.holds_at(as_of):
                holding.setdefault(relation.subject, []).append(relation)
        memberships: dict[InstanceRef, Membership] = {}
        problems: list[PartyProblem] = []
        for member in sorted(holding, key=str):
            found = sorted(holding[member], key=lambda r: r.ref.relation_id)
            if len(found) > 1:
                problems.append(PartyProblem("conflicting_membership", member,
                                             tuple(r.ref.relation_id for r in found)))
                continue
            (relation,) = found
            memberships[member] = Membership(member, relation.object, MEMBERSHIP[relation.predicate],
                                             relation.ref, relation.since)
        if unreadable:
            problems.append(PartyProblem("relations_unreadable"))
        conflicted = frozenset(p.member for p in problems if p.member is not None)
        by_party: dict[InstanceRef, list[Membership]] = {}
        for membership in memberships.values():
            by_party.setdefault(membership.party, []).append(membership)
        members_of = {party: tuple(sorted(found, key=lambda m: str(m.member))) for party, found in by_party.items()}
        return cls(campaign_id, as_of, MappingProxyType(memberships), conflicted, tuple(problems), not unreadable,
                   MappingProxyType(members_of))

    def membership(self, member: InstanceRef) -> Membership | None:
        """``member``'s membership, or ``None``. A conflicted member is refused, never guessed."""
        if member in self.conflicted:
            raise _refuse("conflicting_membership", f"{member} holds more than one membership", member)
        return self.memberships.get(member)

    def party(self, anchor: InstanceRef) -> tuple[tuple[InstanceRef, ...], tuple[InstanceRef, ...]]:
        """``anchor``'s active members and its reserve, each sorted. Conflicted members are in neither."""
        found = self._members_of.get(anchor, ())
        return (tuple(m.member for m in found if m.status == "party"),
                tuple(m.member for m in found if m.status == "reserve"))

    def anchors_above(self, anchor: InstanceRef) -> tuple[InstanceRef, ...]:
        """The parties ``anchor`` itself belongs to, transitively (a group in a larger party), nearest first.

        A cycle recorded by hand or by accepted effects stops the walk where it repeats.
        """
        seen: list[InstanceRef] = []
        current = self.memberships.get(anchor)
        while current is not None and current.party not in seen and current.party != anchor:
            seen.append(current.party)
            current = self.memberships.get(current.party)
        return tuple(seen)


@dataclass(frozen=True, slots=True)
class SceneCast:
    """Who is present in one scene at one sequence: exactly the individuals ``stage`` selected, sorted."""

    campaign_id: str
    scene_id: str
    as_of: int
    present: tuple[InstanceRef, ...]

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        validate_segment(self.scene_id, "scene_id")
        if type(self.as_of) is not int or self.as_of < 0:
            raise _refuse("invalid_field", "as_of must be a non-negative int")
        if type(self.present) is not tuple or any(type(ref) is not InstanceRef for ref in self.present):
            raise _refuse("invalid_field", "a cast is a tuple of InstanceRefs")
        if any(ref.campaign_id != self.campaign_id for ref in self.present):
            raise _refuse("cross_campaign", "every cast member is in the cast's campaign")
        if len(set(self.present)) != len(self.present):
            raise _refuse("duplicate_actor", "a cast names each individual once")
        if len(self.present) > MAX_CAST:
            raise _refuse("too_large", f"a cast has at most {MAX_CAST} individuals")
        object.__setattr__(self, "present", tuple(sorted(self.present, key=str)))

    def may_speak(self, instance: InstanceRef) -> bool:
        """Only a present individual may speak or act as present in this scene."""
        return instance in self.present


def stage(state: PartyState, scene_id: str, present: Iterable[InstanceRef], *,
          instances: Collection[InstanceRef]) -> SceneCast:
    """Select the cast of one scene. ``instances`` are the campaign's instance files.

    Refused (``PartyRefused.reason``): ``relations_unreadable`` while membership is incomplete;
    ``invalid_field``, ``cross_campaign``, ``duplicate_actor``, ``too_large`` for the selection's shape;
    ``missing_actor`` for an individual without an instance file; ``instance_id_conflict`` for an id another
    kind also uses; ``conflicting_membership``; and ``in_reserve`` for a reserved member, which must be
    withdrawn into the party (a recorded move) before it can appear. Any instance kind may be cast, a
    creature included, under its own reference: nothing is re-keyed.
    """
    if not isinstance(state, PartyState):
        raise _refuse("invalid_field", "stage needs a PartyState")
    chosen = tuple(present)
    if any(type(ref) is not InstanceRef for ref in chosen):
        raise _refuse("invalid_field", "a cast is selected by InstanceRef")
    if not state.complete:
        raise _refuse("relations_unreadable", "a relation file cannot be read, so reserve membership is unknown")
    foreign = [ref for ref in chosen if ref.campaign_id != state.campaign_id]
    if foreign:
        raise _refuse("cross_campaign", f"not in campaign {state.campaign_id}", *foreign)
    if len(set(chosen)) != len(chosen):
        raise _refuse("duplicate_actor", "a cast names each individual once")
    if len(chosen) > MAX_CAST:
        raise _refuse("too_large", f"a cast has at most {MAX_CAST} individuals")
    known = set(instances)
    missing = sorted((ref for ref in chosen if ref not in known), key=str)
    if missing:
        raise _refuse("missing_actor", "no instance file", *missing)
    paired = sorted(set(chosen) & paired_instances(known), key=str)
    if paired:
        raise _refuse("instance_id_conflict", "an id also used by another kind is no one individual", *paired)
    conflicted = sorted(set(chosen) & state.conflicted, key=str)
    if conflicted:
        raise _refuse("conflicting_membership", "membership is ambiguous", *conflicted)
    reserved = sorted((ref for ref in chosen if (m := state.memberships.get(ref)) and m.status == "reserve"), key=str)
    if reserved:
        raise _refuse("in_reserve", "a reserved member is not with the party", *reserved)
    return SceneCast(state.campaign_id, scene_id, state.as_of, chosen)


def presence(state: PartyState, cast: SceneCast, instance: InstanceRef) -> str:
    """One individual's presence in ``cast``'s scene; one of ``PRESENCES``.

    ``present``: in the cast. ``accompanying``: in the active party of an anchor who is present (nearby and
    available to join the scene, but not on stage and never a speaker). ``reserved``: stored; it cannot
    appear. ``uncertain``: its membership is ambiguous. ``absent``: anything else, including every
    companion of an anchor who is not in the scene.
    """
    if cast.campaign_id != state.campaign_id or cast.as_of != state.as_of:
        raise _refuse("stale_cast", "the cast and the state must be of the same campaign and sequence")
    if instance in cast.present:
        return "present"
    if instance in state.conflicted:
        return "uncertain"
    membership = state.memberships.get(instance)
    if membership is None:
        return "absent"
    if membership.status == "reserve":
        return "reserved"
    return "accompanying" if membership.party in cast.present else "absent"


@dataclass(frozen=True, slots=True)
class PresenceRow:
    """One individual's standing in a scene: presence, membership and whether a player controls it."""

    instance: InstanceRef
    presence: str
    membership: Membership | None
    controlled: bool


def cast_report(state: PartyState, cast: SceneCast, instances: Iterable[InstanceRef], *,
                controlled: Collection[InstanceRef] = frozenset()) -> tuple[PresenceRow, ...]:
    """One row per instance, sorted, keeping control, membership and presence separate.

    ``controlled`` is the caller's set of player-controlled instances (003's assignments); control neither
    puts an individual in a party nor on stage, and a party membership grants no control.
    """
    rows = []
    for instance in sorted(set(instances), key=str):
        if type(instance) is not InstanceRef:
            raise _refuse("invalid_field", "a report lists InstanceRefs")
        rows.append(PresenceRow(instance, presence(state, cast, instance),
                                None if instance in state.conflicted else state.memberships.get(instance),
                                instance in controlled))
    return tuple(rows)
