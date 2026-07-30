"""The staged pack's JSON files: read one, write one, read ``sheets.json``
with its two containers defaulted.

A shared leaf, not a home for behaviour: ``edits``, ``layout`` and ``renaming``
all reach for these three, and giving them their own file is what keeps those
three from importing each other.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import atomic


def _read_json(root: Path, name: str) -> dict:
    p = root / name
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(root: Path, name: str, data: dict) -> None:
    atomic.write_text(root / name, json.dumps(data, indent=2) + "\n")


def _read_sheets(root: Path) -> dict:
    data = _read_json(root, "sheets.json")
    data.setdefault("groups", {})
    data.setdefault("sheet_types", {})
    return data
