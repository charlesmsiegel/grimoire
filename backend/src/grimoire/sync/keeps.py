"""The kept versions a pair's syncs delivered to this device, which of them it dismissed, and which
of its own versions ``resolve`` held here (028).

``.sync/peers/<peer>/keeps.json`` (``grimoire.sync.keeps/1``) records, per path:

- ``delivered``: the etags of the **peer's** versions a sync wrote here as the first copy of those
  bytes at that path, recorded **in the same commit as the keep** (``Delivery``), so no crash,
  race or dropped connection after it can leave a written keep unrecorded. One stays recorded
  while a slot holds those bytes, or while it is dismissed, and only while that conflict stands: a
  conflict that ends, or whose base moves, takes its deliveries with it (the slots stay, never
  dismissable), so nothing delivered in one conflict is taken for a delivery of the next;
- ``base``: while the path is in conflict (as the last sync that saw it found it), the archive base
  the conflict stands on; ``None`` once it is not. Only a version of a path in conflict can be
  dismissed: once the devices agree again, a kept copy may be the last copy of a version neither
  device holds any more;
- ``dismissed``: of the delivered versions, the ones this device never wants delivered again in
  that conflict: honoured only on its base, and dropped with it;
- ``held``: this device's own versions that ``resolve`` held under ``.conflicts`` when it replaced
  them with the peer's, recorded in a commit of its own just **before** resolve's data commit (so
  no recovery of that commit can lose it) and kept for as long as a slot holds those bytes, or
  might (``identify`` carries them to the new ID's record);
- ``open``: the peer's version this device holds kept (or failed to keep, or dismissed) for a
  conflict that stands, as the last sync of the pair found it, whatever its base and whoever's keep
  first wrote those bytes here. It only says that the devices still disagree on that path (028's
  turns wait while it stands, unless this device dismissed that version): nothing is ever dismissed
  or deleted by it.

It is what makes ``dismiss`` safe: a file under ``.conflicts/`` may be a kept copy of the peer's
version or this device's own version that ``resolve`` held (perhaps its only copy anywhere), and
the slot names cannot tell them apart. Only an etag delivered, and held by no pair's record, may be
dismissed. A keep is delivered only when it writes the **first** copy of its bytes at its path: a
slot found already holding them, or a second copy beside one, may be a hold (even one whose record
was lost to damage), so those bytes never become dismissable through it. A slot that cannot be read
is unknown, never absent (``slot_stands``): it may be such a hold.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator, Mapping

from grimoire.store import InvalidEnvelope, LibraryStore, NameCollision, StoreError, etag
from grimoire.store.engine import MAX_CONFLICT_ATTEMPTS
from grimoire.store.jsonfmt import MAX_FILE_BYTES
from grimoire.store.layout import conflict_parts
from grimoire.store.syncstate import sync_peers

from .bookkeeping import read_state, stage as stage_states
from .decide import archive_candidate
from .documents import NO_VERSION, KeepsView, KeptEntry, check_device_id, keeps_name, parse_keeps, render_keeps
from .model import Base

State = tuple[str, bytes, "str | None"]
#: the largest record this device writes: 010's file cap (a test scales it down, as ``ARCHIVE_LIMIT``)
RECORD_LIMIT = MAX_FILE_BYTES
#: ``(path, etag) -> bool``: whether a kept-version slot of ``path`` on the device holds those bytes,
#: or might (``slot_stands``: an unreadable slot is unknown, so it counts as standing)
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


def _entry(base: str | None, delivered: Iterable[str], dismissed: Iterable[str], held: Iterable[str],
           open: str | None = None) -> KeptEntry | None:
    """The record's one spelling of an entry: a held tag is never delivered, a base only beside a
    delivered version, a dismissal only on a base, and no entry at all when nothing is left."""
    held = frozenset(held)
    delivered = frozenset(delivered) - held
    base = base if delivered else None
    dismissed = frozenset(dismissed) & delivered if base is not None else frozenset()
    if not delivered and not held and open is None:
        return None
    return KeptEntry(base, delivered, dismissed, held, open)


def _entries(view: KeepsView) -> dict[str, KeptEntry]:
    return dict(view.entries) if view.state == "valid" else {}  # an ignored record honoured nothing


def _write(view: KeepsView, peer: str, devices: tuple[str, str], entries: Mapping[str, KeptEntry]) -> State | None:
    entries, data = fit(view, devices, entries)
    if (view.state == "valid" and dict(entries) == dict(view.entries)) or (view.state == "missing" and not entries):
        return None
    return keeps_name(peer), data, None if view.data is None else etag(view.data)


def _render(devices: tuple[str, str], entries: Mapping[str, KeptEntry]) -> bytes:
    data = render_keeps(devices, entries)
    if len(data) > RECORD_LIMIT:
        raise InvalidEnvelope(f"the kept-version record would be larger than {RECORD_LIMIT} bytes")
    return data


def fit(view: KeepsView, devices: tuple[str, str], entries: Mapping[str, KeptEntry]) -> tuple[dict[str, KeptEntry], bytes]:
    """``entries`` and their bytes, within 010's file cap: one record holds every conflict of the pair.

    With more deliveries than fit (thousands of conflicts at once), the excess goes unrecorded, kept in
    this order and then by path and etag: those this record already dismissed, a dismissal being
    recorded now (``dismiss`` refuses unless every earlier one is kept, so a new dismissal never
    evicts one), deliveries already recorded, then new ones. An unrecorded delivery is only a kept
    copy that is never dismissable, which fails closed, and a sync never stops over it. Holds and
    ``open`` versions are never what gives way (an ``open`` let go would let a scene play over a
    conflict that stands). A sync adds no dismissal and never lengthens one (a dismissal lasts only
    on its own base), so the dismissals already recorded outgrow the cap only if ``resolve`` adds
    holds to a full record: then the last ones by path go, and those versions are delivered again.
    Holds and opens alone overflow only past tens of thousands of versions ``resolve`` held and nobody
    deleted (a hold goes once its slot does), or of conflicts of one pair standing at once; then the
    write raises, and the sync or resolve stops before anything of it is written."""
    entries = dict(entries)
    try:
        return entries, _render(devices, entries)
    except InvalidEnvelope:
        pass
    before = _entries(view)
    def rank(item: tuple[str, str]) -> tuple[int, str, str]:
        path, tag = item
        earlier = before.get(path) or KeptEntry(None, frozenset())
        if tag in entries[path].dismissed:
            return (0 if tag in earlier.dismissed else 1, path, tag)
        return (2 if tag in earlier.delivered else 3, path, tag)
    items = sorted(((path, tag) for path, entry in entries.items() for tag in entry.delivered), key=rank)

    def first(count: int) -> tuple[dict[str, KeptEntry], bytes] | None:
        chosen, kept = set(items[:count]), {}
        for path, entry in entries.items():
            entry = _entry(entry.base, {tag for tag in entry.delivered if (path, tag) in chosen}, entry.dismissed, entry.held,
                           entry.open)
            if entry is not None:
                kept[path] = entry
        try:
            return kept, _render(devices, kept)
        except InvalidEnvelope:
            return None
    best = first(0)
    if best is None:  # the holds and opens alone do not fit
        raise InvalidEnvelope("the kept-version record's holds and open conflicts alone exceed the file size limit")
    low, high = 1, len(items) - 1  # the most deliveries that fit: fewer always fit too
    while low <= high:
        middle = (low + high) // 2
        found = first(middle)
        if found is None:
            high = middle - 1
        else:
            best, low = found, middle + 1
    return best


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
    no slot holds them yet, nor might (every slot named for them reads). Asked before the commit
    that writes them."""
    return {(path, etag(data)) for path, data in keeps if not slot_stands(store, path, etag(data))}


def with_delivered(view: KeepsView, peer: str, devices: tuple[str, str], first: Iterable[tuple[str, str]],
                   bases: Mapping[str, str | None]) -> State | None:
    """The record with ``first`` (from ``first_copies``) delivered, for the commit that writes them,
    each path in conflict on its base in ``bases`` (when known). An ignored record is replaced:
    nothing in it was honoured. A delivery is one of the conflict on its base: none recorded on
    another base carries over (``_same_conflict``)."""
    entries = _entries(view)
    for path, tag in sorted(first):
        before = entries.get(path) or KeptEntry(None, frozenset())
        base = bases.get(path) or before.base
        carried = before.delivered if _same_conflict(before.base, base) else frozenset()
        entry = _entry(base, carried | {tag}, before.dismissed if base == before.base else (), before.held, before.open)
        if entry is not None:
            entries[path] = entry
    return _write(view, peer, devices, entries)


class Delivery:
    """The record a sync's data commit writes beside its keeps (``with_delivered``), as the commit
    stages it, shrinks it when a keep is denied, and finally writes it.

    Every commit that keeps a version also guards the record by compare-and-swap against the one
    the plan read, even when it adds no delivery (the same bytes again, as a no-op write): the plan
    keeps a version only because that record did not dismiss it, so a dismissal committed after the
    plan read it stops the commit instead of letting the older plan write the dismissed copy back."""

    def __init__(self, plan, device: str, keeps) -> None:  # type: ignore[no-untyped-def]
        self.view, self.peer, self.devices = plan.kept_records[device], plan.other(device), plan.devices
        self.keeps = {(keep.path, etag(keep.data)) for keep in keeps}
        self.first = first_copies(plan.stores[device], [(keep.path, keep.data) for keep in keeps])
        candidates = {keep.path: archive_candidate(keep.path, *plan.archives.values()) for keep in keeps}
        # the conflict's base as the archives hold it, unless a pending marker moved it (another conflict)
        self.bases = {path: base_token(base) if plan.bases[path] == base else None for path, base in candidates.items()}
        self.state = self._guarded(self.first, self.keeps)
        self.keys: dict[str, str] = {}

    def _guarded(self, first: set[tuple[str, str]], keeps: set[tuple[str, str]]) -> State | None:
        state = with_delivered(self.view, self.peer, self.devices, first, self.bases)
        if state is not None or not keeps:
            return state
        # unchanged (``_write`` returns None only for a valid record or a missing one with nothing to add)
        data = self.view.data if self.view.data is not None else _render(self.devices, {})
        return keeps_name(self.peer), data, None if self.view.data is None else etag(self.view.data)

    def stage(self, txn) -> None:  # type: ignore[no-untyped-def]
        if self.state is not None:
            self.keys = stage_states(txn, [self.state])

    def deny(self, keeps) -> dict[str, bytes | None]:  # type: ignore[no-untyped-def]
        """The record's new bytes once ``keeps`` were denied (``None``: no keep is left to record or guard)."""
        refused = {(keep.path, etag(keep.data)) for keep in keeps}
        self.state = self._guarded(self.first - refused, self.keeps - refused)
        return {key: None if self.state is None else self.state[1] for key in self.keys}

    def committed(self, written: Iterable[object]) -> tuple[KeepsView, tuple[State, ...]]:
        """The record as it now stands, and the state the commit wrote (for a replayed step)."""
        if self.state is None or not set(self.keys) & set(written):
            return self.view, ()
        return parse_keeps(self.state[1], self.devices), (self.state,)


def next_keeps(view: KeepsView, peer: str, devices: tuple[str, str], bases: Mapping[str, str | None],
               stands: Stands, opened: Mapping[str, str]) -> State | None:
    """The record after a completed sync, or ``None`` if it does not change. It never adds a delivered
    version (the data commit did that): it drops what no longer applies.

    ``bases`` maps every path still in conflict to its archive base token, which the entry takes:
    a dismissal carries over only on its own base, and a path no longer in conflict has none (so
    nothing of it can be dismissed). A held etag carries over while ``stands`` finds its slot, and so
    while its slot cannot be read: only a slot confirmed gone lets a hold's record go. A delivered
    one carries over the same way (also while it is dismissed: the person deletes a dismissed copy),
    but only while the same conflict stands (``_same_conflict``): a path no longer in conflict, or
    in conflict on another base, keeps none, so its old slots stay and are never dismissable. Each
    entry's ``open`` is the sync's own (``opened``)."""
    entries: dict[str, KeptEntry] = {}
    for path, before in _entries(view).items():
        base = bases.get(path)
        dismissed = before.dismissed if base is not None and before.base == base else frozenset()
        delivered = before.delivered if path in bases and _same_conflict(before.base, base) else frozenset()
        entry = _entry(base, {tag for tag in delivered if tag in dismissed or stands(path, tag)},
                       dismissed, {tag for tag in before.held if stands(path, tag)}, opened.get(path))
        if entry is not None:
            entries[path] = entry
    for path, tag in opened.items():
        entries.setdefault(path, KeptEntry(None, frozenset(), open=tag))
    return _write(view, peer, devices, entries)


def _same_conflict(before: str | None, base: str | None) -> bool:
    """Whether deliveries recorded on base ``before`` belong to the conflict now on ``base``: the same
    base, or none recorded yet (a delivery on an unknown base, as in a first sync, is of the conflict
    that is open: one that ends takes its deliveries with it, ``without``). A known base that moved
    or became unknown is another conflict, or may be: its deliveries never carry over."""
    return before is None or before == base


def without(view: KeepsView, peer: str, devices: tuple[str, str], paths: Iterable[str]) -> State | None:
    """The record with ``paths`` (conflicts ``resolve`` or a sync just ended) no longer in conflict, so
    without their deliveries and dismissals, or ``None`` if it does not change. Held etags stay. A
    delivered version's slot stays too, but it is no longer the peer's version of an open conflict:
    once the devices agree it may be the last copy of a version neither holds any more, and a later
    conflict on the same path stands on another base, so it is never dismissable again."""
    paths = set(paths)
    if view.state != "valid" or not paths & set(view.entries):
        return None
    entries = {}
    for path, entry in view.entries.items():
        kept = _entry(None, (), (), entry.held) if path in paths else entry
        if kept is not None:
            entries[path] = kept
    return _write(view, peer, devices, entries)


def ended(view: KeepsView, peer: str, devices: tuple[str, str], open_paths: Iterable[str],
          opened: Mapping[str, str]) -> State | None:
    """The record with every path a sync no longer finds in conflict taken out of conflict (``without``),
    for a commit of its own before that sync writes either archive: a crash after an archive records
    the agreement must never leave a kept copy dismissable (perhaps the last copy of a version
    neither device holds any more) on a base only the later pending step would have cleared. Each
    entry's ``open`` becomes the sync's own (``opened``) in the same commit, so a conflict that stands
    is recorded before either archive moves, even one whose keep wrote nothing (its bytes were here)."""
    entries = _entries(view)
    if view.state != "valid" and not opened:
        return None  # nothing to record: an ignored record is replaced by the pending step's, as before
    for path in set(entries) - set(open_paths):
        entries[path] = KeptEntry(None, frozenset(), held=entries[path].held)
    for path in set(entries) | set(opened):
        before = entries.get(path) or KeptEntry(None, frozenset())
        entry = _entry(before.base, before.delivered, before.dismissed, before.held, opened.get(path))
        if entry is None:
            entries.pop(path, None)
        else:
            entries[path] = entry
    return _write(view, peer, devices, entries)


#: the decision table's own conflicts (``decide``): the two devices disagree on the path itself
_DISAGREE = frozenset({"blocked", "missing_ancestor", "deleted", "edit_delete", "concurrent", "resurrected",
                       "revision_modified"})


def opened(plan, device: str) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """``open`` for ``device`` after ``plan``: each conflict path where it holds the peer's version kept, tried
    to keep it (``Conflict.kept``), or did not because it dismissed that version (``Conflict.dismissed``: the
    entry also says so, and a sync after a dismissal rewrites nothing), by that version; and ``NO_VERSION`` for
    each where the devices disagree but the peer holds no version to keep (it deleted the path, or cannot read
    it): a standing conflict is recorded whether or not anything of the peer's is kept here. A conflict of
    another kind (held, denied, a dependency not yet met, ...) is not a disagreement on the path's bytes."""
    other, found = plan.other(device), {}
    for path, conflict in plan.conflicts.items():
        side = (plan.sides.get(path) or {}).get(other)
        if side is None:
            continue
        if (device in conflict.kept or device in conflict.dismissed) and side.etag is not None:
            found[path] = side.etag
        elif conflict.code in _DISAGREE and side.state != "present":
            found[path] = NO_VERSION
    return found


def with_held(view: KeepsView, peer: str, devices: tuple[str, str], path: str, tag: str) -> State | None:
    """The record with ``tag`` held for ``path``: ``resolve`` commits it just before its hold.
    A delivered entry of the same bytes (the hold reuses its slot) stops being delivered."""
    return _changed(view, peer, devices, path,
                    lambda before: _entry(before.base, before.delivered, before.dismissed, {*before.held, tag}, before.open))


def not_delivered(view: KeepsView, peer: str, devices: tuple[str, str], path: str, tag: str) -> State | None:
    """Another pair's record without ``tag`` delivered: ``resolve`` held those bytes here (in a slot
    that pair's sync may have written), so no pair may ever have them dismissed."""
    if view.state != "valid" or tag not in getattr(view.entries.get(path), "delivered", ()):
        return None
    return _changed(view, peer, devices, path,
                    lambda before: _entry(before.base, before.delivered - {tag}, before.dismissed, before.held, before.open))


def not_delivered_elsewhere(store: LibraryStore, device: str, peer: str, path: str, tag: str) -> list[State]:
    """Every other pair's record on ``device`` without ``tag`` delivered for ``path`` (``not_delivered``):
    what a hold of those bytes for the pair with ``peer`` commits beside its own record, before the
    commit that writes the hold. Written by the device that holds, whichever device started the
    resolve: a peer never sees, and may never write, this device's records of its other pairs."""
    states = []
    for other in other_peers(store, device):
        if other != peer:
            devices: tuple[str, str] = tuple(sorted((device, other)))  # type: ignore[assignment]
            state = not_delivered(read_keeps(store, other, devices), other, devices, path, tag)
            if state is not None:
                states.append(state)
    return states


def records_hold(data: bytes | None, devices: tuple[str, str], path: str, tag: str) -> bool:
    """Whether the record ``data`` holds ``tag`` for ``path``: what must already stand before a commit
    holds those bytes, so they can never be dismissed as a delivered version."""
    view = parse_keeps(data, devices)
    entry = view.entries.get(path) if view.state == "valid" else None
    return entry is not None and tag in entry.held


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


def _slot_reads(store: LibraryStore, path: str, tag: str) -> Iterator[tuple[str, bool]]:
    """Each kept-version slot of ``path`` named for ``tag`` that holds exactly those bytes (``True``) or
    cannot be read (``False``). An unreadable slot is unknown, never absent: it may be a hold, and
    taking it for absent would let the same bytes count as a first copy elsewhere, or a hold's
    record go while its only copy still stands. A look-alike spelling is not the slot: the store
    never writes one, and whatever it holds stays where it is (nothing lists it as safe to delete)."""
    parts = tuple(path.split("/"))
    for attempt in range(MAX_CONFLICT_ATTEMPTS):
        slot = "/".join(conflict_parts(parts, tag, attempt))
        try:
            data = store.read_exact(slot)
        except NameCollision:
            continue
        except (StoreError, OSError):
            yield slot, False
            continue
        if data is not None and etag(data) == tag:
            yield slot, True


def kept_slots(store: LibraryStore, path: str, tag: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The kept-version files of ``path`` on this device that hold exactly the bytes ``tag`` names,
    and those of its slots that cannot be read (any of which might hold them too)."""
    reads = list(_slot_reads(store, path, tag))
    return tuple(slot for slot, read in reads if read), tuple(slot for slot, read in reads if not read)


def slot_stands(store: LibraryStore, path: str, tag: str) -> bool:
    """Whether a kept-version slot of ``path`` holds the bytes ``tag`` names, or might (one cannot be
    read). Every caller fails closed this way: no first copy, a hold kept recorded and not dropped."""
    return next(_slot_reads(store, path, tag), None) is not None


def peer_may_write(old: bytes | None, new: bytes, devices: tuple[str, str],
                   first: Stands, stands: Stands, held: Stands) -> bool:
    """Whether a peer replaying a sync or resolve step may write ``new`` over ``old`` (028's transport).

    Exactly what ``with_delivered``, ``next_keeps``, ``without`` and ``with_held`` can produce: a valid
    record that dismisses nothing ``old`` did not on the same base (dismissing is this device's own
    decision, authorized by ``dismiss``), whose newly delivered etags are first copies its own
    (non-hold) keep ops write in the same step (``first``), whose newly held etags are the one
    version a resolve's own record step may hold here (``held``: never in a sync step, never in
    the step that writes the hold, which 010's recovery could finish without its staged record,
    and never a version the resolve does not replace here, which would keep a delivered version
    from ever being dismissed), and which drops a held etag only once no slot holds those bytes
    (``stands``, which an unreadable slot also satisfies): a peer can never relabel a version this
    device already held as delivered."""
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
        if not all(held(path, tag) for tag in entry.held - (prior.held if prior else frozenset())):
            return False
    return True
