"""Rehashed local image ingestion, staged containment and post-publish metadata."""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

from .. import atomic, image_refs, image_scopes, image_store, logs
from . import _contracts, _validation


def _ingest_blobs(z: zipfile.ZipFile, members: _contracts._Members) -> dict[str, str]:
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
        data = _validation._read_member(z, info, _contracts.MAX_BUNDLE_BLOB_BYTES)
        if hashlib.sha256(data).hexdigest() != sha:
            raise _contracts.BundleError(f"image blob does not match its name: {info.filename}")
        ext = info.filename.rsplit(".", 1)[1]
        try:
            local[sha] = image_store.ingest(data, ext).id
        except ValueError as e:
            raise _contracts.BundleError(f"unusable image blob {info.filename}: {e}") from e
    return local


def _contain_refs(staging: Path, id_map: dict[str, str], contained: set[str], *,
                  exclude: tuple[Path, ...] = ()) -> None:
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
        if Path(dirpath) in exclude:
            dirnames[:] = []
            continue
        if _contracts._fold(Path(dirpath).name) != image_refs.REFS_DIR:
            continue
        dirnames[:] = []
        owner = Path(dirpath).parent
        for f in filenames:
            if _contracts._fold(f) == image_refs.JOURNAL:
                (Path(dirpath) / f).unlink()
                journals += 1
        dropped += sum(_contain_ref(owner, f[: -len(".json")], id_map, contained)
                       for f in filenames
                       if _contracts._fold(f) != image_refs.JOURNAL
                       and _contracts._fold(f).endswith(".json"))
    if dropped or journals:
        logs.record("warning", 'grimoire.store.world_bundle',
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
    for assets_dir in _folded_children(staging, _contracts._COLLECTIONS_DIR[0]):
        for collections_dir in _folded_children(assets_dir, _contracts._COLLECTIONS_DIR[1]):
            for path in sorted(collections_dir.iterdir()):
                if path.is_symlink() or not path.is_file() or not _contracts._fold(path.name).endswith(".json"):
                    continue
                lost, gone = _contain_manifest(path, id_map, contained)
                dropped += lost
                removed += gone
    if dropped or removed:
        logs.record("warning", 'grimoire.store.world_bundle',
                    "bundle collection members named images the bundle does not carry; dropped",
                    kind="bundle_collection_members_uncontained", count=dropped,
                    removed=removed)
    return dropped, removed


def _folded_children(d: Path, folded: str) -> list[Path]:
    """The directories directly under `d` whose folded name is `folded`, not
    following symlinks (extraction makes none)."""
    return sorted(p for p in d.iterdir()
                  if _contracts._fold(p.name) == folded and p.is_dir() and not p.is_symlink())


def _contain_manifest(path: Path, id_map: dict[str, str],
                      contained: set[str]) -> tuple[int, int]:
    """One staged manifest, held to `_contain_manifests`'s rule; returns
    (members dropped, 1 if it was removed else 0)."""
    try:
        manifest = _contracts._strict_manifest(path.read_bytes())
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


def _projection(meta: _contracts._ObjectMeta, scope: str) -> dict:
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


def _merge_metadata(metas: list[tuple[str, _contracts._ObjectMeta]], wid: str) -> None:
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
            logs.record("warning", 'grimoire.store.world_bundle',
                        "imported world's image metadata could not be merged",
                        kind="bundle_metadata_merge_failed", world=wid,
                        image=local_id, error=f"{type(e).__name__}: {e}")
