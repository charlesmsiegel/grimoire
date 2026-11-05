"""A pair's archive and pending marker on disk (028): one file while it fits, sharded by path hash past it.

019 kept each as one file under 010's 8 MiB cap (about 23,000 paths), and refused the whole sync
(``ArchiveTooLarge``) past it. Now a document that would not fit is split by the first hex digits
of the SHA-256 of each path:

- the **root** (``archive.json``, ``pending.json``) is ``grimoire.sync.{archive,pending}/2`` and
  lists every shard of its width with the etag that shard must have;
- each **shard** (``archive.<prefix>.json``) holds exactly the entries whose path hashes to its
  prefix, with its own ``check`` digest.

A document that fits in one file is still written as 019's ``/1`` file, byte for byte, so small
libraries read and write exactly as before and an older build still reads them; a ``/1`` file is
read as-is. The width is the smallest whose every file fits by an upper-bound estimate: a pure
function of the entries, so ``sync(a, b)`` and ``sync(b, a)`` still write identical bytes.

Every file of a document is written in one 010 transaction, so a crash leaves the old root with
its old shards or the new root with its new ones. The root's etag list binds the set: a shard that
is missing, damaged, edited or left over from another commit makes the **whole document ignored**,
exactly as a damaged single-file archive is (every path it names becomes uncertain, never trusted
and never forgotten). Shards of another width left by an earlier commit are not listed, so they
are never trusted; they stay on disk until a later width overwrites them, and are read only to
take their etags for that compare-and-swap (and, best effort, the paths they name when the
document is ignored).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from types import MappingProxyType

from grimoire.store import LibraryStore, LinkRefused, NameCollision, UnstorableIdentifier, etag
from grimoire.store.jsonfmt import JsonError, is_etag, parse_strict, render
from grimoire.store.layout import SYNC_MAX_SHARD_WIDTH

from .documents import (
    ArchiveView, DismissedView, PendingView, archive_body, archive_entries, dismissed_name, named_paths,
    parse_archive, parse_dismissed, parse_pending, pending_body, pending_entries, render_archive,
    render_dismissed, render_pending,
)
from .errors import ArchiveTooLarge, SyncInterrupted

FORMATS = {"archive": "grimoire.sync.archive/2", "pending": "grimoire.sync.pending/2"}
#: Upper bounds used to choose a width: one path's archive or marker entry (three etags and their
#: keys, indented) beside its text, and one file's envelope (format, devices, shard, check).
ENTRY_BYTES = 300
FILE_BYTES = 1024
_ROOT_ENTRY_BYTES = 96  # one shard's line in the root: its prefix and etag, indented


def read_state(store: LibraryStore, name: str) -> bytes | None:
    """A ``.sync`` file's bytes; an unreadable one stops the sync (it must never look missing).

    An I/O error, a directory where the file belongs, or a link all stop the sync the same way.
    """
    try:
        return store.read_sync_state(name)
    except (OSError, UnstorableIdentifier, LinkRefused, NameCollision) as error:
        raise SyncInterrupted(f"cannot read .sync/{name} on {store.root}: {error}") from error


def state_names(store: LibraryStore, peer: str) -> tuple[str, ...]:
    """Every ``.sync`` file this library holds for the pair with ``peer``; unlistable stops the sync."""
    try:
        return store.sync_state_names(peer)
    except (OSError, UnstorableIdentifier, LinkRefused, NameCollision) as error:
        raise SyncInterrupted(f"cannot list .sync/peers/{peer} on {store.root}: {error}") from error


def root_name(kind: str, peer: str) -> str:
    return f"peers/{peer}/{kind}.json"


def shard_name(kind: str, peer: str, prefix: str) -> str:
    return f"peers/{peer}/{kind}.{prefix}.json"


def is_shard(name: str, kind: str, peer: str) -> bool:
    head = f"peers/{peer}/{kind}."
    prefix = name[len(head):-len(".json")] if name.startswith(head) and name.endswith(".json") else ""
    return 1 <= len(prefix) <= SYNC_MAX_SHARD_WIDTH and all(char in "0123456789abcdef" for char in prefix)


def shard_of(path: str, width: int) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:width]


def prefixes(width: int) -> list[str]:
    return [format(index, f"0{width}x") for index in range(16 ** width)]


def width_for(paths: Iterable[str], limit: int) -> int | None:
    """The smallest width at which every file of a document naming ``paths`` fits ``limit`` by the
    upper-bound estimate (0: one ``/1`` file); ``None`` when not even the widest does."""
    sizes = {path: ENTRY_BYTES + len(path.encode("utf-8")) for path in paths}
    if sum(sizes.values()) + FILE_BYTES <= limit:
        return 0
    for width in range(1, SYNC_MAX_SHARD_WIDTH + 1):
        if 16 ** width * _ROOT_ENTRY_BYTES + FILE_BYTES > limit:
            return None  # the root alone would not fit, and a wider root is larger still
        totals: dict[str, int] = {}
        for path, size in sizes.items():
            prefix = shard_of(path, width)
            totals[prefix] = totals.get(prefix, 0) + size
        if max(totals.values(), default=0) + FILE_BYTES <= limit:
            return width
    return None


def pair_files(store: LibraryStore, kind: str, peer: str) -> dict[str, bytes]:
    """The root (always read: a look-alike spelling or an unreadable file stops the sync) and every
    shard of ``kind`` present for the pair, by name."""
    files: dict[str, bytes] = {}
    root = read_state(store, root_name(kind, peer))
    if root is not None:
        files[root_name(kind, peer)] = root
    for name in state_names(store, peer):
        if is_shard(name, kind, peer):
            data = read_state(store, name)
            if data is not None:
                files[name] = data
    return files


def _digest(document: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(render(document)).hexdigest()


def parse(kind: str, files: Mapping[str, bytes], peer: str, devices: tuple[str, str]) -> ArchiveView | PendingView:
    """The document ``files`` hold for the pair: as 019 reads it, plus ``files`` itself."""
    cls = ArchiveView if kind == "archive" else PendingView
    files = MappingProxyType(dict(files))
    root = files.get(root_name(kind, peer))

    def ignored(reason: str) -> ArchiveView | PendingView:
        named = frozenset().union(*(named_paths(data) for data in files.values()))
        return cls("ignored", MappingProxyType({}), reason, root, named, files)  # type: ignore[arg-type]
    if root is None:
        # shards without their root: never a first sync, and never trusted (a root is written with them)
        return cls("missing", files=files) if not files else ignored("its root document is missing")
    try:
        document = parse_strict(root)
    except JsonError:
        document = None
    if not isinstance(document, Mapping) or document.get("format") != FORMATS[kind]:
        view = (parse_archive if kind == "archive" else parse_pending)(root, devices)  # /1, as-is
        return ignored(view.reason or "") if view.state == "ignored" else cls(
            view.state, view.entries, None, root, files=files)  # type: ignore[arg-type]
    entries, reason = _sharded(kind, document, files, peer, devices)
    if entries is None:
        return ignored(reason)
    return cls("valid", MappingProxyType(entries), None, root, files=files)  # type: ignore[arg-type]


def _sharded(kind: str, root: Mapping[str, object], files: Mapping[str, bytes], peer: str,
             devices: tuple[str, str]) -> tuple[dict | None, str]:  # type: ignore[type-arg]
    if set(root) != {"format", "devices", "shards", "check"}:
        return None, "unexpected fields in its root"
    if root["devices"] != tuple(devices):
        return None, "it belongs to another pair of devices"
    if root["check"] != _digest({key: value for key, value in root.items() if key != "check"}):
        return None, "its root's check digest does not match its content"
    shards = root["shards"]
    if not isinstance(shards, Mapping):
        return None, "its root does not list one complete set of shards"
    width = len(next(iter(shards), ""))
    if not 1 <= width <= SYNC_MAX_SHARD_WIDTH or sorted(shards) != prefixes(width):
        return None, "its root does not list one complete set of shards"
    entries: dict = {}  # type: ignore[type-arg]
    for prefix, tag in shards.items():
        name = shard_name(kind, peer, prefix)
        data = files.get(name)
        if not is_etag(tag) or data is None or etag(data) != tag:
            return None, f"shard {prefix} is missing or is not the one its root lists"
        try:
            shard = parse_strict(data)
        except JsonError as error:
            return None, f"shard {prefix} is not strict JSON: {error}"
        if (set(shard) != {"format", "devices", "shard", "entries", "check"} or shard["format"] != FORMATS[kind]
                or shard["devices"] != tuple(devices) or shard["shard"] != prefix
                or not isinstance(shard["entries"], Mapping)
                or shard["check"] != _digest({key: value for key, value in shard.items() if key != "check"})):
            return None, f"shard {prefix} is not a valid shard of this document"
        parsed, reason = (archive_entries if kind == "archive" else pending_entries)(shard["entries"])
        if parsed is None:
            return None, f"shard {prefix}: {reason}"
        if any(shard_of(path, width) != prefix for path in parsed):
            return None, f"shard {prefix} holds a path that belongs to another shard"
        entries.update(parsed)
    return entries, ""


def render_files(kind: str, peer: str, devices: tuple[str, str], entries: Mapping[str, object], limit: int | None,
                 width: int | None = None) -> tuple[dict[str, bytes], int]:
    """Every file of the document holding ``entries``, and its width.

    With ``width`` (a marker re-rendered after a denial only shrinks) the document keeps that width.
    Otherwise it is the smallest that fits (``width_for``); a document that fits nowhere is
    ``ArchiveTooLarge``, which a sync's plan already refuses before any write."""
    if width is None:
        width = width_for(entries, limit) if limit is not None else 0
        if width is None:
            raise ArchiveTooLarge(f"the {kind} of {len(entries)} paths does not fit {SYNC_MAX_SHARD_WIDTH}-digit shards "
                                  f"of {limit} bytes")
    if width == 0:
        render_one = render_archive if kind == "archive" else render_pending
        files = {root_name(kind, peer): render_one(devices, entries)}  # type: ignore[arg-type]
    else:
        body = (archive_body if kind == "archive" else pending_body)(entries)  # type: ignore[arg-type]
        split: dict[str, dict[str, object]] = {prefix: {} for prefix in prefixes(width)}
        for path, entry in body.items():
            split[shard_of(path, width)][path] = entry
        files, listed = {}, {}
        for prefix, part in split.items():
            shard = {"format": FORMATS[kind], "devices": list(devices), "shard": prefix, "entries": part}
            data = render({**shard, "check": _digest(shard)})
            files[shard_name(kind, peer, prefix)] = data
            listed[prefix] = etag(data)
        root = {"format": FORMATS[kind], "devices": list(devices), "shards": listed}
        files[root_name(kind, peer)] = render({**root, "check": _digest(root)})
    if limit is not None and any(len(data) > limit for data in files.values()):
        raise ArchiveTooLarge(f"a file of the {kind} would exceed {limit} bytes")
    return files, width


def changes(new: Mapping[str, bytes], old: Mapping[str, bytes], *,
            every: bool = False) -> list[tuple[str, bytes, str | None]]:
    """``(name, bytes, expected etag)`` for each file of ``new`` that differs from what is on disk
    (``every``: each file of ``new``, so a later denial can still replace any of them)."""
    return [(name, data, None if old.get(name) is None else etag(old[name]))
            for name, data in sorted(new.items()) if every or old.get(name) != data]


def stage(txn, states: list[tuple[str, bytes, str | None]]) -> dict[str, str]:  # type: ignore[no-untyped-def]
    """Stage ``states``; returns each staged key's ``.sync`` name."""
    return {txn.put_sync_state(name, data, expected): name for name, data, expected in states}


# -- dismissals (028, see ``dismiss``) ------------------------------------------------------------

def read_dismissed(store: LibraryStore, peer: str, devices: tuple[str, str]) -> DismissedView:
    return parse_dismissed(read_state(store, dismissed_name(peer)), devices)


def forget_dismissals(view: DismissedView, peer: str, devices: tuple[str, str],
                      open_paths: Iterable[str]) -> tuple[str, bytes, str | None] | None:
    """The rewrite that drops every dismissal whose path is no longer in conflict with this peer, or
    ``None``. A dismissal ends with its conflict: if the same bytes come back in a later conflict,
    they are another conflict's version and are kept again. An ignored record is left as it is
    (reported on every sync; ``dismiss`` replaces it), since its contents were never honoured."""
    if view.state != "valid":
        return None
    still = set(open_paths)
    kept = {path: tags for path, tags in view.entries.items() if path in still}
    if kept == dict(view.entries):
        return None
    return dismissed_name(peer), render_dismissed(devices, kept), None if view.data is None else etag(view.data)


def only_forgets(old: bytes | None, new: bytes, devices: tuple[str, str]) -> bool:
    """Whether ``new`` is a valid dismissal record of the pair that dismisses nothing ``old`` did not:
    the only rewrite a sync makes of it (``forget_dismissals``), and so the only one a peer may replay."""
    after, before = parse_dismissed(new, devices), parse_dismissed(old, devices)
    if after.state != "valid" or before.state != "valid":
        return False
    return all(tags <= before.entries.get(path, frozenset()) for path, tags in after.entries.items())
