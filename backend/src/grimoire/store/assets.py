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
import threading
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


_registry_guard = threading.Lock()
_image_locks: dict[str, threading.RLock] = {}


def _image_lock(d: Path, name: str) -> threading.RLock:
    """Serialize writes to one logical image (all extensions of `name` in `d`).

    Cleanup is inherently multi-step -- publish the new extension, then remove
    the stale siblings -- and no filesystem offers an identity-conditional
    unlink, so "verify this is still the file I snapshotted, then delete it"
    has a gap no amount of care closes. Two concurrent uploads of different
    extensions could interleave through it and leave no image at all, which is
    the exact outcome the write-before-cleanup ordering exists to prevent
    (PR review). Serializing the sequence is what actually closes it.

    In-process only, like every other lock in this app; two processes on one
    synced store still race, as they do everywhere else.

    Reentrant, matching ``locks.campaign_lock``: a non-reentrant lock turns
    any future same-thread nesting (a delete invoked from inside a put, say)
    into a deadlock, which is a worse failure than the race it guards.

    Get-or-create under a guard: a plain ``if key not in ...`` is a
    check-then-act race that hands two first-ever callers different locks.
    """
    key = str(d / name)
    with _registry_guard:
        return _image_locks.setdefault(key, threading.RLock())


def _mtime_ns(p: Path) -> int:
    """Sort key that tolerates the file vanishing mid-scan. put_image writes
    the new extension and then unlinks the stale sibling, so a concurrent
    reader can genuinely glob a path that is gone by the time it stats -- and
    the old `sorted(...)[0]` never stat'd at all, so raising here would be a
    regression, not a new safety check."""
    try:
        return p.stat().st_mtime_ns
    except OSError:
        return -1


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
    return max(matches, key=lambda p: (_mtime_ns(p), p.name))


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
    written = d / f"{name}.{ext}"
    with _image_lock(d, name):
        # Write BEFORE dropping prior-extension files. The reverse order (which
        # this used to do) loses the image outright if anything fails between
        # the unlink and the write -- atomicity alone cannot fix an ordering
        # bug. image_path() breaks the resulting momentary tie by mtime.
        #
        # Snapshot the siblings' IDENTITY before writing, and delete only those
        # exact files: the lock keeps concurrent put_image calls out, and the
        # identity check keeps anything that reaches the directory another way
        # (an external tool, a sync client) from having its file deleted by
        # path alone.
        stale = []
        for p in d.glob(f"{name}.*"):
            if p == written:
                continue
            try:
                st = p.stat()
                stale.append((p, st.st_dev, st.st_ino))
            except OSError:
                pass  # vanished already; nothing to clean up
        atomic.write_bytes(written, data)
        for p, dev, ino in stale:
            try:
                st = p.stat()
                if (st.st_dev, st.st_ino) != (dev, ino):
                    continue  # not the file we snapshotted; not ours to delete
                p.unlink()
            except OSError:
                pass  # a lost cleanup self-heals: image_path prefers the newest
    if name == AVATAR:
        clear_focus(root, cid, vid, base)
    return ext


def delete_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        return
    d = _dir(root, cid, vid, base)
    if d.exists():
        # Same lock as put_image: a delete racing an upload must not remove the
        # file the upload just published and leave the caller thinking it wrote
        # one, nor half-remove a set the upload is mid-way through replacing.
        with _image_lock(d, name):
            for p in d.glob(f"{name}.*"):
                try:
                    p.unlink()
                except OSError:
                    pass
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
