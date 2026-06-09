"""Atomic text writes: write to a temp file, then rename over the target.

``os.replace`` is atomic for same-directory renames on POSIX and Windows, so
readers never observe a torn file and a failure mid-write leaves the target
untouched. The temp file lives next to the target (same filesystem) and ends
in ``.tmp`` so suffix-keyed directory watchers (``.md`` / ``.yaml``) ignore it.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write_text(path: str | Path, text: str) -> None:
    """Write ``text`` to ``path`` as UTF-8 via write-then-rename.

    Creates parent directories if needed. Either the target ends up with the
    full new content or it is left exactly as it was.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
