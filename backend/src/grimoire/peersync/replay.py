"""Replaying one 019 apply step on the device the mirror stands in for (028).

The client runs 019's ``apply_sync`` against its own library and the mirror. Each time a step
commits on the mirror, the same writes travel to the serving device, which commits them as one
010 transaction of its own under the client's peer principal, re-reads every file and answers
with its etags. Only then does 019 go on to its next step, so the real devices are written in
019's own order (data(a), data(b), verify, archive(b), archive(a), pending(b), pending(a)) and a
failure at any point leaves exactly the state a crash at that point of a local sync leaves.
"""

from __future__ import annotations

from collections.abc import Callable

from grimoire.store import LibraryStore, StaleWrite, StoreError, WriteConflict, WriteDenied, etag
from grimoire.store.layout import conflict_parts
from grimoire.sync import Step
from grimoire.sync.documents import archive_name, pending_name

from .errors import PeerRefused, TransportError
from .wire import Channel

#: The most writes one step may carry: 019 commits a step as one transaction, never in pieces.
MAX_STEP_OPS = 200_000
MAX_STEP_BYTES = 8 * 1024 * 1024 * 1024


def send_step(channel: Channel, step: Step, blob_reader: Callable[[str, int], bytes | None]) -> None:
    """Send ``step``'s writes and wait until the peer has committed and verified every one of them."""
    expected: dict[str, str] = {}
    for copy in step.copies:
        if copy.data is None:  # a blob: the stand-in now holds the verified bytes
            data = blob_reader(copy.path, copy.size)  # type: ignore[arg-type]
            if data is None:
                raise TransportError(f"the blob {copy.path} vanished before it could be sent")
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
    if header.get("t") != "committed" or header.get("etags") != expected:
        raise PeerRefused("verification", f"the peer did not confirm the {step.kind} step's planned bytes")


class StepBuffer:
    """The serving side: collects one step's writes, then commits and verifies them together."""

    def __init__(self, peer: str) -> None:
        self.peer = peer
        self.ops: list[tuple[dict, bytes]] = []  # type: ignore[type-arg]
        self.size = 0

    def add(self, header: dict, body: bytes) -> None:  # type: ignore[type-arg]
        self.ops.append((header, body))
        self.size += len(body)
        if len(self.ops) > MAX_STEP_OPS or self.size > MAX_STEP_BYTES:
            raise PeerRefused("too_large", "one step carries more than this device accepts")

    def commit(self, store: LibraryStore, handle: object) -> dict[str, str]:
        """Commit every write as one transaction; return each written file's etag as re-read."""
        ops, self.ops, self.size = self.ops, [], 0
        if not ops:
            raise PeerRefused("empty", "a step with no writes")
        txn = store.transaction()
        targets: list[tuple[str, str, Callable[[], bytes | None]]] = []
        allowed = {archive_name(self.peer), pending_name(self.peer)}
        records = False
        try:
            for header, body in ops:
                op, path = header.get("op"), header.get("path")
                if op == "put" and type(path) is str and _etag_or_none(header.get("expected")):
                    txn.put_exact(path, body, header.get("expected"), regenerable=True)
                    targets.append((path, path, _reader(store, path)))
                    records = True
                elif op == "blob" and type(path) is str and type(header.get("size")) is int:
                    data = body
                    txn.put_blob_exact(path, lambda data=data: data, size=header["size"])
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


def _etag_or_none(value: object) -> bool:
    return value is None or type(value) is str


def _reader(store: LibraryStore, path: str) -> Callable[[], bytes | None]:
    def read() -> bytes | None:
        try:
            return store.read_exact(path)
        except (StoreError, OSError):
            return None
    return read
