"""Format-one collection/journal conversion and verified greeting URL key settlement."""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .. import (
    assets,
    atomic,
    greeting_images,
    image_collection_imports,
    image_collections,
    image_refs,
    image_scopes,
    image_store,
    image_surfaces,
    locks,
    paths,
)
from . import _contracts, _execution_state, _folding

# ---- collections and journals (M10) -----------------------------------------------------

def _world_roots(root: Path) -> list[Path]:
    """Every world directory under `root` the walk would enter."""
    try:
        found = sorted(p for p in (root / "worlds").iterdir() if p.is_dir())
    except OSError:
        return []
    return [p for p in found if paths.safe_id(p.name) and not image_surfaces.linked(root, p)]


def _greeting_dirs(root: Path) -> list[tuple[Path, str, Path]]:
    """``(world root, greeting id, its image dir)`` for every world greeting
    image directory holding a `subjects.json`."""
    out = []
    for wroot in _world_roots(root):
        try:
            gids = sorted(p.name for p in (wroot / "greetings").iterdir() if p.is_dir())
        except OSError:
            continue
        for gid in gids:
            try:
                d = assets.version_dir(wroot, gid, "default", base="greetings")
            except ValueError:
                continue
            if (d / _contracts._SUBJECTS).is_file() and not image_surfaces.linked(root, d):
                out.append((wroot, gid, d))
    return out


@dataclass(frozen=True)
class _Member:
    """One converted member: its library name, index and image id."""
    name: str
    index: int
    image_id: str


def _member_keys(wroot: Path, gid: str, wid: str, collection: str, m: _Member
                 ) -> tuple[str, str]:
    """``(library-URL key, member-URL key)`` as a greeting's catalog spells them."""
    lib = greeting_images.image_key(wroot, gid, f"/api/worlds/{wid}/images/{m.name}")
    member = greeting_images.image_key(
        wroot, gid, f"/api/worlds/{wid}/image-collections/{collection}/members/{m.index}")
    return lib, member


def _carry_member_key(ex: _execution_state._Exec, cur: dict, same: bool, scope: str, keys: tuple[str, str],
                      m: _Member) -> bool | None:
    """Make the member key say what the library key says: on the object for
    the same world, in the sidecar (`cur`, written by the caller) for another
    (R11). True when `cur` changed, None when the library key cannot move."""
    value = cur[keys[0]]
    if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
        return None
    if same:
        image_store.update(m.image_id, lambda raw: _folding._with_subjects(raw, scope, value))
        tags = _folding._tags(ex.root, m.image_id, scope)
        return False if tags is not None and set(value) <= tags else None
    if keys[1] not in cur:
        cur[keys[1]] = list(value)
        return True
    return False if cur[keys[1]] == value else None


def _rekey_one(ex: _execution_state._Exec, where: tuple[Path, str, Path], wid: str, collection: str,
               members: list[_Member], delete: bool) -> None:
    """One greeting's library-URL keys for `members`, under its sidecar lock:
    carried to the member (before the manifest is written), and -- once it is,
    `delete` -- dropped where the member holds them and the library URL is
    no longer one of the greeting's pictures (M10)."""
    wroot, gid, d = where
    side = d / _contracts._SUBJECTS
    same = _execution_state._same_file(wroot, ex.root / "worlds" / wid)
    scope = image_scopes.world_scope_of_dir(wroot.name)
    with locks.image_sidecar_lock(d, _contracts._SUBJECTS):
        cur = _folding._sidecar_now(ex, side)
        if not cur:
            return
        shown = set(greeting_images.catalog_with_slots(wroot, gid)) if delete else set()
        dirty = False
        for m in members:
            keys = _member_keys(wroot, gid, wid, collection, m)
            if keys[0] not in cur:
                continue
            carried = _carry_member_key(ex, cur, same, scope, keys, m)
            if carried is None:
                ex.keep(side, keys[0], "member-key-not-carried")
                continue
            dirty = carried or dirty
            if delete and keys[0] not in shown:
                del cur[keys[0]]
                dirty = True
        if dirty and ex.guard_dir("before-key-delete", d):
            if cur:
                atomic.write_text(side, json.dumps(cur, indent=2, sort_keys=True) + "\n")
            else:
                side.unlink()


def _rekey(ex: _execution_state._Exec, wid: str, collection: str, members: list[_Member], *,
           delete: bool) -> None:
    for where in _greeting_dirs(ex.root):
        _rekey_one(ex, where, wid, collection, members, delete)


def _converted_members(ex: _execution_state._Exec, wid: str, ids: list[str]) -> list[_Member]:
    """The members of a format-2 manifest that a format-1 one named by their
    library placements: what its greetings' library-URL keys pointed at."""
    lib = ex.root / "worlds" / wid / "assets" / "images"
    by_id = {r.image: n for n, r in image_refs.scan(lib).items()
             if r.image is not None and _contracts._MEMBER_NAME.fullmatch(n)}
    return [_Member(by_id[i], n, i) for n, i in enumerate(ids) if i in by_id]


def _convert(ex: _execution_state._Exec, rel: str) -> None:
    path = ex.root / rel
    wid, collection = path.parent.parent.parent.name, path.stem
    if not ex.guard_dir("before-collection", path.parent):
        ex.out["collections_kept"].append({"path": rel, "reason": image_surfaces.SYMLINKED})
        return

    def carry(names: list[str], ids: list[str]) -> None:
        members = [_Member(n, i, x) for i, (n, x) in enumerate(zip(names, ids, strict=True))]
        _rekey(ex, wid, collection, members, delete=False)

    try:
        if image_collections.convert_format1(ex.root, wid, collection, before=carry,
                                             guard=partial(ex.guard, "before-manifest")):
            ex.out["collections_converted"].append(rel)
    except image_collections.CollectionInvalidError as exc:
        ex.out["collections_kept"].append({"path": rel, "reason": str(exc)})


def _format2_manifests(ex: _execution_state._Exec) -> list[tuple[str, str, list[str]]]:
    """``(world, collection id, member ids)`` of every format-2 manifest
    under the pinned root."""
    out = []
    for wroot in _world_roots(ex.root):
        d = wroot.joinpath(*image_surfaces.COLLECTIONS_DIR)
        if image_surfaces.linked(ex.root, d):
            continue
        for p in sorted(d.glob("*.json")):
            try:
                got = image_collections.validate(json.loads(p.read_text(encoding="utf-8")))
            except (OSError, ValueError, RecursionError):
                continue
            if got["format"] == 2:
                out.append((wroot.name, p.stem, got["members"]))
    return out


def _settle_converted(ex: _execution_state._Exec) -> None:
    """Drop the greeting keys that named a converted member's library URL,
    where the member now holds them (M10). Over EVERY format-2 manifest whose
    members a format-1 one named, not only this run's: a run that stopped
    between a manifest and this pass leaves keys a rerun has to find."""
    for wid, collection, ids in _format2_manifests(ex):
        members = _converted_members(ex, wid, ids)
        if members:
            _rekey(ex, wid, collection, members, delete=True)


def _collections(ex: _execution_state._Exec) -> None:
    """Every format-1 manifest the plan listed, converted where M10 allows,
    then the greeting keys the conversions moved."""
    for rel in ex.plan.format1_collections:
        if ex.cancel():
            ex.out["outcome"] = _contracts.CANCELLED
            return
        _execution_state._attempt(ex, ex.root / rel, partial(_convert, ex, rel))
    _execution_state._attempt(ex, None, partial(_settle_converted, ex))


def _journals(ex: _execution_state._Exec) -> None:
    """Leftover format-1 harvest journals, converted or retired (M10)."""
    base = ex.root / ".cache" / "image-collection-imports"
    try:
        wids = sorted(p.name for p in base.iterdir() if p.is_dir() and paths.safe_id(p.name))
    except OSError:
        return
    for wid in wids:
        d = image_collections.raw_journal_directory(ex.root, wid)
        if image_surfaces.linked(ex.root, d):
            continue
        for p in sorted(d.glob("*.json")):
            # Asked between journals like between files: a cancel leaves the
            # rest exactly as they were, and the work map for the rerun.
            if _execution_state._stopped(ex):
                return
            _execution_state._attempt(ex, p, partial(_journal, ex, wid, p))


def _journal(ex: _execution_state._Exec, wid: str, p: Path) -> None:
    if ex.guard_dir("before-journal", p.parent):
        got = image_collection_imports.convert_or_retire(
            wid, p, guard=partial(ex.guard, "before-journal-retire"))
        if got != image_collection_imports.FORMAT2:
            ex.out["journals"][got] += 1


def _url_keys_kept(root: Path) -> int:
    """Same-world URL subject keys still waiting for a verified target."""
    count = 0
    for wroot, _gid, d in _greeting_dirs(root):
        try:
            raw = json.loads((d / _contracts._SUBJECTS).read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            continue
        count += sum(1 for k in (raw if isinstance(raw, dict) else {})
                     if image_surfaces.world_url_parts(root, wroot.name, k) is not None)
    return count
