"""Plan and apply a transport-free sync between two open libraries (docs/SYNC.md).

``plan_sync`` only reads. ``apply_sync`` writes in a fixed order — data(a), data(b), verify,
archive(b), archive(a), pending(b), pending(a), where ``a`` is the lower device ID — each step
one 010 transaction. Nothing here reads a clock, a modification time or randomness: decisions
depend only on bytes, the two devices' own archives and their unfinished-sync markers.
"""

from __future__ import annotations

import contextlib
import functools
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from grimoire.core.identity import ImageRef, InstanceRef, SourceRef
from grimoire.security import Permission
from grimoire.store import (
    BlobCorrupt, CrossDevice, ImmutableImage, ImmutableRevision, InstanceIdConflict, InvalidEnvelope, InvalidSourceState,
    LibraryStore, LinkRefused, NameCollision, RevisionReferenced, StaleWrite, StoreError, UnstorableIdentifier,
    WriteConflict, etag,
)
from grimoire.store.jsonfmt import MAX_FILE_BYTES
from grimoire.store.layout import (
    chat_parts, classify, conflict_parts, definition_parts, instance_parts, kept_address, logical,
)
from grimoire.store.replica import (
    SHADOWED, CanonicalScan, chats_named, instances_named, parse_exact, pinned_by, previous_of,
)

from . import bookkeeping, keeps as kept_record
from .art import art_needs, art_unmet
from .decide import archive_candidate, decide, derive_base
from .documents import ArchiveView, KeepsView, PendingView, syncable
from .errors import ArchiveTooLarge, SameDevice, SyncInterrupted, SyncNotQuiet
from .identity import device_of, identify
from .model import ASSET_KINDS, CAMPAIGN_LOCAL, HELD_BACK_KINDS, HOLDS_RELATIONS, Base, Side

_SLOT = re.compile(r"~[0-9a-f]{12}(?:\.([1-9][0-9]*))?$")
#: Folders of the held-back families (021 accepted events and wrap-ups, 023 holdings).
_HELD_FOLDERS = frozenset({"accepted", "wrapups", "holdings"})
_HELD = "held back until 028's merge rules exist"

#: Store refusals that concern one path or unit: that unit becomes ``unapplicable``.
#: ``InstanceIdConflict``: a copy that would form a cross-kind instance id pair on its receiver (A2).
PER_PATH_ERRORS = (InvalidSourceState, NameCollision, UnstorableIdentifier, LinkRefused, CrossDevice,
                   InvalidEnvelope, ImmutableRevision, RevisionReferenced, InstanceIdConflict, ImmutableImage)
#: The largest file of a pair's archive or marker: past it, the document is split into shards (028).
ARCHIVE_LIMIT = MAX_FILE_BYTES


# -- plan ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Copy:
    """One file travelling from ``origin`` to ``dest``. A blob carries no bytes (``data`` is ``None``):
    ``size`` and the ``source`` library it is loaded from, one at a time, when the commit needs it."""

    path: str
    origin: str
    dest: str
    data: bytes | None
    expected: str | None
    size: int | None = None
    source: LibraryStore | None = field(default=None, repr=False, compare=False)

    @property
    def etag(self) -> str:
        return etag(self.data) if self.data is not None else _blob_etag(self.path)


def _blob_etag(path: str) -> str:
    """A blob's etag is its name: the sha256 of the bytes it must hold."""
    found = classify(tuple(path.split("/")))
    assert found is not None and found[0] == "blob"
    return "sha256:" + found[1][0]  # type: ignore[index]


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
    #: devices that dismissed the other device's current version (``dismiss``): not kept for them again
    #: while the conflict stands on the same archive base
    dismissed: tuple[str, ...] = ()


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
    ungranted: dict[str, list[Copy]] = field(default_factory=dict)  # blob copies the receiver may not write
    #: per device, the sources its ``load()`` excludes, read once per plan and only if art is copied
    exclusions: dict[str, frozenset[str]] = field(default_factory=dict)
    #: per device, the peer's versions kept there for open conflicts, and those it dismissed (028)
    kept_records: dict[str, KeepsView] = field(default_factory=dict)

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
    if path in scan.blobs:  # listed by name and size; ``_plan`` hashes it when it must (_verify_blobs)
        return Side.present(_blob_etag(path))
    data = scan.files.get(path)
    if data is None:
        return Side.absent()
    return Side.present(etag(data), previous_of(data))


def _current(side: Side) -> str | None:
    return side.etag if side.state == "present" else None


def plan_sync(left: LibraryStore, right: LibraryStore, handles: Mapping[str, object] | None = None) -> SyncPlan:
    """Decide every path; read only. Raises ``SameDevice``/``NoDeviceIdentity`` or ``SyncInterrupted``.

    With the ``handles`` the sync will commit with, a blob copy its receiver's durable authority
    grants no write for is ``denied`` here, without reading the blob (``_ungranted``)."""
    first, second = _lock_order((left, right))
    with first.cached_reads(), second.cached_reads():
        return _plan(left, right, handles or {})


def _lock_order(stores) -> list[LibraryStore]:  # type: ignore[no-untyped-def]
    """The one order in which a thread takes more than one library's lock: by root path.

    A commit that copies a blob loads it lazily from the origin (``put_blob_exact``), taking the
    origin's lock inside the destination's commit; a plan holds both libraries' locks. So every
    commit whose reads can reach the other library holds both locks, taken in this order, before
    it starts (``_locked``): no thread ever waits for the first library while holding the second.
    Two opens of one folder share a root, so the lock's identity breaks the tie (a total order)."""
    return sorted(stores, key=lambda store: (os.fspath(store.root), id(store.lock)))


@contextlib.contextmanager
def _locked(*stores: LibraryStore):  # type: ignore[no-untyped-def]
    """Hold every one of ``stores``' locks (re-entrant, per thread), taken in ``_lock_order``."""
    with contextlib.ExitStack() as stack:
        for store in _lock_order(stores):
            stack.enter_context(store.lock)
        yield


def _plan(left: LibraryStore, right: LibraryStore, handles: Mapping[str, object]) -> SyncPlan:
    ids = device_of(left), device_of(right)
    if ids[0] == ids[1]:
        raise SameDevice(f"both libraries are device {ids[0]!r}; give one a new identity")
    stores = dict(zip(ids, (left, right)))
    pair: tuple[str, str] = tuple(sorted(ids))  # type: ignore[assignment]
    a, b = pair
    scans = {device: stores[device].scan() for device in pair}
    other = {a: b, b: a}
    archives = {device: bookkeeping.read_document(stores[device], "archive", other[device], pair) for device in pair}
    pendings = {device: bookkeeping.read_document(stores[device], "pending", other[device], pair) for device in pair}
    records = {device: kept_record.read_keeps(stores[device], other[device], pair) for device in pair}
    notices = [f"{device}: archive ignored ({view.reason})" for device, view in archives.items()
               if view.state == "ignored"]
    notices += [f"{device}: pending marker ignored ({view.reason})" for device, view in pendings.items()
                if view.state == "ignored"]
    notices += [f"{device}: dismissed kept versions ignored ({view.reason})" for device, view in records.items()
                if view.state == "ignored"]
    paths: set[str] = set()
    for device in pair:
        scan = scans[device]
        paths |= {path for path in (*scan.files, *scan.blocked, *scan.blobs) if syncable(path)}
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
                    copies={a: [], b: []}, keeps={a: [], b: []}, ungranted={a: [], b: []}, notices=notices,
                    skipped=skipped, held={path for path in ordered if _held_relation(path, held)},
                    kept_records=records)
    ungranted = _verify_blobs(plan, handles)
    for path in ordered:
        if path in plan.held:  # never decided, copied or archived; an uncertain base stays pending
            if not _identical(sides[path].values()):  # but a divergence is never silent (C1)
                plan.conflicts[path] = Conflict(path, "held", f"{_HELD}: its campaign has accepted events (021)")
            continue
        decision = decide(syncable(path), bases[path], sides[path][a], sides[path][b])  # type: ignore[arg-type]
        if decision.action == "agreed":
            plan.agreed.add(path)
        elif decision.action == "conflict":
            shadowed = decision.code == "blocked" and any(side.reason == SHADOWED for side in sides[path].values())
            detail = "; ".join(f"{side.reason} on {device}" for device, side in sides[path].items()
                               if side.state == "blocked")
            plan.conflicts[path] = (Conflict(path, "name_collision", "a differently spelled entry exists") if shadowed
                                    else Conflict(path, decision.code or "conflict", detail))
        else:
            origin = a if decision.origin == "left" else b
            dest = other[origin]
            if syncable(path) == "blob":  # never in memory: loaded from the origin when the commit needs it
                copy = Copy(path, origin, dest, None, None, size=scans[origin].blobs[path], source=stores[origin])
                if path in ungranted:  # the commit would deny it: never staged, so never loaded (C2)
                    plan.ungranted[dest].append(copy)
                    plan.conflicts[path] = Conflict(path, "denied", "not authorized", MappingProxyType({dest: False}))
                else:
                    plan.copies[dest].append(copy)
            else:
                plan.copies[dest].append(Copy(path, origin, dest, scans[origin].files[path],
                                              _current(sides[path][dest])))
    _held_families(plan)
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
        if txn.staged():
            error = _preflight(txn, "the combined plan")
            if error is not None:
                raise SyncInterrupted(f"the planned writes for {device} conflict with each other: {error}")
    return plan


def _check_archive_size(pair: tuple[str, str], paths: tuple[str, ...]) -> None:
    # every archive and marker of this sync names a subset of ``paths``: if they fit, so do both
    if bookkeeping.width_for(paths, ARCHIVE_LIMIT) is None:
        raise ArchiveTooLarge(f"an archive of {len(paths)} paths would not fit even split into shards of "
                              f"{ARCHIVE_LIMIT} bytes")


def _identical(sides) -> bool:  # type: ignore[no-untyped-def]
    """Both devices hold the same bytes, or both lack the file. A blocked side always differs."""
    found = list(sides)
    return (all(side.state == "absent" for side in found)
            or (all(side.state == "present" for side in found) and len({side.etag for side in found}) == 1))


def _verify_blobs(plan: SyncPlan, handles: Mapping[str, object]) -> set[str]:
    """Hash a blob at copy or first sight; otherwise trust this pair's agreement (C2).

    A device's blob is read and hashed unless both devices list it at one size and the base already
    records that very content as agreed. So a blob about to be copied is always hashed on its origin,
    a blob this pair has not yet verified together is hashed on each side that lists it, and an agreed
    one is read by no later sync. A hash mismatch (or any read failure) blocks that side (``corrupt``).
    A blob only one device lists is read only if it is to be copied: one in a conflict that copies
    nothing, or one whose receiver may not write it (returned: its copy is ``denied`` unread), is not.
    """
    a, b = plan.devices
    ungranted: set[str] = set()
    for path in plan.paths:
        if syncable(path) != "blob":
            continue
        sides = plan.sides[path]
        listed = [device for device in plan.devices if sides[device].state == "present"]
        name = _blob_etag(path)
        if (len(listed) == 2 and plan.scans[a].blobs[path] == plan.scans[b].blobs[path]  # type: ignore[attr-defined]
                and plan.bases[path] == Base("agreed", name)):
            continue  # trusted: verified when it was copied or first seen, and unchanged in size since
        if len(listed) == 1:
            if decide("blob", plan.bases[path], sides[a], sides[b]).action != "copy":
                continue  # deleted, missing_ancestor or blocked: never copied, so never read
            dest = plan.other(listed[0])
            if plan.stores[dest].collides(path):
                continue  # a name_collision (``_admit``): never copied
            if _ungranted(plan.stores[dest], handles.get(dest), path):
                ungranted.add(path)
                continue
        changed = False
        for device in listed:
            try:
                plan.stores[device].verify_blob(path)
            except (StoreError, OSError, ValueError) as error:
                sides[device] = Side.blocked("corrupt" if isinstance(error, BlobCorrupt) else "unreadable")
                changed = True
        if changed:
            plan.bases[path] = derive_base(path, (plan.archives[a], plan.archives[b]), (plan.pendings[a], plan.pendings[b]),
                                           (_current(sides[a]), _current(sides[b])))
    return ungranted


def _ungranted(store: LibraryStore, handle: object, path: str) -> bool:
    """Whether the commit on ``store`` would certainly deny ``handle`` writing ``path``: the store's
    durable authority issued the handle and no active grant lets its principal apply there (or the
    path is reserved). Read without asking for a decision (decisions are audited; a plan writes
    nothing). Anything unknown, an unbound store included, is ``False``: the commit decides. So is an
    authority that is not usable (closed, or poisoned by a failed flush): its in-memory grants are no
    answer, and the commit's denial for it propagates (docs/SYNC.md "Denied writes")."""
    authority = store.authority
    if handle is None or authority is None:
        return False
    try:
        if authority.usable is not True:  # type: ignore[attr-defined]
            return False
        if not authority.issued(handle):  # type: ignore[attr-defined]
            return False
        resource = logical(tuple(path.split("/")))
        principal = handle.principal  # type: ignore[attr-defined]
        return resource.reserved or not any(grant.covers(principal, Permission.APPLY, resource)
                                            for grant in authority.active_grants())  # type: ignore[attr-defined]
    except (StoreError, AttributeError, TypeError, ValueError):
        return False


def _held_families(plan: SyncPlan) -> None:
    """021 accepted events and wrap-ups and 023 holdings are never copied (their merge rules are 028's),
    but a path the two devices hold differently is a ``held`` conflict, never silent (C1). A held-family
    folder that cannot be listed (in any spelling, or a campaign folder, ``campaigns`` or the root) is a
    ``held`` conflict on that folder: what diverged there is unknowable. Nothing is decided, copied, kept
    or archived for them; they stay listed in ``skipped`` too."""
    scans = [plan.scans[device] for device in plan.devices]
    family = sorted({path for scan in scans for path in (*scan.files, *scan.blocked)
                     if _kind(path) in HELD_BACK_KINDS})
    for path in family:
        if not _identical(_side(scan, path) for scan in scans):
            plan.conflicts[path] = Conflict(path, "held", f"{_HELD}: 021/023 commit several files as one unit")
    for scan in scans:
        for path in scan.blocked:
            parts = path.split("/")
            if path in ("", "campaigns") or (parts[0] == "campaigns" and (
                    len(parts) == 2 or (len(parts) >= 3 and parts[2].casefold() in _HELD_FOLDERS))):
                if _kind(path) not in HELD_BACK_KINDS:  # a file is already decided above
                    plan.conflicts.setdefault(path, Conflict(path, "held", f"{_HELD}: this folder cannot be listed, "
                                                             "so what diverged in it is unknown"))


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
        if copy.data is None:  # a blob: loaded from the origin only when the commit needs its bytes
            assert copy.source is not None and copy.size is not None and copy.expected is None
            txn.put_blob_exact(copy.path, functools.partial(copy.source.read_blob_exact, copy.path, copy.size),
                               size=copy.size)
        else:
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
    - every campaign instance or chat a record names (107's roster actors, 020's holders and the
      chat a batch came from) that the origin holds is present on ``dest``, already or copied in
      the same commit;
    - a 020 knowledge record's campaign exists on ``dest`` (a scope or an instance, 020's own test)
      whenever it exists on the origin;
    - 018 art (``art_needs``) has every file it needs identical, or present, on ``dest``, and every
      source it belongs to or shows a picture of admitted by ``load()`` on both devices: sync never
      creates a state the 018 batch would refuse.
    A path blocked on either side is never read as present or absent: the dependency is unmet.
    Holding one copy can unmeet another's dependency, so this repeats until nothing changes.
    """
    kept = list(copies)
    held: dict[str, str] = {}
    dest_files = plan.scans[dest].files  # type: ignore[attr-defined]
    origins = {device: _campaigns_with_state(plan.scans[device].files) for device in {c.origin for c in copies}}  # type: ignore[attr-defined]
    while True:
        by_path = {copy.path: copy for copy in kept}
        here = _campaigns_with_state([*dest_files, *by_path])

        def final(path: str) -> bytes | None:
            return by_path[path].data if path in by_path else dest_files.get(path)

        def final_etag(path: str) -> str | None:
            return by_path[path].etag if path in by_path else _etag_on(plan, dest, path)
        newly = {}
        for copy in kept:
            origin_files = plan.scans[copy.origin].files  # type: ignore[attr-defined]
            parts = tuple(copy.path.split("/"))
            origin_scan, dest_scan = plan.scans[copy.origin], plan.scans[dest]

            def blocked(path: str) -> bool:  # unknown on either side: never read as "absent on both"
                return (origin_scan.blocked_reason(path) is not None or dest_scan.blocked_reason(path) is not None
                        or any(side.state == "blocked" for side in plan.sides.get(path, {}).values()))
            if syncable(copy.path) in ASSET_KINDS:
                needs = art_needs(copy.path, copy.data, origin_files, origin_scan.blobs,  # type: ignore[attr-defined]
                                  frozenset() if copy.data is None else _excluded(plan, copy.origin))
                why = art_unmet(needs, blocked, final_etag,
                                lambda path, origin=copy.origin: _etag_on(plan, origin, path), here, dest,
                                _excluded(plan, dest) if needs.admitted else frozenset())
                if why:
                    newly[copy.path] = why
                continue
            heads = set(_manifest_heads(copy)) if syncable(copy.path) == "manifest" else set()
            pins = {"/".join(definition_parts(ref)) for ref in pinned_by(parts, copy.data)}  # type: ignore[arg-type]
            missing = sorted(path for path in pins | heads if final(path) != origin_files.get(path) or blocked(path)
                             or (path in heads and origin_files.get(path) is None))
            actors = sorted(path for path in [*_named_paths(parts, copy.data), *_home_scope(copy.path)]
                            if (path in origin_files and final(path) is None) or blocked(path))
            campaign = _knowledge_campaign(copy.path)
            if campaign is not None and campaign not in here and (
                    campaign in origins[copy.origin] or blocked(f"campaigns/{campaign}/scope.json")):
                actors.append(f"campaigns/{campaign} (no scope or instance)")
            if missing or actors:
                what = (["pinned revisions not identical on " + dest + ": " + ", ".join(missing)] if missing else [])
                what += (["named campaign state missing on " + dest + ": " + ", ".join(actors)] if actors else [])
                newly[copy.path] = "; ".join(what)
        if not newly:
            return kept, held
        held.update(newly)
        kept = [copy for copy in kept if copy.path not in newly]


def _named_paths(parts: tuple[str, ...], data: bytes) -> list[str]:
    """The instance and chat files a record names (``replica.instances_named``/``chats_named``)."""
    return sorted({*("/".join(instance_parts(ref)) for ref in instances_named(parts, data)),
                   *("/".join(chat_parts(ref)) for ref in chats_named(parts, data))})


def _home_scope(path: str) -> list[str]:
    """A 107 chat's own play scope: a chat never lands without the scope the origin homes it in."""
    return [f"campaigns/{path.split('/')[1]}/scope.json"] if syncable(path) == "chat" else []


def _campaigns_with_state(paths) -> set[str]:  # type: ignore[no-untyped-def]
    """The campaigns with real state among ``paths``: a scope or an instance (020's existence test)."""
    return {path.split("/")[1] for path in paths if syncable(path) in ("scope", "instance")}


def _excluded(plan: SyncPlan, device: str) -> frozenset[str]:
    """The sources ``device``'s ``load()`` excludes (018's ``excluded_source``), as art admission reads them.

    Taken from the store's shared, cached ``excluded_sources`` rather than rebuilt from the scan:
    it is ``load()``'s own algorithm (a cross-source ``identity_conflict`` included), and on an
    unchanged library it costs one ``stat`` per manifest and revision entry and reads no file. It
    is read lazily: the origin's the first time an image or placement copy is checked (a blob needs
    nothing), the receiver's the first time one needs a source admitted there (a campaign picture or
    a ``hide`` reads no source), so a sync that copies no such art never pays for it. It is then
    kept on the plan: every later fixpoint round, and the dependency checks ``apply_sync`` reruns
    after a denial, judge against the same answer the plan was made with. Every image or placement
    copy is first checked while planning, which holds both libraries' locks, as it does for the
    scan, so the read happens there. An edit another program makes between the scan and this read
    can make the two disagree for one sync; that is the same window every plan has (the commit's
    compare-and-swap still guards the bytes), and the next sync decides again from fresh reads."""
    found = plan.exclusions.get(device)
    if found is None:
        found = plan.exclusions[device] = plan.stores[device].excluded_sources()
    return found


def _etag_on(plan: SyncPlan, device: str, path: str) -> str | None:
    """What ``device`` holds at ``path`` (a blob as verified or trusted by ``_verify_blobs``)."""
    side = plan.sides.get(path, {}).get(device)
    if side is not None:
        return side.etag if side.state == "present" else None
    data = plan.scans[device].files.get(path)  # type: ignore[attr-defined]
    return None if data is None else etag(data)


def _admit(plan: SyncPlan, dest: str) -> None:
    """Overrides for every copy to ``dest``: name collision, head dependency, then preflight units."""
    store = plan.stores[dest]
    copies = []
    for copy in plan.copies[dest]:
        if store.collides(copy.path):
            plan.conflicts[copy.path] = Conflict(copy.path, "name_collision", "a differently spelled entry exists")
        else:
            copies.append(copy)
    # what is held only because an ungranted blob stays behind is denied, as after a commit's denial
    granted = _dependencies(plan, dest, [*copies, *plan.ungranted[dest]])[1] if plan.ungranted[dest] else None
    copies, held = _dependencies(plan, dest, copies)
    for path, why in held.items():
        plan.conflicts[path] = (Conflict(path, "head_dependency", why) if granted is None or path in granted else
                                Conflict(path, "denied", f"depends on a write that was not authorized: {why}",
                                         MappingProxyType({dest: False})))
    everything = store.transaction()
    _stage(everything, copies, [])
    if not copies or _preflight(everything, f"the copies to {dest}") is None:
        plan.copies[dest] = sorted(copies, key=lambda copy: copy.path)  # the common case: one check
        return
    store.transaction().check()  # library-wide problems propagate; they are never blamed on one path
    plan.fallback.add(dest)
    while True:  # admission and dependencies to a joint fixpoint: dropping either can refuse the other
        admitted, rejected = _admit_units(store, copies)
        for copy, error in rejected:
            plan.conflicts[copy.path] = Conflict(copy.path, "unapplicable", str(error))
        copies, held = _dependencies(plan, dest, admitted)  # what depended on a refused unit stays too
        for path, why in held.items():
            plan.conflicts[path] = Conflict(path, "head_dependency", why)
        if not held:
            break
    plan.copies[dest] = copies


def _admit_units(store: LibraryStore, copies: list[Copy]) -> tuple[list[Copy], list[tuple[Copy, BaseException]]]:
    """Admit copies unit by unit; returns the admitted copies and each refused copy's reason.

    A unit is one campaign-local record, one manifest with the head revisions it names among
    ``copies``, the other revisions of one entity, or one 018 art file. Campaign-local records other
    than instances are checked alone; the instances of one campaign are admitted cumulatively in
    sorted path order, so two that would form a cross-kind pair never both pass (A2). Sources are
    checked on their own; when a source fails as a whole, its units are admitted cumulatively
    (manifest units first), so two units that are each acceptable but not together are never both
    admitted. Manifests that link to other sources are then admitted cumulatively in sorted order, so
    a cross-source link cycle is caught. Each art file is checked once, over the accepted copies its
    owner needs. The caller runs one combined check of everything admitted.
    """
    by_path = {copy.path: copy for copy in copies}
    heads = {copy.path: [path for path in _manifest_heads(copy) if path in by_path]
             for copy in copies if syncable(copy.path) == "manifest"}
    admitted: list[Copy] = []
    rejected: list[tuple[Copy, BaseException]] = []

    def admit(copy: Copy, over: list[Copy], what: str) -> bool:
        txn = store.transaction()
        _stage(txn, [*over, copy], [])
        error = _preflight(txn, what)
        if error is not None:
            rejected.append((copy, error))
        return error is None
    for copy in copies:  # independent of each other: alone
        if syncable(copy.path) in CAMPAIGN_LOCAL - {"instance"} and admit(copy, [], copy.path):
            admitted.append(copy)
    instances: dict[str, list[Copy]] = {}
    for copy in copies:
        if syncable(copy.path) == "instance":
            instances.setdefault(copy.path.split("/")[1], []).append(copy)
    for campaign in sorted(instances):  # cumulative, in path order: two that form an id pair never both pass (A2)
        mine: list[Copy] = []
        for copy in sorted(instances[campaign], key=lambda copy: copy.path):
            if admit(copy, mine, copy.path):
                mine.append(copy)
        admitted.extend(mine)
    sources: dict[str, list[list[Copy]]] = {}
    in_manifest_unit = {path for paths in heads.values() for path in paths}
    for copy in copies:
        kind = syncable(copy.path)
        if kind in CAMPAIGN_LOCAL | ASSET_KINDS or copy.path in in_manifest_unit:
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
        mine: list[Copy] = []  # cumulative within the source: its units can refuse each other
        for unit in sources[source]:
            txn = store.transaction()
            _stage(txn, mine + unit, [])
            error = _preflight(txn, f"source {source}")
            if error is None:
                alone.append(unit)
                mine += unit
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
    # 018 art: each file its own unit, over what its owner needs from this commit (a source's accepted
    # copies, a campaign's accepted scope and instances). Art never constrains other art at store level.
    art: list[Copy] = []
    for copy in (copy for copy in copies if syncable(copy.path) in ASSET_KINDS):
        owner = _asset_owner(copy.path)
        over = ([] if owner is None else
                [other for other in accepted if _source_of(other.path) == owner[1]] if owner[0] == "source" else
                [other for other in admitted if syncable(other.path) in ("scope", "instance")
                 and other.path.split("/")[1] == owner[1]])
        if admit(copy, over, copy.path):
            art.append(copy)
    return sorted(admitted + accepted + art, key=lambda copy: copy.path), rejected


def _asset_owner(path: str) -> tuple[str, str] | None:
    """``("source", id)`` or ``("campaign", id)`` for an image object or placement; ``None`` for a blob."""
    found = classify(tuple(path.split("/")))
    if found is None or found[0] not in ("image", "placement"):
        return None
    ref = found[1]
    if isinstance(ref, ImageRef):
        return ("campaign", ref.campaign_id) if ref.campaign_id is not None else ("source", ref.scope)
    subject = ref.subject  # type: ignore[union-attr]
    return ("campaign", subject.campaign_id) if isinstance(subject, InstanceRef) else ("source", subject.source)


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
        if conflict.code in ("held", "denied") or syncable(path) == "blob":
            continue  # held: nothing is kept until 028 (C1); denied: never kept; a blob's name is its content
        side = plan.sides[path][other]
        mine = plan.sides[path][device]
        if side.state != "present" or (mine.state == "blocked" and mine.reason == "unreadable"):
            continue  # nothing to keep, or this device's own file could not even be read
        base = plan.bases[path]
        if base.kind == "agreed" and side.etag == base.etag and mine.state == "present":
            continue  # the other device holds the version this device already moved on from
        candidate = archive_candidate(path, *plan.archives.values())  # a marker that moved it: another conflict
        if base == candidate and plan.kept_records[device].dismissed(path, side.etag, kept_record.base_token(candidate)):
            plan.conflicts[path] = replace(plan.conflicts[path], dismissed=(*plan.conflicts[path].dismissed, device))
            continue
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
    plan.conflicts[path] = replace(conflict, detail=conflict.detail or detail, kept=MappingProxyType(marks))


# -- apply ---------------------------------------------------------------------------

@dataclass
class _Applied:
    observer: Callable[[Step], None] | None = None
    published: dict[str, list[Copy]] = field(default_factory=dict)
    kept: dict[str, list[Keep]] = field(default_factory=dict)
    #: per device, the marker its data commit wrote: its entries and the files written (028: maybe shards)
    pending_written: dict[str, tuple[dict[str, Mapping[str, object]], dict[str, bytes]]] = field(default_factory=dict)
    wrote: bool = False
    consumed: list[str] = field(default_factory=list)

    def observe(self, step: Step) -> None:
        if self.observer is not None:
            self.observer(step)


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
        elif path not in entries and plan.bases[path].kind in ("first", "none", "deleted") and (
                path in plan.agreed or _held_agreed(plan, path)):
            # agreed now on a base that does not say so (a first sync, a conflict ended by hand, a
            # tombstone): mark it, so a crash before the archives are written never leaves that base
            # behind (a later one-sided deletion would be undone)
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
    stuck = ({_knowledge_campaign(path) for path in plan.conflicts} - {None}) | _unknown_logs(plan.scans.values())
    if not stuck:
        return
    for device in plan.devices:
        kept, held = [], []
        for copy in plan.copies[device]:
            campaign = _knowledge_campaign(copy.path)
            (held if campaign is not None and (campaign in stuck or "*" in stuck) else kept).append(copy)
        plan.copies[device] = kept
        for copy in held:
            plan.skipped[copy.origin] = tuple(sorted({*plan.skipped.get(copy.origin, ()), copy.path}))


def _unknown_logs(scans) -> set[str]:  # type: ignore[no-untyped-def]
    """Campaigns whose knowledge log cannot be seen whole on some device, so it moves nowhere: a blocked
    ``events``/``evidence`` folder (or below), one in another spelling, or a blocked campaign folder,
    ``campaigns/`` or root (``"*"``: every campaign). Fails closed, like ``_held_campaigns``."""
    unknown = set()
    for scan in scans:
        for path in scan.blocked:
            parts = path.split("/")
            if path in ("", "campaigns"):
                unknown.add("*")
            elif parts[0] == "campaigns" and len(parts) == 2:
                unknown.add(parts[1])
            elif parts[0] == "campaigns" and len(parts) >= 3 and parts[2].casefold() in ("events", "evidence"):
                unknown.add(parts[1])
        for path in scan.skipped:
            parts = path.split("/")
            if (parts[0] == "campaigns" and len(parts) >= 3 and parts[2].casefold() in ("events", "evidence")
                    and parts[2] not in ("events", "evidence")):
                unknown.add(parts[1])  # a look-alike spelling of a knowledge folder
    return unknown


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


@dataclass(frozen=True)
class Step:
    """One committed ``apply_sync`` step on one device, exactly as it was written.

    ``kind`` is ``data``, ``archive`` or ``pending``. A data step carries the copies and kept
    versions its commit published (after any denial) and, in ``states``, the pending marker it
    wrote; an archive or pending step carries only its ``.sync`` file. Each state is ``(name,
    bytes, expected etag)``. 028's peer transfer replays a step on the device a local stand-in
    represents, in the same order (docs/DEVICE_SYNC.md).
    """

    kind: str
    device: str
    copies: tuple[Copy, ...] = ()
    keeps: tuple[Keep, ...] = ()
    states: tuple[tuple[str, bytes, str | None], ...] = ()


def apply_sync(plan: SyncPlan, handles: Mapping[str, object],
               approvals: Mapping[str, Mapping[str, str]] | None = None, *,
               observer: Callable[[Step], None] | None = None) -> SyncReport:
    """Write the plan: data(a), data(b), verify, archive(b), archive(a), pending(b), pending(a).

    ``observer`` is called with each step that wrote something, right after its commit and
    before the next step starts; an exception it raises stops the sync there, unchanged, so
    nothing after that step is written (028 replays a stand-in's steps on the real device)."""
    approvals = approvals or {}
    applied = _Applied(observer)
    for device in plan.devices:
        _commit_data(plan, device, handles[device], approvals.get(device, {}), applied)
    _verify(plan, applied)
    resolved = _resolved(plan, applied)
    archived = {device: _new_archive(plan, device, resolved) for device in plan.devices}
    for device in reversed(plan.devices):
        _commit_archive(plan, device, archived[device], applied)
    for device in reversed(plan.devices):
        _commit_pending(plan, device, resolved, archived, applied)
    return _report(plan, applied)


def _commit_data(plan: SyncPlan, device: str, handle: object, approvals: Mapping[str, str],
                 applied: _Applied) -> None:
    store = plan.stores[device]
    copies, keeps = list(plan.copies[device]), list(plan.keeps[device])
    old = plan.pendings[device].files
    # The marker is written with the data, before any archive is rewritten: a fresh archive no
    # longer says why a path was uncertain, so the marker must already say it (R: crash between
    # archive and pending steps).
    entries = _marker(plan, device, copies)
    files, width = bookkeeping.render_files("pending", plan.other(device), plan.devices, entries, ARCHIVE_LIMIT)
    marker = [(entries, files)]  # what the marker will hold (on_denied may change it)
    if not copies and not keeps and not _marker_needed(plan, device, entries, files):
        applied.published[device], applied.kept[device] = [], []
        return
    txn = store.transaction()
    try:
        _stage(txn, copies, keeps)
    except StaleWrite as error:  # a planned kept-version slot was taken meanwhile
        raise SyncInterrupted(f"the data commit on {device} stopped: {error}", _report(plan, applied)) from error
    marker_keys: dict[str, str] = {}  # every file of the marker, so a denial can still rewrite any of them
    if _marker_needed(plan, device, entries, files):
        marker_keys = bookkeeping.stage(txn, bookkeeping.changes(store, files, old, every=True))
    delivery = kept_record.Delivery(plan, device, keeps)
    delivery.stage(txn)  # 028: a keep's delivery is recorded in the commit that writes it
    dropped: set[str] = set()

    def on_denied(keys: frozenset[str]) -> dict[str, bytes | None]:
        denied = {key for key in keys if not key.startswith("kept/")}
        own_units = CAMPAIGN_LOCAL | ASSET_KINDS  # never source-coarse: art is denied only for itself
        sources = {_source_of(path) for path in denied if syncable(path) not in own_units}
        logs = {_knowledge_campaign(path) for path in denied} - {None}  # a log is one unit, too
        changes: dict[str, bytes | None] = {}
        for copy in copies:
            if copy.path in denied or (syncable(copy.path) not in own_units
                                       and _source_of(copy.path) in sources) or _knowledge_campaign(copy.path) in logs:
                dropped.add(copy.path)
                if copy.path not in denied:
                    changes[copy.path] = None
                why = "not authorized" if copy.path in denied else "depends on a write that was not authorized"
                plan.conflicts[copy.path] = Conflict(copy.path, "denied", why,
                                                     MappingProxyType({device: False}))
        def drop(path: str, why: str) -> None:
            dropped.add(path)
            changes[path] = None
            plan.conflicts[path] = Conflict(path, "denied", f"depends on a write that was not authorized: {why}",
                                            MappingProxyType({device: False}))
        while True:  # to a joint fixpoint: a dropped dependency can make the rest unacceptable, and back
            before = len(dropped)
            remaining = [copy for copy in copies if copy.path not in dropped]
            check = store.transaction()
            _stage(check, remaining, [])
            if remaining and _preflight(check, f"the authorized copies to {device}") is not None:
                for copy, error in _admit_units(store, remaining)[1]:  # what is left must stand alone (R1)
                    drop(copy.path, str(error))
            remaining = [copy for copy in copies if copy.path not in dropped]
            _, held = _dependencies(plan, device, remaining)
            broken = {_knowledge_campaign(path) for path in dropped} - {None}  # a log is one unit
            held.update({copy.path: "its knowledge log's other records were not authorized" for copy in remaining
                         if copy.path not in held and _knowledge_campaign(copy.path) in broken})
            for path, why in held.items():
                drop(path, why)
            if len(dropped) == before:
                break
        refused = [keep for keep in keeps if str(_kept_key(keep)) in keys]
        for keep in refused:
            _kept(plan, keep.path, device, False, "keeping it was not authorized")
        changes.update(delivery.deny(refused))
        if marker_keys:  # fewer copies only shrink the marker: the same width holds it
            remaining = [copy for copy in copies if copy.path not in dropped]
            shrunk = _marker(plan, device, remaining)
            written, _ = bookkeeping.render_files("pending", plan.other(device), plan.devices, shrunk, None, width)
            marker[0] = (shrunk, written)
            needed = _marker_needed(plan, device, shrunk, written)
            for key, name in marker_keys.items():
                changes[key] = written[name] if needed else None
        return changes

    try:
        with _locked(*plan.stores.values()):  # a blob copy loads from the origin inside this commit
            result = txn.commit(handle, dict(approvals), on_denied=on_denied)
    except (StaleWrite, WriteConflict, SyncInterrupted, *PER_PATH_ERRORS) as error:
        applied.consumed.extend(getattr(error, "consumed_approvals", ()))  # spent without a write (R16)
        raise SyncInterrupted(f"the data commit on {device} stopped: {error}", _report(plan, applied)) from error
    applied.consumed.extend(result.consumed_approvals)
    published = [copy for copy in copies if copy.path in result.etags]
    applied.published[device] = published
    applied.kept[device] = [keep for keep in keeps if str(_kept_key(keep)) in result.etags]
    states = tuple((name, marker[0][1][name], None if old.get(name) is None else etag(old[name]))
                   for key, name in marker_keys.items() if key in result.etags)
    if states:
        applied.pending_written[device] = (marker[0][0], {name: data for name, data, _ in states})
    applied.wrote = applied.wrote or bool(result.etags)
    plan.copies[device] = published
    plan.kept_records[device], recorded = delivery.committed(result.etags)
    if result.etags:
        applied.observe(Step("data", device, tuple(published), tuple(applied.kept[device]), (*states, *recorded)))


def _marker_needed(plan: SyncPlan, device: str, entries: Mapping[str, object], files: Mapping[str, bytes]) -> bool:
    """Whether the marker must be written: it changes, and is not merely an empty marker where none was."""
    old = plan.pendings[device].files
    changed = any(old.get(name) != data for name, data in files.items())
    return changed and (bookkeeping.root_name("pending", plan.other(device)) in old or bool(entries))


def _marker(plan: SyncPlan, device: str, copies: list[Copy]) -> dict[str, Mapping[str, object]]:
    entries = _pending_entries(plan, device)
    for copy in copies:
        entries[copy.path] = {"from": copy.expected, "to": copy.etag,
                              "base": _archived(archive_candidate(copy.path, *plan.archives.values()))}
    return entries


def _archived(candidate: Base) -> str | None:
    """The agreed etag both archives held when a marker was written (``None`` if none): with it, a
    marker is stale only once the archives have moved on to its ``to`` (``decide.derive_base``)."""
    return candidate.etag if candidate.kind == "agreed" else None


def _kept_key(keep: Keep) -> object:
    return kept_address(tuple(keep.path.split("/")), etag(keep.data), keep.slot)


def _source_of(path: str) -> str | None:
    """The source a definition or manifest belongs to; ``None`` for 018 art (own units, see ``_asset_owner``)."""
    found = classify(tuple(path.split("/")))
    assert found is not None
    if found[0] in ASSET_KINDS:
        return None
    return found[1] if found[0] == "manifest" else found[1].source  # type: ignore[return-value, union-attr]


def _verify(plan: SyncPlan, applied: _Applied) -> None:
    """Re-read every file this sync published or kept; its bytes must be the planned ones."""
    for device in plan.devices:
        store = plan.stores[device]
        checks = [(copy.path, copy.etag) for copy in applied.published.get(device, [])]
        checks += [("/".join(conflict_parts(tuple(keep.path.split("/")), etag(keep.data), keep.slot)), etag(keep.data))
                   for keep in applied.kept.get(device, [])]
        for path, tag in checks:
            try:
                found = store.read_exact(path)
            except NameCollision:  # renamed to a look-alike since it was published
                found = None
            if found is None or etag(found) != tag:
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


def _new_archive(plan: SyncPlan, device: str,
                 resolved: Mapping[str, tuple[str, str] | None]) -> dict[str, tuple[str, str]]:
    view = plan.archives[device]
    entries: dict[str, tuple[str, str]] = dict(view.entries) if view.state == "valid" else {}
    for path, entry in resolved.items():
        if entry is None:
            entries.pop(path, None)
        else:
            entries[path] = entry
    # 028: a blob's tombstone is never kept. It decides nothing a missing entry does not: a blob's
    # etag is its name, so two present sides always agree, and its tombstone never blocks a copy
    # (row 12's exception), which is exactly row 6 for a ``none`` base. Every other tombstone stays:
    # dropping one would turn a resurrection of its exact bytes (row 12) into a silent copy.
    return {path: entry for path, entry in entries.items() if entry[0] != "deleted" or syncable(path) != "blob"}


def _commit_archive(plan: SyncPlan, device: str, entries: Mapping[str, tuple[str, str]], applied: _Applied) -> None:
    files, _ = bookkeeping.render_files("archive", plan.other(device), plan.devices, entries, ARCHIVE_LIMIT)
    states = bookkeeping.changes(plan.stores[device], files, plan.archives[device].files)
    if not states:
        return
    txn = plan.stores[device].transaction()
    bookkeeping.stage(txn, states)
    try:
        txn.commit(None)
    except (StaleWrite, WriteConflict) as error:
        raise SyncInterrupted(f"the archive on {device} changed during the sync: {error}",
                              _report(plan, applied)) from error
    applied.wrote = True
    applied.observe(Step("archive", device, states=tuple(states)))


def _commit_pending(plan: SyncPlan, device: str, resolved: Mapping[str, object],
                    archived: Mapping[str, Mapping[str, tuple[str, str]]], applied: _Applied) -> None:
    """The marker without what this sync resolved, and (028) the kept-version record: a completed sync's last step."""
    current = dict(plan.pendings[device].files)
    if device in applied.pending_written:
        entries, written = applied.pending_written[device]
        current.update(written)
    else:
        entries = _pending_entries(plan, device)
    desired = {path: entry for path, entry in entries.items() if path not in resolved}
    peer = plan.other(device)
    states = []
    if bookkeeping.root_name("pending", peer) in current or desired:
        files, _ = bookkeeping.render_files("pending", peer, plan.devices, desired, ARCHIVE_LIMIT)
        states = bookkeeping.changes(plan.stores[device], files, current)
    bases = {path: kept_record.archived_token(*(archived[d].get(path) for d in plan.devices))  # what the next sync reads
             for path in plan.conflicts}
    record = kept_record.next_keeps(plan.kept_records[device], peer, plan.devices, bases,
                                    functools.partial(kept_record.slot_stands, plan.stores[device]))
    if record is not None:
        states.append(record)
    if not states:
        return
    txn = plan.stores[device].transaction()
    bookkeeping.stage(txn, states)
    try:
        txn.commit(None)
    except (StaleWrite, WriteConflict) as error:
        raise SyncInterrupted(f"the pending marker on {device} changed during the sync: {error}",
                              _report(plan, applied)) from error
    applied.wrote = True
    applied.observe(Step("pending", device, states=tuple(states)))


# -- entry points ----------------------------------------------------------------------

def sync(left: LibraryStore, right: LibraryStore, handles: Mapping[str, object],
         approvals: Mapping[str, Mapping[str, str]] | None = None) -> SyncReport:
    """Plan and apply one sync. ``handles`` (and ``approvals``) are keyed by device ID."""
    return apply_sync(plan_sync(left, right, handles), handles, approvals)


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


__all__ = [
    "ARCHIVE_LIMIT", "Conflict", "Copy", "Keep", "SyncPlan", "SyncReport", "apply_sync", "device_of",
    "identify", "plan_sync", "sync", "sync_until_quiet", "syncable",
]
