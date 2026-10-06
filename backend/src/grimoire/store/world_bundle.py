"""World bundles: a world directory zipped whole, importable as a new world (#54).

The store layout *is* the exchange format. A world directory is already
self-contained -- ``world.md``, the entity kind-folders, ``characters/`` and
``pcs/`` with their per-version assets, ``greetings/``, ``sheets/``,
``plotmap.json``, ``tags.md``, ``calendar.json`` -- so the export walks it and
the import puts it back. Nothing enumerates the kinds, which is the point: a
kind added next month rides along without touching this file.

A ``grimoire-bundle.json`` manifest sits at the archive root beside the
``world/`` prefix, recording the format version, the source world id, its name
and the exporting grimoire's version. It buys two things a bare directory zip
cannot: an import can refuse a bundle from a future grimoire with an honest
message instead of half-extracting one, and it carries the **source world id**,
which the import needs.

The ``world/`` prefix is a deliberate departure from #54's "paths relative to
the world root", and the manifest is what forces it: with world files at the
archive root there is no way to tell bundle metadata from world content except
by knowing every filename grimoire will ever use, and the whole point of
zipping the directory is that no such list exists. One prefix keeps the two
apart forever. Anything outside it is refused rather than guessed at.

That id is the one thing that does not travel: ``store/localize.py`` writes
absolute serving URLs into card and greeting text --
``/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}`` and
``/api/worlds/{wid}/greetings/{gid}/images/{name}`` -- and a PC persona can
carry the matching ``/pcs/{pid}/versions/{vid}/images/{name}`` by hand, so a
world landing under a new id would render every localized image as a 404. Import rewrites that
prefix across the text records, and only the text records: an asset's bytes are
copied verbatim.

An import always creates a **new** world and never merges into an existing one.
Campaigns bind to a world by id, and merging would silently change what an
existing campaign inherits and corrupt its sync bases.

Safety, shared with module-pack import via ``store.ziputil``: every member is
checked -- traversal, absolute/UNC/drive names, symlinks, case collisions,
member count and expanded size -- *before* anything is written, and the tree is
built in a private staging directory that is published with a single rename or
discarded whole. A rejected import leaves no partial world in the library.

**Format 2 carries the world's images** (spec section 10). A placement
(``image-refs/<name>.json``) names an object in the *global* image store, so
the world directory alone holds an id and no picture: a format-1 export
imported into another library would leave every placement unresolved (Codex
review). So the export adds an ``image-store/`` prefix beside ``world/``, with
one ``blobs/<xx>/<sha>.<ext>`` and one ``objects/<xx>/<id>.json`` per image any
placement in the world names. The object is a **projection** (`image_store.
project`): global fields plus this world's own associations and reviews, never
``sources``.

Nothing a bundle says about an image is trusted. Every blob is re-hashed
against its name *as received*, then ingested exactly as an upload is --
sanitized, which is idempotent on bytes a store already sanitized, so a
grimoire export re-imports unchanged, while a hand-built blob carrying text
chunks or trailing bytes cannot become the blob a later local upload of the
same pixels dedupes onto. Every object's id is recomputed *here*, and a
staged ref naming a bundle id that differs from the local one is rewritten
before the world is published: a bundle can never claim an id for pixels it
does not contain. Nor can it place one: a staged ref naming an id the bundle
carries no picture for loses its image, and a staged promotion journal is
removed (`_contain_refs`). Descriptions, associations and reviews are merged only after
`staging.publish` has named the world, under ``world:<final id>``, and only
ever fill what the local object lacks (`image_store.merge_projection`). A
failure there is logged rather than raised: the world already exists, and
answering "failed" would invite a retry that imports a second copy.

Blobs and objects are ingested before the world is published, so an import
that fails afterwards leaves an orphan picture behind -- a GC candidate, never
corruption (spec section 12). Format-1 bundles import exactly as before.

**Format 3 carries format-2 image collections.** A format-2 manifest names
image ids rather than library names, so the export packs those pictures as it
packs placed ones, and the import holds the manifest to the bundle's contents
as it holds a placement (`_contain_manifests`): each member is rewritten to its
local id, one the bundle carries no picture for is dropped, and a manifest left
with no members is removed. A bundle is written as format 3 only when it packs
a format-2 manifest, so a world without one stays importable by an older
grimoire; a bundle of format 1 or 2 carries format-1 manifests only, and is
still refused for any other. An export validates, with the store's own rule,
every manifest the import would refuse -- any that parses as a JSON object and
does not say format 1 -- so it never writes one its import refuses.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import (
    assets,
    atomic,
    fetch,
    image_collections,
    image_refs,
    image_scopes,
    image_store,
    logs,
    ziputil,
)
from .frontmatter import parse_frontmatter
from .paths import ensure_home, now_iso, safe_id, slugify, uniquify
from .worlds import paths as worlds_paths
from .worlds import staging as worlds_staging

#: The format written for a world that packs no format-2 collection manifest,
#: so an older grimoire can still import it.
FORMAT = 2
#: The format written when the export packs a format-2 collection manifest.
COLLECTIONS_FORMAT = 3
#: The highest format this grimoire reads.
MAX_FORMAT = 3
#: Every format this grimoire reads. Format 1 is format 2 without images, and
#: format 2 is format 3 without format-2 collection manifests.
_READABLE = frozenset({1, 2, 3})
MANIFEST_NAME = "grimoire-bundle.json"
WORLD_PREFIX = "world"
STORE_PREFIX = "image-store"

# The only shapes an `image-store/` member may take. Hex is lower-case, and the
# shard must equal the hash's own prefix (checked separately: a regex cannot
# say "these two groups agree" without a backreference that reads worse).
_BLOB_MEMBER = re.compile(
    r"image-store/blobs/([0-9a-f]{2})/([0-9a-f]{64})\.(png|jpg|gif|webp)")
_OBJECT_MEMBER = re.compile(
    r"image-store/objects/([0-9a-f]{2})/(px1-([0-9a-f]{64}))\.json")
# The largest image blob a bundle carries, held on BOTH sides: export refuses
# a world holding a bigger one before it writes anything (`_check_blob_sizes`),
# and import refuses a bigger member -- so an export never produces a bundle
# its own import refuses. It is the fetch cap, the largest image the app takes
# from anywhere else; record uploads themselves are not capped, which is why
# export has to ask.
MAX_BUNDLE_BLOB_BYTES = fetch.MAX_BYTES
# A projected sidecar is a few hundred bytes. This caps the *sum* over every
# object member, checked from the headers before any is read: a per-object cap
# alone let a small archive of many objects naming one tiny blob expand into
# gigabytes of parsed metadata (review).
MAX_OBJECT_BYTES = 64 * 1024 * 1024
# ...and each object on its own, refused from its header before it is read:
# the total cap alone still let one 64 MiB object be parsed whole.
MAX_OBJECT_MEMBER_BYTES = 1024 * 1024
# A collection manifest holds at most 10 000 member names or ids, well under a
# megabyte; this is room for that and a refusal for anything shaped otherwise.
MAX_COLLECTION_MANIFEST_BYTES = 16 * 1024 * 1024
#: The collection manifest formats each bundle format may carry. Before format
#: 3 a bundle carried format-1 manifests only, and still may not carry another.
_COLLECTION_FORMATS = {1: frozenset({1}), 2: frozenset({1}), 3: frozenset({1, 2})}
#: Where a collection manifest sits, relative to the world root, as a
#: case-insensitive filesystem compares it (`_fold`).
_COLLECTIONS_DIR = ("assets", "image-collections")
# The longest bundle description merged. Longer is not a description of a
# picture; it is dropped (and logged) and the image imports undescribed. The
# store's one cap, so an import cannot carry text a description write refuses.
MAX_IMPORTED_DESCRIPTION = image_store.MAX_DESCRIPTION

# Sized for a real library rather than a module pack: worlds here run to
# thousands of files and a gigabyte of character art, so these are a guard
# against a hostile archive filling the disk, not a policy on world size.
MAX_MEMBERS = 100_000
MAX_UNCOMPRESSED = 8 * 1024 * 1024 * 1024
# The largest bundle the import route takes. Here rather than in the route so
# that export can hold its own output to it (`_check_written`).
MAX_BUNDLE_BYTES = 4 * 1024 * 1024 * 1024

# What is worth deflating on the way out: anything textual. Getting this wrong
# costs CPU, nothing else. Deliberately NOT the same set as the one the import
# may rewrite (`worlds.staging._REWRITABLE`) -- Codex review found the two
# conflated, and an `.svg` portrait rewritten as a result.
_COMPRESSIBLE = frozenset({".md", ".json", ".txt", ".csv", ".css", ".html",
                           ".svg", ".yaml", ".yml"})


class BundleError(Exception):
    """A bundle that cannot be read, or is not one."""


class BundleConflict(BundleError):
    """A readable bundle that could not be published -- a lost id race, not a
    bad file. Separated so the route can answer 409 rather than blaming the
    upload with a 400 (Codex review).

    The bundle-shaped face of `worlds.staging.WorldIdConflictError`, which fork
    raises too: this stays a `BundleError` so one `except` in the route still
    covers everything an import can refuse for."""


def app_version() -> str:
    """The running grimoire's version, for the manifest. Purely informational:
    compatibility is decided by ``format``, and this is what someone reads when
    a bundle behaves oddly. Best-effort -- an uninstalled source checkout (or a
    packaging layout without metadata, which the Android build may be) has no
    distribution to ask, and that must not fail an export."""
    try:
        return importlib.metadata.version("grimoire")
    except Exception:  # noqa: BLE001 -- any metadata problem is "unknown", never a failed export
        return "unknown"


def bundle_filename(wid: str) -> str:
    return f"{wid}-world.zip"


# ---- export ----

def write_bundle(wid: str, dest: Path) -> None:
    """Zip the world at `wid` into `dest`. Raises ``WorldNotFound``.

    Written to a path rather than returned as bytes: a world with a full
    character gallery runs past a gigabyte, and holding that in memory to hand
    to a response is not something a phone-sized process survives.

    A best-effort snapshot, not a locked one -- a world edited during the walk
    can be packed half-old and half-new. Files that vanish mid-walk are skipped
    rather than failing the export: by then they are genuinely not part of the
    world any more.

    A promotion journal left by a crash is finished before the walk
    (`assets.recover_promotions_in`), so the export packs the post-swap slots
    and every picture they name.

    ``store.atomic``'s in-flight temps are skipped (`atomic.is_write_temp`):
    they are not part of the world, and the writer that owns one will rename or
    unlink it out from under the walk.

    Symlinks are skipped. ``is_file()`` follows them, so a link inside the world
    would otherwise be packed as a *copy of whatever it points at* -- and the
    bundle is a file the user hands to somebody else, which makes that an
    exfiltration path out of a directory the user may not have written
    themselves (Codex review). Import already refuses symlink members, so
    nothing that round-trips through here can contain one either way.
    """
    # Canonical once, here: the manifest's `world_id` is what import reads the
    # bundle's tag scope as (`world:<world_id>`), and the projection writes
    # tags under the canonical scope (R10). Asked for as `REALM` with the
    # directory spelled `realm`, the two used to disagree, and import dropped
    # every tag (Codex review, P2b).
    wid = worlds_paths.canonical_id(wid)
    root = worlds_paths.world_root(wid)                     # rejects an unsafe id
    meta_path = worlds_paths.world_meta_path(wid)
    if not meta_path.exists():
        raise worlds_paths.WorldNotFound(wid)
    meta, _body = parse_frontmatter(meta_path.read_text(encoding="utf-8"))
    # A crashed promotion is finished first: mid-swap, the picture it moved
    # out of the avatar slot is named only by its journal, which is not packed.
    assets.recover_promotions_in(root)
    world = _world_entries(root)
    collections = _packed_manifests(root, world)
    _check_blob_sizes(root, collections)
    member_ids = _manifest_ids(collections)
    manifest = {"format": COLLECTIONS_FORMAT if member_ids else FORMAT, "kind": "world",
                "world_id": wid, "name": meta.get("name", wid),
                "app_version": app_version(), "exported": now_iso()}
    entries = [_Entry(MANIFEST_NAME, compress=zipfile.ZIP_DEFLATED,
                      data=(json.dumps(manifest, indent=2) + "\n").encode("utf-8")),
               *world, *_image_entries(root, wid, member_ids)]
    _check_whole(entries)
    try:
        with zipfile.ZipFile(dest, "w") as z:
            for e in entries:
                if e.data is not None or e.src is None:
                    z.writestr(e.arc, e.data or b"", compress_type=e.compress)
                    continue
                try:
                    z.write(e.src, e.arc, compress_type=e.compress)
                except FileNotFoundError:
                    continue                    # deleted or collected mid-walk
        _check_written(dest)
    except BaseException:
        # `dest` never keeps a bundle the import would refuse, nor half of one.
        dest.unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class _Entry:
    """One member `write_bundle` will write: a file copied from `src`, or
    `data` written as is."""
    arc: str
    compress: int = zipfile.ZIP_STORED
    src: Path | None = None
    data: bytes | None = None
    size: int = 0

    def __post_init__(self) -> None:
        if self.data is not None:
            object.__setattr__(self, "size", len(self.data))


def _world_entries(root: Path) -> list[_Entry]:
    """Every file of the world tree the bundle carries, with its size now.

    ``store.atomic``'s in-flight temps, promotion journals and symlinks are
    left out (see `write_bundle`), and a file that vanishes mid-walk is not
    part of the world any more."""
    out = []
    for p in sorted(root.rglob("*")):
        try:
            if (atomic.is_write_temp(p) or image_refs.is_transient(p)
                    or p.is_symlink() or not p.is_file()):
                continue
            size = p.stat().st_size
        except OSError:
            continue                                        # vanished mid-walk
        compress = (zipfile.ZIP_DEFLATED if p.suffix.lower() in _COMPRESSIBLE
                    else zipfile.ZIP_STORED)
        out.append(_Entry(f"{WORLD_PREFIX}/{p.relative_to(root).as_posix()}",
                          compress, src=p, size=size))
    return out


def _is_manifest(rel: list[str]) -> bool:
    """Is the world-relative path `rel` a collection manifest, as a
    case-insensitive filesystem would open it? Both sides of a bundle ask this
    one question, so export validates exactly what import checks and contains."""
    return (len(rel) == 3 and tuple(_fold(p) for p in rel[:2]) == _COLLECTIONS_DIR
            and _fold(rel[2]).endswith(".json"))


def _strict_manifest(data: bytes) -> dict:
    """`data` as the store reads a manifest: strict UTF-8, then the store's own
    rule. Raises `ValueError` (a `CollectionInvalidError`, a JSON error or a
    `UnicodeDecodeError`) or `RecursionError` for one that does not read."""
    return image_collections.validate(json.loads(data.decode("utf-8")))


def _packed_manifests(root: Path, world: list[_Entry]) -> list[tuple[str, dict]]:
    """Every format-2 collection manifest among the packed world files,
    validated by the store's own rule, as `(path relative to root, manifest)`.

    Validated exactly where the import would refuse (`_check_collections`): a
    file that does not parse as a JSON object, or that says format 1, is
    packed unchecked, as the import takes it unchecked. Anything else must
    read, or `BundleError` names it before anything is written -- so an export
    never packs a manifest its own import refuses. A manifest that vanished
    since the walk is not part of the world any more."""
    out = []
    for e in world:
        rel = e.arc.split("/")[1:]
        if e.src is None or not _is_manifest(rel):
            continue
        where = "/".join(rel)
        try:
            data = e.src.read_bytes()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BundleError(f"unreadable collection manifest: {where}: {exc}") from exc
        if not _checked_by_import(data):
            continue
        try:
            manifest = _strict_manifest(data)
        except (ValueError, RecursionError) as exc:
            raise BundleError(f"invalid collection manifest: {where}: {exc}") from exc
        out.append((where, manifest))
    return out


def _checked_by_import(data: bytes) -> bool:
    """Does `_check_collections` hold this manifest to a rule? Not when it is no
    JSON object (parsed as the import parses it) or says format 1."""
    try:
        raw = json.loads(data)
    except (ValueError, RecursionError):
        return False
    if not isinstance(raw, dict):
        return False
    fmt = raw.get("format")
    return not (type(fmt) is int and fmt == 1)


def _manifest_ids(collections: list[tuple[str, dict]]) -> set[str]:
    """The image ids the format-2 manifests among `collections` name."""
    return {m for _where, c in collections if c["format"] == 2 for m in c["members"]}


def _check_whole(entries: list[_Entry]) -> None:
    """`BundleError` for a bundle the import's whole-archive caps would refuse
    (`ziputil.scan`'s member count and expanded size, and the object metadata
    caps), asked of the planned members before anything is written."""
    if len(entries) > MAX_MEMBERS:
        raise BundleError(f"world too large to bundle: {len(entries)} files "
                          f"(a bundle holds at most {MAX_MEMBERS})")
    total = sum(e.size for e in entries)
    if total > MAX_UNCOMPRESSED:
        raise BundleError(f"world too large to bundle: {total} bytes "
                          f"(a bundle expands to at most {MAX_UNCOMPRESSED})")
    objects = [e for e in entries if e.arc.startswith(f"{STORE_PREFIX}/objects/")]
    if (sum(e.size for e in objects) > MAX_OBJECT_BYTES
            or any(e.size > MAX_OBJECT_MEMBER_BYTES for e in objects)):
        raise BundleError("world too large to bundle: its image metadata is "
                          "past what a bundle carries")


def _check_written(dest: Path) -> None:
    """`BundleError` for a written archive the import would refuse: larger than
    the import route takes (`MAX_BUNDLE_BYTES`), which is only known once it is
    written, or -- a file that grew since it was planned -- past the caps
    `_check_whole` asked."""
    size = dest.stat().st_size
    if size > MAX_BUNDLE_BYTES:
        raise BundleError(f"bundle too large to import: {size} bytes "
                          f"(an import takes up to {MAX_BUNDLE_BYTES})")
    with zipfile.ZipFile(dest) as z:
        ziputil.scan(z, max_members=MAX_MEMBERS,
                     max_uncompressed=MAX_UNCOMPRESSED, err=BundleError)


def _packable_blob(obj: image_store.ImageObject) -> Path | None:
    """The blob `_image_entries` would pack for `obj`, or None when it skips it
    (missing, not a regular file, or a symlink)."""
    blob = image_store.blob_path(obj.blob_sha256, obj.ext)
    try:
        if blob.is_symlink() or not blob.is_file():
            return None
    except OSError:
        return None
    return blob


def _check_blob_sizes(root: Path, collections: list[tuple[str, dict]]) -> None:
    """`BundleError` naming the first placement or format-2 collection member
    whose blob is larger than `MAX_BUNDLE_BLOB_BYTES` -- asked before the
    bundle is opened, so a refused export writes nothing, and the user is told
    which picture to replace."""
    for d, ref in image_refs.walk(root):
        size = _oversized(ref.image)
        if size is not None:
            where = d.relative_to(root).as_posix()
            raise BundleError(
                f"image too large to bundle: {where}/{ref.name} ({size} bytes; "
                f"a bundle carries images up to {MAX_BUNDLE_BLOB_BYTES} bytes)")
    for where, manifest in collections:
        if manifest["format"] != 2:
            continue
        for index, image_id in enumerate(manifest["members"]):
            size = _oversized(image_id)
            if size is not None:
                raise BundleError(
                    f"image too large to bundle: {where} member {index} ({size} bytes; "
                    f"a bundle carries images up to {MAX_BUNDLE_BLOB_BYTES} bytes)")


def _oversized(image_id: str | None) -> int | None:
    """The size of `image_id`'s blob when the export would pack it and it is
    past `MAX_BUNDLE_BLOB_BYTES`; None otherwise."""
    obj = image_store.read(image_id) if image_id is not None else None
    blob = _packable_blob(obj) if obj is not None else None
    if blob is None:
        return None
    try:
        size = blob.stat().st_size
    except OSError:
        return None                                         # collected mid-walk
    return size if size > MAX_BUNDLE_BLOB_BYTES and image_store.blob_intact(blob) else None


def _image_entries(root: Path, wid: str, member_ids: set[str]) -> list[_Entry]:
    """The blob and the projected object of every image a placement under
    `root` or a format-2 collection manifest (`member_ids`) names -- once
    each, however many name it.

    An id that does not resolve (no object, or its blob missing) is skipped:
    the world still exports, and that ref simply travels without a picture,
    exactly as unresolvable as it already was here. A blob that is a symlink is
    skipped for the reason the world walk skips one. So is a blob whose bytes
    no longer match its name (`image_store.blob_intact`): the import would
    refuse the whole bundle over it, and its placement arrives image-less
    instead, through the import's own containment of an id the bundle does not
    carry.
    """
    scope = image_scopes.world_scope(wid)
    out = []
    for image_id in sorted(set(image_refs.walk_ids(root)) | member_ids):
        obj = image_store.read(image_id)
        blob = _packable_blob(obj) if obj is not None else None
        if obj is None or blob is None:
            continue
        if not image_store.blob_intact(blob):
            logs.record("warning", __name__,
                        "image blob does not match its name; left out of the bundle",
                        kind="bundle_image_skipped", blob=obj.blob_sha256)
            continue
        sha = obj.blob_sha256
        projected = image_store.project(obj.raw, scope)
        out.append(_Entry(f"{STORE_PREFIX}/blobs/{sha[:2]}/{sha}.{obj.ext}",
                          src=blob, size=obj.size))
        out.append(_Entry(f"{STORE_PREFIX}/objects/{image_id[4:6]}/{image_id}.json",
                          zipfile.ZIP_DEFLATED,
                          data=(json.dumps(projected, indent=2, sort_keys=True)
                                + "\n").encode("utf-8")))
    return out


# ---- import ----

def _read_manifest(z: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> dict:
    """The bundle's manifest, validated. Everything this returns is trusted by
    the rest of the import, so it is all checked here."""
    if not any(i.filename == MANIFEST_NAME for i in infos):
        raise BundleError(f"not a world bundle: no {MANIFEST_NAME}")
    try:
        manifest = json.loads(z.read(MANIFEST_NAME))
    except (ValueError, OSError, RuntimeError, NotImplementedError,
            zipfile.BadZipFile) as e:
        raise BundleError(f"unreadable {MANIFEST_NAME}: {e}")
    if not isinstance(manifest, dict):
        raise BundleError(f"{MANIFEST_NAME} is not an object")
    if manifest.get("kind") != "world":
        raise BundleError(f"not a world bundle: kind is {manifest.get('kind')!r}")
    fmt = manifest.get("format")
    # `type(...) is int`, not isinstance: JSON `true` is a Python bool, bool is
    # a subclass of int, and `True == 1` -- so `{"format": true}` would have
    # been read as format 1 (Codex review).
    if type(fmt) is not int or fmt not in _READABLE:
        # Named separately because the fix differs: a newer bundle needs a
        # newer grimoire, anything else is a broken file.
        if type(fmt) is int and fmt > MAX_FORMAT:
            raise BundleError(
                f"bundle format {fmt} is newer than this grimoire understands ({MAX_FORMAT})")
        raise BundleError(f"unsupported bundle format: {fmt!r}")
    if not safe_id(manifest.get("world_id")):
        raise BundleError(f"bundle names an unusable world id: {manifest.get('world_id')!r}")
    return manifest


@dataclass
class _Members:
    world: list[zipfile.ZipInfo]
    #: blob sha -> its member.
    blobs: dict[str, zipfile.ZipInfo]
    #: bundle-claimed image id -> its member.
    objects: dict[str, zipfile.ZipInfo]


#: Names a placement reader opens by exact spelling. On a case-insensitive
#: filesystem any other spelling opens the same file, so it is refused.
_EXACT_NAMES = (image_refs.REFS_DIR, image_refs.JOURNAL)


def _fold(name: str) -> str:
    """`name` as a case-insensitive filesystem compares it. Upper-casing first
    is what catches a dotless i (U+0131), which ``casefold`` leaves alone but NTFS
    upper-cases to ``I``."""
    return name.upper().casefold()


def _check_placement_spelling(parts: list[str], filename: str) -> None:
    """Refuse a member under a case variant of ``image-refs/`` or named a case
    variant of the promotion journal.

    `_contain_refs` walks the staged tree by the exact names, but on macOS or
    Windows ``IMAGE-REFS/cover.json`` *is* ``image-refs/cover.json`` to every
    reader: a placement (or journal) spelled that way would skip the walk and
    place whatever local image it names. No grimoire writes one, so a bundle
    holding one was built by hand, and is refused before anything is written."""
    for part in parts[1:]:
        folded = _fold(part)
        for exact in _EXACT_NAMES:
            if folded == exact and part != exact:
                raise BundleError(f"bundle entry uses another spelling of {exact!r}: {filename}")


def _world_members(infos: list[zipfile.ZipInfo], fmt: int) -> _Members:
    """The members under ``world/`` and ``image-store/``, with the archive's
    shape checked.

    Three things may sit at the archive root: the manifest, the world
    directory, and (format 2) the image store. Anything else means this is not
    a bundle we understand, and guessing at it is how an import writes files
    nobody asked for. Inside ``image-store/`` only the two exact shapes are
    allowed, with the shard agreeing with the hash it shards.
    """
    out = _Members([], {}, {})
    for i in infos:
        if i.filename == MANIFEST_NAME:
            continue
        parts = ziputil.member_parts(i.filename, min_parts=2, err=BundleError)
        if parts[0] == WORLD_PREFIX:
            _check_placement_spelling(parts, i.filename)
            out.world.append(i)
            continue
        if parts[0] != STORE_PREFIX or fmt < 2:
            raise BundleError(f"unexpected entry outside {WORLD_PREFIX}/: {i.filename}")
        blob = _BLOB_MEMBER.fullmatch(i.filename)
        if blob is not None and blob.group(1) == blob.group(2)[:2]:
            # One sha under two extensions is one blob named twice.
            if out.blobs.setdefault(blob.group(2), i) is not i:
                raise BundleError(f"image blob packed twice: {i.filename}")
            continue
        obj = _OBJECT_MEMBER.fullmatch(i.filename)
        if obj is not None and obj.group(1) == obj.group(3)[:2]:
            out.objects[obj.group(2)] = i
            continue
        raise BundleError(f"unexpected entry in {STORE_PREFIX}/: {i.filename}")
    if not any(ziputil.member_parts(i.filename)[1:] == ["world.md"] for i in out.world):
        raise BundleError(f"not a world bundle: no {WORLD_PREFIX}/world.md")
    return out


def _check_collections(z: zipfile.ZipFile, members: _Members, bundle_fmt: int) -> None:
    """Refuse a bundle carrying an image collection manifest of a format its
    own format may not carry, or a format-2 manifest that does not read.

    A bundle of format 1 or 2 carries format-1 manifests only; format 3 may
    carry format 2 as well, each one validated by the store's own rule, since
    `_contain_manifests` rewrites it and the store must then read it. Either
    way the bundle is refused before anything is written. A manifest that is
    not a JSON object at all is left alone: it was as unreadable where it came
    from, and reads as invalid here too."""
    allowed = _COLLECTION_FORMATS[bundle_fmt]
    for info in members.world:
        # Folded: on a case-insensitive filesystem `Image-Collections/` is
        # the same directory.
        if not _is_manifest(ziputil.member_parts(info.filename)[1:]):
            continue
        data = _read_member(z, info, MAX_COLLECTION_MANIFEST_BYTES)
        try:
            raw = json.loads(data)
        except (ValueError, RecursionError):
            continue
        if not isinstance(raw, dict):
            continue
        fmt = raw.get("format")
        if type(fmt) is int and fmt in allowed:
            if fmt == 2:
                # Strictly, as the store and `_contain_manifests` read it: a
                # UTF-16 or BOM manifest `json.loads(bytes)` accepts would
                # otherwise pass here and then skip containment.
                try:
                    _strict_manifest(data)
                except (ValueError, RecursionError) as e:
                    raise BundleError(
                        f"invalid collection manifest: {info.filename}: {e}") from e
            continue
        if type(fmt) is int and fmt > max(_COLLECTION_FORMATS[MAX_FORMAT]):
            raise BundleError(
                f"collection manifest format {fmt} is newer than this grimoire "
                f"understands ({max(_COLLECTION_FORMATS[MAX_FORMAT])}): {info.filename}")
        if type(fmt) is int and fmt in _COLLECTION_FORMATS[MAX_FORMAT]:
            raise BundleError(
                f"collection manifest format {fmt} needs bundle format "
                f"{COLLECTIONS_FORMAT}; this bundle is format {bundle_fmt}: {info.filename}")
        raise BundleError(f"unsupported collection manifest format {fmt!r}: {info.filename}")


def _read_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int) -> bytes:
    """One member's bytes, refused past `cap` and with every way a zip read
    can fail re-dressed as the import's own error."""
    if info.file_size > cap:
        raise BundleError(f"bundle member too large: {info.filename}")
    try:
        return z.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as e:
        raise BundleError(f"unreadable bundle member {info.filename}: {e}") from e


@dataclass(frozen=True)
class _ObjectMeta:
    """What the post-publish merge needs from one bundled object, and nothing
    else: the parsed sidecar is dropped as soon as this is taken from it."""
    blob_sha: str
    description: str | None
    #: The bundle's own-scope associations, scope still the source world's.
    associations: tuple[dict, ...]
    #: Whether the source world's scope is among the reviewed subjects.
    reviewed: bool


def _meta_of(raw: dict, sha: str, source_scope: str, filename: str) -> _ObjectMeta:
    desc = raw.get("description")
    if isinstance(desc, str) and len(desc) > MAX_IMPORTED_DESCRIPTION:
        logs.record("warning", __name__,
                    "bundled image description too long; not imported",
                    kind="bundle_description_dropped", member=filename,
                    length=len(desc))
        desc = None
    assoc = raw.get("associations")
    kept = tuple(a for a in assoc if isinstance(a, dict)
                 and a.get("scope") == source_scope
                 and all(isinstance(v, str) for v in a.values())
                 ) if isinstance(assoc, list) else ()
    reviews = raw.get("reviews")
    subjects = reviews.get("subjects") if isinstance(reviews, dict) else None
    reviewed = isinstance(subjects, list) and source_scope in subjects
    return _ObjectMeta(sha, desc if isinstance(desc, str) else None, kept, reviewed)


def _read_objects(z: zipfile.ZipFile, members: _Members,
                  source_wid: str) -> dict[str, _ObjectMeta]:
    """Each bundled object's mergeable metadata, keyed by the id the bundle
    claims.

    Checked before any blob is ingested, so a malformed object refuses the
    import without leaving pictures behind. The claimed id is only a key: what
    the object is called *here* is recomputed from its blob. Bounded from the
    headers before anything is parsed -- the total object bytes, and no more
    objects than blobs -- and then at most one object per blob: one blob
    determines one object, so a second naming it can only be padding.
    """
    total = sum(i.file_size for i in members.objects.values())
    if total > MAX_OBJECT_BYTES:
        raise BundleError(f"bundle image metadata too large ({total} bytes)")
    if len(members.objects) > len(members.blobs):
        raise BundleError("bundle has more image objects than image blobs")
    source_scope = f"world:{source_wid}"
    out: dict[str, _ObjectMeta] = {}
    claimed: set[str] = set()
    for bundle_id, info in members.objects.items():
        try:
            raw = json.loads(_read_member(z, info, MAX_OBJECT_MEMBER_BYTES))
        except (ValueError, RecursionError) as e:
            raise BundleError(f"unreadable image object {info.filename}: {e}") from e
        blob = raw.get("blob") if isinstance(raw, dict) else None
        sha = blob.get("sha256") if isinstance(blob, dict) else None
        if not isinstance(sha, str) or sha not in members.blobs:
            raise BundleError(f"image object names no bundled blob: {info.filename}")
        if sha in claimed:
            raise BundleError(f"two image objects name one blob: {info.filename}")
        claimed.add(sha)
        out[bundle_id] = _meta_of(raw, sha, source_scope, info.filename)
    return out


def _ingest_blobs(z: zipfile.ZipFile, members: _Members) -> dict[str, str]:
    """Re-hash and ingest every bundled blob; returns blob sha -> local id.

    The name is checked against the bytes *as received*; ingest then
    sanitizes them like any upload (see the module docstring), so the local
    blob can differ from the bundled one only when the bundle's was not one a
    store would keep. Ingest is find-or-create, so a blob the library already
    holds is reused, and a picture it holds under another encoding keeps its
    own (first wins).
    """
    local: dict[str, str] = {}
    for sha, info in members.blobs.items():
        data = _read_member(z, info, MAX_BUNDLE_BLOB_BYTES)
        if hashlib.sha256(data).hexdigest() != sha:
            raise BundleError(f"image blob does not match its name: {info.filename}")
        ext = info.filename.rsplit(".", 1)[1]
        try:
            local[sha] = image_store.ingest(data, ext).id
        except ValueError as e:
            raise BundleError(f"unusable image blob {info.filename}: {e}") from e
    return local


def _contain_refs(staging: Path, id_map: dict[str, str], contained: set[str]) -> None:
    """Hold every staged placement to what the bundle itself carries.

    A ref naming a bundle id is pointed at that object's local id (its focus
    kept). A ref naming an id the bundle carries no picture for -- neither one
    of its objects' claimed ids nor the local id one of its blobs ingested to
    -- would otherwise resolve to whatever the importing library happens to
    hold under that id, so it loses its image: an image-less override where it
    had a focus (the focus is the world's own), nothing where it had not. That
    holds for format 1 too, which carries no placements at all unless it was
    built by hand.

    Promotion journals are removed: an export never packs one
    (`image_refs.is_transient`), and recovery would write the ids a planted one
    names into its slots, past this check. Symlinks are not followed
    (extraction makes none)."""
    dropped = journals = 0
    # Names are compared case-folded, as a case-insensitive filesystem reads
    # them -- behind `_check_placement_spelling`, which refuses the variants.
    for dirpath, dirnames, filenames in os.walk(staging, followlinks=False):
        if _fold(Path(dirpath).name) != image_refs.REFS_DIR:
            continue
        dirnames[:] = []
        owner = Path(dirpath).parent
        for f in filenames:
            if _fold(f) == image_refs.JOURNAL:
                (Path(dirpath) / f).unlink()
                journals += 1
        dropped += sum(_contain_ref(owner, f[: -len(".json")], id_map, contained)
                       for f in filenames
                       if _fold(f) != image_refs.JOURNAL
                       and _fold(f).endswith(".json"))
    if dropped or journals:
        logs.record("warning", __name__,
                    "bundle placements named images the bundle does not carry; dropped",
                    kind="bundle_refs_uncontained", count=dropped, journals=journals)


def _contain_ref(owner: Path, name: str, id_map: dict[str, str],
                 contained: set[str]) -> bool:
    """One staged placement, held to `_contain_refs`'s rule; True when it lost
    its image."""
    ref = image_refs.read(owner, name)
    if ref is None or ref.image is None:
        return False
    if ref.image in id_map:
        if id_map[ref.image] != ref.image:
            image_refs.write(owner, name, id_map[ref.image], focus=ref.focus)
        return False
    if ref.image in contained:
        return False
    image_refs.write(owner, name, None, focus=ref.focus)
    return True


def _contain_manifests(staging: Path, id_map: dict[str, str],
                       contained: set[str]) -> tuple[int, int]:
    """Hold every staged format-2 collection manifest to what the bundle itself
    carries, by `_contain_refs`'s rule; returns (members dropped, manifests
    removed).

    A member naming a bundle id is rewritten to that object's local id, and
    one naming an id the bundle carries no picture for is dropped: it would
    otherwise resolve to whatever this library holds under it. Two members the
    rewrite makes one picture keep the first, since members are unique. A
    manifest left with no members is removed -- an empty one does not read.

    Manifests are found by folded path, as `_contain_refs` finds placements: on
    a case-insensitive filesystem every spelling is the manifest the store
    reads. One that is not a valid format-2 manifest is left alone;
    `_check_collections` has already refused any such format 2."""
    dropped = removed = 0
    for assets_dir in _folded_children(staging, _COLLECTIONS_DIR[0]):
        for collections_dir in _folded_children(assets_dir, _COLLECTIONS_DIR[1]):
            for path in sorted(collections_dir.iterdir()):
                if path.is_symlink() or not path.is_file() or not _fold(path.name).endswith(".json"):
                    continue
                lost, gone = _contain_manifest(path, id_map, contained)
                dropped += lost
                removed += gone
    if dropped or removed:
        logs.record("warning", __name__,
                    "bundle collection members named images the bundle does not carry; dropped",
                    kind="bundle_collection_members_uncontained", count=dropped,
                    removed=removed)
    return dropped, removed


def _folded_children(d: Path, folded: str) -> list[Path]:
    """The directories directly under `d` whose folded name is `folded`, not
    following symlinks (extraction makes none)."""
    return sorted(p for p in d.iterdir()
                  if _fold(p.name) == folded and p.is_dir() and not p.is_symlink())


def _contain_manifest(path: Path, id_map: dict[str, str],
                      contained: set[str]) -> tuple[int, int]:
    """One staged manifest, held to `_contain_manifests`'s rule; returns
    (members dropped, 1 if it was removed else 0)."""
    try:
        manifest = _strict_manifest(path.read_bytes())
    except (ValueError, RecursionError):
        return 0, 0
    if manifest["format"] != 2:
        return 0, 0
    kept: dict[str, None] = {}                  # ordered, so the first is kept
    dropped = 0
    for member in manifest["members"]:
        local = id_map.get(member, member if member in contained else None)
        if local is None:
            dropped += 1
        else:
            kept.setdefault(local)
    if list(kept) == manifest["members"]:
        return 0, 0
    if not kept:
        path.unlink()
        return dropped, 1
    atomic.write_text(path, json.dumps({"format": 2, "members": list(kept)}, indent=2) + "\n")
    return dropped, 0


def _projection(meta: _ObjectMeta, scope: str) -> dict:
    """`meta` as a projection in the final `scope` -- the bundle's own scope
    renamed, which is the only one `_meta_of` kept."""
    out: dict = {}
    if meta.description is not None:
        out["description"] = meta.description
    if meta.associations:
        out["associations"] = [{**a, "scope": scope} for a in meta.associations]
    if meta.reviewed:
        out["reviews"] = {"subjects": [scope]}
    return out


def _merge_metadata(metas: list[tuple[str, _ObjectMeta]], wid: str) -> None:
    """Fill local objects from the bundle's projections, under the final id.

    Never raises: the world is published by now, and a failure here costs its
    descriptions and tags, not the world -- so it is reported, not returned as
    a failed import the caller would retry into a duplicate.
    """
    scope = image_scopes.world_scope(wid)
    for local_id, meta in metas:
        try:
            image_store.merge_projection(local_id, _projection(meta, scope), scope)
        except Exception as e:  # noqa: BLE001 -- the world exists; see above
            logs.record("warning", __name__,
                        "imported world's image metadata could not be merged",
                        kind="bundle_metadata_merge_failed", world=wid,
                        image=local_id, error=f"{type(e).__name__}: {e}")


def _world_name(staging: Path, manifest: dict) -> str:
    """The imported world's display name.

    The extracted ``world.md`` wins over the manifest: the manifest is a
    convenience header, the record is the world. A hand-assembled bundle whose
    header disagrees still imports as the world it actually contains.
    """
    try:
        text = (staging / "world.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise BundleError(f"unreadable {WORLD_PREFIX}/world.md: {e}")
    meta, _body = parse_frontmatter(text)
    for candidate in (meta.get("name"), manifest.get("name"), manifest.get("world_id")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return "Imported World"


def import_bundle(path: Path) -> str:
    """Import the bundle at `path` as a brand-new world; returns its id."""
    ensure_home()
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        raise BundleError(f"not a zip archive: {e}")
    with z:
        infos = ziputil.scan(z, max_members=MAX_MEMBERS,
                             max_uncompressed=MAX_UNCOMPRESSED, err=BundleError)
        manifest = _read_manifest(z, infos)
        members = _world_members(infos, manifest["format"])
        metas = _read_objects(z, members, manifest["world_id"])
        _check_collections(z, members, manifest["format"])
        # The work directory is the context manager's to name and to remove.
        # It used to be this function's, held in the same name as the world's
        # slug -- which the slug then overwrote, so the cleanup `rmtree`'d a
        # bare relative name against the process working directory and the
        # staging tree leaked on every import (see `worlds.staging`).
        with worlds_staging.staging_tree() as staging:
            ziputil.extract(z, members.world, staging, strip=1, err=BundleError)
            base = slugify(_world_name(staging, manifest))
            local = _ingest_blobs(z, members)
            # bundle id -> local id, for every object.
            id_map = {bid: local[m.blob_sha] for bid, m in metas.items()}
            _contain_refs(staging, id_map, set(local.values()))
            _contain_manifests(staging, id_map, set(local.values()))
            wid = uniquify(base, lambda c: worlds_paths.world_root(c).exists())
            if wid != manifest["world_id"]:
                worlds_staging.repoint_urls(staging, manifest["world_id"], wid)
            try:
                wid = worlds_staging.publish(staging, base, wid)
            except worlds_staging.WorldIdConflictError as e:
                # Re-dressed as a BundleError subclass so the import's callers
                # keep one exception family to catch, and the route keeps
                # answering 409 for it.
                raise BundleConflict(str(e)) from e
    _merge_metadata([(id_map[bid], m) for bid, m in metas.items()], wid)
    return wid
