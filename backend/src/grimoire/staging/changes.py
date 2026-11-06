"""Changes to a stage (Sprint 041): physical actions checked against the staging, and corrections.

``apply(stage, change, *, sequence, provenance)`` returns the new stage or raises ``StagingRefused``.
Physical changes are made by an actor standing in an area: one who is off stage, carried by someone or
not an actor cannot move, take, open or lock anything. Corrections (``place``, ``remove``, ``set_way``)
fix the staging directly, with no physical check, and still keep every stage invariant.

Every fact a change sets is ``established`` with ``since = sequence``, except a correction made with
``basis="assumed"`` (plausible, unstated staging). ``provenance`` is attribution text (who or what
asked: ``scene:s1/t4``, ``player:p1``), never authority; this slice does not record it on the stage.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import InstanceRef
from .model import Absence, Placement, Stage, StagingRefused, Way, _label, checked, fail
from .model import _basis, _ref, _segment, _sequence

#: Physical changes, each made by an actor (``who``).
PHYSICAL = ("move", "leave", "enter", "take", "drop", "give", "open", "close", "lock", "unlock")
#: Corrections and staging assumptions: no physical check.
CORRECTIONS = ("place", "remove", "set_way")
OPS = PHYSICAL + CORRECTIONS

#: Which fields each op uses; every other field must be left at its default.
_FIELDS = {
    "move": ("who", "way"), "leave": ("who", "way"), "enter": ("who", "way"),
    "take": ("who", "thing"), "drop": ("who", "thing"), "give": ("who", "thing", "to"),
    "open": ("who", "way"), "close": ("who", "way"), "lock": ("who", "way"), "unlock": ("who", "way"),
    "place": ("who", "area|holder", "basis"), "remove": ("who",), "set_way": ("way", "state", "basis"),
}


@dataclass(frozen=True, slots=True)
class Change:
    """One change to a stage. Build it with the module's functions (``move``, ``take`` ...)."""

    op: str
    who: InstanceRef | None = None
    way: str | None = None
    thing: InstanceRef | None = None
    to: InstanceRef | None = None
    area: str | None = None
    holder: InstanceRef | None = None
    state: str | None = None
    basis: str = "established"

    def __post_init__(self) -> None:
        def check() -> None:
            if type(self.op) is not str or self.op not in OPS:
                raise ValueError("unknown op")
            used = set()
            for name in _FIELDS[self.op]:
                used.update(name.split("|"))
            for name in ("who", "way", "thing", "to", "area", "holder", "state"):
                value = getattr(self, name)
                if name not in used and value is not None:
                    raise ValueError(f"{self.op} takes no {name}")
            if "basis" not in used and self.basis != "established":
                raise ValueError(f"{self.op} takes no basis")
            for name in ("who", "thing", "to", "holder"):
                if name in used and getattr(self, name) is not None:
                    _ref(getattr(self, name))
            for name in ("way", "area"):
                if name in used and getattr(self, name) is not None:
                    _segment(getattr(self, name), name)
            _basis(self.basis)
            required = [name for name in _FIELDS[self.op] if name in ("who", "way", "thing", "to", "state")]
            if any(getattr(self, name) is None for name in required):
                raise ValueError(f"{self.op} needs {', '.join(required)}")
            if self.op == "place" and (self.area is None) == (self.holder is None):
                raise ValueError("place needs an area or a holder")
            if self.op == "set_way" and self.state not in ("open", "closed", "locked"):
                raise ValueError("unknown way state")
        checked(check)


def move(who: InstanceRef, way: str) -> Change:
    """``who`` goes through ``way`` into the area on its other side. The way must be open."""
    return Change("move", who=who, way=way)


def leave(who: InstanceRef, way: str) -> Change:
    """``who`` leaves the scene through ``way``, which leads off stage. What they carry goes with them."""
    return Change("leave", who=who, way=way)


def enter(who: InstanceRef, way: str) -> Change:
    """An absent cast actor comes on stage through ``way`` (which leads off stage) into its area."""
    return Change("enter", who=who, way=way)


def take(who: InstanceRef, thing: InstanceRef) -> Change:
    """``who`` takes ``thing`` into hand: from the floor or a table of their area, or from a container."""
    return Change("take", who=who, thing=thing)


def drop(who: InstanceRef, thing: InstanceRef) -> Change:
    """``who`` puts down ``thing``, which they hold in hand, in their area."""
    return Change("drop", who=who, thing=thing)


def give(who: InstanceRef, thing: InstanceRef, to: InstanceRef) -> Change:
    """``who`` hands ``thing``, held in hand, to ``to``, an actor standing in the same area."""
    return Change("give", who=who, thing=thing, to=to)


def open_way(who: InstanceRef, way: str) -> Change:
    """``who`` opens a closed way from one of its sides."""
    return Change("open", who=who, way=way)


def close_way(who: InstanceRef, way: str) -> Change:
    """``who`` closes an open way from one of its sides."""
    return Change("close", who=who, way=way)


def lock_way(who: InstanceRef, way: str) -> Change:
    """``who`` locks a closed way from one of its sides, with its key in their custody."""
    return Change("lock", who=who, way=way)


def unlock_way(who: InstanceRef, way: str) -> Change:
    """``who`` unlocks a locked way from one of its sides, with its key in their custody."""
    return Change("unlock", who=who, way=way)


def place(who: InstanceRef, *, area: str | None = None, holder: InstanceRef | None = None,
          basis: str = "established") -> Change:
    """Correct or stage where ``who`` is: in ``area`` or held by ``holder``. No physical check.

    An absent actor placed this way is on stage again. ``basis="assumed"`` records plausible staging that
    nothing has stated; a later ``place`` with the default basis establishes it.
    """
    return Change("place", who=who, area=area, holder=holder, basis=basis)


def remove(who: InstanceRef) -> Change:
    """Correct: take ``who`` off the stage. An actor becomes absent (with no way), keeping what they carry;
    a thing stops being tracked, together with everything inside or on it."""
    return Change("remove", who=who)


def set_way(way: str, state: str, *, basis: str = "established") -> Change:
    """Correct or stage a way's state. No physical check (who has the key does not matter)."""
    return Change("set_way", way=way, state=state, basis=basis)


# -- applying ---------------------------------------------------------------------------------------


def actor_refusal(stage: Stage, who: InstanceRef) -> str | None:
    """Why ``who`` cannot act physically now (``not_actor``, ``not_on_stage``, ``restrained``), or ``None``."""
    if not stage.is_actor(who):
        return "not_actor"
    if stage.absence(who) is not None:
        return "not_on_stage"
    if stage.placement(who).holder is not None:
        return "restrained"
    return None


def _free(stage: Stage, who: InstanceRef) -> str:
    """The area of an actor who can act physically, or a refusal."""
    reason = actor_refusal(stage, who)
    if reason is not None:
        fail(reason)
    return stage.placement(who).area


def _way(stage: Stage, way_id: str) -> Way:
    way = stage.way(way_id)
    if way is None:
        fail("unknown_way")
    return way


def _at_side(way: Way, area: str) -> None:
    if area not in way.sides:
        fail("way_elsewhere")


def _passable(way: Way) -> None:
    if way.state != "open":
        fail("way_locked" if way.state == "locked" else "way_closed")


def reach_refusal(stage: Stage, who: InstanceRef, thing: InstanceRef) -> str | None:
    """Why ``who`` cannot get ``thing`` into hand now, or ``None`` when they can (or already have it).

    The one rule behind ``take`` and the ``reach`` question: ``who`` must be able to act; ``thing`` must be
    a tracked, on-stage non-actor in ``who``'s area, held by no other actor at any depth (a thing inside a
    container on the table is reachable; one in another actor's hand or pack is not).
    """
    reason = actor_refusal(stage, who)
    if reason is not None:
        return reason
    if thing == who:
        return "is_self"
    if stage.is_actor(thing):
        return "is_actor"
    if stage.placement(thing) is None:
        return "untracked"
    root = stage.root(thing)
    if root.absent is not None:
        return "thing_off_stage"
    for holder in root.holders:
        if holder == who:
            return None
        if stage.is_actor(holder):
            return "held_by_other"
    if root.area != stage.placement(who).area:
        return "elsewhere"
    return None


def _with(stage: Stage, sequence: int, *, placements: dict | None = None, absences: dict | None = None,
          ways: dict | None = None) -> Stage:
    placed = {p.who: p for p in stage.placements}
    absent = {a.actor: a for a in stage.absences}
    by_id = {w.way_id: w for w in stage.ways}
    for who, value in (placements or {}).items():
        if value is None:
            placed.pop(who, None)
        else:
            placed[who] = value
    for actor, value in (absences or {}).items():
        if value is None:
            absent.pop(actor, None)
        else:
            absent[actor] = value
    by_id.update(ways or {})
    return Stage(stage.campaign_id, stage.scene_id, sequence, stage.areas, tuple(by_id.values()),
                 stage.actors, tuple(placed.values()), tuple(absent.values()))


def _way_state(way: Way, state: str, sequence: int, basis: str = "established") -> Way:
    return Way(way.way_id, way.sides, state, way.key, basis, sequence)


def _worked(stage: Stage, way: Way, who: InstanceRef, op: str, sequence: int) -> Way:
    """``way`` after ``who`` opens, closes, locks or unlocks it."""
    if op == "open":
        if way.state != "closed":
            fail("already_open" if way.state == "open" else "way_locked")
        return _way_state(way, "open", sequence)
    if op == "close":
        if way.state != "open":
            fail("already_closed")
        return _way_state(way, "closed", sequence)
    if way.key is None:
        fail("no_lock")
    if op == "lock" and way.state != "closed":
        fail("still_open" if way.state == "open" else "already_locked")
    if op == "unlock" and way.state != "locked":
        fail("not_locked")
    if way.key not in stage.custody(who):
        fail("no_key")
    return _way_state(way, "locked" if op == "lock" else "closed", sequence)


def apply(stage: Stage, change: Change, *, sequence: int, provenance: str) -> Stage:
    """The stage after ``change`` at campaign ``sequence``, or ``StagingRefused`` with nothing changed."""
    if type(stage) is not Stage or type(change) is not Change:
        fail("invalid_field")

    def shapes() -> None:
        _sequence(sequence)
        _label(provenance)
    checked(shapes)
    if sequence < stage.as_of:
        fail("out_of_order")
    for ref in (change.who, change.thing, change.to, change.holder):
        if ref is not None and ref.campaign_id != stage.campaign_id:
            fail("campaign_mismatch")
    op, who = change.op, change.who

    if op in ("open", "close", "lock", "unlock") and stage.is_actor(who) and stage.absence(who) is not None:
        # an absent actor stands outside: it may work a way that leads off stage, from that side
        way = _way(stage, change.way)
        if not way.to_off_stage:
            fail("not_on_stage")
        return _with(stage, sequence, ways={way.way_id: _worked(stage, way, who, op, sequence)})

    if op in ("move", "leave", "open", "close", "lock", "unlock"):
        area = _free(stage, who)
        way = _way(stage, change.way)
        _at_side(way, area)
        if op == "move":
            if way.to_off_stage:
                fail("leads_off_stage")
            _passable(way)
            return _with(stage, sequence, placements={who: Placement(who, area=way.other(area), since=sequence)})
        if op == "leave":
            if not way.to_off_stage:
                fail("leads_to_area")
            _passable(way)
            return _with(stage, sequence, placements={who: None},
                         absences={who: Absence(who, way.way_id, sequence)})
        return _with(stage, sequence, ways={way.way_id: _worked(stage, way, who, op, sequence)})

    if op == "enter":
        if not stage.is_actor(who):
            fail("not_actor")
        if stage.absence(who) is None:
            fail("already_on_stage")
        way = _way(stage, change.way)
        if not way.to_off_stage:
            fail("leads_to_area")
        _passable(way)
        return _with(stage, sequence, placements={who: Placement(who, area=way.sides[0], since=sequence)},
                     absences={who: None})

    if op == "take":
        reason = reach_refusal(stage, who, change.thing)
        if reason is not None:
            fail(reason)
        if stage.placement(change.thing).holder == who:
            fail("already_held")
        return _with(stage, sequence, placements={change.thing: Placement(change.thing, holder=who, since=sequence)})

    if op in ("drop", "give"):
        area = _free(stage, who)
        thing = change.thing
        placement = stage.placement(thing)
        if placement is None or placement.holder != who:
            fail("not_holding")
        if op == "drop":
            return _with(stage, sequence, placements={thing: Placement(thing, area=area, since=sequence)})
        if change.to == who:
            fail("is_self")
        if change.to == thing:
            fail("custody_cycle")
        if not stage.is_actor(change.to):
            fail("not_actor")
        if stage.absence(change.to) is not None:
            fail("recipient_off_stage")
        recipient = stage.placement(change.to)
        if recipient.holder is not None:
            fail("recipient_restrained")
        if recipient.area != area:
            fail("recipient_elsewhere")
        return _with(stage, sequence, placements={thing: Placement(thing, holder=change.to, since=sequence)})

    if op == "place":
        absences = {}
        if stage.is_actor(who):
            absences[who] = None
        placement = Placement(who, area=change.area, holder=change.holder, basis=change.basis, since=sequence)
        return _with(stage, sequence, placements={who: placement}, absences=absences)

    if op == "remove":
        if stage.is_actor(who):
            if stage.absence(who) is not None:
                fail("already_absent")
            return _with(stage, sequence, placements={who: None}, absences={who: Absence(who, None, sequence)})
        if stage.placement(who) is None:
            fail("untracked")
        if any(stage.is_actor(item) for item in stage.custody(who)):
            fail("holds_actor")
        gone = {who: None, **{item: None for item in stage.custody(who)}}
        return _with(stage, sequence, placements=gone)

    # set_way
    way = _way(stage, change.way)
    return _with(stage, sequence, ways={way.way_id: _way_state(way, change.state, sequence, change.basis)})


__all__ = [
    "CORRECTIONS", "Change", "OPS", "PHYSICAL", "StagingRefused", "actor_refusal", "apply", "close_way", "drop", "enter",
    "give", "leave", "lock_way", "move", "open_way", "place", "reach_refusal", "remove", "set_way", "take",
    "unlock_way",
]
