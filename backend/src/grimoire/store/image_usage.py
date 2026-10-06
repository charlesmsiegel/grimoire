"""Where a picture is used: derived from the placements, never stored.

``find(image_id)`` answers "which records hold this image?" for the image
object's "Used in..." control. Every placement names its image
(`image_refs`), so the answer is a walk: nothing is kept that could drift from
the placements, and a deleted record takes its usage with it.

This is an on-demand full walk of every world and campaign: `image_refs.walk`
visits every directory of every one of them, whether or not it bears an image.
Section 9 of the design (a rebuildable cache) is the escape hatch if profiling
ever demands one; until then the walk reads no object and resolves no blob.

Classification works from the directory that owns a placement. The roots are
every directory directly under ``<home>/worlds`` and ``<home>/campaigns``,
walked as `campaigns.read.world_refs` does rather than through the listings: a
listing may hide a record nobody can read, and a picture placed in one is still
used there. Each owning directory is matched against the layout the path
builders spell (`assets.version_dir` for a record's version, the library
directory, the cover's ``assets/`` directory); a directory that matches none of
them is skipped.

Scope strings are R10's: ``world:<canonical id>`` and ``campaign:<cid>`` as
stored.

The ``collections`` bucket lists a format-1 member by its library placement
(the walk finds the placement, then the manifests naming it) and a format-2
member by its id (C10): such a member is its object and has no placement, so
each world's format-2 manifests are read for the id itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import (
    assets,
    campaign_images,
    covers,
    entities,
    image_collections,
    image_hash,
    image_refs,
    image_scopes,
    paths,
    pcs,
    world_images,
)
from .worlds import paths as worlds_paths

BUCKETS: tuple[str, ...] = ("characters", "pcs", "entities", "greetings", "world_images",
                            "campaign_images", "covers", "collections")

_CHARACTERS = "characters"
_GREETINGS = "greetings"
_VID = "default"          # a greeting's one version directory


def _roots() -> list[tuple[bool, Path]]:
    """``(is_world, root)`` for every directory directly under the worlds and
    campaigns directories, in name order."""
    out: list[tuple[bool, Path]] = []
    for is_world, name in ((True, "worlds"), (False, "campaigns")):
        base = paths.home() / name
        try:
            kids = sorted(base.iterdir())
        except OSError:
            continue
        out.extend((is_world, d) for d in kids if d.is_dir())
    return out


def _record_dir(root: Path, parts: tuple[str, ...]) -> tuple[str, str, str] | None:
    """``(base, record id, version id)`` when the owning directory is a record's
    version directory as `assets.version_dir` builds it, else None."""
    if len(parts) != 4 or parts[2] != "assets":
        return None
    base, rid, _assets, vid = parts
    if base not in (_CHARACTERS, pcs.ASSET_BASE, _GREETINGS, *entities.ENTITY_KINDS):
        return None
    try:
        built = assets.version_dir(root, rid, vid, base=base)
    except ValueError:
        return None
    return (base, rid, vid) if built == root.joinpath(*parts) else None


def _manifests(wid: str) -> list[tuple[str, dict]]:
    """`(collection id, manifest)` for every manifest of the world that reads.
    A manifest nobody can read is skipped: this is a report, and an unknown
    reference is shown by the walk that found the placement."""
    try:
        listing = sorted(image_collections.directory(wid).glob("*.json"))
    except (OSError, worlds_paths.WorldNotFound):
        return []
    out = []
    for path in listing:
        try:
            out.append((path.stem, image_collections.read(wid, path.stem)))
        except (ValueError, OSError):
            continue
    return out


def _collections_of(wid: str, name: str) -> list[dict]:
    """The format-1 manifests that list the library image `name` as a member."""
    folded = name.casefold()
    return [{"wid": wid, "collection": collection}
            for collection, manifest in _manifests(wid)
            if manifest["format"] == 1
            and folded in {m.casefold() for m in manifest["members"]}]


def _collections_naming(wid: str, image_id: str) -> list[dict]:
    """The format-2 manifests that list `image_id` as a member: a format-2
    member is its object, with no library placement for the walk to find."""
    return [{"wid": wid, "collection": collection}
            for collection, manifest in _manifests(wid)
            if manifest["format"] == 2 and image_id in manifest["members"]]


def _sorted(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda e: json.dumps(e, sort_keys=True))


def _add_record(out: dict[str, list[dict]], scope: str, rec: tuple[str, str, str],
                name: str) -> None:
    base, rid, vid = rec
    if base == _GREETINGS:
        if vid == _VID:
            out["greetings"].append({"scope": scope, "id": rid, "name": name})
    elif base in (_CHARACTERS, pcs.ASSET_BASE):
        out[base].append({"scope": scope, "id": rid, "vid": vid, "name": name})
    else:
        out["entities"].append({"scope": scope, "kind": base, "id": rid, "vid": vid,
                                "name": name})


def _scan_root(out: dict[str, list[dict]], is_world: bool, root: Path, image_id: str) -> None:
    if is_world:
        ident = root.name        # from the directory listing: already canonical
        scope = image_scopes.world_scope_of_dir(ident)
        library = root / "assets" / world_images.DIRNAME
    else:
        ident = root.name
        scope = image_scopes.campaign_scope(root.name)
        library = root / "assets" / campaign_images.DIRNAME
    if is_world:
        out["collections"].extend(_collections_naming(ident, image_id))
    for d, ref in image_refs.walk(root):
        if ref.image != image_id:
            continue
        if d == library and is_world:
            out["world_images"].append({"wid": ident, "name": ref.name})
            out["collections"].extend(_collections_of(ident, ref.name))
        elif d == library:
            out["campaign_images"].append({"cid": ident, "name": ref.name})
        elif d == root / "assets":
            if ref.name == covers.NAME:
                out["covers"].append({"scope": scope})
        else:
            rec = _record_dir(root, d.relative_to(root).parts)
            if rec is not None:
                _add_record(out, scope, rec, ref.name)


def find(image_id: str) -> dict[str, list[dict]]:
    """Every placement of `image_id`, by surface (see the module docstring).

    Exactly the keys in `BUCKETS`, each list sorted by its entries' canonical
    JSON. An id nothing places answers all-empty; a malformed id is the
    caller's to refuse (`image_hash.is_image_id`), and here it simply finds
    nothing."""
    out: dict[str, list[dict]] = {b: [] for b in BUCKETS}
    if not image_hash.is_image_id(image_id):
        return out
    for is_world, root in _roots():
        _scan_root(out, is_world, root, image_id)
    return {b: _sorted(rows) for b, rows in out.items()}
