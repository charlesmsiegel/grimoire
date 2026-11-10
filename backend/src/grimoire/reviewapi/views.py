"""JSON views of the domain reports the review UI shows (110). Pure apart from reading a file's current bytes.

Every ref is shown in its own string form, so a client hands it back unchanged. A report's own fields are
kept as the domain service names them: the UI labels them, it never reinterprets them.
"""

from __future__ import annotations

import dataclasses
import enum
import hashlib
from collections.abc import Mapping

from grimoire.changesets import unified
from grimoire.core.identity import ChatRef, ImageRef, InstanceRef, PlacementRef, ScopeRef, SourceRef

from .proposals import MAX_SHOWN_DIFF, MAX_SHOWN_TEXT

_REFS = (SourceRef, InstanceRef, ChatRef, ImageRef, PlacementRef, ScopeRef)
#: how deep a report is walked; deeper values are shown as their type's name (no report nests this deep)
_MAX_DEPTH = 24


def jsonable(value: object, depth: int = 0) -> object:
    """A JSON-ready copy of a report value: dataclasses as objects, refs as their text, tuples as lists."""
    if depth > _MAX_DEPTH:
        return f"<{type(value).__name__}>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, _REFS):
        return str(value)
    if isinstance(value, enum.Enum):
        return jsonable(value.value, depth + 1)
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: jsonable(getattr(value, f.name), depth + 1) for f in dataclasses.fields(value)
                if not f.name.startswith("_")}
    if isinstance(value, Mapping):
        return {str(jsonable(key, depth + 1)): jsonable(item, depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [jsonable(item, depth + 1) for item in value]
        if isinstance(value, (set, frozenset)):
            items.sort(key=lambda item: repr(item))
        return items
    text = str(value)
    return text if text else type(value).__name__


def _text(data: bytes | None) -> str | None:
    if data is None or len(data) > MAX_SHOWN_TEXT:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def file_views(preview: Mapping[str, bytes], current, diffs: Mapping[str, str] | None = None) -> list[dict]:
    """One row per written file, in path order: its exact size and sha256, its text when it is small UTF-8,
    whether it replaces a file (``action``: ``create``, ``replace``, ``unchanged`` or ``write`` when the
    current file cannot be read here, such as a picture blob) and a readable diff of a replaced text file.
    ``current(path)`` returns the file's bytes now, ``None`` when absent, or raises when it cannot say."""
    rows = []
    for path in sorted(preview):
        new = preview[path]
        try:
            old = current(path)
            action = "create" if old is None else "unchanged" if old == new else "replace"
        except (ValueError, OSError, LookupError):
            old, action = None, "write"
        except Exception as error:  # noqa: BLE001 - a store refusal (a look-alike spelling) is shown, never raised
            old, action = None, f"unreadable:{type(error).__name__}"
        text = _text(new)
        row = {"path": path, "action": action, "size": len(new), "sha256": hashlib.sha256(new).hexdigest(),
               "text": text, "binary": text is None and _text(new[:MAX_SHOWN_TEXT]) is None}
        diff = (diffs or {}).get(path)
        if diff is None and action == "replace" and text is not None and _text(old) is not None:
            diff = unified(path, old, path, new)
        row["diff"] = diff if diff is not None and len(diff) <= MAX_SHOWN_DIFF else None
        rows.append(row)
    return rows


def bounded(rows: list, limit: int) -> dict:
    """At most ``limit`` rows, and how many there were."""
    return {"rows": rows[:limit], "total": len(rows), "truncated": len(rows) > limit}


__all__ = ["bounded", "file_views", "jsonable"]
