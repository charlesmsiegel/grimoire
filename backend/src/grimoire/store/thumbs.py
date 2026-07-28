"""Lazily generated, cached downscales of stored images.

The "Appears in" gallery (and other tile shelves) render 96-154px tiles, but
the stored greeting art runs to several MB per file — a character page could
pull 100MB+ of pixels. A tile asks for `?w=320` instead and gets a small WebP.

The cache lives under home()/.cache/thumbs: derived data, safe to delete,
never scanned by the store. Entries are keyed by the source's identity and
stat, so an edited source simply maps to a new entry and the old one goes
unreferenced.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image

from .paths import home
from . import atomic

QUALITY = 80


def _cache_dir() -> Path:
    return home() / ".cache" / "thumbs"


def thumbnail(src: Path, width: int) -> Path | None:
    """Path to a cached WebP of `src` scaled to fit in width x width (never
    upscaled), generating it on first request. None if the source is missing
    or not a decodable image."""
    try:
        st = src.stat()
    except OSError:
        return None
    key = hashlib.sha256(
        f"{src}|{st.st_mtime_ns}|{st.st_size}|{width}".encode("utf-8")).hexdigest()[:32]
    out = _cache_dir() / f"{key}.webp"
    if out.exists():
        return out
    try:
        with Image.open(src) as im:
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.thumbnail((width, width))  # in-place, preserves aspect, no upscale
            out.parent.mkdir(parents=True, exist_ok=True)
            # concurrent generators just overwrite equal bytes; the shared
            # helper's random temp name also stops two threads in one process
            # colliding, which the old pid-based name did not
            with atomic.tempfile_for(out) as tmp:
                im.save(tmp, format="WEBP", quality=QUALITY)
    except Exception:  # noqa: BLE001 — undecodable/corrupt image: no thumb, caller serves original
        return None
    return out
