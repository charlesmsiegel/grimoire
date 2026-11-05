"""Replaying one 019 apply step on the device the mirror stands in for (028).

The client runs 019's ``apply_sync`` against its own library and the mirror. Each time a step
commits on the mirror, the same writes travel to the serving device, which commits them as one
010 transaction of its own under the client's peer principal, re-reads every file and answers
with its etags. Only then does 019 go on to its next step, so the real devices are written in
019's own order (data(a), data(b), verify, archive(b), archive(a), pending(b), pending(a)) and a
failure at any point leaves exactly the state a crash at that point of a local sync leaves.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Callable
from pathlib import Path

from grimoire.store import LibraryStore, StaleWrite, StoreError, WriteConflict, WriteDenied, etag
from grimoire.store.layout import conflict_parts
from grimoire.sync import Step
from grimoire.sync.documents import archive_name, pending_name

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
    expected: dict[str, str] = {}
    for copy in step.copies:
        if copy.data is None:  # a blob: the stand-in now holds the verified bytes
            data = blob_reader(copy.path, copy.size)  # type: ignore[arg-type]
            if data is None:
                raise MirrorMismatch(f"the blob {copy.path} vanished from the mirror before it could be sent")
            channel.send({"t": "op", "op": "blob", "path": copy.path, "size": copy.size}, data)
        else:
            channel.send({"t": "op", "op": "put", "path": copy.path, "expected": copy.expected}, copy.data)
        expected[copy.path] = copy.etag
    for keep in step.keeps:
        channel.send({"t": "op", "op": "keep", "path": keep.path, "slot": keep.slot}, keep.data)
        expected["/".join(conflict_parts(tuple(keep.path.split("/")), etag(keep.data), keep.slot))] = etag(keep.data)
    for name, data, old in step.states:
        channel.send({"t": "op", "op": "state", "name": name, "expected": old}, data)
        expected[f".sync/{name}"] = etag(data)
    channel.send({"t": "commit", "step": step.kind})
    header, _ = channel.recv()
    if header.get("t") != "committed" or header.get("count") != len(expected) or header.get("digest") != confirmation(expected):
        raise PeerRefused("verification", f"the peer did not confirm the {step.kind} step's planned bytes")


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
    """

    def __init__(self, peer: str) -> None:
        self.peer = peer
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

    def commit(self, store: LibraryStore, handle: object) -> dict[str, str]:
        """Commit every write as one transaction; return each written file's etag as re-read."""
        try:
            if self.poisoned is not None:
                raise self.poisoned
            return self._commit(store, handle)
        finally:
            self.reset()

    def _commit(self, store: LibraryStore, handle: object) -> dict[str, str]:
        ops = self.ops
        if not ops:
            raise PeerRefused("empty", "a step with no writes")
        txn = store.transaction()
        targets: list[tuple[str, str, Callable[[], bytes | None]]] = []
        allowed = {archive_name(self.peer), pending_name(self.peer)}
        records = False
        try:
            for header, body in ops:
                op, path = header.get("op"), header.get("path")
                if isinstance(body, Path):  # a spooled blob: read back only when staged
                    if op != "blob" or type(path) is not str or type(header.get("size")) is not int:
                        raise PeerRefused("invalid", f"a write this device does not accept: {op!r}")
                    txn.put_blob_exact(path, _spooled(body), size=header["size"])
                    targets.append((path, path, _reader(store, path)))
                    records = True
                elif op == "put" and type(path) is str and _etag_or_none(header.get("expected")):
                    txn.put_exact(path, body, header.get("expected"), regenerable=True)
                    targets.append((path, path, _reader(store, path)))
                    records = True
                elif op == "keep" and type(path) is str and type(header.get("slot")) is int:
                    target = "/".join(conflict_parts(tuple(path.split("/")), etag(body), header["slot"]))
                    txn.keep_version(path, body, slot=header["slot"], regenerable=True)  # the peer still holds them
                    targets.append((target, target, _reader(store, target)))
                    records = True
                elif op == "state" and header.get("name") in allowed and _etag_or_none(header.get("expected")):
                    name = header["name"]
                    txn.put_sync_state(name, body, header.get("expected"))
                    targets.append((f".sync/{name}", name, lambda name=name: store.read_sync_state(name)))
                else:  # only this pair's own .sync files, and records the peer's grants then decide
                    raise PeerRefused("invalid", f"a write this device does not accept: {op!r}")
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
