"""The content-addressed image store: immutable blobs and their image objects.

Layout (spec section 2), under ``<home>/assets/image-store/``::

    blobs/<b0b1>/<byte_sha256>.<ext>     immutable encoded bytes
    objects/<p0p1>/px1-<hex>.json        image-object sidecar (mutable)

A *blob* is one encoding, named by the sha256 of its bytes. An *object* is one
picture, named by its pixel identity (`image_hash`), and its sidecar names the
one blob it retains. Two files showing the same picture are one object with
one blob: the first representation to arrive is kept, and steady state never
swaps it (spec section 5).

Every path is built from regex-validated hex, never from a caller's string,
and every write goes through `store.atomic`.

**The blob index** (``<home>/.cache/image-store/blob-index/<sha[:2]>/<sha>``,
content: the object id) is the exact-byte shortcut that lets a re-ingest of
bytes already stored skip the decode. It is derived from the sidecars and
rebuilt from them when its directory is missing, and it is *never trusted*: a
hit is used only when the object it names exists and retains exactly this
blob. One file per blob, so concurrent ingests never rewrite a shared file.

**Locks** (`store.locks`): ingest takes `image_ingest_gc_lock` and then the
object's `image_object_lock` stripe across its find-or-create, including the
sidecar mtime touch that keeps GC off an object somebody is placing right now.
`update` takes only the stripe. Both are leaves, and an `update` callback may
not call back into this module (`update`'s docstring says why).

This module must not import `assets`, `image_descriptions`, `overlay` or any
route: it sits below all of them.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from PIL import Image

from . import atomic, fetch, image_hash, image_sanitize, locks, logs, paths, statcache

MIME: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
FORMAT = 1
#: The most source URLs one object records; the oldest are kept.
MAX_SOURCES = 20
#: The longest description an image may carry: one cap for every way text
#: reaches an object -- a description write (`image_descriptions.set_in`) and a
#: bundle import (`world_bundle.MAX_IMPORTED_DESCRIPTION`) alike. Longer text is
#: not a description of a picture.
MAX_DESCRIPTION = 4000

_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_EXT_ALIASES = {"jpeg": "jpg"}
_IDENTITIES = frozenset({"pixels", "bytes"})
#: Bundle projection drops these whole (spec section 10).
_UNPROJECTED = ("sources", "description_conflicts")
_HASH_CHUNK = 1 << 20


@dataclass(frozen=True)
class ImageObject:
    #: Frozen, but `raw` is a dict, so a generated hash could only raise.
    #: Nothing hashes an object; say so rather than advertise a hash it lacks.
    __hash__ = None  # type: ignore[assignment]

    id: str
    #: "pixels" or "bytes" (opaque), as `image_hash.PixelIdentity.identity`.
    identity: str
    blob_sha256: str
    ext: str
    #: Derived from `ext`, never read back from the sidecar's own string.
    mime: str
    size: int
    width: int | None
    height: int | None
    animated: bool
    #: The whole sidecar as read. Shared with the read cache: do not mutate.
    raw: dict


def store_root(root: Path | None = None) -> Path:
    """``<home>/assets/image-store``.

    `root` is a store home pinned by the caller -- a maintenance run captures
    ``paths.home().resolve()`` when it starts and builds every path from that,
    so a data-dir move mid-run cannot point it at another tree (stage-4 M4).
    None, the default everywhere else, is the live `paths.home()`. The same
    keyword on `blob_path`, `object_path` and `read_fresh` means the same."""
    return (paths.home() if root is None else Path(root)) / "assets" / "image-store"


def _is_sha(s: object) -> bool:
    return isinstance(s, str) and _SHA_RE.match(s) is not None


def blob_path(sha: str, ext: str, *, root: Path | None = None) -> Path:
    if not _is_sha(sha):
        raise ValueError("bad blob sha")
    if ext not in MIME:
        raise ValueError("bad blob ext")
    return store_root(root) / "blobs" / sha[:2] / f"{sha}.{ext}"


def object_path(image_id: str, *, root: Path | None = None) -> Path:
    if not image_hash.is_image_id(image_id):
        raise ValueError("bad image id")
    return store_root(root) / "objects" / image_id[4:6] / f"{image_id}.json"


def iter_ids() -> Iterator[str]:
    """Every object id with a sidecar under ``objects/``, sorted.

    Decided from the directory listing alone: a name counts when it is a
    well-formed id and sits in the shard `object_path` would put it in. No
    sidecar is opened, so a garbled one is still listed (and a reader of it
    gets None, as `read` answers); a stray name, a temp or a misplaced file
    is skipped. For whole-store sweeps (`image_scopes`), which read each
    object with `read_fresh`.
    """
    objects = store_root() / "objects"
    found: list[str] = []
    try:
        shards = [d for d in objects.iterdir() if d.is_dir()]
    except OSError:
        return iter(())
    for shard in shards:
        try:
            names = [p.name for p in shard.iterdir()]
        except OSError:
            continue
        for name in names:
            stem, dot, ext = name.rpartition(".")
            if (dot and ext == "json" and image_hash.is_image_id(stem)
                    and stem[4:6] == shard.name):
                found.append(stem)
    return iter(sorted(found))


def blob_sha_of(path: Path) -> str | None:
    """The blob sha when `path` is ``store_root()/blobs/<xx>/<sha>.<ext>``.

    String work on the path -- the file itself is never statted -- so a hot
    serving or thumbnail path can ask it of every file it touches. `path` may
    be spelled through the store root as configured or `.resolve()`d (a home
    reached through a symlink): a name that is a blob's under the configured
    root is compared against the resolved root too, which costs one
    `resolve()` of the root and only on that miss. Never raises.
    """
    p = Path(path)
    sha, dot, ext = p.name.partition(".")
    if not dot or ext not in MIME or not _is_sha(sha) or p.parent.name != sha[:2]:
        return None
    blobs = store_root() / "blobs"
    if p.parent.parent == blobs:
        return sha
    try:
        if p.parent.parent == blobs.resolve():
            return sha
    except (OSError, RuntimeError):     # a root that cannot resolve names nothing
        pass
    return None


def _index_root() -> Path:
    return paths.home() / ".cache" / "image-store" / "blob-index"


def _index_path(sha: str) -> Path:
    return _index_root() / sha[:2] / sha


def _int_or_none(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _parse(raw: object, image_id: str) -> ImageObject | None:
    """A sidecar's object, or None for anything this format does not describe."""
    if not isinstance(raw, dict) or raw.get("format") != FORMAT or raw.get("id") != image_id:
        return None
    identity = raw.get("identity")
    blob = raw.get("blob")
    if identity not in _IDENTITIES or not isinstance(blob, dict):
        return None
    sha, ext = blob.get("sha256"), blob.get("ext")
    size = _int_or_none(blob.get("size"))
    animated = blob.get("animated", False)
    if not isinstance(sha, str) or not _is_sha(sha) or ext not in MIME or size is None:
        return None
    if not isinstance(animated, bool):
        return None
    return ImageObject(
        id=image_id, identity=identity, blob_sha256=sha, ext=ext, mime=MIME[ext],
        size=size, width=_int_or_none(blob.get("width")),
        height=_int_or_none(blob.get("height")), animated=animated, raw=raw)


def _load(path: Path, image_id: str) -> ImageObject | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _parse(raw, image_id)


#: `read`'s own statcache pool (the "image_objects" pool). A describe-queue
#: walk reads an object per placement; in the shared FIFO that would evict the
#: card and entity hashes the sync sweeps rely on. Sized like the shared one.
_OBJECT_POOL: dict = {}
_OBJECT_MAX = statcache.MAX_ENTRIES


def read(image_id: str) -> ImageObject | None:
    """The object, or None if it is absent, garbled or names another id.

    Memoized on the sidecar's stat signature, in this module's own pool
    (`_OBJECT_POOL`), so an unchanged sidecar is parsed once. The returned
    `raw` is shared with that cache: copy before changing.
    """
    if not image_hash.is_image_id(image_id):
        return None
    path = object_path(image_id)
    return statcache.memo("image_store.read", statcache.signature(path),
                          lambda: _load(path, image_id),
                          pool=_OBJECT_POOL, max_entries=_OBJECT_MAX)


def read_fresh(image_id: str, *, root: Path | None = None) -> ImageObject | None:
    """`read` without the cache: parsed from disk every time, remembered never.

    For a whole-store sweep (every sidecar once, rarely): memoized, it would
    cycle the object pool and leave nothing a hot path could reuse. `root`
    pins the store home (`store_root`): a maintenance run reads the tree it
    captured, never whatever the live root has become since.
    """
    if not image_hash.is_image_id(image_id):
        return None
    return _load(object_path(image_id, root=root), image_id)


def _dump(raw: dict) -> str:
    return json.dumps(raw, indent=2, sort_keys=True) + "\n"


def _write_sidecar(image_id: str, raw: dict) -> None:
    path = object_path(image_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, _dump(raw))


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


#: `blob_intact`'s own statcache pool, so serving every picture in a library
#: cannot evict the shared cache's card and entity entries.
_INTACT_POOL: dict = {}
_INTACT_MAX = 4096
#: Signatures already reported damaged, so a damaged blob asked for on every
#: render is one log row per state it is found in, not one per request.
_DAMAGE_LOGGED: dict = {}
_DAMAGE_LOGGED_MAX = 1024
_DAMAGE_LOGGED_GUARD = threading.Lock()


def _first_report(sig: tuple) -> bool:
    with _DAMAGE_LOGGED_GUARD:
        if sig in _DAMAGE_LOGGED:
            return False
        if len(_DAMAGE_LOGGED) >= _DAMAGE_LOGGED_MAX:
            _DAMAGE_LOGGED.pop(next(iter(_DAMAGE_LOGGED)))
        _DAMAGE_LOGGED[sig] = True
        return True


def _hash_matches(path: Path, sha: str, sig: tuple) -> bool:
    try:
        ok = _file_sha(path) == sha
    except OSError:
        ok = False
    if not ok and _first_report(sig):
        logs.record("warning", __name__,
                    "image blob does not match its name; not served until re-ingested",
                    kind="image_blob_damaged", blob=sha)
    return ok


def blob_intact(path: Path) -> bool:
    """Whether blob file `path` still holds the bytes its name says it does.

    A blob's name is its byte sha, and serving trusts that name: it is the
    ETag and the `?v=` token, both cached for good. Bytes that stopped matching
    it -- a truncated or damaged sync, a disk error -- would be cached under
    that name as permanently, and kept by a 304 after the repair. So exactly
    four places ask: serving an original (`routes.common`), making a new
    thumbnail (`thumbs.thumbnail`; one already made is served unasked), ingest
    (which adopts a same-id newcomer's bytes over a damaged blob, the repair)
    and bundle export (which leaves a damaged blob out).

    Re-hashes the file, memoized on its stat signature (path, mtime, size,
    inode) by `statcache.memo`, whose racy-window rule applies: a blob
    modified within the window is hashed every time, never remembered. The
    residual is the one `statcache.signature` accepts -- an in-place
    same-size rewrite with its old mtime restored.

    False for a missing file and for a path that is not a blob's (no name to
    vouch for it). A damaged state is logged once per signature. Never raises.

    Integrity is not an existence question: `image_refs.resolve_ref`,
    `assets.path_in` and everything built on them -- listings, existence
    checks, promote, delete, tombstones -- see a damaged blob's placement as
    it is, because answering "absent" there turns a serving fault into a
    change to the store.
    """
    sha = blob_sha_of(path)
    if sha is None:
        return False
    sig = statcache.signature(path)
    if sig is None:
        return False
    return statcache.memo("image_store.blob_intact", sig,
                          lambda: _hash_matches(path, sha, sig),
                          pool=_INTACT_POOL, max_entries=_INTACT_MAX)


def _publish_blob(sha: str, ext: str, data: bytes) -> None:
    """Make ``blob_path(sha, ext)`` hold `data`.

    A blob already there whose size and hash agree with its name is left
    alone. One that disagrees is corruption (a truncated sync, say) and is
    rewritten -- the only rewrite a blob ever gets -- and logged.
    """
    path = blob_path(sha, ext)
    existed = path.exists()
    if existed:
        try:
            if path.stat().st_size == len(data) and _file_sha(path) == sha:
                return
        except OSError:
            pass                        # unreadable is as wrong as mismatched
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_bytes(path, data)
    if existed:
        logs.record("warning", __name__,
                    "image blob did not match its name; rewritten from ingested bytes",
                    kind="image_blob_repaired", blob=sha)


def _blob_present(obj: ImageObject) -> bool:
    return blob_path(obj.blob_sha256, obj.ext).is_file()


def _norm_source(url: str | None) -> str | None:
    """`url` as a sidecar records it, or None for anything that is not an
    http(s) URL naming a host.

    A recorded source is kept in the sidecar and travels in bundles, so
    whatever credentials it carried (``user:pass@``) are dropped; the host is
    lower-cased (it is case-insensitive, so two spellings are one source) and
    the port kept. The fragment never reaches the server, so it goes too."""
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url.strip())
        host, port = parts.hostname, parts.port      # port: ValueError if malformed
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc += f":{port}"
    return urlunsplit(parts._replace(netloc=netloc, fragment=""))


def _sources(raw: dict) -> list[dict]:
    got = raw.get("sources")
    if not isinstance(got, list):
        return []
    return [s for s in got if isinstance(s, dict) and isinstance(s.get("url"), str)]


def _merge_source(raw: dict, url: str | None) -> bool:
    """Add `url` to ``raw["sources"]`` in place; True if `raw` changed."""
    norm = _norm_source(url)
    if norm is None:
        return False
    sources = _sources(raw)
    if any(s["url"] == norm for s in sources) or len(sources) >= MAX_SOURCES:
        return False
    raw["sources"] = [*sources, {"url": norm}]
    return True


def _blob_fields(sha: str, ext: str, size: int, pid: image_hash.PixelIdentity) -> dict:
    blob: dict = {"sha256": sha, "ext": ext, "mime": MIME[ext], "size": size,
                  "animated": pid.animated}
    if pid.width is not None and pid.height is not None:
        blob["width"], blob["height"] = pid.width, pid.height
    return blob


def _new_raw(sha: str, ext: str, size: int, pid: image_hash.PixelIdentity) -> dict:
    raw: dict = {"format": FORMAT, "id": pid.id, "identity": pid.identity,
                 "blob": _blob_fields(sha, ext, size, pid)}
    if pid.identity == "bytes":
        raw["reason"] = pid.reason
    return raw


def _index_get(sha: str) -> str | None:
    if not _index_root().is_dir():
        rebuild_index()
    return _index_lookup(sha)


def _index_lookup(sha: str) -> str | None:
    """The index's entry for `sha`, unvalidated; never writes (no rebuild)."""
    try:
        got = _index_path(sha).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return got if image_hash.is_image_id(got) else None


def _index_put(sha: str, image_id: str) -> None:
    path = _index_path(sha)
    try:
        if path.read_text(encoding="utf-8").strip() == image_id:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(path, image_id + "\n")


def rebuild_index() -> int:
    """Re-derive the blob index from every readable sidecar; returns how many
    entries it holds now. Entries naming nothing are left: lookups validate."""
    _index_root().mkdir(parents=True, exist_ok=True)
    count = 0
    for side in sorted((store_root() / "objects").glob("*/*.json")):
        obj = read(side.stem)
        if obj is None:
            continue
        _index_put(obj.blob_sha256, obj.id)
        count += 1
    return count


def _norm_ext(ext: str) -> str:
    e = ext.lstrip(".").lower()
    e = _EXT_ALIASES.get(e, e)
    if e not in MIME:
        raise ValueError("unsupported image type")
    return e


def _finish(image_id: str, raw: dict, dirty: bool, source_url: str | None) -> ImageObject:
    """Merge the source, write the sidecar if anything changed, touch it.
    Runs under the ingest locks."""
    dirty = _merge_source(raw, source_url) or dirty
    if dirty:
        _write_sidecar(image_id, raw)
    os.utime(object_path(image_id))
    obj = _parse(raw, image_id)
    if obj is None:                     # unreachable: every caller builds a valid raw
        raise RuntimeError("ingest built an image object it cannot read")
    return obj


def _ingest_hit(image_id: str, sha: str, data: bytes,
                source_url: str | None) -> ImageObject | None:
    """The exact-byte shortcut: the object `image_id` retains blob `sha`.
    None when that turns out not to hold under the lock."""
    with locks.image_ingest_gc_lock(), locks.image_object_lock(image_id):
        obj = read(image_id)
        if obj is None or obj.blob_sha256 != sha:
            return None
        _publish_blob(sha, obj.ext, data)       # restores a missing or corrupt blob
        return _finish(image_id, copy.deepcopy(obj.raw), False, source_url)


@dataclass(frozen=True)
class Prepared:
    data: bytes
    ext: str
    sha: str
    #: Why these bytes may not be named by their pixels: "unsniffable" (none
    #: of the four served formats) or "unsanitizable" (one of them, whose
    #: container the sanitizer could not parse). Either way they are kept
    #: exactly as received, metadata and all. None for sanitized bytes.
    raw_reason: str | None


def prepare(data: bytes, ext: str) -> Prepared:
    """The bytes as the store would keep them: sniffed (falling back to the
    caller's `ext`, validated) and sanitized where they sniff.

    Pure: nothing is read from or written to the store. Public for the
    migration's planner, which hashes every legacy file the way ingest would
    before anything is ingested, and groups them by `sha` (stage-4 M7)."""
    sniffed = fetch.sniff_ext(data)
    if sniffed is None:
        ext = _norm_ext(ext)
        reason: str | None = "unsniffable"
    else:
        ext = sniffed
        data, clean = image_sanitize.sanitize_checked(data)
        reason = None if clean else "unsanitizable"
    return Prepared(data, ext, hashlib.sha256(data).hexdigest(), reason)


def _opens(data: bytes) -> bool:
    """Whether Pillow recognises `data` as an image at all (header only)."""
    try:
        with Image.open(io.BytesIO(data)):
            return True
    except Exception:  # noqa: BLE001 -- anything Pillow will not open is no image
        return False


def identity_of(p: Prepared) -> image_hash.PixelIdentity:
    """The identity `p` is stored under -- ingest's, with no store consulted:
    the blob index is not asked, so this decodes (once per call; the
    migration's planner calls it once per distinct sanitized stream).

    Bytes kept as received are opaque, however well they decode: named by their
    pixels, they would share an object with a clean upload of the same picture,
    and a first arrival's blob -- whatever notes it carries, in whatever format
    -- is the one that object keeps and serves for everybody (spec section 5).
    Unsniffable bytes Pillow cannot even open keep the reason pixel identity
    gives them, "undecodable": they are not an image in any format."""
    if p.raw_reason == "unsniffable" and not _opens(p.data):
        return image_hash.opaque(p.sha, "undecodable")
    if p.raw_reason is not None:
        return image_hash.opaque(p.sha, p.raw_reason)
    return image_hash.pixel_identity(p.data, p.sha)


#: Set while an `update` callback runs on this thread (`_no_reentry`).
_IN_UPDATE = threading.local()


def _no_reentry(what: str) -> None:
    """Refuse a store call from inside an `update` callback.

    The callback holds the object's stripe. `ingest` takes the ingest/GC lock
    and then a stripe -- GC's order is that lock, then the stripe -- so an
    ingest under a stripe is the reverse order and can deadlock with GC; a
    nested `update` takes a second stripe, which may be the same
    non-reentrant one. Refused up front rather than left to deadlock."""
    if getattr(_IN_UPDATE, "active", False):
        raise RuntimeError(f"image_store.{what} called from inside an update() callback")


def identify(data: bytes, ext: str) -> str:
    """The image id ``ingest(data, ext)`` would return, with nothing written.

    For a caller that only needs to COMPARE an upload with an image it already
    holds (a collection member's guard): ingesting to learn the id would leave
    a refused upload behind as an orphan object and blob. No blob, sidecar or
    index entry is written and no mtime touched -- the blob index is consulted
    only when it exists, and a hit is validated exactly as ingest validates it,
    without restoring a missing blob.
    """
    _no_reentry("identify")
    p = prepare(data, ext)
    hit = _index_lookup(p.sha)
    if hit is not None:
        obj = read(hit)
        if obj is not None and obj.blob_sha256 == p.sha:
            return hit
    return identity_of(p).id


def ingest(data: bytes, ext: str, *, source_url: str | None = None) -> ImageObject:
    """Store `data` and return its object -- the one way in (spec section 5).

    Every caller's bytes are sanitized here, bundle import's included; there
    is no way around it. `ext` is used only when the bytes do not sniff as one
    of the four served formats; such bytes are stored verbatim under it with
    opaque identity, as is a container that sniffs but does not parse
    (`identity_of`).
    """
    _no_reentry("ingest")
    p = prepare(data, ext)
    data, ext, sha = p.data, p.ext, p.sha

    hit = _index_get(sha)
    if hit is not None:
        obj = _ingest_hit(hit, sha, data, source_url)
        if obj is not None:
            return obj

    pid = identity_of(p)
    image_id = pid.id
    with locks.image_ingest_gc_lock(), locks.image_object_lock(image_id):
        found = read(image_id)
        if found is None:
            # New: blob first, so a crash leaves an orphan blob and never a
            # sidecar naming nothing.
            _publish_blob(sha, ext, data)
            obj = _finish(image_id, _new_raw(sha, ext, len(data), pid), True, source_url)
        else:
            raw = copy.deepcopy(found.raw)
            dirty = False
            if found.blob_sha256 == sha:
                _publish_blob(sha, found.ext, data)
            elif not _blob_present(found) or not blob_intact(
                    blob_path(found.blob_sha256, found.ext)):
                # The retained blob is gone (mid-sync, partial GC), or its
                # bytes no longer match its name (a damaged sync): adopt these
                # bytes -- the same picture, by its id -- rather than "succeed"
                # into an image that cannot be served.
                _publish_blob(sha, ext, data)
                raw["blob"] = _blob_fields(sha, ext, len(data), pid)
                dirty = True
            obj = _finish(image_id, raw, dirty, source_url)
    _index_put(obj.blob_sha256, obj.id)
    return obj


def update(image_id: str, change: Callable[[dict], dict | None]) -> bool:
    """Read-modify-write one sidecar under its stripe lock; True when it wrote.

    `change` gets a private copy of the sidecar and returns the new one, or
    None to write nothing. An absent or unreadable object is left alone and
    `change` is not called. Raises ValueError on a malformed id, or when
    `change` returns something that would not read back as this object.

    False means nothing was written -- an absent object, or a callback that
    returned None -- so a caller moving text off another file onto the object
    drops the old copy only on True (`image_descriptions.set_in`).

    **The callback contract.** `change` runs under this object's stripe lock,
    so it must be a pure edit of the dict it is given. It must not call
    `ingest`, `update` or `identify` -- refused with RuntimeError (a thread
    latch) -- and must not take a campaign lock or the ingest/GC lock: GC
    takes the ingest/GC lock and THEN a stripe, so taking either under a
    stripe is the reverse order and can deadlock with it (spec section 9). Do
    any such work before calling `update`, and hand the callback its result.
    """
    _no_reentry("update")
    object_path(image_id)               # validates the id before it picks a stripe
    with locks.image_object_lock(image_id):
        obj = read(image_id)
        if obj is None:
            return False
        _IN_UPDATE.active = True
        try:
            new = change(copy.deepcopy(obj.raw))
        finally:
            _IN_UPDATE.active = False
        if new is None:
            return False
        if _parse(new, image_id) is None:
            raise ValueError("update would leave an unreadable image object")
        _write_sidecar(image_id, new)
        return True


def project(raw: dict, scope: str) -> dict:
    """The bundle projection of a sidecar: global fields plus `scope`'s own
    associations and subject reviews, without `sources` or conflicts.
    Computed at export and never stored; `raw` is not modified."""
    out = {k: copy.deepcopy(v) for k, v in raw.items() if k not in _UNPROJECTED}
    assoc = out.get("associations")
    if isinstance(assoc, list):
        out["associations"] = [a for a in assoc
                               if isinstance(a, dict) and a.get("scope") == scope]
    reviews = out.get("reviews")
    if isinstance(reviews, dict):
        subjects = reviews.get("subjects")
        if isinstance(subjects, list):
            reviews["subjects"] = [s for s in subjects if s == scope]
    return out


def _scoped_associations(got: object, scope: str) -> list[dict]:
    if not isinstance(got, list):
        return []
    return [a for a in got if isinstance(a, dict) and a.get("scope") == scope
            and all(isinstance(v, str) for v in a.values())]


def _scoped_subjects(reviews: object, scope: str) -> list[str]:
    subjects = reviews.get("subjects") if isinstance(reviews, dict) else None
    if not isinstance(subjects, list):
        return []
    return [s for s in subjects if s == scope]


def merge_projection(image_id: str, projected: dict, scope: str) -> None:
    """Fold a bundle projection (`project`) into the local object `image_id`.

    Only ever *adds*: a projected ``description`` fills the object only when it
    has none -- local text and a local ``""`` (reviewed, nothing to say) both
    win, and so does a pending ``description_conflicts`` (a disagreement the
    describe queue is waiting on the player to settle; filling would bury it)
    -- and the projection's associations and subject reviews in `scope`
    are unioned in. Everything else in `projected` (its blob, its id, any
    sources a hand-built bundle carried) is ignored: the local object's global
    fields were computed here from the blob and are not the bundle's to say.

    `scope` must already be the *final* scope (``world:<published id>``); the
    caller renames the bundle's own before calling. An absent object is left
    alone, as `update` leaves it.
    """
    if not isinstance(projected, dict):
        return
    desc = projected.get("description")
    assoc = _scoped_associations(projected.get("associations"), scope)
    subjects = _scoped_subjects(projected.get("reviews"), scope)

    def change(raw: dict) -> dict | None:
        dirty = False
        held = raw.get("description_conflicts")
        if (isinstance(desc, str) and not isinstance(raw.get("description"), str)
                and not (isinstance(held, list) and held)):
            raw["description"] = desc
            dirty = True
        if assoc:
            have = raw.get("associations")
            have = have if isinstance(have, list) else []
            for a in assoc:
                if a not in have:
                    have = [*have, a]
                    dirty = True
            raw["associations"] = have
        if subjects:
            reviews = raw.get("reviews")
            reviews = reviews if isinstance(reviews, dict) else {}
            listed = reviews.get("subjects")
            listed = listed if isinstance(listed, list) else []
            for s in subjects:
                if s not in listed:
                    listed = [*listed, s]
                    dirty = True
            raw["reviews"] = {**reviews, "subjects": listed}
        return raw if dirty else None

    update(image_id, change)
