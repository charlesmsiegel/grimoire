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


def _segment(value: object, label: str) -> None:
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise _refuse("invalid_field", str(error)) from None


#: Only ``stage`` builds a ``SceneCast``: a cast made any other way could hold a reserved member.
_STAGED = object()


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
              unreadable: bool) -> PartyState:
        """``relations`` are the campaign's decoded relations (others are ignored); ``unreadable`` says some
        relation file or the folder could not be read."""
        _segment(campaign_id, "campaign_id")
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
        for member in sorted(memberships, key=str):  # a cycle among unambiguous memberships, reported once
            seen, current = [member], memberships[member].party
            while current in memberships and current not in seen:
                seen.append(current)
                current = memberships[current].party
            if current == member and member == min(seen, key=str):
                problems.append(PartyProblem("party_cycle", member,
                                             tuple(sorted(memberships[m].relation.relation_id for m in seen))))
        if unreadable:
            problems.append(PartyProblem("relations_unreadable"))
        conflicted = frozenset(p.member for p in problems if p.code == "conflicting_membership")
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

    def chain(self, instance: InstanceRef) -> tuple[tuple[Membership, ...], bool]:
        """``instance``'s own membership, then its anchor's, and so on upward (a group in a larger party).

        The flag is true when the walk reaches an individual whose membership is ambiguous, so the rest of the
        chain is unknown. A cycle (only a hand edit or accepted effects can record one; ``PartyLibrary`` refuses
        it) stops the walk where it repeats.
        """
        found: list[Membership] = []
        seen = {instance}
        current = instance
        while True:
            if current in self.conflicted:
                return tuple(found), True
            membership = self.memberships.get(current)
            if membership is None:
                return tuple(found), False
            found.append(membership)
            if membership.party in seen:  # a recorded cycle: who travels with whom is unknown
                return tuple(found), True
            seen.add(membership.party)
            current = membership.party

    def anchors_above(self, anchor: InstanceRef) -> tuple[InstanceRef, ...]:
        """The parties ``anchor`` itself belongs to, transitively, nearest first."""
        found, _ = self.chain(anchor)
        return tuple(dict.fromkeys(m.party for m in found if m.party != anchor))

    def reserved(self, instance: InstanceRef) -> bool:
        """Stored, directly or because a party it travels in is itself stored (a boxed group, a boxed anchor)."""
        found, _ = self.chain(instance)
        return any(m.status == "reserve" for m in found)

    def uncertain(self, instance: InstanceRef) -> bool:
        """Its own membership, or one of its anchors', is ambiguous."""
        return self.chain(instance)[1]


@dataclass(frozen=True, slots=True)
class SceneCast:
    """Who is present in one scene at one sequence: exactly the individuals ``stage`` selected, sorted.

    Only ``stage`` builds one; any other construction, ``dataclasses.replace`` included, is refused
    (``invalid_field``). A cast records each member's membership chain as staged (its ``basis``). Membership can
    still change at the same sequence after staging (a member boxed during the scene), so whether a member is
    present is answered against a current state (``presence``, ``may_speak``), never by the cast alone.
    """

    campaign_id: str
    scene_id: str
    as_of: int
    present: tuple[InstanceRef, ...]
    _token: object = field(default=None, repr=False, compare=False)
    basis: tuple = field(default=(), repr=False)  # ((member, signature), ...) sorted by member, as staged

    def __post_init__(self) -> None:
        if self._token is not _STAGED:
            raise _refuse("invalid_field", "a scene cast is built by stage")
        object.__setattr__(self, "_token", None)  # spent: a copy made with replace() cannot carry it
        _segment(self.campaign_id, "campaign_id")
        _segment(self.scene_id, "scene_id")
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

    def may_speak(self, instance: InstanceRef, state: PartyState) -> bool:
        """Only a present individual may speak or act as present, judged against ``state`` (the scene's current
        membership); a member whose membership changed since staging is refused with ``stale_cast``."""
        return presence(state, self, instance) == "present"


def stage(state: PartyState, scene_id: str, present: Iterable[InstanceRef], *,
          instances: Collection[InstanceRef]) -> SceneCast:
    """Select the cast of one scene. ``instances`` are the campaign's instance files.

    Refused (``PartyRefused.reason``): ``relations_unreadable`` while membership is incomplete;
    ``invalid_field``, ``cross_campaign``, ``duplicate_actor``, ``too_large`` for the selection's shape;
    ``missing_actor`` for an individual without an instance file; ``instance_id_conflict`` for an id another
    kind also uses; ``conflicting_membership`` when its membership or an anchor's is ambiguous; and
    ``in_reserve`` for a reserved member, or one travelling in a party that is itself stored, which must be
    withdrawn (a recorded move) before it can appear. Any instance kind may be cast, a
    creature included, under its own reference: nothing is re-keyed.
    """
    if not isinstance(state, PartyState):
        raise _refuse("invalid_field", "stage needs a PartyState")
    _segment(scene_id, "scene_id")
    chosen = tuple(present)
    if any(type(ref) is not InstanceRef for ref in chosen):
        raise _refuse("invalid_field", "a cast is selected by InstanceRef")
    if not state.complete or any(p.code == "relations_unreadable" for p in state.problems):
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
    reserved = sorted((ref for ref in chosen if state.reserved(ref)), key=str)
    if reserved:
        raise _refuse("in_reserve", "a reserved member is not with the party", *reserved)
    conflicted = sorted((ref for ref in chosen if state.uncertain(ref)), key=str)
    if conflicted:
        raise _refuse("conflicting_membership", "membership is ambiguous", *conflicted)
    basis = tuple((ref, _signature(state, ref)) for ref in sorted(chosen, key=str))
    return SceneCast(state.campaign_id, scene_id, state.as_of, chosen, _STAGED, basis)


def _signature(state: PartyState, instance: InstanceRef) -> tuple:
    """What a member's standing rests on: each membership of its chain, endpoints included (a hand edit can keep a
    relation's id and predicate and change its anchor), and whether the walk was cut."""
    chain, unknown = state.chain(instance)
    return (tuple((m.relation, m.member, m.party, m.status, m.since) for m in chain), unknown)


def presence(state: PartyState, cast: SceneCast, instance: InstanceRef) -> str:
    """One individual's presence in ``cast``'s scene; one of ``PRESENCES``.

    ``present``: in the cast. ``reserved``: stored, or travelling in a party that is itself stored; it cannot
    appear. ``uncertain``: its membership or an anchor's is ambiguous. ``accompanying``: in the active party
    of an anchor who is present, directly or through a group (nearby and available to join the scene, but not
    on stage and never a speaker). ``absent``: anything else, including every companion of an anchor who is
    not in the scene. Refused with ``relations_unreadable`` while the state is incomplete, and with
    ``stale_cast`` for a cast of another campaign or sequence, or when a cast member's membership chain is no
    longer the one it was staged with (it was boxed, moved or made ambiguous since).
    """
    if not isinstance(state, PartyState) or not isinstance(cast, SceneCast) or type(instance) is not InstanceRef:
        raise _refuse("invalid_field", "presence needs a PartyState, a SceneCast and an InstanceRef")
    if not state.complete or any(p.code == "relations_unreadable" for p in state.problems):
        raise _refuse("relations_unreadable", "a relation file cannot be read, so reserve membership is unknown")
    if cast.campaign_id != state.campaign_id or cast.as_of != state.as_of:
        raise _refuse("stale_cast", "the cast and the state must be of the same campaign and sequence")
    if instance in cast.present:
        if dict(cast.basis).get(instance) != _signature(state, instance):
            raise _refuse("stale_cast", f"{instance}'s membership changed since the scene was staged; stage it again",
                          instance)
        return "present"
    chain, unknown = state.chain(instance)
    if any(m.status == "reserve" for m in chain):
        return "reserved"
    if unknown:
        return "uncertain"
    return "accompanying" if any(m.party in cast.present for m in chain) else "absent"


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
