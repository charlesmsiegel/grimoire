"""Image directory fork: hardlink probe + deep-copy fallback.

The probe creates a sentinel file in the source images dir and tries to
hardlink it into the destination. If that succeeds, every subsequent
image is hardlinked too. If the probe fails (cross-device, Windows
without ``SeCreateSymbolicLinkPrivilege``, exotic FS errors), the whole
run falls back to ``shutil.copy2``. A per-file hardlink failure
mid-run downgrades the result to ``"mixed"``.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ImageHandlingResult:
    handling: str  # "hardlink" | "deep_copy" | "mixed"
    files_copied: int


_SENTINEL_NAME = ".sentinel-fork-probe"


async def fork_image_files(original_dir: Path, new_dir: Path) -> ImageHandlingResult:
    """Mirror ``original_dir / "images"`` to ``new_dir / "images"``.

    ``original_dir`` and ``new_dir`` are the campaign-root directories
    (not the ``images/`` subdirectories themselves).
    """
    src_images = original_dir / "images"
    dst_images = new_dir / "images"
    dst_images.mkdir(parents=True, exist_ok=True)

    if not src_images.exists():
        return ImageHandlingResult(handling="hardlink", files_copied=0)

    handling = _probe(src_images, dst_images)

    files_copied = 0
    downgraded = False
    for src in src_images.rglob("*"):
        if src.is_dir():
            continue
        if src.name == _SENTINEL_NAME:
            continue
        rel = src.relative_to(src_images)
        dst = dst_images / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst.unlink()
        if handling == "hardlink":
            try:
                os.link(src, dst)
            except OSError:
                shutil.copy2(src, dst)
                downgraded = True
        else:
            shutil.copy2(src, dst)
        files_copied += 1

    if downgraded and handling == "hardlink":
        handling = "mixed"
    return ImageHandlingResult(handling=handling, files_copied=files_copied)


def _probe(src_images: Path, dst_images: Path) -> str:
    """Return ``"hardlink"`` if ``os.link`` works between the two dirs."""
    sentinel_src = src_images / _SENTINEL_NAME
    sentinel_dst = dst_images / _SENTINEL_NAME
    try:
        sentinel_src.touch(exist_ok=True)
    except OSError:
        return "deep_copy"
    try:
        if sentinel_dst.exists():
            sentinel_dst.unlink()
        os.link(sentinel_src, sentinel_dst)
        return "hardlink"
    except (OSError, PermissionError):
        return "deep_copy"
    finally:
        sentinel_src.unlink(missing_ok=True)
        sentinel_dst.unlink(missing_ok=True)
