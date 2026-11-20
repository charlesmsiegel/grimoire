"""A rebuildable, validated in-process navigation basis for large-library browsing (065).

Before this, every ``chats.workspace`` page, search keystroke and source-chooser page parsed the whole
file-backed library (``store.load()``), rebuilt the navigation index and composed every mentioned source:
bounded replies over unbounded work per request. The basis below is everything those requests derive from one
loaded snapshot -- the index, source names, validated composition paths, the response fingerprint and one
listing row per chat -- built once and reused while the library is unchanged.

**Validation, not trust.** A basis is reused only when the library's *stamp* is unchanged. The stamp is the
store's in-process commit ``generation`` plus a metadata walk of the store tree (each entry's relative path,
type, size, ``mtime_ns``, ``ctime_ns`` and inode; links are recorded and never followed). Nothing is read or
parsed, so the walk costs a small fraction of a load. Any in-process commit or recovery bumps ``generation``;
a write from outside this process (another app, a sync folder, a hand edit) changes some entry's metadata,
since rewriting a file updates its ``ctime`` even when a tool preserves ``mtime``. A pending transaction
journal is a new entry, so the stamp changes and the next load refuses exactly as before. On any miss the
basis is rebuilt from ``store.load()``; deleting it is therefore the same as rebuilding, and the reused basis
equals the rebuilt one (``NavigationIndex.to_json()`` and the fingerprint included). The stamp is taken
*before* the load, so a write racing the load can only make the cached basis newer than its stamp, which the
next request's stamp then rejects; it can never make a stale basis look current.

**Limit.** A rewrite that keeps a file's size, ``mtime_ns``, ``ctime_ns`` and inode is not detected. That needs
a filesystem whose timestamps do not move on write, which also defeats sync tools. The cache is per process
and never persisted: the first request after a start pays one full load.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from grimoire.core.identity import SourceRef
from grimoire.core.index import MissingDefinition
from grimoire.core.portrayal import PortrayalError
from grimoire.effective.inspect import compose_in

Stamp = tuple[Any, ...]


def library_stamp(root: os.PathLike[str] | str, generation: int | None) -> Stamp:
    """``generation`` and the metadata of every entry under ``root`` (and of ``root`` itself), in path order."""
    base = os.fspath(root)
    try:
        top = os.lstat(base)
    except OSError as error:
        return (generation, ('<root>', type(error).__name__))
    found: list[tuple[Any, ...]] = [('', top.st_mode, top.st_size, top.st_mtime_ns, top.st_ctime_ns, top.st_ino)]
    stack = [base] if stat.S_ISDIR(top.st_mode) else []
    while stack:
        folder = stack.pop()
        try:
            listing = list(os.scandir(folder))
        except OSError as error:  # an unreadable folder is part of the state, not a reason to trust the cache
            found.append((os.path.relpath(folder, base), 'unreadable', type(error).__name__))
            continue
        for item in listing:
            try:
                info = item.stat(follow_symlinks=False)
            except OSError as error:
                found.append((os.path.relpath(item.path, base), 'vanished', type(error).__name__))
                continue
            found.append((os.path.relpath(item.path, base), info.st_mode, info.st_size, info.st_mtime_ns,
                          info.st_ctime_ns, info.st_ino))
            if stat.S_ISDIR(info.st_mode):
                stack.append(item.path)
    found.sort(key=lambda entry: entry[0])
    return (generation, tuple(found))


@dataclass(frozen=True)
class Basis:
    """What every workspace and listing request derives from one loaded snapshot."""
    view: Any
    index: Any
    order: tuple[Any, ...]          # every chat ref, newest activity first (the index order)
    names: dict[str, str | None]    # unpinned source ref -> display name (presentation only)
    sources: tuple[dict, ...]       # each active character/scenario mention, with its validated path or problem
    diagnostics: tuple[dict, ...]
    rows: dict[str, dict]           # chat ref -> its listing row, as the ``chats`` op lists it
    text: dict[str, str]            # chat ref -> its row as case-folded JSON, the full-library search text
    fingerprint: str


def build_basis(api: Any, view: Any) -> Basis:
    """Compose everything once from ``view`` (``api`` supplies the listing helpers ``TextApi`` already has)."""
    index = api.chats.navigation(view=view)
    order = tuple(index.all_chats())
    entries = [index.entry(ref) for ref in order]
    names = api._mention_names(view, entries)
    candidates = sorted({SourceRef(*key, revision=rev) for entry in entries
                         for role, key, rev, active in entry.mentions
                         if active and role in ('character', 'scenario')}, key=str)
    diagnostics, facts, sources = [], [], []
    for ref in candidates:
        path, problem = [], None
        try:
            effective = compose_in(view, ref)
            path = [str(node.ref) for node in effective.chain]
            facts.append([(str(node.ref), node.etag) for node in effective.chain])
        except (MissingDefinition, PortrayalError, ValueError) as error:
            problem = getattr(error, 'reason', type(error).__name__)
            diagnostics.append({'ref': str(ref), 'code': str(problem)})
            facts.append((str(ref), str(problem)))
        sources.append({'ref': str(ref), 'role': ref.kind, 'name': names.get(str(ref.unpinned())),
                        'path': path, 'problem': problem})
    fingerprint = hashlib.sha256(json.dumps([index.to_json(), names, facts,
        sorted((key, value.etag) for key, value in view.sources.items()),
        [(str(d.ref), d.name, getattr(d, "etag", None)) for d in view.index.definitions()],
        [(p.path, p.code, p.detail) for p in view.problems]], sort_keys=True).encode()).hexdigest()
    listed = api._entries(view, index, order, names)
    rows = {str(ref): row for ref, row in zip(order, listed)}
    text = {key: json.dumps(row, ensure_ascii=False).casefold() for key, row in rows.items()}
    return Basis(view, index, order, names, tuple(sources), tuple(diagnostics), rows, text, fingerprint)


class NavigationCache:
    """One validated :class:`Basis` per process. Thread-safe; a miss rebuilds while holding the lock, so
    concurrent first pages share one load instead of each paying for it."""

    def __init__(self, store: Any, *, stamp: Callable[[Any, int | None], Stamp] = library_stamp) -> None:
        self._store = store
        self._stamp = stamp
        self._lock = threading.Lock()
        self._held: tuple[Stamp, Basis] | None = None
        self.builds = 0  # how many times a basis was rebuilt from a full load (diagnostics and tests)

    def basis(self, api: Any) -> Basis:
        with self._lock:
            now = self._stamp(self._store.root, getattr(self._store, "generation", None))
            if self._held is not None and self._held[0] == now:
                return self._held[1]
            self._held = None
            built = build_basis(api, self._store.load())  # a refusal (pending recovery, closed store) propagates
            self.builds += 1
            self._held = (now, built)
            return built

    def clear(self) -> None:
        with self._lock:
            self._held = None


__all__ = ['Basis', 'NavigationCache', 'build_basis', 'library_stamp']
