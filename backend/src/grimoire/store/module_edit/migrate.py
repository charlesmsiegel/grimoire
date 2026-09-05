"""Crash recovery, the stage -> validate -> journal -> swap -> migrate cycle,
and the sheet migration that cycle replays.

Recovery and migration are one concern, not two: ``recover`` takes
``_campaign_locks``, ``_replay_journal`` calls ``_run_migration``, and
``_apply`` calls back into ``recover`` and ``_require_user_root``. The journal
being replayed *is* the migration journal, so there is no separate
``journal`` file to split them into -- that pair would just import each other.
"""

from __future__ import annotations

import json
import shutil
import uuid
from contextlib import contextmanager
from pathlib import Path

from .. import atomic, locks, logs, revision
from ..campaigns import paths as campaigns_paths
from ..campaigns import read as campaigns_read
from ..modules import binding as modules_binding
from ..modules import pack as modules_pack
from ..paths import safe_id
from ..sheets import paths as sheets_paths
from ..sheets import schema as sheets_schema
from ..worlds import paths as worlds_paths
from ..worlds import read as worlds_read
from .scope import _RenameCollision
from .staging import _M, _staging_root


def _require_user_root(mid: str) -> Path:
    root, source = modules_pack.pack_root(mid)  # raises ModuleNotFound
    if source != "user":
        raise modules_pack.ModuleError(
            "built-in modules cannot be edited — duplicate it first")
    return root


def recover() -> set[str]:
    """Replay leftover journals idempotently; delete staging debris. Runs
    under _M (startup + start of every edit). A journal is only written
    after staging validated, so the cases are exact (spec: Recovery).

    Journal replay can PUBLISH a pack and run its migration — in a live
    process (a crashed edit followed by more requests, not just startup)
    that must exclude LLM flows exactly like a normal swap, so any journal
    that will publish or migrate replays under all campaign locks (codex
    plan review: recovery without them re-opens the R1 race).

    Returns the ids of modules with a journal that is STUCK: parsed and
    safe, but its replay died on I/O -- a rename refused, a sheet rewrite
    failing under `_run_migration`. Such a journal is left where it is
    (replay is idempotent, so the next call finishes it) rather than
    propagated, because this runs from lifespan and from the head of every
    edit, and neither an aborted startup nor an exception past `_apply`'s
    result-dict contract is an answer. `_apply` refuses an edit to a module
    named here: journals replay in nonce order, which is random, so a second
    journal written behind a stuck one could replay first and leave the
    sheets renamed to a name the pack no longer has. A journal whose module
    could not be read is reported as `""`, which refuses every edit."""
    with _M:
        return _recover_locked()


def _recover_locked() -> set[str]:
    d = _staging_root()
    stuck: set[str] = set()
    if d.is_dir():
        journals = sorted(d.glob("*.journal.json"))
        quarantined: set[str] = set()
        if journals:
            with _campaign_locks():
                for jp in journals:
                    try:
                        _replay_journal(jp, quarantined)
                    except OSError as e:
                        # Counts as quarantined for the sweep below, since its
                        # staging dir may still be the only copy of the pack.
                        quarantined.add(jp.name)
                        stuck.add(_journal_mid(jp))
                        logs.record("error", __name__,
                                    f"module journal replay failed: {jp.name}: {e}",
                                    kind="module_edit")
        # non-journaled debris (crash before journal write); no edit can be
        # in flight here — edits hold _M for their whole operation. If ANY
        # journal was quarantined we cannot know which dirs it references,
        # so the sweep is skipped entirely — a torn journal's staging/trash
        # may hold the only copy of a missing live module (codex plan
        # review round 2). Quarantined debris is a human-inspectable
        # leftover, not a correctness hazard. A journal quarantined on a
        # PRIOR run leaves no trace in `quarantined` (that set is local to
        # this call) — a pre-existing `*.journal.bad` file must disable the
        # sweep just as effectively, or the next edit's recover() call would
        # destroy the very staging dir the quarantine was preserving (P1-1).
        if not quarantined and not any(d.glob("*.journal.bad")):
            for p in list(d.iterdir()):
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
    return stuck


def _read_journal(jp: Path) -> dict | None:
    try:
        j = json.loads(jp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError):
        return None
    return j if isinstance(j, dict) else None


def _journal_mid(jp: Path) -> str:
    """The module a journal is for, or "" when that cannot be read."""
    j = _read_journal(jp)
    mid = str(j.get("mid") or "") if j else ""
    return mid if modules_pack._safe_mid(mid) else ""


_MIGRATION_OPS = ("field", "sheet_type", "content")


def _well_formed_migration(mig: object) -> bool:
    """Does a journal's `migration` carry every key its op will read?

    `_migrate_file` indexes `from`/`to` (and `kind`, for content) without
    checking, which is right for a migration `rename` built a moment ago and
    wrong for one read back from disk: a journal torn mid-write can parse as
    JSON and still lack a key, and a KeyError out of replay is exactly the
    startup abort the OSError handling in `recover` exists to prevent.
    """
    if not isinstance(mig, dict) or mig.get("op") not in _MIGRATION_OPS:
        return False
    if not isinstance(mig.get("from"), str) or not isinstance(mig.get("to"), str):
        return False
    if mig["op"] == "field" and not isinstance(mig.get("sheet_types"), list):
        return False
    return not (mig["op"] == "content" and not isinstance(mig.get("kind"), str))


def _replay_journal(jp: Path, quarantined: set[str]) -> None:
    """Replay one journal. A journal that fails to parse, carries an unsafe
    mid/nonce, or names a migration missing what its op reads is QUARANTINED
    (renamed .journal.bad, its nonce dir kept) — never acted on: deriving
    paths from a torn journal could rmtree the whole recovery area (nonce ''
    → base == .module-staging) or walk outside it (codex plan review round
    2), and replaying a torn migration would raise out of startup."""
    d = _staging_root()
    j = _read_journal(jp)
    mid = str(j.get("mid") or "") if j else ""
    nonce = str(j.get("nonce") or "") if j else ""
    migration = j.get("migration") if j else None
    if (not modules_pack._safe_mid(mid) or not safe_id(nonce)
            or (migration is not None and not _well_formed_migration(migration))):
        quarantined.add(jp.name)
        jp.rename(jp.with_suffix(".bad"))
        return
    assert j is not None  # a safe mid parsed out of it
    base = d / nonce
    staging = base / mid
    live = modules_pack.user_dir() / mid
    published = False
    if live.exists() and staging.exists():
        pass  # pre-swap crash: discard the edit (and its migration)
    elif not live.exists() and staging.exists():
        live.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(live)
        published = True
    elif live.exists():
        published = True  # post-swap crash: trash cleanup + migration
    if published and isinstance(migration, dict):
        # `recovering`: the dead pass may have renamed some sheets already, and
        # an idempotent migration cannot tell which -- see `_run_migration`.
        _run_migration(mid, migration, recovering=True)
    shutil.rmtree(base, ignore_errors=True)
    jp.unlink(missing_ok=True)


def _sheet_files(mid: str):
    """Yield (Path, cid|None) for every stored sheet governed by this module:
    each world's <world>/sheets/<mid>/*.json, plus each bound campaign's
    <campaign>/sheets/*.json (bound = resolve(cid) == mid)."""
    for w in worlds_read.list_worlds():
        d = worlds_paths.world_root(w["id"]) / "sheets" / mid
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                yield p, None
    for c in campaigns_read.list_campaigns():
        cid = c["id"]
        if modules_binding.resolve(cid) != mid:
            continue
        d = campaigns_paths.campaign_root(cid) / "sheets"
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
    # `data["fields"]` is reassigned only by the two ops that edit it. A type
    # rename touches `sheet_type` alone, and writing `fields` back from that
    # branch replaced a malformed value (a list, a string) with `{}` -- a
    # migration quietly editing what it did not migrate. The reader flags such
    # a sheet as invalid either way; the file should still say why.
    if op == "field":
        if data.get("sheet_type") in (mig.get("sheet_types") or []) \
                and mig["from"] in fields and mig["to"] not in fields:
            fields[mig["to"]] = fields.pop(mig["from"])
            data["fields"] = fields
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
                data["fields"] = fields
                changed = True
    if changed:
        data["gen"] = uuid.uuid4().hex
        # Through the sheet writer's own function, so a migrated sheet is
        # byte-for-byte what a save would have written (the pack-file writer
        # `_write_json` ends with a newline; a sheet does not). `store.atomic`
        # underneath it discards its temp on any failure, so a crash between
        # write and replace leaves no stray file beside the sheet.
        sheets_paths._atomic_write_json(p, data)
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


def _sidecar_stats_at(root: Path) -> list[dict]:
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


def _impact(mid: str, staged_pack: dict, migration: dict | None, staging_root: Path,
           affected_types: set[str] | None = None) -> dict:
    """Dry-run impact scan: {"sheet_types", "sheets_migrated",
    "sheets_newly_invalid", "dangling_refs"}. Newly-invalid = stored sheets
    valid against the live pack but invalid against the staged one; dangling
    = ref values whose content id exists live but not staged, counted over
    stored sheets AND content stat sidecars. Sidecars are read from
    `staging_root` (the STAGED copy, already rewritten by the mutate step) —
    not the live pack: during a content rename the staged sidecars already
    point at the new id, and a deleted entry's own sidecar is already gone
    from staging, so neither would otherwise be miscounted as dangling
    against the still-live id (P2-2)."""
    live_pack = modules_pack.load_pack(mid)
    newly_invalid = 0
    dangling = 0
    staged_ids = _content_ids(staged_pack)
    live_ids = _content_ids(live_pack)
    migrated = 0
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
            # Counted here, on the raw file, before the preview rewrites it:
            # one walk over the stored sheets answers both questions.
            if _would_migrate(data, migration):
                migrated += 1
            mig_fields = dict(fields)
            _migrate_preview(mig_fields, data, migration)
            st = data.get("sheet_type")
            fields = mig_fields
        live_errs = sheets_schema.instance_errors(live_pack, _file_kind(p), st, fields)
        staged_errs = sheets_schema.instance_errors(staged_pack, _file_kind(p), st, fields)
        if not live_errs and staged_errs:
            newly_invalid += 1
        for ref in _iter_ref_values(fields):
            if ":module:" in ref and ref in live_ids and ref not in staged_ids:
                dangling += 1
    for stat in _sidecar_stats_at(staging_root):
        for ref in _iter_ref_values(stat.get("fields") or {}):
            if ":module:" in ref and ref in live_ids and ref not in staged_ids:
                dangling += 1
    out = {"sheet_types": [], "sheets_migrated": 0,
           "sheets_newly_invalid": newly_invalid, "dangling_refs": dangling}
    if migration:
        out["sheet_types"] = list(migration.get("sheet_types")
                                  or ([migration["from"]] if migration["op"] == "sheet_type" else []))
        out["sheets_migrated"] = migrated
    elif affected_types:
        # ordinary (non-rename) schema edits carry no migration, but the
        # writer knows exactly which sheet types it touched — surface that
        # so the UI's confirm gate can name them (P2-3).
        out["sheet_types"] = sorted(affected_types)
    return out


def _run_migration(mid: str, migration: dict, *, recovering: bool = False) -> dict:
    """Sheet migration for rename ops: rewrite every stored sheet governed by
    this module that the migration op touches, bumping `gen` on each changed
    file. Idempotent (old key absent ⇒ no-op) so journal replay after a crash
    is safe to repeat.

    A campaign whose sheet was rewritten also gets its write token stamped
    (#409). This is the one thing a module edit does that is NOT an edit to
    what a campaign *inherits*: `<campaign>/sheets/*.json` is the campaign's
    own file, rewritten in place, and no response line covers it -- the route
    is under `/api/modules/...`, which the activity middleware never sees, and
    journal replay after a crash has no request behind it at all. Left
    unstamped, a token read before a field rename stayed valid after it, and a
    priced fork or advance could not tell (Codex review, correcting the reason
    given when `module_edit` was first left out).

    Stamped per changed campaign rather than per file, and only for a file that
    actually changed: `_migrate_file` answers False for a sheet the op does not
    touch, and a world's own sheets carry no cid at all.

    `recovering` widens that to every campaign this module governs a sheet for,
    and journal replay is the one caller that passes it. The migration is
    idempotent by design -- an already-renamed sheet answers False -- which is
    what makes replay safe to repeat and also what makes "changed" useless as a
    stamp condition here: a pass interrupted between rewriting a campaign's
    sheet and stamping it leaves that sheet renamed, so the replay reads it as
    untouched and its token would stay current forever (Codex review). Recovery
    cannot tell what the dead pass finished, so it stamps everything it might
    have. That is an over-bump on a rare path -- a re-price for whoever was
    holding one, and the direction this value is deliberately wrong in.
    """
    migrated, skipped, touched = 0, [], []
    # The stamps are in a `finally` for the reason `sync.demote`'s are: the loop
    # rewrites one campaign's sheet at a time, so a later one raising -- an
    # ordinary `OSError` out of `atomic.write_text`, not only a process death --
    # leaves the earlier ones written and unwinds past a post-loop stamp. The
    # journal survives, but nothing replays it until the next module edit or a
    # restart, and a fork or an advance priced in that window would pass
    # (Codex review). `_migrate_file` writes atomically, so the sheet that
    # raised did not land and is rightly absent from `touched`.
    try:
        for p, cid in _sheet_files(mid):
            got = _migrate_file(p, migration)
            if got is None:
                skipped.append(str(p))
            elif got:
                migrated += 1
            if cid is not None and (recovering or got) and cid not in touched:
                touched.append(cid)
    finally:
        for cid in touched:
            revision.bump(cid)
    return {"migrated": migrated, "skipped": skipped}


@contextmanager
def _campaign_locks():
    """Every campaign's sheet lock: the User-edit vs LLM-play exclusion.

    Order is *not* irrelevant, as this docstring used to claim. Once campaign
    locks are cross-process (#234) two multi-lock holders acquiring the same
    campaigns in different orders deadlock, and the global _M no longer masks
    it. `locks.hold_all` sorts, and is the single place that rule lives.

    Known limit, pre-existing: the enumeration is a snapshot, so a campaign
    another process creates afterwards is not covered, and campaign deletion
    takes no lock at all.
    """
    with locks.hold_all(c["id"] for c in campaigns_read.list_campaigns()):
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
        defaults = sheets_schema.default_fields(pack["sheets"], tid)
        errs: list[str] = []
        derived = sheets_schema._compute_derived(pack["sheets"], tid, defaults, errs)
        out[tid] = {"fields": defaults, "derived": derived}
    return out


def _apply(mid: str, mutate, *, dry_run: bool = False,
           migration: dict | None = None, pre_swap=None,
           impact: bool = False, sample: bool = False,
           affected_types: set[str] | None = None) -> dict:
    """stage -> mutate -> validate -> (locks) -> journal -> swap -> migrate.

    mutate(staging_root) edits the staged copy in place. pre_swap(pack) runs
    under all campaign locks just before the journal and returns a list of
    blocking errors (e.g. rename collision scans). Rejection (validation or
    pre_swap) leaves the live pack byte-identical and no staging debris.
    impact=True computes a dry-run impact scan (schema-affecting writers and
    rename ops); sample=True additionally computes a per-sheet-type sample
    (schema defaults + derived) after a clean validation, for sheets.json
    dry-runs. affected_types (group/sheet-type upserts+deletes, computed by
    the writer from the LIVE pack before mutation) surfaces impact.sheet_types
    for non-migration writers, which otherwise report it empty (P2-3)."""
    with _M:
        stuck = recover()
        if mid in stuck or "" in stuck:
            why = "a previous edit to this module is still being recovered; retry in a moment"
            return {"ok": False, "display_errors": [], "errors": [why]}
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
            pack = modules_pack.load_pack_at(staging, mid)
            extra: dict = {}
            if impact:
                extra["impact"] = _impact(mid, pack, migration, staging, affected_types)
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
                atomic.write_text(jp, json.dumps(
                    {"mid": mid, "nonce": nonce, "migration": migration}))
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
