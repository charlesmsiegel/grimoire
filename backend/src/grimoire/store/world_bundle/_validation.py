"""Preflight of every archive member, collection and projected image object."""

from __future__ import annotations

import json
import zipfile

from .. import logs, ziputil
from ..paths import safe_id
from . import _contracts

# ---- import ----

def _read_manifest(z: zipfile.ZipFile, infos: list[zipfile.ZipInfo]) -> dict:
    """The bundle's manifest, validated. Everything this returns is trusted by
    the rest of the import, so it is all checked here."""
    if not any(i.filename == _contracts.MANIFEST_NAME for i in infos):
        raise _contracts.BundleError(f"not a world bundle: no {_contracts.MANIFEST_NAME}")
    try:
        manifest = json.loads(z.read(_contracts.MANIFEST_NAME))
    except (ValueError, OSError, RuntimeError, NotImplementedError,
            zipfile.BadZipFile) as e:
        raise _contracts.BundleError(f"unreadable {_contracts.MANIFEST_NAME}: {e}") from e
    if not isinstance(manifest, dict):
        raise _contracts.BundleError(f"{_contracts.MANIFEST_NAME} is not an object")
    if manifest.get("kind") != "world":
        raise _contracts.BundleError(f"not a world bundle: kind is {manifest.get('kind')!r}")
    fmt = manifest.get("format")
    # `type(...) is int`, not isinstance: JSON `true` is a Python bool, bool is
    # a subclass of int, and `True == 1` -- so `{"format": true}` would have
    # been read as format 1 (Codex review).
    if type(fmt) is not int or fmt not in _contracts._READABLE:
        # Named separately because the fix differs: a newer bundle needs a
        # newer grimoire, anything else is a broken file.
        if type(fmt) is int and fmt > _contracts.MAX_FORMAT:
            raise _contracts.BundleError(
                f"bundle format {fmt} is newer than this grimoire understands ({_contracts.MAX_FORMAT})")
        raise _contracts.BundleError(f"unsupported bundle format: {fmt!r}")
    if not safe_id(manifest.get("world_id")):
        raise _contracts.BundleError(f"bundle names an unusable world id: {manifest.get('world_id')!r}")
    return manifest


def _check_placement_spelling(parts: list[str], filename: str) -> None:
    """Refuse a member under a case variant of ``image-refs/`` or named a case
    variant of the promotion journal.

    `_contain_refs` walks the staged tree by the exact names, but on macOS or
    Windows ``IMAGE-REFS/cover.json`` *is* ``image-refs/cover.json`` to every
    reader: a placement (or journal) spelled that way would skip the walk and
    place whatever local image it names. No grimoire writes one, so a bundle
    holding one was built by hand, and is refused before anything is written."""
    for part in parts[1:]:
        folded = _contracts._fold(part)
        for exact in _contracts._EXACT_NAMES:
            if folded == exact and part != exact:
                raise _contracts.BundleError(f"bundle entry uses another spelling of {exact!r}: {filename}")


def _world_members(infos: list[zipfile.ZipInfo], fmt: int) -> _contracts._Members:
    """The members under ``world/`` and ``image-store/``, with the archive's
    shape checked.

    Three things may sit at the archive root: the manifest, the world
    directory, and (format 2) the image store. Anything else means this is not
    a bundle we understand, and guessing at it is how an import writes files
    nobody asked for. Inside ``image-store/`` only the two exact shapes are
    allowed, with the shard agreeing with the hash it shards.
    """
    out = _contracts._Members([], {}, {})
    for i in infos:
        if i.filename == _contracts.MANIFEST_NAME:
            continue
        parts = ziputil.member_parts(i.filename, min_parts=2, err=_contracts.BundleError)
        if parts[0] == _contracts.WORLD_PREFIX:
            _check_placement_spelling(parts, i.filename)
            out.world.append(i)
            continue
        if parts[0] != _contracts.STORE_PREFIX or fmt < 2:
            raise _contracts.BundleError(f"unexpected entry outside {_contracts.WORLD_PREFIX}/: {i.filename}")
        blob = _contracts._BLOB_MEMBER.fullmatch(i.filename)
        if blob is not None and blob.group(1) == blob.group(2)[:2]:
            # One sha under two extensions is one blob named twice.
            if out.blobs.setdefault(blob.group(2), i) is not i:
                raise _contracts.BundleError(f"image blob packed twice: {i.filename}")
            continue
        obj = _contracts._OBJECT_MEMBER.fullmatch(i.filename)
        if obj is not None and obj.group(1) == obj.group(3)[:2]:
            out.objects[obj.group(2)] = i
            continue
        raise _contracts.BundleError(f"unexpected entry in {_contracts.STORE_PREFIX}/: {i.filename}")
    if not any(ziputil.member_parts(i.filename)[1:] == ["world.md"] for i in out.world):
        raise _contracts.BundleError(f"not a world bundle: no {_contracts.WORLD_PREFIX}/world.md")
    return out


def _check_collections(z: zipfile.ZipFile, members: _contracts._Members, bundle_fmt: int) -> None:
    """Refuse a bundle carrying an image collection manifest of a format its
    own format may not carry, or a format-2 manifest that does not read.

    A bundle of format 1 or 2 carries format-1 manifests only; format 3 may
    carry format 2 as well, each one validated by the store's own rule, since
    `_contain_manifests` rewrites it and the store must then read it. Either
    way the bundle is refused before anything is written. A manifest that is
    not a JSON object at all is left alone: it was as unreadable where it came
    from, and reads as invalid here too."""
    allowed = _contracts._COLLECTION_FORMATS[bundle_fmt]
    for info in members.world:
        # Folded: on a case-insensitive filesystem `Image-Collections/` is
        # the same directory.
        if not _contracts._is_manifest(ziputil.member_parts(info.filename)[1:]):
            continue
        data = _read_member(z, info, _contracts.MAX_COLLECTION_MANIFEST_BYTES)
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
                    _contracts._strict_manifest(data)
                except (ValueError, RecursionError) as e:
                    raise _contracts.BundleError(
                        f"invalid collection manifest: {info.filename}: {e}") from e
            continue
        if type(fmt) is int and fmt > max(_contracts._COLLECTION_FORMATS[_contracts.MAX_FORMAT]):
            raise _contracts.BundleError(
                f"collection manifest format {fmt} is newer than this grimoire "
                f"understands ({max(_contracts._COLLECTION_FORMATS[_contracts.MAX_FORMAT])}): {info.filename}")
        if type(fmt) is int and fmt in _contracts._COLLECTION_FORMATS[_contracts.MAX_FORMAT]:
            raise _contracts.BundleError(
                f"collection manifest format {fmt} needs bundle format "
                f"{_contracts.COLLECTIONS_FORMAT}; this bundle is format {bundle_fmt}: {info.filename}")
        raise _contracts.BundleError(f"unsupported collection manifest format {fmt!r}: {info.filename}")


def _read_member(z: zipfile.ZipFile, info: zipfile.ZipInfo, cap: int) -> bytes:
    """One member's bytes, refused past `cap` and with every way a zip read
    can fail re-dressed as the import's own error."""
    if info.file_size > cap:
        raise _contracts.BundleError(f"bundle member too large: {info.filename}")
    try:
        return z.read(info)
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError) as e:
        raise _contracts.BundleError(f"unreadable bundle member {info.filename}: {e}") from e


def _meta_of(raw: dict, sha: str, source_scope: str, filename: str) -> _contracts._ObjectMeta:
    desc = raw.get("description")
    if isinstance(desc, str) and len(desc) > _contracts.MAX_IMPORTED_DESCRIPTION:
        logs.record("warning", 'grimoire.store.world_bundle',
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
    return _contracts._ObjectMeta(sha, desc if isinstance(desc, str) else None, kept, reviewed)


def _read_objects(z: zipfile.ZipFile, members: _contracts._Members,
                  source_wid: str) -> dict[str, _contracts._ObjectMeta]:
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
    if total > _contracts.MAX_OBJECT_BYTES:
        raise _contracts.BundleError(f"bundle image metadata too large ({total} bytes)")
    if len(members.objects) > len(members.blobs):
        raise _contracts.BundleError("bundle has more image objects than image blobs")
    source_scope = f"world:{source_wid}"
    out: dict[str, _contracts._ObjectMeta] = {}
    claimed: set[str] = set()
    for bundle_id, info in members.objects.items():
        try:
            raw = json.loads(_read_member(z, info, _contracts.MAX_OBJECT_MEMBER_BYTES))
        except (ValueError, RecursionError) as e:
            raise _contracts.BundleError(f"unreadable image object {info.filename}: {e}") from e
        blob = raw.get("blob") if isinstance(raw, dict) else None
        sha = blob.get("sha256") if isinstance(blob, dict) else None
        if not isinstance(sha, str) or sha not in members.blobs:
            raise _contracts.BundleError(f"image object names no bundled blob: {info.filename}")
        if sha in claimed:
            raise _contracts.BundleError(f"two image objects name one blob: {info.filename}")
        claimed.add(sha)
        out[bundle_id] = _meta_of(raw, sha, source_scope, info.filename)
    return out
