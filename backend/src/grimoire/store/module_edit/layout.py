"""Layout tree editing: the specialization walk over the transitive `use`
graph, the cascade-cosmetic prune, and the rename edit_fn both share.
"""

from __future__ import annotations

import json
from collections.abc import Set as AbstractSet
from pathlib import Path

from .packfile import _read_json, _write_json
from .scope import _fragment_users

# ---- layout specialization over the transitive `use` graph ----
# (Task 6 reuses these three for renames instead of redefining them.)


def _edit_tree(node, edit_fn, remap: dict[str, str]):
    """Apply edit_fn to a node tree, remapping `use` refs per `remap`."""
    node = edit_fn(node)
    if not isinstance(node, dict):
        return node
    out = dict(node)
    if isinstance(out.get("use"), str) and out["use"] in remap:
        out["use"] = remap[out["use"]]
    for arr in ("row", "column"):
        if isinstance(out.get(arr), list):
            out[arr] = [k for k in (_edit_tree(k, edit_fn, remap) for k in out[arr])
                        if k is not None]
    return out


def _specialize_layout(layout: dict, in_scope: set[str], edit_fn) -> dict:
    """Rewrite in-scope sheet-type trees; fragments reachable from both
    in-scope and out-of-scope types are cloned (with their use-path
    ancestors, transitively — clones reference clones) and only the
    in-scope roots repointed (spec: Shared layout fragments)."""
    if not isinstance(layout, dict):
        return layout
    out = json.loads(json.dumps(layout))  # deep copy
    frags = out.get("fragments") if isinstance(out.get("fragments"), dict) else {}
    users = _fragment_users(out)
    shared = {fid for fid, tids in users.items()
              if tids & in_scope and tids - in_scope}
    remap: dict[str, str] = {}
    for fid in shared:
        clone = fid + "-2"
        while clone in frags or clone in remap.values():
            clone += "x"
        remap[fid] = clone
    # clones: edited copies whose own `use` refs also follow the remap
    for fid, clone in remap.items():
        frags[clone] = _edit_tree(json.loads(json.dumps(frags.get(fid))), edit_fn, remap)
    # fragments reachable only in-scope: edit in place
    for fid, tids in users.items():
        if fid not in shared and tids and tids <= in_scope:
            frags[fid] = _edit_tree(frags.get(fid), edit_fn, remap)
    if frags:
        out["fragments"] = frags
    sheet_trees = out.get("sheet_types") if isinstance(out.get("sheet_types"), dict) else {}
    for tid in list(sheet_trees):
        if tid in in_scope:
            sheet_trees[tid] = _edit_tree(sheet_trees[tid], edit_fn, remap)
    return out


def _prune_node(node, groups: AbstractSet[str], names: AbstractSet[str]):
    """Returns the pruned node or None when it empties (cascade-cosmetic)."""
    if not isinstance(node, dict):
        return node
    out = dict(node)
    for container in ("row", "column"):
        if isinstance(out.get(container), list):
            kids = [k for k in (_prune_node(k, groups, names) for k in out[container])
                    if k is not None]
            if not kids:
                return None
            out[container] = kids
            return out
    if out.get("group") in groups:
        return None
    for arr in ("fields", "derived"):
        if isinstance(out.get(arr), list):
            kept = [n for n in out[arr] if n not in names]
            if not kept:
                return None
            out[arr] = kept
    return out


def _prune_layout(root: Path, *, in_scope: set[str], group: str | None = None,
                  names: AbstractSet[str] = frozenset(), groups: AbstractSet[str] = frozenset(),
                  drop_type: str | None = None) -> None:
    """Cascade-cosmetic prune, SCOPED to the sheet types that compose the
    edited container (codex plan review: a global prune would strip a
    disjoint type's same-spelled field from its own layout). `group` prunes
    apply everywhere (group ids are globally unique -- a deleted group is
    gone from every type); `names` and `groups` prunes run through the
    fragment-specialization walk so a fragment shared with out-of-scope
    types is cloned-pruned-repointed, never damaged in place. `groups` is
    the scoped form: a type that stopped composing a group loses that
    group's node while every other type keeps its own."""
    layout = _read_json(root, "layout.json")
    if not layout:
        return
    if drop_type and isinstance(layout.get("sheet_types"), dict):
        layout["sheet_types"].pop(drop_type, None)
    if group is not None:
        # group nodes are unambiguous — prune every tree and fragment
        for section in ("fragments", "sheet_types"):
            entries = layout.get(section)
            if not isinstance(entries, dict):
                continue
            for key in list(entries):
                pruned = _prune_node(entries[key], frozenset({group}), frozenset())
                if pruned is None:
                    entries.pop(key)
                else:
                    entries[key] = pruned
    if names or groups:
        layout = _specialize_layout(
            layout, in_scope,
            lambda node: _prune_node(node, frozenset(groups), names))
    _write_json(root, "layout.json", layout)


def _layout_name_edit(old: str, new: str, kind: str):
    """edit_fn renaming `old`->`new` in `fields`/`derived` entry arrays (kind
    'name') or `group` node refs (kind 'group')."""
    def edit(node):
        if not isinstance(node, dict):
            return node
        out = dict(node)
        if kind == "group" and out.get("group") == old:
            out["group"] = new
        if kind == "name":
            for arr in ("fields", "derived"):
                if isinstance(out.get(arr), list):
                    out[arr] = [new if n == old else n for n in out[arr]]
        return out
    return edit
