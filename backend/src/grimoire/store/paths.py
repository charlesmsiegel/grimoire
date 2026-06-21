"""Filesystem location + id helpers for the ~/.grimoire store."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("GRIMOIRE_HOME") or (Path.home() / ".grimoire"))


def ensure_home() -> Path:
    base = home()
    (base / "worlds").mkdir(parents=True, exist_ok=True)
    (base / "campaigns").mkdir(parents=True, exist_ok=True)
    return base


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def uniquify(base_id: str, exists: Callable[[str], bool]) -> str:
    """Return base_id, or base_id-2, base_id-3, ... until `exists` is False."""
    candidate = base_id
    n = 2
    while exists(candidate):
        candidate = f"{base_id}-{n}"
        n += 1
    return candidate
