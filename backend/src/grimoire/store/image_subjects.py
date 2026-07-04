"""Per-greeting image subjects — which characters appear in each localized
greeting image. Sidecar at <root>/greetings/<gid>/assets/default/subjects.json
(the focus.json pattern): {"<image-name>": ["<cid>", ...]}. Tolerant reads,
strict writes. Deliberately named "subjects", not "tags" — tags mean
player-trait gating elsewhere in the store.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import assets, characters

SUBJECTS_FILE = "subjects.json"
_BASE = "greetings"
_VID = "default"


def subjects_path(root: Path, gid: str) -> Path:
    return root / _BASE / gid / "assets" / _VID / SUBJECTS_FILE


def _image_names(root: Path, gid: str) -> set[str]:
    return {i["name"] for i in assets.list_images(root, gid, _VID, base=_BASE)}


def read_subjects(root: Path, gid: str) -> dict[str, list[str]]:
    """Tolerant: {} on missing/garbled file; entries for vanished images and
    deleted characters drop out silently (no dangling chips)."""
    p = subjects_path(root, gid)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    names = _image_names(root, gid)
    cids = {c["id"] for c in characters.list_characters(root)}
    out: dict[str, list[str]] = {}
    for name, subs in raw.items():
        if name not in names or not isinstance(subs, list):
            continue
        kept = [c for c in subs if c in cids]
        if kept:
            out[name] = kept
    return out


def write_subjects(root: Path, gid: str, subjects: dict[str, list[str]]) -> None:
    """Strict: every key must be a stored image of this greeting. Empty lists
    are dropped."""
    names = _image_names(root, gid)
    unknown = set(subjects) - names
    if unknown:
        raise ValueError(f"unknown image(s): {sorted(unknown)}")
    trimmed = {n: list(subs) for n, subs in subjects.items() if subs}
    p = subjects_path(root, gid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trimmed, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def appearances(root: Path, cid: str) -> list[dict]:
    """Every tagged image featuring `cid`, across all greetings — the
    character page's 'Appears in' gallery. Cheap: ~one small file per
    greeting. Sorted by (gid, name) = the greetings tab's order."""
    out: list[dict] = []
    gdir = root / _BASE
    if not gdir.exists():
        return out
    for p in sorted(gdir.glob(f"*/assets/{_VID}/{SUBJECTS_FILE}")):
        gid = p.parents[2].name
        for name, subs in sorted(read_subjects(root, gid).items()):
            if cid in subs:
                out.append({"gid": gid, "name": name})
    return out


def copy_to_character(root: Path, gid: str, name: str, cid: str, vid: str, slot: str) -> str:
    """Copy a greeting image's bytes into a character version's assets.
    slot 'avatar' overwrites the avatar (focus resets, per put_image);
    slot 'gallery' takes the next free gallery_N. Returns the stored name."""
    if slot not in ("avatar", "gallery"):
        raise ValueError(f"unknown slot: {slot}")
    src = assets.image_path(root, gid, _VID, name, base=_BASE)
    if src is None:
        raise FileNotFoundError(name)
    raw, ext = src.read_bytes(), src.suffix.lstrip(".")
    if slot == "avatar":
        assets.put_image(root, cid, vid, assets.AVATAR, raw, ext)
        return assets.AVATAR
    taken = {i["name"] for i in assets.list_images(root, cid, vid)}
    n = 1
    while f"gallery_{n}" in taken:
        n += 1
    assets.put_image(root, cid, vid, f"gallery_{n}", raw, ext)
    return f"gallery_{n}"
