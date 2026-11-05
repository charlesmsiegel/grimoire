"""Unique possessions: pure, frozen values (Sprint 034).

A **unique item** is one campaign instance (an ``InstanceRef``, usually of kind ``item``) whose own
instance file carries a ``possession`` record: who **owns** it, who or what has **custody** of it (a
person, a chest, a room), the **slot** it is equipped in, the slots it **fits**, its **origin** and a
bounded **log** of every change. Two same-named swords are two instances with two records; nothing
here ever matches a name.

Three things stay separate: the reusable definition (009's ``SourceRef``), the individual item
(this record, keyed by ``InstanceRef``) and a fungible counted resource (023's item key).

A change is an **operation**: a caller-chosen ``op_id`` plus up to four ordered steps (move, own,
equip, unequip) applied all-or-nothing to one item, planned against the item revision a human saw.
Nothing here reads files, clocks or randomness: time is passed in.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field

from grimoire.core.identity import InstanceRef, SourceRef, validate_segment
from grimoire.core.json_value import JsonObject, JsonValueError, freeze_object

#: the ``grimoire.entity/0`` payload key 034 owns; 009 keeps it verbatim in ``extras``
POSSESSION_KEY = "possession"
POSSESSION_SCHEMA = "grimoire.possession/1"
MAX_STEPS = 4
MAX_FITS = 16
MAX_LOG = 200
MAX_NOTE = 500
MAX_CAUSE = 256
MAX_NAME = 200
MAX_REV = 10 ** 15
STEP_KINDS = ("move", "own", "equip", "unequip")
#: the record's own top-level fields; any other top-level key is an extra, kept verbatim
RECORD_FIELDS = frozenset({"schema", "rev", "holder", "owner", "equipped", "fits", "origin", "evicted_through",
                           "log"})
#: keys an extra may never use: they could read as the envelope's or the instance payload's identity
IDENTITY_KEYS = frozenset({"address", "campaign_id", "kind", "instance_id", "item", "ref"})
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{6})?Z")
#: control (C0 and C1), surrogate, line and paragraph separator characters
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})
#: bidi embeddings, overrides and isolates, and the byte-order mark: they can disguise what a human is shown.
#: Other format characters (zero-width joiners in emoji, ZWNJ in Persian, soft hyphens) are ordinary text.
_UNSAFE_FORMAT = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff")
_NOTE_CONTROLS = frozenset("\t\n\r")
_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


class PossessionError(ValueError):
    """A possession value or operation that is refused; ``reason`` is machine-readable."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.detail))


class PossessionRefused(PossessionError):
    """An operation or creation that cannot be applied; nothing was written."""


class PossessionCodecError(PossessionError):
    """A stored record this version cannot interpret; it is reported, never rewritten."""


def _invalid(detail: str) -> PossessionRefused:
    return PossessionRefused("invalid_field", detail)


def check_int(value: object, label: str, low: int, high: int = MAX_REV) -> None:
    if type(value) is not int or not low <= value <= high:
        raise _invalid(f"{label} must be an integer from {low} to {high}, not {value!r}")


def check_segment(value: object, label: str) -> None:
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise _invalid(str(error)) from None


def check_text(value: object, label: str, limit: int, allowed: frozenset[str] = frozenset()) -> None:
    """Optional nonblank text a human is shown: no control, bidi-override or separator characters."""
    if value is None:
        return
    if type(value) is not str or not value.strip() or len(value) > limit:
        raise _invalid(f"{label} must be nonblank text of at most {limit} characters")
    if any(char not in allowed and (char in _UNSAFE_FORMAT or unicodedata.category(char) in _UNSAFE_CATEGORIES)
           for char in value):
        raise _invalid(f"{label} may not contain control, bidi-override or separator characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate (a truncated emoji) can never be written
        raise _invalid(f"{label} contains a lone surrogate") from None


def check_timestamp(value: object, label: str = "at") -> None:
    if type(value) is not str or not _TIMESTAMP.fullmatch(value):
        raise _invalid(f"{label} must be a UTC time YYYY-MM-DDTHH:MM:SS[.ffffff]Z, not {value!r}")
    year, month, day = int(value[0:4]), int(value[5:7]), int(value[8:10])
    hour, minute, second = int(value[11:13]), int(value[14:16]), int(value[17:19])
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    if not 1 <= month <= 12 or year < 1:
        raise _invalid(f"{label} is not a real date: {value!r}")
    if not 1 <= day <= _DAYS[month - 1] + (month == 2 and leap) or hour > 23 or minute > 59 or second > 59:
        raise _invalid(f"{label} is not a real UTC time: {value!r}")


def check_party(value: object, label: str, campaign_id: str | None = None) -> None:
    if type(value) is not InstanceRef:
        raise _invalid(f"{label} must be an InstanceRef, not {type(value).__name__}")
    if campaign_id is not None and value.campaign_id != campaign_id:
        raise PossessionRefused("cross_campaign", f"{label} {value} is not in campaign {campaign_id}")


def _fits(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        raise _invalid("fits must be a collection of slot names")
    slots = tuple(value)
    for slot in slots:
        check_segment(slot, "slot")
    if len(set(slots)) != len(slots):
        raise _invalid("fits names a slot twice")
    if len(slots) > MAX_FITS:
        raise _invalid(f"an item fits at most {MAX_FITS} slots")
    return tuple(sorted(slots))


@dataclass(frozen=True, slots=True)
class Step:
    """One change to one item. ``own`` with ``to=None`` leaves the item unowned."""

    kind: str
    to: InstanceRef | None = None
    by: InstanceRef | None = None
    slot: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in STEP_KINDS:
            raise _invalid(f"step kind must be one of {STEP_KINDS}, not {self.kind!r}")
        if self.kind == "move":
            check_party(self.to, "to")
        elif self.kind == "own":
            if self.to is not None:
                check_party(self.to, "to")
        elif self.to is not None:
            raise _invalid(f"a {self.kind} step has no to")
        if self.kind == "equip":
            check_party(self.by, "by")
            check_segment(self.slot, "slot")
        elif self.by is not None or self.slot is not None:
            raise _invalid(f"a {self.kind} step takes no by or slot")

    @classmethod
    def move(cls, to: InstanceRef) -> Step:
        """Custody passes to ``to``: a person, a container or a place (drop is a move to a place)."""
        return cls("move", to=to)

    @classmethod
    def own(cls, to: InstanceRef | None) -> Step:
        """Ownership passes to ``to``; ``None`` leaves the item unowned."""
        return cls("own", to=to)

    @classmethod
    def equip(cls, by: InstanceRef, slot: str) -> Step:
        return cls("equip", by=by, slot=slot)

    @classmethod
    def unequip(cls) -> Step:
        return cls("unequip")

    @property
    def parties(self) -> tuple[InstanceRef, ...]:
        return tuple(party for party in (self.to, self.by) if party is not None)


def _steps(steps: object) -> tuple[Step, ...]:
    if not isinstance(steps, tuple) or not steps or not all(type(step) is Step for step in steps):
        raise _invalid("steps must be a nonempty tuple of Step values")
    if len(steps) > MAX_STEPS:
        raise PossessionRefused("too_large", f"an operation has at most {MAX_STEPS} steps, not {len(steps)}")
    return steps


@dataclass(frozen=True, slots=True)
class Operation:
    """A caller-chosen id and ordered steps, applied all-or-nothing to one item. Replaying it is a no-op."""

    op_id: str
    steps: tuple[Step, ...]
    note: str | None = None
    cause: str | None = None  # opaque: an effect or action id, for audit (021/030)

    def __post_init__(self) -> None:
        check_segment(self.op_id, "op_id")
        _steps(self.steps)
        check_text(self.note, "note", MAX_NOTE, _NOTE_CONTROLS)
        check_text(self.cause, "cause", MAX_CAUSE)
        campaigns = sorted({party.campaign_id for step in self.steps for party in step.parties})
        if len(campaigns) > 1:
            raise PossessionRefused("cross_campaign", f"one operation names one campaign, not {campaigns}")


@dataclass(frozen=True, slots=True)
class State:
    """Who owns an item, who has custody of it and the slot it is equipped in, if any."""

    holder: InstanceRef
    owner: InstanceRef | None = None
    equipped: str | None = None

    def __post_init__(self) -> None:
        check_party(self.holder, "holder")
        if self.owner is not None:
            check_party(self.owner, "owner", self.holder.campaign_id)
        if self.equipped is not None:
            check_segment(self.equipped, "equipped")
            # the wearer is the holder, and only an owner wears their own item
            if self.owner != self.holder:
                raise _invalid(f"an item equipped by {self.holder} must be owned by it, not by {self.owner}")


@dataclass(frozen=True, slots=True)
class Origin:
    """How an item came to be, as created: never rewritten (provenance), and the create's replay key."""

    op: str
    at: str
    holder: InstanceRef
    owner: InstanceRef | None = None
    name: str | None = None
    source: SourceRef | None = None
    fits: tuple[str, ...] = ()
    note: str | None = None
    cause: str | None = None

    def __post_init__(self) -> None:
        check_segment(self.op, "origin op")
        check_timestamp(self.at, "origin at")
        check_party(self.holder, "origin holder")
        if self.owner is not None:
            check_party(self.owner, "origin owner", self.holder.campaign_id)
        check_text(self.name, "name", MAX_NAME)
        if self.source is not None and type(self.source) is not SourceRef:
            raise _invalid(f"origin source must be a SourceRef, not {type(self.source).__name__}")
        object.__setattr__(self, "fits", _fits(self.fits))
        check_text(self.note, "note", MAX_NOTE, _NOTE_CONTROLS)
        check_text(self.cause, "cause", MAX_CAUSE)


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One applied operation: its steps, the item's state before and after, and the revision it made."""

    op: str
    rev: int
    at: str
    steps: tuple[Step, ...]
    before: State
    after: State
    note: str | None = None
    cause: str | None = None

    def __post_init__(self) -> None:
        operation = self.operation  # validates op id, steps, note and cause
        check_int(self.rev, "rev", 2)
        check_timestamp(self.at)
        if type(self.before) is not State or type(self.after) is not State:
            raise _invalid("before and after must be State values")
        campaign = self.before.holder.campaign_id
        if self.after.holder.campaign_id != campaign or any(
                party.campaign_id != campaign for step in operation.steps for party in step.parties):
            raise PossessionRefused("cross_campaign", f"operation {self.op} names another campaign")

    @property
    def operation(self) -> Operation:
        return Operation(self.op, self.steps, self.note, self.cause)

    def same_operation(self, op: Operation) -> bool:
        return (self.op, self.steps, self.note, self.cause) == (op.op_id, op.steps, op.note, op.cause)


@dataclass(frozen=True, slots=True)
class Possession:
    """One unique item's owner, custody, equipped slot, origin and bounded change log, at revision ``rev``."""

    item: InstanceRef
    state: State
    origin: Origin
    rev: int = 1
    fits: tuple[str, ...] = ()
    evicted_through: int = 1
    log: tuple[LogEntry, ...] = ()
    extras: JsonObject = field(default_factory=JsonObject)

    def __post_init__(self) -> None:
        check_party(self.item, "item")
        if type(self.state) is not State or type(self.origin) is not Origin:
            raise _invalid("state must be a State and origin an Origin")
        campaign = self.item.campaign_id
        for label, party in (("holder", self.state.holder), ("owner", self.state.owner),
                             ("origin holder", self.origin.holder), ("origin owner", self.origin.owner)):
            if party is not None:
                check_party(party, label, campaign)
                if party == self.item:
                    raise _invalid(f"an item cannot be its own {label}")
        check_int(self.rev, "rev", 1)
        object.__setattr__(self, "fits", _fits(self.fits))
        if self.state.equipped is not None and self.state.equipped not in self.fits:
            raise _invalid(f"equipped slot {self.state.equipped} is not one the item fits")
        check_int(self.evicted_through, "evicted_through", 1, self.rev)  # rev 1 is the origin, never logged
        if not isinstance(self.log, tuple) or not all(type(entry) is LogEntry for entry in self.log):
            raise _invalid("log must be a tuple of LogEntry values")
        if len(self.log) > MAX_LOG:
            raise _invalid(f"the log holds at most {MAX_LOG} entries")
        previous, ids = self.evicted_through, {self.origin.op}
        for entry in self.log:
            if entry.rev <= previous or entry.rev > self.rev:
                raise _invalid(f"log revs must increase within ({self.evicted_through}, {self.rev}]: {entry.rev}")
            if entry.op in ids:
                raise _invalid(f"operation {entry.op} is logged twice (or is the origin's)")
            if entry.before.holder.campaign_id != campaign:
                raise PossessionRefused("cross_campaign", f"operation {entry.op} names another campaign")
            named = {entry.before.holder, entry.after.holder, entry.before.owner, entry.after.owner}
            if self.item in named.union(*(step.parties for step in entry.steps)):
                raise _invalid(f"operation {entry.op} names the item itself as a party")
            worn = [state.equipped for state in (entry.before, entry.after) if state.equipped is not None]
            worn += [step.slot for step in entry.steps if step.slot is not None]
            if any(slot not in self.fits for slot in worn):
                raise _invalid(f"operation {entry.op} wears the item in a slot it does not fit")
            previous = entry.rev
            ids.add(entry.op)
        try:
            object.__setattr__(self, "extras", freeze_object(self.extras))
        except JsonValueError as error:
            raise _invalid(f"extras: {error}") from None
        clash = sorted((IDENTITY_KEYS | RECORD_FIELDS) & set(self.extras))
        if clash:
            raise _invalid(f"extras may not use the reserved keys {clash}")

    @property
    def holder(self) -> InstanceRef:
        return self.state.holder

    @property
    def owner(self) -> InstanceRef | None:
        return self.state.owner

    @property
    def equipped(self) -> str | None:
        return self.state.equipped

    @property
    def parties(self) -> frozenset[InstanceRef]:
        """Every instance the record names: current, original and logged owners, holders and wearers."""
        found = {self.state.holder, self.origin.holder}
        found.update(party for party in (self.state.owner, self.origin.owner) if party is not None)
        for entry in self.log:
            for state in (entry.before, entry.after):
                found.add(state.holder)
                if state.owner is not None:
                    found.add(state.owner)
            found.update(party for step in entry.steps for party in step.parties)
        return frozenset(found)

    def entry(self, op_id: str) -> LogEntry | None:
        for entry in reversed(self.log):
            if entry.op == op_id:
                return entry
        return None
