"""Scene staging values (Sprint 041, first slice): areas, ways, who and what is where, and who has left.

A **stage** is the immediate physical situation of one scene, as the director has it: room-scale local
areas (no coordinates), the ways between them (doors and passages, open, closed or locked, with the item
that is their key), where each actor and object is, who holds what, and which cast actors are off stage.

It is scene-local staging and director truth. It is never world canon (durable consequences go through
021's accepted effects) and never an actor's knowledge (what an actor knows comes only from its own 012
evidence). Nothing here touches files or calls a model, and nothing is stored in this slice.

Every fact carries a ``basis``: ``established`` (stated in accepted play or by a correction) or
``assumed`` (plausible staging the director filled in, never stated), and the sequence ``since`` which it
has held.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ..core import InstanceRef
from ..core.identity import validate_segment

#: What a fact rests on.
BASES = ("established", "assumed")
#: The states of a way.
WAY_STATES = ("open", "closed", "locked")
#: At most this many of each.
MAX_AREAS = 64
MAX_WAYS = 128
MAX_ACTORS = 256
MAX_PLACEMENTS = 1024
#: A label (an area's description, the scene id) is at most this long, as 012/020 labels are.
LABEL_LIMIT = 200


class StagingRefused(ValueError):
    """A stage, change or question that cannot be made as given. Nothing changed.

    ``reason`` is machine-readable. The error never carries a label, text or reference, so it is safe to
    log, and it is raised outside any handler so neither its cause nor its context holds a refused value.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason,))


def fail(reason: str) -> None:
    """Raise ``StagingRefused(reason)``. Call it outside any ``except`` block."""
    raise StagingRefused(reason)


def checked(check) -> None:
    """Run shape checks; a ``ValueError`` or ``TypeError`` becomes a text-free ``invalid_field``."""
    try:
        check()
    except StagingRefused:
        raise
    except (ValueError, TypeError):
        pass
    else:
        return
    fail("invalid_field")


def _segment(value: object, label: str) -> None:
    validate_segment(value, label)


def _label(value: object) -> None:
    if type(value) is not str or not value.strip() or len(value) > LABEL_LIMIT:
        raise ValueError("expected a nonempty label of at most 200 characters")
    value.encode("utf-8")  # a lone surrogate cannot be stored by any later writer


def _sequence(value: object) -> None:
    if type(value) is not int or value < 0:
        raise ValueError("expected a non-negative int sequence")


def _basis(value: object) -> None:
    if value not in BASES or type(value) is not str:
        raise ValueError("unknown basis")


def _ref(value: object) -> None:
    if type(value) is not InstanceRef:
        raise TypeError("expected an InstanceRef")


def _tuple(value: object) -> tuple:
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise TypeError("expected a collection")
    return tuple(value)


@dataclass(frozen=True, slots=True)
class Area:
    """One room-scale local area of the scene: a study, a hallway, a balcony. ``label`` is for people."""

    area_id: str
    label: str | None = None

    def __post_init__(self) -> None:
        def check() -> None:
            _segment(self.area_id, "area_id")
            if self.label is not None:
                _label(self.label)
        checked(check)


@dataclass(frozen=True, slots=True)
class Way:
    """A door or passage between two areas, or from an area to off stage (``sides[1] is None``).

    ``state`` is ``open``, ``closed`` or ``locked``. ``key`` is the item that locks and unlocks it; a way
    with no key has no lock, and only a correction can change a locked one. ``sides`` is canonical: two
    areas in sorted order, or ``(area, None)``.
    """

    way_id: str
    sides: tuple[str, str | None]
    state: str = "closed"
    key: InstanceRef | None = None
    basis: str = "established"
    since: int = 0

    def __post_init__(self) -> None:
        def check() -> None:
            _segment(self.way_id, "way_id")
            sides = _tuple(self.sides)
            if len(sides) != 2 or sides[0] is None and sides[1] is None:
                raise ValueError("a way has two sides, at most one of them off stage")
            for side in sides:
                if side is not None:
                    _segment(side, "side")
            if sides[0] == sides[1]:
                raise ValueError("a way joins two different areas")
            if sides[0] is None:
                sides = (sides[1], None)
            elif sides[1] is not None:
                sides = tuple(sorted(sides))
            object.__setattr__(self, "sides", sides)
            if self.state not in WAY_STATES or type(self.state) is not str:
                raise ValueError("unknown way state")
            if self.key is not None:
                _ref(self.key)
            _basis(self.basis)
            _sequence(self.since)
        checked(check)

    @property
    def to_off_stage(self) -> bool:
        """Whether this way leads off stage."""
        return self.sides[1] is None

    def other(self, area_id: str) -> str | None:
        """The side across from ``area_id`` (``None``: off stage). ``area_id`` must be a side."""
        return self.sides[1] if self.sides[0] == area_id else self.sides[0]


@dataclass(frozen=True, slots=True)
class Placement:
    """Where one individual is: resting or standing in ``area``, or held or carried by ``holder``.

    Exactly one of the two is set. A thing held by an actor is in hand; a thing held by a thing is inside
    or on it (contained).
    """

    who: InstanceRef
    area: str | None = None
    holder: InstanceRef | None = None
    basis: str = "established"
    since: int = 0

    def __post_init__(self) -> None:
        def check() -> None:
            _ref(self.who)
            if (self.area is None) == (self.holder is None):
                raise ValueError("a placement names an area or a holder")
            if self.area is not None:
                _segment(self.area, "area")
            else:
                _ref(self.holder)
            _basis(self.basis)
            _sequence(self.since)
        checked(check)
        if self.holder == self.who:
            fail("custody_cycle")


@dataclass(frozen=True, slots=True)
class Absence:
    """A cast actor who is off stage: who left through ``via`` (a way id), or who has not entered (``None``)."""

    actor: InstanceRef
    via: str | None = None
    since: int = 0

    def __post_init__(self) -> None:
        def check() -> None:
            _ref(self.actor)
            if self.via is not None:
                _segment(self.via, "via")
            _sequence(self.since)
        checked(check)


@dataclass(frozen=True, slots=True)
class Root:
    """Where an individual's custody chain ends. ``area`` when on stage, ``absent`` (the off-stage actor at
    the top) when it is with someone who left, both ``None`` when the individual is not tracked.
    ``holders`` is the chain from the direct holder upward."""

    area: str | None
    absent: InstanceRef | None
    holders: tuple[InstanceRef, ...]


@dataclass(frozen=True)
class Stage:
    """The physical staging of one scene at campaign sequence ``as_of``.

    Built and checked on every construction (``dataclasses.replace`` included). ``actors`` are the cast
    individuals who act; every other placed individual is a thing. Every actor is either placed or absent,
    never both. A holder is itself placed, or is an absent actor (what someone carries leaves with them).
    No individual holds itself at any depth. All collections are canonical (sorted), so two stages with
    the same facts compare equal.
    """

    campaign_id: str
    scene_id: str
    as_of: int
    areas: tuple[Area, ...]
    ways: tuple[Way, ...] = ()
    actors: tuple[InstanceRef, ...] = ()
    placements: tuple[Placement, ...] = ()
    absences: tuple[Absence, ...] = ()
    _areas: Mapping[str, Area] = field(init=False, repr=False, compare=False, hash=False, default=None)
    _ways: Mapping[str, Way] = field(init=False, repr=False, compare=False, hash=False, default=None)
    _placed: Mapping[InstanceRef, Placement] = field(init=False, repr=False, compare=False, hash=False,
                                                     default=None)
    _absent: Mapping[InstanceRef, Absence] = field(init=False, repr=False, compare=False, hash=False,
                                                   default=None)
    _actor_set: frozenset[InstanceRef] = field(init=False, repr=False, compare=False, hash=False,
                                               default=frozenset())
    _held: Mapping[InstanceRef, tuple[InstanceRef, ...]] = field(init=False, repr=False, compare=False,
                                                                 hash=False, default=None)

    def __post_init__(self) -> None:
        def shapes() -> None:
            _segment(self.campaign_id, "campaign_id")
            _label(self.scene_id)
            _sequence(self.as_of)
            for name, kind in (("areas", Area), ("ways", Way), ("actors", InstanceRef),
                               ("placements", Placement), ("absences", Absence)):
                values = _tuple(getattr(self, name))
                if any(type(value) is not kind for value in values):
                    raise TypeError(f"{name} holds a value of the wrong type")
                object.__setattr__(self, name, values)
        checked(shapes)
        if not self.areas or len(self.areas) > MAX_AREAS or len(self.ways) > MAX_WAYS \
                or len(self.actors) > MAX_ACTORS or len(self.placements) > MAX_PLACEMENTS:
            fail("too_large" if self.areas else "invalid_field")
        self._index()

    def _index(self) -> None:
        campaign = self.campaign_id
        areas = {area.area_id: area for area in self.areas}
        ways = {way.way_id: way for way in self.ways}
        actors = frozenset(self.actors)
        placed = {placement.who: placement for placement in self.placements}
        absent = {absence.actor: absence for absence in self.absences}
        if len(areas) != len(self.areas) or len(ways) != len(self.ways) or len(actors) != len(self.actors):
            fail("duplicate_id")
        if len(placed) != len(self.placements) or len(absent) != len(self.absences):
            fail("placed_twice")
        refs = [*self.actors, *placed, *absent,
                *(p.holder for p in self.placements if p.holder is not None),
                *(way.key for way in self.ways if way.key is not None)]
        if any(ref.campaign_id != campaign for ref in refs):
            fail("campaign_mismatch")
        for way in self.ways:
            if any(side is not None and side not in areas for side in way.sides):
                fail("unknown_area")
            if way.since > self.as_of:
                fail("future_fact")
        for placement in self.placements:
            if placement.area is not None and placement.area not in areas:
                fail("unknown_area")
            if placement.since > self.as_of:
                fail("future_fact")
        for absence in self.absences:
            if absence.actor not in actors:
                fail("not_cast")
            if absence.via is not None and absence.via not in ways:
                fail("unknown_way")
            if absence.via is not None and not ways[absence.via].to_off_stage:
                fail("leads_to_area")
            if absence.since > self.as_of:
                fail("future_fact")
        for actor in self.actors:
            if (actor in placed) == (actor in absent):
                fail("placed_twice" if actor in placed else "actor_unplaced")
        held: dict[InstanceRef, list[InstanceRef]] = {}
        for placement in self.placements:
            if placement.holder is not None:
                if placement.holder not in placed and placement.holder not in absent:
                    fail("orphan_holder")
                held.setdefault(placement.holder, []).append(placement.who)
        # every chain must end at an area or an absent actor; a loop never does
        for start in placed:
            seen = {start}
            current = placed[start]
            while current.holder is not None and current.holder in placed:
                if current.holder in seen:
                    fail("custody_cycle")
                seen.add(current.holder)
                current = placed[current.holder]
        object.__setattr__(self, "areas", tuple(sorted(self.areas, key=lambda a: a.area_id)))
        object.__setattr__(self, "ways", tuple(sorted(self.ways, key=lambda w: w.way_id)))
        object.__setattr__(self, "actors", tuple(sorted(self.actors, key=str)))
        object.__setattr__(self, "placements", tuple(sorted(self.placements, key=lambda p: str(p.who))))
        object.__setattr__(self, "absences", tuple(sorted(self.absences, key=lambda a: str(a.actor))))
        object.__setattr__(self, "_areas", MappingProxyType(areas))
        object.__setattr__(self, "_ways", MappingProxyType(ways))
        object.__setattr__(self, "_placed", MappingProxyType(placed))
        object.__setattr__(self, "_absent", MappingProxyType(absent))
        object.__setattr__(self, "_actor_set", actors)
        object.__setattr__(self, "_held", MappingProxyType(
            {holder: tuple(sorted(items, key=str)) for holder, items in held.items()}))

    def at(self, sequence: int) -> Stage:
        """The same facts at a later ``sequence``: the caller's word that nothing physical changed between.

        ``perceptions`` and ``deliver`` need a stage whose ``as_of`` is the moment they record, so a stage
        left behind by a later change can never be used by mistake. Earlier sequences are ``out_of_order``.
        """
        def shapes() -> None:
            _sequence(sequence)
        checked(shapes)
        if sequence < self.as_of:
            fail("out_of_order")
        return Stage(self.campaign_id, self.scene_id, sequence, self.areas, self.ways, self.actors,
                     self.placements, self.absences)

    def on_stage(self) -> tuple[InstanceRef, ...]:
        """The actors physically in the scene now (the cast less the absent), sorted by reference text."""
        return tuple(actor for actor in self.actors if actor not in self._absent)

    # -- lookups -------------------------------------------------------------------------------------

    def area(self, area_id: str) -> Area | None:
        return self._areas.get(area_id)

    def way(self, way_id: str) -> Way | None:
        return self._ways.get(way_id)

    def placement(self, who: InstanceRef) -> Placement | None:
        return self._placed.get(who)

    def absence(self, actor: InstanceRef) -> Absence | None:
        return self._absent.get(actor)

    def is_actor(self, who: object) -> bool:
        return who in self._actor_set

    def held_by(self, holder: InstanceRef) -> tuple[InstanceRef, ...]:
        """What ``holder`` holds directly (in hand, or inside or on it), sorted by reference text."""
        return self._held.get(holder, ())

    def custody(self, holder: InstanceRef) -> tuple[InstanceRef, ...]:
        """Everything ``holder`` holds at any depth, in a stable order."""
        found: list[InstanceRef] = []
        stack = list(reversed(self.held_by(holder)))
        while stack:
            item = stack.pop()
            found.append(item)
            stack.extend(reversed(self.held_by(item)))
        return tuple(found)

    def root(self, who: InstanceRef) -> Root:
        """Where ``who``'s custody chain ends."""
        if who in self._absent:
            return Root(None, who, ())
        placement = self._placed.get(who)
        holders: list[InstanceRef] = []
        while placement is not None and placement.holder is not None:
            holders.append(placement.holder)
            if placement.holder in self._absent:
                return Root(None, placement.holder, tuple(holders))
            placement = self._placed[placement.holder]
        return Root(placement.area if placement is not None else None, None, tuple(holders))

    def ways_between(self, first: str, second: str) -> tuple[Way, ...]:
        """The ways joining two different areas, by id."""
        pair = tuple(sorted((first, second)))
        return tuple(way for way in self.ways if way.sides == pair)


__all__ = [
    "BASES", "LABEL_LIMIT", "MAX_ACTORS", "MAX_AREAS", "MAX_PLACEMENTS", "MAX_WAYS", "WAY_STATES",
    "Absence", "Area", "Placement", "Root", "Stage", "StagingRefused", "Way",
]
