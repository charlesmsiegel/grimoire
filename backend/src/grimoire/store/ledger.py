"""Append-only, hash-chained ledgers under the reserved ``authority/`` root.

A ledger is a run of batch files ``<seq:010d>.json``; each names the etag of
the batch before it, so an interior edit, deletion or reordering breaks the
chain. A broken ledger is never read partially. Limit, documented: with local
file access, appending, editing or truncating the *tail*, or restoring an older
copy, keeps a valid chain and is not detectable here.

Only trusted code writes ledgers, through one ``LedgerWriter`` per ledger per
store session; appends never pass through a worker's authorization.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .engine import Access
from .errors import LedgerBroken, StoreError
from .jsonfmt import MAX_FILE_BYTES, JsonError, etag, parse_strict, render
from .layout import Parts, is_noise, ledger_folder, ledger_name, ledger_parts, ledger_seq

LEDGER_FORMAT = "grimoire.ledger/1"
MAX_BATCH_ENTRIES = 500
MAX_BATCH_BYTES = 4 * 1024 * 1024
assert MAX_BATCH_BYTES < MAX_FILE_BYTES

Head = tuple[int, str]  # (seq, etag) of the newest batch


@dataclass(frozen=True)
class Batch:
    seq: int
    etag: str
    entries: tuple[Mapping[str, object], ...]


@dataclass(frozen=True)
class Ledger:
    name: str
    batches: tuple[Batch, ...]
    ignored: tuple[str, ...]  # non-canonical files beside the batches: reported, never replayed

    @property
    def head(self) -> Head | None:
        return (self.batches[-1].seq, self.batches[-1].etag) if self.batches else None

    @property
    def entries(self) -> tuple[Mapping[str, object], ...]:
        return tuple(entry for batch in self.batches for entry in batch.entries)


def read_ledger(access: Access, name: str) -> Ledger:
    """The whole ledger, verified; ``LedgerBroken`` for any break, never a partial result."""
    with access.cached_listings():  # one listing of the folder, not one per batch
        return _read_ledger(access, ledger_name(name))


def _read_ledger(access: Access, name: str) -> Ledger:
    folder = ledger_folder(name)
    if access.presence(folder) != "present":
        return Ledger(name, (), ())
    access.guard.check_read(folder)  # the folder itself is never a link (LinkRefused)
    try:
        listed = access.list_dir(folder)
    except OSError as error:
        raise LedgerBroken(name, None, f"cannot list the ledger: {error}") from error
    seqs, ignored = [], []
    for file_name in listed:
        seq = ledger_seq(file_name)
        if seq is not None:
            seqs.append(seq)
        elif not is_noise(file_name):
            ignored.append("/".join(folder + (file_name,)))
    seqs.sort()
    if seqs != list(range(1, len(seqs) + 1)):
        missing = next(n for n, seq in enumerate(seqs, 1) if seq != n)
        raise LedgerBroken(name, missing, "a batch is missing")
    batches: list[Batch] = []
    previous: str | None = None
    for seq in seqs:
        parts = ledger_parts(name, seq)
        try:
            too_large = access.too_large(parts)  # refused before it is read into memory
            data = None if too_large else access.read(parts)
        except (OSError, StoreError, ValueError) as error:  # a link or non-regular file is refused
            raise LedgerBroken(name, seq, f"unreadable: {error}") from error
        if too_large:
            raise LedgerBroken(name, seq, "larger than a reader accepts")
        if data is None:
            raise LedgerBroken(name, seq, "vanished while reading")
        batches.append(_parse_batch(name, seq, data, previous))
        previous = batches[-1].etag
    return Ledger(name, tuple(batches), tuple(sorted(ignored)))


def _parse_batch(name: str, seq: int, data: bytes, previous: str | None) -> Batch:
    try:
        document = parse_strict(data)
    except JsonError as error:
        raise LedgerBroken(name, seq, f"not valid JSON: {error}") from None
    if document.get("format") != LEDGER_FORMAT:
        raise LedgerBroken(name, seq, f"unsupported format {document.get('format')!r}")
    if set(document) != {"format", "ledger", "seq", "previous", "entries"}:
        raise LedgerBroken(name, seq, "unexpected fields")
    if document["ledger"] != name or document["seq"] != seq or type(document["seq"]) is not int:
        raise LedgerBroken(name, seq, "the batch names another ledger or position")
    if document["previous"] != previous:
        raise LedgerBroken(name, seq, "the chain does not link to the previous batch")
    entries = document["entries"]
    if (not isinstance(entries, tuple) or not 1 <= len(entries) <= MAX_BATCH_ENTRIES
            or not all(isinstance(entry, Mapping) for entry in entries)):
        raise LedgerBroken(name, seq, f"a batch holds 1 to {MAX_BATCH_ENTRIES} objects")
    return Batch(seq, etag(data), entries)


def _estimate(entry: Mapping[str, object]) -> int:
    """An upper bound on an entry's bytes inside a batch (each line gains four spaces of indent)."""
    text = render(entry)
    return len(text) + 4 * (text.count(b"\n") + 1) + 2


def entry_cost(entry: Mapping[str, object]) -> int:
    """The entry's estimated size; ``ValueError`` if it cannot be rendered or no batch could hold it."""
    cost = _estimate(entry)
    if cost > MAX_BATCH_BYTES:
        raise ValueError("a single ledger entry is larger than a batch may be")
    return cost


def render_batches(name: str, head: Head | None, entries: Sequence[Mapping[str, object]]
                   ) -> list[tuple[Parts, bytes]]:
    """Chained batch files continuing from ``head``, split by count and by (estimated) size."""
    name = ledger_name(name)
    seq, previous = head if head is not None else (0, None)
    rendered: list[tuple[Parts, bytes]] = []
    chunk: list[Mapping[str, object]] = []
    size = 0

    def emit() -> None:
        nonlocal seq, previous, chunk, size
        data = render({"format": LEDGER_FORMAT, "ledger": name, "seq": seq + 1, "previous": previous,
                       "entries": chunk})
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("a ledger batch would be larger than a reader accepts")
        seq += 1
        rendered.append((ledger_parts(name, seq), data))
        previous, chunk, size = etag(data), [], 0

    for entry in entries:
        cost = entry_cost(entry)
        if chunk and (len(chunk) >= MAX_BATCH_ENTRIES or size + cost > MAX_BATCH_BYTES):
            emit()
        chunk.append(entry)
        size += cost
    if chunk:
        emit()
    return rendered
