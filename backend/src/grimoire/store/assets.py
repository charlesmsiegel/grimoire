"""Per-version image store: <base>/<cid>/assets/<vid>/<name>.<ext>.

The default base is "characters"; entity kinds (locations, lore) pass base=kind
with vid="default" so records without versions get the same folder layout. The
avatar/primary image is the image named AVATAR. Other image kinds (gallery,
emotions, backgrounds, …) drop into the same per-version folder with no schema
change. Images are never hashed into the card, so character sync is untouched
by image edits.
"""

from __future__ import annotations

import json
from pathlib import Path
from . import atomic

AVATAR = "avatar"
FOCUS_FILE = "focus.json"
_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _safe(part: str) -> bool:
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _safe_name(name: str) -> bool:
    # reject "." (ambiguous with ext) and glob metacharacters (the cleanup/lookup globs name.*)
    return _safe(name) and "." not in name and not any(c in name for c in "*?[]")


def _norm_ext(ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return ext if ext in _EXTS else ""


def _dir(root: Path, cid: str, vid: str, base: str = "characters") -> Path:
    return root / base / cid / "assets" / vid


def image_path(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> Path | None:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        return None
    d = _dir(root, cid, vid, base)
    if not d.exists():
        return None
    matches = sorted(d.glob(f"{name}.*"))
    if not matches:
        return None
    # Newest wins, not alphabetically-first. put_image writes the new file
    # before unlinking stale other-extension siblings (so a crash can't lose
    # the image), which leaves both present for a moment -- and a plain
    # sorted()[0] would hand back the stale one. Also self-heals if that
    # unlink ever fails.
    return max(matches, key=lambda p: (p.stat().st_mtime_ns, p.name))


def list_images(root: Path, cid: str, vid: str, base: str = "characters") -> list[dict]:
    if not (_safe(cid) and _safe(vid)):
        return []
    d = _dir(root, cid, vid, base)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.iterdir()):
        if p.is_file() and _norm_ext(p.suffix):
            out.append({"name": p.stem, "ext": p.suffix.lstrip(".").lower(),
                        "v": image_version(p)})
    return out


def image_version(p: Path) -> str:
    """Cache-busting token for an image file's current bytes; a `?v=` URL
    carrying it is served immutable, so the browser never revalidates."""
    st = p.stat()
    return f"{st.st_mtime_ns:x}-{st.st_size:x}"


def read_focus(root: Path, cid: str, vid: str, base: str = "characters") -> int | None:
    """Avatar crop focus: 0-100 along the image's long axis; None = center."""
    if not (_safe(cid) and _safe(vid)):
        return None
    p = _dir(root, cid, vid, base) / FOCUS_FILE
    if not p.exists():
        return None
    try:
        val = json.loads(p.read_text(encoding="utf-8")).get(AVATAR)
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return max(0, min(100, int(val)))


def write_focus(root: Path, cid: str, vid: str, focus: int, base: str = "characters") -> None:
    if not (_safe(cid) and _safe(vid)):
        raise ValueError("unsafe image id")
    d = _dir(root, cid, vid, base)
    d.mkdir(parents=True, exist_ok=True)
    atomic.write_text(d / FOCUS_FILE, json.dumps({AVATAR: max(0, min(100, int(focus)))}))


def clear_focus(root: Path, cid: str, vid: str, base: str = "characters") -> None:
    if not (_safe(cid) and _safe(vid)):
        return
    p = _dir(root, cid, vid, base) / FOCUS_FILE
    if p.exists():
        p.unlink()


def put_image(root: Path, cid: str, vid: str, name: str, data: bytes, ext: str,
              base: str = "characters") -> str:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        raise ValueError("unsafe image id")
    ext = _norm_ext(ext)
    if not ext:
        raise ValueError("unsupported image type")
    d = _dir(root, cid, vid, base)
    d.mkdir(parents=True, exist_ok=True)
    # Write BEFORE dropping prior-extension files. The reverse order (which
    # this used to do) loses the image outright if anything fails between the
    # unlink and the write -- atomicity alone cannot fix an ordering bug.
    # image_path() breaks the resulting momentary tie by mtime.
    written = d / f"{name}.{ext}"
    atomic.write_bytes(written, data)
    for p in d.glob(f"{name}.*"):
        if p != written:
            p.unlink()
    if name == AVATAR:
        clear_focus(root, cid, vid, base)
    return ext


def delete_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        return
    d = _dir(root, cid, vid, base)
    if d.exists():
        for p in d.glob(f"{name}.*"):
            p.unlink()
    if name == AVATAR:
        clear_focus(root, cid, vid, base)


def promote_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    """Make <name> the avatar; the old avatar takes <name>'s slot (swap, nothing lost)."""
    if name == AVATAR:
        return
    src = image_path(root, cid, vid, name, base)
    if src is None:
        raise FileNotFoundError(name)
    cur = image_path(root, cid, vid, AVATAR, base)
    d = _dir(root, cid, vid, base)
    tmp = d / f"promote-tmp{src.suffix}"
    src.rename(tmp)
    if cur is not None:
        cur.rename(d / f"{name}{cur.suffix}")
    tmp.rename(d / f"{AVATAR}{src.suffix}")
    clear_focus(root, cid, vid, base)
