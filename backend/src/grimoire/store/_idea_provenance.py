"""Scene-idea provenance shapes; campaign reference resolution stays with suggest."""

from __future__ import annotations


def drivers(value) -> list[dict]:
    """Stored driver provenance as `[{ref, action}]`: a non-list is `[]`, and an
    entry without a non-empty string `ref` and a string `action` is dropped.
    Shape only -- whether the ref still names anything is `suggest`'s."""
    if not isinstance(value, list):
        return []
    return [{"ref": e["ref"], "action": e["action"]} for e in value
            if isinstance(e, dict) and isinstance(e.get("ref"), str) and e["ref"].strip()
            and isinstance(e.get("action"), str)]


def anchor(value) -> dict | None:
    """Stored `{ref, relation, native}`, or None unless all three are strings
    and `ref` is not blank."""
    if not isinstance(value, dict):
        return None
    ref, relation, native = value.get("ref"), value.get("relation"), value.get("native")
    if not (isinstance(ref, str) and ref.strip() and isinstance(relation, str)
            and isinstance(native, str)):
        return None
    return {"ref": ref, "relation": relation, "native": native}
