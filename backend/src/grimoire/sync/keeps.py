"""The kept versions a pair's syncs delivered to this device, which of them it dismissed, and which
of its own versions ``resolve`` held here (028).

``.sync/peers/<peer>/keeps.json`` (``grimoire.sync.keeps/1``) records, per path:

- ``delivered``: the etags of the **peer's** versions a sync wrote here as the first copy of those
  bytes at that path, recorded **in the same commit as the keep** (``Delivery``), so no crash,
  race or dropped connection after it can leave a written keep unrecorded. One stays recorded
  while a slot holds those bytes, or while it is dismissed;
- ``base``: while the path is in conflict (as the last sync that saw it found it), the archive base
  the conflict stands on; ``None`` once it is not. Only a version of a path in conflict can be
  dismissed: once the devices agree again, a kept copy may be the last copy of a version neither
  device holds any more;
- ``dismissed``: of the delivered versions, the ones this device never wants delivered again in
  that conflict: honoured only on its base, and dropped with it;
- ``held``: this device's own versions that ``resolve`` held under ``.conflicts`` when it replaced
  them with the peer's, recorded in resolve's own data commit and kept for as long as a slot holds
  those bytes (``identify`` carries them to the new ID's record).

It is what makes ``dismiss`` safe: a file under ``.conflicts/`` may be a kept copy of the peer's
version or this device's own version that ``resolve`` held (perhaps its only copy anywhere), and
the slot names cannot tell them apart. Only an etag delivered, and held by no pair's record, may be
dismissed. A keep is delivered only when it writes the **first** copy of its bytes at its path: a
slot found already holding them, or a second copy beside one, may be a hold (even one whose record
was lost to damage), so those bytes never become dismissable through it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping

from grimoire.store import LibraryStore, NameCollision, StoreError, etag
from grimoire.store.engine import MAX_CONFLICT_ATTEMPTS
from grimoire.store.layout import conflict_parts
from grimoire.store.syncstate import sync_peers

from .bookkeeping import read_state, stage as stage_states
from .decide import archive_candidate
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
    """The record's one spelling of an entry: a held tag is never delivered, a base only beside a
    delivered version, a dismissal only on a base, and no entry at all when nothing is left."""
    held = frozenset(held)
    delivered = frozenset(delivered) - held
    base = base if delivered else None
    dismissed = frozenset(dismissed) & delivered if base is not None else frozenset()
    if not delivered and not held:
        return None
    return KeptEntry(base, delivered, dismissed, held)


def _entries(view: KeepsView) -> dict[str, KeptEntry]:
    return dict(view.entries) if view.state == "valid" else {}  # an ignored record honoured nothing


def _write(view: KeepsView, peer: str, devices: tuple[str, str], entries: Mapping[str, KeptEntry]) -> State | None:
    if (view.state == "valid" and dict(entries) == dict(view.entries)) or (view.state == "missing" and not entries):
        return None
    return keeps_name(peer), render_keeps(devices, entries), None if view.data is None else etag(view.data)


def _changed(view: KeepsView, peer: str, devices: tuple[str, str], path: str,
             change: Callable[[KeptEntry], KeptEntry | None]) -> State | None:
    entries = _entries(view)
    entry = change(entries.get(path) or KeptEntry(None, frozenset()))
    if entry is None:
        entries.pop(path, None)
    else:
        entries[path] = entry
    return _write(view, peer, devices, entries)


def first_copies(store: LibraryStore, keeps: Iterable[tuple[str, bytes]]) -> set[tuple[str, str]]:
    """``(path, etag)`` of each planned keep that writes the first copy of its bytes at its path:
    no slot holds them yet. Asked before the commit that writes them."""
    return {(path, etag(data)) for path, data in keeps if not slot_stands(store, path, etag(data))}


def with_delivered(view: KeepsView, peer: str, devices: tuple[str, str], first: Iterable[tuple[str, str]],
                   bases: Mapping[str, str | None]) -> State | None:
    """The record with ``first`` (from ``first_copies``) delivered, for the commit that writes them,
    each path in conflict on its base in ``bases`` (when known). An ignored record is replaced:
    nothing in it was honoured."""
    entries = _entries(view)
    for path, tag in sorted(first):
        before = entries.get(path) or KeptEntry(None, frozenset())
        base = bases.get(path) or before.base
        entry = _entry(base, before.delivered | {tag}, before.dismissed if base == before.base else (), before.held)
        if entry is not None:
            entries[path] = entry
    return _write(view, peer, devices, entries)


class Delivery:
    """The record a sync's data commit writes beside its keeps (``with_delivered``), as the commit
    stages it, shrinks it when a keep is denied, and finally writes it."""

    def __init__(self, plan, device: str, keeps) -> None:  # type: ignore[no-untyped-def]
        self.view, self.peer, self.devices = plan.kept_records[device], plan.other(device), plan.devices
        self.first = first_copies(plan.stores[device], [(keep.path, keep.data) for keep in keeps])
        candidates = {keep.path: archive_candidate(keep.path, *plan.archives.values()) for keep in keeps}
        # the conflict's base as the archives hold it, unless a pending marker moved it (another conflict)
        self.bases = {path: base_token(base) if plan.bases[path] == base else None for path, base in candidates.items()}
        self.state = with_delivered(self.view, self.peer, self.devices, self.first, self.bases)
        self.keys: dict[str, str] = {}

    def stage(self, txn) -> None:  # type: ignore[no-untyped-def]
        if self.state is not None:
            self.keys = stage_states(txn, [self.state])

    def deny(self, keeps) -> dict[str, bytes | None]:  # type: ignore[no-untyped-def]
        """The record's new bytes once ``keeps`` were denied (``None``: it no longer changes)."""
        self.state = with_delivered(self.view, self.peer, self.devices,
                                    self.first - {(keep.path, etag(keep.data)) for keep in keeps}, self.bases)
        return {key: None if self.state is None else self.state[1] for key in self.keys}

    def committed(self, written: Iterable[object]) -> tuple[KeepsView, tuple[State, ...]]:
        """The record as it now stands, and the state the commit wrote (for a replayed step)."""
        if self.state is None or not set(self.keys) & set(written):
            return self.view, ()
        return parse_keeps(self.state[1], self.devices), (self.state,)


def next_keeps(view: KeepsView, peer: str, devices: tuple[str, str], bases: Mapping[str, str | None],
               stands: Stands) -> State | None:
    """The record after a completed sync, or ``None`` if it does not change. It never adds a delivered
    version (the data commit did that): it drops what no longer applies.

    ``bases`` maps every path still in conflict to its archive base token, which the entry takes:
    a dismissal carries over only on its own base, and a path no longer in conflict has none (so
    nothing of it can be dismissed). A delivered or held etag carries over while ``stands`` finds
    its slot (a delivered one also while it is dismissed: the person deletes a dismissed copy)."""
    entries: dict[str, KeptEntry] = {}
    for path, before in _entries(view).items():
        base = bases.get(path)
        dismissed = before.dismissed if base is not None and before.base == base else frozenset()
        entry = _entry(base, {tag for tag in before.delivered if tag in dismissed or stands(path, tag)},
                       dismissed, {tag for tag in before.held if stands(path, tag)})
        if entry is not None:
            entries[path] = entry
    return _write(view, peer, devices, entries)


def without(view: KeepsView, peer: str, devices: tuple[str, str], paths: Iterable[str]) -> State | None:
    """The record with ``paths`` (conflicts ``resolve`` just ended) no longer in conflict, so without
    their dismissals, or ``None`` if it does not change. Delivered and held etags stay: their slots
    still stand."""
    paths = set(paths)
    if view.state != "valid" or not paths & set(view.entries):
        return None
    entries = {}
    for path, entry in view.entries.items():
        kept = _entry(None, entry.delivered, (), entry.held) if path in paths else entry
        if kept is not None:
            entries[path] = kept
    return _write(view, peer, devices, entries)


def with_held(view: KeepsView, peer: str, devices: tuple[str, str], path: str, tag: str) -> State | None:
    """The record with ``tag`` held for ``path``: ``resolve`` stages it beside its hold, in one commit.
    A delivered entry of the same bytes (the hold reused its slot) stops being delivered."""
    return _changed(view, peer, devices, path,
                    lambda before: _entry(before.base, before.delivered, before.dismissed, {*before.held, tag}))


def not_delivered(view: KeepsView, peer: str, devices: tuple[str, str], path: str, tag: str) -> State | None:
    """Another pair's record without ``tag`` delivered: ``resolve`` held those bytes here (in a slot
    that pair's sync may have written), so no pair may ever have them dismissed."""
    if view.state != "valid" or tag not in getattr(view.entries.get(path), "delivered", ()):
        return None
    return _changed(view, peer, devices, path,
                    lambda before: _entry(before.base, before.delivered - {tag}, before.dismissed, before.held))


def other_peers(store: LibraryStore, device: str) -> list[str]:
    """Every pair folder of this device named by a device ID (nothing else there is ever read)."""
    found = []
    for peer in sync_peers(store):
        try:
            check_device_id(peer)
        except ValueError:
            continue
        if peer != device:
            found.append(peer)
    return found


def held_anywhere(store: LibraryStore, device: str, path: str, tag: str) -> bool:
    """Whether any pair's record on this device holds ``tag`` for ``path`` (or might: an ignored
    record's holds are unknown). A device paired with several peers holds one pair's resolve
    hold where every other pair's keeps land, so another peer could deliver the same bytes."""
    for peer in other_peers(store, device):
        view = read_keeps(store, peer, tuple(sorted((device, peer))))  # type: ignore[arg-type]
        entry = view.entries.get(path) if view.state == "valid" else None
        if view.state == "ignored" or (entry is not None and tag in entry.held):
            return True
    return False


def kept_slots(store: LibraryStore, path: str, tag: str, *, first: bool = False) -> tuple[str, ...]:
    """The kept-version files of ``path`` on this device that hold exactly the bytes ``tag`` names
    (with ``first``, only the first one found)."""
    parts, found = tuple(path.split("/")), []
    for attempt in range(MAX_CONFLICT_ATTEMPTS):
        slot = "/".join(conflict_parts(parts, tag, attempt))
        try:
            data = store.read_exact(slot)
        except (NameCollision, StoreError, OSError):  # unreadable or a look-alike: never listed
            continue
        if data is not None and etag(data) == tag:
            found.append(slot)
            if first:
                break
    return tuple(found)


def slot_stands(store: LibraryStore, path: str, tag: str) -> bool:
    return bool(kept_slots(store, path, tag, first=True))


def peer_may_write(old: bytes | None, new: bytes, devices: tuple[str, str],
                   first: Stands, stands: Stands) -> bool:
    """Whether a peer replaying a sync step may write ``new`` over ``old`` (028's transport).

    Exactly what ``with_delivered``, ``next_keeps`` and ``with_held`` can produce: a valid record that
    dismisses nothing ``old`` did not on the same base (dismissing is this device's own decision,
    authorized by ``dismiss``), whose newly delivered etags are first copies its own keep ops write
    in the same step (``first``), and which drops a held etag only once no slot holds those bytes
    (``stands``): a peer can never relabel a version this device already held as delivered."""
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
        if not all(first(path, tag) for tag in entry.delivered - (prior.delivered if prior else frozenset())):
            return False
    return True
