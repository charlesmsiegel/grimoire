"""Low-level description mappings and avatar crop ownership, without recovery recursion."""

from __future__ import annotations

import json
from pathlib import Path

from .. import atomic, image_refs
from ..paths import safe_id
from . import _contracts, _locking


def _clamp_focus(val: object) -> int | None:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return max(0, min(100, int(val)))


def _read_focus_file(d: Path) -> int | None:
    """The legacy crop: `focus.json` in `d`, or None."""
    p = d / _contracts.FOCUS_FILE
    if not p.exists():
        return None
    try:
        val = json.loads(p.read_text(encoding="utf-8")).get(_contracts.AVATAR)
    except (json.JSONDecodeError, AttributeError):
        return None
    return _clamp_focus(val)


def _unlink_focus_file(d: Path) -> None:
    p = d / _contracts.FOCUS_FILE
    if p.exists():
        p.unlink()


def _avatar_ref(d: Path) -> image_refs.Ref | None:
    """The avatar placement in `d`, statted before it is opened: most version
    folders have none, and a listing asks this of every row it rebuilds."""
    if not image_refs.ref_path(d, _contracts.AVATAR).exists():
        return None
    return image_refs.read(d, _contracts.AVATAR)


def owns_focus(root: Path, cid: str, vid: str, base: str = "characters") -> bool:
    """Whether this version folder carries a crop record of its own: an avatar
    placement (with or without an image -- an image-less one is the occurrence
    override a campaign sets on an avatar it inherits) or a legacy
    `focus.json`. `overlay.read_focus`, and the campaign listing that has to
    agree with it, read the crop from whichever root owns one."""
    if not (safe_id(cid) and safe_id(vid)):
        return False
    d = _contracts._dir(root, cid, vid, base)
    return _avatar_ref(d) is not None or (d / _contracts.FOCUS_FILE).exists()


def read_focus(root: Path, cid: str, vid: str, base: str = "characters") -> int | None:
    """Avatar crop focus: 0-100 along the image's long axis; None = center.

    An avatar placement, image-bearing or not, owns the crop when there is
    one -- its `focus`, None included; otherwise the legacy `focus.json`."""
    if not (safe_id(cid) and safe_id(vid)):
        return None
    d = _contracts._dir(root, cid, vid, base)
    ref = _avatar_ref(d)
    if ref is not None:
        return ref.focus
    return _read_focus_file(d)


def clear_focus(root: Path, cid: str, vid: str, base: str = "characters") -> None:
    """Drop the avatar crop: from the placement (an image-less placement goes
    entirely) and the legacy `focus.json`."""
    if not (safe_id(cid) and safe_id(vid)):
        return
    _clear_focus_in(_contracts._dir(root, cid, vid, base))


def _clear_focus_in(d: Path) -> None:
    """`clear_focus` for a directory already in hand."""
    if not d.exists():
        return
    with _locking._image_lock(d, _contracts.AVATAR):
        ref = image_refs.read(d, _contracts.AVATAR)
        if ref is not None and ref.focus is not None:
            image_refs.write(d, _contracts.AVATAR, ref.image)
        _unlink_focus_file(d)


def _read_sidecar(d: Path, filename: str) -> dict:
    """A ``{name: value}`` sidecar in `d`, or ``{}`` for missing or garbled."""
    p = d / filename
    if not p.exists():
        return {}
    try:
        cur = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}
    return cur if isinstance(cur, dict) else {}


def edit_sidecar(d: Path, filename: str, changes: dict[str, str | None]) -> None:
    """Apply `changes` to a ``{name: value}`` sidecar in `d`; ``None`` removes.

    Image lifecycle events have to take the sidecar entries with them, and this
    module is where they happen. It cannot call the sidecar's own module to do
    it: `image_descriptions` enumerates its directory through `assets.list_in`,
    so the import would be a cycle -- and a deferred import to dodge that is
    exactly what `tests/test_import_guard.py` exists to refuse.

    So the split is by *layer*, not by file: this edits a flat JSON mapping,
    knowing nothing about what the values mean, and the owning module keeps
    every rule about them.

    SEVERAL changes at once, because promotion needs two: a swap applied as two
    writes is a window in which one picture holds the other's description.

    Silent on a missing, garbled or unwritable sidecar, matching `clear_focus`:
    these run *after* the bytes have moved, and an image operation must not fail
    over the file that annotates it. A no-op change set never creates a file.
    """
    with _locking.sidecar_lock(d, filename):
        _edit_sidecar_locked(d, filename, changes)


def _edit_sidecar_locked(d: Path, filename: str, changes: dict[str, str | None]) -> None:
    cur = _read_sidecar(d, filename)
    wanted = {k: v for k, v in changes.items() if v is not None}
    dropped = {k for k, v in changes.items() if v is None}
    if not any(cur.get(k) != v for k, v in wanted.items()) and not (dropped & set(cur)):
        return
    cur = {k: v for k, v in cur.items() if k not in dropped}
    cur.update(wanted)
    p = d / filename
    try:
        if cur:
            atomic.write_text(p, json.dumps(cur, indent=2, sort_keys=True) + "\n")
        elif p.exists():
            p.unlink()
    except OSError:
        pass


def drop_sidecar_entry(d: Path, filename: str, key: str) -> None:
    """Remove `key` from a ``{name: value}`` sidecar in `d`, if it is there."""
    edit_sidecar(d, filename, {key: None})
