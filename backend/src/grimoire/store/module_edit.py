"""Module authoring (#829, mechanics Phase 8): staged, validated, journaled
whole-directory publication of user-library pack edits.

Concurrency threat model (spec): exactly two actors — the User (UI) and the
LLM (play flows). One global re-entrant module-edit lock serializes all
module mutation + recovery; every publishing writer also holds every
campaign's sheets.lock_for(cid) across its swap, so LLM flows (which hold
their single campaign lock across resolve/load/compute) never observe a
half-published pack. No machinery for two User actions racing.
Spec: docs/superpowers/specs/2026-07-13-mechanics-phase8-authoring-ui-design.md.
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from contextlib import ExitStack, contextmanager
from pathlib import Path

from . import campaigns, modules, sheets
from .frontmatter import dump_frontmatter
from .paths import home

_M = threading.RLock()


class _RenameCollision(Exception):
    """Placeholder — Task 6 defines the real rename-collision exception
    raised by mutate() when a rename target collides with an existing
    content id. _apply's except clause below only needs the name to exist
    before Task 6 lands."""


@contextmanager
def locked():
    """The global module-edit lock; export and multi-file pack readers wrap
    themselves in this for a swap-coherent view."""
    with _M:
        yield


def _staging_root() -> Path:
    return home() / ".module-staging"


def _require_user_root(mid: str) -> Path:
    root, source = modules.pack_root(mid)  # raises ModuleNotFound
    if source != "user":
        raise modules.ModuleError(
            "built-in modules cannot be edited — duplicate it first")
    return root


def recover() -> None:
    """Replay leftover journals idempotently; delete staging debris. Runs
    under _M (startup + start of every edit). A journal is only written
    after staging validated, so the cases are exact (spec: Recovery).

    Journal replay can PUBLISH a pack and run its migration — in a live
    process (a crashed edit followed by more requests, not just startup)
    that must exclude LLM flows exactly like a normal swap, so any journal
    that will publish or migrate replays under all campaign locks (codex
    plan review: recovery without them re-opens the R1 race)."""
    with _M:
        d = _staging_root()
        if not d.is_dir():
            return
        journals = sorted(d.glob("*.journal.json"))
        quarantined: set[str] = set()
        if journals:
            with _campaign_locks():
                for jp in journals:
                    _replay_journal(jp, quarantined)
        # non-journaled debris (crash before journal write); no edit can be
        # in flight here — edits hold _M for their whole operation. If ANY
        # journal was quarantined we cannot know which dirs it references,
        # so the sweep is skipped entirely — a torn journal's staging/trash
        # may hold the only copy of a missing live module (codex plan
        # review round 2). Quarantined debris is a human-inspectable
        # leftover, not a correctness hazard.
        if not quarantined:
            for p in list(d.iterdir()):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)


def _replay_journal(jp: Path, quarantined: set[str]) -> None:
    """Replay one journal. A journal that fails to parse or carries an
    unsafe mid/nonce is QUARANTINED (renamed .journal.bad, its nonce dir
    kept) — never acted on: deriving paths from a torn journal could
    rmtree the whole recovery area (nonce '' → base == .module-staging) or
    walk outside it (codex plan review round 2)."""
    d = _staging_root()
    try:
        j = json.loads(jp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        j = None
    mid = str(j.get("mid") or "") if isinstance(j, dict) else ""
    nonce = str(j.get("nonce") or "") if isinstance(j, dict) else ""
    if not modules._safe_mid(mid) or not modules._safe_id_like(nonce):
        quarantined.add(jp.name)
        jp.rename(jp.with_suffix(".bad"))
        return
    base = d / nonce
    staging = base / mid
    live = modules.user_dir() / mid
    published = False
    if live.exists() and staging.exists():
        pass  # pre-swap crash: discard the edit (and its migration)
    elif not live.exists() and staging.exists():
        live.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(live)
        published = True
    elif live.exists():
        published = True  # post-swap crash: trash cleanup + migration
    if published and isinstance(j.get("migration"), dict):
        _run_migration(mid, j["migration"])
    shutil.rmtree(base, ignore_errors=True)
    jp.unlink(missing_ok=True)


def _run_migration(mid: str, migration: dict) -> dict:
    """Sheet migration for rename ops — implemented in Task 8. Until then a
    journaled migration replays as a no-op."""
    return {"migrated": 0, "skipped": []}


@contextmanager
def _campaign_locks():
    """Every campaign's sheet lock: the User-edit vs LLM-play exclusion.
    Order is irrelevant — the module edit is the only multi-lock holder that
    can run concurrently with anything (LLM flows hold exactly one)."""
    with ExitStack() as stack:
        for c in campaigns.list_campaigns():
            stack.enter_context(sheets.lock_for(c["id"]))
        yield


def _result(pack: dict, **extra) -> dict:
    out = {"ok": not pack["errors"], "errors": list(pack["errors"]),
           "display_errors": list(pack["display_errors"])}
    out.update(extra)
    return out


def _apply(mid: str, mutate, *, dry_run: bool = False,
           migration: dict | None = None, pre_swap=None) -> dict:
    """stage -> mutate -> validate -> (locks) -> journal -> swap -> migrate.

    mutate(staging_root) edits the staged copy in place. pre_swap(pack) runs
    under all campaign locks just before the journal and returns a list of
    blocking errors (e.g. rename collision scans). Rejection (validation or
    pre_swap) leaves the live pack byte-identical and no staging debris."""
    with _M:
        recover()
        live = _require_user_root(mid)
        nonce = uuid.uuid4().hex
        base = _staging_root() / nonce
        staging = base / mid
        try:
            base.mkdir(parents=True)
            shutil.copytree(live, staging)
            try:
                mutate(staging)
            except _RenameCollision as e:  # Task 6 defines it; harmless before
                return {"ok": False, "errors": [f"rename collision: {e}"],
                        "display_errors": []}
            pack = modules.load_pack_at(staging, mid)
            if pack["errors"] or dry_run:
                return _result(pack)
            with _campaign_locks():
                if pre_swap is not None:
                    blockers = pre_swap(pack)
                    if blockers:
                        return {"ok": False, "errors": blockers,
                                "display_errors": list(pack["display_errors"])}
                jp = _staging_root() / f"{nonce}.journal.json"
                jp.write_text(json.dumps(
                    {"mid": mid, "nonce": nonce, "migration": migration}),
                    encoding="utf-8")
                trash = base / "trash" / mid
                trash.parent.mkdir(parents=True)
                live.rename(trash)
                staging.rename(live)
                mig = _run_migration(mid, migration) if migration else None
                jp.unlink()
            return _result(pack, **({"migration": mig} if mig else {}))
        finally:
            shutil.rmtree(base, ignore_errors=True)


# ---- section writers ----


def set_manifest(mid: str, *, name: str, description: str, version: str,
                 dice: str, notes: str, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        meta = {"name": name}
        if description:
            meta["description"] = description
        if version:
            meta["version"] = version
        if dice:
            meta["dice"] = dice
        (root / "module.md").write_text(
            dump_frontmatter(meta, notes), encoding="utf-8")
    return _apply(mid, mutate, dry_run=dry_run)


# ---- staging JSON helpers ----


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
    (root / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_sheets(root: Path) -> dict:
    data = _read_json(root, "sheets.json")
    data.setdefault("groups", {})
    data.setdefault("sheet_types", {})
    return data


# ---- layout specialization over the transitive `use` graph ----
# (Task 6 reuses these three for renames instead of redefining them.)


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


def _prune_node(node, group: str | None, names: set[str]):
    """Returns the pruned node or None when it empties (cascade-cosmetic)."""
    if not isinstance(node, dict):
        return node
    out = dict(node)
    for container in ("row", "column"):
        if isinstance(out.get(container), list):
            kids = [k for k in (_prune_node(k, group, names) for k in out[container])
                    if k is not None]
            if not kids:
                return None
            out[container] = kids
            return out
    if group is not None and out.get("group") == group:
        return None
    for arr in ("fields", "derived"):
        if isinstance(out.get(arr), list):
            kept = [n for n in out[arr] if n not in names]
            if not kept:
                return None
            out[arr] = kept
    return out


def _prune_layout(root: Path, *, in_scope: set[str], group: str | None = None,
                  names: set[str] = frozenset(),
                  drop_type: str | None = None) -> None:
    """Cascade-cosmetic prune, SCOPED to the sheet types that compose the
    edited container (codex plan review: a global prune would strip a
    disjoint type's same-spelled field from its own layout). `group` prunes
    apply everywhere (group ids are globally unique); `names` prunes run
    through the fragment-specialization walk so a fragment shared with
    out-of-scope types is cloned-pruned-repointed, never damaged in place."""
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
                pruned = _prune_node(entries[key], group, frozenset())
                if pruned is None:
                    entries.pop(key)
                else:
                    entries[key] = pruned
    if names:
        layout = _specialize_layout(
            layout, in_scope,
            lambda node: _prune_node(node, None, names))
    _write_json(root, "layout.json", layout)


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


def upsert_group(mid: str, gid: str, group: dict, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        old = data["groups"].get(gid)
        data["groups"][gid] = group
        _write_json(root, "sheets.json", data)
        if isinstance(old, dict):  # prune layout refs to removed keys
            removed = _field_keys(old) - _field_keys(group if isinstance(group, dict) else {})
            if removed:
                _prune_layout(root, in_scope=_group_scope(data, gid), names=removed)
    return _apply(mid, mutate, dry_run=dry_run)


def delete_group(mid: str, gid: str, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        scope = _group_scope(data, gid)
        old = data["groups"].pop(gid, None)
        _write_json(root, "sheets.json", data)
        _prune_layout(root, in_scope=scope, group=gid,
                      names=_field_keys(old) if isinstance(old, dict) else set())
    return _apply(mid, mutate, dry_run=dry_run)


def upsert_sheet_type(mid: str, tid: str, sheet_type: dict, *,
                      dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        old = data["sheet_types"].get(tid)
        data["sheet_types"][tid] = sheet_type
        _write_json(root, "sheets.json", data)
        if isinstance(old, dict):
            removed = _field_keys(old) - _field_keys(
                sheet_type if isinstance(sheet_type, dict) else {})
            if removed:
                _prune_layout(root, in_scope={tid}, names=removed)
    return _apply(mid, mutate, dry_run=dry_run)


def delete_sheet_type(mid: str, tid: str, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        old = data["sheet_types"].pop(tid, None)
        _write_json(root, "sheets.json", data)
        _prune_layout(root, in_scope={tid}, drop_type=tid,
                      names=_field_keys(old) if isinstance(old, dict) else set())
    return _apply(mid, mutate, dry_run=dry_run)
