"""Per-greeting image subjects — which characters appear in each localized
greeting image. Sidecar at <root>/greetings/<gid>/assets/default/subjects.json
(the focus.json pattern): {"<image-name>": ["<cid>", ...]}. Tolerant reads,
strict writes. Deliberately named "subjects", not "tags" — tags mean
player-trait gating elsewhere in the store.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import assets, atomic, characters

SUBJECTS_FILE = "subjects.json"
_BASE = "greetings"
_VID = "default"


def subjects_path(root: Path, gid: str) -> Path:
    return root / _BASE / gid / "assets" / _VID / SUBJECTS_FILE


def _image_names(root: Path, gid: str) -> set[str]:
    return {i["name"] for i in assets.list_images(root, gid, _VID, base=_BASE)}


def _read_raw(root: Path, gid: str) -> dict:
    """The sidecar as stored: {} on missing/garbled file, no filtering."""
    p = subjects_path(root, gid)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def read_subjects(root: Path, gid: str, known_cids: set[str] | None = None) -> dict[str, list[str]]:
    """Tolerant: {} on missing/garbled file; vanished images and deleted
    characters drop out silently (no dangling chips). An entry that empties
    out stays as [] — it still means 'reviewed'. Sweeps over many greetings
    pass `known_cids` so character ids are enumerated once, not per greeting."""
    raw = _read_raw(root, gid)
    if not raw:
        return {}
    names = _image_names(root, gid)
    cids = set(characters.character_refs(root)) if known_cids is None else known_cids
    out: dict[str, list[str]] = {}
    for name, subs in raw.items():
        if name not in names or not isinstance(subs, list):
            continue
        # `isinstance(c, str)` FIRST, and not for tidiness: `c in cids` against a
        # set raises TypeError for an unhashable member, so a hand-edited or
        # half-synced sidecar holding a nested list or object took the caller
        # down. That was survivable while every caller read one greeting; the
        # world gallery reads them all, so one malformed member 500'd the whole
        # Images view. Same tolerance the rest of this module reads with.
        out[name] = [c for c in subs if isinstance(c, str) and c in cids]
    return out


def write_subjects(root: Path, gid: str, subjects: dict[str, list[str]]) -> None:
    """Strict: every key must be a stored image of this greeting. An explicit
    empty list persists — it means 'reviewed, no subjects' and keeps the image
    out of the untagged queue (key absent = unreviewed)."""
    names = _image_names(root, gid)
    unknown = set(subjects) - names
    if unknown:
        raise ValueError(f"unknown image(s): {sorted(unknown)}")
    trimmed = {n: list(subs) for n, subs in subjects.items()}
    p = subjects_path(root, gid)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(p, json.dumps(trimmed, indent=2, sort_keys=True) + "\n")


def set_image_subjects(root: Path, gid: str, name: str, cids: list[str]) -> None:
    """Read-modify-write of one image's entry (raw read: preserves entries for
    images we aren't touching even if their character was deleted)."""
    p = subjects_path(root, gid)
    cur: dict[str, list[str]] = {}
    if p.exists():
        try:
            loaded = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cur = loaded
        except (json.JSONDecodeError, OSError):
            cur = {}
    cur[name] = list(cids)
    write_subjects(root, gid, cur)


def reviewed_names(root: Path, gid: str) -> set[str]:
    """Which of this greeting's images have been ANSWERED — key presence alone.

    Public, and shared by the two listings that turn on it, because they must
    agree: the tagging queue offers what is not here, and the world gallery
    (#200) marks what is not here as unfinished. `read_subjects` cannot answer
    it, and that is the point -- it drops an entry whose value is not a list, so
    a hand-edited or half-synced sidecar reads there as untagged while the queue
    considers it done, leaving an unfinished tile with no way to resolve it.
    """
    return set(_read_raw(root, gid))


def untagged(root: Path) -> list[dict]:
    """Every stored greeting image with NO sidecar entry — the tagging queue.
    Key absent = unreviewed; an explicit [] counts as reviewed."""
    out: list[dict] = []
    gdir = root / _BASE
    if not gdir.exists():
        return out
    for d in sorted(p for p in gdir.iterdir() if p.is_dir()):
        gid = d.name
        reviewed = reviewed_names(root, gid)
        for name in sorted(_image_names(root, gid)):
            if name not in reviewed:
                out.append({"gid": gid, "name": name})
    return out


def appearances(root: Path, cid: str) -> list[dict]:
    """Every tagged image featuring `cid`, across all greetings — the
    character page's 'Appears in' gallery. Cheap: ~one small file per
    greeting. Sorted by (gid, name) = the greetings tab's order."""
    out: list[dict] = []
    gdir = root / _BASE
    if not gdir.exists() or cid not in characters.character_refs(root):
        return out
    known = {cid}  # only this character's membership matters; skip re-filtering the rest
    for p in sorted(gdir.glob(f"*/assets/{_VID}/{SUBJECTS_FILE}")):
        gid = p.parents[2].name
        for name, subs in sorted(read_subjects(root, gid, known_cids=known).items()):
            if cid in subs:
                out.append({"gid": gid, "name": name})
    return out


def copy_to_character(root: Path, gid: str, name: str, cid: str, vid: str, slot: str,
                      src_root: Path | None = None, taken_names: set[str] | None = None) -> str:
    """Copy a greeting image's bytes into a character version's assets.
    slot 'avatar' overwrites the avatar (focus resets, per put_image);
    slot 'gallery' takes the next free gallery_N. Returns the stored name.
    `src_root` defaults to `root`; a campaign caller passes the overlay-resolved
    root so an inherited (unmaterialized) greeting image can still be copied,
    while the destination character write always lands under `root`.
    `taken_names` overrides the free-slot scan: a campaign caller must pass
    the overlay-resolved union (overlay.list_images) so an inherited world
    gallery image can't be silently shadowed by a reused gallery_N name."""
    if slot not in ("avatar", "gallery"):
        raise ValueError(f"unknown slot: {slot}")
    src = assets.image_path(root if src_root is None else src_root, gid, _VID, name, base=_BASE)
    if src is None:
        raise FileNotFoundError(name)
    raw, ext = src.read_bytes(), src.suffix.lstrip(".")
    if slot == "avatar":
        assets.put_image(root, cid, vid, assets.AVATAR, raw, ext)
        return assets.AVATAR
    taken = ({i["name"] for i in assets.list_images(root, cid, vid)}
             if taken_names is None else taken_names)
    n = 1
    while f"gallery_{n}" in taken:
        n += 1
    assets.put_image(root, cid, vid, f"gallery_{n}", raw, ext)
    return f"gallery_{n}"
