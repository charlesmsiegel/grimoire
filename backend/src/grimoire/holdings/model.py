"""Counted holdings: pure, frozen values (Sprint 023).

A **holder** is an existing campaign instance of any kind (a person, a chest, a ship). It holds at
most one **stack** per **item key**, a campaign-local ID segment naming a fungible kind of thing.
Counts change only through an **operation**: a caller-chosen ``op_id`` plus ordered lines
(add, consume, adjust, move), applied all-or-nothing across every holder it touches. Each holder
keeps a bounded log of the operations that touched it, which makes replays idempotent.

Names are never identity. Nothing here reads files, clocks or randomness: time is passed in.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import InstanceRef, validate_segment
from grimoire.core.json_value import JsonObject, JsonValueError, freeze_object

HOLDINGS_SCHEMA = "grimoire.holdings/1"
MAX_COUNT = 10 ** 12
MAX_STACKS = 1024
MAX_LINES = 32
MAX_HOLDERS = 8
MAX_LOG = 1000
MAX_FILE = 7 * 1024 * 1024  # rendered bytes; the store's reader refuses files above 8 MiB
MAX_NOTE = 500
MAX_CAUSE = 256
LINE_KINDS = ("add", "consume", "adjust", "move")
#: payload keys the envelope owns; a payload or ``extras`` repeating one could rebind identity
IDENTITY_KEYS = frozenset({"schema", "address", "campaign_id", "holder", "kind", "instance_id", "ref"})
#: the payload's own top-level fields
PAYLOAD_FIELDS = frozenset({"seq", "evicted_through", "stacks", "ops"})
_TIMESTAMP = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{6})?Z")
#: control (C0 and C1), format (bidi overrides, zero-width), surrogate and line/paragraph separator characters
_UNSAFE_CATEGORIES = frozenset({"Cc", "Cs", "Zl", "Zp"})
#: bidi embeddings, overrides and isolates, and the byte-order mark: they can disguise what a human is shown.
#: Other format characters (zero-width joiners in emoji, ZWNJ in Persian, soft hyphens) are ordinary text.
_UNSAFE_FORMAT = frozenset("\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069\ufeff")
_NOTE_CONTROLS = frozenset("\t\n\r")
_DAYS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


class HoldingsError(ValueError):
    """A holdings value or operation that is refused; ``reason`` is machine-readable."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail

    def __reduce__(self) -> tuple[object, ...]:
        return (type(self), (self.reason, self.detail))


class HoldingsRefused(HoldingsError):
    """An operation that cannot be applied; nothing was written."""


class HoldingsCodecError(HoldingsError):
    """A stored payload this version cannot interpret; it is reported, never rewritten."""


def _invalid(detail: str) -> HoldingsRefused:
    return HoldingsRefused("invalid_field", detail)


def _int(value: object, label: str, low: int, high: int = MAX_COUNT) -> None:
    if type(value) is not int or not low <= value <= high:
        raise _invalid(f"{label} must be an integer from {low} to {high}, not {value!r}")


def _segment(value: object, label: str) -> None:
    try:
        validate_segment(value, label)
    except ValueError as error:
        raise _invalid(str(error)) from None


def _text(value: object, label: str, limit: int, allowed: frozenset[str] = frozenset()) -> None:
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


def _holder(value: object, label: str) -> None:
    if type(value) is not InstanceRef:
        raise _invalid(f"{label} must be an InstanceRef, not {type(value).__name__}")


@dataclass(frozen=True, slots=True)
class Line:
    """One step of an operation. ``holder`` is the source of a ``move``."""

    kind: str
    holder: InstanceRef
    item: str
    amount: int | None = None
    seen: int | None = None
    to: int | None = None
    target: InstanceRef | None = None

    def __post_init__(self) -> None:
        if self.kind not in LINE_KINDS:
            raise _invalid(f"line kind must be one of {LINE_KINDS}, not {self.kind!r}")
        _holder(self.holder, "holder")
        _segment(self.item, "item")
        if self.kind == "adjust":
            if self.amount is not None or self.target is not None:
                raise _invalid("an adjust line takes seen and to, not amount or target")
            _int(self.seen, "seen", 0)
            _int(self.to, "to", 0)
            return
        if self.seen is not None or self.to is not None:
            raise _invalid(f"a {self.kind} line takes an amount, not seen or to")
        _int(self.amount, "amount", 1)
        if self.kind == "move":
            _holder(self.target, "target")
            if self.target == self.holder:
                raise HoldingsRefused("same_holder", f"cannot move from {self.holder} to itself")
        elif self.target is not None:
            raise _invalid(f"a {self.kind} line has no target")

    @classmethod
    def add(cls, holder: InstanceRef, item: str, amount: int) -> Line:
        return cls("add", holder, item, amount=amount)

    @classmethod
    def consume(cls, holder: InstanceRef, item: str, amount: int) -> Line:
        return cls("consume", holder, item, amount=amount)

    @classmethod
    def adjust(cls, holder: InstanceRef, item: str, seen: int, to: int) -> Line:
        return cls("adjust", holder, item, seen=seen, to=to)

    @classmethod
    def move(cls, source: InstanceRef, target: InstanceRef, item: str, amount: int) -> Line:
        return cls("move", source, item, amount=amount, target=target)

    @property
    def holders(self) -> tuple[InstanceRef, ...]:
        return (self.holder,) if self.target is None else (self.holder, self.target)


def _lines(lines: object) -> tuple[Line, ...]:
    if not isinstance(lines, tuple) or not lines or not all(type(line) is Line for line in lines):
        raise _invalid("lines must be a nonempty tuple of Line values")
    if len(lines) > MAX_LINES:
        raise HoldingsRefused("too_large", f"an operation has at most {MAX_LINES} lines, not {len(lines)}")
    return lines


def _holders_of(lines: tuple[Line, ...]) -> tuple[InstanceRef, ...]:
    return tuple(sorted({holder for line in lines for holder in line.holders}, key=str))


@dataclass(frozen=True, slots=True)
class Operation:
    """A caller-chosen id and ordered lines, applied all-or-nothing. Replaying the same id is a no-op."""

    op_id: str
    lines: tuple[Line, ...]
    note: str | None = None
    cause: str | None = None  # opaque: an effect or action id, for audit (021/030)

    def __post_init__(self) -> None:
        _segment(self.op_id, "op_id")
        _lines(self.lines)
        _text(self.note, "note", MAX_NOTE, _NOTE_CONTROLS)
        _text(self.cause, "cause", MAX_CAUSE)
        holders = self.holders
        if len(holders) > MAX_HOLDERS:
            raise HoldingsRefused("too_large", f"an operation touches at most {MAX_HOLDERS} holders")
        campaigns = sorted({holder.campaign_id for holder in holders})
        if len(campaigns) > 1:
            raise HoldingsRefused("cross_campaign", f"one operation touches one campaign, not {campaigns}")

    @property
    def holders(self) -> tuple[InstanceRef, ...]:
        return _holders_of(self.lines)

    @property
    def campaign_id(self) -> str:
        return self.lines[0].holder.campaign_id


def _counts(value: object, label: str) -> Mapping[str, int]:
    if not isinstance(value, Mapping):
        raise _invalid(f"{label} must be a mapping of item to count")
    for item, count in value.items():
        _segment(item, f"{label} item")
        _int(count, f"{label} count", 0)
    return MappingProxyType(dict(sorted(value.items())))


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One applied operation as one holder logged it: the whole operation, plus where else it landed."""

    op: str
    seq: int
    at: str
    lines: tuple[Line, ...]
    peers: Mapping[InstanceRef, int] = field(default_factory=dict)
    after: Mapping[str, int] = field(default_factory=dict)
    note: str | None = None
    cause: str | None = None

    def __post_init__(self) -> None:
        operation = self.operation  # validates op id, lines, note, cause and the campaign
        _int(self.seq, "seq", 1, 10 ** 15)
        check_timestamp(self.at)
        if not isinstance(self.peers, Mapping):
            raise _invalid("peers must be a mapping of holder to seq")
        others = set(operation.holders)
        for holder, seq in self.peers.items():
            _holder(holder, "peer")
            if holder not in others:
                raise _invalid(f"peer {holder} is not a holder of operation {self.op}")
            _int(seq, "peer seq", 1, 10 ** 15)
        object.__setattr__(self, "peers", MappingProxyType(dict(sorted(self.peers.items(), key=lambda p: str(p[0])))))
        object.__setattr__(self, "after", _counts(self.after, "after"))

    @property
    def operation(self) -> Operation:
        return Operation(self.op, self.lines, self.note, self.cause)

    def same_operation(self, op: Operation) -> bool:
        return (self.op, self.lines, self.note, self.cause) == (op.op_id, op.lines, op.note, op.cause)


@dataclass(frozen=True, slots=True)
class Holdings:
    """One holder's stacks and bounded operation log. An absent stack is a count of 0."""

    holder: InstanceRef
    seq: int = 0
    evicted_through: int = 0
    stacks: Mapping[str, int] = field(default_factory=dict)
    log: tuple[LogEntry, ...] = ()
    extras: JsonObject = field(default_factory=JsonObject)

    def __post_init__(self) -> None:
        _holder(self.holder, "holder")
        _int(self.seq, "seq", 0, 10 ** 15)
        _int(self.evicted_through, "evicted_through", 0, self.seq)
        stacks = _counts(self.stacks, "stacks")
        if any(count == 0 for count in stacks.values()):
            raise _invalid("a stack holds at least 1; having none is no stack")
        if len(stacks) > MAX_STACKS:
            raise HoldingsRefused("stack_limit", f"a holder has at most {MAX_STACKS} stacks")
        object.__setattr__(self, "stacks", stacks)
        if not isinstance(self.log, tuple) or not all(type(entry) is LogEntry for entry in self.log):
            raise _invalid("log must be a tuple of LogEntry values")
        if len(self.log) > MAX_LOG:
            raise _invalid(f"the log holds at most {MAX_LOG} entries")
        previous, ids = self.evicted_through, set()
        for entry in self.log:
            if entry.seq <= previous or entry.seq > self.seq:
                raise _invalid(f"log seqs must increase within ({self.evicted_through}, {self.seq}]: {entry.seq}")
            if entry.op in ids:
                raise _invalid(f"operation {entry.op} is logged twice")
            if self.holder not in entry.operation.holders:
                raise _invalid(f"operation {entry.op} does not touch {self.holder}")
            if self.holder in entry.peers:
                raise _invalid(f"operation {entry.op} names {self.holder} as its own peer")
            previous = entry.seq
            ids.add(entry.op)
        try:
            object.__setattr__(self, "extras", freeze_object(self.extras))
        except JsonValueError as error:
            raise _invalid(f"extras: {error}") from None
        clash = sorted((IDENTITY_KEYS | PAYLOAD_FIELDS) & set(self.extras))
        if clash:
            raise _invalid(f"extras may not use the reserved keys {clash}")

    @classmethod
    def empty(cls, holder: InstanceRef) -> Holdings:
        return cls(holder)

    def count(self, item: str) -> int:
        return self.stacks.get(item, 0)

    def entry(self, op_id: str) -> LogEntry | None:
        for entry in reversed(self.log):
            if entry.op == op_id:
                return entry
        return None
