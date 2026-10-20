"""Pinned world snapshot, image projections and bounded streaming archive export."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import stat
import zipfile
from contextlib import suppress
from pathlib import Path

from .. import (
    assets,
    atomic,
    image_refs,
    image_scopes,
    image_store,
    image_surfaces,
    logs,
    paths,
    ziputil,
)
from ..frontmatter import parse_frontmatter
from ..paths import now_iso
from ..worlds import paths as worlds_paths
from . import _contracts


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
    world any more. A format-2 collection member is required instead: omitting
    it would shift later indices on import (#478).

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
    collections, pinned = _packed_manifests(root, world)
    _check_blob_sizes(root, collections)
    member_ids = _manifest_ids(collections)
    manifest = {"format": _contracts.COLLECTIONS_FORMAT if member_ids else _contracts.FORMAT, "kind": "world",
                "world_id": wid, "name": meta.get("name", wid),
                "app_version": app_version(), "exported": now_iso()}
    entries = [_contracts._Entry(_contracts.MANIFEST_NAME, compress=zipfile.ZIP_DEFLATED,
                      data=(json.dumps(manifest, indent=2) + "\n").encode("utf-8")),
               *_pin(world, pinned),
               *_image_entries(root, wid, member_ids)]
    _check_whole(entries)
    try:
        with zipfile.ZipFile(dest, "w") as z:
            missing_blobs: set[Path] = set()
            for e in entries:
                if e.requires_blob in missing_blobs:
                    continue
                if e.data is not None or e.src is None:
                    z.writestr(e.arc, e.data or b"", compress_type=e.compress)
                    continue
                if e.blob_stat is not None:
                    if not _write_blob(z, e):
                        missing_blobs.add(e.src)
                    continue
                try:
                    z.write(e.src, e.arc, compress_type=e.compress)
                except FileNotFoundError as exc:
                    if e.required is not None:
                        raise _contracts.BundleError(f"collection member unavailable: {e.required}") from exc
                    continue                    # deleted or collected mid-walk
        _check_written(dest)
    except BaseException:
        # `dest` never keeps a bundle the import would refuse, nor half of one.
        dest.unlink(missing_ok=True)
        raise



def _write_blob(z: zipfile.ZipFile, entry: _contracts._Entry) -> bool:
    """Copy the validated blob through one checked handle, hashing as copied.

    A sync can replace a blob or an ancestor after preflight (#478). Refuse
    links again at the open, and compare the handle's identity BEFORE reading
    bytes: Windows has no O_NOFOLLOW, and that flag alone would not protect
    ancestors on POSIX either. Once open, a path replacement cannot redirect
    this handle. Hashing the actual copy also catches an in-place rewrite.
    A refusal discards the whole archive through `write_bundle`. Only an
    optional blob gone BEFORE opening returns False; its projection must then
    be omitted too, since import refuses an object without its blob.
    """
    assert entry.src is not None and entry.blob_stat is not None
    error = (f"collection member unavailable: {entry.required}" if entry.required is not None
             else f"image changed while bundling: {entry.arc}")
    expected = entry.blob_stat
    opened = False
    try:
        if image_surfaces.linked(paths.home(), entry.src):
            raise _contracts.BundleError(error)
        with open(entry.src, "rb", opener=lambda p, flags: os.open(
                p, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))) as source:
            opened = True
            held = os.fstat(source.fileno())
            if (not stat.S_ISREG(held.st_mode) or not os.path.samestat(held, expected)
                    or (held.st_size, held.st_mtime_ns) != (expected.st_size, expected.st_mtime_ns)
                    or image_surfaces.linked(paths.home(), entry.src)):
                raise _contracts.BundleError(error)
            digest = hashlib.sha256()
            size = 0
            info = zipfile.ZipInfo(entry.arc)
            info.compress_type = entry.compress
            # atomic-ok: the caller's export archive, discarded whole on failure,
            # never a live record in the store.
            with z.open(info, "w", force_zip64=True) as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    size += len(chunk)
                    if size > _contracts.MAX_BUNDLE_BLOB_BYTES:
                        raise _contracts.BundleError(error)
                    digest.update(chunk)
                    target.write(chunk)
            if digest.hexdigest() != image_store.blob_sha_of(entry.src):
                raise _contracts.BundleError(error)
    except FileNotFoundError as exc:
        if entry.required is None and not opened:
            return False
        raise _contracts.BundleError(error) from exc
    except OSError as exc:
        raise _contracts.BundleError(error) from exc
    return True


def _world_entries(root: Path) -> list[_contracts._Entry]:
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
        compress = (zipfile.ZIP_DEFLATED if p.suffix.lower() in _contracts._COMPRESSIBLE
                    else zipfile.ZIP_STORED)
        out.append(_contracts._Entry(f"{_contracts.WORLD_PREFIX}/{p.relative_to(root).as_posix()}",
                          compress, src=p, size=size))
    return out


def _packed_manifests(root: Path, world: list[_contracts._Entry]
                      ) -> tuple[list[tuple[str, dict]], dict[Path, _contracts._Entry | None]]:
    """Every format-2 collection manifest among the packed world files,
    validated by the store's own rule, as `(path relative to root, manifest)`,
    and what each collection manifest the walk found is packed as, keyed by its
    source: the bytes read here, or None for one gone by now. The archive
    writes those, never the file again, whatever format it was read as: a sync
    that replaces or recreates a manifest after this read -- a format-1 one
    with a format-2 one included -- must not pack a member map other than the
    one the blobs were chosen by (#478).

    Validated exactly where the import would refuse (`_check_collections`): a
    file that does not parse as a JSON object, or that says format 1, is
    packed unchecked, as the import takes it unchecked. Anything else must
    read, or `BundleError` names it before anything is written -- so an export
    never packs a manifest its own import refuses. A manifest that vanished
    since the walk is not part of the world any more."""
    out: list[tuple[str, dict]] = []
    pinned: dict[Path, _contracts._Entry | None] = {}
    for e in world:
        rel = e.arc.split("/")[1:]
        if e.src is None or not _contracts._is_manifest(rel):
            continue
        where = "/".join(rel)
        try:
            data = e.src.read_bytes()
        except FileNotFoundError:
            pinned[e.src] = None
            continue
        except OSError as exc:
            raise _contracts.BundleError(f"unreadable collection manifest: {where}: {exc}") from exc
        pinned[e.src] = _contracts._Entry(e.arc, compress=e.compress, data=data)
        if not _contracts._checked_by_import(data):
            continue
        try:
            manifest = _contracts._strict_manifest(data)
        except (ValueError, RecursionError) as exc:
            raise _contracts.BundleError(f"invalid collection manifest: {where}: {exc}") from exc
        out.append((where, manifest))
    return out, pinned


def _pin(world: list[_contracts._Entry], pinned: dict[Path, _contracts._Entry | None]) -> list[_contracts._Entry]:
    """`world` with every collection manifest replaced by what
    `_packed_manifests` read, and one it found gone left out."""
    out = []
    for e in world:
        if e.src is None or e.src not in pinned:
            out.append(e)
        elif (entry := pinned[e.src]) is not None:
            out.append(entry)
    return out


def _manifest_ids(collections: list[tuple[str, dict]]) -> dict[str, str]:
    """Image id -> one collection member position that requires it (#478)."""
    return {m: f"{where} member {i}" for where, c in collections if c["format"] == 2
            for i, m in enumerate(c["members"])}


def _check_whole(entries: list[_contracts._Entry]) -> None:
    """`BundleError` for a bundle the import's whole-archive caps would refuse
    (`ziputil.scan`'s member count and expanded size, and the object metadata
    caps), asked of the planned members before anything is written."""
    if len(entries) > _contracts.MAX_MEMBERS:
        raise _contracts.BundleError(f"world too large to bundle: {len(entries)} files "
                          f"(a bundle holds at most {_contracts.MAX_MEMBERS})")
    total = sum(e.size for e in entries)
    if total > _contracts.MAX_UNCOMPRESSED:
        raise _contracts.BundleError(f"world too large to bundle: {total} bytes "
                          f"(a bundle expands to at most {_contracts.MAX_UNCOMPRESSED})")
    objects = [e for e in entries if e.arc.startswith(f"{_contracts.STORE_PREFIX}/objects/")]
    if (sum(e.size for e in objects) > _contracts.MAX_OBJECT_BYTES
            or any(e.size > _contracts.MAX_OBJECT_MEMBER_BYTES for e in objects)):
        raise _contracts.BundleError("world too large to bundle: its image metadata is "
                          "past what a bundle carries")


def _check_written(dest: Path) -> None:
    """`BundleError` for a written archive the import would refuse: larger than
    the import route takes (`MAX_BUNDLE_BYTES`), which is only known once it is
    written, or -- a file that grew since it was planned -- past the caps
    `_check_whole` asked."""
    size = dest.stat().st_size
    if size > _contracts.MAX_BUNDLE_BYTES:
        raise _contracts.BundleError(f"bundle too large to import: {size} bytes "
                          f"(an import takes up to {_contracts.MAX_BUNDLE_BYTES})")
    with zipfile.ZipFile(dest) as z:
        ziputil.scan(z, max_members=_contracts.MAX_MEMBERS,
                     max_uncompressed=_contracts.MAX_UNCOMPRESSED, err=_contracts.BundleError)


def _packable_object(image_id: str | None) -> image_store.ImageObject | None:
    """An object's metadata only when no dependency path follows a link (#478).
    Check before even the size preflight reads it, not only before packing."""
    if image_id is None:
        return None
    try:
        if image_surfaces.linked(paths.home(), image_store.object_path(image_id)):
            return None
    except OSError:
        return None
    return image_store.read(image_id)


def _packable_blob(obj: image_store.ImageObject) -> Path | None:
    """The blob `_image_entries` would pack for `obj`, or None when it skips it
    (missing, not a regular file, or a symlink)."""
    blob = image_store.blob_path(obj.blob_sha256, obj.ext)
    try:
        if image_surfaces.linked(paths.home(), blob) or not blob.is_file():
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
            raise _contracts.BundleError(
                f"image too large to bundle: {where}/{ref.name} ({size} bytes; "
                f"a bundle carries images up to {_contracts.MAX_BUNDLE_BLOB_BYTES} bytes)")
    for where, manifest in collections:
        if manifest["format"] != 2:
            continue
        for index, image_id in enumerate(manifest["members"]):
            size = _oversized(image_id)
            if size is not None:
                raise _contracts.BundleError(
                    f"image too large to bundle: {where} member {index} ({size} bytes; "
                    f"a bundle carries images up to {_contracts.MAX_BUNDLE_BLOB_BYTES} bytes)")


def _oversized(image_id: str | None) -> int | None:
    """The size of `image_id`'s blob when the export would pack it and it is
    past `MAX_BUNDLE_BLOB_BYTES`; None otherwise."""
    obj = _packable_object(image_id)
    blob = _packable_blob(obj) if obj is not None else None
    if blob is None:
        return None
    try:
        size = blob.stat().st_size
    except OSError:
        return None                                         # collected mid-walk
    return size if size > _contracts.MAX_BUNDLE_BLOB_BYTES and image_store.blob_intact(blob) else None


def _image_entries(root: Path, wid: str, member_ids: dict[str, str]) -> list[_contracts._Entry]:
    """The blob and the projected object of every image a placement under
    `root` or a format-2 collection manifest (`member_ids`) names -- once
    each, however many name it.

    A collection member that cannot be packed refuses the export: importing
    an incomplete collection would shift every later member's index (#478).
    A placement id that does not resolve (no object, or its blob missing) is skipped:
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
    for image_id in sorted(set(image_refs.walk_ids(root)) | member_ids.keys()):
        obj = _packable_object(image_id)
        blob = _packable_blob(obj) if obj is not None else None
        blob_stat = None
        if blob is not None:
            with suppress(OSError):                     # collected after preflight
                blob_stat = blob.stat(follow_symlinks=False)
        intact = (blob is not None and blob_stat is not None and stat.S_ISREG(blob_stat.st_mode)
                  and image_store.blob_intact(blob))
        if image_id in member_ids and not intact:
            raise _contracts.BundleError(f"collection member unavailable: {member_ids[image_id]}")
        if obj is None or blob is None:
            continue
        if not intact:
            logs.record("warning", 'grimoire.store.world_bundle',
                        "image blob does not match its name; left out of the bundle",
                        kind="bundle_image_skipped", blob=obj.blob_sha256)
            continue
        sha = obj.blob_sha256
        projected = image_store.project(obj.raw, scope)
        out.append(_contracts._Entry(f"{_contracts.STORE_PREFIX}/blobs/{sha[:2]}/{sha}.{obj.ext}",
                          src=blob, size=obj.size, required=member_ids.get(image_id),
                          blob_stat=blob_stat))
        out.append(_contracts._Entry(f"{_contracts.STORE_PREFIX}/objects/{image_id[4:6]}/{image_id}.json",
                          zipfile.ZIP_DEFLATED,
                          data=(json.dumps(projected, indent=2, sort_keys=True)
                                + "\n").encode("utf-8"), requires_blob=blob))
    return out
