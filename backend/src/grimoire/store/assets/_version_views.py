"""Version image views and the exact stamps that vouch for returned art."""

from __future__ import annotations

import os
from pathlib import Path

from .. import image_refs, image_store, statcache
from ..paths import safe_id
from . import _contracts, _legacy_rescue, _lookup, _metadata, _recovery


def image_path(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> Path | None:
    found = image_file(root, cid, vid, name, base)
    return found[0] if found is not None else None


def image_file(root: Path, cid: str, vid: str, name: str,
               base: str = "characters") -> tuple[Path, str | None] | None:
    """`image_path` with identity from the same resolution (#477).

    Recovery and legacy fallback stay here, so a URL's immutable token and its
    image id cannot describe two different reads of a changing placement.
    """
    if not (safe_id(cid) and safe_id(vid) and _contracts._safe_name(name)):
        return None
    d = _contracts._dir(root, cid, vid, base)
    if not d.exists():
        return None
    _recovery._recover_promotion(d)
    p = _lookup.file_in(d, name)
    if p is None and name == _contracts.AVATAR:
        # A promotion interrupted before #253 may have stranded the avatar under
        # `promote-tmp`; adopt it rather than serve a 404 over a file we have.
        _legacy_rescue._heal_stranded_promotion(d)
        p = _lookup.file_in(d, name)
    return p


def list_images(root: Path, cid: str, vid: str, base: str = "characters") -> list[dict]:
    if not (safe_id(cid) and safe_id(vid)):
        return []
    d = _contracts._dir(root, cid, vid, base)
    if not d.exists():
        return []
    # An interrupted promotion is finished (or its stale journal dropped)
    # before anything is listed, so the listing never shows a half-swap.
    _recovery._recover_promotion(d)
    # The "asset directory scan" the recovery in #253 was asked for: a temp the
    # old promotion stranded gets a reachable name before the listing is built,
    # so it shows up in the editor instead of staying invisible forever.
    _legacy_rescue._heal_stranded_promotion(d)
    return _lookup.list_in(d)


def version_art(root: Path, cid: str, vid: str,
                base: str = "characters") -> tuple[list[dict], int | None, tuple | None]:
    """`(list_images(...), read_focus(...), stamps)` for one version folder.

    The same two answers those functions give, plus what vouches for them in
    `statcache.memo_stamped`'s terms, so a caller memoizing a row built from a
    version's art knows which stats will tell it the art moved:

    - the folder itself, stamped before it is listed -- its mtime covers every
      image arriving, leaving or being renamed, and `focus.json` appearing;
    - each file that could answer for `AVATAR`, whose bytes are the only ones
      a row reports on (`avatar_v`); for every other image only the NAME is
      used, and names are the folder's listing;
    - `focus.json` when there is one, stamped before it is read;
    - the placements folder (``image-refs/``) when there is one, whose listing
      covers a placement arriving, leaving or being rewritten (every write is
      an atomic rename), and ``image-refs/avatar.json`` when there is one --
      the avatar's placement names the bytes `avatar_v` reports and carries
      the crop. Both stamped before the placements are read;
    - for an image-bearing avatar placement, the store's object sidecar it
      names (stamped before it is read) and the blob that resolves to -- the
      global half of `avatar_v`, which an adoption or a vanished blob moves
      without touching this folder.

    A folder that is not there is vouched for by the nearest ancestor that is,
    up to `root`: creating it moves that directory's mtime.

    The stamps are None -- "compute again next time" -- when a file vanished
    mid-read, and when the folder holds a stranded promotion or a promotion
    journal: `list_images` heals and recovers on every scan, and a repair that
    failed (a read-only store, a held file, a promotion in flight) is meant to
    be retried by the next scan rather than remembered as the answer.
    Likewise while any image-bearing placement in the folder does not resolve:
    its object or blob may sync in later without moving anything stamped here,
    and that arrival must be noticed rather than remembered as an absence.
    """
    if not (safe_id(cid) and safe_id(vid)):
        return [], None, ()
    d = _contracts._dir(root, cid, vid, base)
    here = statcache.stamp(d)
    if here is None:
        # Walk up to whatever exists; `root` itself missing leaves nothing to
        # vouch with, and an empty answer that is recomputed every time.
        up = next((s for s in map(statcache.stamp, (d.parent, d.parent.parent,
                                                     d.parent.parent.parent, root))
                   if s is not None), None)
        # The folder may have been created since that stamp; whatever the
        # reads below see, the stamp no longer matches next time.
        return (list_images(root, cid, vid, base), _metadata.read_focus(root, cid, vid, base),
                None if up is None else (up,))
    stamps = [here]
    _recovery._recover_promotion(d)            # as `list_images` does, before listing
    _legacy_rescue._heal_stranded_promotion(d)
    for p in (d / image_refs.REFS_DIR, image_refs.ref_path(d, _contracts.AVATAR)):
        ref_stamp = statcache.stamp(p)
        if ref_stamp is not None:
            stamps.append(ref_stamp)
    found = _lookup._candidates(d)
    if found is None:
        return [], _metadata.read_focus(root, cid, vid, base), None
    files, refs = found
    avatar_ok = _stamp_avatar_object(refs, stamps)
    resolved, complete = _lookup._resolve_refs(refs)
    avatar_ok = _stamp_avatar_blob(resolved, stamps) and avatar_ok
    cacheable = (_restamp_avatar(d, files, stamps) and not _lookup._stranded(files)
                 and complete and avatar_ok and image_refs.read_journal(d) is None)
    focus_stamp = statcache.stamp(d / _contracts.FOCUS_FILE)
    if focus_stamp is not None:
        stamps.append(focus_stamp)
    focus = _metadata.read_focus(root, cid, vid, base)
    return _lookup._listing(found, resolved), focus, (tuple(stamps) if cacheable else None)


def _stamp_avatar_object(refs: dict[str, image_refs.Ref], stamps: list) -> bool:
    """Stamp the store sidecar of the object the avatar placement names, onto
    `stamps`, BEFORE it is read (`_resolve_refs`). False when there is one to
    stamp and it cannot be statted.

    The sidecar is global state no version folder's stat covers: an adoption
    rewrites the blob it retains -- the bytes `avatar_v` names -- without
    moving anything under `d`."""
    ref = refs.get(_contracts.AVATAR)
    if ref is None or ref.image is None:
        return True
    s = statcache.stamp(image_store.object_path(ref.image))
    if s is None:
        return False
    stamps.append(s)
    return True


def _stamp_avatar_blob(resolved: dict[str, image_refs.ResolvedImage], stamps: list) -> bool:
    """Stamp the blob a resolved avatar placement reports, onto `stamps`: its
    vanishing (a partial GC, a sync) must be noticed, not remembered as there.
    False when it cannot be statted. Only the avatar's: every other image
    contributes a NAME to a row, never bytes."""
    r = resolved.get(_contracts.AVATAR)
    if r is None:
        return True
    s = statcache.stamp(r.blob_path)
    if s is None:
        return False
    stamps.append(s)
    return True


def _restamp_avatar(d: Path, found: _lookup._Files, stamps: list) -> bool:
    """Stamp every LEGACY file that could answer for `AVATAR`, onto `stamps`, and
    rebuild its candidates in `found` from those stamps. False when one of
    them vanished since the scan (the listing then simply lacks it).

    From the STAMPS, so the token a row reports and the stat that vouches for
    it are one stat: stamping after the scan instead would let a rewrite
    between the two store the new stamp beside the old token. Not the scan's
    own stat either: `DirEntry.stat()` has no inode on Windows, so it would
    never equal the `os.stat` a later call compares it with.
    """
    ok = True
    fresh = []
    for name, _mtime, _size in found.get(_contracts.AVATAR, []):
        s = statcache.stamp(os.path.join(d, name))
        if s is None:
            ok = False
            continue
        stamps.append(s)
        fresh.append((name, s[1], s[3]))
    if fresh:
        found[_contracts.AVATAR] = fresh
    else:
        found.pop(_contracts.AVATAR, None)
    return ok


def image_id(root: Path, cid: str, vid: str, name: str,
             base: str = "characters") -> str | None:
    """The image id placement `name` of this version resolves to, or None
    (no placement, an image-less one, a legacy file, or not resolvable)."""
    if not (safe_id(cid) and safe_id(vid) and _contracts._safe_name(name)):
        return None
    r = _lookup.resolve(_contracts._dir(root, cid, vid, base), name)
    return r.image_id if r is not None else None
