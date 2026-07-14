# Mechanics Phase 8 — Module Authoring UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In-app editing of user-library mechanics modules across every pack
file (manifest, groups, sheet types, checks, rules, content, layout, theme),
with rename-aware sheet migration, dry-run impact preview, duplicate, and
zip export/import.

**Architecture:** Backend: a new `store/module_edit.py` owns all mutation via
one primitive — copy the live pack to staging, apply the edit, validate the
staged root through the same loader `resolve()` trusts, then publish by a
journaled whole-directory swap under one global module-edit RLock plus every
campaign's sheet lock (the User-edit vs LLM-play exclusion). Rename ops
rewrite scope-bound references and migrate stored sheets journal-resumably.
Routes are per-section PUT/DELETEs plus a rename op, all with `dry_run`.
Frontend: `ModulesView` gains Edit/Duplicate/Export/Import actions; a new
`ModuleEditor` hosts eight sections, each a mini list/detail with debounced
dry-run validation and an impact confirm.

**Tech Stack:** FastAPI + pydantic (v1/v2-agnostic), pytest
(`GRIMOIRE_HOME`-isolated); React + TypeScript, vitest; stdlib-only store
code (`zipfile`, `shutil`, `threading`, `ast`-free regex rewriting).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-13-mechanics-phase8-authoring-ui-design.md` (codex-approved after 10 review rounds, then narrowed to the two-actor threat model — every decision traces to its Decisions table).
- **Concurrency threat model: exactly two concurrent actors — the User (UI) and the LLM (play flows).** Do not add machinery for two User actions racing (no barriers, no sorted-lock disciplines, no world-write locks). The lock story is: one global re-entrant module-edit lock `_M`, plus every campaign's `sheets.lock_for(cid)` held across each swap; LLM flows hold their single campaign lock across resolve→load→compute and never take `_M`.
- The on-disk module must **never be invalid**: every writer validates the full staged pack and rejects (nothing written) on any `errors`. `display_errors` never reject.
- `store/modules.py::load_pack` (and the new `load_pack_at`) must never raise on malformed content — only `ModuleNotFound`/`ContentNotFound` propagate.
- Builtins are immutable: every `module_edit` writer raises `ModuleError` for a builtin-sourced mid (message: `"built-in modules cannot be edited — duplicate it first"`).
- Store code stays pure-stdlib, pydantic-free, filesystem via `store.paths.home()`/`modules.user_dir()`/`pack_root()` (Android-safe).
- pydantic models in `routes.py` stay v1/v2-agnostic: plain `BaseModel` fields only, no `Field`/validators/`ConfigDict`; dump via `routes._dump` where echoed.
- Module-edit result dicts come back HTTP 200 with `"ok": false` + `errors` on validation rejection; 400 is reserved for `ModuleError` (builtin), 404 for unknown ids.
- Reserved module id: `none` (campaign binding sentinel) — id allocation must never produce it.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`; run `backend/.venv/Scripts/python.exe -m pytest backend -q`. Frontend: from `frontend/`, `npx vitest run` and `npx tsc -b` (never `npx --prefix frontend`).
- Privacy: all fixture names invented (Realm/Mara/Seraphine/Winifred/Saltmarch-style placeholders) — never a real world/campaign/character name.
- Reference modules `d20-basic`/`pool-basic` stay untouched (they are builtins — the editor must refuse them).

---

## File Structure

Backend:
- Modify `backend/src/grimoire/store/modules.py` — `load_pack_at` refactor, `manifest.notes`, reserved contextual names in `_validate_field`/`_validate_derived`.
- Create `backend/src/grimoire/store/module_edit.py` — global lock, staging/journal/swap/recovery, section writers, rename ops + migration, impact, duplicate/export/import, id allocation.
- Modify `backend/src/grimoire/store/sheets.py` — `instance_errors()` (full read-time judgment vs an arbitrary pack), unknown-key rejection in `_checked_write`, world CAS (`write_world` `expected`, `delete_world` `expected_gen`).
- Modify `backend/src/grimoire/store/proposals.py` — lock domain unification with `sheets.lock_for`.
- Modify `backend/src/grimoire/store/checks.py` — `resolve_check` under the campaign lock.
- Modify `backend/src/grimoire/store/context.py` — `_mechanics` under the campaign lock.
- Modify `backend/src/grimoire/routes.py` — module-edit routes + models; `_continuation_rule_bodies` under the campaign lock; world sheet route CAS plumbing; startup recovery hook in `backend/src/grimoire/main.py`.
- Create `backend/tests/test_module_edit.py`; modify `backend/tests/test_modules_store.py`, `backend/tests/test_sheets_store.py`, `backend/tests/test_routes.py`.

Frontend:
- Modify `frontend/src/api/client.ts` — module-edit types + API fns; world deleteSheet gen.
- Modify `frontend/src/routes/ModulesView.tsx` — Import/Export/Duplicate/Edit actions, `ModuleEditor` mount.
- Create `frontend/src/components/ModuleEditor.tsx` — section nav + shared save/dry-run/confirm harness + Manifest section.
- Create `frontend/src/components/ModuleSchemaEditor.tsx` — Groups + Sheet types sections (field/derived row editors, rename affordance).
- Create `frontend/src/components/ModuleRulesEditor.tsx` — Checks + `_defaults` + Rules sections.
- Create `frontend/src/components/ModuleContentEditor.tsx` — Content section (with stat block).
- Create `frontend/src/components/ModuleDisplayEditor.tsx` — Layout (JSON + live preview) + Theme sections.
- Tests: modify `frontend/src/routes/ModulesView.test.tsx`; create `frontend/src/components/ModuleEditor.test.tsx`, `ModuleSchemaEditor.test.tsx`, `ModuleRulesEditor.test.tsx`, `ModuleContentEditor.test.tsx`, `ModuleDisplayEditor.test.tsx`.

Docs/skill:
- Modify `.claude/skills/create-mechanics-module/SKILL.md` — note the in-app editor exists.

No new CSS beyond a handful of layout helpers reusing existing tokens; new
elements reuse `chips`/`chip`/`chip on`/`side-section`/`field-hint`/`banner`/
`row`/`form-actions` classes already used by `ModulesView.tsx`/`SheetEditor.tsx`.

---

### Task 1: `load_pack_at` refactor + `manifest.notes`

**Files:**
- Modify: `backend/src/grimoire/store/modules.py:612-680` (`load_pack`)
- Test: `backend/tests/test_modules_store.py`

**Interfaces:**
- Produces: `modules.load_pack_at(root: Path, mid: str, source: str = "user") -> dict` — identical output to `load_pack` but reads an explicit root (staging validation goes through the *identical* code path `resolve()` trusts). `load_pack(mid)` becomes a thin wrapper. `pack["manifest"]` gains `"notes"`: the `module.md` body (authoring notes, for the manifest editor round-trip).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_modules_store.py`:

```python
def test_load_pack_at_explicit_root(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = modules.create_module("Realm System")
    root = modules.user_dir() / mid
    pack = modules.load_pack_at(root, mid)
    assert pack["id"] == mid
    assert pack["errors"] == []
    assert pack == modules.load_pack(mid)


def test_manifest_notes_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = modules.create_module("Realm System")
    p = modules.user_dir() / mid / "module.md"
    text = p.read_text(encoding="utf-8")
    p.write_text(text + "Authoring notes body.\n", encoding="utf-8")
    pack = modules.load_pack(mid)
    assert "Authoring notes body." in pack["manifest"]["notes"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -k "load_pack_at or manifest_notes" -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'load_pack_at'`; `KeyError: 'notes'`.

- [ ] **Step 3: Refactor**

In `backend/src/grimoire/store/modules.py`, rename the existing `load_pack` body to `load_pack_at` with an explicit root, keeping every line the same except the first and the manifest dict:

```python
def load_pack(mid: str) -> dict:
    root, source = pack_root(mid)
    return load_pack_at(root, mid, source)


def load_pack_at(root: Path, mid: str, source: str = "user") -> dict:
    """load_pack against an explicit root — the staging validator uses this
    so a staged edit is judged by the identical code path resolve() trusts."""
    errors: list[str] = []
    ...  # body unchanged from the old load_pack, minus its pack_root() line
```

and change the manifest line in the pack dict to:

```python
        "manifest": {**meta, "id": mid, "notes": _body},
```

(the body variable is already parsed as `_body`; rename it to `body_text` if
the underscore prefix trips a linter, and use it in both places).

- [ ] **Step 4: Run the full modules test file**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_modules_store.py -q`
Expected: PASS (all — the refactor must not change any existing behavior).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "refactor(modules): load_pack_at explicit-root variant + manifest notes"
```

---

### Task 2: `module_edit.py` core — lock, staging, swap, journal, recovery, `set_manifest`

**Files:**
- Create: `backend/src/grimoire/store/module_edit.py`
- Modify: `backend/src/grimoire/main.py` (startup recovery)
- Test: `backend/tests/test_module_edit.py` (new file)

**Interfaces:**
- Consumes: `modules.load_pack_at(root, mid)` (Task 1), `modules.pack_root`, `modules.user_dir`, `sheets.lock_for(cid)`, `campaigns.list_campaigns()`.
- Produces:
  - `module_edit._M: threading.RLock` — the global module-edit lock. `module_edit.locked()` context manager (used later by export and routes).
  - `module_edit.recover() -> None` — journal replay; idempotent; called at startup (main.py lifespan) and at the start of every edit.
  - `module_edit._apply(mid, mutate, *, dry_run=False, migration=None, pre_swap=None) -> dict` — the stage→validate→swap primitive. `mutate(staging_root: Path)` applies the edit to the staged copy. `migration` is an optional dict executed by `_run_migration` after the swap (Task 8; until then `_apply` just journals and ignores it — pass `None`). `pre_swap(staging_pack: dict) -> list[str]` runs under all campaign locks before the journal; non-empty ⇒ abort with those errors. Returns `{"ok": bool, "errors": [...], "display_errors": [...]}`.
  - `module_edit.set_manifest(mid, name, description, version, dice, notes, *, dry_run=False) -> dict`.
  - Result-dict convention for every writer in this module: HTTP-friendly plain dict, `ok=False` + `errors` on rejection, never a raise except `ModuleError` (builtin) / `ModuleNotFound`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_module_edit.py`:

```python
import json
import threading

import pytest

from grimoire.store import campaigns, module_edit, modules, sheets, worlds


def _mk(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return modules.create_module("Realm System")


def test_set_manifest_round_trip(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    res = module_edit.set_manifest(mid, name="Realm System", description="d",
                                   version="0.2", dice="1d20", notes="notes body")
    assert res["ok"] is True and res["errors"] == []
    pack = modules.load_pack(mid)
    assert pack["manifest"]["version"] == "0.2"
    assert pack["manifest"]["notes"].strip() == "notes body"


def test_set_manifest_rejects_invalid(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    before = (modules.user_dir() / mid / "module.md").read_text(encoding="utf-8")
    res = module_edit.set_manifest(mid, name="", description="", version="",
                                   dice="", notes="")
    assert res["ok"] is False
    assert any("requires a name" in e for e in res["errors"])
    # live pack untouched, no staging debris
    assert (modules.user_dir() / mid / "module.md").read_text(encoding="utf-8") == before
    staging = tmp_path / ".module-staging"
    assert not staging.exists() or not any(staging.iterdir())


def test_dry_run_writes_nothing(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    res = module_edit.set_manifest(mid, name="Renamed", description="", version="",
                                   dice="", notes="", dry_run=True)
    assert res["ok"] is True
    assert modules.load_pack(mid)["manifest"]["name"] == "Realm System"


def test_builtin_refused(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with pytest.raises(modules.ModuleError):
        module_edit.set_manifest("d20-basic", name="X", description="",
                                 version="", dice="", notes="")


def test_recover_pre_swap_discards(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    # Simulate a crash after journal write, before any rename: live + staging.
    base = tmp_path / ".module-staging" / "nonce1"
    staging = base / mid
    staging.mkdir(parents=True)
    (staging / "module.md").write_text("---\nname: Ghost\n---\n", encoding="utf-8")
    (tmp_path / ".module-staging" / "nonce1.journal.json").write_text(
        json.dumps({"mid": mid, "nonce": "nonce1", "migration": None}), encoding="utf-8")
    module_edit.recover()
    assert modules.load_pack(mid)["manifest"]["name"] == "Realm System"
    assert not (tmp_path / ".module-staging" / "nonce1.journal.json").exists()
    assert not base.exists()


def test_recover_between_renames_publishes(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    # Simulate: live renamed to trash, staging not yet renamed in.
    base = tmp_path / ".module-staging" / "nonce2"
    trash = base / "trash" / mid
    trash.parent.mkdir(parents=True)
    live = modules.user_dir() / mid
    live.rename(trash)
    staging = base / mid
    staging.mkdir(parents=True)
    (staging / "module.md").write_text("---\nname: Published\n---\n", encoding="utf-8")
    (staging / "sheets.json").write_text('{"groups": {}, "sheet_types": {}}', encoding="utf-8")
    (tmp_path / ".module-staging" / "nonce2.journal.json").write_text(
        json.dumps({"mid": mid, "nonce": "nonce2", "migration": None}), encoding="utf-8")
    module_edit.recover()
    assert modules.load_pack(mid)["manifest"]["name"] == "Published"
    assert not base.exists()


def test_recover_post_swap_cleans_trash(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    base = tmp_path / ".module-staging" / "nonce3"
    trash = base / "trash" / mid
    trash.mkdir(parents=True)
    (trash / "module.md").write_text("---\nname: Old\n---\n", encoding="utf-8")
    (tmp_path / ".module-staging" / "nonce3.journal.json").write_text(
        json.dumps({"mid": mid, "nonce": "nonce3", "migration": None}), encoding="utf-8")
    module_edit.recover()
    assert modules.load_pack(mid)["manifest"]["name"] == "Realm System"
    assert not base.exists()


def test_malformed_journal_quarantined_not_destructive(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    d = tmp_path / ".module-staging"
    keep = d / "aaaabbbbccccddddaaaabbbbccccdddd" / mid
    keep.mkdir(parents=True)
    (keep / "module.md").write_text("---\nname: Rescue\n---\n", encoding="utf-8")
    (d / "torn.journal.json").write_text("{not json", encoding="utf-8")
    module_edit.recover()
    assert (d / "torn.journal.bad").exists()       # quarantined, not deleted
    assert keep.exists()                           # recovery data preserved
    assert d.exists()                              # never rmtree'd wholesale


def test_edit_excludes_campaign_locked_consumer(monkeypatch, tmp_path):
    """User-vs-LLM exclusion: an edit blocks while a campaign lock is held."""
    mid = _mk(monkeypatch, tmp_path)
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Run", wid)
    order: list[str] = []

    def edit():
        module_edit.set_manifest(mid, name="Realm System", description="x",
                                 version="", dice="", notes="")
        order.append("edit-done")

    with sheets.lock_for(cid):
        t = threading.Thread(target=edit)
        t.start()
        t.join(timeout=0.3)
        assert t.is_alive()          # edit is waiting on the campaign lock
        order.append("lock-released")
    t.join(timeout=5)
    assert not t.is_alive()
    assert order == ["lock-released", "edit-done"]
```

(Signatures verified against the store: `worlds.create_world(name) -> wid`,
`campaigns.create_campaign(name, world_id, ...) -> cid`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -v`
Expected: FAIL — `ImportError: cannot import name 'module_edit'`.

- [ ] **Step 3: Implement the core**

Create `backend/src/grimoire/store/module_edit.py`:

```python
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


_NONCE_RE = re.compile(r"[0-9a-f]{32}\Z")


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
    if not modules._safe_mid(mid) or not _NONCE_RE.match(nonce):
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
```

In `backend/src/grimoire/main.py`, find the FastAPI lifespan/startup hook (or
app creation) and add, before the app starts serving:

```python
from .store import module_edit
module_edit.recover()
```

(match the file's existing startup idiom — if there is a `lifespan` async
context manager, call it in the pre-yield section; if not, module scope right
after `app = FastAPI(...)` is acceptable for this single-process app).

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -v`
Expected: PASS (all 8).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/module_edit.py backend/src/grimoire/main.py backend/tests/test_module_edit.py
git commit -m "feat(module_edit): staged, journaled, lock-excluded module publication core"
```

---

### Task 3: Group + sheet-type writers, layout cascade prune

**Files:**
- Modify: `backend/src/grimoire/store/module_edit.py`
- Test: `backend/tests/test_module_edit.py`

**Interfaces:**
- Consumes: `_apply` (Task 2).
- Produces:
  - `upsert_group(mid, gid, group: dict, *, dry_run=False) -> dict`
  - `delete_group(mid, gid, *, dry_run=False) -> dict`
  - `upsert_sheet_type(mid, tid, sheet_type: dict, *, dry_run=False) -> dict`
  - `delete_sheet_type(mid, tid, *, dry_run=False) -> dict`
  - `_read_json(root, name) -> dict` / `_write_json(root, name, data)` staging helpers (pretty-printed, `indent=2`, trailing newline).
  - `_prune_layout(layout: dict, *, group: str | None = None, names: set[str] = frozenset()) -> dict` — removes `group` nodes naming a deleted group and entries in `fields`/`derived` arrays naming deleted keys, drops emptied nodes/containers, fragments included (cascade-cosmetic rule). Deleting a sheet type also drops `layout["sheet_types"][tid]`.
  - Delete semantics: fatal references (group in a type's `groups`/check `requires`; field named in derived/check roll/creation/advancement; etc.) are rejected *by the staged validation itself* — no special code, the rejection carries the validator's message naming the referee.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_module_edit.py` (top of file gains a richer fixture):

```python
GROUP = {"label": "Attributes",
         "fields": [{"key": "strength", "label": "Strength", "type": "dots", "max": 5},
                    {"key": "essence", "label": "Essence", "type": "resource", "max": 10}],
         "derived": {"might": "strength * 2"}}
TYPE = {"label": "Warden", "kind": "characters", "groups": ["attributes"],
        "fields": [{"key": "notes_line", "label": "Notes", "type": "text"}],
        "derived": {"guard": "strength + 1"}}


def _mk_schema(monkeypatch, tmp_path):
    mid = _mk(monkeypatch, tmp_path)
    assert module_edit.upsert_group(mid, "attributes", GROUP)["ok"]
    assert module_edit.upsert_sheet_type(mid, "warden", TYPE)["ok"]
    return mid


def test_upsert_group_and_type(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    pack = modules.load_pack(mid)
    assert pack["errors"] == []
    assert pack["sheets"]["groups"]["attributes"]["label"] == "Attributes"
    assert pack["sheets"]["sheet_types"]["warden"]["kind"] == "characters"


def test_upsert_group_bad_expression_rejected(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    bad = {**GROUP, "derived": {"might": "strength +"}}
    res = module_edit.upsert_group(mid, "attributes", bad)
    assert res["ok"] is False
    assert any("might" in e for e in res["errors"])
    assert modules.load_pack(mid)["errors"] == []  # live pack still valid


def test_delete_group_with_fatal_ref_rejected(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.delete_group(mid, "attributes")
    assert res["ok"] is False
    assert any("attributes" in e for e in res["errors"])  # named referee


def test_delete_type_then_group_cascades_layout(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    layout = {"sheet_types": {"warden": {"column": [
        {"group": "attributes"},
        {"fields": ["notes_line"]},
        {"derived": ["guard"]}]}}}
    (modules.user_dir() / mid / "layout.json").write_text(
        json.dumps(layout), encoding="utf-8")
    assert module_edit.delete_sheet_type(mid, "warden")["ok"]
    pack = modules.load_pack(mid)
    assert pack["errors"] == []
    assert pack["layout"]["sheet_types"] == {}      # type's tree dropped
    assert pack["display_errors"] == []             # no dangling display refs
    assert module_edit.delete_group(mid, "attributes")["ok"]
    assert "attributes" not in modules.load_pack(mid)["sheets"]["groups"]


def test_delete_field_prunes_layout_entry(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    layout = {"sheet_types": {"warden": {"column": [
        {"group": "attributes"}, {"fields": ["notes_line"]}]}}}
    (modules.user_dir() / mid / "layout.json").write_text(
        json.dumps(layout), encoding="utf-8")
    slim = {**TYPE, "fields": []}  # drop notes_line (no fatal refs on it)
    assert module_edit.upsert_sheet_type(mid, "warden", slim)["ok"]
    pack = modules.load_pack(mid)
    assert pack["display_errors"] == []
    tree = json.dumps(pack["layout"])
    assert "notes_line" not in tree
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -k "group or type or field_prunes" -v`
Expected: FAIL — `AttributeError: ... no attribute 'upsert_group'`.

- [ ] **Step 3: Implement**

Add to `backend/src/grimoire/store/module_edit.py`:

```python
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
```

`_prune_layout`'s `names` path runs through the fragment-specialization
walk, so **this task also implements the three graph helpers the spec's
"Shared layout fragments" rule requires** — `_fragment_users(layout)`,
`_edit_tree(node, edit_fn, remap)`, and `_specialize_layout(layout,
in_scope, edit_fn)` — exactly as printed in Task 6's Step 3 (Task 6 then
*reuses* them for renames instead of defining them; copy the three
functions from that listing into this task's implementation verbatim).
Add a disjoint-scope regression test alongside the Step 1 tests:

```python
def test_prune_scoped_to_composing_types(monkeypatch, tmp_path):
    """Removing warden's notes_line must not touch a disjoint type's
    same-named field in ITS layout tree."""
    mid = _mk_schema(monkeypatch, tmp_path)
    other_group = {"label": "Spirit", "fields": [
        {"key": "notes_line", "label": "Notes", "type": "text"}]}
    other_type = {"label": "Medium", "kind": "characters",
                  "groups": ["spirit"], "fields": []}
    assert module_edit.upsert_group(mid, "spirit", other_group)["ok"]
    assert module_edit.upsert_sheet_type(mid, "medium", other_type)["ok"]
    layout = {"sheet_types": {
        "warden": {"fields": ["notes_line"]},
        "medium": {"fields": ["notes_line"]}}}
    (modules.user_dir() / mid / "layout.json").write_text(
        json.dumps(layout), encoding="utf-8")
    slim = {**TYPE, "fields": []}          # remove warden's notes_line
    assert module_edit.upsert_sheet_type(mid, "warden", slim)["ok"]
    raw = json.loads((modules.user_dir() / mid / "layout.json").read_text(encoding="utf-8"))
    assert "warden" not in raw["sheet_types"] \
        or "notes_line" not in json.dumps(raw["sheet_types"].get("warden"))
    assert raw["sheet_types"]["medium"] == {"fields": ["notes_line"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/module_edit.py backend/tests/test_module_edit.py
git commit -m "feat(module_edit): group + sheet-type writers with layout cascade prune"
```

---

### Task 4: Check, check-defaults, rules, and content writers

**Files:**
- Modify: `backend/src/grimoire/store/module_edit.py`
- Test: `backend/tests/test_module_edit.py`

**Interfaces:**
- Consumes: `_apply`, `_read_json`, `_write_json` (Tasks 2-3).
- Produces:
  - `upsert_check(mid, check_id, check: dict, *, dry_run=False)` / `delete_check(mid, check_id, *, dry_run=False)` — Task 7 adds the proposal guard to delete/rename; here delete is unguarded (test only pack-level effects).
  - `set_check_defaults(mid, defaults: dict, *, dry_run=False)` — writes/clears `checks.json`'s `_defaults` key (empty dict removes it).
  - `upsert_rule(mid, slug, flags: dict, body: str, *, dry_run=False)` / `delete_rule(mid, slug, *, dry_run=False)` — `flags` keys: `keys` (list[str] → CSV), `always`/`on_roll` (bool → `"true"`), `sheet_types` (list[str] → CSV). Slug must satisfy `modules._safe_mid`.
  - `upsert_content(mid, kind, content_id, *, name, body, keys, fields: dict, sheet: dict | None, dry_run=False)` / `delete_content(mid, kind, content_id, *, dry_run=False)` — writes `content/<kind>/<id>.md` (frontmatter: name, keys, plus `fields` entries as string scalars) and the `.sheet.json` sidecar when `sheet` is given (`{"sheet_type", "fields"}`), removes the sidecar when `sheet` is None. `kind` must be in `modules.CONTENT_KINDS` and `content_id` pass `modules._safe_id_like`; violations return `ok=False` with one error, not a raise.

- [ ] **Step 1: Write the failing tests**

```python
CHECK = {"label": "Guard Reflexes", "roll": "1d20 + {might}", "requires": ["attributes"]}


def test_upsert_and_delete_check(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_check(mid, "guard_reflexes", CHECK)["ok"]
    assert "guard_reflexes" in modules.load_pack(mid)["checks"]
    bad = {**CHECK, "roll": "1d20 + {nonsense}"}
    res = module_edit.upsert_check(mid, "guard_reflexes", bad)
    assert res["ok"] is False and any("nonsense" in e for e in res["errors"])
    assert module_edit.delete_check(mid, "guard_reflexes")["ok"]
    assert "guard_reflexes" not in modules.load_pack(mid)["checks"]


def test_check_defaults_round_trip(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.set_check_defaults(mid, {"difficulty": 12})["ok"]
    assert modules.load_pack(mid)["checks"]["_defaults"]["difficulty"] == 12
    assert module_edit.set_check_defaults(mid, {})["ok"]
    assert "_defaults" not in modules.load_pack(mid)["checks"]


def test_rule_round_trip_and_flags(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.upsert_rule(mid, "combat-basics",
                                  {"always": True, "keys": ["melee", "brawl"],
                                   "sheet_types": ["warden"]},
                                  "Swing first.")
    assert res["ok"]
    pack = modules.load_pack(mid)
    doc = next(r for r in pack["rules"] if r["id"] == "combat-basics")
    assert doc["always"] is True and doc["keys"] == ["melee", "brawl"]
    assert modules.read_rule(mid, "combat-basics")["body"].strip() == "Swing first."
    # unknown sheet_type flag rejects
    bad = module_edit.upsert_rule(mid, "combat-basics", {"sheet_types": ["ghost"]}, "x")
    assert bad["ok"] is False
    # delete blocked while a check references the doc
    assert module_edit.upsert_check(mid, "brawl", {**CHECK, "rules": ["combat-basics"]})["ok"]
    res = module_edit.delete_rule(mid, "combat-basics")
    assert res["ok"] is False and any("combat-basics" in e for e in res["errors"])
    assert module_edit.delete_check(mid, "brawl")["ok"]
    assert module_edit.delete_rule(mid, "combat-basics")["ok"]


def test_content_round_trip_with_sheet(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    item_type = {"label": "Relic", "kind": "items", "groups": [],
                 "fields": [{"key": "power", "type": "dots", "max": 5}]}
    assert module_edit.upsert_sheet_type(mid, "relic", item_type)["ok"]
    res = module_edit.upsert_content(
        mid, "items", "sunblade", name="Sunblade", body="A blade of dawn.",
        keys="sunblade, dawn", fields={},
        sheet={"sheet_type": "relic", "fields": {"power": 3}})
    assert res["ok"]
    got = modules.read_content(mid, "items", "sunblade")
    assert got["name"] == "Sunblade" and got["fields"] == {"power": 3}
    # invalid stat block rejects
    res = module_edit.upsert_content(
        mid, "items", "sunblade", name="Sunblade", body="x", keys="", fields={},
        sheet={"sheet_type": "relic", "fields": {"power": 9}})
    assert res["ok"] is False and any("power" in e for e in res["errors"])
    # delete removes md + sidecar
    assert module_edit.delete_content(mid, "items", "sunblade")["ok"]
    with pytest.raises(modules.ContentNotFound):
        modules.read_content(mid, "items", "sunblade")


def test_content_bad_kind_or_id(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_content(mid, "characters", "x", name="x", body="",
                                      keys="", fields={}, sheet=None)["ok"] is False
    assert module_edit.upsert_content(mid, "items", "../evil", name="x", body="",
                                      keys="", fields={}, sheet=None)["ok"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -k "check or rule or content" -v`
Expected: FAIL — missing attributes.

- [ ] **Step 3: Implement**

```python
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
    return _apply(mid, mutate, dry_run=dry_run)
```

- [ ] **Step 4: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/module_edit.py backend/tests/test_module_edit.py
git commit -m "feat(module_edit): check, defaults, rules, and content writers"
```

---

### Task 5: Layout + theme writers; reserved contextual names

**Files:**
- Modify: `backend/src/grimoire/store/module_edit.py`, `backend/src/grimoire/store/modules.py:103-126` (`_validate_field`), `:174-193` (`_validate_derived`)
- Test: `backend/tests/test_module_edit.py`, `backend/tests/test_modules_store.py`

**Interfaces:**
- Produces:
  - `module_edit.set_layout(mid, layout: dict, *, dry_run=False)` / `set_theme(mid, theme: dict, *, dry_run=False)` — whole-file replacement. Layout/theme problems are `display_errors` and never reject the save; the full gate still runs (a display write can't land on an otherwise-broken pack).
  - `modules.RESERVED_NAMES = ("difficulty", "modifier", "new")` — rejected as field keys (`_validate_field`) **and** derived names (`_validate_derived`), alongside the existing function-name rule (spec: Reserved contextual names; a derived named `new` is silently shadowed at advancement time, `difficulty`/`modifier` at check resolution).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_module_edit.py`:

```python
def test_set_layout_lands_with_display_errors(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.set_layout(mid, {"sheet_types": {"warden": {"fields": ["ghost_key"]}}})
    assert res["ok"] is True                       # display problems never reject
    assert any("ghost_key" in e["message"] for e in res["display_errors"])
    assert module_edit.set_layout(mid, {"sheet_types": {"warden": {"group": "attributes"}}})["ok"]
    pack = modules.load_pack(mid)
    assert "warden" in pack["layout"]["sheet_types"] and pack["display_errors"] == []


def test_set_theme_round_trip(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    theme = {"colors": {"bg": "#171a21", "ink": "#d8d2c4"}, "dots": "diamond"}
    assert module_edit.set_theme(mid, theme)["ok"]
    assert modules.load_pack(mid)["theme"]["dots"] == "diamond"
```

Add to `backend/tests/test_modules_store.py` (same `_sheets_error` idiom as
the existing reserved-key tests):

```python
def test_reserved_contextual_field_key(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["fields"].append(
            {"key": "difficulty", "type": "number"}))
    assert any("reserved" in e for e in errs)


def test_reserved_contextual_derived_name(monkeypatch, tmp_path):
    errs = _sheets_error(
        monkeypatch, tmp_path,
        lambda s: s["groups"]["attributes"]["derived"].update({"new": "1 + 1"}))
    assert any("reserved" in e for e in errs)
```

(If `GOOD_SHEETS`'s group has no `derived` key, `setdefault("derived", {})`
first — copy whatever shape the neighboring tests use.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py backend/tests/test_modules_store.py -k "layout or theme or reserved_contextual" -v`
Expected: FAIL — missing `set_layout`/`set_theme`; reserved-name tests get no error.

- [ ] **Step 3: Implement**

In `modules.py`, add next to `FIELD_TYPES`:

```python
RESERVED_NAMES = ("difficulty", "modifier", "new")
```

In `_validate_field`, after the `expressions._FUNCS` check:

```python
    if key in RESERVED_NAMES:
        errors.append(f"{where}.{key}: reserved key (ambient expression name)")
        return
```

In `_validate_derived`, at the top of the loop body (before the collision check):

```python
        if name in RESERVED_NAMES or name in expressions._FUNCS:
            errors.append(f"{where}.{name}: reserved derived name")
            continue
```

In `module_edit.py`:

```python
def set_layout(mid: str, layout: dict, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        _write_json(root, "layout.json", layout if isinstance(layout, dict) else {})
    return _apply(mid, mutate, dry_run=dry_run)


def set_theme(mid: str, theme: dict, *, dry_run: bool = False) -> dict:
    def mutate(root: Path) -> None:
        _write_json(root, "theme.json", theme if isinstance(theme, dict) else {})
    return _apply(mid, mutate, dry_run=dry_run)
```

- [ ] **Step 4: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS — in particular both reference modules must still validate
clean (neither ships a reserved name; if any *other* fixture does, rename
that fixture's field, never weaken the rule).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/module_edit.py backend/src/grimoire/store/modules.py backend/tests/test_module_edit.py backend/tests/test_modules_store.py
git commit -m "feat(module_edit): layout/theme writers; reserved contextual names validation"
```

---

### Task 6: Rename core — scope-bound rewriting, fragment specialization, guards

**Files:**
- Modify: `backend/src/grimoire/store/module_edit.py`
- Test: `backend/tests/test_module_edit.py`

**Interfaces:**
- Consumes: `_apply` (`pre_swap` hook), `proposals._read` shape (`{sid: {status, payload, resolution}}`), `modules` validators.
- Produces:
  - `rename(mid, kind, address: dict, to: str, *, dry_run=False) -> dict` — `kind` ∈ `("group", "field", "derived", "sheet_type", "check", "rule", "content")`. Address forms: group/sheet_type/check/rule: `{"from": old}`; field/derived: `{"from": old, "group": gid}` or `{"from": old, "sheet_type": tid}`; content: `{"from": old, "kind": content_kind}`.
  - Returns the standard result dict; sheet-migrating kinds (`field`, `sheet_type`, `content`) pass a `migration` dict to `_apply` (executed by Task 8; until then journaled and no-op).
  - `_rewrite_expr(expr, old, new) -> str` — word-boundary text replacement (also `old_max` → `new_max` handled by the caller for resource fields).
  - `_composing_tids(sheets_json, owner) -> set[str]` — sheet types whose assembled set contains the owner's fields (`{"group": gid}` ⇒ tids listing gid; `{"sheet_type": tid}` ⇒ `{tid}`).
  - `_specialize_layout(layout, in_scope_tids, edit_fn) -> dict` — applies `edit_fn(node) -> node|None` to layout trees reachable from `in_scope_tids`, cloning any fragment (and its `use`-path ancestors) also reachable from out-of-scope tids, repointing only in-scope `use` nodes (spec: Shared layout fragments, transitive graph).
  - The check rename/delete proposal guard (`pre_swap`): scans every campaign's `proposals.json` for a non-terminal record whose `payload.check` or `resolution.check` equals the old id; blockers name the campaign and scene.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_module_edit.py`:

```python
def _pack_sheets(mid):
    return modules.load_pack(mid)["sheets"]


def test_rename_group_rewrites_refs(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_check(mid, "brawl", CHECK)["ok"]
    assert module_edit.set_layout(mid, {"sheet_types": {"warden": {"group": "attributes"}}})["ok"]
    res = module_edit.rename(mid, "group", {"from": "attributes"}, "traits")
    assert res["ok"], res["errors"]
    pack = modules.load_pack(mid)
    assert "traits" in pack["sheets"]["groups"] and "attributes" not in pack["sheets"]["groups"]
    assert pack["sheets"]["sheet_types"]["warden"]["groups"] == ["traits"]
    assert pack["checks"]["brawl"]["requires"] == ["traits"]
    assert pack["layout"]["sheet_types"]["warden"]["group"] == "traits"
    assert pack["errors"] == [] and pack["display_errors"] == []


def test_rename_field_rewrites_scope_bound(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    # Disjoint second group with the SAME field key, on a different type.
    other_group = {"label": "Spirit", "fields": [
        {"key": "strength", "label": "Will Strength", "type": "dots", "max": 5}],
        "derived": {"spirit_might": "strength * 3"}}
    other_type = {"label": "Medium", "kind": "characters", "groups": ["spirit"],
                  "fields": [], "derived": {}}
    assert module_edit.upsert_group(mid, "spirit", other_group)["ok"]
    assert module_edit.upsert_sheet_type(mid, "medium", other_type)["ok"]
    assert module_edit.upsert_check(mid, "brawl", CHECK)["ok"]           # requires attributes
    spirit_check = {"label": "Channel", "roll": "1d20 + {strength}", "requires": ["spirit"]}
    assert module_edit.upsert_check(mid, "channel", spirit_check)["ok"]
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"], res["errors"]
    pack = modules.load_pack(mid)
    g = pack["sheets"]["groups"]
    assert g["traits" if "traits" in g else "attributes"]["fields"][0]["key"] == "brawn"
    assert g["attributes"]["derived"]["might"] == "brawn * 2"
    assert pack["sheets"]["sheet_types"]["warden"]["derived"]["guard"] == "brawn + 1"
    # the OTHER group's same-spelled field and its consumers are untouched
    assert g["spirit"]["fields"][0]["key"] == "strength"
    assert g["spirit"]["derived"]["spirit_might"] == "strength * 3"
    assert pack["checks"]["channel"]["roll"] == "1d20 + {strength}"
    # the in-scope check IS rewritten
    assert pack["checks"]["brawl"]["roll"] == "1d20 + {brawn}"
    assert pack["errors"] == []


def test_rename_field_word_boundary(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    g = {"label": "A", "fields": [
        {"key": "str", "type": "dots", "max": 5},
        {"key": "strength_bonus", "type": "number"}],
        "derived": {"total": "str + strength_bonus"}}
    assert module_edit.upsert_group(mid, "abilities", g)["ok"]
    t = {"label": "Scout", "kind": "characters", "groups": ["abilities"], "fields": []}
    assert module_edit.upsert_sheet_type(mid, "scout", t)["ok"]
    res = module_edit.rename(mid, "field", {"from": "str", "group": "abilities"}, "vigor")
    assert res["ok"], res["errors"]
    d = modules.load_pack(mid)["sheets"]["groups"]["abilities"]["derived"]
    assert d["total"] == "vigor + strength_bonus"   # strength_bonus untouched


def test_rename_resource_rewrites_max_name(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    t2 = {**TYPE, "derived": {"guard": "strength + 1", "reserve": "essence_max - essence"}}
    assert module_edit.upsert_sheet_type(mid, "warden", t2)["ok"]
    res = module_edit.rename(mid, "field", {"from": "essence", "group": "attributes"}, "mana")
    assert res["ok"], res["errors"]
    d = modules.load_pack(mid)["sheets"]["sheet_types"]["warden"]["derived"]
    assert d["reserve"] == "mana_max - mana"


def test_rename_to_reserved_or_collision_rejected(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "new")
    assert res["ok"] is False
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "essence")
    assert res["ok"] is False
    # map-key collisions must reject, never overwrite the destination (and
    # the destination definition must survive intact)
    assert module_edit.upsert_check(mid, "brawl", CHECK)["ok"]
    assert module_edit.upsert_check(mid, "melee", {**CHECK, "label": "Melee"})["ok"]
    res = module_edit.rename(mid, "check", {"from": "brawl"}, "melee")
    assert res["ok"] is False
    assert modules.load_pack(mid)["checks"]["melee"]["label"] == "Melee"
    assert module_edit.upsert_group(mid, "spirit", {"label": "Spirit", "fields": []})["ok"]
    res = module_edit.rename(mid, "group", {"from": "spirit"}, "attributes")
    assert res["ok"] is False
    assert modules.load_pack(mid)["sheets"]["groups"]["attributes"]["label"] == "Attributes"


def test_rename_sheet_type_rewrites_flags_layout_sidecars(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_rule(mid, "warden-powers", {"sheet_types": ["warden"]}, "body")["ok"]
    assert module_edit.set_layout(mid, {"sheet_types": {"warden": {"group": "attributes"}}})["ok"]
    res = module_edit.rename(mid, "sheet_type", {"from": "warden"}, "keeper")
    assert res["ok"], res["errors"]
    pack = modules.load_pack(mid)
    assert "keeper" in pack["sheets"]["sheet_types"]
    doc = next(r for r in pack["rules"] if r["id"] == "warden-powers")
    assert doc["sheet_types"] == ["keeper"]
    assert "keeper" in pack["layout"]["sheet_types"]


def test_rename_traversal_and_file_collision_rejected(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    victim = modules.create_module("Victim System")
    before = (modules.user_dir() / victim / "module.md").read_bytes()
    res = module_edit.rename(mid, "rule",
                             {"from": f"../../{victim}/module"}, "stolen")
    assert res["ok"] is False
    assert (modules.user_dir() / victim / "module.md").read_bytes() == before
    assert module_edit.upsert_rule(mid, "a-doc", {}, "a")["ok"]
    assert module_edit.upsert_rule(mid, "b-doc", {}, "b")["ok"]
    res = module_edit.rename(mid, "rule", {"from": "a-doc"}, "b-doc")
    assert res["ok"] is False                          # file collision
    assert modules.read_rule(mid, "b-doc")["body"].strip() == "b"


def test_rename_rule_and_check(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_rule(mid, "combat", {}, "body")["ok"]
    assert module_edit.upsert_check(mid, "brawl", {**CHECK, "rules": ["combat"]})["ok"]
    assert module_edit.rename(mid, "rule", {"from": "combat"}, "combat-core")["ok"]
    pack = modules.load_pack(mid)
    assert pack["checks"]["brawl"]["rules"] == ["combat-core"]
    assert modules.read_rule(mid, "combat-core") is not None
    assert module_edit.rename(mid, "check", {"from": "brawl"}, "melee")["ok"]
    assert "melee" in modules.load_pack(mid)["checks"]


def _bound_campaign(mid):
    """Shared by Tasks 6-8: a world + campaign bound to the module."""
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Run", wid)
    modules.set_campaign_module(cid, mid)
    return wid, cid


def test_check_rename_blocked_by_live_proposal(monkeypatch, tmp_path):
    from grimoire.store import proposals
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_check(mid, "brawl", CHECK)["ok"]
    wid, cid = _bound_campaign(mid)
    proposals.new(cid, "s1", {"check": "brawl"})
    res = module_edit.rename(mid, "check", {"from": "brawl"}, "melee")
    assert res["ok"] is False and any(cid in e for e in res["errors"])
    res = module_edit.delete_check(mid, "brawl",
                                   pre_swap=module_edit.check_proposal_guard(mid, "brawl"))
    assert res["ok"] is False
    proposals.supersede(cid, "s1")   # superseded is terminal — guard clears
    assert module_edit.rename(mid, "check", {"from": "brawl"}, "melee")["ok"]


def test_shared_fragment_specialized(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    other_group = {"label": "Spirit", "fields": [
        {"key": "strength", "type": "dots", "max": 5}]}
    other_type = {"label": "Medium", "kind": "characters", "groups": ["spirit"], "fields": []}
    assert module_edit.upsert_group(mid, "spirit", other_group)["ok"]
    assert module_edit.upsert_sheet_type(mid, "medium", other_type)["ok"]
    layout = {"fragments": {"stat-block": {"fields": ["strength"]}},
              "sheet_types": {"warden": {"use": "stat-block"},
                              "medium": {"use": "stat-block"}}}
    assert module_edit.set_layout(mid, layout)["ok"]
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"], res["errors"]
    pack = modules.load_pack(mid)
    raw = json.loads((modules.user_dir() / mid / "layout.json").read_text(encoding="utf-8"))
    # medium still uses the original fragment; warden repointed to a clone
    assert raw["fragments"]["stat-block"] == {"fields": ["strength"]}
    warden_use = raw["sheet_types"]["warden"]["use"]
    assert warden_use != "stat-block"
    assert raw["fragments"][warden_use] == {"fields": ["brawn"]}
    assert pack["display_errors"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -k rename -v`
Expected: FAIL — `AttributeError: ... no attribute 'rename'`.

- [ ] **Step 3: Implement**

Add to `module_edit.py` (imports gain `re` and `from . import proposals, worlds`):

```python
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


class _RenameCollision(Exception):
    pass


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


# ---- layout specialization over the transitive `use` graph ----
# (Defined in Task 3, where _prune_layout already needs it — shown here for
# the rename callers' reference; do NOT define it twice.)

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


# ---- the rename op ----

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
        migration = {"op": "field", "from": old, "to": to,
                     "owner": {k: address[k] for k in ("group", "sheet_type") if k in address}}
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

    return _apply(mid, mutate, dry_run=dry_run, migration=migration, pre_swap=pre_swap)
```

Note: collision rejection (renaming onto an existing key, or to a reserved
name) needs no dedicated code — the staged validation catches duplicates and
reserved names (Task 5) and `_apply` returns those messages.

- [ ] **Step 4: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/module_edit.py backend/tests/test_module_edit.py
git commit -m "feat(module_edit): rename ops with scope-bound rewriting, fragment specialization, proposal guard"
```

---

### Task 7: Sheet migration — journaled, idempotent, gen-bumping

**Files:**
- Modify: `backend/src/grimoire/store/module_edit.py` (replace the Task-2 `_run_migration` stub; add the both-keys pre-scan to `rename`)
- Test: `backend/tests/test_module_edit.py`

**Interfaces:**
- Consumes: journal plumbing (Task 2), `rename` migration dicts (Task 6), `sheets._campaign_path`/`_world_dir` layout (`<campaign>/sheets/<kind>--<id>.json`, `<world>/sheets/<mid>/…`), `campaigns.list_campaigns()`, `worlds.list_worlds()`, `modules.resolve(cid)`.
- Produces: `_run_migration(mid, migration) -> {"migrated": int, "skipped": [paths]}` — rewrites every affected stored sheet file:
  - `field` op: rename the key in `fields` of sheets whose `sheet_type` is in the op's composing set (the mutate step computed it; store it in the migration dict as `"sheet_types": sorted(in_scope)`).
  - `sheet_type` op: rewrite the `sheet_type` value.
  - `content` op: rewrite `<kind>:module:<old>` entries inside every list-valued field.
  - Every changed file is rewritten atomically with a **new `gen`** (`uuid.uuid4().hex`); unchanged files untouched; unparseable files skipped + reported. Idempotent: old key absent ⇒ no-op.
  - Scope: every world's `<world>/sheets/<mid>/` + campaign sheets of campaigns whose `resolve(cid) == mid` (checked under the already-held campaign locks — `_run_migration` runs inside `_apply`'s `_campaign_locks()` block, and journal replay re-runs it under `recover()`'s `_M`; replay-after-crash holds no campaign locks, which is fine — recovery runs before serving requests or before the next edit).
  - `rename` gains a `pre_swap` both-keys scan for `field` ops: any target sheet holding **both** old and new keys rejects the rename listing the paths (value collision — spec: Sheet migration).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_module_edit.py`:

```python
# (_bound_campaign was defined in Task 6's test additions)

def _write_campaign_sheet(cid, kind, eid, sheet_type, fields):
    p = campaigns.campaign_root(cid) / "sheets" / f"{kind}--{eid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"sheet_type": sheet_type, "fields": fields,
                             "gen": "g1"}), encoding="utf-8")
    return p


def test_field_rename_migrates_sheets(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    cp = _write_campaign_sheet(cid, "characters", "mara", "warden", {"strength": 3})
    wp = worlds.world_root(wid) / "sheets" / mid / "characters--winifred.json"
    wp.parent.mkdir(parents=True, exist_ok=True)
    wp.write_text(json.dumps({"sheet_type": "warden", "fields": {"strength": 2},
                              "gen": "g0"}), encoding="utf-8")
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"], res["errors"]
    assert res["migration"]["migrated"] == 2 and res["migration"]["skipped"] == []
    cdata = json.loads(cp.read_text(encoding="utf-8"))
    assert cdata["fields"] == {"brawn": 3} and cdata["gen"] != "g1"
    wdata = json.loads(wp.read_text(encoding="utf-8"))
    assert wdata["fields"] == {"brawn": 2} and wdata["gen"] != "g0"


def test_field_rename_skips_other_types_and_unbound(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    other = campaigns.create_campaign("Freeform Nights", wid)   # resolves to None
    op = _write_campaign_sheet(other, "characters", "mara", "warden", {"strength": 5})
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"]
    assert json.loads(op.read_text(encoding="utf-8"))["fields"] == {"strength": 5}


def test_both_keys_collision_rejects(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    _write_campaign_sheet(cid, "characters", "mara", "warden",
                          {"strength": 3, "brawn": 4})   # orphaned removed key
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"] is False
    assert any("mara" in e for e in res["errors"])
    # nothing swapped: schema still has strength
    assert "strength" in json.dumps(modules.load_pack(mid)["sheets"])


def test_sheet_type_rename_migrates(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    cp = _write_campaign_sheet(cid, "characters", "mara", "warden", {"strength": 3})
    res = module_edit.rename(mid, "sheet_type", {"from": "warden"}, "keeper")
    assert res["ok"], res["errors"]
    assert json.loads(cp.read_text(encoding="utf-8"))["sheet_type"] == "keeper"


def test_content_rename_migrates_refs(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    ref_type = {"label": "Adept", "kind": "characters", "groups": [],
                "fields": [{"key": "known", "type": "ref", "ref_kind": "lore"}]}
    assert module_edit.upsert_sheet_type(mid, "adept", ref_type)["ok"]
    assert module_edit.upsert_content(mid, "lore", "old-rite", name="Old Rite",
                                      body="", keys="", fields={}, sheet=None)["ok"]
    wid, cid = _bound_campaign(mid)
    cp = _write_campaign_sheet(cid, "characters", "mara", "adept",
                               {"known": ["lore:module:old-rite", "lore:kept"]})
    res = module_edit.rename(mid, "content", {"from": "old-rite", "kind": "lore"}, "new-rite")
    assert res["ok"], res["errors"]
    got = json.loads(cp.read_text(encoding="utf-8"))["fields"]["known"]
    assert got == ["lore:module:new-rite", "lore:kept"]


def test_unparseable_sheet_skipped(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    bad = campaigns.campaign_root(cid) / "sheets" / "characters--broken.json"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_text("{not json", encoding="utf-8")
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    assert res["ok"]
    assert any("broken" in s for s in res["migration"]["skipped"])


def test_journaled_migration_replays(monkeypatch, tmp_path):
    """Crash after swap, before migration: recovery finishes it."""
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    cp = _write_campaign_sheet(cid, "characters", "mara", "warden", {"strength": 3})
    # Perform the rename with migration suppressed to simulate the crash,
    # leaving a journal exactly as _apply writes it post-swap.
    real = module_edit._run_migration
    monkeypatch.setattr(module_edit, "_run_migration",
                        lambda *a, **k: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"}, "brawn")
    monkeypatch.setattr(module_edit, "_run_migration", real)
    # journal survived; pack already published; sheet not yet migrated
    assert json.loads(cp.read_text(encoding="utf-8"))["fields"] == {"strength": 3}
    module_edit.recover()
    assert json.loads(cp.read_text(encoding="utf-8"))["fields"] == {"brawn": 3}
    module_edit.recover()   # idempotent replay
    assert json.loads(cp.read_text(encoding="utf-8"))["fields"] == {"brawn": 3}
```

(The KeyboardInterrupt escape hatch relies on `_apply`'s `finally` only
removing the staging base, never the journal — assert that holds; if
`_apply` currently unlinks the journal in `finally`, it must not: the journal
is removed only after migration succeeds.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -k "migrat or both_keys or content_rename or sheet_type_rename or unparseable" -v`
Expected: FAIL — migration stub returns `{"migrated": 0}`; both-keys scan missing.

- [ ] **Step 3: Implement**

Replace the `_run_migration` stub in `module_edit.py`:

```python
def _sheet_files(mid: str):
    """Yield (Path, cid|None) for every stored sheet governed by this module:
    each world's <world>/sheets/<mid>/*.json, plus each bound campaign's
    <campaign>/sheets/*.json (bound = resolve(cid) == mid)."""
    from . import worlds
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


def _run_migration(mid: str, migration: dict) -> dict:
    migrated, skipped = 0, []
    for p, _cid in _sheet_files(mid):
        got = _migrate_file(p, migration)
        if got is None:
            skipped.append(str(p))
        elif got:
            migrated += 1
    return {"migrated": migrated, "skipped": skipped}
```

In `rename`, for `field` ops: after computing `in_scope` inside `mutate`
the migration dict needs it — restructure so `in_scope` is computed **before**
`_apply` (read the live `sheets.json` via
`_read_sheets(modules.pack_root(mid)[0])` at the top of `rename`) and stored
as `migration["sheet_types"] = sorted(in_scope)`; `mutate` reuses the same
owner logic. Then add the both-keys `pre_swap` for `field` ops:

```python
    if kind == "field":
        def both_keys_guard(_pack: dict) -> list[str]:
            blockers = []
            for p, _cid in _sheet_files(mid):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError, OSError):
                    continue
                fields = data.get("fields") if isinstance(data, dict) and isinstance(data.get("fields"), dict) else {}
                if data.get("sheet_type") in migration["sheet_types"] \
                        and old in fields and to in fields:
                    blockers.append(
                        f"{p.name}: holds both {old!r} and {to!r} — resolve the "
                        "orphaned value first")
            return blockers
        pre_swap = both_keys_guard
```

Also verify `_apply`'s `finally` block: it must remove only
`_staging_root()/<nonce>` (the base dir), never the journal file — the
journal lives at `_staging_root()/<nonce>.journal.json` (a sibling, not
inside base), so the Task 2 code is already correct; add a comment saying the
journal must outlive a migration crash.

Note on `pre_swap` guards running `resolve(cid)`/`_sheet_files` under the
already-held campaign locks: `sheets.lock_for` returns an `RLock`, and
`_campaign_locks()` holds them all, so nested `resolve()` calls are fine.

- [ ] **Step 4: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/module_edit.py backend/tests/test_module_edit.py
git commit -m "feat(module_edit): journaled idempotent sheet migration with gen bumps"
```

---

### Task 8: Dry-run impact + sample computation

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py` (factor `instance_errors`), `backend/src/grimoire/store/module_edit.py`
- Test: `backend/tests/test_module_edit.py`, `backend/tests/test_sheets_store.py`

**Interfaces:**
- Produces:
  - `sheets.instance_errors(pack: dict, file_kind: str, sheet_type, fields: dict) -> list[str]` — the **full read-time judgment** against an arbitrary pack dict: sheet-type existence, kind match, value validation, **and derived computation against the stored values** (spec: the impact scan must judge exactly as a read would). `_read_path` refactors to call it (against the resolved pack) so there is one path.
  - `module_edit._impact(mid, staged_pack, migration) -> dict` — `{"sheet_types": [...], "sheets_migrated": int, "sheets_newly_invalid": int, "dangling_refs": int}`. Newly-invalid = stored sheets valid against the live pack but invalid against the staged one; dangling = `ref` values of the form `<kind>:module:<id>` whose content id exists in the live pack but not the staged one — counted over stored sheets **and content stat sidecars**.
  - `_apply` computes impact for schema-affecting writers and rename ops (callers pass `impact=True`); the result dict gains `"impact"` and — for sheets.json dry-runs — `"sample"`: `{tid: {"fields": assembled defs with schema defaults, "derived": computed values}}` via `sheets.default_fields` + `sheets._compute_derived` per staged sheet type.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_sheets_store.py`:

```python
def test_instance_errors_includes_derived_failures(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = modules.create_module("Realm System")
    root = modules.user_dir() / mid
    (root / "sheets.json").write_text(json.dumps({
        "groups": {"a": {"fields": [{"key": "strength", "type": "dots", "max": 5}],
                          "derived": {"bad": "10 // strength"}}},
        "sheet_types": {"warden": {"label": "W", "kind": "characters",
                                   "groups": ["a"], "fields": []}}}), encoding="utf-8")
    pack = modules.load_pack(mid)
    assert pack["errors"] == []          # valid at defaults... unless default 0
    errs = sheets.instance_errors(pack, "characters", "warden", {"strength": 0})
    assert any("bad" in e for e in errs)     # division by zero at stored values
    assert sheets.instance_errors(pack, "characters", "warden", {"strength": 2}) == []
    assert sheets.instance_errors(pack, "characters", "ghost", {}) != []
    assert sheets.instance_errors(pack, "items", "warden", {}) != []
```

(If pack validation happens to reject `10 // strength` at load time because
the sample uses defaults of 0 — check: `_validate_derived` only parses and
scopes, it doesn't evaluate, so load passes. Good.)

Add to `backend/tests/test_module_edit.py`:

```python
def test_dry_run_impact_counts(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    _write_campaign_sheet(cid, "characters", "mara", "warden", {"strength": 3})
    # deleting the sheet type: its sheet becomes newly invalid
    res = module_edit.delete_sheet_type(mid, "warden", dry_run=True)
    assert res["ok"] is True
    assert res["impact"]["sheets_newly_invalid"] == 1
    # renaming a field: migration counted, nothing newly invalid
    res = module_edit.rename(mid, "field", {"from": "strength", "group": "attributes"},
                             "brawn", dry_run=True)
    assert res["impact"]["sheets_migrated"] == 1
    assert res["impact"]["sheets_newly_invalid"] == 0
    assert "warden" in res["impact"]["sheet_types"]
    # dry-run wrote nothing
    assert "strength" in json.dumps(modules.load_pack(mid)["sheets"])


def test_dry_run_dangling_refs_counts_sidecars(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    ref_type = {"label": "Adept", "kind": "characters", "groups": [],
                "fields": [{"key": "known", "type": "ref", "ref_kind": "lore"}]}
    lore_type = {"label": "Rite", "kind": "lore", "groups": [],
                 "fields": [{"key": "linked", "type": "ref", "ref_kind": "lore"}]}
    assert module_edit.upsert_sheet_type(mid, "adept", ref_type)["ok"]
    assert module_edit.upsert_sheet_type(mid, "rite", lore_type)["ok"]
    assert module_edit.upsert_content(mid, "lore", "old-rite", name="Old Rite",
                                      body="", keys="", fields={}, sheet=None)["ok"]
    assert module_edit.upsert_content(
        mid, "lore", "linked-rite", name="Linked", body="", keys="", fields={},
        sheet={"sheet_type": "rite", "fields": {"linked": ["lore:module:old-rite"]}})["ok"]
    wid, cid = _bound_campaign(mid)
    _write_campaign_sheet(cid, "characters", "mara", "adept",
                          {"known": ["lore:module:old-rite"]})
    res = module_edit.delete_content(mid, "lore", "old-rite", dry_run=True)
    assert res["ok"] is True
    assert res["impact"]["dangling_refs"] == 2    # stored sheet + sidecar


def test_dry_run_sample_derived(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    res = module_edit.upsert_group(mid, "attributes",
                                   {**GROUP, "derived": {"might": "strength * 2"}},
                                   dry_run=True)
    assert res["ok"]
    sample = res["sample"]["warden"]
    assert sample["derived"]["might"] == 0        # defaults: strength 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py backend/tests/test_sheets_store.py -k "impact or instance_errors or sample or dangling" -v`
Expected: FAIL — `instance_errors` missing; results carry no `impact`/`sample`.

- [ ] **Step 3: Implement**

In `sheets.py`, factor out of `_read_path` (which currently calls
`_validate_instance` then `_compute_derived`):

```python
def instance_errors(pack: dict, file_kind: str, sheet_type, fields: dict) -> list[str]:
    """The full read-time judgment for a stored sheet against an arbitrary
    pack dict — sheet-type/kind/value validation PLUS derived evaluation
    against the stored values (impact scans must judge exactly as reads do)."""
    sheets_def = pack["sheets"] if isinstance(pack.get("sheets"), dict) else {}
    errors = _validate_instance(sheets_def, file_kind, sheet_type, fields)
    if isinstance(sheet_type, str):
        _compute_derived(sheets_def, sheet_type, fields, errors)
    return errors
```

and have `_read_path`'s validated branch use it:

```python
    pack = modules.load_pack(mid)
    errors = instance_errors(pack, file_kind, sheet_type, fields)
    derived: dict = {}
    if isinstance(sheet_type, str):
        derived = _compute_derived(pack["sheets"], sheet_type, fields, [])
```

(the derived values are recomputed for the return payload; errors were
already collected once — keep a single computation if you prefer by having
`instance_errors` optionally return the derived map, but do not change
`_read_path`'s output shape).

In `module_edit.py`:

```python
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
    live_pack = modules.load_pack(mid)
    newly_invalid = 0
    dangling = 0
    staged_ids = _content_ids(staged_pack)
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
            if ":module:" in ref and ref in _content_ids(live_pack) and ref not in staged_ids:
                dangling += 1
    for stat in _sidecar_stats(mid):
        for ref in _iter_ref_values(stat.get("fields") or {}):
            if ":module:" in ref and ref in _content_ids(live_pack) and ref not in staged_ids:
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
```

with two small helpers derived from `_migrate_file`'s branches:

```python
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
```

and `_file_kind(p: Path) -> str` = `p.stem.partition("--")[0]`.

Wire into `_apply`: add parameter `impact: bool = False`; when True, after
validation compute `impact_data = _impact(mid, pack, migration)` and include
it in every return (both the dry-run return and the post-swap return). Mark
`upsert_group`, `delete_group`, `upsert_sheet_type`, `delete_sheet_type`,
`delete_content`, and `rename` as `impact=True` callers. For the two
sheets.json upsert writers also compute `sample` after a *clean* validation:

```python
def _sample(pack: dict) -> dict:
    out = {}
    for tid in (pack["sheets"].get("sheet_types") or {}):
        defaults = sheets.default_fields(pack["sheets"], tid)
        errs: list[str] = []
        derived = sheets._compute_derived(pack["sheets"], tid, defaults, errs)
        out[tid] = {"fields": defaults, "derived": derived}
    return out
```

(`module_edit` importing `sheets` is already established in Task 2.)

- [ ] **Step 4: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py backend/tests/test_sheets_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/src/grimoire/store/module_edit.py backend/tests/
git commit -m "feat(module_edit): dry-run impact with full read-time judgment + sample computation"
```

---

### Task 9: Duplicate, export, import

**Files:**
- Modify: `backend/src/grimoire/store/module_edit.py`
- Test: `backend/tests/test_module_edit.py`

**Interfaces:**
- Produces:
  - `new_mid(name_or_id: str) -> str` — the one id allocator for create/duplicate/import: `slugify`, reject empty, **reserve `"none"`**, dedupe against builtin + user ids via `uniquify` (mirror `modules.create_module`'s predicate).
  - `create_module(name) -> str` — replaces direct route use of `modules.create_module` (codex plan review: the old scaffold creates the live dir first, so a crash mid-scaffold leaves a partial live pack): under `_M` + `recover()`, allocate via `new_mid`, scaffold `module.md` + empty `sheets.json` in staging, publish by the single rename. The `POST /modules` route (Task 12) switches to it; `modules.create_module` stays for its existing test surface but the route no longer calls it.
  - `delete_module(mid)` — the locked wrapper the spec requires (codex plan review: the bare `shutil.rmtree` can race an LLM consumer mid-computation, exposing a missing module or a builtin-shadow fallback): under `_M`, `recover()`, then **all campaign locks**, then `modules.delete_module(mid)`. The `DELETE /modules/{mid}` route (Task 12) switches to it.
  - `duplicate_module(mid, name) -> str` — copy any pack (builtin or user) to staging, publish by single rename into `user_dir()` under `_M`. Content copied as-is, valid or not.
  - `export_module(mid) -> bytes` — zip with a single top-level `<mid>/` dir; runs under `locked()` for a swap-coherent archive.
  - `import_module(path: Path) -> str` — archive checks (member count ≤ 2000; cumulative uncompressed `ZipInfo.file_size` ≤ 64 MB; exactly one top-level dir; plain files only, no symlink external attrs; normalized paths stay inside; no case-insensitive path collisions), extract to staging, allocate id via `new_mid` on the top-level dir name, validate with `load_pack_at` (invalid ⇒ raise `modules.ModuleError` with the joined messages — import has no partial-result UI), publish by single rename.

- [ ] **Step 1: Write the failing tests**

```python
import io
import zipfile


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in entries.items():
            z.writestr(name, text)
    return buf.getvalue()


def test_duplicate_builtin(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    new = module_edit.duplicate_module("d20-basic", "My D20")
    assert new == "my-d20"
    pack = modules.load_pack(new)
    assert pack["source"] == "user" and pack["errors"] == []
    # editable now
    assert module_edit.set_manifest(new, name="My D20", description="", version="",
                                    dice="1d20", notes="")["ok"]


def test_new_mid_reserves_none_and_dedupes(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    assert module_edit.new_mid("None") != "none"
    a = modules.create_module("Realm System")
    assert module_edit.new_mid("Realm System") != a


def test_create_module_staged_and_locked_delete(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = module_edit.create_module("Realm System")
    assert modules.load_pack(mid)["errors"] == []
    wid = worlds.create_world("Realm")
    cid = campaigns.create_campaign("Saltmarch Run", wid)
    done = []
    with sheets.lock_for(cid):        # an LLM flow is mid-computation
        t = threading.Thread(target=lambda: (module_edit.delete_module(mid),
                                             done.append(1)))
        t.start()
        t.join(timeout=0.3)
        assert not done               # delete waits for the campaign lock
    t.join(timeout=5)
    assert done
    with pytest.raises(modules.ModuleNotFound):
        modules.pack_root(mid)


def test_export_import_round_trip(monkeypatch, tmp_path):
    mid = _mk_schema(monkeypatch, tmp_path)
    data = module_edit.export_module(mid)
    zpath = tmp_path / "pack.zip"
    zpath.write_bytes(data)
    new = module_edit.import_module(zpath)
    assert new != mid                     # deduped
    assert modules.load_pack(new)["errors"] == []
    assert modules.load_pack(new)["sheets"] == modules.load_pack(mid)["sheets"]


def test_import_rejections(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cases = {
        "traversal": {"pack/module.md": "---\nname: X\n---\n",
                      "pack/../evil.txt": "x"},
        "absolute": {"/abs/module.md": "x"},
        "double-slash": {"pack//module.md": "x"},
        "dot-segment": {"pack/./module.md": "x"},
        "drive": {"C:/pack/module.md": "x"},
        "unc": {"//srv/share/module.md": "x"},
        "two-roots": {"a/module.md": "x", "b/module.md": "y"},
        "invalid-pack": {"pack/module.md": "---\nname: X\n---\n"},  # no sheets.json
        "case-collision": {"pack/module.md": "---\nname: X\n---\n",
                           "pack/Sheets.json": "{}",
                           "pack/sheets.json": "{}"},
    }
    for label, entries in cases.items():
        zpath = tmp_path / f"{label}.zip"
        zpath.write_bytes(_zip_bytes(entries))
        with pytest.raises(modules.ModuleError):
            module_edit.import_module(zpath)
    assert not any(modules.user_dir().iterdir()) if modules.user_dir().is_dir() else True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -k "duplicate or new_mid or export or import" -v`
Expected: FAIL — missing attributes.

- [ ] **Step 3: Implement**

```python
import io
import zipfile

from .paths import slugify, uniquify

MAX_MEMBERS = 2000
MAX_UNCOMPRESSED = 64 * 1024 * 1024


def new_mid(name_or_id: str) -> str:
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
        modules.pack_root(mid)  # 404 before taking every lock
        with _campaign_locks():
            modules.delete_module(mid)


def export_module(mid: str) -> bytes:
    with _M:
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
    the stripped remainder '/module.md' then resolves to the drive root)."""
    name = raw_name.replace("\\", "/")
    if _DRIVE_OR_UNC.match(name) or name.startswith("/"):
        raise modules.ModuleError(f"unsafe zip entry: {raw_name}")
    parts = name.split("/")
    if len(parts) < 2 or any(p in ("", ".", "..") for p in parts):
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
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(z.read(i))
                pack = modules.load_pack_at(staging, mid)
                if pack["errors"]:
                    raise modules.ModuleError(
                        "invalid module pack: " + "; ".join(pack["errors"]))
                return _publish(staging, mid)
            finally:
                shutil.rmtree(base, ignore_errors=True)
```

- [ ] **Step 4: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/module_edit.py backend/tests/test_module_edit.py
git commit -m "feat(module_edit): duplicate, zip export, hardened zip import with shared id allocation"
```

---

### Task 10: Stale-form closure — unknown-key 400s, world sheet CAS

**Files:**
- Modify: `backend/src/grimoire/store/sheets.py:242-257` (`_checked_write`), `:361-367` (`write_world`), `:483-490` (`delete_world`), `backend/src/grimoire/routes.py:574-602` (world sheet routes)
- Test: `backend/tests/test_sheets_store.py`, `backend/tests/test_routes.py`
- Frontend: `frontend/src/api/client.ts:768-773` (`deleteSheet` world URL gains `?gen=`)

**Interfaces:**
- Produces (spec: Sheet migration, stale-form closure — sequential hazard, not concurrency):
  - `_checked_write` **rejects** unknown submitted field keys (`SheetError: "<key>: not a field of sheet type ..."`) instead of silently filtering them. (`modules.validate_sheet_values` already produces exactly that message for unknown keys — so the fix is to *stop pre-filtering*: delete the `allowed`/filter lines and let validation see the full payload.)
  - `write_world(wid, mid, kind, eid, sheet_type, fields=None, *, expected: dict | None)` — mandatory whole-sheet CAS via the existing `_check_expected`, exactly like `write()`.
  - `write_world_creation(wid, mid, kind, eid, sheet_type, spends, *, expected: dict | None)` — same mandatory CAS (`_check_expected` before `_checked_creation_write`); the route already receives `SheetCreationBody.expected` and now passes it, 409 on conflict (codex plan review round 2: a stale/retried creation PUT could otherwise overwrite a migrated world sheet). `CreationWizard` already sends `expected: null` for fresh creations — no frontend change.
  - `delete_world(wid, mid, kind, eid, *, expected_gen: str | None) -> bool` — gen CAS like campaign `delete()`.
  - Routes: `PUT /worlds/{wid}/sheets/{mid}/{kind}/{eid}` passes `body.expected` (the `SheetBody` model already carries it — campaign PUT uses it today); 409 on `SheetConflict`. `DELETE /worlds/...` gains `gen: str | None = None` query param, 409 on conflict.
  - Client: `deleteSheet` world branch appends the same `?gen=` query the campaign branch already sends. (`putSheet` already sends `expected` for both scopes — no client change needed there.)

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_sheets_store.py` (reuse the file's existing
module/world/campaign fixture helpers — copy the idiom of the
`write_world` tests already present):

```python
def test_unknown_key_rejected_not_filtered(monkeypatch, tmp_path, ...):
    # build a valid module + campaign as the neighboring tests do
    with pytest.raises(sheets.SheetError, match="not a field"):
        sheets.write(cid, "characters", "mara", "warden",
                     {"ghost_key": 1}, expected=None)


def test_write_world_requires_cas(monkeypatch, tmp_path, ...):
    sheets.write_world(wid, mid, "characters", "winifred", "warden", None, expected=None)
    stored = sheets.read_world(wid, mid, "characters", "winifred")
    # stale snapshot: wrong gen
    with pytest.raises(sheets.SheetConflict):
        sheets.write_world(wid, mid, "characters", "winifred", "warden",
                           {"strength": 2},
                           expected={"sheet_type": "warden", "fields": {}, "gen": "stale"})
    ok = {"sheet_type": stored["sheet_type"], "fields": stored["fields"], "gen": stored["gen"]}
    sheets.write_world(wid, mid, "characters", "winifred", "warden",
                       {"strength": 2}, expected=ok)


def test_write_world_creation_requires_cas(monkeypatch, tmp_path, ...):
    sheets.write_world_creation(wid, mid, "characters", "winifred", "warden",
                                {}, expected=None)
    with pytest.raises(sheets.SheetConflict):
        sheets.write_world_creation(wid, mid, "characters", "winifred", "warden",
                                    {}, expected=None)   # already exists


def test_delete_world_requires_gen(monkeypatch, tmp_path, ...):
    sheets.write_world(wid, mid, "characters", "winifred", "warden", None, expected=None)
    stored = sheets.read_world(wid, mid, "characters", "winifred")
    with pytest.raises(sheets.SheetConflict):
        sheets.delete_world(wid, mid, "characters", "winifred", expected_gen="stale")
    assert sheets.delete_world(wid, mid, "characters", "winifred",
                               expected_gen=stored["gen"]) is True
```

Fill the `...` fixture args from the neighboring world-sheet tests in the
same file (they already construct `wid`/`mid`/a valid module).

Add to `backend/tests/test_routes.py` (next to the existing world sheet route
tests): a PUT with a stale `expected` returns 409; a DELETE without `gen`
against an existing sheet returns 409; DELETE with the right gen returns
`{"ok": true}`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_sheets_store.py -k "unknown_key or world_requires" -v`
Expected: FAIL — unknown key silently dropped; `write_world` has no `expected` kwarg.

- [ ] **Step 3: Implement**

In `_checked_write`, delete the filter:

```python
        if not isinstance(fields, dict):
            raise SheetError("fields must be an object")
        errs = modules.validate_sheet_values(sheets_def, sheet_type, fields)
        if errs:
            raise SheetError("; ".join(errs))
```

**Caveat — the type-change path:** `write()`'s docstring says a sheet-type
change keeps keys that exist in the new type and "others are filtered out
here". Check `SheetEditor.tsx`'s type-change flow (it builds the retained
field map client-side before calling `putSheet`) and `test_sheets_store.py`'s
type-change tests: if any existing test feeds `write()` old-type keys and
expects silent filtering, that test changes to expect `SheetError` — the
caller must send a clean payload (the frontend already does; verify by
reading `SheetEditor.tsx:160-180` before changing the test).

`write_world` and `delete_world`:

```python
def write_world(wid: str, mid: str, kind: str, eid: str, sheet_type: str,
                fields: dict | None = None, *, expected: dict | None) -> None:
    if not _safe_part(mid):
        raise SheetError(f"bad module id {mid!r}")
    modules.pack_root(mid)  # raises ModuleNotFound
    path = _world_path(wid, mid, kind, eid)
    _check_expected(path, expected)
    _checked_write(path, mid, kind, eid, sheet_type, fields)


def write_world_creation(wid: str, mid: str, kind: str, eid: str, sheet_type: str,
                         spends: dict[str, dict[str, int]], *,
                         expected: dict | None) -> None:
    modules.pack_root(mid)  # raises ModuleNotFound
    _assert_world_entity_exists(wid, kind, eid)
    path = _world_path(wid, mid, kind, eid)
    _check_expected(path, expected)
    _checked_creation_write(path, mid, kind, eid, sheet_type, spends)


def delete_world(wid: str, mid: str, kind: str, eid: str, *,
                 expected_gen: str | None) -> bool:
    if kind not in FILE_KINDS or not _safe_part(eid) or not _safe_part(mid):
        return False
    p = _world_path(wid, mid, kind, eid)
    stored = _stored_snapshot(p)
    if stored is None:
        return False
    if stored["gen"] != expected_gen:
        raise SheetConflict("the sheet changed since it was loaded")
    p.unlink()
    return True
```

Grep for every `write_world(`/`delete_world(` call site (`seed` does not call
them; the routes and `instantiate` route do) and pass `expected=None` where
the caller semantically creates (instantiate) or thread the body value
(routes). Routes:

```python
@router.put("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def put_world_sheet(wid: str, mid: str, kind: str, eid: str, body: SheetBody):
    _world_root_or_404(wid)
    try:
        store.sheets.write_world(wid, mid, kind, eid, body.sheet_type, body.fields,
                                 expected=body.expected)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
    except store.sheets.SheetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@router.delete("/worlds/{wid}/sheets/{mid}/{kind}/{eid}")
def delete_world_sheet(wid: str, mid: str, kind: str, eid: str, gen: str | None = None):
    _world_root_or_404(wid)
    try:
        return {"ok": store.sheets.delete_world(wid, mid, kind, eid, expected_gen=gen)}
    except store.sheets.SheetConflict as e:
        raise HTTPException(status_code=409, detail=str(e))
```

and `put_world_sheet_creation` (`routes.py:585-596`) passes
`expected=body.expected` to `sheets.write_world_creation` and gains the same
`SheetConflict` → 409 except-clause (the body model already carries
`expected`; the campaign creation route already does exactly this).

Client (`frontend/src/api/client.ts` `deleteSheet`): make both branches
append `${gen ? `?gen=${encodeURIComponent(gen)}` : ""}`.

The world instantiate route (`routes.py:618` area) calls
`sheets.write_world(...)` — pass `expected=None` (fresh creation; a 409 there
surfaces as the instantiate error, correct for a duplicate). Also
`module_edit` does not use these paths (its migration writes files directly).

- [ ] **Step 4: Run the full backend suite + frontend types**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (fix any test that relied on silent filtering per the caveat).
Run (from `frontend/`): `npx tsc -b && npx vitest run`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sheets.py backend/src/grimoire/routes.py backend/tests/ frontend/src/api/client.ts
git commit -m "fix(sheets): unknown-key payloads reject; world sheet CAS parity (write+delete)"
```

---

### Task 11: R2 lock spans — resolve_check, context, continuation, proposals

**Files:**
- Modify: `backend/src/grimoire/store/checks.py` (`resolve_check`), `backend/src/grimoire/store/context.py:294` (`_mechanics`), `backend/src/grimoire/routes.py:2780` (`_continuation_rule_bodies`), `backend/src/grimoire/store/proposals.py:27-33` (`_lock`)
- Test: `backend/tests/test_module_edit.py`

**Interfaces:**
- Produces (spec: Locking rules R2 — every LLM flow that reads pack state holds its campaign lock for the whole computation):
  - `proposals._lock(cid)` delegates to `sheets.lock_for(cid)` — **one lock per campaign, period**; the `_LOCKS`/`_LOCKS_GUARD` registry is deleted. (Import: function-level `from . import sheets` inside `_lock` to avoid import cycles.)
  - **The proposal-creation call site holds the lock across derive + persist** (codex plan review: locking only `proposals.new()`'s write still lets an edit swap the pack between deriving the check from it and persisting). In `routes.py`, find where the streamed roll fence is turned into a proposal (`store.proposals.new(cid, sid, payload)` at `routes.py:1840` and the healing path at `:1872`; read the surrounding function — the payload derivation loads the pack/check just above). Wrap each derivation-through-`proposals.new` span in `with store.sheets.lock_for(cid):` so the check id persisted always came from the currently-published pack.
  - `checks.resolve_check(...)` wraps its body in `with sheets.lock_for(cid):` (read its current signature first; the cid is a parameter).
  - `context._mechanics` wraps its body in `with sheets.lock_for(cid):`.
  - `routes._continuation_rule_bodies` wraps its body in `with store.sheets.lock_for(cid):`.
  - All of these are re-entrant (`RLock`) so nested calls (e.g. `_mechanics` → `sheets.read` which takes the same lock) are safe.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_module_edit.py`:

```python
def test_llm_consumers_hold_campaign_lock(monkeypatch, tmp_path):
    """An edit holding the campaign locks excludes context assembly (proxy
    for all R2 consumers — they share the lock_for domain)."""
    from grimoire.store import context, proposals
    mid = _mk_schema(monkeypatch, tmp_path)
    wid, cid = _bound_campaign(mid)
    # proposals lock domain is unified: taking lock_for(cid) blocks proposals.new
    hit = []
    with sheets.lock_for(cid):
        t = threading.Thread(target=lambda: (proposals.new(cid, "s1", {}), hit.append(1)))
        t.start()
        t.join(timeout=0.3)
        assert not hit          # blocked while the campaign lock is held
    t.join(timeout=5)
    assert hit
```

Add the derivation-interleaving test (the spec's paused-between-derivation-
and-persist case — a source-string check cannot prove this one):

```python
def test_proposal_derivation_excluded_by_edit_lock(monkeypatch, tmp_path):
    """The whole derive→persist span holds the campaign lock: with the lock
    held by an 'edit', a proposal creation cannot even START deriving."""
    from grimoire.store import proposals
    mid = _mk_schema(monkeypatch, tmp_path)
    assert module_edit.upsert_check(mid, "brawl", CHECK)["ok"]
    wid, cid = _bound_campaign(mid)
    derived_under_lock = []

    def derive_and_persist():
        # mirrors the route's shape: lock, read the pack's check, persist
        with sheets.lock_for(cid):
            pack = modules.load_pack(mid)
            assert "brawl" in pack["checks"]
            derived_under_lock.append(proposals.new(cid, "s1", {"check": "brawl"}))

    with sheets.lock_for(cid):
        t = threading.Thread(target=derive_and_persist)
        t.start()
        t.join(timeout=0.3)
        assert not derived_under_lock       # blocked before deriving
        # rename the check while the creator is excluded... would deadlock
        # here (we hold cid's lock) — so just release and let both proceed.
    t.join(timeout=5)
    assert derived_under_lock
    rec = proposals.get(cid, "s1")
    assert rec["payload"]["check"] == "brawl"
```

and a route-level assertion that the real call sites are wrapped:

```python
def test_proposal_route_sites_locked():
    import inspect
    from grimoire import routes as routes_mod
    src = inspect.getsource(routes_mod)
    for line_marker in ("proposals.new(",):
        # every proposals.new call site in routes.py sits inside a
        # `with store.sheets.lock_for(` block — enforced by review, smoke-
        # checked here: the file must contain at least one such wrap.
        assert "sheets.lock_for(" in src
```

Plus a source-level assertion test (cheap and unambiguous):

```python
def test_r2_consumers_reference_lock_for():
    import inspect
    from grimoire.store import checks as checks_mod, context as context_mod
    from grimoire import routes as routes_mod
    assert "lock_for" in inspect.getsource(checks_mod.resolve_check)
    assert "lock_for" in inspect.getsource(context_mod._mechanics)
    assert "lock_for" in inspect.getsource(routes_mod._continuation_rule_bodies)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_module_edit.py -k "llm_consumers or r2_consumers" -v`
Expected: FAIL — `proposals.new` does not block; sources lack `lock_for`.

- [ ] **Step 3: Implement**

`proposals.py` — replace the registry:

```python
def _lock(cid: str) -> threading.RLock:
    """Unified per-campaign lock domain (mechanics Phase 8): proposals share
    sheets.lock_for so a module edit holding the campaign locks excludes
    proposal creation/transition — a proposal derived from the old pack can
    never persist after a check rename/delete swapped it away."""
    from . import sheets  # function-level: avoid import cycles
    return sheets.lock_for(cid)
```

(delete `_LOCKS` and `_LOCKS_GUARD`; keep `locked()` as-is — it already
calls `_lock`.)

`checks.py` — read `resolve_check`'s body; wrap everything after argument
validation in `with sheets.lock_for(cid):` (add `sheets` to the module's
existing imports from `.`; it very likely already imports it — check).

`context.py` `_mechanics` — indent the body under:

```python
def _mechanics(cid: str, sid: str, cast, recent_text: str) -> dict:
    with sheets.lock_for(cid):
        mid = modules.resolve(cid)
        ...
```

`routes.py` `_continuation_rule_bodies` — same wrap with
`store.sheets.lock_for(cid)`.

- [ ] **Step 4: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS — especially the whole Phase 4/5 proposal + audit suites
(the RLock unification must not deadlock any existing nested path; if a test
hangs, the culprit is a plain-`Lock` assumption — `lock_for` returns RLock,
`proposals` previously used RLock, so nesting is safe).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/proposals.py backend/src/grimoire/store/checks.py backend/src/grimoire/store/context.py backend/src/grimoire/routes.py backend/tests/test_module_edit.py
git commit -m "fix(mechanics): LLM pack consumers hold their campaign lock; proposals lock domain unified"
```

---

### Task 12: Module-edit routes

**Files:**
- Modify: `backend/src/grimoire/routes.py` (after the existing module routes, `:383-410`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: every `module_edit` function (Tasks 2-9).
- Produces the route surface (spec: Routes):

```
POST   /api/modules/{mid}/duplicate            {name} → {"id": new_mid}
PUT    /api/modules/{mid}/manifest             {name, description, version, dice, notes, dry_run}
PUT    /api/modules/{mid}/groups/{gid}         {group, dry_run}
DELETE /api/modules/{mid}/groups/{gid}         ?dry_run=1
PUT    /api/modules/{mid}/sheet-types/{tid}    {sheet_type, dry_run}
DELETE /api/modules/{mid}/sheet-types/{tid}    ?dry_run=1
PUT    /api/modules/{mid}/checks/{check_id}    {check, dry_run}
DELETE /api/modules/{mid}/checks/{check_id}    ?dry_run=1
PUT    /api/modules/{mid}/check-defaults       {defaults, dry_run}
PUT    /api/modules/{mid}/rules/{slug}         {flags, body, dry_run}
DELETE /api/modules/{mid}/rules/{slug}         ?dry_run=1
PUT    /api/modules/{mid}/content/{kind}/{id}  {name, body, keys, fields, sheet, dry_run}
DELETE /api/modules/{mid}/content/{kind}/{id}  ?dry_run=1
PUT    /api/modules/{mid}/layout               {layout, dry_run}
PUT    /api/modules/{mid}/theme                {theme, dry_run}
POST   /api/modules/{mid}/rename               {kind, address, to, dry_run}
GET    /api/modules/{mid}/export               → application/zip attachment
POST   /api/modules/import                     raw application/zip body → {"id": new_mid}
```

Result dicts pass through as-is (200, `ok` true/false). `ModuleError` ⇒ 400
with its message; `ModuleNotFound` ⇒ 404. `GET /modules/{mid}` also wraps
its `load_pack` in `module_edit.locked()` (swap-coherent read, R3), as does
export. The import route rejects over-limit `Content-Length` up front (413,
cap 16 MB transfer) and streams `request.stream()` chunk-by-chunk to a temp
file, aborting past the cap — never `await request.body()`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_routes.py` (reuse its `client` fixture idiom):

```python
def _user_module(client):
    return client.post("/api/modules", json={"name": "Realm System"}).json()["id"]


def test_module_edit_routes_round_trip(client):
    mid = _user_module(client)
    r = client.put(f"/api/modules/{mid}/manifest",
                   json={"name": "Realm System", "description": "d", "version": "1",
                         "dice": "1d20", "notes": "n", "dry_run": False})
    assert r.status_code == 200 and r.json()["ok"] is True
    group = {"label": "Attributes",
             "fields": [{"key": "strength", "type": "dots", "max": 5}]}
    assert client.put(f"/api/modules/{mid}/groups/attributes",
                      json={"group": group, "dry_run": False}).json()["ok"]
    st = {"label": "Warden", "kind": "characters", "groups": ["attributes"], "fields": []}
    assert client.put(f"/api/modules/{mid}/sheet-types/warden",
                      json={"sheet_type": st, "dry_run": False}).json()["ok"]
    # dry-run rejection carries errors, writes nothing
    bad = {**group, "fields": [{"key": "strength", "type": "nope"}]}
    r = client.put(f"/api/modules/{mid}/groups/attributes",
                   json={"group": bad, "dry_run": True})
    assert r.status_code == 200 and r.json()["ok"] is False and r.json()["errors"]
    # rename
    r = client.post(f"/api/modules/{mid}/rename",
                    json={"kind": "group", "address": {"from": "attributes"},
                          "to": "traits", "dry_run": False})
    assert r.json()["ok"] is True
    pack = client.get(f"/api/modules/{mid}").json()
    assert "traits" in pack["sheets"]["groups"]
    assert pack["manifest"]["notes"].strip() == "n"


def test_module_edit_builtin_400(client):
    for call in [
        lambda: client.put("/api/modules/d20-basic/manifest",
                           json={"name": "X", "description": "", "version": "",
                                 "dice": "", "notes": "", "dry_run": False}),
        lambda: client.delete("/api/modules/d20-basic/groups/attributes"),
        lambda: client.post("/api/modules/d20-basic/rename",
                            json={"kind": "check", "address": {"from": "a"},
                                  "to": "b", "dry_run": False}),
    ]:
        assert call().status_code == 400


def test_module_edit_unknown_mid_404(client):
    assert client.put("/api/modules/ghost/manifest",
                      json={"name": "X", "description": "", "version": "",
                            "dice": "", "notes": "", "dry_run": False}).status_code == 404


def test_module_duplicate_export_import(client):
    r = client.post("/api/modules/d20-basic/duplicate", json={"name": "My D20"})
    assert r.status_code == 200
    new = r.json()["id"]
    z = client.get(f"/api/modules/{new}/export")
    assert z.status_code == 200
    assert z.headers["content-type"] == "application/zip"
    r = client.post("/api/modules/import", content=z.content,
                    headers={"content-type": "application/zip"})
    assert r.status_code == 200 and r.json()["id"] not in ("", new)


def test_module_import_413(client):
    r = client.post("/api/modules/import", content=b"x",
                    headers={"content-length": str(20 * 1024 * 1024)})
    assert r.status_code == 413
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k module_edit -v`
Expected: FAIL — 404/405s (routes missing).

- [ ] **Step 3: Implement**

Models (next to `ModuleCreate` in `routes.py`):

```python
class ModuleManifestBody(BaseModel):
    name: str = ""
    description: str = ""
    version: str = ""
    dice: str = ""
    notes: str = ""
    dry_run: bool = False


class ModuleGroupBody(BaseModel):
    group: dict = {}
    dry_run: bool = False


class ModuleSheetTypeBody(BaseModel):
    sheet_type: dict = {}
    dry_run: bool = False


class ModuleCheckBody(BaseModel):
    check: dict = {}
    dry_run: bool = False


class ModuleDefaultsBody(BaseModel):
    defaults: dict = {}
    dry_run: bool = False


class ModuleRuleBody(BaseModel):
    flags: dict = {}
    body: str = ""
    dry_run: bool = False


class ModuleContentBody(BaseModel):
    name: str = ""
    body: str = ""
    keys: str = ""
    fields: dict = {}
    sheet: dict | None = None
    dry_run: bool = False


class ModuleLayoutBody(BaseModel):
    layout: dict = {}
    dry_run: bool = False


class ModuleThemeBody(BaseModel):
    theme: dict = {}
    dry_run: bool = False


class ModuleRenameBody(BaseModel):
    kind: str = ""
    address: dict = {}
    to: str = ""
    dry_run: bool = False
```

One error-mapping helper + the routes (import `tempfile`; `Request` and
`Response` from fastapi/starlette are already imported or add them):

```python
def _module_edit_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
    except store.modules.ModuleError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/modules/{mid}/duplicate")
def post_module_duplicate(mid: str, body: ModuleCreate):
    return {"id": _module_edit_call(store.module_edit.duplicate_module, mid, body.name)}


@router.put("/modules/{mid}/manifest")
def put_module_manifest(mid: str, body: ModuleManifestBody):
    return _module_edit_call(store.module_edit.set_manifest, mid,
                             name=body.name, description=body.description,
                             version=body.version, dice=body.dice,
                             notes=body.notes, dry_run=body.dry_run)


@router.put("/modules/{mid}/groups/{gid}")
def put_module_group(mid: str, gid: str, body: ModuleGroupBody):
    return _module_edit_call(store.module_edit.upsert_group, mid, gid,
                             body.group, dry_run=body.dry_run)


@router.delete("/modules/{mid}/groups/{gid}")
def delete_module_group(mid: str, gid: str, dry_run: bool = False):
    return _module_edit_call(store.module_edit.delete_group, mid, gid, dry_run=dry_run)
```

…and the analogous PUT/DELETE pairs for `sheet-types/{tid}`,
`checks/{check_id}`, `check-defaults` (PUT only), `rules/{slug}`,
`content/{kind}/{id}` (PUT passes `name/body/keys/fields/sheet`), `layout`,
`theme` (PUT only), each one line of delegation exactly like the group pair.
For check deletion pass the guard:

```python
@router.delete("/modules/{mid}/checks/{check_id}")
def delete_module_check(mid: str, check_id: str, dry_run: bool = False):
    return _module_edit_call(
        store.module_edit.delete_check, mid, check_id, dry_run=dry_run,
        pre_swap=store.module_edit.check_proposal_guard(mid, check_id))
```

Rename, export, import:

```python
@router.post("/modules/{mid}/rename")
def post_module_rename(mid: str, body: ModuleRenameBody):
    return _module_edit_call(store.module_edit.rename, mid, body.kind,
                             body.address, body.to, dry_run=body.dry_run)


@router.get("/modules/{mid}/export")
def get_module_export(mid: str):
    data = _module_edit_call(store.module_edit.export_module, mid)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{mid}.zip"'})


IMPORT_CAP = 16 * 1024 * 1024


@router.post("/modules/import")
async def post_module_import(request: Request):
    cl = request.headers.get("content-length")
    if cl and cl.isdigit() and int(cl) > IMPORT_CAP:
        raise HTTPException(status_code=413, detail="zip too large")
    fd, tmp_name = tempfile.mkstemp(suffix=".zip")
    total = 0
    try:
        with os.fdopen(fd, "wb") as f:
            async for chunk in request.stream():
                total += len(chunk)
                if total > IMPORT_CAP:
                    raise HTTPException(status_code=413, detail="zip too large")
                f.write(chunk)
        return {"id": _module_edit_call(store.module_edit.import_module, Path(tmp_name))}
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
```

Rewire the two existing module mutators to the transactional path (Task 9):
`post_module` (`routes.py:388-390`) calls
`store.module_edit.create_module(body.name)`; `delete_module`
(`routes.py:401-409`) calls `store.module_edit.delete_module(mid)` (same
except-clauses). Add route tests: POST still returns `{"id"}` and the pack
validates; DELETE on a builtin still 400s.

Wrap `get_module` (`routes.py:393-398`) in the R3 lock:

```python
@router.get("/modules/{mid}")
def get_module(mid: str):
    try:
        with store.module_edit.locked():
            return store.modules.load_pack(mid)
    except store.modules.ModuleNotFound:
        raise HTTPException(status_code=404, detail="module not found")
```

and the same `with store.module_edit.locked():` around
`store.modules.read_content` in `get_module_content` (`routes.py:608-615`).
Ensure `store/__init__.py` re-exports `module_edit` the same way it exports
`modules`/`sheets` (check how `store.modules` resolves — add
`from . import module_edit` if the package uses explicit imports).

- [ ] **Step 4: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/src/grimoire/store/__init__.py backend/tests/test_routes.py
git commit -m "feat(routes): module authoring endpoints — sections, rename, duplicate, export, streamed import"
```

---

### Task 13: Frontend API client + ModulesView actions

**Files:**
- Modify: `frontend/src/api/client.ts:270-324` (types) and `:719-742` (fns), `frontend/src/routes/ModulesView.tsx`
- Test: `frontend/src/routes/ModulesView.test.tsx`

**Interfaces:**
- Produces (client):

```typescript
export type ModuleEditResult = {
  ok: boolean; errors: string[]; display_errors: DisplayError[];
  impact?: { sheet_types: string[]; sheets_migrated: number;
             sheets_newly_invalid: number; dangling_refs: number };
  sample?: Record<string, { fields: Record<string, unknown>;
                            derived: Record<string, number | boolean> }>;
  migration?: { migrated: number; skipped: string[] };
};
export type ModuleRenameKind =
  "group" | "field" | "derived" | "sheet_type" | "check" | "rule" | "content";
```

  and `ModuleDetail.manifest` gains `notes?: string`. New api fns (all thin
  `request` wrappers following the file's existing style):
  `duplicateModule(mid, name) → {id}`, `importModule(file: Blob) → {id}`
  (POST raw body, `content-type: application/zip`), `exportModuleUrl(mid) →
  string` (returns the `/api/modules/${mid}/export` URL for an `<a
  download>`), `putModuleManifest(mid, body)`, `putModuleGroup(mid, gid,
  group, dryRun)`, `deleteModuleGroup(mid, gid, dryRun)`,
  `putModuleSheetType(mid, tid, sheetType, dryRun)`,
  `deleteModuleSheetType(mid, tid, dryRun)`, `putModuleCheck(mid, id, check,
  dryRun)`, `deleteModuleCheck(mid, id, dryRun)`,
  `putModuleCheckDefaults(mid, defaults, dryRun)`, `putModuleRule(mid, slug,
  flags, body, dryRun)`, `deleteModuleRule(mid, slug, dryRun)`,
  `putModuleContent(mid, kind, id, body, dryRun)`,
  `deleteModuleContent(mid, kind, id, dryRun)`, `putModuleLayout(mid,
  layout, dryRun)`, `putModuleTheme(mid, theme, dryRun)`,
  `renameModulePart(mid, kind, address, to, dryRun)` — every mutator returns
  `ModuleEditResult`. DELETE fns pass `?dry_run=1` when dryRun.
- Produces (ModulesView): rail gains **+ New module** (existing?) — check:
  the current rail has no create button; add **+ New** (`api.createModule` —
  add `createModule(name) → {id}` wrapping the existing `POST /api/modules`
  if the client lacks it) and **+ Import** (hidden `<input type="file"
  accept=".zip">`). Detail sidebar gains `.form-actions` buttons: **Edit**
  (user modules only) → `mode: "edit"` mounting `ModuleEditor`; **Duplicate**
  (always; inline name prompt defaulting to `"<name> copy"`); **Export**
  (an `<a href={exportModuleUrl(mid)} download>` styled as a button).
  Builtins show the hint `built-in — duplicate to customize` where Edit
  would be.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/routes/ModulesView.test.tsx` (mock `api` per the file's
existing pattern; the mock gains the new fns):

```tsx
test("user module shows Edit; builtin shows duplicate hint", async () => {
  (api.listModules as any).mockResolvedValue([
    { id: "mine", name: "Mine", source: "user", valid: true },
    { id: "d20-basic", name: "Basic D20", source: "builtin", valid: true },
  ]);
  (api.readModule as any).mockResolvedValue({ ...DETAIL, id: "mine", source: "user" });
  render(<ModulesView />);
  fireEvent.click(await screen.findByText("Mine"));
  expect(await screen.findByRole("button", { name: "Edit" })).toBeInTheDocument();
  (api.readModule as any).mockResolvedValue({ ...DETAIL, id: "d20-basic", source: "builtin" });
  fireEvent.click(screen.getByText("Basic D20"));
  await waitFor(() =>
    expect(screen.getByText(/duplicate to customize/)).toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
});

test("Duplicate prompts for a name and selects the copy", async () => {
  (api.duplicateModule as any).mockResolvedValue({ id: "basic-d20-copy" });
  // select builtin as above, click Duplicate, accept the default name
  fireEvent.click(screen.getByRole("button", { name: "Duplicate" }));
  fireEvent.click(screen.getByRole("button", { name: "Create copy" }));
  await waitFor(() => expect(api.duplicateModule).toHaveBeenCalledWith(
    "d20-basic", "Basic D20 copy"));
});

test("Import posts the picked file and reloads the list", async () => {
  (api.importModule as any).mockResolvedValue({ id: "imported" });
  render(<ModulesView />);
  const input = screen.getByLabelText("Import module zip") as HTMLInputElement;
  const file = new File(["zip"], "pack.zip", { type: "application/zip" });
  fireEvent.change(input, { target: { files: [file] } });
  await waitFor(() => expect(api.importModule).toHaveBeenCalled());
});

test("Edit mounts the module editor", async () => {
  // user module selected as above
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(await screen.findByText("Manifest")).toBeInTheDocument(); // section nav
});
```

Define `DETAIL` at the top of the test file as a minimal valid
`ModuleDetail` (manifest/sheets/checks/rules/content/errors keys — copy the
shape the existing tests in this file use and extend it).

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/routes/ModulesView.test.tsx`
Expected: FAIL — buttons absent, api fns missing on the mock/type level.

- [ ] **Step 3: Implement**

`client.ts`: add the types above; add the fns, e.g.:

```typescript
  createModule: (name: string) => request<{ id: string }>("POST", "/api/modules", { name }),
  duplicateModule: (mid: string, name: string) =>
    request<{ id: string }>("POST", `/api/modules/${mid}/duplicate`, { name }),
  importModule: (file: Blob) =>
    fetch("/api/modules/import", { method: "POST", body: file,
      headers: { "content-type": "application/zip" } }).then(async (r) => {
        if (!r.ok) throw new ApiError(r.status, (await r.json()).detail ?? r.statusText);
        return r.json() as Promise<{ id: string }>;
      }),
  exportModuleUrl: (mid: string) => `/api/modules/${mid}/export`,
  putModuleManifest: (mid: string, body: { name: string; description: string;
    version: string; dice: string; notes: string; dry_run: boolean }) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/manifest`, body),
  putModuleGroup: (mid: string, gid: string, group: unknown, dryRun = false) =>
    request<ModuleEditResult>("PUT", `/api/modules/${mid}/groups/${gid}`,
      { group, dry_run: dryRun }),
  deleteModuleGroup: (mid: string, gid: string, dryRun = false) =>
    request<ModuleEditResult>("DELETE",
      `/api/modules/${mid}/groups/${gid}${dryRun ? "?dry_run=1" : ""}`),
  renameModulePart: (mid: string, kind: ModuleRenameKind, address: Record<string, string>,
                     to: string, dryRun = false) =>
    request<ModuleEditResult>("POST", `/api/modules/${mid}/rename`,
      { kind, address, to, dry_run: dryRun }),
```

…and the remaining wrappers following the same two-line pattern (match
`request`'s actual signature in this file — if it already supports raw
bodies, prefer it over bare `fetch` for `importModule`).

`ModulesView.tsx`: add `mode` state (`"view" | "edit"`), the rail buttons,
sidebar `.form-actions` with Edit/Duplicate/Export (Export as
`<a className="row" href={api.exportModuleUrl(detail.id)} download>`),
an inline duplicate-name form (`useState<string | null>(dupName)`), the
hidden file input with `aria-label="Import module zip"`, and:

```tsx
{mode === "edit" && detail ? (
  <ModuleEditor detail={detail} onDone={() => { setMode("view"); select(detail.id); }} />
) : ( /* existing detail-view */ )}
```

`ModuleEditor` doesn't exist yet — create a placeholder component in
`frontend/src/components/ModuleEditor.tsx` that renders the section nav
labels only (`Manifest · Groups · Sheet types · Checks · Rules · Content ·
Layout · Theme`) and the Done button; Task 14 fills it in. This keeps the
task shippable and the tests green.

- [ ] **Step 4: Run tests + typecheck**

Run (from `frontend/`): `npx tsc -b && npx vitest run src/routes/ModulesView.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/ModulesView.tsx frontend/src/components/ModuleEditor.tsx frontend/src/routes/ModulesView.test.tsx
git commit -m "feat(frontend): module edit API client + ModulesView edit/duplicate/export/import actions"
```

---

### Task 14: ModuleEditor shell — section nav, save harness, Manifest

**Files:**
- Modify: `frontend/src/components/ModuleEditor.tsx` (replace the Task 13 placeholder)
- Test: `frontend/src/components/ModuleEditor.test.tsx` (new)

**Interfaces:**
- Consumes: `ModuleDetail`, the Task 13 api fns.
- Produces:
  - `ModuleEditor({ detail, onDone }: { detail: ModuleDetail; onDone: () => void })` — section nav + per-section bodies. Holds `pack` state (refetched via `api.readModule` after every successful save; child sections receive `pack` + `reload()`).
  - `useModuleDryRun(saveFn)` — shared hook: `{ result, pending, check(payload), save(payload) }`; `check` debounces (500 ms) `saveFn(payload, /*dryRun*/ true)` into `result` (a `ModuleEditResult`); `save` runs `saveFn(payload, false)` but **first** shows the impact confirm when the latest `result.impact` has `sheets_migrated + sheets_newly_invalid + dangling_refs > 0`.
  - `ImpactConfirm({ impact, onConfirm, onCancel })` — renders "used by N sheet types · migrates N sheets · N sheets become invalid · N refs go dangling" with Confirm/Cancel buttons.
  - `ErrorList({ result })` — `result.errors` as `.banner` lines, `result.display_errors` as `.field-hint` lines.
  - The **Manifest** section: name/description/version/dice inputs + notes textarea, live dry-run validation, Save.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/ModuleEditor.test.tsx`:

```tsx
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { api } from "../api/client";
import ModuleEditor from "./ModuleEditor";

vi.mock("../api/client", () => ({
  api: {
    readModule: vi.fn(), putModuleManifest: vi.fn(),
    putModuleGroup: vi.fn(), deleteModuleGroup: vi.fn(),
    putModuleSheetType: vi.fn(), deleteModuleSheetType: vi.fn(),
    putModuleCheck: vi.fn(), deleteModuleCheck: vi.fn(),
    putModuleCheckDefaults: vi.fn(),
    putModuleRule: vi.fn(), deleteModuleRule: vi.fn(),
    putModuleContent: vi.fn(), deleteModuleContent: vi.fn(),
    putModuleLayout: vi.fn(), putModuleTheme: vi.fn(),
    renameModulePart: vi.fn(), listEntities: vi.fn(),
  },
  ApiError: class extends Error {},
}));

const DETAIL: any = {
  id: "realm-system", source: "user",
  manifest: { id: "realm-system", name: "Realm System", version: "1", notes: "n" },
  sheets: { groups: {}, sheet_types: {} },
  checks: {}, rules: [], content: [], errors: [],
  layout: { sheet_types: {} }, theme: {}, display_errors: [],
};
const OK = { ok: true, errors: [], display_errors: [] };

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
  (api.readModule as any).mockResolvedValue(DETAIL);
});

test("manifest section saves and reloads", async () => {
  (api.putModuleManifest as any).mockResolvedValue(OK);
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "Manifest" }));
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Realm 2" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleManifest).toHaveBeenCalledWith(
    "realm-system", expect.objectContaining({ name: "Realm 2", dry_run: false })));
  await waitFor(() => expect(api.readModule).toHaveBeenCalled());
});

test("debounced dry-run renders errors inline", async () => {
  (api.putModuleManifest as any).mockResolvedValue(
    { ok: false, errors: ["module.md: manifest requires a name"], display_errors: [] });
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "" } });
  await vi.advanceTimersByTimeAsync(600);
  expect(await screen.findByText(/requires a name/)).toBeInTheDocument();
  expect(api.putModuleManifest).toHaveBeenCalledWith(
    "realm-system", expect.objectContaining({ dry_run: true }));
});

test("impact confirm gates the save and Cancel aborts", async () => {
  (api.putModuleManifest as any).mockResolvedValue(
    { ...OK, impact: { sheet_types: ["warden"], sheets_migrated: 2,
                       sheets_newly_invalid: 1, dangling_refs: 0 } });
  render(<ModuleEditor detail={DETAIL} onDone={() => {}} />);
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "X" } });
  await vi.advanceTimersByTimeAsync(600);          // dry-run stores the impact
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(await screen.findByText(/migrates 2 sheets/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  // only the dry_run call happened
  const real = (api.putModuleManifest as any).mock.calls
    .filter((c: any[]) => c[1].dry_run === false);
  expect(real).toHaveLength(0);
});
```

(Manifest edits never actually carry impact — the harness is exercised
through the manifest section because it is the simplest host; the behavior
is shared by every section.)

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/ModuleEditor.test.tsx`
Expected: FAIL — the placeholder has no Manifest form.

- [ ] **Step 3: Implement**

`ModuleEditor.tsx` core:

```tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ModuleDetail, type ModuleEditResult } from "../api/client";

const SECTIONS = ["Manifest", "Groups", "Sheet types", "Checks", "Rules",
                  "Content", "Layout", "Theme"] as const;
type Section = (typeof SECTIONS)[number];

export type SaveFn = (dryRun: boolean) => Promise<ModuleEditResult>;

export function useModuleDryRun(save: SaveFn, deps: unknown[]) {
  const [result, setResult] = useState<ModuleEditResult | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [saving, setSaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const revision = useRef(0);
  useEffect(() => {
    const rev = ++revision.current;      // stale debounced responses drop
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      save(true).then((r) => {
        if (rev === revision.current) setResult(r);
      }).catch(() => {});
    }, 500);
    return () => clearTimeout(timer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  const impactful = (i?: ModuleEditResult["impact"]) =>
    !!i && i.sheets_migrated + i.sheets_newly_invalid + i.dangling_refs > 0;
  const commit = useCallback(async (onOk: (r: ModuleEditResult) => void) => {
    const r = await save(false);
    setResult(r);
    if (r.ok) onOk(r);
  }, [save]);
  // Save NEVER trusts the last debounced result — it awaits a fresh
  // dry-run of the CURRENT form before deciding whether to confirm (codex
  // plan review round 2: save-immediately-after-edit consulted a stale or
  // null result and committed a destructive change unconfirmed).
  const requestSave = useCallback(async (onOk: (r: ModuleEditResult) => void) => {
    clearTimeout(timer.current);
    setSaving(true);
    try {
      const fresh = await save(true);
      setResult(fresh);
      if (!fresh.ok) return;             // errors render; nothing committed
      if (impactful(fresh.impact)) setConfirming(true);
      else await commit(onOk);
    } finally {
      setSaving(false);
    }
  }, [save, commit]);
  return { result, confirming, setConfirming, commit, requestSave, saving };
}

export function ErrorList({ result }: { result: ModuleEditResult | null }) {
  if (!result) return null;
  return (
    <>
      {result.errors.map((e, i) => <div key={i} className="banner">{e}</div>)}
      {result.display_errors.map((e, i) => (
        <div key={`d${i}`} className="field-hint">{e.message}</div>
      ))}
    </>
  );
}

export function ImpactConfirm({ impact, onConfirm, onCancel }: {
  impact: NonNullable<ModuleEditResult["impact"]>;
  onConfirm: () => void; onCancel: () => void;
}) {
  return (
    <div className="banner">
      used by {impact.sheet_types.length} sheet types · migrates{" "}
      {impact.sheets_migrated} sheets · {impact.sheets_newly_invalid} sheets
      become invalid · {impact.dangling_refs} refs go dangling
      <div className="form-actions">
        <button className="primary" onClick={onConfirm}>Confirm</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

export default function ModuleEditor({ detail, onDone }: {
  detail: ModuleDetail; onDone: () => void;
}) {
  const [pack, setPack] = useState(detail);
  const [section, setSection] = useState<Section>("Manifest");
  const reload = useCallback(
    () => api.readModule(detail.id).then(setPack), [detail.id]);
  return (
    <div className="module-editor">
      <div className="chips">
        {SECTIONS.map((s) => (
          <button key={s}
                  className={"chip" + (s === section ? " on" : "")}
                  onClick={() => setSection(s)}>{s}</button>
        ))}
        <button className="chip" onClick={onDone}>Done</button>
      </div>
      {section === "Manifest" && <ManifestSection pack={pack} reload={reload} />}
      {/* Groups / Sheet types: Task 15; Checks / Rules: Task 16;
          Content: Task 17; Layout / Theme: Task 18 */}
    </div>
  );
}

function ManifestSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const m = pack.manifest;
  const [form, setForm] = useState({
    name: m.name ?? "", description: m.description ?? "",
    version: m.version ?? "", dice: m.dice ?? "", notes: (m as any).notes ?? "",
  });
  const save: SaveFn = (dryRun) =>
    api.putModuleManifest(pack.id, { ...form, dry_run: dryRun });
  const dr = useModuleDryRun(save, [form]);
  return (
    <div className="detail-main">
      {dr.confirming && dr.result?.impact && (
        <ImpactConfirm impact={dr.result.impact}
                       onConfirm={() => { dr.setConfirming(false); void dr.commit(() => void reload()); }}
                       onCancel={() => dr.setConfirming(false)} />
      )}
      <ErrorList result={dr.result} />
      <label>Name
        <input value={form.name}
               onChange={(e) => setForm({ ...form, name: e.target.value })} />
      </label>
      <label>Description
        <input value={form.description}
               onChange={(e) => setForm({ ...form, description: e.target.value })} />
      </label>
      <label>Version
        <input value={form.version}
               onChange={(e) => setForm({ ...form, version: e.target.value })} />
      </label>
      <label>Dice
        <input value={form.dice}
               onChange={(e) => setForm({ ...form, dice: e.target.value })} />
      </label>
      <label>Notes
        <textarea value={form.notes}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })} />
      </label>
      <div className="form-actions">
        <button className="primary"
                onClick={() => dr.requestSave(() => void reload())}>Save</button>
      </div>
    </div>
  );
}
```

(Adjust `<label>` markup to the codebase's existing form idiom — check
`GreetingEditor.tsx` for the exact label/input pattern and testing-library
accessibility; `getByLabelText` must resolve.)

- [ ] **Step 4: Run tests + typecheck**

Run (from `frontend/`): `npx tsc -b && npx vitest run src/components/ModuleEditor.test.tsx src/routes/ModulesView.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ModuleEditor.tsx frontend/src/components/ModuleEditor.test.tsx
git commit -m "feat(frontend): ModuleEditor shell — section nav, dry-run harness, impact confirm, manifest"
```

---

### Task 15: Schema sections — Groups + Sheet types + rename affordance

**Files:**
- Create: `frontend/src/components/ModuleSchemaEditor.tsx`
- Modify: `frontend/src/components/ModuleEditor.tsx` (mount the sections)
- Test: `frontend/src/components/ModuleSchemaEditor.test.tsx` (new)

**Interfaces:**
- Consumes: `useModuleDryRun`/`ErrorList`/`ImpactConfirm`/`SaveFn` (Task 14), api fns (Task 13).
- Produces:
  - `GroupsSection({ pack, reload })` and `SheetTypesSection({ pack, reload })` (exported from `ModuleSchemaEditor.tsx`; `ModuleEditor` renders them for the `Groups`/`Sheet types` nav entries). Each is a mini list/detail per CLAUDE.md: a records rail (`+ New group` / `+ New sheet type`, one `.row` per record) and a view/edit body (view = read-only chips like `ModulesView` renders today; Edit/Save/Cancel; `+ New` opens the form with an id input).
  - Group form: field rows — key (read-only for existing fields + a `Rename…` button; free input for new rows), label, type `<select>` over `number|dots|track|resource|text|list|ref`, extras per type (`max` number input for dots/track/resource, `min`/`max`/`default` for number, `ref_kind` select over `locations|lore|items|groups|creatures` for ref) — plus derived rows (name + expression input; existing names get the rename affordance too) and a remove button per row.
  - Sheet-type form: label, kind `<select>` over the six kinds, ordered group-membership picker (checkbox list of `pack.sheets.groups` keys, checked order preserved in an array), own-field rows and derived rows (same row components), `creation` sub-form (per composed group: budget input + cost rows over that group's field keys), `advancement` sub-form (pool select over the assembled resource fields, cost rows of field key + expression).
  - Rename affordance: the `Rename…` button opens an inline prompt (input + Rename/Cancel); it is **disabled while the form is dirty** (compare form state to the pack). **Renames and deletes are impact-gated exactly like saves** (codex plan review): the action first runs with `dryRun=true`; when the returned `impact` has any nonzero count, `ImpactConfirm` renders and only Confirm issues the real call; Cancel issues nothing. A shared `confirmGate` helper owns this (below). On success, `reload()`. The sample computation (`result.sample`) renders each derived expression's value as a `.field-hint` next to its row when present.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/ModuleSchemaEditor.test.tsx` (same api mock
block as Task 14's test file):

```tsx
const PACK: any = {
  id: "realm-system", source: "user",
  manifest: { id: "realm-system", name: "Realm System" },
  sheets: {
    groups: { attributes: { label: "Attributes",
      fields: [{ key: "strength", label: "Strength", type: "dots", max: 5 }],
      derived: { might: "strength * 2" } } },
    sheet_types: { warden: { label: "Warden", kind: "characters",
      groups: ["attributes"], fields: [] } },
  },
  checks: {}, rules: [], content: [], errors: [],
  layout: { sheet_types: {} }, theme: {}, display_errors: [],
};

test("group row opens read-only view; Edit reveals the form", async () => {
  render(<GroupsSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Attributes"));
  expect(screen.getByText("Strength")).toBeInTheDocument();
  expect(screen.queryByDisplayValue("Strength")).not.toBeInTheDocument(); // no inputs
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByDisplayValue("Strength")).toBeInTheDocument();
});

test("saving a group posts the assembled def", async () => {
  (api.putModuleGroup as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<GroupsSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Attributes"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByDisplayValue("Strength"), { target: { value: "Brawn" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleGroup).toHaveBeenCalledWith(
    "realm-system", "attributes",
    expect.objectContaining({
      fields: [expect.objectContaining({ key: "strength", label: "Brawn" })] }),
    false));
});

test("+ New group opens the form directly with an id input", () => {
  render(<GroupsSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "+ New group" }));
  expect(screen.getByLabelText("Group id")).toBeInTheDocument();
});

test("rename affordance dry-runs, then commits when impact is clean", async () => {
  (api.renameModulePart as any).mockResolvedValue({ ok: true, errors: [], display_errors: [],
    impact: { sheet_types: [], sheets_migrated: 0, sheets_newly_invalid: 0, dangling_refs: 0 } });
  const reload = vi.fn().mockResolvedValue(undefined);
  render(<GroupsSection pack={PACK} reload={reload} />);
  fireEvent.click(screen.getByText("Attributes"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Rename…" })[0]);
  fireEvent.change(screen.getByLabelText("New key"), { target: { value: "brawn" } });
  fireEvent.click(screen.getByRole("button", { name: "Rename" }));
  await waitFor(() => expect(api.renameModulePart).toHaveBeenCalledWith(
    "realm-system", "field", { from: "strength", group: "attributes" }, "brawn", true));
  await waitFor(() => expect(api.renameModulePart).toHaveBeenCalledWith(
    "realm-system", "field", { from: "strength", group: "attributes" }, "brawn", false));
  expect(reload).toHaveBeenCalled();
  // dirty form blocks the affordance entirely
  fireEvent.change(screen.getByDisplayValue("Strength"), { target: { value: "X" } });
  expect(screen.getAllByRole("button", { name: "Rename…" })[0]).toBeDisabled();
});

test("impactful rename shows the confirm; Cancel sends no real call", async () => {
  (api.renameModulePart as any).mockResolvedValue({ ok: true, errors: [], display_errors: [],
    impact: { sheet_types: ["warden"], sheets_migrated: 3, sheets_newly_invalid: 0, dangling_refs: 0 } });
  render(<GroupsSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Attributes"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.click(screen.getAllByRole("button", { name: "Rename…" })[0]);
  fireEvent.change(screen.getByLabelText("New key"), { target: { value: "brawn" } });
  fireEvent.click(screen.getByRole("button", { name: "Rename" }));
  expect(await screen.findByText(/migrates 3 sheets/)).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  const real = (api.renameModulePart as any).mock.calls.filter((c: any[]) => c[4] === false);
  expect(real).toHaveLength(0);
});

test("sheet-type form drives group membership and advancement pool options", async () => {
  (api.putModuleSheetType as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<SheetTypesSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Warden"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(screen.getByRole("checkbox", { name: "Attributes" })).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleSheetType).toHaveBeenCalledWith(
    "realm-system", "warden",
    expect.objectContaining({ kind: "characters", groups: ["attributes"] }),
    false));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/ModuleSchemaEditor.test.tsx`
Expected: FAIL — module doesn't exist.

- [ ] **Step 3: Implement**

`ModuleSchemaEditor.tsx` in full:

```tsx
import { useMemo, useState } from "react";
import { api, type ModuleDetail, type ModuleEditResult, type ModuleField } from "../api/client";
import { ErrorList, ImpactConfirm, useModuleDryRun, type SaveFn } from "./ModuleEditor";

const FIELD_TYPES = ["number", "dots", "track", "resource", "text", "list", "ref"];
const REF_KINDS = ["locations", "lore", "items", "groups", "creatures"];
const SHEET_KINDS = ["characters", "items", "locations", "creatures", "groups", "lore"];

type Derived = Record<string, string>;

export function RenamePrompt({ disabled, onRename }: {
  disabled: boolean; onRename: (to: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState("");
  if (!open) {
    return (
      <button disabled={disabled}
              title={disabled ? "save or cancel your edits first" : "rename"}
              onClick={() => setOpen(true)}>Rename…</button>
    );
  }
  return (
    <span className="chips">
      <label>New key<input value={to} onChange={(e) => setTo(e.target.value)} /></label>
      <button onClick={() => { setOpen(false); onRename(to); }}>Rename</button>
      <button onClick={() => setOpen(false)}>Cancel</button>
    </span>
  );
}

function num(v: string): number | undefined {
  const n = parseInt(v, 10);
  return Number.isNaN(n) ? undefined : n;
}

export function FieldRows({ fields, setFields, existingKeys, dirty, onRename }: {
  fields: ModuleField[]; setFields: (f: ModuleField[]) => void;
  existingKeys: Set<string>; dirty: boolean;
  onRename: (from: string, to: string) => void;
}) {
  const upd = (i: number, patch: Partial<ModuleField>) =>
    setFields(fields.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  return (
    <div>
      {fields.map((f, i) => (
        <div className="chips" key={i}>
          <input aria-label="Field key" value={f.key}
                 readOnly={existingKeys.has(f.key)}
                 onChange={(e) => upd(i, { key: e.target.value })} />
          {existingKeys.has(f.key) && (
            <RenamePrompt disabled={dirty} onRename={(to) => onRename(f.key, to)} />
          )}
          <input aria-label="Field label" value={f.label ?? ""}
                 onChange={(e) => upd(i, { label: e.target.value })} />
          <select aria-label="Field type" value={f.type}
                  onChange={(e) => upd(i, { type: e.target.value })}>
            {FIELD_TYPES.map((t) => <option key={t}>{t}</option>)}
          </select>
          {["dots", "track", "resource", "number"].includes(f.type) && (
            <input aria-label="Max" type="number" value={f.max ?? ""}
                   onChange={(e) => upd(i, { max: num(e.target.value) })} />
          )}
          {f.type === "number" && (
            <>
              <input aria-label="Min" type="number" value={f.min ?? ""}
                     onChange={(e) => upd(i, { min: num(e.target.value) })} />
              <input aria-label="Default" type="number" value={f.default ?? ""}
                     onChange={(e) => upd(i, { default: num(e.target.value) })} />
            </>
          )}
          {f.type === "ref" && (
            <select aria-label="Ref kind" value={f.ref_kind ?? "lore"}
                    onChange={(e) => upd(i, { ref_kind: e.target.value })}>
              {REF_KINDS.map((k) => <option key={k}>{k}</option>)}
            </select>
          )}
          <button onClick={() => setFields(fields.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <button onClick={() => setFields([...fields, { key: "", type: "number" } as ModuleField])}>
        + Add field
      </button>
    </div>
  );
}

export function DerivedRows({ derived, setDerived, existing, dirty, onRename, sample }: {
  derived: Derived; setDerived: (d: Derived) => void;
  existing: Set<string>; dirty: boolean;
  onRename: (from: string, to: string) => void;
  sample?: Record<string, number | boolean>;
}) {
  const entries = Object.entries(derived);
  return (
    <div>
      {entries.map(([name, expr]) => (
        <div className="chips" key={name}>
          <input aria-label="Derived name" value={name} readOnly={existing.has(name)}
                 onChange={(e) => {
                   const d = { ...derived };
                   delete d[name];
                   d[e.target.value] = expr;
                   setDerived(d);
                 }} />
          {existing.has(name) && (
            <RenamePrompt disabled={dirty} onRename={(to) => onRename(name, to)} />
          )}
          <input aria-label="Derived expression" value={expr}
                 onChange={(e) => setDerived({ ...derived, [name]: e.target.value })} />
          {sample && name in sample && (
            <span className="field-hint">= {String(sample[name])}</span>
          )}
          <button onClick={() => {
            const d = { ...derived };
            delete d[name];
            setDerived(d);
          }}>Remove</button>
        </div>
      ))}
      <button onClick={() => setDerived({ ...derived, "": "" })}>+ Add derived</button>
    </div>
  );
}

type GroupForm = { gid: string; isNew: boolean; label: string;
                   fields: ModuleField[]; derived: Derived };

export function GroupsSection({ pack, reload }: {
  pack: ModuleDetail; reload: () => Promise<unknown>;
}) {
  const groups = pack.sheets.groups;
  const [selected, setSelected] = useState<string | null>(null);
  const [mode, setMode] = useState<"view" | "edit">("view");
  const [form, setForm] = useState<GroupForm | null>(null);

  const seed = (gid: string): GroupForm => ({
    gid, isNew: false, label: groups[gid]?.label ?? gid,
    fields: (groups[gid]?.fields ?? []).map((f) => ({ ...f })),
    derived: { ...(groups[gid]?.derived ?? {}) },
  });
  const baseline = useMemo(
    () => (form && !form.isNew ? JSON.stringify(seed(form.gid)) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [form?.gid, pack]);
  const dirty = form != null && baseline != null && JSON.stringify(form) !== baseline;

  const save: SaveFn = (dryRun) =>
    api.putModuleGroup(pack.id, form!.gid, {
      label: form!.label, fields: form!.fields,
      ...(Object.keys(form!.derived).length ? { derived: form!.derived } : {}),
    }, dryRun);
  const dr = useModuleDryRun(form ? save : async () => ({ ok: true, errors: [], display_errors: [] } as ModuleEditResult), [form]);
  const done = () => { setMode("view"); setForm(null); void reload(); };
  // impact gate shared by renames and deletes: dry-run first; confirm when
  // any impact count is nonzero; Cancel sends nothing.
  const [gate, setGate] = useState<{ impact: NonNullable<ModuleEditResult["impact"]>;
                                     run: () => Promise<unknown> } | null>(null);
  const [gateError, setGateError] = useState<string[]>([]);
  const confirmGate = (dryCall: () => Promise<ModuleEditResult>,
                       realCall: () => Promise<ModuleEditResult>) =>
    dryCall().then((r) => {
      if (!r.ok) { setGateError(r.errors); return; }
      const i = r.impact;
      const run = () => realCall().then((rr) =>
        rr.ok ? done() : setGateError(rr.errors));
      if (i && i.sheets_migrated + i.sheets_newly_invalid + i.dangling_refs > 0) {
        setGate({ impact: i, run });
      } else {
        void run();
      }
    });
  const rename = (kind: "field" | "derived") => (from: string, to: string) =>
    void confirmGate(
      () => api.renameModulePart(pack.id, kind, { from, group: form!.gid }, to, true),
      () => api.renameModulePart(pack.id, kind, { from, group: form!.gid }, to, false));

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="row" onClick={() => {
          setSelected(null);
          setForm({ gid: "", isNew: true, label: "", fields: [], derived: {} });
          setMode("edit");
        }}>+ New group</button>
        {Object.keys(groups).map((gid) => (
          <button key={gid} className={"row" + (selected === gid ? " active" : "")}
                  onClick={() => { setSelected(gid); setMode("view"); setForm(null); }}>
            {groups[gid]?.label ?? gid}
          </button>
        ))}
      </div>
      <div className="editor-body">
        {mode === "view" && selected && (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{groups[selected]?.label ?? selected}</h3>
              <div className="chips">
                {(groups[selected]?.fields ?? []).map((f) => (
                  <span key={f.key} className="chip">{f.label ?? f.key}</span>
                ))}
                {Object.keys(groups[selected]?.derived ?? {}).map((d) => (
                  <span key={d} className="chip on">{d}</span>
                ))}
              </div>
            </div>
            <aside className="detail-sidebar">
              {gate && (
                <ImpactConfirm impact={gate.impact}
                               onConfirm={() => { const g = gate; setGate(null); void g.run(); }}
                               onCancel={() => setGate(null)} />
              )}
              {gateError.map((e, i) => <div key={i} className="banner">{e}</div>)}
              <div className="form-actions">
                <button onClick={() => { setForm(seed(selected)); setMode("edit"); }}>Edit</button>
                <button onClick={() => void confirmGate(
                  () => api.deleteModuleGroup(pack.id, selected, true),
                  () => api.deleteModuleGroup(pack.id, selected, false))}>Delete</button>
              </div>
            </aside>
          </div>
        )}
        {mode === "edit" && form && (
          <div className="detail-main">
            {dr.confirming && dr.result?.impact && (
              <ImpactConfirm impact={dr.result.impact}
                             onConfirm={() => { dr.setConfirming(false); void dr.commit(done); }}
                             onCancel={() => dr.setConfirming(false)} />
            )}
            {gate && (
              <ImpactConfirm impact={gate.impact}
                             onConfirm={() => { const g = gate; setGate(null); void g.run(); }}
                             onCancel={() => setGate(null)} />
            )}
            {gateError.map((e, i) => <div key={i} className="banner">{e}</div>)}
            <ErrorList result={dr.result} />
            {form.isNew && (
              <label>Group id
                <input value={form.gid}
                       onChange={(e) => setForm({ ...form, gid: e.target.value })} />
              </label>
            )}
            <label>Label
              <input value={form.label}
                     onChange={(e) => setForm({ ...form, label: e.target.value })} />
            </label>
            <FieldRows fields={form.fields}
                       setFields={(fields) => setForm({ ...form, fields })}
                       existingKeys={new Set((groups[form.gid]?.fields ?? []).map((f) => f.key))}
                       dirty={dirty} onRename={rename("field")} />
            <DerivedRows derived={form.derived}
                         setDerived={(derived) => setForm({ ...form, derived })}
                         existing={new Set(Object.keys(groups[form.gid]?.derived ?? {}))}
                         dirty={dirty} onRename={rename("derived")} />
            <div className="form-actions">
              <button className="primary" onClick={() => dr.requestSave(done)}>Save</button>
              <button onClick={() => { setMode("view"); setForm(null); }}>Cancel</button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

`SheetTypesSection` follows the identical list/detail shape with a
`TypeForm = { tid, isNew, label, kind, groups: string[], fields, derived,
creation, advancement }`; write it in the same file mirroring
`GroupsSection` line for line, with these deltas:

- kind `<select>` over `SHEET_KINDS`;
- group membership: one labeled checkbox per `pack.sheets.groups` key
  (`<label>{groups[gid]?.label ?? gid}<input type="checkbox" …/></label>`),
  toggling appends/removes from `form.groups` (append order preserved);
- own fields + derived reuse `FieldRows`/`DerivedRows` with owner
  `{sheet_type: tid}` in the rename calls;
- `creation` sub-form: for each gid in `form.groups`, a budget input
  (`aria-label={`Budget ${gid}`}`, string kept verbatim — int or expression)
  plus one cost number-input per that group's field keys; empty budgets/costs
  omit the pool; assembled as
  `{pools: {gid: {budget, costs}}}` only when non-empty;
- `advancement` sub-form: pool `<select>` over the assembled resource field
  keys (group fields of `form.groups` + own fields, `type === "resource"`),
  cost rows of (field key `<select>` over assembled `number`/`dots` keys +
  expression input); omitted entirely when no pool chosen;
- save posts `api.putModuleSheetType(pack.id, form.tid, def, dryRun)` where
  `def` includes `creation`/`advancement` only when present; the type id
  itself renames via `api.renameModulePart(pack.id, "sheet_type",
  {from: tid}, to)` from a `RenamePrompt` next to the (read-only) id in the
  form header; delete via `api.deleteModuleSheetType`;
- the sample computation: pass `dr.result?.sample?.[form.tid]?.derived` into
  `DerivedRows`' `sample` prop.

Mount both sections in `ModuleEditor`'s section switch
(`{section === "Groups" && <GroupsSection pack={pack} reload={reload} />}`,
same for `Sheet types`).

The same `confirmGate` pattern (dry-run → `ImpactConfirm` when impactful →
real call; `!ok` results land in the `gateError` banner) is mandatory for
every section's renames and deletes — `SheetTypesSection` here, and Tasks
16-17's checks/rules/content sections reuse it identically (export
`confirmGate`'s shape by copying it; it depends only on local state).

- [ ] **Step 4: Run tests + typecheck**

Run (from `frontend/`): `npx tsc -b && npx vitest run src/components/ModuleSchemaEditor.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ModuleSchemaEditor.tsx frontend/src/components/ModuleSchemaEditor.test.tsx frontend/src/components/ModuleEditor.tsx
git commit -m "feat(frontend): groups + sheet-type editors with rename affordances"
```

---

### Task 16: Checks + Rules sections

**Files:**
- Create: `frontend/src/components/ModuleRulesEditor.tsx`
- Modify: `frontend/src/components/ModuleEditor.tsx` (mount)
- Test: `frontend/src/components/ModuleRulesEditor.test.tsx` (new)

**Interfaces:**
- Consumes: the Task 14 harness, `RenamePrompt` (Task 15).
- Produces: `ChecksSection({ pack, reload })` and `RulesSection({ pack, reload })`, both `GroupsSection`-shaped list/details:
  - Check form: label input; roll template input; `requires` — labeled checkbox per group id; `rules` — labeled checkbox per `pack.rules[].id`; difficulty number input (optional → omitted when blank); outcomes rows (label + `when` expression inputs, `+ Add outcome`/Remove). Save → `api.putModuleCheck(pack.id, checkId, def, dryRun)`; id renames via `RenamePrompt` → `renameModulePart(pack.id, "check", {from}, to)`; delete → `api.deleteModuleCheck` — a `!r.ok` delete result renders its errors (the proposal-guard message) in the section banner.
  - A `_defaults` pseudo-row pinned at the top of the checks rail ("Defaults") opening a difficulty + outcomes form saved via `api.putModuleCheckDefaults`.
  - Rule form: body textarea; `always`/`on_roll` labeled checkboxes; `keys` — a comma-separated text input split/joined on save; `sheet_types` — labeled checkbox per sheet-type id. Save → `api.putModuleRule(pack.id, slug, flags, body, dryRun)`; slug renames via `RenamePrompt` → kind `"rule"`; delete → `api.deleteModuleRule`. Rule bodies aren't in `pack.rules` (frontmatter only) — the form fetches the body via `api.readModuleContent`? No: rules aren't content. Add `readModuleRule(mid, slug)` — **wait, no such route exists.** The pack's `rules` list has no bodies; add to Task 12's route list `GET /api/modules/{mid}/rules/{slug}` returning `store.modules.read_rule(mid, slug)` (404 on None) wrapped in `module_edit.locked()`, and a client fn `readModuleRule(mid, slug) → {meta, body}`. (Task 12's implementer: include this route; Task 16 consumes it.)

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/ModuleRulesEditor.test.tsx` (same mock block
as Task 14, plus `readModuleRule: vi.fn()`), with the `PACK` fixture from
Task 15 extended by one check and one rule:

```tsx
PACK.checks = { brawl: { label: "Brawl", roll: "1d20 + {might}", requires: ["attributes"] } };
PACK.rules = [{ id: "combat", keys: ["melee"], always: true, on_roll: false, sheet_types: [] }];

test("check row → view → Edit → save round-trip", async () => {
  (api.putModuleCheck as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ChecksSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Brawl"));
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("Roll"), { target: { value: "1d20" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleCheck).toHaveBeenCalledWith(
    "realm-system", "brawl",
    expect.objectContaining({ roll: "1d20", requires: ["attributes"] }), false));
});

test("blocked check delete shows the guard message", async () => {
  (api.deleteModuleCheck as any).mockResolvedValue(
    { ok: false, errors: ["check 'brawl' has a live roll proposal in campaign 'c1', scene 's1' — resolve or discard it first"], display_errors: [] });
  render(<ChecksSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Brawl"));
  fireEvent.click(screen.getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/live roll proposal/)).toBeInTheDocument();
});

test("rule form loads the body and saves flags + body", async () => {
  (api.readModuleRule as any).mockResolvedValue({ meta: {}, body: "Swing first." });
  (api.putModuleRule as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<RulesSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("combat"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(await screen.findByDisplayValue("Swing first.")).toBeInTheDocument();
  fireEvent.click(screen.getByLabelText("On roll"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleRule).toHaveBeenCalledWith(
    "realm-system", "combat",
    expect.objectContaining({ always: true, on_roll: true, keys: ["melee"] }),
    "Swing first.", false));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/ModuleRulesEditor.test.tsx`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

Backend prerequisite (tiny, belongs to this task if Task 12 didn't include
it): the `GET /api/modules/{mid}/rules/{slug}` route + `readModuleRule`
client fn per the Interfaces note, plus a route test asserting 200 with
`{meta, body}` and 404 for a ghost slug.

`ModuleRulesEditor.tsx`: mirror `GroupsSection`'s list/detail skeleton
exactly (rail of rows + `+ New check` / `+ New rule`, view mode with chips,
edit mode with the form, `useModuleDryRun` + `ErrorList` + `ImpactConfirm`
wiring, `RenamePrompt` on existing ids, Delete button whose `!ok` result
lands in a `deleteError` banner). Check form fields per Interfaces; save
assembles:

```tsx
const def: Record<string, unknown> = { label: form.label, roll: form.roll };
if (form.requires.length) def.requires = form.requires;
if (form.rules.length) def.rules = form.rules;
if (form.difficulty !== "") def.difficulty = parseInt(form.difficulty, 10);
if (form.outcomes.length) def.outcomes = form.outcomes;
```

Rule form seeds `body` from `api.readModuleRule(pack.id, slug)` on Edit
(async; render the textarea once loaded) and saves via:

```tsx
api.putModuleRule(pack.id, form.slug,
  { always: form.always, on_roll: form.onRoll,
    keys: form.keys.split(",").map((k) => k.trim()).filter(Boolean),
    sheet_types: form.sheetTypes },
  form.body, dryRun)
```

Mount both in `ModuleEditor` (`Checks`/`Rules` nav entries).

- [ ] **Step 4: Run tests + typecheck**

Run (from `frontend/`): `npx tsc -b && npx vitest run src/components/ModuleRulesEditor.test.tsx`
Expected: PASS. Also run the backend route test if this task added the rules
GET route: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k read_rule -v`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ModuleRulesEditor.tsx frontend/src/components/ModuleRulesEditor.test.tsx frontend/src/components/ModuleEditor.tsx backend/src/grimoire/routes.py backend/tests/test_routes.py frontend/src/api/client.ts
git commit -m "feat(frontend): checks + rules sections with proposal-guard surfacing"
```

---

### Task 17: Content section

**Files:**
- Create: `frontend/src/components/ModuleContentEditor.tsx`
- Modify: `frontend/src/components/ModuleEditor.tsx` (mount)
- Test: `frontend/src/components/ModuleContentEditor.test.tsx` (new)

**Interfaces:**
- Consumes: harness + `RenamePrompt`; `api.readModuleContent` (existing) for the body; `SheetWidgets`' field editors are **not** reused here (they need a full sheet context) — the stat block uses plain inputs driven by the chosen sheet type's assembled fields.
- Produces: `ContentSection({ pack, reload })` — rail groups `pack.content` rows by kind with a kind `<select>` on the `+ New content` form (`modules.CONTENT_KINDS` mirror: `locations|lore|items|groups|creatures`). View mode: name + markdown body (react-markdown, like `EntityEditor`'s content preview) + stat chips. Edit form: name, keys, body textarea, and an optional stat block — a sheet-type `<select>` over the pack's types whose `kind` matches, and per assembled field a typed input (number inputs for `number`/`dots`/`track`, current+max pair for `resource`, text input, one-per-line textarea for `list`, comma-separated text input for `ref` values); "No stat block" option clears it. Save → `api.putModuleContent(pack.id, kind, id, {name, body, keys, fields: {}, sheet}, dryRun)` with `sheet: {sheet_type, fields}` or `null`. Rename via kind `"content"` + `{from, kind}` address; delete via `api.deleteModuleContent` (impact confirm fires on dangling refs).

- [ ] **Step 1: Write the failing tests**

```tsx
PACK.content = [{ kind: "items", id: "sunblade", name: "Sunblade", sheet_type: "relic" }];
PACK.sheets.sheet_types.relic = { label: "Relic", kind: "items", groups: [],
  fields: [{ key: "power", type: "dots", max: 5 }] };

test("content row loads the body read-only, Edit reveals the form", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "sunblade", name: "Sunblade", body: "A blade of dawn.",
    keys: "sunblade", sheet_type: "relic", fields: { power: 3 } });
  render(<ContentSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Sunblade"));
  expect(await screen.findByText("A blade of dawn.")).toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit" }));
  expect(await screen.findByDisplayValue("A blade of dawn.")).toBeInTheDocument();
  expect(screen.getByLabelText("power")).toHaveValue(3);
});

test("saving posts body + stat block", async () => {
  (api.readModuleContent as any).mockResolvedValue({
    kind: "items", id: "sunblade", name: "Sunblade", body: "b", keys: "",
    sheet_type: "relic", fields: { power: 3 } });
  (api.putModuleContent as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ContentSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByText("Sunblade"));
  fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
  fireEvent.change(screen.getByLabelText("power"), { target: { value: "4" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleContent).toHaveBeenCalledWith(
    "realm-system", "items", "sunblade",
    expect.objectContaining({ sheet: { sheet_type: "relic", fields: { power: 4 } } }),
    false));
});

test("+ New content offers the kind select and posts a fresh entry", async () => {
  (api.putModuleContent as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ContentSection pack={PACK} reload={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", { name: "+ New content" }));
  fireEvent.change(screen.getByLabelText("Content id"), { target: { value: "moonshard" } });
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "lore" } });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Moonshard" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleContent).toHaveBeenCalledWith(
    "realm-system", "lore", "moonshard",
    expect.objectContaining({ name: "Moonshard", sheet: null }), false));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/ModuleContentEditor.test.tsx`
Expected: FAIL.

- [ ] **Step 3: Implement**

Mirror the `GroupsSection` skeleton; the stat-block sub-form:

```tsx
function StatBlock({ pack, sheetType, setSheetType, fields, setFields, kind }: {
  pack: ModuleDetail; sheetType: string | null;
  setSheetType: (t: string | null) => void;
  fields: Record<string, unknown>; setFields: (f: Record<string, unknown>) => void;
  kind: string;
}) {
  const types = Object.entries(pack.sheets.sheet_types)
    .filter(([, st]) => st.kind === kind);
  const assembled: ModuleField[] = sheetType
    ? [...(pack.sheets.sheet_types[sheetType]?.groups ?? [])
         .flatMap((g) => pack.sheets.groups[g]?.fields ?? []),
       ...(pack.sheets.sheet_types[sheetType]?.fields ?? [])]
    : [];
  return (
    <div className="side-section">
      <h4>Stat block</h4>
      <select aria-label="Sheet type" value={sheetType ?? ""}
              onChange={(e) => setSheetType(e.target.value || null)}>
        <option value="">No stat block</option>
        {types.map(([tid, st]) => <option key={tid} value={tid}>{st.label}</option>)}
      </select>
      {assembled.map((f) => {
        const v = fields[f.key];
        if (["number", "dots", "track"].includes(f.type)) {
          return (
            <label key={f.key}>{f.key}
              <input type="number" aria-label={f.key}
                     value={typeof v === "number" ? v : 0}
                     onChange={(e) => setFields({ ...fields, [f.key]: parseInt(e.target.value || "0", 10) })} />
            </label>
          );
        }
        if (f.type === "resource") {
          const r = (v ?? { current: f.max ?? 0, max: f.max ?? 0 }) as { current: number; max: number };
          return (
            <label key={f.key}>{f.key}
              <input type="number" aria-label={`${f.key} current`} value={r.current}
                     onChange={(e) => setFields({ ...fields, [f.key]: { ...r, current: parseInt(e.target.value || "0", 10) } })} />
              <input type="number" aria-label={`${f.key} max`} value={r.max}
                     onChange={(e) => setFields({ ...fields, [f.key]: { ...r, max: parseInt(e.target.value || "0", 10) } })} />
            </label>
          );
        }
        if (f.type === "list" || f.type === "ref") {
          return (
            <label key={f.key}>{f.key}
              <input aria-label={f.key}
                     value={Array.isArray(v) ? (v as string[]).join(", ") : ""}
                     onChange={(e) => setFields({ ...fields, [f.key]:
                       e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })} />
            </label>
          );
        }
        return (
          <label key={f.key}>{f.key}
            <input aria-label={f.key} value={typeof v === "string" ? v : ""}
                   onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })} />
          </label>
        );
      })}
    </div>
  );
}
```

Only fields the user actually sets go into the saved `sheet.fields`
(initialize `fields` from the loaded entry; don't inject defaults for
untouched keys — the backend validates whatever is sent). View mode renders
the body via `<Markdown remarkPlugins={[remarkGfm]}>` (same imports as
`EntityEditor.tsx`).

**Frontmatter metadata must round-trip** (codex plan review round 2: the
backend writer reconstructs frontmatter from the submitted `fields`, so
always sending `fields: {}` silently deletes an imported entry's custom
metadata on any save). `readModuleContent` already returns extra frontmatter
keys as top-level string properties — seed a `meta: Record<string, string>`
form state from every returned key not in
`("kind","id","name","body","keys","sheet_type","fields")`, render it as
editable key/value rows (a "Metadata" `.side-section`, `+ Add` / Remove),
and submit it as the PUT body's `fields`. Add a test: load an entry whose
mock includes `rarity: "legendary"`, change only the body, Save — the PUT
body's `fields` still carries `{ rarity: "legendary" }`.

- [ ] **Step 4: Run tests + typecheck**

Run (from `frontend/`): `npx tsc -b && npx vitest run src/components/ModuleContentEditor.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ModuleContentEditor.tsx frontend/src/components/ModuleContentEditor.test.tsx frontend/src/components/ModuleEditor.tsx
git commit -m "feat(frontend): module content section with stat-block editing"
```

---

### Task 18: Layout + Theme sections with live preview

**Files:**
- Create: `frontend/src/components/ModuleDisplayEditor.tsx`
- Modify: `frontend/src/components/ModuleEditor.tsx` (mount)
- Test: `frontend/src/components/ModuleDisplayEditor.test.tsx` (new)

**Interfaces:**
- Consumes: `SheetLayout` + its exports (read `frontend/src/components/SheetLayout.tsx` first: it exports the renderer plus `assembledDefs`/`defaultLayout` helpers and `themeStyle(theme)`), `sheets.default_fields` equivalence client-side — the preview builds a sample sheet from schema defaults (0 for number/dots/track, `{current: max, max}` for resource, "" / [] otherwise).
- Produces: `LayoutSection({ pack, reload })` — a JSON `<textarea>` seeded with `JSON.stringify(rawLayout, null, 2)` where rawLayout is fetched from... **the pack's `layout` is the *expanded* tree (fragments spliced server-side, Phase 6), not the authored file.** The editor must edit the authored file: add `pack["layout_raw"]`? No — keep it simple and honest: `load_pack_at`/`load_pack` already read `layout.json`; expose the raw file via the existing pack dict as `pack["layout_source"]` (the parsed-but-unspliced JSON, `{}` when absent). That's a small `modules.py` addition this task makes (with a one-line backend test): in `load_pack_at`, `pack["layout_source"] = _read raw layout.json dict or {}` (reuse `module_display._read_json` semantics — a malformed file yields `{}`; its parse error is already in `display_errors`). `ModuleDetail` gains `layout_source?: Record<string, unknown>`.
  - Beside the textarea: a sheet-type `<select>` and the live preview — `SheetLayout` rendered with the **expanded** tree from the latest dry-run… the dry-run result doesn't carry the pack. Simplest correct loop: on JSON change (debounced), call `api.putModuleLayout(pack.id, parsed, /*dryRun*/ true)` for validation display, and *separately* render the preview from the parsed JSON's `sheet_types[selected]` tree with `use` nodes resolved client-side against `parsed.fragments` (a 15-line recursive splice — cycle-guard with a visited set, depth cap 32). Unparseable JSON shows "invalid JSON" and keeps the last good preview.
  - Save posts the parsed object (Save disabled while unparseable).
  - `ThemeSection({ pack, reload })` — form controls: color inputs (`type="color"`) for bg/ink/muted/accent/rule (bg+ink presented as a pair with one "use custom colors" toggle clearing both), font selects (`display`/`body` over `display|body|mono|serif|sans`), dots select (`circle|square|diamond`), corners select (`sharp|rounded`); the same preview panel wrapped in a div carrying `themeStyle(form)` + `data-dots`/`data-corners`. Save → `api.putModuleTheme`.

- [ ] **Step 1: Write the failing tests**

```tsx
PACK.layout_source = { sheet_types: { warden: { group: "attributes" } } };

test("layout textarea seeds from layout_source and previews the selected type", async () => {
  (api.putModuleLayout as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<LayoutSection pack={PACK} reload={vi.fn()} />);
  const ta = screen.getByLabelText("Layout JSON") as HTMLTextAreaElement;
  expect(ta.value).toContain('"warden"');
  // preview renders the group's fields via SheetLayout
  expect(await screen.findByText("Strength")).toBeInTheDocument();
});

test("invalid JSON disables Save and shows a hint", () => {
  render(<LayoutSection pack={PACK} reload={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Layout JSON"), { target: { value: "{nope" } });
  expect(screen.getByText(/invalid JSON/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
});

test("dry-run display errors render as hints", async () => {
  (api.putModuleLayout as any).mockResolvedValue({ ok: true, errors: [],
    display_errors: [{ source: "layout", sheet_type: "warden", message: "warden: unknown field 'ghost'" }] });
  render(<LayoutSection pack={PACK} reload={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Layout JSON"),
    { target: { value: JSON.stringify({ sheet_types: { warden: { fields: ["ghost"] } } }) } });
  await vi.advanceTimersByTimeAsync(600);
  expect(await screen.findByText(/unknown field 'ghost'/)).toBeInTheDocument();
});

test("theme controls drive the preview vars and save the token object", async () => {
  (api.putModuleTheme as any).mockResolvedValue({ ok: true, errors: [], display_errors: [] });
  render(<ThemeSection pack={PACK} reload={vi.fn()} />);
  fireEvent.change(screen.getByLabelText("Dots"), { target: { value: "diamond" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(api.putModuleTheme).toHaveBeenCalledWith(
    "realm-system", expect.objectContaining({ dots: "diamond" }), false));
});
```

Backend addition test (in `backend/tests/test_modules_store.py`):

```python
def test_layout_source_exposed(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    mid = modules.create_module("Realm System")
    (modules.user_dir() / mid / "layout.json").write_text(
        '{"fragments": {"f": {"fields": ["x"]}}, "sheet_types": {}}', encoding="utf-8")
    pack = modules.load_pack(mid)
    assert pack["layout_source"]["fragments"] == {"f": {"fields": ["x"]}}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/components/ModuleDisplayEditor.test.tsx` and the
backend test above.
Expected: FAIL.

- [ ] **Step 3: Implement**

`modules.py` (`load_pack_at`, next to the `module_display.load_display`
call):

```python
    layout_source: dict = {}
    lp = root / "layout.json"
    if lp.exists():
        try:
            raw = json.loads(lp.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            raw = {}
        if isinstance(raw, dict):
            layout_source = raw
```

and `"layout_source": layout_source,` in the pack dict. Client type gains
`layout_source?: Record<string, unknown>`.

`ModuleDisplayEditor.tsx`: client-side fragment splice:

```tsx
function splice(node: any, fragments: Record<string, any>, seen: string[] = []): any {
  if (!node || typeof node !== "object") return node;
  if (typeof node.use === "string") {
    if (seen.includes(node.use) || seen.length > 32) return {};
    return splice(fragments[node.use] ?? {}, fragments, [...seen, node.use]);
  }
  const out: any = { ...node };
  for (const arr of ["row", "column"] as const) {
    if (Array.isArray(out[arr])) out[arr] = out[arr].map((k: any) => splice(k, fragments, seen));
  }
  return out;
}
```

`LayoutSection` holds `text` state (seeded from `layout_source`), parses on
change (`parsed | null`), debounces `api.putModuleLayout(pack.id, parsed,
true)` into a `ModuleEditResult` for `ErrorList`, renders the sheet-type
select + `SheetLayout` fed with the spliced tree and a sample sheet:

```tsx
function sampleSheet(pack: ModuleDetail, tid: string) {
  const st = pack.sheets.sheet_types[tid];
  const fields: Record<string, unknown> = {};
  const defs = [...(st?.groups ?? []).flatMap((g) => pack.sheets.groups[g]?.fields ?? []),
                ...(st?.fields ?? [])];
  for (const f of defs) {
    if (["number", "dots", "track"].includes(f.type)) fields[f.key] = f.default ?? 0;
    else if (f.type === "resource") fields[f.key] = { current: f.max ?? 0, max: f.max ?? 0 };
    else if (f.type === "list" || f.type === "ref") fields[f.key] = [];
    else fields[f.key] = "";
  }
  return { sheet_type: tid, fields, derived: {}, errors: [], gen: null };
}
```

**Before writing the render calls, read `SheetLayout.tsx`'s actual prop
signature** and match it (it takes the module/sheets def, the tree, the
sheet, and a mode — copy a call site from `SheetEditor.tsx`). `ThemeSection`
is a flat form + the same preview inside
`<div style={themeStyle(form)} data-dots={form.dots} data-corners={form.corners}>`
(import `themeStyle` from where `SheetEditor` imports it). Mount both
sections in `ModuleEditor`.

- [ ] **Step 4: Run tests + typecheck + backend**

Run (from `frontend/`): `npx tsc -b && npx vitest run`
Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ModuleDisplayEditor.tsx frontend/src/components/ModuleDisplayEditor.test.tsx frontend/src/components/ModuleEditor.tsx frontend/src/api/client.ts backend/src/grimoire/store/modules.py backend/tests/test_modules_store.py
git commit -m "feat(frontend): layout JSON editor with live preview + theme form"
```

---

### Task 19: Skill note, full verification, end-state walkthrough

**Files:**
- Modify: `.claude/skills/create-mechanics-module/SKILL.md`
- Test: full suites + manual walkthrough via the `verify` skill

**Interfaces:** none new.

- [ ] **Step 1: Update the authoring skill**

Add near the top of `.claude/skills/create-mechanics-module/SKILL.md` (where
it says the in-app authoring UI doesn't exist yet — replace that sentence):

```markdown
The in-app authoring UI (mechanics Phase 8) now exists: the Modules page can
edit user-library modules directly (builtins: Duplicate first). This skill
remains the conversational authoring path; both edit the same pack files,
and this skill's validate-after-each-step flow is unchanged.
```

- [ ] **Step 2: Run both full suites**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Run (from `frontend/`): `npx tsc -b && npx vitest run`
Expected: PASS, zero regressions.

- [ ] **Step 3: End-state walkthrough (manual, via the `verify` skill)**

Launch the app against an isolated store (`verify` skill) and, through the
UI alone: create a module; fill the manifest; add a group with fields + a
derived expression (watch the sample value update); add a sheet type
composing it with creation/advancement blocks; add a check, a rules doc, a
statted content entry; author a layout and theme watching the live preview;
bind the module to a test campaign, sheet a character, then rename a field
and confirm the sheet migrated (open it — new key, old value); delete a
field and confirm the sheet flags invalid; export the module, re-import it
(deduped id); duplicate `d20-basic` and edit the copy; confirm `d20-basic`
itself offers no Edit.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/create-mechanics-module/SKILL.md
git commit -m "docs(skill): create-mechanics-module notes the Phase 8 in-app editor"
```

---

## Execution notes

- Tasks 1→12 are backend and strictly ordered (each consumes the previous).
  Tasks 13→18 are frontend and ordered among themselves; the frontend chain
  only needs Task 12's routes to exist. Task 19 is last.
- After the final task, per CLAUDE.md: run `/codex:review` against the diff,
  then the final `/codex:adversarial-review` against the diff *and* the spec
  (does the implementation actually implement the spec?), before
  `finishing-a-development-branch`.
