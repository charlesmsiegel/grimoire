"""Whole-world staged import and atomic publication as a new identity."""

from __future__ import annotations

import zipfile
from pathlib import Path

from .. import ziputil
from ..expression_library import transfer as expression_transfer
from ..frontmatter import parse_frontmatter
from ..paths import ensure_home, slugify, uniquify
from ..worlds import paths as worlds_paths
from ..worlds import staging as worlds_staging
from . import _contracts, _images, _validation


def _world_name(staging: Path, manifest: dict) -> str:
    """The imported world's display name.

    The extracted ``world.md`` wins over the manifest: the manifest is a
    convenience header, the record is the world. A hand-assembled bundle whose
    header disagrees still imports as the world it actually contains.
    """
    try:
        text = (staging / "world.md").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise _contracts.BundleError(f"unreadable {_contracts.WORLD_PREFIX}/world.md: {e}") from e
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
        raise _contracts.BundleError(f"not a zip archive: {e}") from e
    with z:
        infos = ziputil.scan(z, max_members=_contracts.MAX_MEMBERS,
                             max_uncompressed=_contracts.MAX_UNCOMPRESSED, err=_contracts.BundleError)
        manifest = _validation._read_manifest(z, infos)
        members = _validation._world_members(infos, manifest["format"])
        metas = _validation._read_objects(z, members, manifest["world_id"])
        _validation._check_collections(z, members, manifest["format"])
        # The work directory is the context manager's to name and to remove.
        # It used to be this function's, held in the same name as the world's
        # slug -- which the slug then overwrote, so the cleanup `rmtree`'d a
        # bare relative name against the process working directory and the
        # staging tree leaked on every import (see `worlds.staging`).
        with worlds_staging.staging_tree() as staging:
            ziputil.extract(z, members.world, staging, strip=1, err=_contracts.BundleError)
            base = slugify(_world_name(staging, manifest))
            local = _images._ingest_blobs(z, members)
            # bundle id -> local id, for every object.
            id_map = {bid: local[m.blob_sha] for bid, m in metas.items()}
            try:
                expression_transfer.rehome(staging, id_map, set(local.values()))
            except ValueError as exc:
                raise _contracts.BundleError(str(exc)) from exc
            _images._contain_refs(staging, id_map, set(local.values()),
                                  exclude=tuple(expression_transfer.directories(staging)))
            _images._contain_manifests(staging, id_map, set(local.values()))
            wid = uniquify(base, lambda c: worlds_paths.world_root(c).exists())
            if wid != manifest["world_id"]:
                worlds_staging.repoint_urls(staging, manifest["world_id"], wid)
            try:
                wid = worlds_staging.publish(staging, base, wid)
            except worlds_staging.WorldIdConflictError as e:
                # Re-dressed as a BundleError subclass so the import's callers
                # keep one exception family to catch, and the route keeps
                # answering 409 for it.
                raise _contracts.BundleConflict(str(e)) from e
    _images._merge_metadata([(id_map[bid], m) for bid, m in metas.items()], wid)
    return wid
