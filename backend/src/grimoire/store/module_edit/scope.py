"""The small helpers the rest of the package shares: the rename-collision
signal, a container's field keys, the sheet types composing a group, and the
layout fragment reachability map.

They live together in a leaf that imports nothing because their readers are
spread across four files -- ``edits`` reads ``_field_keys`` and
``_group_scope``, ``layout`` reads ``_fragment_users``, ``migrate._apply``
catches ``_RenameCollision`` and ``renaming`` raises it. Left in ``renaming``
(where the rename code that also uses them lives) those four would tie
``edits``, ``layout``, ``migrate`` and ``renaming`` into one cycle.
"""

from __future__ import annotations


class _RenameCollision(Exception):
    """Raised by a rename mutate() when the destination key already exists
    in a map-backed namespace (groups/sheet_types/checks/derived) — caught
    by _apply, which turns it into a clean ok=False result instead of
    silently overwriting the destination's definition."""


def _fragment_users(layout: dict) -> dict[str, set[str]]:
    """fragment id -> sheet-type ids that transitively reach it."""
    frags = layout.get("fragments") if isinstance(layout.get("fragments"), dict) else {}

    def uses(node) -> set[str]:
        if not isinstance(node, dict):
            return set()
        out = set()
        if isinstance(node.get("use"), str):
            out.add(node["use"])
        for arr in ("row", "column"):
            for kid in (node.get(arr) or []):
                out |= uses(kid)
        return out

    reach: dict[str, set[str]] = {}
    for tid, tree in (layout.get("sheet_types") or {}).items():
        frontier = uses(tree)
        seen: set[str] = set()
        while frontier:
            fid = frontier.pop()
            if fid in seen:
                continue
            seen.add(fid)
            frontier |= uses(frags.get(fid))
        for fid in seen:
            reach.setdefault(fid, set()).add(tid)
    return reach


def _field_keys(container: dict) -> set[str]:
    out = set()
    for f in container.get("fields", []) or []:
        if isinstance(f, dict) and isinstance(f.get("key"), str):
            out.add(f["key"])
    for name in (container.get("derived") or {}):
        if isinstance(name, str):
            out.add(name)
    return out


def _group_scope(data: dict, gid: str) -> set[str]:
    """Sheet types composing a group — the prune/rewrite scope."""
    return {tid for tid, st in data.get("sheet_types", {}).items()
            if isinstance(st, dict) and gid in (st.get("groups") or [])}
