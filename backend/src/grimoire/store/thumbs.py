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
import io
from pathlib import Path

from PIL import Image

from . import atomic
from .paths import home

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
        f"{src}|{st.st_mtime_ns}|{st.st_size}|{width}".encode()).hexdigest()[:32]
    out = _cache_dir() / f"{key}.webp"
    if out.exists():
        return out
    try:
        with Image.open(src) as im:
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            im.thumbnail((width, width))  # in-place, preserves aspect, no upscale
            out.parent.mkdir(parents=True, exist_ok=True)
            # Encode to memory, then publish through the shared writer. PIL
            # accepts a file object, so nothing ever hands out the temp's
            # *pathname* -- which is what let an attacker with write access to
            # the cache dir swap a symlink in before im.save() opened it (PR
            # review). A tile is a few KB; buffering it is free.
            buf = io.BytesIO()
            im.save(buf, format="WEBP", quality=QUALITY)
            # concurrent generators just overwrite equal bytes
            atomic.write_bytes(out, buf.getvalue())
    except Exception:  # noqa: BLE001 — undecodable/corrupt image: no thumb, caller serves original
        return None
    return out
