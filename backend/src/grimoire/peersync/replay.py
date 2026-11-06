"""Replaying one 019 apply step on the device the mirror stands in for (028).

The client runs 019's ``apply_sync`` against its own library and the mirror. Each time a step
commits on the mirror, the same writes travel to the serving device, which commits them as one
010 transaction of its own under the client's peer principal, re-reads every file and answers
with its etags. Only then does 019 go on to its next step, so the real devices are written in
019's own order (data(a), data(b), verify, keeps(b), keeps(a), archive(b), archive(a), pending(b),
pending(a)) and a failure at any point leaves exactly the state a crash at that point of a local
sync leaves.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

from grimoire.store import LibraryStore, StaleWrite, StoreError, WriteConflict, WriteDenied, etag
from grimoire.store.layout import conflict_parts
from grimoire.sync import Step, device_of
from grimoire.sync import keeps as kept_record
from grimoire.sync.bookkeeping import is_shard, listed_shards, root_name
from grimoire.sync.bookkeeping import parse as parse_document
from grimoire.sync.bookkeeping import stage as stage_states
from grimoire.sync.documents import keeps_name, pair_state
from grimoire.sync.keeps import (State, first_copies, not_delivered_elsewhere, peer_may_write, records_hold,
                                 slot_stands)

from .errors import MirrorMismatch, PeerRefused
from .wire import Channel

#: The most writes one step may carry: 019 commits a step as one transaction, never in pieces.
MAX_STEP_OPS = 200_000
MAX_STEP_BYTES = 8 * 1024 * 1024 * 1024
#: Of which record, kept-version and state bodies (held in memory, as a local 019 step holds
#: them) may be at most this much; blobs are spooled to disk and count only toward the total.
MAX_STEP_MEMORY = 1024 * 1024 * 1024


def send_step(channel: Channel, step: Step, blob_reader: Callable[[str, int], bytes | None]) -> None:
    """Send ``step``'s writes and wait until the peer has committed and verified every one of them."""
    def ops() -> Iterator[Op]:
        for copy in step.copies:
            if copy.data is None:  # a blob: the stand-in now holds the verified bytes
                data = blob_reader(copy.path, copy.size)  # type: ignore[arg-type]
                if data is None:
                    raise MirrorMismatch(f"the blob {copy.path} vanished from the mirror before it could be sent")
                yield {"op": "blob", "path": copy.path, "size": copy.size}, data, copy.path, copy.etag
            else:
                yield {"op": "put", "path": copy.path, "expected": copy.expected}, copy.data, copy.path, copy.etag
        for keep in step.keeps:
            yield ({"op": "keep", "path": keep.path, "slot": keep.slot}, keep.data,
                   kept_target(keep.path, keep.data, keep.slot), etag(keep.data))
        for name, data, old in step.states:
            yield {"op": "state", "name": name, "expected": old}, data, f".sync/{name}", etag(data)
    send_ops(channel, step.kind, ops())


#: One write of a step: its ``op`` header, its body, and the file it lands in with the etag it must then hold.
Op = tuple[dict, bytes, str, str]  # type: ignore[type-arg]


def send_ops(channel: Channel, kind: str, ops: Iterable[Op]) -> None:
    """Stream one step's writes, commit them, and check the peer's reply against what they must hold.

    ``ops`` is consumed as it is sent, so a step of many blobs never holds them all in memory.
    """
    expected: dict[str, str] = {}
    for header, body, target, tag in ops:
        channel.send({"t": "op", **header}, body)
        expected[target] = tag
    channel.send({"t": "commit", "step": kind})
    header, _ = channel.recv()
    if header.get("t") != "committed" or header.get("count") != len(expected) or header.get("digest") != confirmation(expected):
        raise PeerRefused("verification", f"the peer did not confirm the {kind} step's planned bytes")


def kept_target(path: str, data: bytes, slot: int) -> str:
    """Where a kept version of ``path`` holding ``data`` lands in ``slot``."""
    return "/".join(conflict_parts(tuple(path.split("/")), etag(data), slot))


def confirmation(etags: dict[str, str]) -> str:
    """One digest over every ``path -> etag`` a step wrote, as the serving device re-read them.

    The commit reply carries this rather than the etags themselves: a step may hold up to
    ``MAX_STEP_OPS`` writes, and their etags would not fit one reply's header.
    """
    return hashlib.sha256(json.dumps(etags, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class StepBuffer:
    """The serving side: collects one step's writes, then commits and verifies them together.

    Exactly one reply goes back per step, at its ``commit``. The client streams a step's ``op``
    frames without waiting, so a write refused while they arrive (the step grew too large, a blob
    could not be staged) *poisons* the buffer: every later op of that step is dropped unread and
    the ``commit`` is answered with the refusal. Refusing at once instead would leave the step's
    remaining ops to be committed on their own as if they were a step, and the client would read
    that reply as the answer to its next request.

    Blob bodies are spooled to a private temporary folder as they arrive and read back one at a
    time when the commit stages them, so a first sync of many pictures never holds them all in
    memory; record bodies (each at most 010's 8 MiB file cap, 1 GiB in all) stay in memory until the commit.

    ``resolving`` is the one path a resolve request named, while the session is resolving it.
    Only then may a write carry resolve's power, and only for that path: keeping the replaced
    version as a *hold*, which an aborted commit preserves (``hold``), and which is recorded as
    held exactly as a local ``resolve`` records it, in commits that land before the hold's own
    (``_held_beforehand``). A sync step never needs it:
    what it keeps is the other device's version, which that device still holds. A peer never
    replaces a definition revision here (``replace``): a revision is immutable, and only the
    device that holds the conflicted one replaces it, by starting the resolve itself.
    """

    def __init__(self, peer: str) -> None:
        self.peer = peer
        self.resolving: str | None = None
        self.ops: list[tuple[dict, bytes | Path]] = []  # type: ignore[type-arg]
        self.size = self.memory = 0
        self.poisoned: PeerRefused | None = None
        self._spool: tempfile.TemporaryDirectory[str] | None = None

    def reset(self) -> None:
        """Drop the step under way, its spooled blobs included."""
        self.ops, self.size, self.memory, self.poisoned = [], 0, 0, None
        if self._spool is not None:
            self._spool.cleanup()
            self._spool = None

    def add(self, header: dict, body: bytes) -> None:  # type: ignore[type-arg]
        if self.poisoned is not None:
            return
        self.size += len(body)
        if header.get("op") != "blob":
            self.memory += len(body)
        if len(self.ops) >= MAX_STEP_OPS or self.size > MAX_STEP_BYTES or self.memory > MAX_STEP_MEMORY:
            self._poison(PeerRefused("too_large", "one step carries more than this device accepts"))
            return
        if header.get("op") == "blob":
            try:
                if self._spool is None:
                    self._spool = tempfile.TemporaryDirectory(prefix="grimoire-peer-step-")
                spooled = Path(self._spool.name) / str(len(self.ops))
                spooled.write_bytes(body)
            except OSError as error:
                self._poison(PeerRefused("unavailable", f"cannot stage a blob here: {error}"))
                return
            self.ops.append((header, spooled))
        else:
            self.ops.append((header, body))

    def _poison(self, refusal: PeerRefused) -> None:
        self.reset()
        self.poisoned = refusal

    def commit(self, store: LibraryStore, handle: object, *, may_hold: str | None = None) -> dict[str, str]:
        """Commit every write as one transaction; return each written file's etag as re-read.

        ``may_hold`` is the etag of the one version this step's record may newly hold at the resolving
        path: this device's own current version, in the step a resolve makes to record its hold
        before the data commit (``ResolveRequest.check_step`` decides it). In every other step a
        record may add no hold."""
        try:
            if self.poisoned is not None:
                raise self.poisoned
            return self._commit(store, handle, may_hold)
        finally:
            self.reset()

    def _commit(self, store: LibraryStore, handle: object, may_hold: str | None) -> dict[str, str]:
        ops = self.ops
        if not ops:
            raise PeerRefused("empty", "a step with no writes")
        txn = store.transaction()
        targets: list[tuple[str, str, Callable[[], bytes | None]]] = []
        records = False
        # the roots this step writes, by name: a shard is accepted only as its own root lists it
        roots = {header.get("name"): body for header, body in ops
                 if header.get("op") == "state" and header.get("name") in (
                     root_name("archive", self.peer), root_name("pending", self.peer))}
        try:
            self._check_documents(store, roots, ops)
            # 028: the versions a resolve step holds here (this device's own, as ``check_step`` found),
            # which an earlier step's record must already hold (``_held_beforehand``), and the one
            # version a resolve's record step may add as held
            recordable = set() if may_hold is None or self.resolving is None else {(self.resolving, may_hold)}
            holds = {(header["path"], etag(body)) for header, body in ops if header.get("op") == "keep"
                     and header.get("hold") is True and header.get("path") == self.resolving
                     and isinstance(body, bytes)}
            # the keeps of this step that write the first copy of the peer's bytes at their path, read
            # before anything is staged: only those may this step's record add as delivered. A keep of
            # bytes some slot already holds writes nothing new, or a second copy of what may be a hold;
            # and a hold is never delivered (``dismiss`` would take this device's own version for it).
            first = first_copies(store, [(header["path"], body) for header, body in ops if header.get("op") == "keep"
                                         and header.get("hold", False) is False
                                         and type(header.get("path")) is str and isinstance(body, bytes)])
            for header, body in ops:
                op, path = header.get("op"), header.get("path")
                if isinstance(body, Path):  # a spooled blob: read back only when staged
                    if op != "blob" or type(path) is not str or type(header.get("size")) is not int:
                        raise PeerRefused("invalid", f"a write this device does not accept: {op!r}")
                    txn.put_blob_exact(path, _spooled(body), size=header["size"])
                    targets.append((path, path, _reader(store, path)))
                    records = True
                elif op == "put" and type(path) is str and _etag_or_none(header.get("expected")):
                    if header.get("replace", False) is not False:
                        raise PeerRefused("invalid", "a peer never replaces a definition revision here: "
                                                     "start the resolve on this device")
                    txn.put_exact(path, body, header.get("expected"), regenerable=True)
                    targets.append((path, path, _reader(store, path)))
                    records = True
                elif op == "keep" and type(path) is str and type(header.get("slot")) is int:
                    target = kept_target(path, body, header["slot"])
                    # A sync keeps the peer's version, which the peer still holds; resolve holds the
                    # version it replaces here, which may exist nowhere else.
                    hold = self._resolve_power(header, "hold", path)
                    txn.keep_version(path, body, slot=header["slot"], regenerable=not hold)
                    targets.append((target, target, _reader(store, target)))
                    records = True
                elif op == "state" and pair_state(header.get("name"), self.peer) and _etag_or_none(header.get("expected")):
                    name = header["name"]
                    expected = self._state_expected(store, name, body, header.get("expected"), roots, first,
                                                    recordable)
                    txn.put_sync_state(name, body, expected)
                    targets.append((f".sync/{name}", name, lambda name=name: store.read_sync_state(name)))
                else:  # only this pair's own .sync files, and records the peer's grants then decide
                    raise PeerRefused("invalid", f"a write this device does not accept: {op!r}")
            elsewhere = self._held_beforehand(store, ops, holds)
            if elsewhere:  # this device's own records of its other pairs, committed before the hold
                # a step that cannot commit writes nothing, its grants included: otherwise a peer the
                # hold's path denies could still have this device's own bytes taken out of its other
                # pairs' delivered versions, request after request, leaving those copies undismissable
                txn.check(handle if records else None)
                early = store.transaction()
                stage_states(early, elsewhere)
                early.commit(None)
            txn.commit(handle if records else None, {})  # the peer can never supply an approval
        except PeerRefused:
            raise
        except (StaleWrite, WriteConflict) as error:
            raise PeerRefused("stale", f"this library changed since the snapshot: {error}") from error
        except WriteDenied as error:
            raise PeerRefused("denied", f"a write was not authorized: {error}") from error
        except (StoreError, ValueError) as error:
            raise PeerRefused("refused", f"{type(error).__name__}: {error}") from error
        found: dict[str, str] = {}
        for key, _, read in targets:
            data = read()
            if data is None:
                raise PeerRefused("verification", f"{key} is missing after the commit")
            found[key] = etag(data)
        return found

    def _resolve_power(self, header: dict, name: str, path: str) -> bool:  # type: ignore[type-arg]
        """Whether this write uses resolve's power ``name`` (``hold``); refused unless it is for the
        path the session's resolve request named."""
        value = header.get(name, False)
        if value is False:
            return False
        if value is not True or self.resolving is None or path != self.resolving:
            raise PeerRefused("invalid", f"{name!r} is only for the path a resolve request named")
        return True

    def _held_beforehand(self, store: LibraryStore, ops: list,  # type: ignore[type-arg]
                         holds: set[tuple[str, str]]) -> list[State]:
        """Check that each of ``holds`` is already recorded as held, as a local ``resolve`` records it
        (``grimoire.sync.resolution``), and return what this device must still commit **before** the
        hold: those bytes taken out of every other pair's delivered versions.

        The pair's record must already hold the bytes on disk, written by the resolve's own record
        step (or already so, when the hold reuses a slot it lists as held), and a record this step
        writes must still hold them. Staged in the hold's own commit, the record would be ``.sync``
        state that 010's recovery may drop (a staged file gone or damaged) while it finishes the hold:
        a slot the hold reused (bytes once delivered) would then stay listed as delivered, and
        ``dismiss`` would take this device's own version, perhaps its only copy, for a peer's. The
        other pairs' records are this device's own (the peer sees none of them, and may write none),
        so it writes them itself, in a commit of their own under the same lock. If the hold then
        never lands, those bytes are only never dismissable through another pair: that fails closed."""
        if not holds:
            return []
        name = keeps_name(self.peer)
        written = [body for header, body in ops if header.get("op") == "state" and header.get("name") == name]
        standing = store.read_sync_state(name)
        device = device_of(store)
        devices: tuple[str, str] = tuple(sorted((device, self.peer)))  # type: ignore[assignment]
        states: list[State] = []
        for path, tag in sorted(holds):
            if not all(records_hold(record, devices, path, tag) for record in (standing, *written)):
                raise PeerRefused("invalid", f"a resolve's hold of {path} is recorded as held in a commit before "
                                             "the one that writes it")
            states.extend(not_delivered_elsewhere(store, device, self.peer, path, tag))
        return states

    def _state_expected(self, store: LibraryStore, name: str, body: bytes, expected: str | None, roots: dict,  # type: ignore[type-arg]
                        first: set[tuple[str, str]], recordable: set[tuple[str, str]]) -> str | None:
        """The etag a state write of the pair must replace, after checking the write is one a sync makes."""
        if name == keeps_name(self.peer) and len(body) > kept_record.RECORD_LIMIT:
            # this device writes no record past the cap (``keeps.fit`` leaves excess deliveries
            # unrecorded): one it accepted from a peer would stop its own next sync or dismissal
            raise PeerRefused("invalid", f"a kept-version record past {kept_record.RECORD_LIMIT} bytes")
        if name == keeps_name(self.peer) and not peer_may_write(
                store.read_sync_state(name), body, tuple(sorted((device_of(store), self.peer))),  # type: ignore[arg-type]
                lambda path, tag: (path, tag) in first, lambda path, tag: slot_stands(store, path, tag),
                lambda path, tag: (path, tag) in recordable):
            # a sync records as delivered only the first copies its own keeps write in the same commit, keeps
            # a held version recorded while its slot stands, and never dismisses one: dismissing is this
            # device's own decision, authorized here (``grimoire.sync.dismiss``), never the peer's. Only a
            # resolve's record step adds a hold, and only of the version that resolve replaces here.
            raise PeerRefused("invalid", "a peer may not dismiss, add or drop a hold, or record a version it "
                                         "did not deliver")
        for kind in ("archive", "pending"):
            if not is_shard(name, kind, self.peer):
                continue
            # a sync writes a shard only with the root that lists it, so a peer can never plant one
            if listed_shards(kind, self.peer, roots.get(root_name(kind, self.peer))).get(name) != etag(body):
                raise PeerRefused("invalid", f"{name} is not a shard its root lists in this step")
            if name not in listed_shards(kind, self.peer, store.read_sync_state(root_name(kind, self.peer))):
                # a shard this device's root does not list is stale and never read or sent, so the
                # client could not know its etag; its own root's compare-and-swap guards the document
                current = store.read_sync_state(name)
                return None if current is None else etag(current)
        return expected

    def _check_documents(self, store: LibraryStore, roots: dict, ops: list) -> None:  # type: ignore[type-arg]
        """Each archive or marker this step writes must read, with its shards, as a valid document of
        this pair: the shards written now and the listed ones already here. A peer could otherwise
        leave a root that lists junk shards, which every later plan reads in full and then ignores."""
        written = {header.get("name"): body for header, body in ops if header.get("op") == "state"}
        devices = tuple(sorted((device_of(store), self.peer)))
        for kind in ("archive", "pending"):
            name = root_name(kind, self.peer)
            if name not in roots:
                continue
            files = {name: roots[name]}
            for shard in listed_shards(kind, self.peer, roots[name]):
                data = written[shard] if shard in written else store.read_sync_state(shard)
                if data is not None:
                    files[shard] = data
            view = parse_document(kind, files, self.peer, devices)  # type: ignore[arg-type]
            if view.state != "valid":
                raise PeerRefused("invalid", f"the {kind} this step writes is not valid for this pair: {view.reason}")


def _spooled(path: Path) -> Callable[[], bytes | None]:
    def read() -> bytes | None:
        try:
            return path.read_bytes()
        except OSError:
            return None  # the store then refuses the step as stale
    return read


def _etag_or_none(value: object) -> bool:
    return value is None or type(value) is str


def _reader(store: LibraryStore, path: str) -> Callable[[], bytes | None]:
    def read() -> bytes | None:
        try:
            return store.read_exact(path)
        except (StoreError, OSError):
            return None
    return read
