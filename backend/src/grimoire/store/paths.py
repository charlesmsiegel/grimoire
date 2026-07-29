"""Filesystem location + id helpers for the ~/.grimoire store."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from . import atomic

DEFAULT_HOME = Path.home() / ".grimoire"


def _pointer_path() -> Path:
    """Fixed location of the bootstrap pointer that records the data dir.

    This must live *outside* the data dir itself — the data dir is what it
    points at, so it cannot also store the pointer (chicken/egg). It sits
    beside the default store as a sibling dotfile.
    """
    return Path.home() / ".grimoire.json"


def _read_pointer() -> dict:
    path = _pointer_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _pointer_data_dir() -> Path | None:
    raw = _read_pointer().get("data_dir")
    return Path(raw).expanduser() if raw else None


def home() -> Path:
    """Resolve the data root.

    Order: ``GRIMOIRE_HOME`` env var (override / test isolation) → the
    user-chosen path from the bootstrap pointer → the default ``~/.grimoire``.
    Resolved live on every call so a path change takes effect immediately.
    """
    env = os.environ.get("GRIMOIRE_HOME")
    if env:
        return Path(env)
    pointer = _pointer_data_dir()
    if pointer:
        return pointer
    return DEFAULT_HOME


def ensure_home() -> Path:
    base = home()
    (base / "worlds").mkdir(parents=True, exist_ok=True)
    (base / "campaigns").mkdir(parents=True, exist_ok=True)
    return base


def set_data_dir(path: str | Path | None) -> Path:
    """Persist the data dir to the bootstrap pointer and return the new root.

    A falsy ``path`` clears the override, reverting to the default. The target
    directory (and its ``worlds``/``campaigns`` subtrees) is created if missing.
    Raises ``ValueError`` if the target exists but is not a directory.
    """
    pointer = _pointer_path()
    data = _read_pointer()

    if not path or not str(path).strip():
        data.pop("data_dir", None)
        atomic.write_text(pointer, json.dumps(data, indent=2) + "\n")
        return ensure_home()

    resolved = Path(str(path).strip()).expanduser()
    if resolved.exists() and not resolved.is_dir():
        raise ValueError(f"{resolved} exists but is not a directory")
    resolved.mkdir(parents=True, exist_ok=True)

    data["data_dir"] = str(resolved)
    pointer.parent.mkdir(parents=True, exist_ok=True)
    atomic.write_text(pointer, json.dumps(data, indent=2) + "\n")
    return ensure_home()


def data_dir_info() -> dict:
    """Describe the active data dir for the settings UI."""
    env = os.environ.get("GRIMOIRE_HOME")
    pointer = _pointer_data_dir()
    current = home()
    return {
        "data_dir": str(current),
        "default": str(DEFAULT_HOME),
        "is_default": not env and pointer is None,
        "source": "env" if env else ("custom" if pointer else "default"),
        "exists": current.exists(),
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def safe_id(value: object) -> bool:
    """Reject ids that could escape their parent directory (defense in depth).

    For every value this accepts, ``parent / value`` names a direct child of
    ``parent``: no path separator, no ``.`` or ``..``, no empty string (which
    would resolve to ``parent`` itself), and no colon -- on Windows a
    drive-relative id replaces the base outright (``Path("store") / "C:evil"``
    is ``C:evil``), and any colon names an NTFS alternate data stream.
    Non-strings are rejected too, so ids read back out of on-disk JSON need no
    separate type check.

    Every id-to-path resolver in the store goes through this one function --
    it used to be copy-pasted per module, and the copies that were never made
    were exactly the resolvers that lacked the guard (#240).
    """
    return (isinstance(value, str) and value not in ("", ".", "..")
            and not any(c in value for c in "/\\:"))


def natural_key(text: str) -> tuple:
    """Sort key that orders digit runs numerically: A2 before A10, SoL 2 before
    SoL 19. Case-insensitive. Splitting on digit runs keeps types aligned
    (str at even positions, int at odd), so mixed keys always compare."""
    return tuple(int(tok) if tok.isdigit() else tok.lower()
                 for tok in re.split(r"(\d+)", text))


def uniquify(base_id: str, exists: Callable[[str], bool]) -> str:
    """Return base_id, or base_id-2, base_id-3, ... until `exists` is False."""
    candidate = base_id
    n = 2
    while exists(candidate):
        candidate = f"{base_id}-{n}"
        n += 1
    return candidate
