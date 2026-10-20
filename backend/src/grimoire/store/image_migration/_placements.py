"""Ingest/placement verification, exact legacy cleanup and metadata-only occurrence execution under original holds."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import nullcontext
from pathlib import Path

from .. import assets, image_refs, image_store, image_surfaces, locks, thumbs
from . import _contracts, _execution_state, _folding, _planning, _preview

# ---- one legacy file ------------------------------------------------------------

def _write_placement(d: Path, name: str, image_id: str, focus: int | None) -> None:
    """Publish the placement (a named step, so a crash can be injected here)."""
    image_refs.write(d, name, image_id, focus=focus)


def _hashes_to(p: Path, sha: str) -> bool:
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return False
    return h.hexdigest() == sha


def _object_under(root: Path, image_id: str, blob: str | None = None) -> bool:
    """Whether `image_id`'s object is under `root` (retaining `blob`, when
    named) and its blob there re-hashes to its name."""
    obj = image_store.read_fresh(image_id, root=root)
    if obj is None or (blob is not None and obj.blob_sha256 != blob):
        return False
    return _hashes_to(image_store.blob_path(obj.blob_sha256, obj.ext, root=root),
                      obj.blob_sha256)


def _verify(root: Path, d: Path, name: str, image_id: str, blob: str | None,
            focus: int | None) -> bool:
    """M8's verify, under the pinned root and never through `path_in`: the
    placement reads back as written, its object is under `root` retaining the
    intended blob, and that blob re-hashes."""
    ref = image_refs.read(d, name)
    if ref is None or ref.image != image_id or ref.focus != focus:
        return False
    return _object_under(root, image_id, blob)


def _focus_file(d: Path) -> int | None:
    """The legacy crop in `d`'s `focus.json` (as `assets` reads it), or None
    for none, a garbled one, or one reached through a symlink."""
    p = d / assets.FOCUS_FILE
    if p.is_symlink():
        return None
    try:
        val = json.loads(p.read_text(encoding="utf-8"))
        v = val.get(assets.AVATAR) if isinstance(val, dict) else None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return max(0, min(100, int(v)))
    except (OSError, ValueError, OverflowError):
        return None


def _unlink_same(ex: _execution_state._Exec, p: Path, snap: tuple[int, int, int, int]) -> bool:
    """Unlink exactly the file that was hashed: the same regular file by
    dev, inode, size and mtime, in a directory not reached through a link."""
    if not ex.guard_dir("before-unlink", p.parent):
        return False
    st = _execution_state._lstat(p)
    if st is None or not stat.S_ISREG(st.st_mode) or _execution_state._snap(st) != snap:
        return False
    p.unlink()
    ex.wrote = True
    return True


def _drop_thumbs(ex: _execution_state._Exec, rel: str, st: os.stat_result) -> None:
    """The legacy file's current-generation thumbnails (§11.F)."""
    ex.guard("before-thumbnails")
    for key in thumbs.legacy_keys(rel, st):
        p = ex.root / key
        try:
            mode = p.stat(follow_symlinks=False).st_mode
            if stat.S_ISREG(mode) and not image_surfaces.linked(ex.root, p):
                p.unlink()
                ex.out["thumbnails_removed"] += 1
        except FileNotFoundError:
            pass
        except OSError as exc:
            # Cache cleanup is best effort: a read-only cache must not hold
            # the original image or its metadata hostage. Report and try the
            # other entries; a real interruption still leaves the source for
            # retry because cleanup remains before its unlink (#478).
            ex.out["errors"].append({"path": ex.plan.rel(p), "error": _execution_state._error_text(exc)})


def _drop_focus_file(ex: _execution_state._Exec, d: Path, moved: bool) -> bool:
    """`focus.json` beside a verified avatar placement, which answers for the
    crop from now on (M9 d). Caller holds the AVATAR name lock."""
    p = d / assets.FOCUS_FILE
    if p.is_symlink():
        ex.keep(p, assets.AVATAR, "symlinked-sidecar")
        return False
    if not p.is_file() or not ex.guard_dir("before-focus-delete", d):
        return False
    p.unlink()
    ex.wrote = True
    ex.out["focus_moved" if moved else "focus_dropped"] += 1
    return True


def _move_focus(ex: _execution_state._Exec, d: Path, focus: int | None) -> bool:
    """`focus.json` after its crop moved onto a placement written over a
    legacy avatar: dropped only while it still reads as that crop. One that
    did not read (unreadable, garbled, changed since) stays, reported: the
    placement was written without it, and it is the only copy of the crop."""
    p = d / assets.FOCUS_FILE
    if not (p.exists() or p.is_symlink()):
        return False
    if focus is None or _focus_file(d) != focus:
        ex.keep(p, assets.AVATAR, "focus-not-moved")
        return False
    return _drop_focus_file(ex, d, True)


def _reread(ex: _execution_state._Exec, item: _contracts.Item) -> bytes | None:
    """The item's file again, when it is still exactly what was hashed."""
    assert item.occurrence.path is not None
    got = _planning._read(item.occurrence.path)
    if (got is None or _execution_state._snap(got[1]) != item.stat
            or image_store.prepare(got[0], item.occurrence.path.suffix).sha != item.stream):
        ex.skip(item.occurrence.path, "changed-since-hashing")
        return None
    return got[0]


def _item(ex: _execution_state._Exec, item: _contracts.Item) -> None:
    """Ingest, place, verify, delete and fold one legacy file (M8)."""
    occ = item.occurrence
    assert occ.path is not None
    rel = ex.plan.rel(occ.path)
    if not ex.guard_dir("before-ingest", occ.dir, occ.name):
        # Not even a promotion recovery: it writes placements in there.
        ex.skip(occ.path, image_surfaces.SYMLINKED)
        return
    journal = image_refs.read_journal(occ.dir)
    if journal is not None:
        # Recovery can write BOTH named slots, not only this item's slot,
        # and rewrites `descriptions.json` and unlinks `focus.json` beside
        # them. None of those may be replaced through a link as a side
        # effect, so all four are checked junction-aware first (#478).
        name = assets._journal_ok(journal)
        if name is not None and (
                any(_execution_state._linked_slot(ex.root, occ.dir, slot) for slot in (assets.AVATAR, name))
                or any(image_surfaces.linked(ex.root, occ.dir / side)
                       for side in (assets.DESCRIPTIONS_FILE, assets.FOCUS_FILE))):
            ex.skip(occ.path, image_surfaces.SYMLINKED)
            return
    assets.recover_promotion(occ.dir)
    data = _reread(ex, item)
    if data is None:
        return
    _execution_state._pend(ex, item.image_id, rel)
    # Outside every lock: the decode is the slow part (M8.2).
    obj = image_store.ingest(data, occ.path.suffix)
    _count_added(ex, item, obj.blob_sha256)
    ex.guard("after-ingest")
    if obj.id != item.image_id:
        ex.skip(occ.path, "identity-changed")
    elif not _object_under(ex.root, obj.id, obj.blob_sha256):
        ex.skip(occ.path, "not-under-root")
    else:
        with _execution_state._campaign_write(ex, _execution_state._campaign_of(occ)), _execution_state._collection_held(occ), \
                locks.image_name_lock(occ.dir, occ.name):
            if _place_and_clean(ex, item, obj.blob_sha256):
                ex.wrote = True
    # Not in a `finally`: a process that dies mid-item leaves the entry, and
    # with it the object it ingested, a GC root until a run completes.
    _execution_state._unpend(ex, item.image_id, rel)


def _count_added(ex: _execution_state._Exec, item: _contracts.Item, blob: str) -> None:
    """Record the blob a new picture's first ingest added, once per picture,
    mirroring how the plan counted `bytes_after`."""
    g = ex.plan.groups.get(item.image_id)
    if g is None or g.existing or item.image_id in ex.added_bytes:
        return
    if not ex.stream_sizes:
        ex.stream_sizes = {i.stream: i.size for i in ex.plan.items}
    ex.added_bytes[item.image_id] = ex.stream_sizes.get(blob, item.size)


def _placement_for(ex: _execution_state._Exec, item: _contracts.Item) -> tuple[bool, int | None, bool] | None:
    """Under the name lock: `(written, focus, written over a legacy avatar)`
    once a placement of the item's picture is there -- written now when there
    was none -- or None when the slot holds another picture, which is never
    overwritten (M8.3). An image-less placement (a crop override) is filled
    with the picture under its own crop."""
    occ = item.occurrence
    assert occ.path is not None
    ref = image_refs.read(occ.dir, occ.name)
    if ref is not None and ref.image is not None:
        if ref.image != item.image_id:
            ex.plan.legacy_differs.append({"path": ex.plan.rel(occ.path), "placement": ref.image,
                                           "reason": "concurrent-placement"})
            ex.skip(occ.path, "legacy-differs")
            return None
        ex.out["already_placed"] += 1
        return False, ref.focus, False
    legacy = ref is None and occ.name == assets.AVATAR
    focus = ref.focus if ref is not None else (_focus_file(occ.dir) if legacy else None)
    _write_placement(occ.dir, occ.name, item.image_id, focus)
    ex.wrote = True
    ex.out["placed"] += 1
    return True, focus, legacy


def _place_and_clean(ex: _execution_state._Exec, item: _contracts.Item, blob: str) -> bool:
    """Everything under the item's locks; whether anything was written."""
    occ = item.occurrence
    assert occ.path is not None
    rel = ex.plan.rel(occ.path)
    now = _execution_state._lstat(occ.path)
    if not ex.guard_dir("before-place", occ.dir, occ.name):
        ex.skip(occ.path, image_surfaces.SYMLINKED)
        return False
    if now is None or not stat.S_ISREG(now.st_mode) or _execution_state._snap(now) != item.stat:
        ex.skip(occ.path, "changed-since-hashing")
        return False
    if image_refs.read_journal(occ.dir) is not None:
        # A promotion the recovery could not finish: a write now could make
        # its journal stale, and with it lose the picture it moved (spec §8).
        ex.skip(occ.path, "promotion-unfinished")
        return False
    placed = _placement_for(ex, item)
    if placed is None:
        return False
    written, focus, moved = placed
    if not _verify(ex.root, occ.dir, occ.name, item.image_id, blob, focus):
        ex.skip(occ.path, "verify-failed")
        return written
    ex.guard("after-verify")
    ex.verified.add((occ.dir, occ.name))
    _execution_state._unpend(ex, item.image_id, rel)
    # Cache first: if cleanup crashes, the legacy file still identifies the
    # thumbnail keys a rerun must remove. Unlinking first orphaned them (#478).
    _drop_thumbs(ex, rel, now)
    if _unlink_same(ex, occ.path, item.stat):
        ex.out["legacy_deleted"] += 1
        ex.deleted_bytes += item.raw_size
        written = True
    if occ.name == assets.AVATAR:
        dropped = (_move_focus(ex, occ.dir, focus) if moved
                   else _drop_focus_file(ex, occ.dir, False))
        written = dropped or written
    return _folding._fold_keys(ex, occ, item.image_id) or written


# ---- metadata-only occurrences (M6 a-d, M9) -------------------------------------------

def _target_verified(ex: _execution_state._Exec, occ: _contracts.Occurrence, image_id: str) -> bool:
    """Whether the occurrence's target placement verified: in this run, or
    now, under the pinned root."""
    if occ.target is None:
        return False
    name = occ.target_name or occ.name
    if _execution_state._linked_slot(ex.root, occ.target, name):
        return False
    # A URL would serve the post-promotion picture. The inventory cannot
    # recover it (it is read-only), so keep the key until the swap settles
    # rather than attach its subjects to the picture about to leave (#478).
    journal = image_refs.read_journal(occ.target)
    promoted = assets._journal_ok(journal) if journal is not None else None
    if promoted is not None and any(
            name == slot or _execution_state._same_file(image_refs.ref_path(occ.target, name),
                                       image_refs.ref_path(occ.target, slot))
            for slot in (assets.AVATAR, promoted)):
        return False
    ref = image_refs.read(occ.target, name)
    if ref is None or ref.image != image_id:
        return False
    return (occ.target, name) in ex.verified or _object_under(ex.root, image_id)


def _override(ex: _execution_state._Exec, occ: _contracts.Occurrence) -> bool:
    """(b): a campaign's bare `focus.json` over an inherited avatar becomes
    the image-less occurrence override, under the campaign lock (held by the
    caller) and the campaign directory's AVATAR name lock -- only while no
    avatar of the campaign's own is there."""
    d = occ.dir
    with locks.image_name_lock(d, assets.AVATAR):
        side = d / assets.FOCUS_FILE
        if image_refs.read(d, assets.AVATAR) is not None or assets.has_legacy(d, assets.AVATAR):
            ex.keep(side, assets.AVATAR, "campaign-avatar-exists")
            return False
        focus = _focus_file(d)
        if focus is None:
            ex.keep(side, assets.AVATAR, "unreadable-focus")
            return False
        if not ex.guard_dir("before-override", d, assets.AVATAR):
            ex.keep(side, assets.AVATAR, image_surfaces.SYMLINKED)
            return False
        image_refs.write(d, assets.AVATAR, None, focus=focus)
        ex.wrote = True
        if image_refs.read(d, assets.AVATAR) != image_refs.Ref(assets.AVATAR, None, focus):
            ex.keep(side, assets.AVATAR, "fold-not-confirmed")
            return True
        ex.out["overrides_written"] += 1
        if _focus_file(d) == focus and ex.guard_dir("before-focus-delete", d):
            side.unlink()
        return True


def _dropped_focus(ex: _execution_state._Exec, occ: _contracts.Occurrence) -> bool:
    """(d): the placement's focus is authoritative; `focus.json` goes, under
    the AVATAR name lock, and nothing folds."""
    with locks.image_name_lock(occ.dir, assets.AVATAR):
        ref = image_refs.read(occ.dir, assets.AVATAR)
        if ref is None or ref.image is None or _execution_state._linked_slot(ex.root, occ.dir, assets.AVATAR):
            return False            # gone meanwhile, or not ours to trust: leave the crop
        return _drop_focus_file(ex, occ.dir, False)


def _still_shown(ex: _execution_state._Exec, occ: _contracts.Occurrence) -> bool:
    """Whether a URL subject key is still one its greeting references. The
    inventory admitted it on that ground, but the greeting may have been
    edited since; folding a key nothing reads any more would surface its
    tags through the picture's other placements (#478)."""
    if occ.target_name is None:
        return True
    wroot = ex.root / "worlds" / occ.scope.partition(":")[2]
    return occ.name in image_surfaces.shown_keys(wroot, occ.dir)


def _metadata_one(ex: _execution_state._Exec, occ: _contracts.Occurrence, image_id: str | None) -> None:
    side = occ.dir / (occ.sidecar or "")
    # A verified pair can have been replaced since planning. Hold the target
    # name through revalidation, object fold and source-key deletion, so a
    # cooperating upload cannot move the URL to another image between them
    # (#478). A sync client takes no lock, so each fold also re-checks the
    # target before deleting its key. Focus operations take their own name
    # locks below.
    target_lock = (locks.image_name_lock(occ.target, occ.target_name or occ.name)
                   if occ.target is not None and occ.metadata_only in ("a", "c")
                   else nullcontext())
    with _execution_state._campaign_write(ex, _execution_state._campaign_of(occ)), target_lock:
        if image_id is None or not _target_verified(ex, occ, image_id):
            ex.keep(side, occ.name, "target-not-verified")
            return
        if not _still_shown(ex, occ):
            ex.keep(side, occ.name, "url-no-longer-shown")
            return
        if occ.metadata_only == "b":
            changed = _override(ex, occ)
        elif occ.metadata_only == "d":
            changed = _dropped_focus(ex, occ)
        elif occ.sidecar == _contracts._SUBJECTS:
            changed = _folding._fold_subjects(ex, occ.dir, occ.name, image_id, occ.scope,
                                     lambda: (_target_verified(ex, occ, image_id)
                                              and _still_shown(ex, occ)))
        else:
            changed = _folding._fold_description(ex, occ.dir, occ.name, image_id, _preview._label(ex.plan, occ),
                                        lambda: _target_verified(ex, occ, image_id))
        if changed:
            ex.wrote = True
