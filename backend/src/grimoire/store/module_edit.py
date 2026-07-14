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
