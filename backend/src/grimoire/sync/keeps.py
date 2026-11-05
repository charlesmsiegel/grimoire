"""The kept versions a pair's syncs delivered to this device, which of them it dismissed, and which
of its own versions ``resolve`` held here (028).

``.sync/peers/<peer>/keeps.json`` (``grimoire.sync.keeps/1``) records, per path:

- ``delivered``: the etags of the **peer's** versions a completed sync *wrote* here as kept
  versions, with the archive base the conflict stands on (``base``);
- ``dismissed``: of those, the ones this device never wants delivered again;
- ``held``: this device's own versions that ``resolve`` held under ``.conflicts`` when it replaced
  them with the peer's, recorded in resolve's own data commit (the hold and its entry land
  together) and kept for as long as a slot holds those bytes.

It is what makes ``dismiss`` safe: a file under ``.conflicts/`` may be a kept copy of the peer's
version or this device's own version that ``resolve`` held (perhaps its only copy anywhere), and
the slot names cannot tell them apart. Only an etag delivered and held by no pair's record may be
dismissed. A delivered etag is recorded only for a slot a sync wrote, never one it found standing:
a standing slot with those bytes may be a hold, so recording it would let a dismissal delete it.

The delivered list is rewritten only in a completed sync's last step (pending), after both
archives. A crash before that step leaves a delivered version unrecorded, which only refuses a
dismissal (until the slot is deleted by hand and delivered again): the safe direction. A path's
delivered and dismissed etags are dropped once the path is no longer in conflict or its archive
base moved (``resolve`` drops them in its own commit), so a dismissal never outlives its conflict:
the same bytes in a later conflict are kept again.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from grimoire.store import LibraryStore, NameCollision, StoreError, etag
from grimoire.store.engine import MAX_CONFLICT_ATTEMPTS
from grimoire.store.layout import conflict_parts
from grimoire.store.syncstate import sync_peers

from .bookkeeping import read_state
from .documents import KeepsView, KeptEntry, check_device_id, keeps_name, parse_keeps, render_keeps
from .model import Base

State = tuple[str, bytes, "str | None"]
#: ``(path, etag) -> bool``: whether a kept-version slot of ``path`` on the device holds those bytes
Stands = Callable[[str, str], bool]


def read_keeps(store: LibraryStore, peer: str, devices: tuple[str, str]) -> KeepsView:
    return parse_keeps(read_state(store, keeps_name(peer)), devices)


def base_token(candidate: Base) -> str | None:
    """The archive base a conflict stands on, as the record spells it; ``None`` when no archive
    agrees on one (a first sync, an ignored archive): nothing is recorded or honoured then."""
    if candidate.kind == "none":
        return "none"
    if candidate.kind in ("agreed", "deleted") and candidate.etag is not None:
        return f"{candidate.kind}:{candidate.etag}"
    return None


def archived_token(mine: tuple[str, str] | None, theirs: tuple[str, str] | None) -> str | None:
    """``base_token`` of the base the next sync will read from two archives' entries for one path."""
    if mine != theirs:
        return None
    return "none" if mine is None else f"{mine[0]}:{mine[1]}"


def _entry(base: str | None, delivered: Iterable[str], dismissed: Iterable[str], held: Iterable[str]) -> KeptEntry | None:
    """The record's one spelling of an entry: a held tag is never delivered, a base only with
    something delivered, and no entry at all when nothing is left."""
    held = frozenset(held)
    delivered = frozenset(delivered) - held
    if not delivered and not held:
        return None
    return KeptEntry(base if delivered else None, delivered,
                     frozenset(dismissed) & delivered, held)


def _write(view: KeepsView, peer: str, devices: tuple[str, str], entries: Mapping[str, KeptEntry]) -> State | None:
    if (view.state == "valid" and dict(entries) == dict(view.entries)) or (view.state == "missing" and not entries):
        return None
    return keeps_name(peer), render_keeps(devices, entries), None if view.data is None else etag(view.data)


def next_keeps(view: KeepsView, peer: str, devices: tuple[str, str], bases: Mapping[str, str | None],
               written: Mapping[str, str], stands: Stands) -> State | None:
    """The record after a completed sync, or ``None`` if it does not change.

    ``bases`` maps every path still in conflict to its archive base token; ``written`` maps a path to
    the etag of the peer's version this sync wrote here as a kept version (a slot it found standing
    is not delivery). Delivered and dismissed etags carry over only for a path still in conflict on
    the same base; held etags carry over while ``stands`` finds their slot. An ignored record is
    replaced, since nothing in it was honoured."""
    old = view.entries if view.state == "valid" else {}
    entries: dict[str, KeptEntry] = {}
    for path in sorted({*bases, *old}):
        before = old.get(path)
        held = {tag for tag in (before.held if before else ()) if stands(path, tag)}
        base = bases.get(path)
        same = path in bases and base is not None and before is not None and before.base == base
        delivered = set(before.delivered) if same else set()  # type: ignore[union-attr]
        if base is not None and path in written:
            delivered.add(written[path])
        entry = _entry(base, delivered, before.dismissed if same else (), held)  # type: ignore[union-attr]
        if entry is not None:
            entries[path] = entry
    return _write(view, peer, devices, entries)


def without(view: KeepsView, peer: str, devices: tuple[str, str], paths: Iterable[str]) -> State | None:
    """The record without the delivered and dismissed etags of ``paths`` (conflicts ``resolve`` just
    ended), or ``None`` if it does not change. Their held etags stay: their slots still stand."""
    paths = set(paths)
    if view.state != "valid" or not paths & set(view.entries):
        return None
    entries = {}
    for path, entry in view.entries.items():
        kept = _entry(None, (), (), entry.held) if path in paths else entry
        if kept is not None:
            entries[path] = kept
    return _write(view, peer, devices, entries)


def with_held(view: KeepsView, peer: str, devices: tuple[str, str], path: str, tag: str) -> State | None:
    """The record with ``tag`` held for ``path``: ``resolve`` stages it beside its hold, in one commit.

    An ignored record is replaced (nothing in it was honoured); a delivered entry of the same bytes
    stops being delivered, so it can never be dismissed."""
    entries = dict(view.entries) if view.state == "valid" else {}
    before = entries.get(path)
    entry = _entry(before.base if before else None, before.delivered if before else (),
                   before.dismissed if before else (), {*(before.held if before else ()), tag})
    entries[path] = entry  # type: ignore[assignment]
    return _write(view, peer, devices, entries)


def held_anywhere(store: LibraryStore, device: str, path: str, tag: str) -> bool:
    """Whether any pair's record on this device holds ``tag`` for ``path`` (or might: an ignored
    record's holds are unknown). A device paired with several peers holds one pair's resolve
    hold where every other pair's keeps land, so another peer could deliver the same bytes."""
    for peer in sync_peers(store):
        try:
            check_device_id(peer)
        except ValueError:
            continue  # not a pair's folder: nothing there is ever read
        if peer == device:
            continue
        view = read_keeps(store, peer, tuple(sorted((device, peer))))  # type: ignore[arg-type]
        entry = view.entries.get(path) if view.state == "valid" else None
        if view.state == "ignored" or (entry is not None and tag in entry.held):
            return True
    return False


def kept_slots(store: LibraryStore, path: str, tag: str) -> tuple[str, ...]:
    """The kept-version files of ``path`` on this device that hold exactly the bytes ``tag`` names."""
    parts, found = tuple(path.split("/")), []
    for attempt in range(MAX_CONFLICT_ATTEMPTS):
        slot = "/".join(conflict_parts(parts, tag, attempt))
        try:
            data = store.read_exact(slot)
        except (NameCollision, StoreError, OSError):  # unreadable or a look-alike: never listed
            continue
        if data is not None and etag(data) == tag:
            found.append(slot)
    return tuple(found)


def peer_may_write(old: bytes | None, new: bytes, devices: tuple[str, str],
                   delivered_here: Stands, stands: Stands) -> bool:
    """Whether a peer replaying a sync's last step may write ``new`` over ``old`` (028's transport).

    Exactly what ``next_keeps`` can produce: a valid record that dismisses nothing ``old`` did not on
    the same base (dismissing is this device's own decision, authorized by ``dismiss``), whose newly
    delivered etags are bytes the peer's own keep ops wrote here in this sync (``delivered_here``),
    and which drops a held etag only once no slot holds those bytes (``stands``): a peer can never
    relabel a version ``resolve`` held here as delivered, so never make it dismissable."""
    after, before = parse_keeps(new, devices), parse_keeps(old, devices)
    if after.state != "valid":
        return False
    previous = before.entries if before.state == "valid" else {}
    for path in {*after.entries, *previous}:
        entry, prior = after.entries.get(path), previous.get(path)
        if any(stands(path, tag) for tag in (prior.held if prior else frozenset()) - (entry.held if entry else frozenset())):
            return False
        if entry is None:
            continue
        same = prior is not None and prior.base is not None and prior.base == entry.base
        if not entry.dismissed <= (prior.dismissed if same else frozenset()):  # type: ignore[union-attr]
            return False
        if not all(delivered_here(path, tag) for tag in entry.delivered - (prior.delivered if same else frozenset())):  # type: ignore[union-attr]
            return False
    return True
