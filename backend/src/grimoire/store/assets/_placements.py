"""Publish-before-cleanup placement changes and lossless adoption of legacy art."""

from __future__ import annotations

from pathlib import Path

from .. import image_refs, image_store
from . import _contracts, _locking, _lookup, _metadata


def _snapshot_siblings(d: Path, name: str,
                       supported_only: bool) -> list[tuple[Path, int, int]]:
    """Every legacy file of `name`, with its identity, for `_drop_snapshotted`."""
    stale = []
    for p in _lookup.siblings(d, name, supported_only):
        try:
            st = p.stat()
            stale.append((p, st.st_dev, st.st_ino))
        except OSError:
            pass  # vanished already; nothing to clean up
    return stale


def _drop_snapshotted(stale: list[tuple[Path, int, int]]) -> None:
    """Unlink exactly the files `_snapshot_siblings` saw: the lock keeps
    concurrent callers out, and the identity check keeps anything that reaches
    the directory another way (an external tool, a sync client) from having its
    file deleted by path alone."""
    for p, dev, ino in stale:
        try:
            st = p.stat()
            if (st.st_dev, st.st_ino) != (dev, ino):
                continue  # not the file we snapshotted; not ours to delete
            p.unlink()
        except OSError:
            pass  # a lost cleanup is harmless: the placement wins over it


def _drop_if_placed(d: Path, name: str, image_id: str,
                    stale: list[tuple[Path, int, int]]) -> None:
    """`_drop_snapshotted`, only once placement `name` RESOLVES to `image_id`.

    A placement whose object or blob has not arrived (a link to an image that
    is mid-sync) is not an image to any reader yet, and the legacy file is
    what `path_in` falls back to meanwhile: redundant data beats lost data
    (spec section 6). The file goes on a later write, or stays shadowed once
    the placement resolves."""
    placed = image_refs.resolve(d, name)
    if placed is not None and placed.image_id == image_id:
        _drop_snapshotted(stale)


def _sheds_caption(d: Path, name: str, old: image_refs.Ref | None,
                   supported_only: bool, image_id: str) -> bool:
    """Whether placing `image_id` as `name` should drop the name's legacy
    description key (stage-2 ruling R12): the slot held a KNOWN, DIFFERENT
    picture, and the new one's object already carries a string description.

    Known means the old placement names an image, or -- with none -- a legacy
    file sits under the name whose identity (`image_store.identify`, the test
    `characters._legacy_identity` makes) can be read. A first placement, an
    adoption of the same picture, and a promote's `link_in` onto an empty slot
    (whose key R5 carried just before) keep the key: there the key is either
    about this very picture or the only text the reader can see.

    Cheapest question first, so an ordinary upload with no key costs one small
    read; a legacy file is decoded only when everything else says drop.
    Called before the placement is written: the legacy file is about to go.
    """
    if name not in _metadata._read_sidecar(d, _contracts.DESCRIPTIONS_FILE):
        return False
    obj = image_store.read(image_id)
    if obj is None or not isinstance(obj.raw.get("description"), str):
        return False
    if old is not None and old.image is not None:
        return old.image != image_id
    legacy = _lookup.legacy_path(d, name, supported_only)
    if legacy is None:
        return False
    try:
        held = image_store.identify(legacy.read_bytes(), legacy.suffix)
    except (OSError, ValueError):
        return False            # unreadable: not a known identity
    return held != image_id


def _place(d: Path, name: str, image_id: str, *, keep_focus: bool,
           supported_only: bool) -> None:
    """Publish placement `name` -> `image_id`, then drop the legacy siblings.

    Placement BEFORE the unlinks, as `put_in` always wrote before it cleaned
    up: a failure in between leaves a legacy file the placement shadows, never
    no image -- and the unlinks only once the placement resolves
    (`_drop_if_placed`). `keep_focus` keeps the old placement's focus;
    otherwise a focus survives only when the placement already held this very
    image. A replaced picture sheds a stale caption (`_sheds_caption`), in the
    same operation and only once the new placement is written: the decision,
    the placement and the drop under one hold of the description sidecar's
    lock, so a description saved in between cannot be the key the drop
    removes. Caller holds `_image_lock(d, name)`; image lock then sidecar lock
    is the order promotion takes them in.
    """
    stale = _snapshot_siblings(d, name, supported_only)
    with _locking.sidecar_lock(d, _contracts.DESCRIPTIONS_FILE):
        old = image_refs.read(d, name)
        shed = _sheds_caption(d, name, old, supported_only, image_id)
        focus = (old.focus if old is not None and (keep_focus or old.image == image_id)
                 else None)
        image_refs.write(d, name, image_id, focus=focus)
        if shed:
            _metadata._edit_sidecar_locked(d, _contracts.DESCRIPTIONS_FILE, {name: None})
    _drop_if_placed(d, name, image_id, stale)


def adopt_legacy(d: Path, name: str) -> str | None:
    """Turn legacy image `name` in `d` into a placement and return its id.

    - an image-bearing placement already there: its id, nothing written;
    - a legacy file: its bytes are ingested, the placement written and the
      legacy file(s) removed. The avatar's legacy crop (`focus.json`) moves
      onto the placement, unless a placement already carries one;
    - neither: None.

    `ValueError` for a legacy file of an extension this module never accepted
    (only an external tool can put one there), which is left untouched.
    """
    if not _contracts._safe_name(name) or not d.exists():
        return None
    with _locking._image_lock(d, name):
        old = image_refs.read(d, name)
        if old is not None and old.image is not None:
            return old.image
        src = _lookup.legacy_path(d, name, False)
        if src is None:
            return None
        if not _contracts.norm_ext(src.suffix):
            raise ValueError(f"unsupported image type: {src.name}")
        obj = image_store.ingest(src.read_bytes(), src.suffix)
        focus = old.focus if old is not None else None
        if name == _contracts.AVATAR and focus is None:
            focus = _metadata._read_focus_file(d)
        stale = _snapshot_siblings(d, name, False)
        image_refs.write(d, name, obj.id, focus=focus)
        _drop_if_placed(d, name, obj.id, stale)
        if name == _contracts.AVATAR:
            # The placement answers for the crop from here on (`read_focus`),
            # so a `focus.json` left behind could only ever be stale.
            _metadata._unlink_focus_file(d)
        return obj.id


def _set_placement(d: Path, name: str, image_id: str) -> None:
    """Point `name` at `image_id` with no focus, unless it already does --
    a roll-forward repeats the forward pass's writes, and a read that repairs
    should not rewrite what is already right."""
    if image_refs.read(d, name) != image_refs.Ref(name, image_id, None):
        image_refs.write(d, name, image_id)
