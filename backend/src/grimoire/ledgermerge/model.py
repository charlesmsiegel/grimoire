"""The values of a ledger merge (028): what each device holds, what the merge writes, and why it stops.

A **ledger** is one campaign's 021 accepted events and wrap-ups, the 017 relations they change and the
023 holder files, each file decoded and kept with the envelope fields a rewrite must carry over. The
merge reads a **base** (the last state both devices agreed on) and two **sides**, each named by its
device id, and either writes one result both devices store byte for byte, or stops with a **review**
naming every reason it found. Nothing here reads files, clocks or randomness.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Generic, TypeVar

from grimoire.core.identity import AcceptedRef, HoldingsRef, InstanceRef, RelationRef, WrapUpRef, validate_segment
from grimoire.core.json_value import JsonObject, JsonValueError, freeze_object

T = TypeVar("T")

#: why a merge stopped; the policy layer (028's review, later) decides what each one offers the owner
REASONS = frozenset({
    "same_chat",            # one chat took new events on both devices
    "both_changed",         # a file both devices changed, differently (or a wrap-up written on both)
    "revoked_vs_extended",  # one device revoked an agreed event while the other accepted new ones
    "id_collision",         # one id, two different contents
    "same_effect",          # both devices accepted the same effect (one fingerprint) as two events
    "attempt_reused",       # both devices spent one attempt in two events
    "deleted",              # a file one device no longer has: deletion is never merged silently
    "log_diverged",         # a holder log that does not extend the agreed one, or a cycle between two logs
    "partial_op",           # an operation logged on some of its holders only, on one device
    "hand_edit_overlap",    # a hand edit of a count that the other device also changed
    "partial_revocation",   # a new event revoked with some of its relation changes left in force
    "receipt_mismatch",     # an event whose replay does not reproduce what it recorded
    "order_cycle",          # events that each say they were accepted after the other (a hand edit)
    "new_problem",          # the merged ledger reports a 021 problem neither device reported
})
#: the word for the agreed state in ``Kept.source``. It is not an ID segment, so no device id can be it: a
#: device may well be called ``base`` (``sync.documents.check_device_id`` takes any segment).
BASE = "@base"


def device_tag(device: str) -> str:
    """028's tag for ``device``: the first eight hex digits of the SHA-256 of its id (``devicetag``)."""
    return hashlib.sha256(device.encode()).hexdigest()[:8]


@dataclass(frozen=True)
class Version(Generic[T]):
    """One version of one file as a device (or the base) holds it.

    ``value`` is the decoded record (an ``AcceptedEvent``, ``SceneWrapUp``, 017 ``Relation`` or 023
    ``Holdings``); a file that cannot be decoded is the caller's to report, never merged. ``etag`` names
    the exact bytes: two versions with one etag are the same file. ``owner`` and ``extra`` are the
    envelope's own fields (``ScopeRecord.owner`` and its unknown fields), carried over to a rewrite.
    """

    value: T
    etag: str
    owner: str | None = None
    extra: Mapping[str, object] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if type(self.etag) is not str or not self.etag:
            raise ValueError("a version's etag must be nonempty text")
        if self.owner is not None and (type(self.owner) is not str or not self.owner.strip()):
            raise ValueError("owner must be nonempty text or None")
        try:
            object.__setattr__(self, "extra", freeze_object(self.extra))
        except JsonValueError as error:
            raise ValueError(f"extra: {error}") from None

    @property
    def meta(self) -> tuple[str | None, JsonObject]:
        """What a rewrite carries over from the envelope."""
        return self.owner, self.extra  # type: ignore[return-value]


@dataclass(frozen=True)
class Ledger:
    """One campaign's ledger files as one device (or the base) holds them, decoded."""

    campaign_id: str
    events: Mapping[AcceptedRef, Version] = field(default_factory=dict)
    wrapups: Mapping[WrapUpRef, Version] = field(default_factory=dict)
    relations: Mapping[RelationRef, Version] = field(default_factory=dict)
    holdings: Mapping[InstanceRef, Version] = field(default_factory=dict)  # by holder

    def __post_init__(self) -> None:
        validate_segment(self.campaign_id, "campaign_id")
        for name in ("events", "wrapups", "relations", "holdings"):
            files = getattr(self, name)
            if not isinstance(files, Mapping) or not all(type(item) is Version for item in files.values()):
                raise ValueError(f"{name} must map each file to its Version")
            if any(key.campaign_id != self.campaign_id for key in files):
                raise ValueError(f"{name} names a file of another campaign")
            object.__setattr__(self, name, MappingProxyType(dict(files)))
        for files in (self.events, self.wrapups, self.relations):
            for key, version in files.items():
                if getattr(version.value, "ref", None) != key:
                    raise ValueError(f"the version filed under {key} is another record")
        for holder, version in self.holdings.items():
            if getattr(version.value, "holder", None) != holder:
                raise ValueError(f"the holdings filed under {holder} are another holder's")


@dataclass(frozen=True)
class Kept:
    """The merged file is exactly this existing version: ``source`` (``base`` or a device id) holds it."""

    etag: str
    source: str


@dataclass(frozen=True)
class Rewrite:
    """The merged file is new bytes: this payload under this schema, in the store's scope-record envelope.

    Every field is decided from the inputs alone, so both devices render the same bytes: the payload
    comes from the record family's own codec, ``owner`` and ``extra`` from the envelope, and ``previous``
    is the etag of the agreed version (or of the lowest-ranked device's, for a file the base lacks).
    """

    schema: str
    payload: Mapping[str, object]
    owner: str | None
    extra: Mapping[str, object]
    previous: str | None


Output = Kept | Rewrite
Address = AcceptedRef | WrapUpRef | RelationRef | HoldingsRef


@dataclass(frozen=True, order=True)
class ReviewItem:
    """One reason the merge stopped: ``subject`` is the record, operation or chat it is about (ids only)."""

    reason: str
    subject: str
    detail: str = ""

    def __post_init__(self) -> None:
        # One of REASONS, or a refusal passed through by name: 023's (``insufficient``, ``stale_count``, ...)
        # or 021's planner's (``precondition_failed``, ``missing_endpoint``, ...).
        if type(self.reason) is not str or not self.reason.isidentifier():
            raise ValueError(f"a review reason is an identifier, not {self.reason!r}")


@dataclass(frozen=True)
class Review:
    """The merge stopped: nothing is written. Every reason found, sorted, so both devices see one list."""

    items: tuple[ReviewItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(sorted(set(self.items))))
        if not self.items:
            raise ValueError("a review names at least one reason")

    @property
    def reasons(self) -> frozenset[str]:
        return frozenset(item.reason for item in self.items)


@dataclass(frozen=True)
class Merged:
    """What both devices store: every file of the base and of either side, by address.

    ``order`` is the merged order of the events the base lacks, as their new ordinals number them.
    """

    files: Mapping[Address, Output]
    order: tuple[str, ...] = ()


def ranks(sides: Mapping[str, object]) -> dict[str, int]:
    """Each device's rank: the lower device id first. Exactly two distinct, valid device ids."""
    if not isinstance(sides, Mapping) or len(sides) != 2:
        raise ValueError("a merge takes exactly two sides, by device id")
    for device in sides:
        validate_segment(device, "device")  # so never BASE, which is no segment
    return {device: rank for rank, device in enumerate(sorted(sides))}


def choose(base: Version | None, versions: Mapping[str, Version], order: Mapping[str, int], same) -> str | None:
    """Which version a three-way merge keeps: ``base``, a device id, or ``None`` when both changed it.

    ``same(a, b)`` says whether two versions mean the same file. A side that agrees with the base did
    not change it; the other side's change is kept. Both sides agreeing is kept too (the lower-ranked).
    """
    devices = sorted(versions, key=order.__getitem__)
    changed = [d for d in devices if base is None or not same(versions[d], base)]
    if not changed:
        return BASE
    first = changed[0]
    if all(same(versions[d], versions[first]) for d in changed[1:]):
        return first
    return None
