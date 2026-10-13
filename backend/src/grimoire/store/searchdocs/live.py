"""The live set of a campaign's scene SearchDocuments, with lazy build and one
batch lookup (roadmap 08-C2a, spec section 6).

1. The live set is read from the filesystem on every call: `list_scenes`, which
   also derives `closed_by`, checked against an `os.scandir` listing that
   raises for a directory it cannot read (a `glob` may answer empty). One
   unreadable head makes `list_scenes` raise; that, or a disagreement with the
   listing, falls back to this module's own walk over the listing -- each head
   read guarded on its own (an unreadable one is `skipped`), and `closed_by`
   derived by the same rule.
2. `CampaignInputs.load`: the memoized ledger parses.
3. Each live scene's head, slices, digests and key.
4. The process's own bounded memo, keyed by the document key; then ONE batch
   lookup in 03's compiled cache of every key the memo did not hold.
5. Every miss is rendered from the slices in hand and stored, with a
   `materialized` row on every store file its slices read, its `instance`
   `{"campaign": cid, "identity": <identity>}` -- so 05's sync knows which
   documents a write to that file made stale.
6. The answer, in play order.

**Liveness by construction.** A document is only ever found through a key
computed from the current slices, so a superseded one is never returned,
whether or not any sync ran, and there is no "retire the old one" step: an
unreachable row is evicted by 03's LRU.

**Two tiers.** Every document this process built or read is held in a FIFO
keyed by the document key, bounded at `DOC_MEMO_ENTRIES`, and asked first: on
a synthetic 400-scene campaign the cache's batch lookup cost as much as all the
slicing, so a warm call asks 03 only for what the memo lacks (after a restart,
or past the bound). With the cache off (`GRIMOIRE_COMPILED_CACHE=0`, no
SQLite, a held campaign lock or the event loop, where 03 refuses) no key is
made and the memo is the only tier. Answers are identical either way.

**No lock.** A read that crosses an absorb's sequence can pair a new chronicle
slice with an old plot slice; the document is keyed by exactly those slices,
so it is self-consistent with what it read, and the next call computes the new
keys. Taking the lock would put a turn-path reader behind an absorb save.
Called from threadpool workers and detached runs, never the event loop.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from .. import compiled, paths, statcache
from ..scenes import paths as scenes_paths
from ..scenes import read as scenes_read
from . import scene as scene_mod
from . import slices as slices_mod

#: The cache-off memo's bound: at most about 48 MB of text at the worst-case
#: 5803 bytes a document, typically far less. Structural; tune later.
DOC_MEMO_ENTRIES = 8192
_MEMO: dict = {}
#: Which documents this process has written `materialized` rows for, by the
#: rows' content (document key, campaign, identity, a digest of the paths). A
#: hit is served without a store, but a re-pad or a rename keeps the key while
#: moving the scene file, so a hit whose paths this process has not recorded
#: records them -- once per process, which keeps a warm call free of writes.
_RECORDED: dict = {}

#: 03 stages at most this many rows per request (`compiled.MAX_STAGED_ROWS`):
#: a document and its `materialized` rows are stored in batches that fit it.
_STAGED_ROWS = compiled.MAX_STAGED_ROWS


@dataclass(frozen=True)
class LiveDocument:
    sid: str                         # current
    closed_by: dict | None           # list_scenes' derivation, never cached
    doc: scene_mod.SceneDocument     # key, text, meta


#: Each live document's input files, by (campaign, sid, identity, document
#: key) -- the sid too, since two identity-less scenes can share a key.
#: Kept here rather than on `LiveDocument`, which leaves the package and so
#: carries nothing that is not JSON-safe; `input_paths` is how `embedded`
#: reads them. Bounded like the memo.
_INPUTS: dict = {}


def input_paths(cid: str, live_doc: LiveDocument) -> tuple[Path, ...]:
    """The store files `live_doc`'s slices read: from this process's record
    of the call that returned it, else sliced again -- and only if that still
    gives the same document key; () when it does not or will not read."""
    identity = live_doc.doc.meta.get("identity", "")
    got = _INPUTS.get((cid, live_doc.sid, identity, live_doc.doc.key))
    if got is not None:
        return got
    try:
        sl = slices_mod.scene_slices(slices_mod.CampaignInputs.load(cid),
                                     slices_mod.read_head(cid, live_doc.sid))
    except Exception:  # noqa: BLE001 -- advisory: no paths, never an error
        return ()
    return sl.paths if scene_mod.key_of(sl) == live_doc.doc.key else ()


@dataclass(frozen=True)
class LiveSet:
    documents: tuple[LiveDocument, ...]   # play order
    skipped: tuple[str, ...]              # sids whose head could not be read
    degraded: tuple[str, ...]             # campaign inputs that would not read


def _listing(cid: str) -> list[str]:
    """The scene ids on disk, through `os.scandir`, which raises: a scenes
    directory that cannot be listed is not tolerated. `glob` and `exists`
    can read it as empty, and "this campaign has no history" is a wrong
    answer where "could not look" is the true one. Only a directory that is
    not there reads as no scenes."""
    try:
        with os.scandir(scenes_paths._scenes_dir(cid)) as listing:
            return sorted(e.name[:-3] for e in listing
                          if e.name.endswith(".md") and paths.safe_id(e.name[:-3]))
    except FileNotFoundError:
        return []


def _walk(cid: str) -> tuple[list[dict], list[str]]:
    """`list_scenes` rows (id, closed_by), tolerant of an unreadable HEAD, and
    checked against the error-reporting `_listing`: when `list_scenes` raises,
    or answers other scenes than the listing holds (its `glob` can swallow a
    directory error), this module's own walk over the listing answers
    instead."""
    try:
        rows = scenes_read.list_scenes(cid)
    except (OSError, ValueError):     # UnicodeDecodeError is a ValueError
        rows = None
    names = _listing(cid)
    if rows is not None and sorted(r["id"] for r in rows) == names:
        return rows, []
    rows, skipped = [], []
    d = scenes_paths._scenes_dir(cid)
    for stem in names:
        p = d / f"{stem}.md"
        try:
            head = slices_mod.read_head(cid, p.stem)
        except (OSError, scenes_paths.SceneNotFound, ValueError):
            skipped.append(p.stem)
            continue
        # The row `list_scenes` would have built (`scenes.read._scene_row`):
        # the raw frontmatter values, so damage elsewhere in the campaign
        # cannot change how this scene's branch group resolves.
        meta = head.meta
        rows.append({"id": p.stem, "title": meta.get("title", p.stem),
                     "done": str(meta.get("done", "")).lower() == "true",
                     **{k: str(meta[k]) for k in ("branch_group", "branch_of") if meta.get(k)},
                     "_identity": str(meta.get("identity", "") or "")})
    scenes_read.resolve_groups(rows)
    return rows, skipped


def _cache_key(sl: slices_mod.Slices) -> compiled.Key | None:
    return compiled.make_key(compiled.SEARCHDOCS_SCENE, [], params=scene_mod.key_params(sl))


def _decoded(value: object, key: str) -> scene_mod.SceneDocument | None:
    """A cached payload, if it is a whole document for exactly this key."""
    if (isinstance(value, dict) and value.get("key") == key
            and isinstance(value.get("text"), str) and isinstance(value.get("meta"), dict)):
        return scene_mod.SceneDocument(key=key, text=value["text"], meta=value["meta"])
    return None


def _payload(doc: scene_mod.SceneDocument) -> dict:
    return {"key": doc.key, "text": doc.text, "meta": doc.meta}


def _fifo(pool: dict, key: object, value: object) -> None:
    """FIFO insert under `DOC_MEMO_ENTRIES`. Races under the threadpool at
    worst evict one entry too many, which costs a re-render or a re-record."""
    while len(pool) >= DOC_MEMO_ENTRIES:
        try:
            del pool[next(iter(pool))]
        except (StopIteration, KeyError, RuntimeError):
            break
    pool[key] = value


def _remember(doc: scene_mod.SceneDocument) -> None:
    _fifo(_MEMO, doc.key, doc)


def _recorded(cid: str, sl: slices_mod.Slices, doc: scene_mod.SceneDocument) -> tuple:
    """The memo key for written rows: the paths as one digest, so the memo
    holds a fixed size per document however many group files a cast spans."""
    paths_digest = hashlib.sha256(b"\0".join(os.fsencode(p) for p in sl.paths)).hexdigest()
    return (doc.key, cid, sl.identity, paths_digest)


def row_instance(cid: str, identity: str, sid: str, path: Path, scene_path: Path | None) -> dict:
    """The `materialized` instance a document's row on `path` carries.

    On the scene's own file it names the scene, `{campaign, identity}`. On a
    shared file -- a ledger, a card, a rule file, a world record -- it names
    the campaign alone, so every scene of a campaign shares one row there:
    03 reads a path's history only up to `MAX_HISTORY_ROWS` rows and treats a
    longer one as unreadable, which one row per scene on `chronicle.json`
    would reach in a long campaign, hiding the file from every hook. 05's
    hook rebuilds all of a campaign's live documents for a campaign row, and
    finds which of them were embedded from their scene files' own rows.

    A scene from before the identity backfill has none: its own row names
    it by `sid`, which the hook honours only while that sid is still an
    identity-less live scene -- a recycled sid has an identity of its own."""
    if scene_path is not None and path == scene_path:
        return {"campaign": cid, "identity": identity} if identity else {"campaign": cid, "sid": sid}
    return {"campaign": cid}


def _store(cid: str, built: list[tuple[compiled.Key, slices_mod.Slices,
                                       scene_mod.SceneDocument, bool]]) -> None:
    """Put each built document (`put`) and write its `materialized` rows.

    03 stages at most `MAX_STAGED_ROWS` rows per request and refuses the rest
    silently, so the work is flattened into single rows -- a payload, or one
    path's row -- and flushed in batches of that size. One document with more
    input paths than a batch holds (a cast in many groups) is split across
    batches rather than cut short. A batch that cannot persist costs a miss
    next time, never an answer."""
    ops: list[tuple] = []
    owners: list[int] = []            # which of `built` each op belongs to
    for n, (key, sl, doc, put) in enumerate(built):
        if put:
            ops.append(("put", key, _payload(doc)))
            owners.append(n)
        ops += [("mark", path, row_instance(cid, sl.identity, sl.sid, path, sl.scene_path))
                for path in sl.paths]
        owners += [n] * len(sl.paths)
    failed: set[int] = set()
    for start in range(0, len(ops), _STAGED_ROWS):
        if not _flush(ops[start:start + _STAGED_ROWS]):
            failed.update(owners[start:start + _STAGED_ROWS])
    # Only what committed is remembered as written: a refused or failed write
    # is retried by the next call that finds the document, not forgotten.
    for n, (_key, sl, doc, _put) in enumerate(built):
        if n not in failed:
            _fifo(_RECORDED, _recorded(cid, sl, doc), True)


def _flush(ops: list[tuple]) -> bool:
    """Stage `ops` in one request; whether 03 committed them."""
    with compiled.batch() as request:
        for op, target, value in ops:
            if op == "put":
                request.put(target, value)
            else:
                request.mark_materialized(target, scene_mod.KIND, instance=value)
    return request.committed


def _wanted(cid: str, rows: list[dict], inputs: slices_mod.CampaignInputs,
            exclude: frozenset[str], only: frozenset[str] | None,
            skipped: list[str]) -> list[tuple[dict, slices_mod.Slices]]:
    """Each live scene asked for, in play order, with its slices; a head that
    will not read is added to `skipped`."""
    out = []
    for row in sorted(rows, key=lambda r: r["id"]):
        sid = row["id"]
        if sid in exclude or (only is not None and sid not in only):
            continue
        sl = _sliced(cid, sid, inputs)
        if sl is None:
            skipped.append(sid)
            continue
        out.append((row, sl))
    return out


def _sliced(cid: str, sid: str, inputs: slices_mod.CampaignInputs,
            *, tries: int = 2) -> slices_mod.Slices | None:
    """The scene's slices, or None for a head that will not read or a file
    that keeps moving. The file's signature is taken with the head and again
    after slicing, and the two must agree: a sid is reusable, and a delete and
    re-create between them would pair one scene's head (its identity, its
    `done`) with what slicing read of the other -- its transcript's rolling
    summary, or the old scene's ledger rows under the new one's name. A file
    that moved is sliced once more; one that moves again is skipped, as a
    head that will not read is."""
    for _ in range(tries):
        try:
            head = slices_mod.read_head(cid, sid)
        except (OSError, scenes_paths.SceneNotFound, ValueError):
            return None
        sl = slices_mod.scene_slices(inputs, head)
        if head.sig is not None and statcache.signature(head.path) == head.sig:
            return sl
    return None


def _documents(cid: str, wanted: list[tuple[dict, slices_mod.Slices]]
               ) -> tuple[list[scene_mod.SceneDocument], list[tuple], set[str]]:
    """Each scene's document from the memo, else the cache (one batch lookup
    of what the memo lacked), else rendered; and what to store: each rendered
    document with a cache key, and each hit whose `materialized` rows this
    process has not written."""
    doc_keys = [scene_mod.key_of(sl) for _row, sl in wanted]
    held = {k: d for k in doc_keys
            if isinstance(d := _MEMO.get(k), scene_mod.SceneDocument)}
    keys = [_cache_key(sl) for _row, sl in wanted]
    asked = [k for k, doc_key in zip(keys, doc_keys, strict=True)
             if k is not None and doc_key not in held]
    found: dict = {}
    if asked:
        with compiled.batch() as request:
            found = request.lookup_many(asked).values
    docs, built = [], []
    rendered: set[str] = set()
    for (_row, sl), key, doc_key in zip(wanted, keys, doc_keys, strict=True):
        doc = held.get(doc_key)
        if doc is None and key is not None:
            hit = found.get(key)
            doc = _decoded(hit.value, doc_key) if hit is not None and hit.hit else None
        if doc is None:
            doc = scene_mod.render(sl)
            rendered.add(doc.key)
            if key is not None:
                built.append((key, sl, doc, True))
        elif key is not None and _recorded(cid, sl, doc) not in _RECORDED:
            # A memo hit was never confirmed in the cache (its first store may
            # have failed), so it is put again with its rows; a cache hit is.
            built.append((key, sl, doc, doc_key in held))
        if doc_key not in held:
            _remember(doc)
        docs.append(doc)
    return docs, built, rendered


def scene_documents(cid: str, *, exclude: frozenset[str] = frozenset(),
                    only: frozenset[str] | None = None) -> LiveSet:
    """The campaign's live scene documents, in play order. `exclude` drops
    sids (09 excludes the scene being played); `only` restricts (05's hook).
    Never raises for damage to a scene or a ledger; raises `CampaignNotFound`,
    and `OSError` when the scenes directory itself cannot be listed."""
    return build(cid, exclude=exclude, only=only)[0]


def build(cid: str, *, exclude: frozenset[str] = frozenset(),
          only: frozenset[str] | None = None,
          store: bool = True) -> tuple[LiveSet, frozenset[str]]:
    """`scene_documents`, and the keys of the documents rendered by this
    call (neither memo nor cache held them) -- what 05's hook reports as
    `built` rather than `hit`. `store=False` stores nothing in 03's cache
    and writes no row (a dry run)."""
    rows, skipped = _walk(cid)
    inputs = slices_mod.CampaignInputs.load(cid)
    wanted = _wanted(cid, rows, inputs, exclude, only, skipped)
    docs, built, rendered = _documents(cid, wanted)
    if built and store:
        _store(cid, built)
    out = []
    for (row, sl), doc in zip(wanted, docs, strict=True):
        _fifo(_INPUTS, (cid, row["id"], sl.identity, doc.key), sl.paths)
        out.append(LiveDocument(sid=row["id"], doc=doc,
                                closed_by=dict(row["closed_by"]) if row.get("closed_by") else None))
    return LiveSet(documents=tuple(out), skipped=tuple(sorted(set(skipped))),
                   degraded=inputs.degraded), frozenset(rendered)


def scene_file(cid: str, live_doc: LiveDocument) -> Path:
    """The scene's own file: where its identity-level rows sit."""
    return scenes_paths._scene_path(cid, live_doc.sid)


def identities(cid: str) -> dict[str, str]:
    """Each live scene's identity, to its current sid: how 05's hook turns
    the `instance` on a `materialized` row back into a scene. A scene whose
    head will not read, or that carries no identity, is absent -- as is a
    deleted one, so a row it left names nobody. Raises as `_listing` does."""
    out: dict[str, str] = {}
    for sid in _listing(cid):
        try:
            head = slices_mod.read_head(cid, sid)
        except (OSError, scenes_paths.SceneNotFound, ValueError):
            continue
        if head.identity:
            out.setdefault(head.identity, sid)
    return out
