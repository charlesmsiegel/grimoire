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
`update` takes only the stripe. Both are leaves.

This module must not import `assets`, `image_descriptions`, `overlay` or any
route: it sits below all of them.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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

_SHA_RE = re.compile(r"[0-9a-f]{64}\Z")
_EXT_ALIASES = {"jpeg": "jpg"}
_IDENTITIES = frozenset({"pixels", "bytes"})
#: Bundle projection drops these whole (spec section 10).
_UNPROJECTED = ("sources", "description_conflicts")
_HASH_CHUNK = 1 << 20


@dataclass(frozen=True)
class ImageObject:
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


def store_root() -> Path:
    return paths.home() / "assets" / "image-store"


def _is_sha(s: object) -> bool:
    return isinstance(s, str) and _SHA_RE.match(s) is not None


def blob_path(sha: str, ext: str) -> Path:
    if not _is_sha(sha):
        raise ValueError("bad blob sha")
    if ext not in MIME:
        raise ValueError("bad blob ext")
    return store_root() / "blobs" / sha[:2] / f"{sha}.{ext}"


def object_path(image_id: str) -> Path:
    if not image_hash.is_image_id(image_id):
        raise ValueError("bad image id")
    return store_root() / "objects" / image_id[4:6] / f"{image_id}.json"


def blob_sha_of(path: Path) -> str | None:
    """The blob sha when `path` is ``store_root()/blobs/<xx>/<sha>.<ext>``.

    Pure string work on the path -- nothing is statted -- so a hot serving or
    thumbnail path can ask it of every file it touches.
    """
    p = Path(path)
    sha, dot, ext = p.name.partition(".")
    if not dot or ext not in MIME or not _is_sha(sha) or p.parent.name != sha[:2]:
        return None
    if p.parent.parent != store_root() / "blobs":
        return None
    return sha


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


def read(image_id: str) -> ImageObject | None:
    """The object, or None if it is absent, garbled or names another id.

    Memoized on the sidecar's stat signature, so an unchanged sidecar is parsed
    once. The returned `raw` is shared with that cache: copy before changing.
    """
    if not image_hash.is_image_id(image_id):
        return None
    path = object_path(image_id)
    return statcache.memo("image_store.read", statcache.signature(path),
                          lambda: _load(path, image_id))


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
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit(parts._replace(fragment=""))


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


def ingest(data: bytes, ext: str, *, source_url: str | None = None,
           sanitize: bool = True) -> ImageObject:
    """Store `data` and return its object -- the one way in (spec section 5).

    `ext` is used only when the bytes do not sniff as an image; such bytes are
    stored verbatim under it with opaque identity. ``sanitize=False`` is for
    bundle import, whose blobs are already stored bytes.
    """
    sniffed = fetch.sniff_ext(data)
    if sniffed is None:
        ext = _norm_ext(ext)
    else:
        ext = sniffed
        if sanitize:
            data = image_sanitize.sanitize(data)
    sha = hashlib.sha256(data).hexdigest()

    hit = _index_get(sha)
    if hit is not None:
        obj = _ingest_hit(hit, sha, data, source_url)
        if obj is not None:
            return obj

    pid = image_hash.pixel_identity(data, sha)
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
            elif not _blob_present(found):
                # The retained blob is gone (mid-sync, partial GC): adopt these
                # bytes rather than "succeed" into an unresolvable image.
                _publish_blob(sha, ext, data)
                raw["blob"] = _blob_fields(sha, ext, len(data), pid)
                dirty = True
            obj = _finish(image_id, raw, dirty, source_url)
    _index_put(obj.blob_sha256, obj.id)
    return obj


def update(image_id: str, change: Callable[[dict], dict | None]) -> None:
    """Read-modify-write one sidecar under its stripe lock.

    `change` gets a private copy of the sidecar and returns the new one, or
    None to write nothing. An absent or unreadable object is left alone and
    `change` is not called. Raises ValueError on a malformed id, or when
    `change` returns something that would not read back as this object.
    """
    object_path(image_id)               # validates the id before it picks a stripe
    with locks.image_object_lock(image_id):
        obj = read(image_id)
        if obj is None:
            return
        new = change(copy.deepcopy(obj.raw))
        if new is None:
            return
        if _parse(new, image_id) is None:
            raise ValueError("update would leave an unreadable image object")
        _write_sidecar(image_id, new)


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
