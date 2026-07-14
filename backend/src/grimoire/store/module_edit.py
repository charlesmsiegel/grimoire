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

import io
import json
import re
import shutil
import threading
import uuid
import zipfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

from . import campaigns, modules, proposals, sheets, worlds
from .frontmatter import dump_frontmatter
from .paths import home, slugify, uniquify

_M = threading.RLock()


class _RenameCollision(Exception):
    """Raised by a rename mutate() when the destination key already exists
    in a map-backed namespace (groups/sheet_types/checks/derived) — caught
    by _apply, which turns it into a clean ok=False result instead of
    silently overwriting the destination's definition."""


@contextmanager
def locked():
    """The global module-edit lock; export and multi-file pack readers wrap
    themselves in this for a swap-coherent view."""
    with _M:
        yield


def _staging_root() -> Path:
    return home() / ".module-staging"


MAX_MEMBERS = 2000
MAX_UNCOMPRESSED = 64 * 1024 * 1024


def new_mid(name_or_id: str) -> str:
    """The one id allocator for create/duplicate/import: slugify, reject
    empty, reserve 'none', dedupe against builtin + user ids (mirrors
    modules.create_module's predicate)."""
    base = slugify(" ".join(str(name_or_id).split()) or "module")
    return uniquify(base or "module",
                    lambda i: i == "none" or (modules.user_dir() / i).exists()
                    or (modules.builtin_dir() / i / "module.md").exists())


def _publish(staging: Path, mid: str) -> str:
    dest = modules.user_dir() / mid
    dest.parent.mkdir(parents=True, exist_ok=True)
    staging.rename(dest)
    return mid


def duplicate_module(mid: str, name: str) -> str:
    """Copy any pack (builtin or user) to staging, publish by single rename
    into user_dir() under _M. Content copied as-is, valid or not."""
    with _M:
        recover()
        root, _source = modules.pack_root(mid)   # raises ModuleNotFound
        new = new_mid(name or f"{mid} copy")
        nonce = uuid.uuid4().hex
        base = _staging_root() / nonce
        try:
            staging = base / new
            base.mkdir(parents=True)
            shutil.copytree(root, staging)
            return _publish(staging, new)
        finally:
            shutil.rmtree(base, ignore_errors=True)


def create_module(name: str) -> str:
    """Staged scaffold + single-rename publish (a crash never leaves a
    partial live pack, unlike modules.create_module's in-place mkdir)."""
    with _M:
        recover()
        clean = " ".join(str(name).split()) or "Untitled"
        mid = new_mid(clean)
        nonce = uuid.uuid4().hex
        base = _staging_root() / nonce
        try:
            staging = base / mid
            staging.mkdir(parents=True)
            (staging / "module.md").write_text(
                dump_frontmatter({"name": clean, "description": "",
                                  "version": "0.1"}, ""), encoding="utf-8")
            (staging / "sheets.json").write_text(
                '{\n  "groups": {},\n  "sheet_types": {}\n}\n', encoding="utf-8")
            return _publish(staging, mid)
        finally:
            shutil.rmtree(base, ignore_errors=True)


def delete_module(mid: str) -> None:
    """Locked deletion: a bound module vanishing (or a same-id user shadow
    falling through to the builtin) mid-LLM-computation must be impossible —
    the campaign locks are exactly what those consumers hold."""
    with _M:
        recover()
        _root, source = modules.pack_root(mid)  # 404 before taking every lock
        if source != "user":
            raise modules.ModuleError("built-in modules cannot be deleted")
        with _campaign_locks():
            modules.delete_module(mid)


def export_module(mid: str) -> bytes:
    with locked():
        root, _source = modules.pack_root(mid)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    z.write(p, f"{mid}/{p.relative_to(root).as_posix()}")
        return buf.getvalue()


_DRIVE_OR_UNC = re.compile(r"^[A-Za-z]:|^[/\\]{2}")


def _member_parts(raw_name: str) -> list[str]:
    """Normalized path components for a zip member, or raise. Rejects
    absolute paths, drive-qualified and UNC names, and EMPTY / '.' / '..'
    components (codex plan review: 'pack//module.md' passes a naive split —
    the stripped remainder '/module.md' then resolves to the drive root).
    Also rejects any component containing ':' — the whole-name
    `_DRIVE_OR_UNC` check only anchors at the start, so a mid-path drive
    segment like 'pack/C:evil.txt' would otherwise pass here and then get
    collapsed onto the drive root by Path.joinpath, escaping staging before
    the containment recheck ever runs (review finding: all checks must
    happen before any extraction, not be caught mid-extraction)."""
    name = raw_name.replace("\\", "/")
    if _DRIVE_OR_UNC.match(name) or name.startswith("/"):
        raise modules.ModuleError(f"unsafe zip entry: {raw_name}")
    parts = name.split("/")
    if len(parts) < 2 or any(p in ("", ".", "..") or ":" in p for p in parts):
        raise modules.ModuleError(f"unsafe zip entry: {raw_name}")
    return parts


def _check_archive(z: zipfile.ZipFile) -> str:
    infos = [i for i in z.infolist() if not i.is_dir()]
    if len(infos) > MAX_MEMBERS:
        raise modules.ModuleError(f"zip has too many entries (> {MAX_MEMBERS})")
    if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED:
        raise modules.ModuleError("zip expands past the size cap")
    roots: set[str] = set()
    seen_ci: set[str] = set()
    for i in infos:
        if (i.external_attr >> 16) & 0o170000 == 0o120000:
            raise modules.ModuleError(f"zip contains a symlink: {i.filename}")
        parts = _member_parts(i.filename)
        roots.add(parts[0])
        ci = "/".join(parts).casefold()   # normalized + case-folded collisions
        if ci in seen_ci:
            raise modules.ModuleError(f"case-colliding zip entries: {i.filename}")
        seen_ci.add(ci)
    if len(roots) != 1:
        raise modules.ModuleError("zip must contain exactly one top-level module directory")
    return next(iter(roots))


def import_module(path: Path) -> str:
    with _M:
        recover()
        try:
            z = zipfile.ZipFile(path)
        except (zipfile.BadZipFile, OSError) as e:
            raise modules.ModuleError(f"not a zip archive: {e}")
        with z:
            src_root = _check_archive(z)
            mid = new_mid(src_root)
            nonce = uuid.uuid4().hex
            base = _staging_root() / nonce
            try:
                staging = base / mid
                staging.mkdir(parents=True)
                staging_resolved = staging.resolve()
                for i in z.infolist():
                    if i.is_dir():
                        continue
                    parts = _member_parts(i.filename)
                    dest = staging.joinpath(*parts[1:])
                    try:  # containment check (no Path.is_relative_to — 3.8-safe)
                        dest.resolve().relative_to(staging_resolved)
                    except ValueError:
                        raise modules.ModuleError(f"unsafe zip entry: {i.filename}")
                    try:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(z.read(i))
                    except OSError:
                        # pathological names (reserved device names CON/NUL,
                        # trailing dots/spaces on Windows) can raise a raw
                        # OSError here — never let that escape uncontained.
                        raise modules.ModuleError(f"unextractable zip entry: {i.filename}")
                pack = modules.load_pack_at(staging, mid)
                if pack["errors"]:
                    raise modules.ModuleError(
                        "invalid module pack: " + "; ".join(pack["errors"]))
                return _publish(staging, mid)
            finally:
                shutil.rmtree(base, ignore_errors=True)


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


def _sheet_files(mid: str):
    """Yield (Path, cid|None) for every stored sheet governed by this module:
    each world's <world>/sheets/<mid>/*.json, plus each bound campaign's
    <campaign>/sheets/*.json (bound = resolve(cid) == mid)."""
    for w in worlds.list_worlds():
        d = worlds.world_root(w["id"]) / "sheets" / mid
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                yield p, None
    for c in campaigns.list_campaigns():
        cid = c["id"]
        if modules.resolve(cid) != mid:
            continue
        d = campaigns.campaign_root(cid) / "sheets"
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                yield p, cid


def _migrate_file(p: Path, mig: dict) -> bool | None:
    """True = rewritten, False = untouched, None = unparseable (skip)."""
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    changed = False
    op = mig.get("op")
    if op == "field":
        if data.get("sheet_type") in (mig.get("sheet_types") or []) \
                and mig["from"] in fields and mig["to"] not in fields:
            fields[mig["to"]] = fields.pop(mig["from"])
            changed = True
    elif op == "sheet_type":
        if data.get("sheet_type") == mig["from"]:
            data["sheet_type"] = mig["to"]
            changed = True
    elif op == "content":
        marker = f"{mig.get('kind')}:module:{mig['from']}"
        repl = f"{mig.get('kind')}:module:{mig['to']}"
        for k, v in list(fields.items()):
            if isinstance(v, list) and marker in v:
                fields[k] = [repl if e == marker else e for e in v]
                changed = True
    if changed:
        data["fields"] = fields
        data["gen"] = uuid.uuid4().hex
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(p)
    return changed


def _would_migrate(data: dict, mig: dict) -> bool:
    fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
    if mig["op"] == "field":
        return data.get("sheet_type") in (mig.get("sheet_types") or []) and mig["from"] in fields
    if mig["op"] == "sheet_type":
        return data.get("sheet_type") == mig["from"]
    marker = f"{mig.get('kind')}:module:{mig['from']}"
    return any(isinstance(v, list) and marker in v for v in fields.values())


def _migrate_preview(fields: dict, data: dict, mig: dict) -> None:
    """Apply the migration's effect to a copy, for staged-validation parity
    (a renamed key/type must be judged under its NEW name — otherwise every
    sheet of a renamed type would falsely count as newly invalid)."""
    if mig["op"] == "field" and data.get("sheet_type") in (mig.get("sheet_types") or []) \
            and mig["from"] in fields and mig["to"] not in fields:
        fields[mig["to"]] = fields.pop(mig["from"])
    elif mig["op"] == "sheet_type" and data.get("sheet_type") == mig["from"]:
        data["sheet_type"] = mig["to"]
    elif mig["op"] == "content":
        marker = f"{mig.get('kind')}:module:{mig['from']}"
        repl = f"{mig.get('kind')}:module:{mig['to']}"
        for k, v in list(fields.items()):
            if isinstance(v, list) and marker in v:
                fields[k] = [repl if e == marker else e for e in v]


def _file_kind(p: Path) -> str:
    return p.stem.partition("--")[0]


def _iter_ref_values(fields: dict):
    for v in (fields or {}).values():
        if isinstance(v, list):
            for e in v:
                if isinstance(e, str):
                    yield e


def _content_ids(pack: dict) -> set[str]:
    return {f"{c['kind']}:module:{c['id']}" for c in pack.get("content", [])}


def _sidecar_stats(mid: str) -> list[dict]:
    root, _ = modules.pack_root(mid)
    out = []
    cd = root / "content"
    if cd.is_dir():
        for sc in sorted(cd.rglob("*.sheet.json")):
            try:
                stat = json.loads(sc.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue
            if isinstance(stat, dict):
                out.append(stat)
    return out


def _impact(mid: str, staged_pack: dict, migration: dict | None) -> dict:
    """Dry-run impact scan: {"sheet_types", "sheets_migrated",
    "sheets_newly_invalid", "dangling_refs"}. Newly-invalid = stored sheets
    valid against the live pack but invalid against the staged one; dangling
    = ref values whose content id exists live but not staged, counted over
    stored sheets AND content stat sidecars."""
    live_pack = modules.load_pack(mid)
    newly_invalid = 0
    dangling = 0
    staged_ids = _content_ids(staged_pack)
    live_ids = _content_ids(live_pack)
    for p, _cid in _sheet_files(mid):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        st = data.get("sheet_type")
        fields = data.get("fields") if isinstance(data.get("fields"), dict) else {}
        if migration:   # judge post-migration state, not the raw file
            mig_fields = dict(fields)
            _migrate_preview(mig_fields, data, migration)
            st = data.get("sheet_type")
            fields = mig_fields
        live_errs = sheets.instance_errors(live_pack, _file_kind(p), st, fields)
        staged_errs = sheets.instance_errors(staged_pack, _file_kind(p), st, fields)
        if not live_errs and staged_errs:
            newly_invalid += 1
        for ref in _iter_ref_values(fields):
            if ":module:" in ref and ref in live_ids and ref not in staged_ids:
                dangling += 1
    for stat in _sidecar_stats(mid):
        for ref in _iter_ref_values(stat.get("fields") or {}):
            if ":module:" in ref and ref in live_ids and ref not in staged_ids:
                dangling += 1
    out = {"sheet_types": [], "sheets_migrated": 0,
           "sheets_newly_invalid": newly_invalid, "dangling_refs": dangling}
    if migration:
        out["sheet_types"] = list(migration.get("sheet_types")
                                  or ([migration["from"]] if migration["op"] == "sheet_type" else []))
        migrated = 0
        for p, _cid in _sheet_files(mid):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                continue
            if isinstance(data, dict) and _would_migrate(data, migration):
                migrated += 1
        out["sheets_migrated"] = migrated
    return out


def _run_migration(mid: str, migration: dict) -> dict:
    """Sheet migration for rename ops: rewrite every stored sheet governed by
    this module that the migration op touches, bumping `gen` on each changed
    file. Idempotent (old key absent ⇒ no-op) so journal replay after a crash
    is safe to repeat."""
    migrated, skipped = 0, []
    for p, _cid in _sheet_files(mid):
        got = _migrate_file(p, migration)
        if got is None:
            skipped.append(str(p))
        elif got:
            migrated += 1
    return {"migrated": migrated, "skipped": skipped}


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


def _sample(pack: dict) -> dict:
    """Per-sheet-type sample: schema defaults + derived, for a dry-run
    preview of what a fresh sheet of that type would look like."""
    out = {}
    for tid in (pack["sheets"].get("sheet_types") or {}):
        defaults = sheets.default_fields(pack["sheets"], tid)
        errs: list[str] = []
        derived = sheets._compute_derived(pack["sheets"], tid, defaults, errs)
        out[tid] = {"fields": defaults, "derived": derived}
    return out


def _apply(mid: str, mutate, *, dry_run: bool = False,
           migration: dict | None = None, pre_swap=None,
           impact: bool = False, sample: bool = False) -> dict:
    """stage -> mutate -> validate -> (locks) -> journal -> swap -> migrate.

    mutate(staging_root) edits the staged copy in place. pre_swap(pack) runs
    under all campaign locks just before the journal and returns a list of
    blocking errors (e.g. rename collision scans). Rejection (validation or
    pre_swap) leaves the live pack byte-identical and no staging debris.
    impact=True computes a dry-run impact scan (schema-affecting writers and
    rename ops); sample=True additionally computes a per-sheet-type sample
    (schema defaults + derived) after a clean validation, for sheets.json
    dry-runs."""
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
            extra: dict = {}
            if impact:
                extra["impact"] = _impact(mid, pack, migration)
            if pack["errors"] or dry_run:
                if sample and not pack["errors"]:
                    extra["sample"] = _sample(pack)
                return _result(pack, **extra)
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
            if mig:
                extra["migration"] = mig
            return _result(pack, **extra)
        finally:
            # Only the nonce staging dir (`base`) is removed here — never the
            # journal (`jp`, a sibling under _staging_root(), not under
            # `base`). The journal must outlive a crash during
            # _run_migration (e.g. the KeyboardInterrupt escape hatch in
            # tests) so recover() can find it and replay the migration.
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


# ---- rename ----

_RENAME_KINDS = ("group", "field", "derived", "sheet_type", "check", "rule", "content")


def _rewrite_expr(expr: str, old: str, new: str) -> str:
    """Word-boundary text replacement. Safe: the expression language has no
    strings/attributes/comments, so \\b<old>\\b can only match a Name; the
    staged validation re-parses everything afterwards regardless."""
    return re.sub(rf"\b{re.escape(old)}\b", new, expr)


def _rewrite_exprs(expr: str, old: str, new: str, resource: bool) -> str:
    out = _rewrite_expr(expr, old, new)
    if resource:
        out = _rewrite_expr(out, f"{old}_max", f"{new}_max")
    return out


def _rewrite_placeholders(roll: str, old: str, new: str, resource: bool) -> str:
    return re.sub(r"\{([^{}]+)\}",
                  lambda m: "{" + _rewrite_exprs(m.group(1), old, new, resource) + "}",
                  roll)


def _rename_map_key(d: dict, old: str, new: str) -> None:
    """Move a map key, refusing to overwrite an existing destination (codex
    plan review: d[new] = d.pop(old) silently destroys a same-named valid
    definition and leaves nothing for staged validation to catch)."""
    if isinstance(d, dict) and old in d:
        if new in d:
            raise _RenameCollision(f"{new!r} already exists")
        d[new] = d.pop(old)


def _composing_tids(sheets_json: dict, owner: dict) -> set[str]:
    if "sheet_type" in owner:
        return {owner["sheet_type"]}
    gid = owner.get("group")
    out = set()
    for tid, st in (sheets_json.get("sheet_types") or {}).items():
        if isinstance(st, dict) and gid in (st.get("groups") or []):
            out.add(tid)
    return out


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


def check_proposal_guard(mid: str, check_id: str):
    """pre_swap callback: block while any campaign bound to this module has
    a non-terminal proposal referencing the check (spec: check rename row)."""
    def guard(_pack: dict) -> list[str]:
        blockers: list[str] = []
        for c in campaigns.list_campaigns():
            cid = c["id"]
            if modules.resolve(cid) != mid:
                continue
            for sid, rec in proposals._read(cid).items():
                if not isinstance(rec, dict) or rec.get("status") not in proposals.NON_TERMINAL:
                    continue
                payload = rec.get("payload") if isinstance(rec.get("payload"), dict) else {}
                res = rec.get("resolution") if isinstance(rec.get("resolution"), dict) else {}
                if payload.get("check") == check_id or res.get("check") == check_id:
                    blockers.append(
                        f"check {check_id!r} has a live roll proposal in campaign "
                        f"{cid!r}, scene {sid!r} — resolve or discard it first")
        return blockers
    return guard


_SAFE_KEY = re.compile(r"[a-z0-9][a-z0-9._-]*\Z", re.IGNORECASE)


def rename(mid: str, kind: str, address: dict, to: str, *,
           dry_run: bool = False) -> dict:
    if kind not in _RENAME_KINDS:
        return {"ok": False, "errors": [f"unknown rename kind {kind!r}"], "display_errors": []}
    old = address.get("from")
    # Codex plan review round 2: rule/content ids interpolate into paths —
    # a '../'-laden 'from' could move ANOTHER live module's file into
    # staging (then delete it in cleanup), and a colliding destination file
    # would be silently overwritten on POSIX. Both names must be safe keys,
    # for every kind (field/derived keys are never paths, but a uniform
    # gate is cheaper than remembering which kinds touch the filesystem).
    if not isinstance(old, str) or not _SAFE_KEY.match(old) \
            or not isinstance(to, str) or not _SAFE_KEY.match(to):
        return {"ok": False, "errors": ["rename needs safe 'from' and 'to' keys"],
                "display_errors": []}
    if old == to:
        return {"ok": False, "errors": ["'from' and 'to' are the same"], "display_errors": []}
    # source-exists + destination-free preflight per namespace: the mutate
    # step's _rename_map_key covers map-backed kinds; file-backed kinds
    # (rule, content) check here because a filesystem rename onto an
    # existing path must never happen at all.
    live_root, _src = modules.pack_root(mid)
    if kind == "rule":
        if not (live_root / "rules" / f"{old}.md").exists():
            return {"ok": False, "errors": [f"unknown rules doc {old!r}"], "display_errors": []}
        if (live_root / "rules" / f"{to}.md").exists():
            return {"ok": False, "errors": [f"rules doc {to!r} already exists"], "display_errors": []}
    if kind == "content":
        ckind = address.get("kind")
        if ckind not in modules.CONTENT_KINDS:
            return {"ok": False, "errors": [f"unknown content kind {ckind!r}"], "display_errors": []}
        if not (live_root / "content" / ckind / f"{old}.md").exists():
            return {"ok": False, "errors": [f"unknown content {ckind}/{old}"], "display_errors": []}
        if (live_root / "content" / ckind / f"{to}.md").exists():
            return {"ok": False, "errors": [f"content {ckind}/{to} already exists"], "display_errors": []}

    migration = None
    pre_swap = None
    if kind == "check":
        pre_swap = check_proposal_guard(mid, old)
    if kind == "field":
        owner = {k: address[k] for k in ("group", "sheet_type") if k in address}
        in_scope = _composing_tids(_read_sheets(live_root), owner)
        migration = {"op": "field", "from": old, "to": to, "owner": owner,
                     "sheet_types": sorted(in_scope)}

        def both_keys_guard(_pack: dict) -> list[str]:
            blockers = []
            for p, _cid in _sheet_files(mid):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    continue
                fields = data.get("fields") if isinstance(data, dict) \
                    and isinstance(data.get("fields"), dict) else {}
                if data.get("sheet_type") in migration["sheet_types"] \
                        and old in fields and to in fields:
                    blockers.append(
                        f"{p.name}: holds both {old!r} and {to!r} — resolve the "
                        "orphaned value first")
            return blockers
        pre_swap = both_keys_guard
    elif kind == "sheet_type":
        migration = {"op": "sheet_type", "from": old, "to": to}
    elif kind == "content":
        migration = {"op": "content", "kind": address.get("kind"), "from": old, "to": to}

    def mutate(root: Path) -> None:
        sheets_json = _read_sheets(root)
        checks_json = _read_json(root, "checks.json")
        layout_json = _read_json(root, "layout.json")

        if kind == "group":
            _rename_map_key(sheets_json.get("groups", {}), old, to)
            for st in sheets_json.get("sheet_types", {}).values():
                if isinstance(st, dict) and isinstance(st.get("groups"), list):
                    st["groups"] = [to if g == old else g for g in st["groups"]]
                creation = st.get("creation") if isinstance(st, dict) else None
                if isinstance(creation, dict) and isinstance(creation.get("pools"), dict):
                    _rename_map_key(creation["pools"], old, to)
            for check in checks_json.values():
                if isinstance(check, dict) and isinstance(check.get("requires"), list):
                    check["requires"] = [to if g == old else g for g in check["requires"]]
            if layout_json:
                all_tids = set(sheets_json.get("sheet_types", {}))
                layout_json = _specialize_layout(
                    layout_json, all_tids, _layout_name_edit(old, to, "group"))
                _write_json(root, "layout.json", layout_json)

        elif kind in ("field", "derived"):
            owner = {k: address[k] for k in ("group", "sheet_type") if k in address}
            if not owner:
                raise modules.ModuleError("field/derived rename needs an owner")
            in_scope = _composing_tids(sheets_json, owner)
            groups = sheets_json.get("groups", {})
            types = sheets_json.get("sheet_types", {})
            owner_container = (groups.get(owner.get("group"))
                               if "group" in owner else types.get(owner.get("sheet_type")))
            resource = False
            if kind == "field" and isinstance(owner_container, dict):
                for f in owner_container.get("fields", []) or []:
                    if isinstance(f, dict) and f.get("key") == old:
                        resource = f.get("type") == "resource"
                        f["key"] = to
            if kind == "derived" and isinstance(owner_container, dict):
                _rename_map_key(owner_container.get("derived") or {}, old, to)
            # scope-bound expression rewrites
            if "group" in owner and isinstance(owner_container, dict):
                d = owner_container.get("derived")
                if isinstance(d, dict):
                    for name in list(d):
                        if isinstance(d[name], str):
                            d[name] = _rewrite_exprs(d[name], old, to, resource)
            for tid in in_scope:
                st = types.get(tid)
                if not isinstance(st, dict):
                    continue
                d = st.get("derived")
                if isinstance(d, dict):
                    for name in list(d):
                        if isinstance(d[name], str):
                            d[name] = _rewrite_exprs(d[name], old, to, resource)
                adv = st.get("advancement")
                if isinstance(adv, dict):
                    if adv.get("pool") == old:
                        adv["pool"] = to
                    costs = adv.get("costs")
                    if isinstance(costs, dict):
                        _rename_map_key(costs, old, to)
                        for name in list(costs):
                            if isinstance(costs[name], str):
                                costs[name] = _rewrite_exprs(costs[name], old, to, resource)
                creation = st.get("creation")
                if isinstance(creation, dict) and "group" in owner:
                    pool = (creation.get("pools") or {}).get(owner["group"])
                    if isinstance(pool, dict) and isinstance(pool.get("costs"), dict):
                        _rename_map_key(pool["costs"], old, to)
            if "group" in owner:
                gid = owner["group"]
                for check in checks_json.values():
                    if isinstance(check, dict) and gid in (check.get("requires") or []):
                        if isinstance(check.get("roll"), str):
                            check["roll"] = _rewrite_placeholders(check["roll"], old, to, resource)
            # content sidecars of composing types (pack files: staged rewrite)
            if kind == "field":
                for sc in sorted((root / "content").rglob("*.sheet.json")) \
                        if (root / "content").is_dir() else []:
                    try:
                        stat = json.loads(sc.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        continue
                    if isinstance(stat, dict) and stat.get("sheet_type") in in_scope \
                            and isinstance(stat.get("fields"), dict):
                        _rename_map_key(stat["fields"], old, to)
                        sc.write_text(json.dumps(stat, indent=2) + "\n", encoding="utf-8")
            if layout_json:
                layout_json = _specialize_layout(
                    layout_json, in_scope, _layout_name_edit(old, to, "name"))
                _write_json(root, "layout.json", layout_json)

        elif kind == "sheet_type":
            _rename_map_key(sheets_json.get("sheet_types", {}), old, to)
            rd = root / "rules"
            if rd.is_dir():
                for p in sorted(rd.glob("*.md")):
                    text = p.read_text(encoding="utf-8")
                    from .frontmatter import parse_frontmatter
                    meta, body = parse_frontmatter(text)
                    flags = [v.strip() for v in (meta.get("sheet_types") or "").split(",") if v.strip()]
                    if old in flags:
                        meta["sheet_types"] = ", ".join(to if f == old else f for f in flags)
                        p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
            if isinstance(layout_json.get("sheet_types"), dict):
                _rename_map_key(layout_json["sheet_types"], old, to)
                _write_json(root, "layout.json", layout_json)
            cd = root / "content"
            if cd.is_dir():
                for sc in sorted(cd.rglob("*.sheet.json")):
                    try:
                        stat = json.loads(sc.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        continue
                    if isinstance(stat, dict) and stat.get("sheet_type") == old:
                        stat["sheet_type"] = to
                        sc.write_text(json.dumps(stat, indent=2) + "\n", encoding="utf-8")

        elif kind == "check":
            _rename_map_key(checks_json, old, to)

        elif kind == "rule":
            src, dst = root / "rules" / f"{old}.md", root / "rules" / f"{to}.md"
            if src.exists():
                src.rename(dst)
            for check in checks_json.values():
                if isinstance(check, dict) and isinstance(check.get("rules"), list):
                    check["rules"] = [to if r == old else r for r in check["rules"]]

        elif kind == "content":
            ckind = address.get("kind")
            d = root / "content" / str(ckind)
            if (d / f"{old}.md").exists():
                (d / f"{old}.md").rename(d / f"{to}.md")
            if (d / f"{old}.sheet.json").exists():
                (d / f"{old}.sheet.json").rename(d / f"{to}.sheet.json")
            marker, repl = f"{ckind}:module:{old}", f"{ckind}:module:{to}"
            cd = root / "content"
            if cd.is_dir():
                for sc in sorted(cd.rglob("*.sheet.json")):
                    try:
                        stat = json.loads(sc.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                        continue
                    if isinstance(stat, dict) and isinstance(stat.get("fields"), dict):
                        changed = False
                        for k, v in stat["fields"].items():
                            if isinstance(v, list):
                                nv = [repl if e == marker else e for e in v]
                                if nv != v:
                                    stat["fields"][k] = nv
                                    changed = True
                        if changed:
                            sc.write_text(json.dumps(stat, indent=2) + "\n", encoding="utf-8")

        _write_json(root, "sheets.json", sheets_json)
        if checks_json or (root / "checks.json").exists():
            _write_json(root, "checks.json", checks_json)

    return _apply(mid, mutate, dry_run=dry_run, migration=migration, pre_swap=pre_swap,
                  impact=True)


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
    return _apply(mid, mutate, dry_run=dry_run, impact=True, sample=True)


def delete_group(mid: str, gid: str, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        scope = _group_scope(data, gid)
        old = data["groups"].pop(gid, None)
        _write_json(root, "sheets.json", data)
        _prune_layout(root, in_scope=scope, group=gid,
                      names=_field_keys(old) if isinstance(old, dict) else set())
    return _apply(mid, mutate, dry_run=dry_run, impact=True)


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
    return _apply(mid, mutate, dry_run=dry_run, impact=True, sample=True)


def delete_sheet_type(mid: str, tid: str, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_sheets(root)
        old = data["sheet_types"].pop(tid, None)
        _write_json(root, "sheets.json", data)
        _prune_layout(root, in_scope={tid}, drop_type=tid,
                      names=_field_keys(old) if isinstance(old, dict) else set())
    return _apply(mid, mutate, dry_run=dry_run, impact=True)


# ---- check, rule, content writers ----


def upsert_check(mid: str, check_id: str, check: dict, *, dry_run: bool = False) -> dict:
    if not isinstance(check_id, str) or not check_id or check_id == "_defaults":
        return {"ok": False, "errors": [f"bad check id {check_id!r}"], "display_errors": []}
    def mutate(root: Path) -> None:
        data = _read_json(root, "checks.json")
        data[check_id] = check
        _write_json(root, "checks.json", data)
    return _apply(mid, mutate, dry_run=dry_run)


def delete_check(mid: str, check_id: str, *, dry_run: bool = False,
                 pre_swap=None) -> dict:
    def mutate(root: Path) -> None:
        data = _read_json(root, "checks.json")
        data.pop(check_id, None)
        _write_json(root, "checks.json", data)
    return _apply(mid, mutate, dry_run=dry_run, pre_swap=pre_swap)


def set_check_defaults(mid: str, defaults: dict, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        data = _read_json(root, "checks.json")
        if defaults:
            data["_defaults"] = defaults
        else:
            data.pop("_defaults", None)
        _write_json(root, "checks.json", data)
    return _apply(mid, mutate, dry_run=dry_run)


def _rule_meta(flags: dict) -> dict:
    meta: dict = {}
    if flags.get("always"):
        meta["always"] = "true"
    if flags.get("on_roll"):
        meta["on_roll"] = "true"
    if flags.get("keys"):
        meta["keys"] = ", ".join(flags["keys"])
    if flags.get("sheet_types"):
        meta["sheet_types"] = ", ".join(flags["sheet_types"])
    return meta


def upsert_rule(mid: str, slug: str, flags: dict, body: str, *,
                dry_run: bool = False) -> dict:
    if not modules._safe_mid(slug if isinstance(slug, str) else ""):
        return {"ok": False, "errors": [f"bad rules slug {slug!r}"], "display_errors": []}
    def mutate(root: Path) -> None:
        (root / "rules").mkdir(exist_ok=True)
        (root / "rules" / f"{slug}.md").write_text(
            dump_frontmatter(_rule_meta(flags or {}), body), encoding="utf-8")
    return _apply(mid, mutate, dry_run=dry_run)


def delete_rule(mid: str, slug: str, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        p = root / "rules" / f"{slug}.md"
        if p.exists():
            p.unlink()
    return _apply(mid, mutate, dry_run=dry_run)


def upsert_content(mid: str, kind: str, content_id: str, *, name: str,
                   body: str, keys: str, fields: dict, sheet: dict | None,
                   dry_run: bool = False) -> dict:
    if kind not in modules.CONTENT_KINDS:
        return {"ok": False, "errors": [f"unknown content kind {kind!r}"], "display_errors": []}
    if not modules._safe_id_like(content_id):
        return {"ok": False, "errors": [f"bad content id {content_id!r}"], "display_errors": []}
    def mutate(root: Path) -> None:
        d = root / "content" / kind
        d.mkdir(parents=True, exist_ok=True)
        meta = {"name": name or content_id}
        if keys:
            meta["keys"] = keys
        for k, v in (fields or {}).items():
            if k not in ("name", "keys") and isinstance(v, str):
                meta[k] = v
        (d / f"{content_id}.md").write_text(dump_frontmatter(meta, body), encoding="utf-8")
        sidecar = d / f"{content_id}.sheet.json"
        if sheet:
            _write_json(root, f"content/{kind}/{content_id}.sheet.json",
                        {"sheet_type": sheet.get("sheet_type"),
                         "fields": sheet.get("fields", {})})
        elif sidecar.exists():
            sidecar.unlink()
    return _apply(mid, mutate, dry_run=dry_run)


def delete_content(mid: str, kind: str, content_id: str, *, dry_run: bool = False) -> dict:
    if kind not in modules.CONTENT_KINDS or not modules._safe_id_like(content_id):
        return {"ok": False, "errors": [f"unknown content {kind}/{content_id}"], "display_errors": []}
    def mutate(root: Path) -> None:
        d = root / "content" / kind
        for p in (d / f"{content_id}.md", d / f"{content_id}.sheet.json"):
            if p.exists():
                p.unlink()
    return _apply(mid, mutate, dry_run=dry_run, impact=True)


def set_layout(mid: str, layout: dict, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        _write_json(root, "layout.json", layout if isinstance(layout, dict) else {})
    return _apply(mid, mutate, dry_run=dry_run)


def set_theme(mid: str, theme: dict, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        _write_json(root, "theme.json", theme if isinstance(theme, dict) else {})
    return _apply(mid, mutate, dry_run=dry_run)
