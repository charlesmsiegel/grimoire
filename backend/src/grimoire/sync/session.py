"""Plan and apply a transport-free sync between two open libraries (docs/SYNC.md).

``plan_sync`` only reads. ``apply_sync`` writes in a fixed order — data(a), data(b), verify,
archive(b), archive(a), pending(b), pending(a), where ``a`` is the lower device ID — each step
one 010 transaction. Nothing here reads a clock, a modification time or randomness: decisions
depend only on bytes, the two devices' own archives and their unfinished-sync markers.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from grimoire.core.identity import SourceRef
from grimoire.store import (
    CrossDevice, ImmutableRevision, InvalidEnvelope, InvalidSourceState, LibraryStore, LinkRefused,
    NameCollision, RevisionReferenced, StaleWrite, StoreError, UnstorableIdentifier, WriteConflict, etag,
)
from grimoire.store.jsonfmt import MAX_FILE_BYTES
from grimoire.store.layout import classify, conflict_parts, definition_parts, instance_parts, kept_address, manifest_parts
from grimoire.store.replica import CanonicalScan, instances_named, parse_exact, pinned_by, previous_of

from .decide import archive_candidate, decide, derive_base
from .documents import (
    DEVICE_FILE, ArchiveView, PendingView, syncable, archive_name, check_device_id, parse_archive, parse_device,
    parse_pending, pending_name, render_archive, render_device, render_pending, render_retired,
    named_paths, is_retired,
)
from .errors import (
    ArchiveTooLarge, IdentityExists, NoDeviceIdentity, ResolveRefused, SameDevice, SyncInterrupted,
    SyncNotQuiet,
)
from .model import CAMPAIGN_LOCAL, HELD_BACK_KINDS, HOLDS_RELATIONS, Base, Side

_SLOT = re.compile(r"~[0-9a-f]{12}(?:\.([1-9][0-9]*))?$")

#: Store refusals that concern one path or unit: that unit becomes ``unapplicable``.
PER_PATH_ERRORS = (InvalidSourceState, NameCollision, UnstorableIdentifier, LinkRefused, CrossDevice,
                   InvalidEnvelope, ImmutableRevision, RevisionReferenced)
#: The largest archive this slice writes (one file under the store's limit; no compaction yet).
ARCHIVE_LIMIT = MAX_FILE_BYTES



# -- identity ------------------------------------------------------------------------

def _state(store: LibraryStore, name: str) -> bytes | None:
    """A ``.sync`` file's bytes; an unreadable one stops the sync (it must never look missing).

    An I/O error, a directory where the file belongs, or a link all stop the sync the same way.
    """
    try:
        return store.read_sync_state(name)
    except (OSError, UnstorableIdentifier, LinkRefused) as error:
        raise SyncInterrupted(f"cannot read .sync/{name} on {store.root}: {error}") from error


def device_of(store: LibraryStore) -> str:
    try:
        data = store.read_sync_state(DEVICE_FILE)
    except (UnstorableIdentifier, LinkRefused) as error:  # not a file: no usable identity
        raise NoDeviceIdentity(f"{store.root}/.sync/{DEVICE_FILE} is not a readable file: {error}") from error
    except OSError as error:
        raise SyncInterrupted(f"cannot read .sync/{DEVICE_FILE} on {store.root}: {error}") from error
    device = parse_device(data)
    if device is None:
        raise NoDeviceIdentity(f"{store.root} has no readable .sync/{DEVICE_FILE}; call identify first")
    return device


def identify(store: LibraryStore, device_id: str, *, replace: bool = False) -> str:
    """Give a library its device ID (an explicit human or app action; IDs are never generated here).

    A damaged identity is replaced. Changing a valid ID needs ``replace=True`` (the fix for a
    library copied with its ``.sync``). In the same commit every peer archive and marker of the old
    ID is *retired*: kept present, so the next sync of the new ID is conservative, but never valid
    again, so renaming the devices back cannot revive a stale base. If both devices take new IDs,
    their next sync is a true first sync.
    """
    check_device_id(device_id)
    current = store.read_sync_state(DEVICE_FILE)  # a read error propagates as itself
    existing = parse_device(current)
    if existing == device_id:
        return device_id
    if existing is not None and not replace:
        raise IdentityExists(f"{store.root} is already device {existing!r}")
    txn = store.transaction()
    txn.put_sync_state(DEVICE_FILE, render_device(device_id), None if current is None else etag(current))
    if existing is not None:
        for peer in store.sync_peers():
            for name in (archive_name(peer), pending_name(peer)):
                try:
                    data = store.read_sync_state(name)
                except ValueError:  # not a device name: never read as state, nothing to retire
                    break
                if data is not None and not is_retired(data):
                    txn.put_sync_state(name, render_retired(existing, named_paths(data)), etag(data))
    txn.commit(None)
    return device_id


# -- plan ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Copy:
    path: str
    origin: str
    dest: str
    data: bytes
    expected: str | None

    @property
    def etag(self) -> str:
        return etag(self.data)


@dataclass(frozen=True)
class Keep:
    """``device`` keeps ``data`` (the other device's version) beside ``path``."""

    path: str
    device: str
    data: bytes
    slot: int


@dataclass(frozen=True)
class Conflict:
    path: str
    code: str
    detail: str = ""
    #: device -> whether it now holds the other device's version under ``.conflicts``
    kept: Mapping[str, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class SyncReport:
    devices: tuple[str, str]
    copied: tuple[tuple[str, str], ...] = ()  # (path, destination device)
    conflicts: tuple[Conflict, ...] = ()
    skipped: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    notices: tuple[str, ...] = ()
    wrote: bool = False
    consumed_approvals: tuple[str, ...] = ()

    def conflict(self, path: str) -> Conflict | None:
        return next((found for found in self.conflicts if found.path == path), None)


@dataclass
class SyncPlan:
    devices: tuple[str, str]
    stores: dict[str, LibraryStore]
    scans: dict[str, CanonicalScan]
    archives: dict[str, ArchiveView]
    pendings: dict[str, PendingView]
    paths: tuple[str, ...]
    sides: dict[str, dict[str, Side]]
    bases: dict[str, Base]
    copies: dict[str, list[Copy]] = field(default_factory=dict)
    keeps: dict[str, list[Keep]] = field(default_factory=dict)
    conflicts: dict[str, Conflict] = field(default_factory=dict)
    agreed: set[str] = field(default_factory=set)
    notices: list[str] = field(default_factory=list)
    fallback: set[str] = field(default_factory=set)  # devices whose copies were admitted unit by unit
    skipped: dict[str, tuple[str, ...]] = field(default_factory=dict)  # per device: reported, never touched
    held: set[str] = field(default_factory=set)  # held-back paths still tracked: no decision, no archive change

    def other(self, device: str) -> str:
        return self.devices[1] if device == self.devices[0] else self.devices[0]

    def preview(self) -> dict[str, dict[str, bytes]]:
        """Per device, the exact bytes each authorized write will hold, keyed as approvals are."""
        found: dict[str, dict[str, bytes]] = {}
        for device in self.devices:
            txn = self.stores[device].transaction()
            _stage(txn, self.copies[device], self.keeps[device])
            found[device] = {key: data for key, data in txn.preview().items() if not key.startswith(".sync/")}
        return found


def _side(scan, path: str) -> Side:  # type: ignore[no-untyped-def]
    reason = scan.blocked_reason(path)
    if reason is not None:
        return Side.blocked(reason)
    data = scan.files.get(path)
    if data is None:
        return Side.absent()
    return Side.present(etag(data), previous_of(data))


def _current(side: Side) -> str | None:
    return side.etag if side.state == "present" else None


def plan_sync(left: LibraryStore, right: LibraryStore) -> SyncPlan:
    """Decide every path; read only. Raises ``SameDevice``/``NoDeviceIdentity`` or ``SyncInterrupted``."""
    first, second = sorted((left, right), key=lambda store: os.fspath(store.root))  # one lock order: no deadlock
    with first.cached_reads(), second.cached_reads():
        return _plan(left, right)


def _plan(left: LibraryStore, right: LibraryStore) -> SyncPlan:
    ids = device_of(left), device_of(right)
    if ids[0] == ids[1]:
        raise SameDevice(f"both libraries are device {ids[0]!r}; give one a new identity")
    stores = dict(zip(ids, (left, right)))
    pair: tuple[str, str] = tuple(sorted(ids))  # type: ignore[assignment]
    a, b = pair
    scans = {device: stores[device].scan() for device in pair}
    other = {a: b, b: a}
    archives = {device: parse_archive(_state(stores[device], archive_name(other[device])), pair)
                for device in pair}
    pendings = {device: parse_pending(_state(stores[device], pending_name(other[device])), pair)
                for device in pair}
    notices = [f"{device}: archive ignored ({view.reason})" for device, view in archives.items()
               if view.state == "ignored"]
    notices += [f"{device}: pending marker ignored ({view.reason})" for device, view in pendings.items()
                if view.state == "ignored"]
    paths: set[str] = set()
    for device in pair:
        scan = scans[device]
        paths |= {path for path in (*scan.files, *scan.blocked) if syncable(path)}
        paths |= set(archives[device].entries) | set(pendings[device].entries)
        paths |= archives[device].named | pendings[device].named  # ignored: uncertain, never forgotten
    held = _held_campaigns(scans.values())
    skipped = {device: tuple(sorted({*scans[device].skipped, *(
        path for path in (*scans[device].files, *scans[device].blocked)
        if _kind(path) in HELD_BACK_KINDS or _held_relation(path, held))})) for device in pair}
    ordered = tuple(sorted(paths))  # held relations stay: no decision, but their bookkeeping is kept
    _check_archive_size(pair, ordered)
    sides = {path: {device: _side(scans[device], path) for device in pair} for path in ordered}
    bases = {path: derive_base(path, (archives[a], archives[b]), (pendings[a], pendings[b]),
                               (_current(sides[path][a]), _current(sides[path][b]))) for path in ordered}
    plan = SyncPlan(pair, stores, scans, archives, pendings, ordered, sides, bases,
                    copies={a: [], b: []}, keeps={a: [], b: []}, notices=notices, skipped=skipped,
                    held={path for path in ordered if _held_relation(path, held)})
    for path in ordered:
        if path in plan.held:  # never decided, copied or archived; an uncertain base stays pending
            continue
        decision = decide(syncable(path), bases[path], sides[path][a], sides[path][b])  # type: ignore[arg-type]
        if decision.action == "agreed":
            plan.agreed.add(path)
        elif decision.action == "conflict":
            plan.conflicts[path] = Conflict(path, decision.code or "conflict")
        else:
            origin = a if decision.origin == "left" else b
            dest = other[origin]
            plan.copies[dest].append(Copy(path, origin, dest, scans[origin].files[path],
                                          _current(sides[path][dest])))
    for device in pair:
        _admit(plan, device)
    _group_knowledge(plan)
    for device in pair:
        _plan_keeps(plan, device)
    for device in pair:  # the whole planned commit, once: interactions between units are caught here
        if not plan.copies[device] or (not plan.keeps[device] and device not in plan.fallback):
            continue  # what remains was already checked as one whole
        txn = stores[device].transaction()
        _stage(txn, plan.copies[device], plan.keeps[device])
        if txn.preview():
            error = _preflight(txn, "the combined plan")
            if error is not None:
                raise SyncInterrupted(f"the planned writes for {device} conflict with each other: {error}")
    return plan


def _check_archive_size(pair: tuple[str, str], paths: tuple[str, ...]) -> None:
    # each path costs at most one archive entry and one pending entry (two etags) beside its text
    estimate = len(paths) * 300 + sum(len(path.encode("utf-8")) for path in paths)
    if estimate > ARCHIVE_LIMIT:
        raise ArchiveTooLarge(f"an archive of {len(paths)} paths would exceed {ARCHIVE_LIMIT} bytes")


def _preflight(txn, what: str) -> BaseException | None:  # type: ignore[no-untyped-def]
    """``None`` if the transaction would commit; a per-path refusal; else raise."""
    try:
        txn.check()
    except StaleWrite as error:
        raise SyncInterrupted(f"a library changed while {what} was planned: {error}") from error
    except PER_PATH_ERRORS as error:
        return error
    return None


def _stage(txn, copies: list[Copy], keeps: list[Keep]) -> None:  # type: ignore[no-untyped-def]
    for copy in copies:  # the origin still holds these bytes: an aborted commit drops, never parks, them
        txn.put_exact(copy.path, copy.data, copy.expected, regenerable=True)
    for keep in keeps:
        txn.keep_version(keep.path, keep.data, slot=keep.slot, regenerable=True)  # the peer still holds them


def _manifest_heads(copy: Copy) -> list[str]:
    """The revision files a manifest names as heads. A head no library can store (``con``, ``nul``)
    can never exist anywhere: it gets a placeholder path that no device holds, so the head
    dependency is unmet (the manifest is a ``head_dependency`` conflict) instead of a crash."""
    envelope = parse_exact(tuple(copy.path.split("/")), copy.data)
    paths = []
    for ref, revision in envelope.head_refs().items():  # type: ignore[union-attr]
        try:
            paths.append("/".join(definition_parts(ref.at(revision))))
        except (ValueError, UnstorableIdentifier):
            paths.append(f"<unstorable head {ref.at(revision)}>")
    return paths


def _dependencies(plan: SyncPlan, dest: str, copies: list[Copy]) -> tuple[list[Copy], dict[str, str]]:
    """The copies whose dependencies hold on ``dest``, and why each other one is held (a fixpoint).

    A copy lands only if, once this commit is applied, ``dest`` means what the origin means:
    - every revision it pins (``replica.pinned_by``, the same set the store's reference check uses:
      manifest heads and links, instance ``base``/``derived_from``, any pin in a payload) holds the
      origin's bytes on ``dest``, already or copied in the same commit; a manifest's heads must
      exist on the origin too;
    - every campaign instance a scope or chat names (107's roster actors) that the origin holds is
      present on ``dest``, already or copied in the same commit.
    Holding one copy can unmeet another's dependency, so this repeats until nothing changes.
    """
    kept = list(copies)
    held: dict[str, str] = {}
    dest_files = plan.scans[dest].files  # type: ignore[attr-defined]
    while True:
        by_path = {copy.path: copy for copy in kept}

        def final(path: str) -> bytes | None:
            return by_path[path].data if path in by_path else dest_files.get(path)
        newly = {}
        for copy in kept:
            origin_files = plan.scans[copy.origin].files  # type: ignore[attr-defined]
            parts = tuple(copy.path.split("/"))
            heads = set(_manifest_heads(copy)) if syncable(copy.path) == "manifest" else set()
            pins = {"/".join(definition_parts(ref)) for ref in pinned_by(parts, copy.data)}
            origin_scan, dest_scan = plan.scans[copy.origin], plan.scans[dest]

            def blocked(path: str) -> bool:  # unknown on either side: never read as "absent on both"
                return origin_scan.blocked_reason(path) is not None or dest_scan.blocked_reason(path) is not None
            missing = sorted(path for path in pins | heads if final(path) != origin_files.get(path) or blocked(path)
                             or (path in heads and origin_files.get(path) is None))
            actors = sorted(path for path in ("/".join(instance_parts(ref)) for ref in instances_named(parts, copy.data))
                            if (path in origin_files and final(path) is None) or blocked(path))
            if missing or actors:
                what = (["pinned revisions not identical on " + dest + ": " + ", ".join(missing)] if missing else [])
                what += (["named instances missing on " + dest + ": " + ", ".join(actors)] if actors else [])
                newly[copy.path] = "; ".join(what)
        if not newly:
            return kept, held
        held.update(newly)
        kept = [copy for copy in kept if copy.path not in newly]


def _admit(plan: SyncPlan, dest: str) -> None:
    """Overrides for every copy to ``dest``: name collision, head dependency, then preflight units."""
    store = plan.stores[dest]
    copies = []
    for copy in plan.copies[dest]:
        if store.collides(copy.path):
            plan.conflicts[copy.path] = Conflict(copy.path, "name_collision", "a differently spelled entry exists")
        else:
            copies.append(copy)
    copies, held = _dependencies(plan, dest, copies)
    for path, why in held.items():
        plan.conflicts[path] = Conflict(path, "head_dependency", why)
    everything = store.transaction()
    _stage(everything, copies, [])
    if not copies or _preflight(everything, f"the copies to {dest}") is None:
        plan.copies[dest] = sorted(copies, key=lambda copy: copy.path)  # the common case: one check
        return
    store.transaction().check()  # library-wide problems propagate; they are never blamed on one path
    plan.fallback.add(dest)
    admitted, rejected = _admit_units(store, copies)
    for copy, error in rejected:
        plan.conflicts[copy.path] = Conflict(copy.path, "unapplicable", str(error))
    admitted, held = _dependencies(plan, dest, admitted)  # what depended on a refused unit stays too
    for path, why in held.items():
        plan.conflicts[path] = Conflict(path, "head_dependency", why)
    plan.copies[dest] = admitted


def _admit_units(store: LibraryStore, copies: list[Copy]) -> tuple[list[Copy], list[tuple[Copy, BaseException]]]:
    """Admit copies unit by unit; returns the admitted copies and each refused copy's reason.

    A unit is one campaign-local record, one manifest with the head revisions it names among
    ``copies``, or the other revisions of one entity. Campaign-local units and sources are checked
    on their own (a source's units only when the source as a whole fails); manifests that link to
    other sources are then admitted cumulatively in sorted order, so a cross-source link cycle is
    caught. The caller runs one combined check of everything admitted.
    """
    by_path = {copy.path: copy for copy in copies}
    heads = {copy.path: [path for path in _manifest_heads(copy) if path in by_path]
             for copy in copies if syncable(copy.path) == "manifest"}
    admitted: list[Copy] = []
    rejected: list[tuple[Copy, BaseException]] = []
    for copy in (copy for copy in copies if syncable(copy.path) in CAMPAIGN_LOCAL):  # independent: alone
        txn = store.transaction()
        _stage(txn, [copy], [])
        error = _preflight(txn, copy.path)
        if error is None:
            admitted.append(copy)
        else:
            rejected.append((copy, error))
    sources: dict[str, list[list[Copy]]] = {}
    in_manifest_unit = {path for paths in heads.values() for path in paths}
    for copy in copies:
        kind = syncable(copy.path)
        if kind in CAMPAIGN_LOCAL or copy.path in in_manifest_unit:
            continue
        if kind == "manifest":
            units = sources.setdefault(_source_of(copy.path), [])
            units.insert(0, [copy, *(by_path[path] for path in heads[copy.path])])
            continue
        units = sources.setdefault(_source_of(copy.path), [])
        unit = next((unit for unit in units if syncable(unit[0].path) == "definition"
                     and _entity(unit[0].path) == _entity(copy.path)), None)
        if unit is None:
            units.append([copy])
        else:
            unit.append(copy)
    # Each source alone: a source's consistency (heads, kinds, revisions) depends only on itself.
    alone: list[list[Copy]] = []
    for source in sorted(sources):
        txn = store.transaction()
        _stage(txn, [copy for unit in sources[source] for copy in unit], [])
        if _preflight(txn, f"source {source}") is None:
            alone.extend(sources[source])
            continue
        for unit in sources[source]:
            txn = store.transaction()
            _stage(txn, unit, [])
            error = _preflight(txn, f"source {source}")
            if error is None:
                alone.append(unit)
            else:
                rejected.extend((copy, error) for copy in unit)
    # Only manifests with cross-source links can make each other invalid (a link cycle across
    # sources): those are admitted cumulatively, in order; every other unit stands on its own.
    accepted = [copy for unit in alone if not _links_out(unit) for copy in unit]
    linked: list[Copy] = []
    for unit in (unit for unit in alone if _links_out(unit)):
        txn = store.transaction()
        _stage(txn, linked + unit, [])
        error = _preflight(txn, f"source {_source_of(unit[0].path)}")
        if error is None:
            linked += unit
        else:
            rejected.extend((copy, error) for copy in unit)
    accepted += linked
    return sorted(admitted + accepted, key=lambda copy: copy.path), rejected


def _links_out(unit: list[Copy]) -> bool:
    """Whether a unit's manifest declares a link to another source (only those interact)."""
    copy = unit[0]
    if syncable(copy.path) != "manifest":
        return False
    manifest = parse_exact(tuple(copy.path.split("/")), copy.data)
    source = manifest.source  # type: ignore[union-attr]
    for link in manifest.links:  # type: ignore[union-attr]
        try:
            if SourceRef.parse(link.object).source != source:
                return True
        except ValueError:
            return True
    return False


def _entity(path: str) -> tuple[str, str]:
    found = classify(tuple(path.split("/")))
    assert found is not None and isinstance(found[1], SourceRef)
    return found[1].kind, found[1].entity_id


def _plan_keeps(plan: SyncPlan, device: str) -> None:
    """Keep-both: ``device`` receives the other device's present version of each conflict path."""
    other, store = plan.other(device), plan.stores[device]
    keeps: list[Keep] = []
    for path, conflict in sorted(plan.conflicts.items()):
        side = plan.sides[path][other]
        mine = plan.sides[path][device]
        if side.state != "present" or (mine.state == "blocked" and mine.reason == "unreadable"):
            continue  # nothing to keep, or this device's own file could not even be read
        base = plan.bases[path]
        if base.kind == "agreed" and side.etag == base.etag and mine.state == "present":
            continue  # the other device holds the version this device already moved on from
        data = plan.scans[other].files[path]  # type: ignore[attr-defined]
        txn = store.transaction()
        try:
            target = txn.keep_version(path, data, regenerable=True)  # the peer still holds these bytes
        except (StoreError, OSError) as error:  # one unreadable kept slot never stops the whole sync
            _kept(plan, path, device, False, str(error))
            continue
        if not txn.preview():
            _kept(plan, path, device, True)  # already kept by an earlier sync
            continue
        slot = _SLOT.search(target)
        assert slot is not None
        keeps.append(Keep(path, device, data, int(slot.group(1) or 0)))
    txn = store.transaction()
    _stage(txn, [], keeps)
    if keeps and _preflight(txn, "kept versions") is not None:
        good = []
        for keep in keeps:  # split only on failure
            single = store.transaction()
            _stage(single, [], [keep])
            error = _preflight(single, keep.path)
            if error is None:
                good.append(keep)
            else:
                _kept(plan, keep.path, device, False, str(error))
        keeps = good
    for keep in keeps:
        _kept(plan, keep.path, device, True)
    plan.keeps[device] = keeps


def _kept(plan: SyncPlan, path: str, device: str, kept: bool, detail: str = "") -> None:
    conflict = plan.conflicts[path]
    marks = {**conflict.kept, device: kept}
    plan.conflicts[path] = Conflict(path, conflict.code, conflict.detail or detail, MappingProxyType(marks))


# -- apply ---------------------------------------------------------------------------

@dataclass
class _Applied:
    published: dict[str, list[Copy]] = field(default_factory=dict)
    kept: dict[str, list[Keep]] = field(default_factory=dict)
    pending_written: dict[str, bytes] = field(default_factory=dict)
    wrote: bool = False
    consumed: list[str] = field(default_factory=list)


def _pending_entries(plan: SyncPlan, device: str) -> dict[str, Mapping[str, object]]:
    """The device's marker entries before this sync's own copies are added.

    Uncertainty is persisted: every path not agreed now whose base was ``uncertain`` (a missing,
    damaged or disagreeing archive, an unreadable marker) keeps an ``{"uncertain": true}`` entry
    until it resolves, because the fresh archives this sync writes no longer say why it was.
    """
    view = plan.pendings[device]
    if view.state == "ignored":  # unknown entries: every path not already agreed stays uncertain
        return {path: {"uncertain": True} for path in plan.paths
                if path not in plan.agreed or _both_absent_uncertain(plan, path)}
    entries = dict(view.entries)
    for path in plan.paths:
        if path not in entries and plan.bases[path].kind == "uncertain" and (
                path not in plan.agreed or _both_absent_uncertain(plan, path)):
            entries[path] = {"uncertain": True}
        elif path not in entries and plan.bases[path].kind == "first" and (
                path in plan.agreed or _held_agreed(plan, path)):
            # agreed now, on a first sync: mark it, so a crash before the archives are written never
            # leaves a "first" base behind (a later one-sided deletion would be undone)
            side = plan.sides[path][device]
            if side.state == "present":
                entries[path] = {"from": side.etag, "to": side.etag}
    return entries


def _kind(path: str) -> str | None:
    found = classify(tuple(path.split("/")))
    return found[0] if found is not None else None


def _held_agreed(plan: SyncPlan, path: str) -> bool:
    """A held path both devices already hold with identical bytes: a verified agreement, recorded so
    that lifting the hold later can never read "never agreed" and re-create a deletion."""
    sides = list(plan.sides[path].values())
    return path in plan.held and all(side.state == "present" for side in sides) and len({side.etag for side in sides}) == 1


def _held_campaigns(scans) -> set[str]:  # type: ignore[no-untyped-def]
    """Campaigns whose relations 021 effects may have changed: any accepted event, or an ``accepted``
    folder (any spelling) that cannot be listed, on either device. Fails closed."""
    held = set()
    for scan in scans:
        for path in (*scan.files, *scan.blocked, *scan.skipped):
            parts = path.split("/")
            if len(parts) >= 3 and parts[0] == "campaigns" and parts[2].casefold() == HOLDS_RELATIONS:
                held.add(parts[1])
        for path in scan.blocked:  # a campaign (or every campaign) whose contents cannot be listed
            parts = path.split("/")
            if path in ("", "campaigns"):
                held.add("*")
            elif len(parts) == 2 and parts[0] == "campaigns":
                held.add(parts[1])
    return held


def _group_knowledge(plan: SyncPlan) -> None:
    """020 commits an append as its records plus one batch: a campaign's knowledge log is one unit.

    If any knowledge path of a campaign is a conflict (diverged batches, a refused or blocked
    record), no knowledge copy of that campaign lands: half an append would leave records no batch
    lists, and the log would refuse every later append. The held copies are reported as skipped.
    """
    stuck = {_knowledge_campaign(path) for path in plan.conflicts} - {None}
    if not stuck:
        return
    for device in plan.devices:
        kept, held = [], []
        for copy in plan.copies[device]:
            (held if _knowledge_campaign(copy.path) in stuck else kept).append(copy)
        plan.copies[device] = kept
        for copy in held:
            plan.skipped[copy.origin] = tuple(sorted({*plan.skipped.get(copy.origin, ()), copy.path}))


def _knowledge_campaign(path: str) -> str | None:
    return path.split("/")[1] if syncable(path) == "knowledge" else None


def _held_relation(path: str, held: set[str]) -> bool:
    """A 017 relation of a campaign whose relations are held back (see ``model.HOLDS_RELATIONS``)."""
    return syncable(path) == "relation" and (path.split("/")[1] in held or "*" in held)


def _both_absent_uncertain(plan: SyncPlan, path: str) -> bool:
    """Absent on both devices, with an uncertain base: agreed now, but not resolvable (see _resolved)."""
    return plan.bases[path].kind == "uncertain" and all(
        side.state == "absent" for side in plan.sides[path].values())


def _report(plan: SyncPlan, applied: _Applied) -> SyncReport:
    copied = tuple(sorted((copy.path, device) for device, copies in applied.published.items() for copy in copies))
    skipped = {device: plan.skipped.get(device, plan.scans[device].skipped) for device in plan.devices}
    return SyncReport(plan.devices, copied, tuple(plan.conflicts[path] for path in sorted(plan.conflicts)),
                      MappingProxyType(skipped), tuple(plan.notices), applied.wrote, tuple(applied.consumed))


def apply_sync(plan: SyncPlan, handles: Mapping[str, object],
               approvals: Mapping[str, Mapping[str, str]] | None = None) -> SyncReport:
    """Write the plan: data(a), data(b), verify, archive(b), archive(a), pending(b), pending(a)."""
    approvals = approvals or {}
    applied = _Applied()
    for device in plan.devices:
        _commit_data(plan, device, handles[device], approvals.get(device, {}), applied)
    _verify(plan, applied)
    resolved = _resolved(plan, applied)
    for device in reversed(plan.devices):
        _commit_archive(plan, device, resolved, applied)
    for device in reversed(plan.devices):
        _commit_pending(plan, device, resolved, applied)
    return _report(plan, applied)


def _commit_data(plan: SyncPlan, device: str, handle: object, approvals: Mapping[str, str],
                 applied: _Applied) -> None:
    store = plan.stores[device]
    copies, keeps = list(plan.copies[device]), list(plan.keeps[device])
    old = plan.pendings[device].data
    # The marker is written with the data, before any archive is rewritten: a fresh archive no
    # longer says why a path was uncertain, so the marker must already say it (R: crash between
    # archive and pending steps).
    marker = [_marker(plan, device, copies)]  # the bytes the marker will hold (on_denied may change them)
    if not copies and not keeps and not _marker_needed(plan, marker[0], old):
        applied.published[device], applied.kept[device] = [], []
        return
    txn = store.transaction()
    try:
        _stage(txn, copies, keeps)
    except StaleWrite as error:  # a planned kept-version slot was taken meanwhile
        raise SyncInterrupted(f"the data commit on {device} stopped: {error}", _report(plan, applied)) from error
    marker_key = None
    if _marker_needed(plan, marker[0], old):
        marker_key = txn.put_sync_state(pending_name(plan.other(device)), marker[0],
                                        None if old is None else etag(old))
    dropped: set[str] = set()

    def on_denied(keys: frozenset[str]) -> dict[str, bytes | None]:
        denied = {key for key in keys if not key.startswith("kept/")}
        sources = {_source_of(path) for path in denied if syncable(path) not in CAMPAIGN_LOCAL}
        logs = {_knowledge_campaign(path) for path in denied} - {None}  # a log is one unit, too
        changes: dict[str, bytes | None] = {}
        for copy in copies:
            if copy.path in denied or (syncable(copy.path) not in CAMPAIGN_LOCAL
                                       and _source_of(copy.path) in sources) or _knowledge_campaign(copy.path) in logs:
                dropped.add(copy.path)
                if copy.path not in denied:
                    changes[copy.path] = None
                why = "not authorized" if copy.path in denied else "depends on a write that was not authorized"
                plan.conflicts[copy.path] = Conflict(copy.path, "denied", why,
                                                     MappingProxyType({device: False}))
        remaining = [copy for copy in copies if copy.path not in dropped]
        if remaining:  # what is left must still be acceptable on its own (R1): re-admit it
            check = store.transaction()
            _stage(check, remaining, [])
            if _preflight(check, f"the authorized copies to {device}") is not None:
                _, refused = _admit_units(store, remaining)
                for copy, error in refused:
                    dropped.add(copy.path)
                    changes[copy.path] = None
                    plan.conflicts[copy.path] = Conflict(
                        copy.path, "denied", f"depends on a write that was not authorized: {error}",
                        MappingProxyType({device: False}))
                broken = {_knowledge_campaign(copy.path) for copy, _ in refused} - {None}
                for copy in copies:  # the rest of a refused knowledge log's append stays behind too
                    if copy.path not in dropped and _knowledge_campaign(copy.path) in broken:
                        dropped.add(copy.path)
                        changes[copy.path] = None
                        plan.conflicts[copy.path] = Conflict(
                            copy.path, "denied", "its knowledge log's other records were not authorized",
                            MappingProxyType({device: False}))
        while True:  # nothing may land whose dependency or knowledge log was just dropped
            remaining = [copy for copy in copies if copy.path not in dropped]
            _, held = _dependencies(plan, device, remaining)
            broken = {_knowledge_campaign(path) for path in dropped} - {None}
            held.update({copy.path: "its knowledge log's other records were not authorized" for copy in remaining
                         if copy.path not in held and _knowledge_campaign(copy.path) in broken})
            if not held:
                break
            for path, why in held.items():
                dropped.add(path)
                changes[path] = None
                plan.conflicts[path] = Conflict(path, "denied", f"depends on a write that was not authorized: {why}",
                                                MappingProxyType({device: False}))
        for keep in keeps:
            if str(_kept_key(keep)) in keys:
                _kept(plan, keep.path, device, False, "keeping it was not authorized")
        if marker_key is not None:
            remaining = [copy for copy in copies if copy.path not in dropped]
            marker[0] = _marker(plan, device, remaining)
            changes[marker_key] = marker[0] if _marker_needed(plan, marker[0], old) else None
        return changes

    try:
        result = txn.commit(handle, dict(approvals), on_denied=on_denied)
    except (StaleWrite, WriteConflict, SyncInterrupted, *PER_PATH_ERRORS) as error:
        applied.consumed.extend(getattr(error, "consumed_approvals", ()))  # spent without a write (R16)
        raise SyncInterrupted(f"the data commit on {device} stopped: {error}", _report(plan, applied)) from error
    applied.consumed.extend(result.consumed_approvals)
    published = [copy for copy in copies if copy.path in result.etags]
    applied.published[device] = published
    applied.kept[device] = [keep for keep in keeps if str(_kept_key(keep)) in result.etags]
    if marker_key is not None and marker_key in result.etags:
        applied.pending_written[device] = marker[0]
    applied.wrote = applied.wrote or bool(result.etags)
    plan.copies[device] = published


def _marker_needed(plan: SyncPlan, data: bytes, old: bytes | None) -> bool:
    """Whether the marker must be written: it changes, and is not merely an empty marker where none was."""
    return data != old and (old is not None or data != render_pending(plan.devices, {}))


def _marker(plan: SyncPlan, device: str, copies: list[Copy]) -> bytes:
    entries = _pending_entries(plan, device)
    for copy in copies:
        entries[copy.path] = {"from": copy.expected, "to": copy.etag,
                              "base": _archived(archive_candidate(copy.path, *plan.archives.values()))}
    return render_pending(plan.devices, entries)


def _archived(candidate: Base) -> str | None:
    """The agreed etag both archives held when a marker was written (``None`` if none): with it, a
    marker is stale only once the archives have moved on to its ``to`` (``decide.derive_base``)."""
    return candidate.etag if candidate.kind == "agreed" else None


def _kept_key(keep: Keep) -> object:
    return kept_address(tuple(keep.path.split("/")), etag(keep.data), keep.slot)


def _source_of(path: str) -> str:
    found = classify(tuple(path.split("/")))
    assert found is not None
    return found[1] if found[0] == "manifest" else found[1].source  # type: ignore[return-value, union-attr]


def _verify(plan: SyncPlan, applied: _Applied) -> None:
    """Re-read every file this sync published or kept; its bytes must be the planned ones."""
    for device in plan.devices:
        store = plan.stores[device]
        checks = [(copy.path, copy.data) for copy in applied.published.get(device, [])]
        checks += [("/".join(conflict_parts(tuple(keep.path.split("/")), etag(keep.data), keep.slot)), keep.data)
                   for keep in applied.kept.get(device, [])]
        for path, data in checks:
            found = store.read_exact(path)
            if found is None or etag(found) != etag(data):
                raise SyncInterrupted(f"verification failed on {device}: {path} does not hold the planned bytes",
                                      _report(plan, applied))


def _final(plan: SyncPlan, applied: _Applied, path: str, device: str) -> Side:
    for copy in applied.published.get(device, []):
        if copy.path == path:
            return Side.present(copy.etag)
    return plan.sides[path][device]


def _resolved(plan: SyncPlan, applied: _Applied) -> dict[str, tuple[str, str] | None]:
    """Paths both devices now verifiably agree on, with their new archive entry (``None``: no entry)."""
    a, b = plan.devices
    found: dict[str, tuple[str, str] | None] = {}
    for path in plan.paths:
        if path in plan.held and not _held_agreed(plan, path):
            continue  # a held path is never decided; only an agreement both devices already hold is recorded
        left, right = _final(plan, applied, path, a), _final(plan, applied, path, b)
        if "blocked" in (left.state, right.state) or left.state != right.state:
            continue
        if left.state == "present":
            if left.etag == right.etag:
                found[path] = ("agreed", left.etag)  # type: ignore[assignment]
            continue
        base = plan.bases[path]  # both absent: keep (or derive) a tombstone
        candidates = sorted({entry[1] for view in plan.archives.values() if view.state == "valid"
                             for entry in [view.entries.get(path)] if entry is not None}
                            | {entry["to"] for view in plan.pendings.values() if view.state == "valid"  # type: ignore[misc]
                               for entry in [view.entries.get(path)] if entry is not None and "to" in entry})
        if base.kind in ("agreed", "deleted"):
            found[path] = ("deleted", base.etag)  # type: ignore[assignment]
        elif len(candidates) == 1:
            found[path] = ("deleted", candidates[0])
        elif candidates:  # disagreeing tombstones (a crash between the archive commits): stay uncertain
            continue
        elif base.kind != "uncertain":
            found[path] = None
        # absent on both with an uncertain base and no tombstone to derive: a deletion whose
        # agreed bytes are unknown. It stays uncertain (pending), so a later restore is a conflict.
    return found


def _new_archive(plan: SyncPlan, device: str, resolved: Mapping[str, tuple[str, str] | None]) -> bytes:
    view = plan.archives[device]
    entries: dict[str, tuple[str, str]] = dict(view.entries) if view.state == "valid" else {}
    for path, entry in resolved.items():
        if entry is None:
            entries.pop(path, None)
        else:
            entries[path] = entry
    return render_archive(plan.devices, entries)


def _commit_archive(plan: SyncPlan, device: str, resolved: Mapping[str, tuple[str, str] | None],
                    applied: _Applied) -> None:
    data = _new_archive(plan, device, resolved)
    old = plan.archives[device].data
    if data == old:
        return
    if len(data) > ARCHIVE_LIMIT:
        raise ArchiveTooLarge(f"the archive for {device} would be {len(data)} bytes")
    txn = plan.stores[device].transaction()
    txn.put_sync_state(archive_name(plan.other(device)), data, None if old is None else etag(old))
    try:
        txn.commit(None)
    except (StaleWrite, WriteConflict) as error:
        raise SyncInterrupted(f"the archive on {device} changed during the sync: {error}",
                              _report(plan, applied)) from error
    applied.wrote = True


def _commit_pending(plan: SyncPlan, device: str, resolved: Mapping[str, object], applied: _Applied) -> None:
    current = applied.pending_written.get(device, plan.pendings[device].data)
    if device in applied.pending_written:
        entries = dict(parse_pending(current, plan.devices).entries)
    else:
        entries = _pending_entries(plan, device)
    desired = {path: entry for path, entry in entries.items() if path not in resolved}
    if current is None and not desired:
        return
    data = render_pending(plan.devices, desired)
    if data == current:
        return
    txn = plan.stores[device].transaction()
    txn.put_sync_state(pending_name(plan.other(device)), data, None if current is None else etag(current))
    try:
        txn.commit(None)
    except (StaleWrite, WriteConflict) as error:
        raise SyncInterrupted(f"the pending marker on {device} changed during the sync: {error}",
                              _report(plan, applied)) from error
    applied.wrote = True


# -- entry points ----------------------------------------------------------------------

def sync(left: LibraryStore, right: LibraryStore, handles: Mapping[str, object],
         approvals: Mapping[str, Mapping[str, str]] | None = None) -> SyncReport:
    """Plan and apply one sync. ``handles`` (and ``approvals``) are keyed by device ID."""
    return apply_sync(plan_sync(left, right), handles, approvals)


def sync_until_quiet(left: LibraryStore, right: LibraryStore, handles: Mapping[str, object],
                     approvals: Mapping[str, Mapping[str, str]] | None = None, *, attempts: int = 4) -> SyncReport:
    """Sync until one sync completes and writes nothing; retries ``SyncInterrupted``.

    The returned report merges every attempt: all copies, notices and consumed approvals, and
    the conflicts of the final (quiet) sync.
    """
    last: Exception | None = None
    reports: list[SyncReport] = []
    for _ in range(attempts):
        try:
            report = sync(left, right, handles, approvals)
        except SyncInterrupted as error:
            last = error
            if isinstance(error.report, SyncReport):
                reports.append(error.report)
            continue
        reports.append(report)
        if not report.wrote:
            return _merged(reports)
        last = None
    if last is not None:
        if reports and hasattr(last, "report"):  # even an error raised while planning carries what landed
            last.report = _merged(reports)  # type: ignore[attr-defined]
        raise last
    raise SyncNotQuiet(f"every one of {attempts} syncs still wrote something", _merged(reports))


def _merged(reports: list[SyncReport]) -> SyncReport:
    final = reports[-1]

    def union(values) -> tuple:  # type: ignore[no-untyped-def]
        return tuple(dict.fromkeys(value for report in reports for value in values(report)))

    return SyncReport(final.devices, union(lambda report: report.copied), final.conflicts, final.skipped,
                      union(lambda report: report.notices), any(report.wrote for report in reports),
                      union(lambda report: report.consumed_approvals))


def resolve(left: LibraryStore, right: LibraryStore, path: str, keep: str, expected: Mapping[str, str | None],
            handles: Mapping[str, object], approvals: Mapping[str, Mapping[str, str]] | None = None) -> str:
    """End one conflict by choosing the version whose etag is ``keep``; returns the device that changed.

    The other device receives those bytes and keeps its replaced version under ``.conflicts``,
    in one transaction; both archives then record the agreement. See docs/SYNC.md for refusals.
    """
    kind = syncable(path)
    if kind is None:
        raise ResolveRefused(f"{path} is not a synchronized record")
    ids = device_of(left), device_of(right)
    if ids[0] == ids[1]:
        raise SameDevice(f"both libraries are device {ids[0]!r}")
    stores = dict(zip(ids, (left, right)))
    pair: tuple[str, str] = tuple(sorted(ids))  # type: ignore[assignment]
    scans = {device: stores[device].scan() for device in pair}
    if kind == "relation" and _held_relation(path, _held_campaigns(scans.values())):  # the scans decided from
        raise ResolveRefused(f"{path} is held back: its campaign has accepted events (021), and a relation "
                             "they changed must never arrive without them")
    sides = {device: _side(scans[device], path) for device in pair}
    for device, side in sides.items():
        if side.state == "blocked":
            raise ResolveRefused(f"{path} is {side.reason} on {device}")
        if set(expected) != set(pair) or _current(side) != expected[device]:
            raise ResolveRefused(f"{path} changed on {device} since the conflict was reported")
    other = {pair[0]: pair[1], pair[1]: pair[0]}
    pendings, archived = {}, []
    for device in pair:  # a rebuilt archive or marker would forget why every other path is uncertain
        archive = parse_archive(_state(stores[device], archive_name(other[device])), pair)
        archived.append(archive)
        pendings[device] = parse_pending(_state(stores[device], pending_name(other[device])), pair)
        for what, state in (("archive", archive.state), ("pending marker", pendings[device].state)):
            if state == "ignored" or (what == "archive" and state != "valid"):
                raise ResolveRefused(f"the {what} on {device} for this pair is {state}; sync the devices "
                                     "once first, so their uncertainty is recorded")
    winners = [device for device in pair if _current(sides[device]) == keep]
    if not winners:
        raise ResolveRefused("keep must be the current etag of one device's version (deletions cannot be chosen)")
    if len(winners) == 2:
        raise ResolveRefused(f"{path} is already identical on both devices")
    winner = winners[0]
    receiver = pair[1] if winner == pair[0] else pair[0]
    data = scans[winner].files[path]
    mine = scans[receiver].files.get(path)
    txn = stores[receiver].transaction()
    resolved = [path]
    if kind == "knowledge":
        resolved = _knowledge_log_copies(scans, winner, receiver, path)  # the whole log unit, create-only
        for done in resolved:
            txn.put_exact(done, scans[winner].files[done], None, regenerable=True)
    elif kind == "definition":
        # A manifest byte-identical on both devices names the same revision id on both: once the
        # receiver holds the winner's bytes, that reference means the same thing everywhere.
        manifest = "/".join(manifest_parts(_source_of(path)))
        same = scans[receiver].files.get(manifest)
        allowed = frozenset({etag(same)}) if same is not None and same == scans[winner].files.get(manifest) else frozenset()
        txn.put_exact(path, data, None if mine is None else etag(mine), replace_unreferenced=mine is not None,
                      regenerable=True, allowed_manifests=allowed)
    else:
        txn.put_exact(path, data, None if mine is None else etag(mine), regenerable=True)
    if kind == "manifest":
        for head in _manifest_heads(Copy(path, winner, receiver, data, None)):
            theirs, ours = scans[winner].files.get(head), scans[receiver].files.get(head)
            if theirs is None:
                raise ResolveRefused(f"{head} is not readable on {winner}")
            if ours is None:
                txn.put_exact(head, theirs, None, regenerable=True)
                resolved.append(head)
            elif ours != theirs:
                raise ResolveRefused(f"{head} differs between the devices; resolve it first")
    for done in resolved:  # every record resolve writes, head revisions included: sync's dependency rule
        written = scans[winner].files[done]
        for pinned in sorted({"/".join(definition_parts(ref)) for ref in pinned_by(tuple(done.split("/")), written)}
                             - set(resolved)):
            if scans[winner].files.get(pinned) != scans[receiver].files.get(pinned) or any(
                    scans[device].blocked_reason(pinned) is not None for device in pair):
                raise ResolveRefused(f"{pinned}, which {done} pins, differs between the devices; resolve it first")
        for ref in instances_named(tuple(done.split("/")), written):
            named = "/".join(instance_parts(ref))
            if (named in scans[winner].files and named not in scans[receiver].files) or any(
                    scans[device].blocked_reason(named) is not None for device in pair):
                raise ResolveRefused(f"{named}, which {done} names, is missing on {receiver}; sync or resolve it first")
    if mine is not None:
        txn.keep_version(path, mine)
    # Like a sync's copy, the resolve's copy is marked in the same commit: if the archives are not
    # written after it (a crash, a held file), the next sync still knows the peer held these bytes,
    # and a revert on either device is a conflict rather than an "unchanged" side.
    marked = dict(pendings[receiver].entries)
    for done in resolved:
        theirs, ours = scans[winner].files[done], scans[receiver].files.get(done)
        marked[done] = {"from": None if ours is None else etag(ours), "to": etag(theirs),
                        "base": _archived(archive_candidate(done, *archived))}
    old_marker = pendings[receiver].data
    txn.put_sync_state(pending_name(other[receiver]), render_pending(pair, marked),
                       None if old_marker is None else etag(old_marker))
    try:
        txn.check()
    except (StaleWrite, *PER_PATH_ERRORS) as error:
        raise ResolveRefused(f"{path} cannot be resolved on {receiver}: {error}") from error
    txn.commit(handles[receiver], dict((approvals or {}).get(receiver, {})))
    found = stores[receiver].read_exact(path)
    if found is None or etag(found) != keep:
        raise SyncInterrupted(f"verification failed on {receiver}: {path} does not hold the chosen bytes")
    for device in reversed(pair):
        store = stores[device]
        archive = parse_archive(_state(store, archive_name(other[device])), pair)
        if archive.state != "valid":  # damaged since the check: never rebuild it from nothing
            raise SyncInterrupted(f"the archive on {device} changed during resolve")
        entries = dict(archive.entries)
        for done in resolved:
            entries[done] = ("agreed", etag(scans[winner].files[done]))
        rendered = render_archive(pair, entries)
        if rendered != archive.data:
            state = store.transaction()
            state.put_sync_state(archive_name(other[device]), rendered,
                                 None if archive.data is None else etag(archive.data))
            _commit_state(state, device)
    for device in reversed(pair):
        store = stores[device]
        pending = parse_pending(_state(store, pending_name(other[device])), pair)
        if pending.state != "valid" or not set(resolved) & set(pending.entries):
            continue
        state = store.transaction()
        state.put_sync_state(pending_name(other[device]), render_pending(
            pair, {key: value for key, value in pending.entries.items() if key not in resolved}),
            etag(pending.data))  # type: ignore[arg-type]
        _commit_state(state, device)
    return receiver


def _knowledge_log_copies(scans: Mapping[str, CanonicalScan], winner: str, receiver: str, path: str) -> list[str]:
    """What resolving one knowledge path delivers: every knowledge record of its campaign that the
    winner holds and the receiver lacks (020 writes records and their batch as one unit), never a
    replacement. Two logs that diverged (a path both hold with different bytes) cannot be merged yet."""
    if scans[receiver].files.get(path) is not None:
        raise ResolveRefused(f"{path} is a create-only knowledge record both devices hold differently; two "
                             "devices' knowledge logs cannot be merged yet (correct it by appending)")
    campaign = _knowledge_campaign(path)
    log = sorted({candidate for scan in scans.values() for candidate in (*scan.files, *scan.blocked)
                  if _knowledge_campaign(candidate) == campaign})
    copies = []
    for candidate in log:
        if any(scan.blocked_reason(candidate) is not None for scan in scans.values()):
            raise ResolveRefused(f"{candidate}, in the same knowledge log, is blocked; fix it first")
        theirs, ours = scans[winner].files.get(candidate), scans[receiver].files.get(candidate)
        if theirs is not None and ours is not None and theirs != ours:
            raise ResolveRefused(f"{candidate} differs between the devices: their knowledge logs diverged and "
                                 "cannot be merged yet")
        if theirs is not None and ours is None:
            copies.append(candidate)
    return copies


def _commit_state(txn, device: str) -> None:  # type: ignore[no-untyped-def]
    """A trusted ``.sync`` commit after resolve's data landed; a race is ``SyncInterrupted`` (the next
    sync sees equal bytes and records the agreement)."""
    try:
        txn.commit(None)
    except (StaleWrite, WriteConflict) as error:
        raise SyncInterrupted(f"the sync state on {device} changed during resolve: {error}") from error


__all__ = [
    "ARCHIVE_LIMIT", "Conflict", "Copy", "Keep", "SyncPlan", "SyncReport", "apply_sync", "device_of",
    "identify", "plan_sync", "resolve", "sync", "sync_until_quiet", "syncable",
]
