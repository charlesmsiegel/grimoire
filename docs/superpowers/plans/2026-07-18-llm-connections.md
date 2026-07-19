# Unified LLM Connections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace grimoire's flat `provider`/`openrouter_key`/`model`/`claude_model` config fields with a swappable list of named LLM connections (OpenRouter, Claude, and arbitrary OpenAI-compatible endpoints), each owning its own credentials and cached model list.

**Architecture:** A new `store/llm_connections.py` CRUD module (one frontmatter file per connection, plus a JSON sidecar for cached models) replaces the provider fields in `config.py`, migrating existing installs once. A new `openai_compatible.py` provider client joins `openrouter.py`/`claude_agent.py`; `llm.py`'s `LLMClient` dispatches on a connection's `kind` instead of `provider`. `routes.py` gains `/llm-connections` CRUD + a `/models/refresh` proxy, and every existing LLM-generation call site is rewired from `cfg = store.read_config()` to `conn = _require_connection()`. The frontend gets a new "Connections" page (the standard list/detail editor pattern) and `ConfigView`/`App`/`CampaignView` shrink to consuming `config.ready`/`config.active_connection` instead of assuming OpenRouter.

**Tech Stack:** FastAPI + pytest (backend), React + Vite + vitest (frontend), httpx for outbound LLM calls.

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)` + `importlib.reload(store)`.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Run frontend tests from `frontend/`: `npx vitest run` (not `npx --prefix frontend vitest run` — that skips `frontend/vitest.config.ts` and disables `globals`).
- Type-check frontend from `frontend/`: `npx tsc -b`.
- Never commit anything under `~/.grimoire` / `store.home()`. Use only placeholder names (Seraphine, Mara, Winifred, Realm, Saltmarch) in any fixtures.
- Frontend record-list pages follow the list/detail editor pattern (`.editor` / `.editor-list` / `.editor-body`, view/edit modes, Edit button in `.form-actions`) per `CLAUDE.md` — `StyleGuideEditor.tsx` is the closest existing precedent for the new `ConnectionEditor.tsx`.
- The full design and its rationale (including 5 rounds of Codex adversarial review) live in `docs/superpowers/specs/2026-07-18-llm-connections-design.md` — consult it for the "why" behind anything that looks surprising here (the `rev` token, the migration marker, the sidecar hygiene rules).

---

### Task 1: Backend store — `llm_connections.py`, config narrowing, migration

**Files:**
- Create: `backend/src/grimoire/store/llm_connections.py`
- Modify: `backend/src/grimoire/store/config.py` (narrow `_CONFIG_KEYS`/defaults)
- Modify: `backend/src/grimoire/store/__init__.py` (register the new module)
- Create: `backend/tests/test_llm_connections_store.py`
- Modify: `backend/tests/test_config_store.py` (three tests reference removed fields — see Step 6)
- Modify: `backend/tests/test_data_dir.py` (one test reads a removed field — see Step 7)

**Interfaces:**
- Produces (used by Tasks 3, 4, 5): `store.llm_connections.ConnectionNotFound`, `list_connections() -> list[dict]`, `read_connection(id: str) -> dict` (raises `ConnectionNotFound`), `read_connection_raw(id: str) -> dict` (raises `ConnectionNotFound`), `create_connection(kind: str, name: str, **fields) -> str`, `update_connection(id: str, **fields) -> None` (raises `ConnectionNotFound`), `delete_connection(id: str) -> None` (raises `ConnectionNotFound`), `get_active() -> dict | None`, `cached_models(id: str) -> dict`, `set_cached_models(id: str, models: list[dict], rev: str) -> None`, `ensure_migrated() -> None`.
- Each connection dict has keys: `id, kind, name, base_url, api_key, model, post_process, rev` (raw) or the same minus `api_key` plus `key_set: bool` (masked, from `list_connections`/`read_connection`). `read_connection` additionally merges in `models: list[dict]` and `fetched_at: str` from the sidecar.
- Consumes: `store.config` (`_config_path`, `DEFAULT_MODEL`, `DEFAULT_CLAUDE_MODEL`, `read_config`, `write_config`), `store.frontmatter` (`dump_frontmatter`, `parse_frontmatter`), `store.paths` (`home`, `slugify`, `uniquify`, `now_iso`).

- [ ] **Step 1: Write the failing store tests**

Create `backend/tests/test_llm_connections_store.py`:

```python
import importlib
import json

import grimoire.store as store


def reload_with_home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    return store


# ---- migration ----

def test_zero_config_seeds_both_connections_openrouter_active(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    conns = {c["id"]: c for c in s.llm_connections.list_connections()}
    assert set(conns) == {"openrouter", "claude"}
    assert conns["openrouter"]["kind"] == "openrouter"
    assert conns["claude"]["kind"] == "claude"
    assert s.read_config()["active_connection_id"] == "openrouter"


def test_migrates_legacy_config_fields(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    (tmp_path / "config.md").write_text(
        "---\n"
        "openrouter_key: 'sk-or-legacy'\n"
        "model: anthropic/claude-x\n"
        "provider: claude\n"
        "claude_model: sonnet\n"
        "---\n\n",
        encoding="utf-8",
    )
    conns = {c["id"]: c for c in s.llm_connections.list_connections()}
    assert conns["openrouter"]["key_set"] is True
    openrouter = s.llm_connections.read_connection_raw("openrouter")
    assert openrouter["api_key"] == "sk-or-legacy"
    assert openrouter["model"] == "anthropic/claude-x"
    claude = s.llm_connections.read_connection_raw("claude")
    assert claude["model"] == "sonnet"
    assert s.read_config()["active_connection_id"] == "claude"


def test_migration_is_idempotent_even_after_deleting_everything(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.llm_connections.list_connections()  # triggers migration
    s.llm_connections.delete_connection("openrouter")
    s.llm_connections.delete_connection("claude")
    assert s.llm_connections.list_connections() == []
    s.llm_connections.ensure_migrated()  # must NOT reseed
    assert s.llm_connections.list_connections() == []


def test_crash_recovery_resumes_a_partial_migration(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    home = tmp_path / "llm_connections"
    home.mkdir(parents=True)
    (home / "openrouter.md").write_text(
        "---\nkind: openrouter\nname: OpenRouter\napi_key: 'sk-or-x'\nmodel: m\nbase_url: ''\npost_process: none\nrev: 'r1'\n---\n\n",
        encoding="utf-8",
    )
    # no claude.md, no .migrated marker: simulates a crash between the two seeds
    s.llm_connections.ensure_migrated()
    conns = {c["id"]: c for c in s.llm_connections.list_connections()}
    assert set(conns) == {"openrouter", "claude"}
    # the pre-existing openrouter seed must not be clobbered/duplicated
    assert s.llm_connections.read_connection_raw("openrouter")["api_key"] == "sk-or-x"


def test_corrupt_seed_file_is_treated_as_unseeded(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    (tmp_path / "config.md").write_text(
        "---\nopenrouter_key: 'sk-or-fresh'\n---\n\n", encoding="utf-8")
    home = tmp_path / "llm_connections"
    home.mkdir(parents=True)
    (home / "openrouter.md").write_text("not frontmatter at all", encoding="utf-8")
    s.llm_connections.ensure_migrated()
    assert s.llm_connections.read_connection_raw("openrouter")["api_key"] == "sk-or-fresh"


def test_preemptive_create_connection_does_not_block_migration(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    (tmp_path / "config.md").write_text(
        "---\nopenrouter_key: 'sk-or-legacy'\n---\n\n", encoding="utf-8")
    s.llm_connections.create_connection("openai_compatible", "z.ai GLM", base_url="https://api.z.ai")
    conns = {c["id"]: c for c in s.llm_connections.list_connections()}
    assert "openrouter" in conns and "claude" in conns
    assert s.llm_connections.read_connection_raw("openrouter")["api_key"] == "sk-or-legacy"


# ---- CRUD ----

def test_create_read_update_delete_openai_compatible(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "z.ai GLM", base_url="https://api.z.ai/v4",
        api_key="sk-z", model="glm-4.6", post_process="strict")
    raw = s.llm_connections.read_connection_raw(cid)
    assert raw["kind"] == "openai_compatible"
    assert raw["base_url"] == "https://api.z.ai/v4"
    assert raw["post_process"] == "strict"
    masked = s.llm_connections.read_connection(cid)
    assert "api_key" not in masked and masked["key_set"] is True
    s.llm_connections.update_connection(cid, name="z.ai GLM (renamed)")
    assert s.llm_connections.read_connection_raw(cid)["name"] == "z.ai GLM (renamed)"
    s.llm_connections.delete_connection(cid)
    import pytest
    with pytest.raises(s.llm_connections.ConnectionNotFound):
        s.llm_connections.read_connection_raw(cid)


def test_multiple_connections_of_the_same_kind_are_allowed(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    a = s.llm_connections.create_connection("openai_compatible", "Endpoint A", base_url="https://a")
    b = s.llm_connections.create_connection("openai_compatible", "Endpoint B", base_url="https://b")
    assert a != b
    assert {c["id"] for c in s.llm_connections.list_connections()} >= {a, b, "openrouter", "claude"}


def test_key_clears_when_base_url_changes_without_a_new_key(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Endpoint", base_url="https://old.example.com", api_key="sk-old")
    s.llm_connections.update_connection(cid, base_url="https://new.example.com")
    assert s.llm_connections.read_connection_raw(cid)["api_key"] == ""


def test_key_is_kept_when_base_url_and_key_change_together(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Endpoint", base_url="https://old.example.com", api_key="sk-old")
    s.llm_connections.update_connection(cid, base_url="https://new.example.com", api_key="sk-new")
    assert s.llm_connections.read_connection_raw(cid)["api_key"] == "sk-new"


def test_unrelated_field_update_leaves_the_key_untouched(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Endpoint", base_url="https://x", api_key="sk-x")
    s.llm_connections.update_connection(cid, name="Renamed")
    assert s.llm_connections.read_connection_raw(cid)["api_key"] == "sk-x"


def test_explicit_empty_api_key_is_treated_as_omitted_when_base_url_unchanged(monkeypatch, tmp_path):
    # A caller that always serializes api_key="" (rather than omitting the
    # field) must not erase a working credential on an unrelated update.
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Endpoint", base_url="https://x", api_key="sk-x")
    s.llm_connections.update_connection(cid, name="Renamed", api_key="")
    assert s.llm_connections.read_connection_raw(cid)["api_key"] == "sk-x"


# ---- rev stamping ----

def test_rev_changes_on_create_and_update(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    rev1 = s.llm_connections.read_connection_raw(cid)["rev"]
    s.llm_connections.update_connection(cid, name="Renamed")
    rev2 = s.llm_connections.read_connection_raw(cid)["rev"]
    assert rev1 != rev2


def test_delete_then_recreate_same_name_gets_a_different_rev(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid1 = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    rev1 = s.llm_connections.read_connection_raw(cid1)["rev"]
    s.llm_connections.delete_connection(cid1)
    cid2 = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://y")
    assert s.llm_connections.read_connection_raw(cid2)["rev"] != rev1


# ---- sidecar / cached models ----

def test_cached_models_empty_until_set(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    assert s.llm_connections.cached_models(cid) == {"models": [], "fetched_at": ""}


def test_set_cached_models_visible_when_rev_matches(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    rev = s.llm_connections.read_connection_raw(cid)["rev"]
    models = [{"id": "glm-4.6", "name": "GLM 4.6", "context": 128000, "prompt": None, "completion": None}]
    s.llm_connections.set_cached_models(cid, models, rev)
    result = s.llm_connections.cached_models(cid)
    assert result["models"] == models
    assert result["fetched_at"]


def test_cached_models_hidden_when_rev_is_stale(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    models = [{"id": "old-model", "name": "Old", "context": None, "prompt": None, "completion": None}]
    s.llm_connections.set_cached_models(cid, models, "a-stale-rev")  # connection's real rev differs
    assert s.llm_connections.cached_models(cid) == {"models": [], "fetched_at": ""}


def test_sidecar_cleared_when_base_url_changes(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://old")
    rev = s.llm_connections.read_connection_raw(cid)["rev"]
    s.llm_connections.set_cached_models(cid, [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}], rev)
    s.llm_connections.update_connection(cid, base_url="https://new")
    assert s.llm_connections.cached_models(cid) == {"models": [], "fetched_at": ""}


def test_delete_removes_the_sidecar_file(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    rev = s.llm_connections.read_connection_raw(cid)["rev"]
    s.llm_connections.set_cached_models(cid, [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}], rev)
    sidecar = tmp_path / "llm_connections" / f"{cid}.models.json"
    assert sidecar.exists()
    s.llm_connections.delete_connection(cid)
    assert not sidecar.exists()


def test_recreated_connection_never_inherits_an_orphaned_sidecar(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Orphan Source", base_url="https://x")
    rev = s.llm_connections.read_connection_raw(cid)["rev"]
    s.llm_connections.set_cached_models(cid, [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}], rev)
    sidecar_path = tmp_path / "llm_connections" / f"{cid}.models.json"
    (tmp_path / "llm_connections" / f"{cid}.md").unlink()  # delete the record but NOT the sidecar (simulates a crash)
    assert sidecar_path.exists()
    new_id = s.llm_connections.create_connection("openai_compatible", "Orphan Source", base_url="https://y")
    assert new_id == cid  # slugify collides with the freed id
    assert s.llm_connections.cached_models(new_id) == {"models": [], "fetched_at": ""}


def test_deleting_the_active_connection_leaves_nothing_active(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    s.write_config(active_connection_id=cid)
    s.llm_connections.delete_connection(cid)
    assert s.read_config()["active_connection_id"] == ""
    assert s.llm_connections.get_active() is None


def test_recreating_a_deleted_active_connection_does_not_silently_reactivate_it(monkeypatch, tmp_path):
    # The identity-confusion case Codex's review caught: deleting the active
    # connection, then creating a new one under the same name (reusing the
    # freed slug) must NOT make the new one active just because config.md
    # still happened to reference that id — it must require an explicit
    # Set-as-active, same as any other newly-created connection.
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection(
        "openai_compatible", "Reused Name", base_url="https://old", api_key="sk-old")
    s.write_config(active_connection_id=cid)
    s.llm_connections.delete_connection(cid)
    new_id = s.llm_connections.create_connection(
        "claude", "Reused Name", model="opus")  # even a different kind
    assert new_id == cid  # same freed slug
    assert s.read_config()["active_connection_id"] == ""
    assert s.llm_connections.get_active() is None


def test_delete_clears_active_id_even_if_file_removal_then_fails(monkeypatch, tmp_path):
    # Proves the ordering fix directly: force the file unlink itself to fail
    # AFTER active_connection_id has already been cleared, and confirm the
    # clear survives (rather than testing the trivial case of failing before
    # any write happens, which proves nothing about the ordering).
    s = reload_with_home(monkeypatch, tmp_path)
    cid = s.llm_connections.create_connection("openai_compatible", "Endpoint", base_url="https://x")
    s.write_config(active_connection_id=cid)

    from pathlib import Path
    original_unlink = Path.unlink
    state = {"failed_once": False}

    def flaky_unlink(self, *args, **kwargs):
        if self.name == f"{cid}.md" and not state["failed_once"]:
            state["failed_once"] = True
            raise OSError("simulated failure")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    try:
        s.llm_connections.delete_connection(cid)
    except OSError:
        pass
    # active_connection_id is already cleared, even though the file unlink
    # itself failed and the connection technically still exists on disk
    assert s.read_config()["active_connection_id"] == ""

    monkeypatch.setattr(Path, "unlink", original_unlink)
    s.llm_connections.delete_connection(cid)  # retry completes cleanly
    import pytest
    with pytest.raises(s.llm_connections.ConnectionNotFound):
        s.llm_connections.read_connection_raw(cid)


# ---- get_active ----

def test_get_active_resolves_the_configured_connection(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(active_connection_id="claude")
    active = s.llm_connections.get_active()
    assert active is not None and active["kind"] == "claude"


def test_get_active_none_when_unset(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(active_connection_id="")
    assert s.llm_connections.get_active() is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_llm_connections_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'grimoire.store.llm_connections'` (or `AttributeError: module 'grimoire.store' has no attribute 'llm_connections'`).

- [ ] **Step 3: Write `store/llm_connections.py`**

```python
"""Named LLM connections: openrouter / claude / openai_compatible profiles,
each remembering its own key+model so switching the active one never loses
credentials. Migrates the pre-connections flat config fields once. See
docs/superpowers/specs/2026-07-18-llm-connections-design.md for the full
rationale, especially around the `rev` token and the migration marker.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from . import config
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import home, now_iso, slugify, uniquify

_KINDS = ("openrouter", "claude", "openai_compatible")
_FIELDS = ("kind", "name", "base_url", "api_key", "model", "post_process")


class ConnectionNotFound(Exception):
    pass


def _dir() -> Path:
    return home() / "llm_connections"


def _path(id: str) -> Path:
    return _dir() / f"{id}.md"


def _sidecar_path(id: str) -> Path:
    return _dir() / f"{id}.models.json"


def _write_raw(id: str, **fields: str) -> None:
    """Unconditional write: stamps a fresh rev and clears any sidecar for
    this id, on every call (create AND update) — simpler than conditioning
    the sidecar clear on which field changed, and no less correct: the rev
    bump alone already makes any stale sidecar invisible on read (see
    cached_models below), so clearing it here is pure hygiene either way."""
    meta = {k: fields.get(k, "") for k in _FIELDS}
    meta["rev"] = secrets.token_hex(8)
    _dir().mkdir(parents=True, exist_ok=True)
    _sidecar_path(id).unlink(missing_ok=True)
    _path(id).write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def _read(id: str) -> dict | None:
    """None for missing, unreadable, or unrecognized-kind files — all three
    count as "not a valid seeded/created connection", used both by normal
    lookups and by migration's crash-recovery check."""
    p = _path(id)
    if not p.exists():
        return None
    try:
        meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    if meta.get("kind") not in _KINDS:
        return None
    return {"id": id, **{k: meta.get(k, "") for k in _FIELDS}, "rev": meta.get("rev", "")}


def _mask(conn: dict) -> dict:
    out = {k: v for k, v in conn.items() if k != "api_key"}
    out["key_set"] = bool(conn["api_key"])
    return out


def list_connections() -> list[dict]:
    ensure_migrated()
    out = []
    if _dir().exists():
        for p in sorted(_dir().glob("*.md")):
            conn = _read(p.stem)
            if conn is not None:
                out.append(_mask(conn))
    return out


def read_connection(id: str) -> dict:
    ensure_migrated()
    conn = _read(id)
    if conn is None:
        raise ConnectionNotFound(id)
    return {**_mask(conn), **cached_models(id)}


def read_connection_raw(id: str) -> dict:
    ensure_migrated()
    conn = _read(id)
    if conn is None:
        raise ConnectionNotFound(id)
    return conn


def create_connection(kind: str, name: str, **fields) -> str:
    ensure_migrated()

    def exists(c: str) -> bool:
        return _path(c).exists()

    id = uniquify(slugify(name), exists)
    _write_raw(id, kind=kind, name=name, **fields)
    return id


def update_connection(id: str, **fields) -> None:
    ensure_migrated()
    conn = _read(id)
    if conn is None:
        raise ConnectionNotFound(id)
    fields = {k: v for k, v in fields.items() if v is not None}
    base_url_changed = "base_url" in fields and fields["base_url"] != conn["base_url"]
    if base_url_changed:
        # A custom endpoint's base_url is user-editable (unlike OpenRouter's
        # fixed URL) — carrying the old key over to a newly-pointed host
        # would silently leak it, so repointing always drops the key unless
        # this same call also supplies a fresh one.
        fields.setdefault("api_key", "")
    elif not fields.get("api_key"):
        # "type to replace" convention: an omitted OR empty api_key means
        # "keep the stored one" whenever base_url isn't changing. Dropping
        # it from `fields` here (rather than filtering only None above) is
        # what makes that true — otherwise an explicit api_key="" from any
        # caller that always serializes the field would silently erase a
        # working credential on an unrelated update (e.g. a rename).
        fields.pop("api_key", None)
    merged = {**conn, **fields}
    _write_raw(id, **{k: merged[k] for k in _FIELDS})


def delete_connection(id: str) -> None:
    ensure_migrated()
    p = _path(id)
    if not p.exists():
        raise ConnectionNotFound(id)
    if config.read_config().get("active_connection_id") == id:
        # Clear this BEFORE unlinking the file, not after — otherwise a
        # failure between the two steps (disk error, process death) leaves
        # the file gone (its slug now reusable) while config.md still
        # references it, reproducing the exact dangling-reference bug this
        # exists to close, just via a partial-failure window instead of
        # never having the fix at all. With this ordering, every failure
        # window is retry-safe: fail here and nothing changed yet (clean
        # retry); fail during the unlink below and active_connection_id is
        # already correctly cleared even though the file still exists (a
        # retriable "delete didn't finish" state, not a dangling reference).
        config.write_config(active_connection_id="")
    p.unlink()
    _sidecar_path(id).unlink(missing_ok=True)


def get_active() -> dict | None:
    ensure_migrated()
    id = config.read_config().get("active_connection_id", "")
    if not id:
        return None
    return _read(id)


def cached_models(id: str) -> dict:
    """The sole read path for the model-list cache — gates on `rev` here,
    not at write time, so there's no check-then-act gap for a concurrent
    update/delete/recreate to land in (see the design spec's §5)."""
    empty = {"models": [], "fetched_at": ""}
    p = _sidecar_path(id)
    if not p.exists():
        return empty
    try:
        sidecar = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty
    conn = _read(id)
    if conn is None or sidecar.get("rev") != conn["rev"]:
        return empty
    return {"models": sidecar["models"], "fetched_at": sidecar["fetched_at"]}


def set_cached_models(id: str, models: list[dict], rev: str) -> None:
    """Writes unconditionally, tagged with the rev captured before the
    fetch that produced `models` — staleness is judged later, on read, by
    cached_models(), not here."""
    payload = {"models": models, "fetched_at": now_iso(), "rev": rev}
    _sidecar_path(id).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def ensure_migrated() -> None:
    _dir().mkdir(parents=True, exist_ok=True)
    marker = _dir() / ".migrated"
    if marker.exists():
        return
    # Read the pre-migration fields directly off the frontmatter file — NOT
    # via config.read_config(), whose narrowed key set no longer returns
    # them (see Task 1's config.py edit). A file that predates this change
    # still has them physically present; parse_frontmatter returns whatever
    # keys exist regardless of the "official" schema.
    path = config._config_path()
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ({}, "")
    if _read("openrouter") is None:
        _write_raw("openrouter", kind="openrouter", name="OpenRouter",
                    api_key=meta.get("openrouter_key", ""),
                    model=meta.get("model", config.DEFAULT_MODEL),
                    base_url="", post_process="none")
    if _read("claude") is None:
        _write_raw("claude", kind="claude", name="Claude",
                    model=meta.get("claude_model", config.DEFAULT_CLAUDE_MODEL),
                    base_url="", api_key="", post_process="none")
    if "active_connection_id" not in meta:
        # Presence in the raw pre-migration frontmatter, not truthiness via
        # config.read_config() — the two conflate under a truthiness check:
        # a key that was never written (migration hasn't decided yet, should
        # seed from the legacy `provider` field) reads identically to a key
        # explicitly written as "" (already decided "no active connection"
        # on purpose, must not be silently overwritten). A truthiness check
        # here would make ensure_migrated() re-seed "openrouter" every time
        # active_connection_id is deliberately cleared to "" (e.g.
        # delete_connection on the active connection, per this task's own
        # test_get_active_none_when_unset) — found by hand-tracing that test
        # against this function during implementation.
        active = "openrouter" if meta.get("provider", "openrouter") == "openrouter" else "claude"
        config.write_config(active_connection_id=active)
    marker.write_text("1", encoding="utf-8")
```

- [ ] **Step 4: Narrow `store/config.py`**

Read the current file first (`backend/src/grimoire/store/config.py`), then replace its full contents with:

```python
"""config.md read/write (frontmatter only)."""

from __future__ import annotations

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home

DEFAULT_MODEL = "anthropic/claude-opus-4.1"
DEFAULT_THEME = "codex"
DEFAULT_SCAN_DEPTH = "8"
DEFAULT_RECAP_DEPTH = "5"
DEFAULT_USER_LABEL = "You"
DEFAULT_ASSISTANT_LABEL = "Grimoire"
DEFAULT_CLAUDE_MODEL = "opus"
_CONFIG_KEYS = ("theme", "context_scan_depth", "system_prompt",
                "quote_color", "recap_depth", "user_label", "assistant_label",
                "default_style_id", "active_connection_id")


def _config_path():
    return home() / "config.md"


def read_config() -> dict[str, str]:
    ensure_home()
    path = _config_path()
    defaults = {"theme": DEFAULT_THEME,
                "context_scan_depth": DEFAULT_SCAN_DEPTH, "system_prompt": "", "quote_color": "off",
                "recap_depth": DEFAULT_RECAP_DEPTH,
                "user_label": DEFAULT_USER_LABEL, "assistant_label": DEFAULT_ASSISTANT_LABEL,
                "default_style_id": "", "active_connection_id": ""}
    if not path.exists():
        path.write_text(dump_frontmatter(defaults, ""), encoding="utf-8")
        return defaults
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {k: meta.get(k, default) for k, default in defaults.items()}


def write_config(**fields: str) -> dict[str, str]:
    # Merge onto the file's RAW frontmatter (not read_config()'s narrowed
    # reconstruction) so any key not in _CONFIG_KEYS — including the legacy
    # openrouter_key/model/provider/claude_model fields on a pre-migration
    # install — survives every write untouched. This is what makes the
    # design spec's "legacy fields stay physically present for recovery if
    # llm_connections/ is ever deleted" claim actually true: migration's own
    # first write (ensure_migrated's config.write_config(active_connection_id=...))
    # would otherwise silently erase them immediately.
    ensure_home()
    path = _config_path()
    raw, _ = parse_frontmatter(path.read_text(encoding="utf-8")) if path.exists() else ({}, "")
    for key, value in fields.items():
        if key in _CONFIG_KEYS and value is not None:
            raw[key] = value
    path.write_text(dump_frontmatter(raw, ""), encoding="utf-8")
    return read_config()
```

(`DEFAULT_MODEL`/`DEFAULT_CLAUDE_MODEL` stay — `llm_connections.ensure_migrated()` uses them as seed defaults. `DEFAULT_PROVIDER` is deleted; nothing reads it anymore.)

- [ ] **Step 5: Register the module in `store/__init__.py`**

In `backend/src/grimoire/store/__init__.py`, add `llm_connections` to the `from . import (...)` block (alphabetically, after `localize`):

```python
from . import (
    absorb, appearances, assets, audit, campaigns, cards, changes, characters, checks, chronicle,
    chub, context, dice, dossiers, entities, entity_schema, epub, export, fence, fetch, greetings, groupstate,
    image_subjects, llm_connections, localize, lorebook, migrations, module_edit, modules, overlay, pcs, playing,
    playstate, plot, proposals, relationships, rolls, scene_ids, scene_refs, scenes, sheets, styles, suggest,
    sync, tags, taglines, thumbs, worlds,
)
```

Add `from .llm_connections import ConnectionNotFound` near the other `NotFound` imports, and add `"llm_connections"` and `"ConnectionNotFound"` to `__all__`.

- [ ] **Step 6: Update `test_config_store.py`**

Read `backend/tests/test_config_store.py` in full first — three tests reference the removed `openrouter_key`/`model`/`provider`/`claude_model` fields, not just the two provider-specific ones:

1. Delete `test_provider_defaults` and `test_provider_roundtrip` entirely — that behavior is now owned by `test_llm_connections_store.py`.
2. `test_first_read_creates_defaults` currently asserts `cfg["openrouter_key"] == ""` and `cfg["model"]` (a truthy check) — remove both lines; keep `assert cfg["theme"] == "codex"` and the `config.md` existence check.
3. `test_write_merges_without_clearing` currently writes/asserts `openrouter_key`/`model` — replace its body to prove the same "one write doesn't clobber another field" property using fields that still exist:
```python
def test_write_merges_without_clearing(monkeypatch, tmp_path):
    s = reload_with_home(monkeypatch, tmp_path)
    s.write_config(user_label="Kestrel")
    s.write_config(theme="manuscript")  # must not wipe the label
    cfg = s.read_config()
    assert cfg["user_label"] == "Kestrel"
    assert cfg["theme"] == "manuscript"
```

- [ ] **Step 7: Fix `test_data_dir.py`'s config-follows-data-dir test**

Read `backend/tests/test_data_dir.py`. `test_config_follows_data_dir` (near the end of the file) writes/reads `model`, which no longer exists:
```python
def test_config_follows_data_dir(monkeypatch, tmp_path):
    isolate(monkeypatch, tmp_path)
    store.set_data_dir(str(tmp_path / "campaign-a"))
    store.write_config(active_connection_id="claude")

    store.set_data_dir(str(tmp_path / "campaign-b"))
    # A fresh store at the new location gets defaults, not campaign-a's value.
    assert store.read_config()["active_connection_id"] != "claude"
    assert (tmp_path / "campaign-b" / "config.md").exists()
```

- [ ] **Step 8: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_llm_connections_store.py backend/tests/test_config_store.py backend/tests/test_data_dir.py -v`
Expected: PASS, all tests.

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/store/llm_connections.py backend/src/grimoire/store/config.py backend/src/grimoire/store/__init__.py backend/tests/test_llm_connections_store.py backend/tests/test_config_store.py backend/tests/test_data_dir.py
git commit -m "feat(backend): add llm_connections store with migration from flat provider config"
```

---

### Task 2: Backend provider client — `openai_compatible.py`

**Files:**
- Create: `backend/src/grimoire/openai_compatible.py`
- Create: `backend/tests/test_openai_compatible.py`

**Interfaces:**
- Consumes: `grimoire.llm.LLMError` (base exception).
- Produces (used by Task 3): `OpenAICompatibleError(LLMError)`, `OpenAICompatibleClient` with `async stream(messages, model, key, base_url, strict=False) -> AsyncIterator[str]`, `async complete(messages, model, key, base_url, strict=False) -> str`, `async list_models(base_url, key) -> list[dict]` (each `{id, name, context: int|None, prompt: str|None, completion: str|None}`), `async aclose() -> None`. Also produces `_strict_messages(messages: list[dict]) -> list[dict]` (module-private, but imported directly by tests).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_openai_compatible.py`:

```python
import httpx
import pytest

from grimoire.llm import LLMError
from grimoire.openai_compatible import OpenAICompatibleClient, OpenAICompatibleError, _strict_messages

SSE_BODY = (
    'data: {"choices":[{"delta":{"content":"Hel"}}]}\n\n'
    'data: {"choices":[{"delta":{"content":"lo"}}]}\n\n'
    "data: [DONE]\n\n"
)


def make_client(handler):
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://custom.example.com")
    return OpenAICompatibleClient(http=http)


async def test_stream_yields_deltas():
    def handler(request):
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    chunks = [c async for c in client.stream(
        [{"role": "user", "content": "hi"}], "m", "sk-x", "https://custom.example.com/v1")]
    assert "".join(chunks) == "Hello"


async def test_key_omitted_from_headers_when_empty():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    [c async for c in client.stream([], "m", "", "https://custom.example.com/v1")]
    assert captured["auth"] is None


async def test_key_present_sends_bearer_header():
    captured = {}

    def handler(request):
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    [c async for c in client.stream([], "m", "sk-secret", "https://custom.example.com/v1")]
    assert captured["auth"] == "Bearer sk-secret"


async def test_missing_base_url_raises():
    client = OpenAICompatibleClient()
    with pytest.raises(OpenAICompatibleError) as exc:
        [c async for c in client.stream([], "m", "k", "")]
    assert exc.value.kind == "missing_key"


async def test_auth_error_normalized():
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    client = make_client(handler)
    with pytest.raises(OpenAICompatibleError) as exc:
        [c async for c in client.stream([], "m", "k", "https://custom.example.com/v1")]
    assert exc.value.kind == "auth"
    assert exc.value.detail == "bad key"
    assert isinstance(exc.value, LLMError)


async def test_list_models_parses_id_name_context_pricing():
    def handler(request):
        assert request.url.path == "/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "glm-4.6", "name": "GLM-4.6", "context_length": 128000,
             "pricing": {"prompt": "0.000002", "completion": "0.000006"}},
        ]})

    client = make_client(handler)
    models = await client.list_models("https://custom.example.com/v1", "sk-x")
    assert models == [{"id": "glm-4.6", "name": "GLM-4.6", "context": 128000,
                        "prompt": "0.000002", "completion": "0.000006"}]


async def test_list_models_missing_pricing_and_context_come_back_none():
    def handler(request):
        return httpx.Response(200, json={"data": [{"id": "local-model"}]})

    client = make_client(handler)
    models = await client.list_models("https://custom.example.com/v1", "")
    assert models == [{"id": "local-model", "name": "local-model",
                        "context": None, "prompt": None, "completion": None}]


# ---- _strict_messages ----

def test_strict_system_before_user_folds_into_it():
    out = _strict_messages([
        {"role": "system", "content": "Be terse."},
        {"role": "user", "content": "Hello"},
    ])
    assert out == [{"role": "user", "content": "Be terse.\n\nHello"}]


def test_strict_system_before_assistant_folds_into_the_preceding_user_turn():
    # A system turn sitting between an existing user turn and an assistant
    # turn must fold INTO that preceding user turn, not become a second,
    # separate, consecutive user message — two adjacent "user" entries would
    # violate the strict alternation this function exists to guarantee.
    # (Found during Task 2's implementation: an earlier draft of this test
    # asserted the non-alternating shape and "fixed" append() to match it —
    # backwards, since _strict_messages's whole purpose is preventing exactly
    # that shape from reaching a backend that rejects it.)
    out = _strict_messages([
        {"role": "user", "content": "Hi"},
        {"role": "system", "content": "Stay in character."},
        {"role": "assistant", "content": "Hello there."},
    ])
    assert out == [
        {"role": "user", "content": "Hi\n\nStay in character."},
        {"role": "assistant", "content": "Hello there."},
    ]


def test_strict_trailing_system_message_becomes_a_final_user_turn():
    out = _strict_messages([
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": "Hello."},
        {"role": "system", "content": "npc cards go here"},
    ])
    assert out[-1] == {"role": "user", "content": "npc cards go here"}


def test_strict_folding_induced_adjacent_same_role_runs_remerge():
    out = _strict_messages([
        {"role": "system", "content": "Sys A"},
        {"role": "system", "content": "Sys B"},
        {"role": "user", "content": "Hi"},
    ])
    assert out == [{"role": "user", "content": "Sys A\n\nSys B\n\nHi"}]


def test_strict_assistant_first_gets_a_defensive_placeholder_user_turn():
    out = _strict_messages([{"role": "assistant", "content": "Hello."}])
    assert out[0] == {"role": "user", "content": "(continue)"}
    assert out[1] == {"role": "assistant", "content": "Hello."}


def test_strict_empty_list_gets_a_placeholder():
    assert _strict_messages([]) == [{"role": "user", "content": "(continue)"}]


def test_strict_unrecognized_role_raises():
    with pytest.raises(OpenAICompatibleError) as exc:
        _strict_messages([{"role": "tool", "content": "x"}])
    assert exc.value.kind == "bad_response"


async def test_strict_mode_transforms_payload_before_sending():
    captured = {}

    def handler(request):
        captured["body"] = request.content
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    [c async for c in client.stream(
        [{"role": "system", "content": "Be terse."}, {"role": "user", "content": "Hi"}],
        "m", "k", "https://custom.example.com/v1", strict=True)]
    import json
    body = json.loads(captured["body"])
    assert body["messages"] == [{"role": "user", "content": "Be terse.\n\nHi"}]


async def test_no_tools_field_in_payload():
    captured = {}

    def handler(request):
        captured["body"] = request.content
        return httpx.Response(200, text=SSE_BODY)

    client = make_client(handler)
    [c async for c in client.stream([{"role": "user", "content": "hi"}], "m", "k",
                                     "https://custom.example.com/v1")]
    import json
    assert "tools" not in json.loads(captured["body"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_openai_compatible.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'grimoire.openai_compatible'`.

- [ ] **Step 3: Write `openai_compatible.py`**

```python
"""Chat-completions client for an arbitrary OpenAI-compatible endpoint —
same wire protocol/error-shape as openrouter.py, but base_url is
caller-supplied and the API key is optional. See
docs/superpowers/specs/2026-07-18-llm-connections-design.md.
"""

from __future__ import annotations

import json
import os
import ssl
from typing import AsyncIterator

import certifi
import httpx

from .llm import LLMError


class OpenAICompatibleError(LLMError):
    pass


def _status_kind(status: int) -> str:
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    return "bad_response"


def _extract_error(text: str) -> str:
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text.strip()
    err = obj.get("error", obj) if isinstance(obj, dict) else obj
    if isinstance(err, dict):
        return str(err.get("message") or err.get("detail") or err)
    return str(err)


def _strict_messages(messages: list[dict]) -> list[dict]:
    """Fold system messages into adjacent user turns and guarantee the result
    starts with role=user and alternates strictly — required by chat-completion
    backends (e.g. z.ai's GLM coding endpoint) that reject a system message
    mid-conversation or a non-user opening turn."""
    folded: list[dict] = []
    pending: list[str] = []

    def flush(extra: str = "") -> str:
        nonlocal pending
        parts = [*pending, extra] if extra else list(pending)
        pending = []
        return "\n\n".join(parts)

    def append(role: str, content: str) -> None:
        if folded and folded[-1]["role"] == role:
            folded[-1]["content"] += "\n\n" + content
        else:
            folded.append({"role": role, "content": content})

    for m in messages:
        if m["role"] == "system":
            pending.append(m["content"])
        elif m["role"] == "user":
            append("user", flush(m["content"]))
        elif m["role"] == "assistant":
            if pending:
                append("user", flush())
            append("assistant", m["content"])
        else:
            # grimoire's context.py only ever emits system/user/assistant
            # today, but silently folding an unrecognized role into
            # "assistant" would misattribute its content as model-authored.
            raise OpenAICompatibleError("bad_response", f"unsupported message role: {m['role']!r}")
    if pending:
        append("user", flush())
    if not folded or folded[0]["role"] != "user":
        folded.insert(0, {"role": "user", "content": "(continue)"})
    return folded


class OpenAICompatibleClient:
    def __init__(self, http: httpx.AsyncClient | None = None):
        self._http = http
        self._owns = http is None

    def _verify(self) -> ssl.SSLContext:
        cert = os.environ.get("SSL_CERT_FILE")
        cafile = cert if cert and os.path.exists(cert) else certifi.where()
        return ssl.create_default_context(cafile=cafile)

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(120.0, connect=30.0), verify=self._verify()
            )
        return self._http

    def _headers(self, key: str) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    async def stream(self, messages, model: str, key: str, base_url: str,
                      strict: bool = False) -> AsyncIterator[str]:
        if not base_url:
            raise OpenAICompatibleError("missing_key", "No base URL configured")
        payload_messages = _strict_messages(messages) if strict else messages
        url = base_url.rstrip("/") + "/chat/completions"
        payload = {"model": model, "messages": payload_messages, "stream": True}
        try:
            http = self._client()
            async with http.stream("POST", url, headers=self._headers(key), json=payload) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    raise OpenAICompatibleError(_status_kind(resp.status_code), _extract_error(resp.text))
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        return
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0]["delta"].get("content")
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except OpenAICompatibleError:
            raise
        except httpx.HTTPError as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        except Exception as exc:  # client/TLS setup and other unexpected failures
            raise OpenAICompatibleError("network", str(exc)) from exc

    async def complete(self, messages, model: str, key: str, base_url: str, strict: bool = False) -> str:
        return "".join([chunk async for chunk in self.stream(messages, model, key, base_url, strict)])

    async def list_models(self, base_url: str, key: str) -> list[dict]:
        if not base_url:
            raise OpenAICompatibleError("missing_key", "No base URL configured")
        url = base_url.rstrip("/") + "/models"
        try:
            http = self._client()
            resp = await http.get(url, headers=self._headers(key))
            if resp.status_code >= 400:
                raise OpenAICompatibleError(_status_kind(resp.status_code), _extract_error(resp.text))
            data = resp.json().get("data", [])
        except OpenAICompatibleError:
            raise
        except httpx.HTTPError as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        except Exception as exc:
            raise OpenAICompatibleError("network", str(exc)) from exc
        out = []
        for m in data:
            pricing = m.get("pricing") or {}
            out.append({
                "id": m["id"], "name": m.get("name") or m["id"],
                "context": m.get("context_length"),
                "prompt": pricing.get("prompt"), "completion": pricing.get("completion"),
            })
        return out

    async def aclose(self) -> None:
        if self._owns and self._http is not None:
            await self._http.aclose()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_openai_compatible.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/openai_compatible.py backend/tests/test_openai_compatible.py
git commit -m "feat(backend): add OpenAI-compatible provider client with strict-mode message folding"
```

---

### Task 3: `LLMClient` dispatch on connection `kind`

**Files:**
- Modify: `backend/src/grimoire/llm.py`
- Modify: `backend/tests/test_llm.py`

**Interfaces:**
- Consumes: `store.llm_connections`' connection dict shape (`kind, name, base_url, api_key, model, post_process, rev` — see Task 1), `openai_compatible.OpenAICompatibleClient` (Task 2).
- Produces (used by Task 5): `LLMClient(openrouter=None, claude=None, openai_compatible=None)`, `.stream(messages: list[dict], conn: dict)`, `.complete(messages, conn) -> str`, `.aclose()`. **Breaking change from today:** the second parameter is a resolved connection dict (`conn["kind"]`), not the flat config dict (`cfg["provider"]`) — every caller in `routes.py` must pass a connection (Task 5).

- [ ] **Step 1: Write the failing tests**

Read `backend/tests/test_llm.py` first, then replace its dispatch-related content (keep `test_openrouter_error_is_llm_error` and `test_llm_error_detail_defaults_to_kind` as-is) with:

```python
from grimoire.llm import LLMError
from grimoire.openrouter import OpenRouterError


def test_openrouter_error_is_llm_error():
    err = OpenRouterError("auth", "bad key")
    assert isinstance(err, LLMError)
    assert err.kind == "auth"
    assert err.detail == "bad key"


def test_llm_error_detail_defaults_to_kind():
    assert LLMError("network").detail == "network"


from grimoire.llm import LLMClient


class FakeProvider:
    def __init__(self, tag):
        self.tag = tag
        self.calls = []

    async def stream(self, messages, *args, **kwargs):
        self.calls.append((args, kwargs))
        yield self.tag


def _conn(kind, **fields):
    return {"kind": kind, "model": "m", "api_key": "k", "base_url": "", "post_process": "none", **fields}


async def test_dispatches_to_openrouter():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openrouter", model="or-model", api_key="sk-or-x")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["or"]
    assert op.calls == [(("or-model", "sk-or-x"), {})]
    assert cl.calls == [] and oc.calls == []


async def test_dispatches_to_claude():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("claude", model="opus")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["cl"]
    assert cl.calls == [(("opus",), {})]
    assert op.calls == [] and oc.calls == []


async def test_claude_missing_model_defaults_to_opus():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("claude", model="")
    [c async for c in client.stream([], conn)]
    assert cl.calls == [(("opus",), {})]


async def test_dispatches_to_openai_compatible_with_strict_flag():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", model="glm-4.6", api_key="sk-z",
                 base_url="https://api.z.ai/v4", post_process="strict")
    chunks = [c async for c in client.stream([], conn)]
    assert chunks == ["oc"]
    assert oc.calls == [(("glm-4.6", "sk-z", "https://api.z.ai/v4"), {"strict": True})]


async def test_openai_compatible_none_post_process_is_not_strict():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = _conn("openai_compatible", post_process="none")
    [c async for c in client.stream([], conn)]
    assert oc.calls[0][1] == {"strict": False}


async def test_missing_kind_defaults_to_openrouter():
    op, cl, oc = FakeProvider("or"), FakeProvider("cl"), FakeProvider("oc")
    client = LLMClient(openrouter=op, claude=cl, openai_compatible=oc)
    conn = {"model": "or-model", "api_key": "sk-or-x"}  # defensive: no kind key at all
    assert [c async for c in client.stream([], conn)] == ["or"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_llm.py -v`
Expected: FAIL — `test_dispatches_to_openrouter`/`test_dispatches_to_claude` fail on the `LLMClient(openrouter=op, claude=cl, openai_compatible=oc)` constructor call (unexpected keyword `openai_compatible`) and on the `_conn(...)`-shaped dict not matching the current `provider`/`openrouter_key`/`claude_model` keys `LLMClient.stream` still reads.

- [ ] **Step 3: Rewrite `llm.py`'s dispatch**

Read `backend/src/grimoire/llm.py` first (keep the `LLMError` class untouched), then replace the `LLMClient` class with:

```python
class LLMClient:
    """Dispatches each call to the resolved connection's kind."""

    def __init__(self, openrouter=None, claude=None, openai_compatible=None):
        # Imported here rather than at module top: openrouter.py imports
        # LLMError from this module, so top-level imports would be circular.
        from .claude_agent import ClaudeAgentClient
        from .openrouter import OpenRouterClient
        from .openai_compatible import OpenAICompatibleClient
        self._openrouter = openrouter if openrouter is not None else OpenRouterClient()
        self._claude = claude if claude is not None else ClaudeAgentClient()
        self._openai_compatible = (openai_compatible if openai_compatible is not None
                                    else OpenAICompatibleClient())

    def stream(self, messages: list[dict], conn: dict):
        kind = conn.get("kind", "openrouter")
        if kind == "claude":
            return self._claude.stream(messages, conn.get("model") or "opus")
        if kind == "openai_compatible":
            return self._openai_compatible.stream(
                messages, conn.get("model", ""), conn.get("api_key", ""),
                conn.get("base_url", ""), strict=conn.get("post_process") == "strict")
        return self._openrouter.stream(messages, conn["model"], conn.get("api_key", ""))

    async def complete(self, messages: list[dict], conn: dict) -> str:
        return "".join([chunk async for chunk in self.stream(messages, conn)])

    async def aclose(self) -> None:
        await self._openrouter.aclose()
        await self._openai_compatible.aclose()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_llm.py -v`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/llm.py backend/tests/test_llm.py
git commit -m "feat(backend): LLMClient dispatches on connection kind instead of flat provider config"
```

---

### Task 4: `/llm-connections` CRUD + models/refresh routes

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Modify: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.llm_connections` (Task 1), `openai_compatible.OpenAICompatibleClient` (Task 2).
- Produces (used by Task 8, the frontend `api/client.ts`): `GET/POST /api/llm-connections`, `GET/PUT/DELETE /api/llm-connections/{id}`, `POST /api/llm-connections/{id}/models/refresh`. Also produces `get_openai_compatible_client()` — a FastAPI dependency, mirroring the existing `get_llm()` pattern, so tests can override the outbound HTTP call the same way `routes.get_llm` is already overridden in the `client` fixture.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py` (near the existing `# ---- config (unchanged behavior) ----` section — read that section first to match its style):

```python
# ---- llm connections ----
def test_llm_connections_seeded_by_migration(client):
    ids = {c["id"] for c in client.get("/api/llm-connections").json()}
    assert ids == {"openrouter", "claude"}


def test_create_read_update_delete_connection(client):
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "z.ai GLM",
        "base_url": "https://api.z.ai/v4", "api_key": "sk-z", "model": "glm-4.6",
        "post_process": "strict",
    })
    assert r.status_code == 200
    cid = r.json()["id"]

    detail = client.get(f"/api/llm-connections/{cid}").json()
    assert detail["kind"] == "openai_compatible"
    assert detail["key_set"] is True
    assert "api_key" not in detail
    assert detail["models"] == []

    r = client.put(f"/api/llm-connections/{cid}", json={"name": "z.ai GLM (renamed)"})
    assert r.json()["name"] == "z.ai GLM (renamed)"

    assert client.delete(f"/api/llm-connections/{cid}").json() == {"ok": True}
    assert client.get(f"/api/llm-connections/{cid}").status_code == 404


def test_connection_never_leaks_key(client):
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint", "base_url": "https://x", "api_key": "sk-secret"})
    cid = r.json()["id"]
    body = client.get(f"/api/llm-connections/{cid}").json()
    assert body["key_set"] is True
    assert "sk-secret" not in json.dumps(body)


def test_update_connection_not_found_404(client):
    assert client.put("/api/llm-connections/nope", json={"name": "x"}).status_code == 404


def test_delete_connection_not_found_404(client):
    assert client.delete("/api/llm-connections/nope").status_code == 404


def test_models_refresh_400_for_openrouter_and_claude(client):
    assert client.post("/api/llm-connections/openrouter/models/refresh").status_code == 400
    assert client.post("/api/llm-connections/claude/models/refresh").status_code == 400


def test_models_refresh_404_for_missing_connection(client):
    assert client.post("/api/llm-connections/nope/models/refresh").status_code == 404


class FakeModelsClient:
    def __init__(self, models=None, error=None):
        self.models = models or []
        self.error = error
        self.calls = []

    async def list_models(self, base_url, key):
        self.calls.append((base_url, key))
        if self.error:
            raise self.error
        return self.models


def test_models_refresh_fetches_and_caches(client):
    from grimoire.llm import LLMError
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint", "base_url": "https://x", "api_key": "sk-x"})
    cid = r.json()["id"]
    fake = FakeModelsClient(models=[
        {"id": "glm-4.6", "name": "GLM-4.6", "context": 128000, "prompt": None, "completion": None}])
    client.app.dependency_overrides[routes.get_openai_compatible_client] = lambda: fake

    r = client.post(f"/api/llm-connections/{cid}/models/refresh")
    assert r.status_code == 200
    assert r.json()["models"] == fake.models
    assert fake.calls == [("https://x", "sk-x")]

    # persisted: a plain GET now shows the cached list without another fetch
    detail = client.get(f"/api/llm-connections/{cid}").json()
    assert detail["models"] == fake.models
    assert detail["fetched_at"]


def test_models_refresh_upstream_error_normalized(client):
    from grimoire.llm import LLMError
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint", "base_url": "https://x", "api_key": "sk-x"})
    cid = r.json()["id"]
    fake = FakeModelsClient(error=LLMError("auth", "bad key"))
    client.app.dependency_overrides[routes.get_openai_compatible_client] = lambda: fake

    r = client.post(f"/api/llm-connections/{cid}/models/refresh")
    assert r.status_code == 502
    assert r.json()["kind"] == "auth"


def test_models_refresh_route_write_hidden_if_connection_changes_during_the_fetch(client):
    # This must exercise the actual ROUTE, not just cached_models()'s gate
    # (that's already covered store-side in test_llm_connections_store.py) —
    # a route bug (e.g. capturing rev AFTER the fetch, or conditionally
    # skipping the write instead of writing unconditionally) wouldn't be
    # caught by a test that bypasses the route and pokes the store directly.
    # The fake's list_models mutates the connection AS PART OF its own
    # execution — since the route awaits it before writing the sidecar,
    # this reproduces "someone edited the connection while the fetch was
    # in flight" without needing real threading/concurrency.
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Endpoint", "base_url": "https://old", "api_key": "sk-x"})
    cid = r.json()["id"]

    class MutatingFakeClient:
        async def list_models(self, base_url, key):
            store.llm_connections.update_connection(cid, base_url="https://mutated-during-fetch")
            return [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}]

    client.app.dependency_overrides[routes.get_openai_compatible_client] = lambda: MutatingFakeClient()
    r = client.post(f"/api/llm-connections/{cid}/models/refresh")
    assert r.status_code == 200
    assert r.json()["models"] == [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}]

    detail = client.get(f"/api/llm-connections/{cid}").json()
    assert detail["models"] == []  # the stale write never surfaces
    assert detail["base_url"] == "https://mutated-during-fetch"  # the mutation itself did land


def test_models_refresh_route_write_hidden_after_delete_and_recreate_during_fetch(client):
    r = client.post("/api/llm-connections", json={
        "kind": "openai_compatible", "name": "Reused Name", "base_url": "https://old", "api_key": "sk-x"})
    cid = r.json()["id"]

    class DeleteRecreateFakeClient:
        async def list_models(self, base_url, key):
            store.llm_connections.delete_connection(cid)
            new_id = store.llm_connections.create_connection(
                "openai_compatible", "Reused Name", base_url="https://new")
            assert new_id == cid  # same freed slug — the whole point of this race
            return [{"id": "m", "name": "m", "context": None, "prompt": None, "completion": None}]

    client.app.dependency_overrides[routes.get_openai_compatible_client] = lambda: DeleteRecreateFakeClient()
    r = client.post(f"/api/llm-connections/{cid}/models/refresh")
    assert r.status_code == 200

    detail = client.get(f"/api/llm-connections/{cid}").json()
    assert detail["models"] == []  # the stale write never surfaces on the recreated connection
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k connection -v`
Expected: FAIL — every test 404s (`/api/llm-connections` doesn't exist yet) except `test_models_refresh_400_for_openrouter_and_claude`/`test_models_refresh_404_for_missing_connection`, which also 404 (route missing) rather than returning their expected status codes.

- [ ] **Step 3: Add the models to `routes.py`**

In `backend/src/grimoire/routes.py`, add near `ConfigUpdate` (after line 46, before `class DataDirUpdate`):

```python
class ConnectionCreate(BaseModel):
    kind: Literal["openrouter", "claude", "openai_compatible"]
    name: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    post_process: Literal["none", "strict"] = "none"


class ConnectionUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    post_process: Literal["none", "strict"] | None = None
```

- [ ] **Step 4: Add the DI helper and import near the top of `routes.py`**

Change the import line `from .llm import LLMClient, LLMError` (line 17) to also import the new client, and add the DI helper next to `get_llm()` (lines 20-32):

```python
from .llm import LLMClient, LLMError
from .openai_compatible import OpenAICompatibleClient

router = APIRouter()
_llm = LLMClient()
_openai_compatible_client = OpenAICompatibleClient()


def _dump(model: BaseModel) -> dict:
    """model_dump() on pydantic v2, dict() on v1. The Android build may pin the
    pure-python pydantic 1.x wheel (docs/android-architecture.md §7); this is
    the only v2-specific API the codebase uses."""
    dump = getattr(model, "model_dump", None)
    return dump() if dump is not None else model.dict()


def get_llm() -> LLMClient:
    return _llm


def get_openai_compatible_client() -> OpenAICompatibleClient:
    return _openai_compatible_client
```

- [ ] **Step 5: Add the routes**

In `backend/src/grimoire/routes.py`, add right after the existing `# ---- config ----` block's `put_data_dir` (after line 443, before `# ---- modules (#160) ----`):

```python
# ---- llm connections ----
@router.get("/llm-connections")
def get_connections():
    return store.llm_connections.list_connections()


@router.post("/llm-connections")
def post_connection(body: ConnectionCreate):
    fields = _dump(body)
    kind = fields.pop("kind")
    name = fields.pop("name")
    return {"id": store.llm_connections.create_connection(kind, name, **fields)}


@router.get("/llm-connections/{id}")
def get_connection(id: str):
    try:
        return store.llm_connections.read_connection(id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")


@router.put("/llm-connections/{id}")
def put_connection(id: str, body: ConnectionUpdate):
    fields = {k: v for k, v in _dump(body).items() if v is not None}
    try:
        store.llm_connections.update_connection(id, **fields)
        return store.llm_connections.read_connection(id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")


@router.delete("/llm-connections/{id}")
def delete_connection_route(id: str):
    try:
        store.llm_connections.delete_connection(id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")
    return {"ok": True}


@router.post("/llm-connections/{id}/models/refresh")
async def post_connection_models_refresh(
    id: str, client: OpenAICompatibleClient = Depends(get_openai_compatible_client),
):
    try:
        conn = store.llm_connections.read_connection_raw(id)
    except store.llm_connections.ConnectionNotFound:
        raise HTTPException(status_code=404, detail="connection not found")
    if conn["kind"] != "openai_compatible":
        raise HTTPException(status_code=400, detail="model listing not supported for this connection kind")
    rev = conn["rev"]
    try:
        models = await client.list_models(conn["base_url"], conn["api_key"])
    except LLMError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    fetched_at = store.now_iso()
    store.llm_connections.set_cached_models(id, models, rev)
    return {"models": models, "fetched_at": fetched_at, "rev": rev}
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k connection -v`
Expected: PASS, all tests.

- [ ] **Step 7: Run the full backend suite to check for collateral breakage**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: only the pre-existing `openrouter_key`/`provider`-dependent tests from before Task 5 land should fail (they're fixed in Task 5) — note which ones fail here so Task 5's Step 1 isn't a surprise.

- [ ] **Step 8: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(backend): add /llm-connections CRUD and models/refresh routes"
```

---

### Task 5: Rewire generation routes to `_require_connection`, rewrite `_public_config`

This is a mechanical but wide-reaching task: every route that generates LLM output currently does `cfg = store.read_config(); _require_key(cfg)` then threads `cfg` through helper functions to `client.stream`/`client.complete`. All of it moves to a resolved connection dict instead. There are 9 call sites and 5 helper functions that thread the value through; each is listed explicitly below — do not skip any, a missed one leaves a route calling `client.stream(messages, cfg)` with the OLD flat dict shape, which silently dispatches to OpenRouter with `cfg["model"]` (a `KeyError` now that `model` no longer exists on the config dict).

**Line numbers below are from the file as it stood before Task 4's edits** — Task 4 inserted ~70 lines earlier in the file (new Pydantic models + the `/llm-connections` routes), which shifts everything after it. Treat every "around line X" as approximate; locate each site by the function name and the exact code shown, not by line number. Read the current `backend/src/grimoire/routes.py` before starting this task, and re-run `grep -n "def post_chat\|def post_retry\|def _fence_stream\|_require_key"` (etc., per site below) against the live file to get accurate line numbers before editing.

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Modify: `backend/tests/test_routes.py`
- Modify: `backend/src/grimoire/store/scenes.py` (`create_scene()` stamps a new scene's `model` metadata from the removed `read_config()["model"]` — see Step 7)
- Modify: `backend/scripts/ingest_scene.py` (a standalone CLI consumer of `read_config()`/`cfg["model"]`/`cfg["openrouter_key"]`, outside `routes.py` — easy to miss, but Task 1 removing those fields breaks its `main()` entry point; see Step 8)
- Modify: `backend/tests/test_ingest_scene.py`

**Interfaces:**
- Consumes: `LLMClient.stream/complete(messages, conn)` (Task 3), `store.llm_connections.get_active()` (Task 1).
- Produces: `_require_connection() -> dict` (raises `HTTPException(409, ...)`), replacing `_require_key(cfg)`; `_public_config(cfg)` returns `{theme, system_prompt, quote_color, user_label, assistant_label, default_style_id, active_connection_id, active_connection, ready}` — no more `model`, `key_set`, `provider`, `claude_model`. This is a breaking response-shape change for `GET/PUT /api/config`, consumed by Task 9 (`ConfigView.tsx`) and Task 10 (`App.tsx`/`CampaignView.tsx`).

- [ ] **Step 1: Update `ConfigUpdate` and `_public_config` in `routes.py`**

Replace the `ConfigUpdate` model (lines 36-46):

```python
class ConfigUpdate(BaseModel):
    theme: str | None = None
    system_prompt: str | None = None
    quote_color: str | None = None
    user_label: str | None = None
    assistant_label: str | None = None
    default_style_id: str | None = None
    active_connection_id: str | None = None
```

Replace `_public_config`, `get_config`, `put_config` (the `# ---- config ----` block, roughly lines 410-429):

```python
# ---- config ----
def _public_config(cfg: dict[str, str]) -> dict:
    active = store.llm_connections.get_active()
    return {"theme": cfg["theme"], "system_prompt": cfg.get("system_prompt", ""),
            "quote_color": cfg.get("quote_color", "off"),
            "user_label": cfg.get("user_label", "You"),
            "assistant_label": cfg.get("assistant_label", "Grimoire"),
            "default_style_id": cfg.get("default_style_id", ""),
            "active_connection_id": active["id"] if active else "",
            "active_connection": ({"id": active["id"], "kind": active["kind"], "name": active["name"]}
                                   if active else None),
            "ready": _connection_ready(active)}


def _connection_ready(conn: dict | None) -> bool:
    if conn is None:
        return False
    if conn["kind"] == "openrouter":
        return bool(conn["api_key"])
    if conn["kind"] == "openai_compatible":
        return bool(conn["base_url"])
    return True  # claude never needs a key


@router.get("/config")
def get_config():
    return _public_config(store.read_config())


@router.put("/config")
def put_config(update: ConfigUpdate):
    fields = {k: v for k, v in _dump(update).items() if v is not None}
    return _public_config(store.write_config(**fields))
```

- [ ] **Step 2: Replace `_require_key` with `_require_connection`**

Find `_require_key` (the `# ---- scenes ----` section, around line 2003-2008) and replace it entirely:

```python
def _require_connection() -> dict:
    conn = store.llm_connections.get_active()
    if conn is None:
        raise HTTPException(
            status_code=409, detail={"detail": "No LLM connection selected", "kind": "missing_key"})
    if conn["kind"] == "openrouter" and not conn["api_key"]:
        raise HTTPException(
            status_code=409, detail={"detail": "OpenRouter key not set", "kind": "missing_key"})
    if conn["kind"] == "openai_compatible" and not conn["base_url"]:
        raise HTTPException(
            status_code=409, detail={"detail": "Endpoint base URL not set", "kind": "missing_key"})
    return conn
```

- [ ] **Step 3: Rename `cfg: dict` to `conn: dict` in the five threading helpers**

These functions only pass the parameter through opaquely — the rename is a pure identifier substitution, no logic change. For each, change the parameter name in the `def` line AND every reference to it inside the function body:

1. `_fence_stream` (around line 2036): `def _fence_stream(cid: str, sid: str, messages: list[dict], cfg: dict,` → `conn: dict,`; body reference `async for delta in client.stream(messages, cfg):` (around line 2051) → `client.stream(messages, conn)`.
2. `_chat_stream` (around line 2100): `def _chat_stream(cid: str, sid: str, messages: list[dict], cfg: dict, client: LLMClient):` → `conn: dict, client: LLMClient):`; body reference `return _fence_stream(cid, sid, messages, cfg, client, finalize, on_error)` (around line 2123) → `_fence_stream(cid, sid, messages, conn, client, finalize, on_error)`.
3. `_continuation_stream` (around line 2126): `def _continuation_stream(cid: str, sid: str, pid: str, messages: list[dict], cfg: dict, client: LLMClient):` → `conn: dict, client: LLMClient):`; find its `_fence_stream(...)` call inside and rename that argument too (read the function body to find the exact line — it mirrors `_chat_stream`'s pattern).
4. `_ephemeral_stream` (around line 2156): `def _ephemeral_stream(messages: list[dict], cfg: dict, client: LLMClient):` → `conn: dict, client: LLMClient):`; body reference `async for delta in client.stream(messages, cfg):` (around line 2160) → `client.stream(messages, conn)`.
5. `_run_audit` (around line 2341): `async def _run_audit(cid: str, sid: str, client: LLMClient, cfg: dict) -> tuple[list[dict], dict]:` → `conn: dict) -> tuple[list[dict], dict]:`; body reference `text = await client.complete(messages, cfg)` (around line 2366) → `client.complete(messages, conn)`.

- [ ] **Step 4: Rewire the 9 route call sites**

Each currently reads:
```python
    cfg = store.read_config()
    _require_key(cfg)
```
Change every one of these to:
```python
    conn = _require_connection()
```
Then rename every downstream use of `cfg` in that same route function to `conn`. Site by site:

1. **`post_character_tagline_generate`** (around line 1160): the pair at lines 1160-1161, and `text = await client.complete(messages, cfg)` (line 1169) → `client.complete(messages, conn)`.
2. **`post_scene_suggestions`** (around line 2210): the pair at lines 2210-2211, and `text = await client.complete(messages, cfg)` (line 2217) → `client.complete(messages, conn)`.
3. **`post_chat`** (around line 2271): the pair at lines 2271-2272, and both `_chat_stream(cid, sid, messages, cfg, client)` calls (lines 2280, 2291) → `_chat_stream(cid, sid, messages, conn, client)`.
4. **`post_retry`** (around line 2297): the pair at lines 2297-2298, and `_chat_stream(cid, sid, messages, cfg, client)` (line 2304) → `_chat_stream(cid, sid, messages, conn, client)`.
5. **`post_regenerate`** (around line 2312): the pair at lines 2312-2313, and `_chat_stream(cid, sid, messages, cfg, client)` (line 2332) → `_chat_stream(cid, sid, messages, conn, client)`.
6. **`post_absorb`** (around line 2386): the pair at lines 2386-2387; both `text = await client.complete(messages, cfg)` (line 2397) and `d_text = await client.complete(msgs, cfg)` (line 2410) → `conn`; `_run_audit(cid, sid, client, cfg)` (line 2416) → `_run_audit(cid, sid, client, conn)`.
7. **`post_audit`** (around line 2427): the pair at lines 2427-2428, and `_run_audit(cid, sid, client, cfg)` (line 2431) → `_run_audit(cid, sid, client, conn)`.
8. **`post_roll_proposal`** (around line 3111): the pair at lines 3111-3112, and `_continuation_stream(cid, sid, pid, messages, cfg, client)` (line 3172) → `_continuation_stream(cid, sid, pid, messages, conn, client)`.
9. **`post_opener`** (around line 3479): the pair at lines 3479-3480, and `_ephemeral_stream(messages, cfg, client)` (line 3482) → `_ephemeral_stream(messages, conn, client)`.

After this step, `grep -n "cfg" backend/src/grimoire/routes.py` should show zero remaining references anywhere in the file (verify this before moving on — any hit is a missed site).

- [ ] **Step 5: Bulk-update `test_routes.py`'s config setup boilerplate**

Migration seeds a connection at the **fixed id `openrouter`** (Task 1), so every test that today does `client.put("/api/config", json={"openrouter_key": "X"})` to make scene-generation routes usable can retarget the same key value at `/api/llm-connections/openrouter` instead. Run this from the repo root to do the bulk mechanical replacement:

```bash
cd backend/tests
python -c "
import re
p = 'test_routes.py'
text = open(p, encoding='utf-8').read()
text = re.sub(
    r'client\.put\(\"/api/config\", json=\{\"openrouter_key\": (\"[^\"]*\")\}\)',
    r'client.put(\"/api/llm-connections/openrouter\", json={\"api_key\": \1})',
    text,
)
open(p, 'w', encoding='utf-8').write(text)
"
```

Verify it caught everything:

Run: `grep -n "openrouter_key" backend/tests/test_routes.py`
Expected: no output (zero matches) — if any remain, they're a variant the regex didn't match (e.g. different quoting) and need a manual fix.

- [ ] **Step 6: Fix the remaining bespoke tests by hand**

These don't fit the mechanical pattern from Step 5 — read each in context first, then replace:

1. **`test_config_never_leaks_key`** (lines 41-45) — this tested `/api/config`'s own key-masking, which no longer applies (config never holds a key at all now). Replace the whole test:
```python
def test_connection_never_leaks_key(client):
    r = client.post("/api/llm-connections", json={
        "kind": "openrouter", "name": "OR2", "api_key": "sk-or-secret"})
    cid = r.json()["id"]
    body = client.get(f"/api/llm-connections/{cid}").json()
    assert body["key_set"] is True
    assert "sk-or-secret" not in json.dumps(body)
```
(This is redundant with `test_connection_never_leaks_key` added in Task 4 for an `openai_compatible` connection — keep both, one per kind, or drop this one if Task 4's version already covers the masking behavior generically. Prefer keeping both: they exercise different `create_connection` kind branches.)

2. **`test_config_system_prompt_and_quote_color_roundtrip`** (lines 49-54) — drop the now-meaningless `assert "openrouter_key" not in body` line; keep the rest of the test as-is (system_prompt/quote_color are unrelated to this change).

3. **`test_config_provider_roundtrip`** (lines 57-63) and **`test_config_rejects_unknown_provider`** (lines 66-68) — delete both; `/api/config` no longer has a `provider` field. Replace with:
```python
def test_config_active_connection_id_roundtrip(client):
    r = client.put("/api/config", json={"active_connection_id": "claude"})
    assert r.status_code == 200
    assert r.json()["active_connection_id"] == "claude"
    assert client.get("/api/config").json()["active_connection"]["kind"] == "claude"
```

4. **`test_chat_missing_key_ok_for_claude_provider`** (lines 1117-1126) — change the setup line from `client.put("/api/config", json={"provider": "claude"})` to `client.put("/api/config", json={"active_connection_id": "claude"})`; leave the rest of the test unchanged.

- [ ] **Step 7: Fix `store/scenes.py`'s scene-creation model stamp**

`create_scene()` stamps every new scene's `meta["model"]` from `read_config()["model"]` — a third consumer of the removed field, outside both `routes.py` and `ingest_scene.py`, that would raise `KeyError` on the very first scene created after Task 1 lands. (`GET /campaigns/{cid}/scenes/{sid}/context` reads this stamped value back via `scene["meta"].get("model", "")` — it doesn't call `read_config()` itself, so fixing the stamp here is the only change needed; no route changes required.)

Read `backend/src/grimoire/store/scenes.py` first. Change the import (line 9):
```python
from .config import read_config
from .llm_connections import get_active as _get_active_connection
```
Change `create_scene`'s meta construction (line 132):
```python
    active = _get_active_connection()
    meta = {"title": title, "model": active["model"] if active else "",
             "created": now, "updated": now}
```
(`read_config` may still be imported/used elsewhere in this file for unrelated fields — only remove the import if this was its sole use; check with `grep -n "read_config" backend/src/grimoire/store/scenes.py` before deleting the import line.)

- [ ] **Step 8: Fix `backend/scripts/ingest_scene.py`**

This standalone CLI (used by the `ingest-campaign-log` skill) calls `read_config()` and reads `cfg["model"]`/`cfg["openrouter_key"]` directly, and hardcodes `OpenRouterClient` — none of that goes through `routes.py`, so it's easy to miss, but Task 1 removing those config fields breaks its `main()` entry point. Read the file first, then:

1. Change the imports (near the top):
```python
from grimoire.llm import LLMClient  # noqa: E402
from grimoire.store import (  # noqa: E402
    absorb, appearances, campaigns, characters, chronicle, llm_connections, overlay, scenes,
)
```
(drop the `from grimoire.openrouter import OpenRouterClient` and `read_config` imports)

2. Change `run_absorb`'s signature and body (the `cfg: dict` parameter and its one use):
```python
async def run_absorb(cid: str, sid: str, client: LLMClient, conn: dict) -> dict:
    scene = scenes.read_scene(cid, sid)
    facts = chronicle.scene_facts(cid, sid)
    transcript = chronicle.transcript_text(scene["messages"])
    messages = absorb.build_prompt(
        transcript, facts, absorb.state_snapshot(cid, sid),
        absorb.relationships_snapshot(cid, sid), absorb.plot_snapshot(cid))
    text = await client.complete(messages, conn)
    parsed = absorb.parse_output(text)
    edits = absorb.materialize(cid, sid, parsed)
    return {"parsed": parsed, "edits": edits}
```

3. Change `ingest_one_scene`'s signature and its one call to `run_absorb`:
```python
async def ingest_one_scene(cid: str, scene: dict, client: LLMClient, conn: dict) -> dict:
```
(only the parameter name/type annotation changes; its body's `result = await run_absorb(cid, sid, client, cfg)` becomes `result = await run_absorb(cid, sid, client, conn)`)

4. Change `main()`'s config resolution:
```python
    scene = json.loads(args.input.read_text(encoding="utf-8"))
    conn = llm_connections.get_active()
    if conn is None:
        print("error: no LLM connection selected (set one up in grimoire's Configuration page)",
              file=sys.stderr)
        return 1
    if conn["kind"] == "openrouter" and not conn["api_key"]:
        print("error: the active OpenRouter connection has no key set", file=sys.stderr)
        return 1
    if conn["kind"] == "openai_compatible" and not conn["base_url"]:
        print("error: the active custom connection has no base URL set", file=sys.stderr)
        return 1
    client = LLMClient()
    result = asyncio.run(ingest_one_scene(args.campaign, scene, client, conn))
    print(json.dumps(result, indent=2))
    return 0
```

- [ ] **Step 9: Fix `test_ingest_scene.py`**

Read `backend/tests/test_ingest_scene.py` first. `FakeClient.complete` currently mirrors `OpenRouterClient.complete(messages, model, key)` — change it to mirror `LLMClient.complete(messages, conn)`:

```python
class FakeClient:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def complete(self, messages, conn):
        self.calls.append((messages, conn))
        return self.text
```

Then, every `cfg = {"model": "test/model", "openrouter_key": "k"}` literal (4 occurrences) becomes `conn = {"kind": "openrouter", "model": "test/model", "api_key": "k"}`, and every call site passing `cfg` (as a variable or the inline literal in `test_run_absorb_and_apply_scene`) passes `conn` instead. `test_run_absorb_and_apply_scene`'s final assertion changes from indexing `FakeClient.calls`' old `(messages, model, key)` shape:
```python
    assert client.calls[0][1]["model"] == "test/model" and client.calls[0][1]["api_key"] == "k"
```

- [ ] **Step 10: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, all tests (this also covers the routes.py rewiring from Steps 1-4 — most of that coverage comes from the now-passing existing route tests, not new ones).

- [ ] **Step 11: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py backend/src/grimoire/store/scenes.py backend/scripts/ingest_scene.py backend/tests/test_ingest_scene.py
git commit -m "feat(backend): rewire LLM generation routes and the ingest_scene CLI to resolved connections"
```

---

### Task 6: Frontend API client — types and connection CRUD methods

`client.ts` does have its own test file, `client.test.ts` — it covers the module-level `getConfig()` cache (a `Promise<Config> | null` singleton, shared across every component that calls `api.getConfig()`, refreshed by `putConfig`'s response and explicitly invalidated by `putDataDir`). This task's `putConfig` edit is a **narrowing of an existing function**, not a fresh one — replacing it wholesale rather than editing only its type signature would silently drop its cache-refresh side effect, which is exactly the mistake to avoid here (see Step 3).

**Files:**
- Modify: `frontend/src/api/client.ts`
- Modify: `frontend/src/api/client.test.ts`
- Modify: `frontend/src/api/models.ts`

**Interfaces:**
- Produces (used by Tasks 7, 8, 9, 10): `Model` type now nullable on `context`/`prompt`/`completion`; `LLMConnectionKind`, `LLMConnection`, `LLMConnectionDetail`, `LLMConnectionDraft`, `ModelsRefreshResult` types; `Config` type drops `model`/`key_set`/`provider`/`claude_model`, gains `active_connection_id`/`active_connection`/`ready`; `api.listConnections()`, `api.createConnection(draft)`, `api.readConnection(id)`, `api.updateConnection(id, patch)`, `api.deleteConnection(id)`, `api.refreshConnectionModels(id)`. `createConnection`/`updateConnection`/`deleteConnection` each invalidate the shared config cache (Task 10's App-level refetch, and any other in-page `getConfig()` caller, depends on this — editing or deleting a connection can change the *active* connection's readiness even though it never calls `/api/config` itself; `createConnection`'s invalidation is defense in depth, since Task 1's `delete_connection` fix means a freshly-created connection is never auto-active).

- [ ] **Step 1: Widen `Model`'s nullable fields in `api/models.ts`**

Read `frontend/src/api/models.ts` first. Change the `Model` type (lines 1-7):

```ts
export type Model = {
  id: string;
  name: string;
  context: number | null;
  prompt: string | null;
  completion: string | null;
};
```

`fetchModels`, `getModels`, `invalidateModelsCache`, `compact`, `strip`, `contextLabel` stay unchanged — OpenRouter's catalog always reports real values, and `contextLabel`'s callers (Task 7) always guard `m.context != null` before calling it, so direct-property narrowing keeps its `number` parameter valid at every call site.

`tokensPerDollar`/`priceLabel` do need a small change: `priceLabel(model: Model)` passes `model.prompt`/`model.completion` straight through to `tokensPerDollar(price: string)`, and with `Model`'s fields now `string | null`, `strict: true` rejects that at compile time — narrowing a caller's `m.prompt != null` check doesn't propagate *into* `priceLabel`'s own body, since TypeScript only narrows based on what a function's own code checks, not what its caller checked before calling it. Make both null-aware, matching the "unknown pricing renders nothing, never a fake 'Free'" rule from the design spec:

```ts
export function tokensPerDollar(price: string | null): string {
  if (price == null) return "";
  const n = Number(price);
  if (!isFinite(n) || n === 0) return "Free";
  return compact(1 / n);
}

export function priceLabel(model: Model): string {
  if (model.prompt == null || model.completion == null) return "";
  if (Number(model.prompt) === 0 && Number(model.completion) === 0) return "Free";
  return `${tokensPerDollar(model.prompt)} / ${tokensPerDollar(model.completion)} tok/$`;
}
```

Task 7's `ModelCombobox` call-site guard (`m.prompt != null && m.completion != null && <span>...`) still governs whether the chip renders *at all* — this change is what stops `priceLabel` itself from being a type error, and is redundant-but-harmless defense-in-depth if that guard is ever bypassed (it returns `""`, never `"Free"`, for unknown pricing either way).

- [ ] **Step 2: Update `Config` and add connection types in `client.ts`**

Read `frontend/src/api/client.ts` first. Add near the top (after the `import` line, before `export class ApiError`):

```ts
import type { Model } from "./models";
```

Replace the `Config` type (around line 45-49):

```ts
export type LLMConnectionKind = "openrouter" | "claude" | "openai_compatible";
export type LLMConnection = {
  id: string; kind: LLMConnectionKind; name: string;
  base_url: string; model: string; post_process: "none" | "strict";
  key_set: boolean; rev: string;
};
export type LLMConnectionDetail = LLMConnection & { models: Model[]; fetched_at: string };
export type LLMConnectionDraft = {
  kind?: LLMConnectionKind; name?: string; base_url?: string; api_key?: string;
  model?: string; post_process?: "none" | "strict";
};
export type ModelsRefreshResult = { models: Model[]; fetched_at: string; rev: string };
export type Config = {
  theme: string; system_prompt: string;
  quote_color: string; user_label: string; assistant_label: string; default_style_id: string;
  active_connection_id: string;
  active_connection: { id: string; kind: LLMConnectionKind; name: string } | null;
  ready: boolean;
};
```

- [ ] **Step 3: Narrow `putConfig`'s type — keep its cache-refresh body — and add the connection CRUD methods**

Find `putConfig` in the `api` object (around line 395). Change **only its parameter type**; its body (the `.then()` that refreshes `configCache`) must survive unchanged:

```ts
  putConfig: (body: Partial<{ theme: string; system_prompt: string; quote_color: string; user_label: string; assistant_label: string; default_style_id: string; active_connection_id: string }>) =>
    request<Config>("PUT", "/api/config", body).then((cfg) => {
      configCache = Promise.resolve(cfg); // the write's response is the fresh config
      return cfg;
    }),
```

Add near `listStyles`/`createStyle`/etc. (around line 670-676), following the same shape. `updateConnection` and `deleteConnection` each call `invalidateConfigCache()` on success — neither touches `/api/config`, but either can change the *active* connection's `ready`/shape (editing its key, or deleting it out from under `active_connection_id`), and nothing else would tell the cached `Config` to refresh:

```ts
  listConnections: () => request<LLMConnection[]>("GET", "/api/llm-connections"),
  createConnection: (draft: LLMConnectionDraft) =>
    request<{ id: string }>("POST", "/api/llm-connections", draft).then((r) => {
      // The backend never auto-activates a freshly-created connection (see
      // store/llm_connections.py's delete_connection, which clears
      // active_connection_id specifically so a same-slug recreation can't
      // silently reactivate) — this invalidation is defense in depth, not
      // load-bearing, in case that ever changes.
      invalidateConfigCache();
      return r;
    }),
  readConnection: (id: string) => request<LLMConnectionDetail>("GET", `/api/llm-connections/${id}`),
  updateConnection: (id: string, patch: Partial<LLMConnectionDraft>) =>
    request<LLMConnectionDetail>("PUT", `/api/llm-connections/${id}`, patch).then((r) => {
      invalidateConfigCache();
      return r;
    }),
  deleteConnection: (id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/llm-connections/${id}`).then((r) => {
      invalidateConfigCache();
      return r;
    }),
  refreshConnectionModels: (id: string) =>
    request<ModelsRefreshResult>("POST", `/api/llm-connections/${id}/models/refresh`),
```

- [ ] **Step 4: Fix `client.test.ts`'s stale `CFG` mock and cache test**

Read `frontend/src/api/client.test.ts` first. `CFG` (used across several tests, not just the cache one) still has the old shape:

```ts
const CFG = {
  theme: "t", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  default_style_id: "", active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter" as const, name: "OpenRouter" }, ready: true,
};
```

The cache test itself (`"getConfig is cached across sequential calls until a config write"`) doesn't need behavioral changes — `putConfig({ theme: "dark" })` still exercises the same cache-refresh path, and `theme` is still a valid field — but add a new test proving the *new* invalidation path (connection CRUD), using the real API client against a mocked `fetch` (not a mocked `api.getConfig`), so it actually proves the network call happens rather than trusting a mock to reflect the fix:

```ts
test("updating a connection invalidates the config cache", async () => {
  invalidateConfigCache();
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getConfig();
  await api.getConfig();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fetchMock.mockResolvedValue(jsonOk({ id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "", model: "m", post_process: "none", key_set: true, rev: "r2" }));
  await api.updateConnection("openrouter", { name: "OpenRouter" });

  fetchMock.mockResolvedValue(jsonOk({ ...CFG, ready: true }));
  await api.getConfig();  // must hit the network again -- the cache was invalidated
  expect(fetchMock).toHaveBeenCalledTimes(3);  // 1 getConfig + 1 updateConnection + 1 fresh getConfig
  invalidateConfigCache();
});

test("creating a connection also invalidates the config cache", async () => {
  // Defense in depth (store/llm_connections.py's delete_connection clears
  // active_connection_id specifically so creation is never supposed to
  // silently change what's active) — still proves the client doesn't rely
  // solely on that server-side guarantee to stay correct.
  invalidateConfigCache();
  const fetchMock = vi.fn().mockResolvedValue(jsonOk(CFG));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.getConfig();
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fetchMock.mockResolvedValue(jsonOk({ id: "new-conn" }));
  await api.createConnection({ kind: "openai_compatible", name: "New Endpoint" });

  fetchMock.mockResolvedValue(jsonOk(CFG));
  await api.getConfig();  // must hit the network again
  expect(fetchMock).toHaveBeenCalledTimes(3);  // 1 getConfig + 1 createConnection + 1 fresh getConfig
  invalidateConfigCache();
});
```

- [ ] **Step 5: Run the test and type-check**

Run (from `frontend/`): `npx vitest run src/api/client.test.ts && npx tsc -b`
Expected: PASS on the test; `tsc -b` shows new errors only in `ConfigView.tsx` (still references `config.model`/`config.provider`/etc.) — that's expected and fixed in Task 9. No errors in `client.ts`/`client.test.ts`/`models.ts` themselves.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts frontend/src/api/models.ts
git commit -m "feat(frontend): add LLM connection types and CRUD API methods, wire config cache invalidation"
```

---

### Task 7: Generalize `ModelCombobox` to take a `models` prop

**Files:**
- Modify: `frontend/src/routes/ModelCombobox.tsx`
- Modify: `frontend/src/routes/ModelCombobox.test.tsx`

**Interfaces:**
- Consumes: `Model` type (Task 6).
- Produces (used by Task 8): `ModelCombobox({ value, onChange, models, error? })` — no longer self-fetches via `getModels()`; the caller supplies the list and an optional `error` flag. Rows omit the price/context chip when the corresponding field is `null`, and the "free" search match only fires when pricing is actually known.

- [ ] **Step 1: Rewrite the failing test file**

Replace `frontend/src/routes/ModelCombobox.test.tsx` entirely:

```tsx
import { useState } from "react";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import ModelCombobox from "./ModelCombobox";

const MODELS = [
  { id: "anthropic/claude", name: "Claude", context: 200000, prompt: "0.00001", completion: "0.00002" },
  { id: "google/gemini", name: "Gemini", context: 1048576, prompt: "0", completion: "0" },
];

function Harness({ initial = "", models = MODELS }: { initial?: string; models?: typeof MODELS }) {
  const [v, setV] = useState(initial);
  return <ModelCombobox value={v} onChange={setV} models={models} />;
}

test("focusing with an empty query shows the full list", async () => {
  render(<Harness />);
  fireEvent.focus(screen.getByRole("textbox"));
  expect(await screen.findByText("anthropic/claude")).toBeInTheDocument();
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});

test("typing filters by id", async () => {
  render(<Harness />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  await screen.findByText("anthropic/claude");
  fireEvent.change(input, { target: { value: "google" } });
  await waitFor(() => expect(screen.queryByText("anthropic/claude")).not.toBeInTheDocument());
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});

test("typing filters by name", async () => {
  render(<Harness />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  await screen.findByText("anthropic/claude");
  fireEvent.change(input, { target: { value: "Gemini" } });
  await waitFor(() => expect(screen.queryByText("anthropic/claude")).not.toBeInTheDocument());
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});

test("rows show the model context limit", async () => {
  render(<Harness />);
  fireEvent.focus(screen.getByRole("textbox"));
  expect(await screen.findByText("200K ctx")).toBeInTheDocument();
  expect(screen.getByText("1M ctx")).toBeInTheDocument();
});

test("typing 'free' filters by the price label", async () => {
  render(<Harness />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  await screen.findByText("anthropic/claude");
  fireEvent.change(input, { target: { value: "free" } });
  await waitFor(() => expect(screen.queryByText("anthropic/claude")).not.toBeInTheDocument());
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});

test("selecting a row sets the model id", async () => {
  const onChange = vi.fn();
  render(<ModelCombobox value="" onChange={onChange} models={MODELS} />);
  fireEvent.focus(screen.getByRole("textbox"));
  fireEvent.mouseDown(await screen.findByText("google/gemini"));
  expect(onChange).toHaveBeenCalledWith("google/gemini");
});

test("free-text typing passes through even for an unlisted id", async () => {
  const onChange = vi.fn();
  render(<ModelCombobox value="" onChange={onChange} models={MODELS} />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  await screen.findByText("google/gemini");
  fireEvent.change(input, { target: { value: "my/custom-model" } });
  expect(onChange).toHaveBeenCalledWith("my/custom-model");
});

test("an error prop shows a note and still allows free-text typing", async () => {
  const onChange = vi.fn();
  render(<ModelCombobox value="" onChange={onChange} models={[]} error />);
  expect(await screen.findByText(/couldn.t load model list/i)).toBeInTheDocument();
  const input = screen.getByRole("textbox");
  fireEvent.change(input, { target: { value: "still/works" } });
  expect(onChange).toHaveBeenCalledWith("still/works");
});

test("a model with unknown pricing/context shows no chips and is not matched by 'free'", async () => {
  const models = [
    { id: "custom/model", name: "Custom", context: null, prompt: null, completion: null },
    ...MODELS,
  ];
  render(<Harness models={models} />);
  const input = screen.getByRole("textbox");
  fireEvent.focus(input);
  const row = (await screen.findByText("custom/model")).closest("li")!;
  expect(row.textContent).not.toMatch(/ctx/);
  fireEvent.change(input, { target: { value: "free" } });
  await waitFor(() => expect(screen.queryByText("custom/model")).not.toBeInTheDocument());
  expect(screen.getByText("google/gemini")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/routes/ModelCombobox.test.tsx`
Expected: FAIL — `ModelCombobox` still requires no `models` prop and calls `getModels()` internally, so most assertions time out waiting for text that never renders (no mock is set up for `getModels()` anymore in this test file).

- [ ] **Step 3: Rewrite `ModelCombobox.tsx`**

Replace the full file:

```tsx
import { useRef, useState } from "react";
import { priceLabel, contextLabel, type Model } from "../api/models";

export default function ModelCombobox({
  value,
  onChange,
  models,
  error = false,
}: {
  value: string;
  onChange: (id: string) => void;
  models: Model[];
  error?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [touched, setTouched] = useState(false);
  const blurTimer = useRef<number>();

  // Show the full list on a fresh focus; narrow once the user types.
  const q = value.toLowerCase();
  const matches =
    touched && q
      ? models.filter(
          (m) =>
            m.id.toLowerCase().includes(q) ||
            m.name.toLowerCase().includes(q) ||
            (m.prompt != null && m.completion != null && priceLabel(m).toLowerCase().includes(q)),
        )
      : models;

  function select(id: string) {
    onChange(id);
    setOpen(false);
    setTouched(false);
  }

  return (
    <div className="combobox">
      <input
        type="text"
        value={value}
        onChange={(e) => {
          onChange(e.target.value);
          setTouched(true);
          setOpen(true);
        }}
        onFocus={() => {
          setTouched(false);
          setOpen(true);
        }}
        onBlur={() => {
          blurTimer.current = window.setTimeout(() => setOpen(false), 120);
        }}
      />
      {error && (
        <div className="combobox-note">couldn't load model list — type a model id</div>
      )}
      {open && !error && matches.length > 0 && (
        <ul className="combobox-list">
          {matches.map((m) => (
            <li
              key={m.id}
              className="combobox-row"
              onMouseDown={(e) => {
                e.preventDefault();
                select(m.id);
              }}
            >
              <div className="combobox-row-top">
                <span className="combobox-name">{m.name}</span>
                {m.prompt != null && m.completion != null && (
                  <span className="combobox-price">{priceLabel(m)}</span>
                )}
              </div>
              <div className="combobox-row-bottom">
                <span className="combobox-id">{m.id}</span>
                {m.context != null && m.context > 0 && (
                  <span className="combobox-ctx">{contextLabel(m.context)}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run (from `frontend/`): `npx vitest run src/routes/ModelCombobox.test.tsx`
Expected: PASS, all tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/ModelCombobox.tsx frontend/src/routes/ModelCombobox.test.tsx
git commit -m "refactor(frontend): ModelCombobox takes models as a prop instead of self-fetching"
```

---

### Task 8: `ConnectionEditor` — the Connections page

**Files:**
- Create: `frontend/src/components/ConnectionEditor.tsx`
- Create: `frontend/src/routes/ConnectionsView.tsx`
- Create: `frontend/src/components/ConnectionEditor.test.tsx`
- Modify: `frontend/src/App.tsx` (nav entry + route)

**Interfaces:**
- Consumes: `api.listConnections/createConnection/readConnection/updateConnection/deleteConnection/refreshConnectionModels` and `api.getConfig/putConfig` (Task 6), `getModels` (`api/models.ts`, unchanged), `ModelCombobox` (Task 7), `Field` (`components/Field.tsx`, unchanged).
- Produces: `ConnectionEditor` (exported component, no props — mirrors `StyleGuideEditor`'s self-contained shape), `ConnectionsView` (default-exported route wrapper).

- [ ] **Step 1: Write the failing test file**

Create `frontend/src/components/ConnectionEditor.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { ConnectionEditor } from "./ConnectionEditor";

vi.mock("../api/client", () => ({
  api: {
    listConnections: vi.fn(), readConnection: vi.fn(), createConnection: vi.fn(),
    updateConnection: vi.fn(), deleteConnection: vi.fn(), refreshConnectionModels: vi.fn(),
    getConfig: vi.fn(), putConfig: vi.fn(),
  },
}));
vi.mock("../api/models", () => ({ getModels: vi.fn(), priceLabel: () => "", contextLabel: () => "" }));
import { api } from "../api/client";
import { getModels } from "../api/models";

const OPENROUTER = { id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "", model: "anthropic/claude-opus-4.1", post_process: "none", key_set: true, rev: "r1" };
const CUSTOM = { id: "zai-glm", kind: "openai_compatible", name: "z.ai GLM", base_url: "https://api.z.ai/v4", model: "glm-4.6", post_process: "strict", key_set: true, rev: "r2" };

beforeEach(() => {
  vi.clearAllMocks();
  (getModels as any).mockResolvedValue([]);
  (api.listConnections as any).mockResolvedValue([OPENROUTER, CUSTOM]);
  (api.getConfig as any).mockResolvedValue({ active_connection_id: "openrouter" });
  (api.readConnection as any).mockImplementation((id: string) => Promise.resolve(
    id === "openrouter"
      ? { ...OPENROUTER, models: [], fetched_at: "" }
      : { ...CUSTOM, models: [], fetched_at: "" }));
  (api.createConnection as any).mockResolvedValue({ id: "new-conn" });
  (api.updateConnection as any).mockResolvedValue({ ok: true });
  (api.deleteConnection as any).mockResolvedValue({ ok: true });
  (api.refreshConnectionModels as any).mockResolvedValue({ models: [], fetched_at: "2026-07-18", rev: "r2" });
  (api.putConfig as any).mockResolvedValue({ active_connection_id: "zai-glm" });
});

test("clicking a connection shows a read-only view", async () => {
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  expect(screen.getByText(/openai_compatible/)).toBeInTheDocument();
  expect(screen.queryByLabelText("Base URL")).toBeNull();
});

test("Edit reveals the form with kind locked", async () => {
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(screen.getByLabelText("Base URL")).toBeInTheDocument();
  expect(screen.getByLabelText("Kind")).toBeDisabled();
});

test("+ New opens the form directly with an unlocked kind picker", async () => {
  render(<ConnectionEditor />);
  await screen.findByText("+ New connection");
  fireEvent.click(screen.getByText("+ New connection"));
  expect(screen.getByLabelText("Kind")).not.toBeDisabled();
  expect(screen.queryByLabelText("Base URL")).toBeNull(); // defaults to openrouter kind
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "openai_compatible" } });
  expect(screen.getByLabelText("Base URL")).toBeInTheDocument();
});

test("creating a custom endpoint connection posts the right fields", async () => {
  render(<ConnectionEditor />);
  await screen.findByText("+ New connection");
  fireEvent.click(screen.getByText("+ New connection"));
  fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "openai_compatible" } });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "My Endpoint" } });
  fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://api.example.com/v1" } });
  fireEvent.change(screen.getByLabelText("API key"), { target: { value: "sk-new" } });
  fireEvent.click(screen.getByRole("button", { name: /create connection/i }));
  await waitFor(() => expect(api.createConnection).toHaveBeenCalledWith(
    expect.objectContaining({
      kind: "openai_compatible", name: "My Endpoint",
      base_url: "https://api.example.com/v1", api_key: "sk-new",
    })));
});

test("Set as active updates the active connection", async () => {
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /set as active/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ active_connection_id: "zai-glm" }));
});

test("Refresh models calls the refresh endpoint and shows the result", async () => {
  (api.refreshConnectionModels as any).mockResolvedValue({
    models: [{ id: "glm-4.6", name: "GLM-4.6", context: 128000, prompt: null, completion: null }],
    fetched_at: "2026-07-18T12:00:00", rev: "r2",
  });
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /refresh models/i }));
  await waitFor(() => expect(api.refreshConnectionModels).toHaveBeenCalledWith("zai-glm"));
  expect(await screen.findByText(/2026-07-18t12:00:00/i)).toBeInTheDocument();
});

test("a stale refresh response (rev no longer matches) is discarded", async () => {
  let resolveRefresh: (v: any) => void;
  (api.refreshConnectionModels as any).mockReturnValue(new Promise((res) => { resolveRefresh = res; }));
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /refresh models/i }));
  // the connection changes underneath the open form (e.g. base_url saved) before the refresh resolves
  (api.readConnection as any).mockResolvedValueOnce({ ...CUSTOM, rev: "r3", models: [], fetched_at: "" });
  await select_again();
  resolveRefresh!({ models: [{ id: "stale", name: "stale", context: null, prompt: null, completion: null }], fetched_at: "old", rev: "r2" });
  await waitFor(() => expect(screen.queryByText("stale")).not.toBeInTheDocument());

  async function select_again() {
    fireEvent.click(await within(rail).findByText("z.ai GLM"));
    await waitFor(() => expect(api.readConnection).toHaveBeenCalledTimes(2));
  }
});

test("deleting a connection removes it", async () => {
  const original = window.confirm;
  window.confirm = () => true;
  render(<ConnectionEditor />);
  const rail = await waitFor(() => screen.getByText("+ New connection").closest(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("z.ai GLM"));
  await waitFor(() => expect(api.readConnection).toHaveBeenCalledWith("zai-glm"));
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
  await waitFor(() => expect(api.deleteConnection).toHaveBeenCalledWith("zai-glm"));
  window.confirm = original;
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/ConnectionEditor.test.tsx`
Expected: FAIL with a module resolution error — `./ConnectionEditor` doesn't exist yet.

- [ ] **Step 3: Write `ConnectionEditor.tsx`**

```tsx
import { useCallback, useEffect, useState } from "react";
import {
  api, type LLMConnection, type LLMConnectionDetail, type LLMConnectionKind,
} from "../api/client";
import { getModels, type Model } from "../api/models";
import ModelCombobox from "../routes/ModelCombobox";
import { Field } from "./Field";

// Aliases resolve to the newest model of each tier at request time (the Agent
// SDK passes them through to Claude Code); pinned ids freeze a version and
// need a refresh here when new models ship.
const CLAUDE_ALIASES = [
  { id: "fable", label: "Fable (latest)" },
  { id: "opus", label: "Opus (latest)" },
  { id: "sonnet", label: "Sonnet (latest)" },
  { id: "haiku", label: "Haiku (latest)" },
];
const CLAUDE_PINNED = [
  "claude-fable-5",
  "claude-opus-4-8",
  "claude-opus-4-7",
  "claude-opus-4-6",
  "claude-sonnet-5",
  "claude-sonnet-4-6",
  "claude-haiku-4-5",
];

const BLANK_FORM = {
  kind: "openrouter" as LLMConnectionKind, name: "", base_url: "",
  model: "", post_process: "none" as "none" | "strict",
};

export function ConnectionEditor() {
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [activeId, setActiveId] = useState("");
  const [id, setId] = useState<string | null>(null);
  const [detail, setDetail] = useState<LLMConnectionDetail | null>(null);
  const [form, setForm] = useState(BLANK_FORM);
  const [mode, setMode] = useState<"view" | "edit">("edit");
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [orModels, setOrModels] = useState<Model[]>([]);
  const [orError, setOrError] = useState(false);
  const [refreshing, setRefreshing] = useState(false);

  const reload = useCallback(() => api.listConnections().then(setConnections), []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { api.getConfig().then((c) => setActiveId(c.active_connection_id)); }, []);
  useEffect(() => {
    let alive = true;
    getModels().then((m) => alive && setOrModels(m)).catch(() => alive && setOrError(true));
    return () => { alive = false; };
  }, []);

  function resetForm() {
    setId(null);
    setDetail(null);
    setForm(BLANK_FORM);
    setKey("");
    setMode("edit");
    setError(null);
  }

  async function select(cid: string) {
    setError(null);
    const d = await api.readConnection(cid);
    setId(cid);
    setDetail(d);
    setForm({ kind: d.kind, name: d.name, base_url: d.base_url, model: d.model, post_process: d.post_process });
    setKey("");
    setMode("view");
  }

  async function save() {
    if (!form.name.trim()) return;
    setError(null);
    try {
      if (id) {
        const patch: Record<string, unknown> = {
          name: form.name, base_url: form.base_url, model: form.model, post_process: form.post_process,
        };
        if (key) patch.api_key = key;
        await api.updateConnection(id, patch);
        await reload();
        await select(id);
      } else {
        const { id: newId } = await api.createConnection({ ...form, api_key: key });
        await reload();
        await select(newId);
      }
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function remove(c: LLMConnection) {
    if (!window.confirm(`Delete connection '${c.name}'?`)) return;
    await api.deleteConnection(c.id);
    if (id === c.id) resetForm();
    await reload();
  }

  async function setActive(cid: string) {
    const next = await api.putConfig({ active_connection_id: cid });
    setActiveId(next.active_connection_id);
  }

  async function refreshModels() {
    if (!id) return;
    const forId = id;
    setRefreshing(true);
    try {
      const result = await api.refreshConnectionModels(forId);
      // Discard a response that arrived after the open form moved on to a
      // different connection or a newer revision of this one (e.g. the
      // user saved a base_url change while the fetch was in flight) — the
      // same stale-async-response guard used elsewhere in this codebase
      // (ModelCombobox/StyleGuideEditor's `alive` pattern), keyed here on
      // the connection's rev instead of a mount flag.
      setDetail((d) => (d && d.id === forId && d.rev === result.rev
        ? { ...d, models: result.models, fetched_at: result.fetched_at }
        : d));
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setRefreshing(false);
    }
  }

  const customModels = detail?.models ?? [];

  return (
    <div className="editor">
      <div className="editor-list">
        <button className="primary new" onClick={resetForm}>+ New connection</button>
        {connections.map((c) => (
          <button key={c.id} className={"row" + (id === c.id ? " active" : "")} onClick={() => select(c.id)}>
            {c.name}
            <span className="mark-badge">{c.kind}</span>
            {activeId === c.id && <span className="mark-badge">active</span>}
          </button>
        ))}
      </div>

      <div className="editor-body">
        {error && <div className="banner">{error}</div>}
        {mode === "view" && id && detail ? (
          <div className="detail-view">
            <div className="detail-main">
              <h3>{detail.name}</h3>
              <div className="detail-rendered">
                <p>Kind: {detail.kind}</p>
                <p>Model: {detail.model || "(none set)"}</p>
                {detail.kind === "openai_compatible" && <p>Base URL: {detail.base_url || "(none set)"}</p>}
                {detail.kind === "openai_compatible" && <p>Prompt post-processing: {detail.post_process}</p>}
              </div>
            </div>
            <aside className="detail-sidebar">
              <div className="form-actions">
                {activeId === id
                  ? <span className="chip on">Active</span>
                  : <button className="subtle" onClick={() => setActive(id)}>Set as active</button>}
                <button className="subtle" onClick={() => setMode("edit")}>Edit</button>
              </div>
              <div className="side-section">
                <h4>Credentials</h4>
                {detail.kind === "claude"
                  ? <span className="field-hint">Uses the local Claude Code login — no key needed.</span>
                  : <span className={"chip" + (detail.key_set ? " on" : "")}>
                      {detail.key_set ? "Key set" : "No key set"}
                    </span>}
              </div>
              {detail.kind === "openai_compatible" && (
                <div className="side-section">
                  <h4>Cached models</h4>
                  <div className="field-hint">
                    {detail.fetched_at ? `Last fetched ${detail.fetched_at}` : "Never fetched"}
                  </div>
                  <button className="subtle" onClick={refreshModels} disabled={refreshing}>
                    {refreshing ? "Refreshing…" : "Refresh models"}
                  </button>
                </div>
              )}
            </aside>
          </div>
        ) : (
          <div className="form">
            <h3>{id ? "Edit connection" : "New connection"}</h3>
            <Field label="Kind">
              <select value={form.kind} disabled={!!id}
                      onChange={(e) => setForm({ ...form, kind: e.target.value as LLMConnectionKind })}>
                <option value="openrouter">OpenRouter</option>
                <option value="claude">Claude</option>
                <option value="openai_compatible">Custom (OpenAI-compatible)</option>
              </select>
            </Field>
            <Field label="Name">
              <input type="text" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </Field>

            {form.kind === "openrouter" && (
              <>
                <Field label="API key">
                  <input type="password" placeholder={detail?.key_set ? "A key is set — type to replace" : "sk-or-…"}
                         value={key} onChange={(e) => setKey(e.target.value)} />
                </Field>
                <Field label="Model">
                  <ModelCombobox value={form.model} onChange={(v) => setForm({ ...form, model: v })}
                                 models={orModels} error={orError} />
                </Field>
              </>
            )}

            {form.kind === "claude" && (
              <Field label="Claude model">
                <select aria-label="Claude model" value={form.model}
                        onChange={(e) => setForm({ ...form, model: e.target.value })}>
                  <optgroup label="Latest">
                    {CLAUDE_ALIASES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                  </optgroup>
                  <optgroup label="Pinned versions">
                    {CLAUDE_PINNED.map((mid) => <option key={mid} value={mid}>{mid}</option>)}
                  </optgroup>
                  {form.model &&
                    !CLAUDE_ALIASES.some((m) => m.id === form.model) &&
                    !CLAUDE_PINNED.includes(form.model) && (
                      <optgroup label="Custom">
                        <option value={form.model}>{form.model}</option>
                      </optgroup>
                    )}
                </select>
              </Field>
            )}

            {form.kind === "openai_compatible" && (
              <>
                <Field label="Base URL">
                  <input type="text" placeholder="https://api.example.com/v1" value={form.base_url}
                         onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
                </Field>
                <Field label="API key" hint="Optional — leave blank for servers that don't require auth.">
                  <input type="password" placeholder={detail?.key_set ? "A key is set — type to replace" : "(optional)"}
                         value={key} onChange={(e) => setKey(e.target.value)} />
                </Field>
                <Field label="Model">
                  <ModelCombobox value={form.model} onChange={(v) => setForm({ ...form, model: v })}
                                 models={customModels} />
                </Field>
                {id && (
                  <p className="field-hint">
                    {detail?.fetched_at ? `Cached models last fetched ${detail.fetched_at}. ` : "No cached models yet. "}
                    <button className="link" onClick={refreshModels} disabled={refreshing}>
                      {refreshing ? "Refreshing…" : "Fetch models"}
                    </button>
                  </p>
                )}
                <Field label="Prompt post-processing"
                       hint="Strict folds system messages into user turns and forces the sequence to start with a user turn — needed by some coding-style endpoints (e.g. z.ai's GLM) that reject a system message mid-conversation.">
                  <select value={form.post_process}
                          onChange={(e) => setForm({ ...form, post_process: e.target.value as "none" | "strict" })}>
                    <option value="none">None</option>
                    <option value="strict">Strict</option>
                  </select>
                </Field>
              </>
            )}

            <div className="form-actions">
              {id && <button className="subtle" onClick={() => remove(connections.find((c) => c.id === id)!)}>Delete</button>}
              {id && <button className="subtle" onClick={() => setMode("view")}>Cancel</button>}
              <button className="primary" onClick={save} disabled={!form.name.trim()}>
                {id ? "Save connection" : "Create connection"}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Create `ConnectionsView.tsx`**

```tsx
import { ConnectionEditor } from "../components/ConnectionEditor";

export default function ConnectionsView() {
  return (
    <div className="page view-anim" style={{ maxWidth: 1080 }}>
      <div className="page-head">
        <h1 className="page-h1">Connections</h1>
      </div>
      <ConnectionEditor />
    </div>
  );
}
```

- [ ] **Step 5: Add the nav entry and route in `App.tsx`**

Read `frontend/src/App.tsx` first. Add the import (near the other route imports):

```tsx
import ConnectionsView from "./routes/ConnectionsView";
```

Add the nav link (after the "Styles" `NavLink`, around line 48-50):

```tsx
          <NavLink to="/connections" className={({ isActive }) => "nav-btn" + (isActive ? " active" : "")}>
            Connections
          </NavLink>
```

Add the route (after the `/styles` route, around line 70):

```tsx
        <Route path="/connections" element={<ConnectionsView />} />
```

- [ ] **Step 6: Run the tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/ConnectionEditor.test.tsx`
Expected: PASS, all tests.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/ConnectionEditor.tsx frontend/src/components/ConnectionEditor.test.tsx frontend/src/routes/ConnectionsView.tsx frontend/src/App.tsx
git commit -m "feat(frontend): add the Connections page (list/detail editor for LLM connections)"
```

---

### Task 9: Shrink `ConfigView.tsx`'s LLM provider section

**Files:**
- Modify: `frontend/src/routes/ConfigView.tsx`
- Modify: `frontend/src/routes/ConfigView.test.tsx`

**Interfaces:**
- Consumes: `api.listConnections`, `Config` type with `active_connection_id`/`active_connection`/`ready` (Task 6).

- [ ] **Step 1: Rewrite the failing test file**

Read `frontend/src/routes/ConfigView.test.tsx` first (to preserve the tests unrelated to provider fields), then replace it entirely:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import ConfigView from "./ConfigView";

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    getConfig: vi.fn(), putConfig: vi.fn(), getDataDir: vi.fn(), putDataDir: vi.fn(),
    listStyles: vi.fn(), listConnections: vi.fn(),
  },
}));
vi.mock("../theme/ThemeProvider", () => ({ useTheme: () => ({ setTheme: vi.fn() }) }));
import { api } from "../api/client";

// ConfigView now renders a <Link to="/connections">, which needs Router
// context — bare render(<ConfigView />) throws ("Cannot destructure
// property 'basename' of ...useContext(...) as it is null"). Wrap it, same
// pattern as CampaignsView.test.tsx.
function renderView() {
  return render(
    <MemoryRouter>
      <ConfigView />
    </MemoryRouter>,
  );
}

const cfg = {
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  default_style_id: "", active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true,
};
const dataDir = {
  data_dir: "/home/u/.grimoire", default: "/home/u/.grimoire",
  is_default: true, source: "default" as const, exists: true,
};
const connections = [
  { id: "openrouter", kind: "openrouter", name: "OpenRouter", base_url: "", model: "m", post_process: "none", key_set: true, rev: "r1" },
  { id: "claude", kind: "claude", name: "Claude", base_url: "", model: "opus", post_process: "none", key_set: false, rev: "r2" },
];
beforeEach(() => {
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(cfg);
  (api.putConfig as any).mockResolvedValue(cfg);
  (api.getDataDir as any).mockResolvedValue(dataDir);
  (api.putDataDir as any).mockResolvedValue(dataDir);
  (api.listStyles as any).mockResolvedValue([
    { id: "gothic-horror", name: "Gothic Horror", description: "", tags: [], built_in: true },
    { id: "noir-detective", name: "Noir Detective", description: "", tags: [], built_in: true },
  ]);
  (api.listConnections as any).mockResolvedValue(connections);
});

test("saves the system prompt", async () => {
  renderView();
  const ta = await screen.findByLabelText(/system prompt/i);
  fireEvent.change(ta, { target: { value: "Never speak for the PC." } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ system_prompt: "Never speak for the PC." })));
});

test("saves the default prose style", async () => {
  renderView();
  const sel = await screen.findByLabelText(/default prose style/i);
  fireEvent.change(sel, { target: { value: "noir-detective" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith(
    expect.objectContaining({ default_style_id: "noir-detective" })));
});

test("toggling quote color saves immediately", async () => {
  renderView();
  const cb = await screen.findByLabelText(/color quoted/i);
  fireEvent.click(cb);
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ quote_color: "on" }));
});

test("moving the storage location saves the new path", async () => {
  (api.putDataDir as any).mockResolvedValue({ ...dataDir, data_dir: "/sync/grimoire", is_default: false, source: "custom" });
  renderView();
  const input = await screen.findByLabelText(/storage location/i);
  fireEvent.change(input, { target: { value: "/sync/grimoire" } });
  fireEvent.click(screen.getByRole("button", { name: /^move$/i }));
  await waitFor(() => expect(api.putDataDir).toHaveBeenCalledWith("/sync/grimoire"));
});

test("edits transcript labels and saves them", async () => {
  renderView();
  const user = await screen.findByLabelText(/your label/i);
  fireEvent.change(user, { target: { value: "Kestrel" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() =>
    expect(api.putConfig).toHaveBeenCalledWith(expect.objectContaining({ user_label: "Kestrel" })));
});

test("shows the three theme cards", async () => {
  renderView();
  expect(await screen.findByText("CODEX")).toBeInTheDocument();
  expect(screen.getByText("MANUSCRIPT")).toBeInTheDocument();
  expect(screen.getByText("ASTRAL")).toBeInTheDocument();
});

test("shows every connection in the LLM connection dropdown", async () => {
  renderView();
  const select = await screen.findByLabelText("LLM connection");
  const values = Array.from(select.querySelectorAll("option")).map((o) => (o as HTMLOptionElement).value);
  expect(values).toEqual(["openrouter", "claude"]);
  expect((select as HTMLSelectElement).value).toBe("openrouter");
});

test("switching the active connection saves immediately", async () => {
  renderView();
  const select = await screen.findByLabelText("LLM connection");
  fireEvent.change(select, { target: { value: "claude" } });
  await waitFor(() => expect(api.putConfig).toHaveBeenCalledWith({ active_connection_id: "claude" }));
});

test("links to the Connections page to manage keys/endpoints", async () => {
  renderView();
  await screen.findByLabelText("LLM connection");
  expect(screen.getByRole("link", { name: /connections/i })).toHaveAttribute("href", "/connections");
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/routes/ConfigView.test.tsx`
Expected: FAIL — `ConfigView` still renders the old provider `<select>`/OpenRouter key/Claude model fields, not an "LLM connection" labeled dropdown, and doesn't call `api.listConnections()`.

- [ ] **Step 3: Rewrite `ConfigView.tsx`**

Read the current `frontend/src/routes/ConfigView.tsx` first (needed for the untouched sections: storage location, system prompt, default prose style, transcript, theme, the `save()` helper's shape). Then:

1. Remove the imports/consts no longer needed: the `ModelCombobox` import (line 5) and the `CLAUDE_ALIASES`/`CLAUDE_PINNED` consts (lines 7-24).
2. Add `import { Link } from "react-router-dom";` and `import { api, ApiError, type Config, type DataDirInfo, type LLMConnection, type Style } from "../api/client";` (extending the existing import line with `LLMConnection`).
3. Replace the `provider`/`claudeModel`/`model`/`key` state (lines 29-32) with:
```tsx
  const [connections, setConnections] = useState<LLMConnection[]>([]);
  const [activeConnectionId, setActiveConnectionId] = useState("");
```
4. In the `useEffect` (lines 44-60), replace the `getConfig().then(...)` body's provider-related lines and add the connections fetch:
```tsx
  useEffect(() => {
    api.getConfig().then((c) => {
      setConfig(c);
      setActiveConnectionId(c.active_connection_id);
      setSystemPrompt(c.system_prompt);
      setUserLabel(c.user_label);
      setAssistantLabel(c.assistant_label);
      setDefaultStyleId(c.default_style_id);
    });
    api.getDataDir().then((d) => {
      setDataDir(d);
      setDataDirInput(d.data_dir);
    });
    api.listStyles().then(setStyleOptions).catch(() => setStyleOptions([]));
    api.listConnections().then(setConnections).catch(() => setConnections([]));
  }, []);
```
5. Update the `save()` function's parameter type (line 77) — replace `provider: string; claude_model: string;` with `active_connection_id: string;` and drop `openrouter_key: string; model: string;`:
```tsx
  async function save(fields: Partial<{ theme: string; system_prompt: string; quote_color: string; user_label: string; assistant_label: string; default_style_id: string; active_connection_id: string }>) {
    const next = await api.putConfig(fields);
    setConfig(next);
    setSaved(true);
    if (fields.theme) setTheme(fields.theme);
    setTimeout(() => setSaved(false), 1500);
  }
```
6. Replace the entire "LLM provider" section (the `<div className="section-label">LLM provider</div>` block through the closing of the `provider === "openrouter" ? (...) : (...)` conditional — lines 134-190 in the current file) with:
```tsx
      <div className="section-label">LLM connection</div>
      <select
        aria-label="LLM connection"
        value={activeConnectionId}
        onChange={(e) => {
          setActiveConnectionId(e.target.value);
          save({ active_connection_id: e.target.value });
        }}
      >
        {connections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
      </select>
      <p className="field-hint">
        Manage connections (add a custom OpenAI-compatible endpoint, edit keys, pull a
        model list) on the <Link to="/connections">Connections</Link> page.
      </p>
```
7. In the final Save button's `save({...})` call (lines 253-259), drop `model, provider, claude_model, ...(key ? { openrouter_key: key } : {})` — the connection dropdown now saves itself immediately (step 6), so it no longer belongs in the bottom Save button's payload:
```tsx
          onClick={() => save({
            system_prompt: systemPrompt,
            user_label: userLabel, assistant_label: assistantLabel,
            default_style_id: defaultStyleId,
          })}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/routes/ConfigView.test.tsx`
Expected: PASS, all tests.

- [ ] **Step 5: Type-check**

Run (from `frontend/`): `npx tsc -b`
Expected: no errors in `ConfigView.tsx`. Errors may still remain in `App.tsx`/`CampaignView.tsx`/`CampaignWizard.tsx` (fixed in Task 10).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/ConfigView.tsx frontend/src/routes/ConfigView.test.tsx
git commit -m "refactor(frontend): ConfigView picks an active connection instead of a provider+key form"
```

---

### Task 10: Rename `keySet` to `ready`, make the header pill provider-aware

`keySet` (from `config.key_set`, which no longer exists — Task 5 replaced it with `config.ready`) threads through five source files and four test files: `App.tsx` → `CampaignView.tsx` → `CastPanel.tsx` / `NewSceneChooser.tsx`, and `App.tsx` → `CampaignWizard.tsx` directly. This is a pure rename everywhere except `App.tsx` itself, which also gains new logic (showing the active connection's name instead of a hardcoded "OPENROUTER").

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/routes/CampaignView.tsx`
- Modify: `frontend/src/routes/CampaignWizard.tsx`
- Modify: `frontend/src/components/CastPanel.tsx`
- Modify: `frontend/src/components/NewSceneChooser.tsx`
- Modify: `frontend/src/routes/CampaignView.test.tsx`
- Modify: `frontend/src/routes/CampaignWizard.test.tsx`
- Modify: `frontend/src/components/CastPanel.test.tsx`
- Modify: `frontend/src/components/NewSceneChooser.test.tsx`

**Interfaces:**
- Consumes: `Config.ready: boolean`, `Config.active_connection: {id, kind, name} | null` (Task 6), and — critically — Task 6's cache-invalidation behavior on `putConfig`/`updateConnection`/`deleteConnection`; this task's location-based refetch (Step 4) is a no-op without it.
- Produces: `ready` prop replaces `keySet` on `CampaignView`, `CampaignWizard`, `CastPanel`, `NewSceneChooser`.

- [ ] **Step 1: Bulk-rename `keySet` to `ready` across all nine files**

Run from `frontend/`:

```bash
python -c "
import re
paths = [
    'src/App.tsx', 'src/components/CastPanel.tsx', 'src/components/NewSceneChooser.tsx',
    'src/routes/CampaignView.tsx', 'src/routes/CampaignWizard.tsx',
    'src/components/CastPanel.test.tsx', 'src/components/NewSceneChooser.test.tsx',
    'src/routes/CampaignView.test.tsx', 'src/routes/CampaignWizard.test.tsx',
]
for p in paths:
    text = open(p, encoding='utf-8').read()
    text = re.sub(r'\bkeySet\b', 'ready', text)
    open(p, 'w', encoding='utf-8').write(text)
"
```

Verify: `grep -rn "keySet" src` (from `frontend/`)
Expected: no output.

`CampaignView.test.tsx` separately mocks `api.getConfig()` twice (unrelated to `keySet` — `CampaignView.tsx` calls it directly to read `quote_color`) with the pre-Task-6 response shape (`model`, `key_set`, no `active_connection`/`ready`). Both are cast `as any`, so this doesn't fail `tsc -b` or break the test (the component only reads `c.quote_color`, which is unaffected) — but update both occurrences to the current shape anyway, for a mock that doesn't lie about what `/api/config` actually returns:
```ts
{ theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire", default_style_id: "", active_connection_id: "openrouter", active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true }
```
(the second occurrence additionally overrides `user_label`/`assistant_label` — keep those overrides, just on the new base shape).

- [ ] **Step 2: Fix the hint text in `CastPanel.tsx` and `NewSceneChooser.tsx`**

Both currently read `{!ready && <div className="field-hint">Set an OpenRouter key in Config to generate.</div>}` (post-rename) — this text is now wrong for non-OpenRouter connections. In both files (`frontend/src/components/CastPanel.tsx` line 253, `frontend/src/components/NewSceneChooser.tsx` line 134), change the text to:

```tsx
{!ready && <div className="field-hint">Set up an LLM connection in Config to generate.</div>}
```

- [ ] **Step 3: Update `CampaignView.tsx`'s banner text**

Read `frontend/src/routes/CampaignView.tsx` around its (post-rename) `{!ready && (...)}` block (around line 697-701). Replace:

```tsx
        {!ready && (
          <div className="banner">
            No LLM connection ready. <Link to="/config">Set one up in Config</Link>.
          </div>
        )}
```

(`Link` is already imported in this file.)

- [ ] **Step 4: Make `App.tsx`'s status pill provider-aware and keep it live across navigation**

`App.tsx` mounts once for the whole SPA session — it never remounts when the route changes (`<Routes>` only swaps which child element renders), so a `useEffect(..., [])` that fetches `ready`/the active connection only runs once, ever. Editing the active connection on `/config` or `/connections` would otherwise leave the header pill (and every `ready`-gated control fed by it) stuck showing the state from whenever `App.tsx` first mounted, until a hard page reload — a real regression from the feature's own point (letting you freely swap which connection is active). Refetch on every route change instead of only once.

Read `frontend/src/App.tsx` first. Add the `useLocation` import (extend the existing `react-router-dom` import line):

```tsx
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
```

Replace the `keySet` state (post-rename, `ready`) and add an active-connection label:

```tsx
  const [ready, setReady] = useState(false);
  const [activeLabel, setActiveLabel] = useState("NO CONNECTION");
```

Split the single `getConfig()` effect into two: keep the original one-time effect fetching only `theme` (unchanged in shape — `theme === null` still gates the initial render, so this must stay a one-time, `[]`-dependency effect), and add a second effect that fetches `ready`/`active_connection` and re-runs whenever the route changes:

```tsx
  const location = useLocation();

  useEffect(() => {
    api.getConfig().then((c) => setTheme(c.theme)).catch(() => setTheme(DEFAULT_THEME));
  }, []);

  useEffect(() => {
    api.getConfig().then((c) => {
      setReady(c.ready);
      setActiveLabel(c.active_connection ? c.active_connection.name.toUpperCase() : "NO CONNECTION");
    });
  }, [location.pathname]);
```

(Two separate `getConfig()` calls on first mount is an acceptable, harmless duplication — `request()`'s in-flight-GET dedup in `client.ts` already coalesces genuinely-simultaneous calls to the same path into one network request.)

This effect only *reads* `getConfig()` — it depends entirely on Task 6's `putConfig`/`updateConnection`/`deleteConnection` correctly invalidating/refreshing the shared `configCache` for that read to ever return anything other than what was cached at mount. Without Task 6's fix, this effect would re-run on every navigation but keep observing the same stale cached promise, appearing to do nothing — do not implement this step out of order relative to Task 6.

Change the status pill (the line with `OPENROUTER · {ready ? "CONNECTED" : "NO KEY"}`):

```tsx
            <span className="dot">●</span> {activeLabel} · {ready ? "CONNECTED" : "NOT READY"}
```

- [ ] **Step 5: Rewrite `App.test.tsx`**

The existing test mocks `getConfig()` with the old response shape (`model`, `key_set`) and asserts the old hardcoded `"openrouter · connected"` text — both are gone. Replace the file:

```tsx
import { render, screen, within, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

vi.mock("./api/client", () => ({
  api: {
    getConfig: vi.fn(),
    listCampaigns: vi.fn().mockResolvedValue([]),
    listWorlds: vi.fn().mockResolvedValue([]),
    listModules: vi.fn().mockResolvedValue([]),
  },
}));
import { api } from "./api/client";

const READY_OPENROUTER = {
  theme: "codex", system_prompt: "", quote_color: "off", user_label: "You", assistant_label: "Grimoire",
  default_style_id: "", active_connection_id: "openrouter",
  active_connection: { id: "openrouter", kind: "openrouter", name: "OpenRouter" }, ready: true,
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.getConfig as any).mockResolvedValue(READY_OPENROUTER);
});

test("renders the chrome top bar with brand, nav, and connection status", async () => {
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(await screen.findByText(/GRIMOIRE/)).toBeInTheDocument();
  const topbar = within(screen.getByRole("banner"));
  expect(topbar.getByRole("link", { name: /campaigns/i })).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /worlds/i })).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /modules/i })).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /connections/i })).toBeInTheDocument();
  expect(topbar.getByText(/openrouter · connected/i)).toBeInTheDocument();
  expect(topbar.getByRole("link", { name: /config/i })).toBeInTheDocument();
});

test("shows NOT READY and the connection's name when unready", async () => {
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false,
    active_connection: { id: "zai-glm", kind: "openai_compatible", name: "z.ai GLM" },
  });
  render(<MemoryRouter><App /></MemoryRouter>);
  expect(await screen.findByText(/z\.ai glm · not ready/i)).toBeInTheDocument();
});

test("the status pill refetches and updates after navigating, without a reload", async () => {
  render(<MemoryRouter initialEntries={["/"]}><App /></MemoryRouter>);
  await screen.findByText(/openrouter · connected/i);

  // simulate the active connection having changed elsewhere (Config/Connections
  // page) — the next getConfig() call reflects it
  (api.getConfig as any).mockResolvedValue({
    ...READY_OPENROUTER, ready: false, active_connection_id: "claude",
    active_connection: { id: "claude", kind: "claude", name: "Claude" },
  });
  fireEvent.click(screen.getByRole("link", { name: /worlds/i }));
  await waitFor(() => expect(screen.getByText(/claude · not ready/i)).toBeInTheDocument());
});
```

- [ ] **Step 6: Run the frontend suite**

Run (from `frontend/`): `npx vitest run`
Expected: PASS, all tests (this exercises the rename across `CampaignView.test.tsx`, `CampaignWizard.test.tsx`, `CastPanel.test.tsx`, `NewSceneChooser.test.tsx`, and the rewritten `App.test.tsx`, alongside everything else).

- [ ] **Step 7: Type-check**

Run (from `frontend/`): `npx tsc -b`
Expected: no errors anywhere in the frontend.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignWizard.tsx frontend/src/components/CastPanel.tsx frontend/src/components/NewSceneChooser.tsx frontend/src/routes/CampaignView.test.tsx frontend/src/routes/CampaignWizard.test.tsx frontend/src/components/CastPanel.test.tsx frontend/src/components/NewSceneChooser.test.tsx
git commit -m "refactor(frontend): rename keySet to ready, show the active connection in the header pill"
```

---

### Task 11: End-to-end verification in a real browser

Type checks and unit tests verify code correctness, not that the feature actually works end-to-end in the app — this task is that check, per `CLAUDE.md`'s UI-change verification requirement. Use the `verify` skill (isolated store, mocked OpenRouter) rather than the user's real `~/.grimoire`.

**Files:** none (verification only — no code changes expected; if this task finds a bug, fix it and add a regression test to the task above that owns the broken behavior, then re-run this task).

- [ ] **Step 1: Launch the app against an isolated store**

Invoke the `verify` skill to start the backend + frontend against a scratch `GRIMOIRE_HOME` with a mocked OpenRouter, and open it in the browser tool.

- [ ] **Step 2: Confirm migration ran correctly on a fresh store**

Navigate to `/config`. Confirm:
- The "LLM connection" dropdown shows exactly two options: "OpenRouter" and "Claude".
- The header pill shows "OPENROUTER · NOT READY" (fresh store, no key yet).

- [ ] **Step 3: Exercise the Connections page**

Navigate to `/connections`. Confirm:
- Both migrated connections (OpenRouter, Claude) appear in the rail.
- Clicking OpenRouter shows its read-only view (kind, model); Edit reveals the form with the key/model fields; typing a key and saving works, and the sidebar's "Key set" chip flips on.
- `+ New connection` opens a blank form with an unlocked Kind picker; switching Kind to "Custom (OpenAI-compatible)" reveals Base URL/API key/Model/Prompt post-processing fields.
- Create a custom connection pointed at a real or fake OpenAI-compatible URL (e.g. `https://httpbin.org` is fine for a network-reachability check even though it isn't a real chat endpoint — the goal here is exercising the request path, not getting real models back). Confirm the "Fetch models"/"Refresh models" button fires a request and doesn't crash the page on a non-200/network error (the error surfaces in the `error` banner, not a blank screen).
- Click "Set as active" on the custom connection; confirm the sidebar flips to an "Active" badge and the header pill's label updates on next navigation.

- [ ] **Step 4: Confirm scene generation still works on the default OpenRouter connection**

Set OpenRouter back to active (or from a fresh store, leave it active). Set its key. Create a campaign, start a scene, send a chat turn. Confirm the mocked OpenRouter response streams in as before — this is the regression check that Task 5's rewiring of `_require_connection`/the streaming helpers didn't break the primary path.

- [ ] **Step 5: Confirm Claude readiness (no key required)**

Switch the active connection to Claude via `/config`'s dropdown. Confirm the header pill shows "NOT READY" flips to not requiring a key — i.e. `ready` becomes `true` for Claude even with no `api_key` set (verifiable via the `/api/config` response in the browser's network tab, since actually invoking Claude generation isn't exercised by the `verify` skill's mocked-OpenRouter setup).

- [ ] **Step 6: Record findings**

If any step surfaces a bug, go back to the task that owns the broken file, fix it there (with a regression test), then return to this task and re-verify from Step 1. Once everything above passes cleanly, this plan's frontend work is confirmed working, not just type-checked.

---

## Self-Review

**Spec coverage** — every section of `docs/superpowers/specs/2026-07-18-llm-connections-design.md` maps to a task: §1 data model + module surface → Task 1; §2 migration → Task 1; §3 config & dispatch → Tasks 1, 3, 5; §4 provider client + strict folding → Task 2; §5 model caching + `rev` gating + sidecar hygiene → Tasks 1, 4, 8; §6 routes (including the `api_key`-clears-on-`base_url`-change rule) → Tasks 1, 4, 5; §7 frontend → Tasks 6-10. All 5 rounds of Codex findings (credential leak, migration marker safety, stale `_public_config`, sidecar lifecycle, strict-mode role failure, the `rev` token itself, and the refresh-response staleness guard) are implemented in Tasks 1 (`_write_raw`/`ensure_migrated`/`update_connection`), 2 (`_strict_messages`'s `else` branch), and 8 (`refreshModels`'s rev comparison) respectively — each has a corresponding test.

**Placeholder scan** — no TBD/TODO markers; every step has literal code or an exact shell command; no "similar to Task N" references (Task 5's call-site rewiring is the one place content is described by line-number pattern rather than fully reproduced, because `routes.py` is 3500+ lines — but the transformation itself is shown in full and applies identically at each named location, which is DRY rather than a placeholder).

**Type consistency** — checked `conn`/connection dict field names (`kind, name, base_url, api_key, model, post_process, rev`) match across Tasks 1, 3, 4, 5, 8; `LLMClient.stream(messages, conn)` signature matches between Task 3's definition and Task 5's call sites; `Model`'s nullable fields (`context/prompt/completion: T | null`) match between Task 6's type, Task 2's `list_models()` output shape, and Task 7's `ModelCombobox` null-guards; `ModelsRefreshResult`'s `rev` field matches between Task 4's route response and Task 8's `refreshModels()` comparison.

**Codex adversarial review (plan)** — a background review against this plan (separate from the 5 rounds already run against the spec it implements) found 6 findings, all fixed in place: Task 1's Step 6/7 originally left two `test_config_store.py` tests and one `test_data_dir.py` test asserting removed config fields (now rewritten); `update_connection` filtered only `None`, so an explicitly-serialized empty `api_key` could erase a credential on an unrelated update (now treats empty-when-`base_url`-unchanged as omitted, with a dedicated test); Task 6's `Model` widening broke `tsc -b` through `priceLabel`/`tokensPerDollar`'s non-nullable signatures (both made null-safe); `App.tsx`'s config fetch ran once on mount with no re-fetch trigger, so switching connections elsewhere left the header pill and `ready`-gated controls stale until a hard reload (now re-fetches on every route change, with a test); `write_config()` reconstructed the file from only the narrowed `_CONFIG_KEYS`, silently erasing the legacy fields the spec explicitly promised would survive as a recovery snapshot — on migration's own first write (now preserves unrecognized raw keys); and the original stale-refresh test bypassed the route entirely, poking the store directly rather than proving the route itself captures `rev` before the fetch and writes unconditionally (replaced with two route-level tests using a fake whose `list_models` mutates the connection mid-fetch, covering both the base_url-change and delete-recreate races).

**My own follow-up sweep** (after applying the above) found three more consumers of the removed config fields that neither the original plan nor the review had caught, all now fixed: `store/scenes.py`'s `create_scene()` stamped every new scene's `model` metadata from `read_config()["model"]` — would `KeyError` on the very first scene created post-migration (Task 5, Step 7); `backend/scripts/ingest_scene.py`, a standalone CLI outside `routes.py` entirely, read `cfg["model"]`/`cfg["openrouter_key"]` and hardcoded `OpenRouterClient` in its `main()` (Task 5, Steps 8-9, plus its test file); and `App.test.tsx` (missed from Task 10's original file list) mocked the old `getConfig()` shape and asserted the old hardcoded pill text (now rewritten with a dedicated navigation-refetch test). `test_frontmatter.py`'s incidental use of `"openrouter_key"` as a sample field name was checked and confirmed unrelated — it tests the generic frontmatter parser, not `config.py`'s schema, and needs no change.

**Codex adversarial review, round 2 (plan)** — a second background pass against the round-1 fixes found one more high-severity issue, which round 1's own fixes had inadvertently caused: Task 6's `putConfig` replacement showed only a narrowed type signature and silently dropped the existing function's cache-refresh body (`configCache = Promise.resolve(cfg)`) in the process — a real regression that would have broken `client.test.ts`'s pre-existing cache test and made Task 10's location-based refetch (added to fix round 1's App-staleness finding) a no-op, since it reads through the very cache that was no longer being refreshed. Also flagged, correctly: `updateConnection`/`deleteConnection` never invalidated the cache at all, so editing or deleting the *active* connection's own fields (fixing a missing key, say) wouldn't be reflected in `ready` even after the `putConfig` fix, since neither call touches `/api/config`. Both fixed in Task 6 (Step 3's `putConfig` now keeps its `.then()`; `updateConnection`/`deleteConnection` each call `invalidateConfigCache()`), with `client.test.ts`'s `CFG` mock updated to the new shape and a new test proving the invalidation path against a mocked `fetch` rather than a mocked `api.getConfig()` (Step 4) — Task 10 cross-references this dependency explicitly so the two tasks aren't implemented out of order.

**Codex adversarial review, round 3 (plan)** — a third pass (two prior attempts hit a transient Codex capacity error mid-turn and were discarded as inconclusive rather than trusted) found one more high-severity issue, an identity-confusion gap distinct from rounds 1-2's caching focus: `delete_connection` never cleared `config.active_connection_id`, so deleting the *active* connection left a dangling reference to its now-freed slug — and `create_connection` reuses freed slugs (the same collision the sidecar's `rev` gate exists to guard against). A later connection created under the same name would land at the identical id and silently become "active" — potentially a different kind or endpoint entirely — without ever being explicitly selected. Fixed at the root: `delete_connection` now clears `active_connection_id` whenever it matches the id being deleted (Task 1), so a same-slug recreation always starts inactive regardless of when or how it's recreated; `createConnection` also gained cache invalidation as explicit defense in depth (Task 6), with tests for both (a store-level delete/recreate test in Task 1, a client-cache test in Task 6).

**Codex adversarial review, round 4 (plan)** — a fourth pass found that round 3's fix, while correct for the normal path, still had a partial-failure window of its own: `delete_connection` cleared `active_connection_id` *after* unlinking the file, so a failure between those two steps (disk error, process death) would leave the file gone — its slug freed and reusable — while `config.md` still referenced it, reproducing the exact dangling-reference bug from a different angle. Fixed by pure reordering, no new infrastructure: clear `active_connection_id` *before* unlinking the file, so every partial-failure window is retry-safe — fail before the config write and nothing changed yet; fail during the unlink after and `active_connection_id` is already correctly cleared even though the file (harmlessly) still exists, a retriable "delete didn't finish" state rather than a dangling active reference. Added a failure-injection test (Task 1) that forces the file unlink to raise after the config write succeeds, proving the clear survives and a retry completes cleanly.

---

**Plan complete and saved to `docs/superpowers/plans/2026-07-18-llm-connections.md`. Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
