"""Shared process locks, registries, ordered holds and native identity barriers."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Iterable
from contextlib import ExitStack, contextmanager, suppress
from pathlib import Path
from typing import Literal

from .. import paths, proclock

_registry_guard = threading.Lock()

# Longer than any legitimate hold, but bounded so a cross-process lock-order
# inversion surfaces as a 409 naming the campaign rather than a wedged server.
# Not a proof: a module migration over a large library on a synced or removable
# filesystem can still exceed it, and its waiter then gets a retryable 409.
LOCK_TIMEOUT = 30.0


class StoreBusy(Exception):
    """A store lock stayed held past ``LOCK_TIMEOUT``. One handler maps this to
    HTTP 409.

    The message does not say who holds it, because nothing here knows: the
    holder may be another process, a long write in this one, or -- for the
    striped image locks -- a write to an unrelated name that shares a stripe.
    """

    def __init__(self, name: str, what: str = "resource"):
        super().__init__(f"this {what} is busy; try again in a moment")
        self.name = name


class CampaignBusy(StoreBusy):
    def __init__(self, cid: str):
        super().__init__(cid, "campaign")


class WorldActorBusy(StoreBusy):
    def __init__(self, wid: str):
        super().__init__(wid, "world actor roster")


class BackupBusy(StoreBusy):
    def __init__(self, name: str = "backups"):
        super().__init__(name, "store backup")


class ModuleEditBusy(StoreBusy):
    def __init__(self, name: str = "module-edit"):
        super().__init__(name, "module library")


class MigrationBusy(StoreBusy):
    """The inference-settings migration lock is held (`inference_migration_lock`)."""


class ConfigBusy(StoreBusy):
    def __init__(self, name: str = "config"):
        super().__init__(name, "configuration")


def _remaining(deadline) -> float:
    """RLock treats -1 as "no timeout" and rejects every other negative, so a
    computed remainder must be clamped rather than passed through."""
    if deadline is None:
        return -1
    return max(0.0, deadline - time.monotonic())


_campaign_holds_local = threading.local()


def _domain_holds(domain: str) -> set[int]:
    if getattr(_campaign_holds_local, "pid", None) != os.getpid():
        _campaign_holds_local.pid = os.getpid()
        _campaign_holds_local.held = {}
    return _campaign_holds_local.held.setdefault(domain, set())


def campaign_locks_held() -> bool:
    """Physical campaign holds on this thread, including reentrant callers."""
    return bool(_domain_holds("campaign"))


class _ProcessScopedLock:
    """A ``threading.RLock`` plus an OS advisory file lock (#234).

    The file lock is taken on the OUTERMOST acquisition only and released on
    the outermost release, so reentrancy touches the filesystem once -- which
    ``audit.apply_delta`` -> ``sheets.set_field``, ``scenes._serialized``
    nesting under a holder, and ``module_edit._apply`` -> ``recover()`` all
    depend on.

    ``acquire`` keeps ``RLock``'s contract exactly (returns a bool, never
    raises ``StoreBusy``); ``__enter__`` is the layer that raises. The timeout
    is a deadline for the WHOLE acquisition -- time spent on the thread lock is
    subtracted from the file lock's budget -- and it bounds lock *contention*
    only: ``realpath``/``mkdir``/``open`` on an unavailable path are blocking
    syscalls no userspace deadline can interrupt.
    """

    def __init__(self, domain: str, name: str, busy: type[StoreBusy]):
        self._domain = domain
        self._name = name
        self._busy = busy
        self._rlock = threading.RLock()
        self._depth = 0
        self._fd: int | None = None

    def _path(self):
        # Resolved per outermost acquisition, never cached: home() resolves
        # live on every call and the Storage location can change at runtime.
        # The domain keeps a campaign id from ever colliding with the
        # module-edit lock -- see proclock.lock_path.
        return proclock.lock_path(paths.home(), self._domain, self._name)

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if timeout != -1 and timeout < 0:   # RLock parity: -1 is the only
            raise ValueError(               # legal negative, not "any negative"
                "timeout value must be a positive number")
        if not blocking:
            if timeout != -1:
                raise ValueError("can't specify a timeout for a non-blocking call")
            ok = self._rlock.acquire(False)
            deadline = proclock.NO_WAIT     # ONE attempt; None would retry forever
        else:
            deadline = time.monotonic() + timeout if timeout >= 0 else None
            ok = self._rlock.acquire(True, _remaining(deadline))
        if not ok:
            return False
        fd = None
        try:
            if self._depth > 0:             # we already hold the file lock
                self._depth += 1
                return True
            fd = proclock.acquire(self._path(), deadline)
            if fd is None:
                self._rlock.release()
                return False
            self._fd = fd                   # installed BEFORE depth becomes 1
            fd = None                       # ownership transferred to self
            self._depth = 1
            if self._domain in {"campaign", "world-actors"}:
                _domain_holds(self._domain).add(id(self))
            return True
        except BaseException:
            # Three windows, and the handler has to tell them apart. `depth`
            # is the discriminator because it is the thing `release()` keys on.
            if self._depth > 0:
                # We had already established (or incremented) the hold when the
                # exception landed -- an asynchronous one, after `depth = 1` or
                # after the reentrant `depth += 1`. The object is *consistent*
                # here, so unwind it the normal way. Doing what the two
                # branches below do instead would release the thread lock while
                # leaving depth > 0 and the fd installed: the lock would then
                # claim to be held with no thread owning it, and the next
                # acquire would see depth > 0 and skip the file lock entirely,
                # silently dropping cross-process exclusion. Worse than a leak.
                self.release()
            else:
                # Either the file lock was taken but never installed (local
                # `fd`), or it was installed and the exception landed before
                # depth reached 1 (`self._fd` set with depth 0, which
                # `release()` would never clean up). Both leak for the life of
                # the process if not reclaimed here.
                if fd is None and self._fd is not None:
                    fd, self._fd = self._fd, None
                if fd is not None:
                    proclock.release(fd)
                self._rlock.release()       # never strand the thread lock
            raise

    def release(self) -> None:
        if not self._rlock._is_owned():     # ownership BEFORE state, so a
            raise RuntimeError(             # non-owner corrupts nothing
                "cannot release un-acquired lock")
        if self._depth > 1:
            self._depth -= 1
            self._rlock.release()
            return
        fd, self._fd = self._fd, None
        try:
            if fd is not None:
                proclock.release(fd)
        finally:                            # even if the unlock raises
            self._depth = 0
            if self._domain in {"campaign", "world-actors"}:
                _domain_holds(self._domain).discard(id(self))
            self._rlock.release()

    def __enter__(self):
        if not self.acquire(timeout=LOCK_TIMEOUT):
            raise self._busy(self._name)
        return self

    def __exit__(self, *exc) -> Literal[False]:
        # `Literal[False]`, not `bool`: a context manager whose `__exit__` may
        # return True can swallow an exception, so under `-> bool` mypy treats
        # every `with lock:` block as one control may fall out of -- and reports
        # a function that returns only inside the block as missing a return.
        # This lock never swallows, and saying so is what lets those functions
        # type-check as written.
        self.release()
        return False

    def _is_owned(self) -> bool:
        """RLock parity: two sheets tests assert `modules.resolve` runs inside
        the lock by calling this."""
        return self._rlock._is_owned()


_campaign_locks: dict[str, _ProcessScopedLock] = {}
_world_actor_locks: dict[str, _ProcessScopedLock] = {}
_image_collection_locks: dict[str, _ProcessScopedLock] = {}
_image_collection_job_locks: dict[tuple[str, str], _ProcessScopedLock] = {}


def image_collection_lock(wid: str) -> _ProcessScopedLock:
    """World-image membership checks and publication share one process lock."""
    wid = os.path.normcase(str((paths.home() / "worlds" / wid).resolve()))
    with _registry_guard:
        lock = _image_collection_locks.get(wid)
        if lock is None:
            lock = _image_collection_locks[wid] = _ProcessScopedLock(
                "image-collections", wid, StoreBusy)
        return lock


def image_collection_job_lock(wid: str, job: str) -> _ProcessScopedLock:
    """Serialize one transient harvest journal; never acquired from an image lock."""
    wid = os.path.normcase(str((paths.home() / "worlds" / wid).resolve()))
    key = (wid, job)
    with _registry_guard:
        lock = _image_collection_job_locks.get(key)
        if lock is None:
            lock = _image_collection_job_locks[key] = _ProcessScopedLock(
                "image-collection-jobs", f"{wid}:{job}", StoreBusy)
        return lock


#: Sidecar locks are striped by the id's first hex byte so the set of lock
#: files is bounded: one per object would accumulate forever.
IMAGE_OBJECT_STRIPES = 64
_image_object_locks: dict[tuple[str, int], _ProcessScopedLock] = {}
_image_ingest_gc_locks: dict[str, _ProcessScopedLock] = {}


def _store_key() -> str:
    """The live store root, normalised, so two roots never share a lock object."""
    return os.path.normcase(str(paths.home().resolve()))


def image_object_lock(image_id: str) -> _ProcessScopedLock:
    """Serialize read-modify-writes of one image-object sidecar (`store.image_store`).

    64 stripes keyed by the id's first hex byte (`px1-<xx>…`), so two objects
    may share a stripe and wait on each other -- a spurious wait, never a lost
    update. Raises ValueError on an id whose first byte is not hex.

    A **leaf**: it may be taken while a campaign lock (or an image-collection
    lock) is held, but nothing acquires a campaign lock while holding it. The
    only lock taken around it is `image_ingest_gc_lock`, always first.
    """
    stripe = int(image_id[4:6], 16) % IMAGE_OBJECT_STRIPES
    key = (_store_key(), stripe)
    with _registry_guard:
        lock = _image_object_locks.get(key)
        if lock is None:
            lock = _image_object_locks[key] = _ProcessScopedLock(
                "image-objects", f"stripe-{stripe:02d}", StoreBusy)
        return lock


def image_ingest_gc_lock() -> _ProcessScopedLock:
    """Store-wide exclusion between image ingest's find-or-create and image GC.

    Ingest holds it across publish-blob, write-sidecar and the mtime touch; GC
    deletes under it after re-checking that touch, so the two cannot
    interleave (spec section 12). Order: this lock, then `image_object_lock`,
    never the reverse.

    A **leaf** like `image_object_lock`: never acquire a campaign lock while
    holding it.
    """
    key = _store_key()
    with _registry_guard:
        lock = _image_ingest_gc_locks.get(key)
        if lock is None:
            lock = _image_ingest_gc_locks[key] = _ProcessScopedLock(
                "image-store", "ingest-gc", StoreBusy)
        return lock


#: Stripes per family of the per-name image locks (`image_name_lock`,
#: `image_sidecar_lock`). Bounded for the reason `IMAGE_OBJECT_STRIPES` is: a
#: lock per name would leave one lock file per image name ever written.
IMAGE_NAME_STRIPES = 256
#: The stripe scheme is IN the domain names, so a server running a different
#: scheme (another stripe count or key) shares no lock file with this one and
#: cannot hold a lock this one reads as some other name's stripe.
_NAME_DOMAIN = f"image-names-v1-{IMAGE_NAME_STRIPES}"
_SIDECAR_DOMAIN = f"image-sidecars-v1-{IMAGE_NAME_STRIPES}"
_image_stripe_locks: dict[tuple[str, str, int], StripeLock] = {}
#: How many stripe acquisitions (either family, reentrant ones included) the
#: current thread holds -- what `image_stripes_held` answers from.
_stripe_holds = threading.local()


class StripeLock(_ProcessScopedLock):
    """A `_ProcessScopedLock` that keeps a per-thread count of its holds, so a
    caller can ask whether this thread already holds ANY image stripe before it
    decides to wait on one (`image_stripes_held`)."""

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        ok = super().acquire(blocking, timeout)
        if ok:
            _stripe_holds.n = getattr(_stripe_holds, "n", 0) + 1
        return ok

    def release(self) -> None:
        owned = self._is_owned()
        try:
            super().release()
        finally:
            if owned:
                _stripe_holds.n -= 1

    def __enter__(self):
        # "this image is busy": what a waiter on any stripe was writing. The
        # stripe's own name would mean nothing to whoever reads the 409.
        if not self.acquire(timeout=LOCK_TIMEOUT):
            raise StoreBusy(self._name, "image")
        return self


def image_stripes_held() -> bool:
    """Whether the current thread holds any image name or sidecar stripe.

    A stripe this thread takes while already holding one, outside an up-front
    sorted set, could be BELOW the one it holds -- the order that wedges against
    a holder going the other way. Callers that can be reached that way (the
    stranded-promotion heal) ask this and then never wait."""
    return getattr(_stripe_holds, "n", 0) > 0


def _fold(key: str) -> str:
    """Upper-case, then casefold: the rule `image_refs.fold` and the vacancy
    scan compare names by, so names that are one file on NTFS (a dotless i and
    an i, which ``casefold`` alone keeps apart) are one lock too."""
    return key.upper().casefold()


def _image_stripe(d: Path, key: str) -> int:
    """The stripe of `key` (an image name or a sidecar filename) in directory
    `d`: the resolved, case-normalised directory and the case-FOLDED key, so a
    name and its case variant -- one file on a case-insensitive filesystem --
    are always one lock, and the same directory spelled two ways is one too.

    The directory is taken RELATIVE to the resolved store root when it is under
    it (the absolute path only when it is not). The lock files already live in
    a per-store directory keyed on the root's identity, so two processes that
    spell one store's root differently -- `/Users/A/Store` and
    `/users/a/store` on a case-insensitive POSIX volume, where `normcase` folds
    nothing -- must still land on the same stripe for the same directory."""
    resolved = Path(d).resolve()
    try:
        where = "store:" + os.path.normcase(resolved.relative_to(paths.home().resolve()).as_posix())
    except ValueError:
        where = "abs:" + os.path.normcase(str(resolved))
    digest = hashlib.sha256(
        f"{where}\0{_fold(key)}".encode("utf-8", "surrogateescape")).digest()
    return int.from_bytes(digest[:8], "big") % IMAGE_NAME_STRIPES


def _stripe_lock(domain: str, stripe: int) -> StripeLock:
    key = (_store_key(), domain, stripe)
    with _registry_guard:
        lock = _image_stripe_locks.get(key)
        if lock is None:
            lock = _image_stripe_locks[key] = StripeLock(
                domain, f"stripe-{stripe:03d}", StoreBusy)
        return lock


def _stripe_set(domain: str, keyed: Iterable[tuple[Path, str]]) -> list[StripeLock]:
    return [_stripe_lock(domain, s) for s in sorted({_image_stripe(d, k) for d, k in keyed})]


def image_name_lock(d: Path, name: str) -> StripeLock:
    """Serialize writes to logical image `name` in directory `d`
    (`store.assets._image_lock`): every extension, its placement and its
    legacy siblings.

    Process-scoped, so a second server on the same store serializes too, and
    striped (`IMAGE_NAME_STRIPES`, keyed by `_image_stripe`), so unrelated
    names may share a stripe and wait on each other -- a spurious wait, never a
    lost update. Reentrant. A thread holding one name stripe must not BLOCK on
    another outside one up-front sorted set: take several through
    `image_name_locks`."""
    return _stripe_lock(_NAME_DOMAIN, _image_stripe(d, name))


def image_name_locks(d: Path, names: Iterable[str]) -> list[StripeLock]:
    """The name stripes of `names` in `d`, deduplicated and in stripe order --
    the one order every holder of several takes them in, whatever the names."""
    return _stripe_set(_NAME_DOMAIN, [(d, n) for n in names])


def image_name_locks_for(keyed: Iterable[tuple[Path, str]]) -> list[StripeLock]:
    """One sorted, deduplicated name-stripe set across all source/destination
    directories. Sorting each directory separately does not establish one
    order when their names hash onto interleaved stripes."""
    return _stripe_set(_NAME_DOMAIN, keyed)


def image_sidecar_lock(d: Path, filename: str) -> StripeLock:
    """Serialize read-modify-writes of sidecar `filename` in `d`
    (`store.assets.sidecar_lock`). A family of its own, taken AFTER any name
    stripe and never before one; striped and process-scoped like
    `image_name_lock`."""
    return _stripe_lock(_SIDECAR_DOMAIN, _image_stripe(d, filename))


def image_sidecar_locks(keyed: Iterable[tuple[Path, str]]) -> list[StripeLock]:
    """The sidecar stripes of `(d, filename)` pairs, deduplicated and in stripe
    order, for a caller that rewrites sidecars in more than one directory."""
    return _stripe_set(_SIDECAR_DOMAIN, list(keyed))


class WorldLifecycleBusy(StoreBusy):
    def __init__(self, name: str):
        super().__init__(name, "World lifecycle")


class _WorldLifecycleLock(_ProcessScopedLock):
    """A source identity's pinned store, never a later global home pointer."""
    def __init__(self, store: Path, world: str):
        self.store = store
        self._pid = os.getpid()
        name = hashlib.sha256(world.encode("utf-8")).hexdigest()
        super().__init__("world-lifecycle", name, WorldLifecycleBusy)

    def _process(self) -> None:
        if self._pid == os.getpid():
            return
        # Old references can survive fork as well as the registry. Closing a
        # duplicated fd is safe; issuing an unlock would release the parent.
        if self._fd is not None:
            with suppress(OSError):
                os.close(self._fd)
        self._fd, self._depth = None, 0
        self._rlock = threading.RLock()
        self._pid = os.getpid()

    def _path(self):
        return proclock.lock_path(self.store, self._domain, self._name)

    def _is_owned(self) -> bool:
        self._process()
        return super()._is_owned()

    def release(self) -> None:
        self._process()
        super().release()

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        if not self._is_owned() and (campaign_locks_held() or _domain_holds("world-actors")):
            raise RuntimeError("World lifecycle barrier must be held before campaign or world-actor locks")
        return super().acquire(blocking=blocking, timeout=timeout)


_world_lifecycle_locks: dict[tuple[str, str], _WorldLifecycleLock] = {}
_world_lifecycle_pid = os.getpid()


def _world_lifecycle_process() -> None:
    global _world_lifecycle_pid, _registry_guard  # noqa: PLW0603 -- child registries cannot retain parent-owned guards
    if _world_lifecycle_pid == os.getpid():
        return
    # Close child descriptor copies without unlocking the parent's advisory
    # lock. Do not retain parent RLocks or a guard owned by a vanished thread.
    for lock in _world_lifecycle_locks.values():
        lock._process()
    _world_lifecycle_locks.clear()
    _registry_guard = threading.Lock()
    _world_lifecycle_pid = os.getpid()


def world_lifecycle_lock(store: Path, world: Path) -> _WorldLifecycleLock:
    _world_lifecycle_process()
    pinned, source = store.resolve(), str(world.resolve())
    key = (str(pinned), source)
    with _registry_guard:
        lock = _world_lifecycle_locks.get(key)
        if lock is None:
            lock = _world_lifecycle_locks[key] = _WorldLifecycleLock(pinned, source)
        return lock


def world_lifecycle_held(store: str, world: str | None) -> bool:
    # Stored provenance is an equality key, never a filesystem path to open.
    _world_lifecycle_process()
    if world is None:
        return False
    lock = _world_lifecycle_locks.get((store, world))
    return lock is not None and lock._is_owned()


def world_actor_lock(wid: str) -> _ProcessScopedLock:
    """Serialize world actor name claims across characters and PCs.

    Both kinds have separate directories, so their own file writes cannot
    protect the shared name check. Use the same registry guard and process
    lock as campaign writes to cover threads and a second server process.
    """
    with _registry_guard:
        lock = _world_actor_locks.get(wid)
        if lock is None:
            lock = _world_actor_locks[wid] = _ProcessScopedLock(
                "world-actors", wid, WorldActorBusy)
        return lock


def campaign_lock(cid: str) -> _ProcessScopedLock:
    """Get-or-create the per-campaign lock atomically -- a plain
    ``if cid not in _campaign_locks: ...`` is a check-then-act race that can
    hand two concurrent first-ever callers different lock objects.

    Keyed by cid alone: two *different* stores' campaigns sharing a cid share
    one lock object, which is over-serialization inside one process and never a
    correctness hole, while the lock *file* is per store.
    """
    with _registry_guard:
        lock = _campaign_locks.get(cid)
        if lock is None:
            lock = _campaign_locks[cid] = _ProcessScopedLock("campaign", cid, CampaignBusy)
        return lock


@contextmanager
def campaign_lock_nowait(cid: str):
    """``campaign_lock`` that never waits: yields whether it got the lock.

    For work that belongs in the domain but must not delay the caller when the
    campaign is busy. ``campaign_lock`` waits up to ``LOCK_TIMEOUT`` (30s), which
    is right for a mutation that has to happen and wrong for one that can simply
    be skipped -- ``store.prompt_log.record`` runs on the generating path, before
    the route returns its streaming response, so waiting there would stall a turn
    for half a minute and then discard the debug snapshot anyway.

    **The caller must honour the boolean.** A body that writes regardless has
    taken no lock at all; this yields False rather than raising precisely so the
    decision is the caller's, and `test_lock_domain_guard.py` counts a `with`
    over this as serialization without being able to check that.

    Reentrant like the underlying RLock: a thread that already holds this
    campaign's lock always gets True.
    """
    lock = campaign_lock(cid)
    got = lock.acquire(blocking=False)
    try:
        yield got
    finally:
        if got:
            lock.release()


@contextmanager
def best_effort_campaign_lock(cid: str, timeout: float = 2.0):
    """Hold the campaign lock if it can be had quickly; otherwise proceed
    without it. Yields whether it was actually held.

    For READ paths whose only stake in the lock is seeing two files in a
    consistent state. `campaign_lock` is the wrong tool there: it raises
    ``StoreBusy`` after ``LOCK_TIMEOUT``, and a reader that can 409 turns a
    nicety into a new way for a turn to fail. `context._assemble` is the case —
    it reads the scene's transcript under it, and `post_chat`
    has already appended the player's post by the time it runs, with the undo
    that would take it back off not yet wired. A timeout there would strand
    that post with no reply and nothing able to remove it.

    So the trade is stated rather than inherited: under contention this returns
    an unlocked read, which can observe one writer's two files a moment apart.
    That costs one prompt a stale field. Refusing would cost the turn.

    `acquire` returns a bool and never raises (it keeps ``RLock``'s contract);
    only ``__enter__`` raises, which is exactly the layer being avoided.
    """
    lock = campaign_lock(cid)
    held = lock.acquire(timeout=timeout)
    try:
        yield held
    finally:
        if held:
            lock.release()


_config = _ProcessScopedLock("domain", "config", ConfigBusy)


def config_lock() -> _ProcessScopedLock:
    """The global `config.md` lock.

    `write_config` is a read-merge-write of one file, so two of them lose one
    update — and the writers are not all user-initiated: `_setup_state` on
    `GET /api/config` backfills `setup_done`, which means merely loading the
    app in a second tab can race a setting being saved in the first (#194
    review). Cross-process for the same reason the campaign locks are: the
    file belongs to the store, not to one server.

    It is taken inside a campaign lock (`campaigns.set_campaign_routing`'s
    `read_config`, which can write `config.md` on a store's first read, and
    `inference.settings`' campaign write) and never around one: a holder of
    this lock must never reach a campaign lock. `llm_connections.LOCK` is this
    same object -- one cross-process lock over every model-settings
    read-modify-write, rather than a process-local one taken before it -- and
    the only locks taken under it are `inference.facts`' process-local file
    lock and `inference_retired`'s, the retirement record's -- both
    process-local and innermost: nothing that holds either takes another.

    `config.format_hold` is this lock with the store's format read inside it,
    and the one hold every model-settings write lands in: the format switch
    writes its marker under this lock, from any process, so a write that
    checks and writes in one hold cannot be outrun by it.
    """
    return _config


_backups = _ProcessScopedLock("domain", "backups", BackupBusy)


def backup_lock() -> _ProcessScopedLock:
    """The store-wide backup lock (#32).

    One archive of one store at a time. Two backups running together would zip
    the same library twice for nothing, and -- worse -- a retention sweep that
    listed the directory while another run was publishing into it can delete
    by a count it took before the newest archive existed. Cross-process for the
    same reason the others are: the schedule belongs to the store, and a second
    grimoire on the same synced folder is exactly the case this exists for.

    A leaf, like `config_lock`: nothing under it takes another lock, so it has
    no place in the ordering rules above and cannot participate in a cycle. It
    deliberately does **not** take the campaign locks -- an archive of a
    hundred campaigns cannot hold a hundred locks for the length of a zip
    without stalling play, so a backup taken mid-write can catch one campaign's
    two files a moment apart. That is the accepted trade, and it is why the
    archive is a coarse restore point rather than a transactional snapshot.
    """
    return _backups


_inference_migration = _ProcessScopedLock("domain", "inference-migration", MigrationBusy)


def inference_migration_lock() -> _ProcessScopedLock:
    """The store-wide lock the inference-settings migration runs under
    (`store.inference.migrate`, spec 11.2): two starts never interleave, in one
    process or two.

    Only ever TRIED, never waited on: `migrate.ensure` returns the current
    status when it is held, and `PUT /config/data-dir` answers 409 `busy`. So
    it cannot sit in a wait cycle. It is the outermost lock its holder takes:
    under it the migration takes the backup lock, `config_lock`, the facts
    module's lock and -- one at a time, never waiting -- a campaign lock; and
    nothing that holds any of those waits for this. Status may try it and
    immediately release it under a settings read (#496); it never waits.
    """
    return _inference_migration


def holds_campaign(cid: str) -> bool:
    """Whether the calling thread holds `campaign_lock(cid)` -- for a function
    whose contract is that its CALLER holds it, to refuse rather than write
    unlocked (`migrate.campaign`)."""
    return campaign_lock(cid)._is_owned()


_module_edit = _ProcessScopedLock("domain", "module-edit", ModuleEditBusy)


def module_edit_lock() -> _ProcessScopedLock:
    """The global module-edit lock. Cross-process for the same reason the
    campaign locks are: whole-directory pack publication mutates the shared
    user library, not just one campaign."""
    return _module_edit


@contextmanager
def hold_all(cids):
    """Hold every named campaign lock, in sorted order, under ONE deadline.

    **Sorted order.** The two multi-campaign holders -- ``module_edit.
    _campaign_locks`` and the world-module rebind route -- did NOT agree before
    this function existed, though an earlier version of this docstring said
    they did and called the agreement accidental. ``list_campaigns()`` does
    walk ``sorted(base.iterdir())``, but it ends on
    ``out.sort(key=updated, reverse=True)`` (``campaigns/read.py``), so what it
    returns is recency order; the rebind route sorts by cid. Whenever those two
    orders disagree -- the ordinary case, since ids are slugs and ``updated`` is
    a clock -- the two holders acquire the same locks in opposite orders, and
    two concurrent requests wedge permanently on each other (#267). Both
    endpoints are plain ``def``, so FastAPI runs them in the threadpool and
    they are genuinely concurrent.

    So this is the fix rather than a formality: it is the ONLY place that
    sorts, and no other caller may hold more than one campaign lock.

    That last sentence used to be a rule and not a guarantee, and the
    difference is the whole of #267: the second holder was written without
    knowledge of the first, each carrying a comment calling itself the only
    one, and the only checks were two per-holder tests in
    ``test_locks_store.py`` -- one for each holder that already existed.
    ``test_lock_order_guard.py`` is what now stands between this docstring and
    a third: it reads the package's ASTs and fails any function outside here
    that can hold two campaign locks at once, so the rule is enforced on code
    rather than on whoever reads this. Its reach is stated in its own
    docstring, and it stops short of a lock reached through an alias and of two
    locks taken across a call boundary -- so if you are adding a holder, route
    it through here and add its behavioural test beside those two.

    **One deadline**, not one per lock: applying ``LOCK_TIMEOUT`` to each of N
    locks while holding the earlier ones would give an N x LOCK_TIMEOUT convoy.

    ``ExitStack`` rather than a hand-rolled reversed loop: it registers each
    lock the instant it is acquired, and it runs EVERY registered exit even
    when one of them raises. ``for lock in reversed(held): lock.release()``
    strands every remaining lock the moment one release fails.
    """
    deadline = time.monotonic() + LOCK_TIMEOUT
    with ExitStack() as stack:
        for cid in sorted(set(cids)):
            lock = campaign_lock(cid)
            if not lock.acquire(timeout=_remaining(deadline)):
                raise CampaignBusy(cid)
            stack.callback(lock.release)
        yield
