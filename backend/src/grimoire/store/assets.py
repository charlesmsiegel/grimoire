"""Per-version character image store: characters/<cid>/assets/<vid>/<name>.<ext>.

The avatar is the image named AVATAR. Other image kinds (emotions, backgrounds,
…) drop into the same per-version folder with no schema change. Images are never
hashed into the card, so character sync is untouched by image edits.
"""

from __future__ import annotations

from pathlib import Path

AVATAR = "avatar"
_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _safe(part: str) -> bool:
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _safe_name(name: str) -> bool:
    return _safe(name) and "." not in name


def _norm_ext(ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return ext if ext in _EXTS else ""


def _dir(root: Path, cid: str, vid: str) -> Path:
    return root / "characters" / cid / "assets" / vid


def image_path(root: Path, cid: str, vid: str, name: str) -> Path | None:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        return None
    d = _dir(root, cid, vid)
    if not d.exists():
        return None
    matches = sorted(d.glob(f"{name}.*"))
    return matches[0] if matches else None


def list_images(root: Path, cid: str, vid: str) -> list[dict]:
    if not (_safe(cid) and _safe(vid)):
        return []
    d = _dir(root, cid, vid)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix:
            out.append({"name": p.stem, "ext": p.suffix.lstrip(".").lower()})
    return out


def put_image(root: Path, cid: str, vid: str, name: str, data: bytes, ext: str) -> str:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        raise ValueError("unsafe image id")
    ext = _norm_ext(ext)
    if not ext:
        raise ValueError("unsupported image type")
    d = _dir(root, cid, vid)
    d.mkdir(parents=True, exist_ok=True)
    for p in d.glob(f"{name}.*"):  # drop any prior-ext file of this name
        p.unlink()
    (d / f"{name}.{ext}").write_bytes(data)
    return ext


def delete_image(root: Path, cid: str, vid: str, name: str) -> None:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        return
    d = _dir(root, cid, vid)
    if d.exists():
        for p in d.glob(f"{name}.*"):
            p.unlink()
