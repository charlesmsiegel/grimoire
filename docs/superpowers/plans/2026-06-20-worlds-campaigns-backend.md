# Worlds & Campaigns — Backend Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat conversation pile with a worlds → campaigns → scenes data model plus a per-object push/sync engine, all as markdown under `~/.grimoire/`.

**Architecture:** `store.py` becomes a `store/` package of focused modules. A container-agnostic `entities` module does generic kind/id CRUD + content hashing over any root (a world dir or a campaign dir). `worlds`/`campaigns` add meta + copy-on-create. `sync` computes per-campaign incoming changes (new/update/conflict) by comparing world/base/mine content hashes, with accept/reject advancing a per-campaign `sync.md` base-hash manifest. `scenes` is the old conversation code re-homed under a campaign. `routes` exposes the new HTTP surface and drops `/api/conversations*`.

**Tech Stack:** Python 3.11+, FastAPI, pytest, `httpx` (existing). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-20-worlds-campaigns-design.md`

## Global Constraints

- Python 3.11+ (uses `str | None` unions, `tuple[str, str]` builtins).
- No new runtime dependencies; standard library only for new code (`hashlib`, `shutil`, `pathlib`, `re`, `datetime`).
- Markdown-as-database only — no SQLite, no JSON files on disk. The `sync.md` manifest reuses the existing frontmatter writer (string scalars only).
- Frontmatter values are string scalars only; keys may contain `/` (manifest refs like `characters/seraphine`).
- All filesystem state lives under `home()` (`GRIMOIRE_HOME` env var or `~/.grimoire`), resolved live on every call (no caching) so tests can point it at a temp dir.
- **Entity IDs are stable for life** — renaming an entity changes only its `name` frontmatter, never its file/id (sync refs depend on stable `(kind, id)`). Scene IDs follow the old conversation behavior (rename moves the file).
- **Entities carry no timestamps**; `world.md`/`campaign.md` carry `created`/`updated`.
- Entity kinds are gated by the allowlist `("characters", "locations", "lore")`.
- Run tests from `backend/` using the project venv: `python -m pytest` (on Windows `.venv\Scripts\python -m pytest`).
- Commit after every task with the trailer `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

```
backend/src/grimoire/
  store/
    __init__.py        # re-exports public surface; preserves `import grimoire.store as store`
    frontmatter.py     # parse_frontmatter / dump_frontmatter / quoting   (moved verbatim)
    paths.py           # home(), ensure_home(), now_iso(), slugify(), uniquify()
    config.py          # read_config / write_config + DEFAULT_MODEL/THEME   (moved verbatim)
    entities.py        # generic kind/id CRUD + hashing over a container root
    worlds.py          # world meta CRUD + delete
    campaigns.py       # campaign meta CRUD + copy-on-create + manifest IO
    sync.py            # incoming / accept / reject / campaigns_for_world
    scenes.py          # scene CRUD + append_message (re-homed conversations)
  routes.py            # rewritten HTTP surface
backend/tests/
  test_frontmatter.py        # unchanged (re-exports keep it green)
  test_config_store.py       # unchanged
  test_entities_store.py     # new
  test_worlds_store.py       # new
  test_campaigns_store.py    # new
  test_sync_store.py         # new  (the critical engine tests)
  test_scene_store.py        # replaces test_conversation_store.py
  test_routes.py             # rewritten for campaigns/scenes/worlds/sync
  test_openrouter.py         # unchanged
```

Import-cycle rule: `sync` imports `campaigns`/`worlds`/`entities`; `campaigns` imports `worlds`/`entities` (NOT `sync`). The manifest read/write helpers live in `campaigns` so `campaigns.create_campaign` can write a manifest without importing `sync`.

---

## Task 1: Split `store.py` into a package (frontmatter + paths + config)

Pure refactor: move existing code into modules behind a re-exporting `__init__.py`. Conversation code is intentionally **left behind / dropped** here (it returns under `scenes` in Task 6); to keep the suite green during the move, `test_conversation_store.py` is deleted in this task (its replacement arrives in Task 6).

**Files:**
- Create: `backend/src/grimoire/store/__init__.py`
- Create: `backend/src/grimoire/store/frontmatter.py`
- Create: `backend/src/grimoire/store/paths.py`
- Create: `backend/src/grimoire/store/config.py`
- Delete: `backend/src/grimoire/store.py`
- Delete: `backend/tests/test_conversation_store.py`
- Test: `backend/tests/test_frontmatter.py`, `backend/tests/test_config_store.py` (existing, must still pass)

**Interfaces:**
- Produces:
  - `frontmatter.parse_frontmatter(text: str) -> tuple[dict[str, str], str]`
  - `frontmatter.dump_frontmatter(meta: dict[str, str], body: str) -> str`
  - `paths.home() -> Path`, `paths.ensure_home() -> Path`, `paths.now_iso() -> str`, `paths.slugify(text: str) -> str`, `paths.uniquify(base_id: str, exists: Callable[[str], bool]) -> str`
  - `config.read_config() -> dict[str, str]`, `config.write_config(**fields: str) -> dict[str, str]`, `config.DEFAULT_MODEL`, `config.DEFAULT_THEME`
  - `store` package re-exports all of the above so `store.parse_frontmatter`, `store.read_config`, etc. keep working.

- [ ] **Step 1: Create `frontmatter.py`** — move `_needs_quotes`, `_quote`, `_unquote`, `parse_frontmatter`, `dump_frontmatter` verbatim from the current `store.py` (lines 11–65). No logic changes.

```python
"""`---`-fenced frontmatter with string-scalar values (dependency-light)."""

from __future__ import annotations


def _needs_quotes(value: str) -> bool:
    if value == "":
        return True
    if value != value.strip():
        return True
    return any(c in value for c in ":#'\"")


def _quote(value: str) -> str:
    if not _needs_quotes(value):
        return value
    return "'" + value.replace("'", "''") + "'"


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split `---`-fenced frontmatter from the body. String scalars only."""
    if not text.startswith("---\n"):
        return {}, text
    rest = text[4:]
    end = rest.find("\n---")
    if end == -1:
        return {}, text
    block = rest[:end]
    after = rest[end + 4:]
    if after.startswith("\n"):
        after = after[1:]
    if after.startswith("\n"):
        after = after[1:]
    meta: dict[str, str] = {}
    for line in block.splitlines():
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        meta[key.strip()] = _unquote(value)
    return meta, after


def dump_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {_quote('' if value is None else str(value))}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + "\n" + body
```

- [ ] **Step 2: Create `paths.py`** — extract `home()`, add `ensure_home()` (now creating `worlds/` and `campaigns/`), `now_iso()`, `slugify()`, and a generic `uniquify()`.

```python
"""Filesystem location + id helpers for the ~/.grimoire store."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("GRIMOIRE_HOME") or (Path.home() / ".grimoire"))


def ensure_home() -> Path:
    base = home()
    (base / "worlds").mkdir(parents=True, exist_ok=True)
    (base / "campaigns").mkdir(parents=True, exist_ok=True)
    return base


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "untitled"


def uniquify(base_id: str, exists: Callable[[str], bool]) -> str:
    """Return base_id, or base_id-2, base_id-3, ... until `exists` is False."""
    candidate = base_id
    n = 2
    while exists(candidate):
        candidate = f"{base_id}-{n}"
        n += 1
    return candidate
```

- [ ] **Step 3: Create `config.py`** — move `read_config`/`write_config` and the config constants verbatim (current `store.py` lines 68–108), repointing at `paths`.

```python
"""config.md read/write (frontmatter only)."""

from __future__ import annotations

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home

DEFAULT_MODEL = "anthropic/claude-opus-4.1"
DEFAULT_THEME = "occult"
_CONFIG_KEYS = ("openrouter_key", "model", "theme")


def _config_path():
    return home() / "config.md"


def read_config() -> dict[str, str]:
    ensure_home()
    path = _config_path()
    if not path.exists():
        defaults = {"openrouter_key": "", "model": DEFAULT_MODEL, "theme": DEFAULT_THEME}
        path.write_text(dump_frontmatter(defaults, ""), encoding="utf-8")
        return defaults
    meta, _ = parse_frontmatter(path.read_text(encoding="utf-8"))
    return {
        "openrouter_key": meta.get("openrouter_key", ""),
        "model": meta.get("model", DEFAULT_MODEL),
        "theme": meta.get("theme", DEFAULT_THEME),
    }


def write_config(**fields: str) -> dict[str, str]:
    cfg = read_config()
    for key, value in fields.items():
        if key in _CONFIG_KEYS and value is not None:
            cfg[key] = value
    _config_path().write_text(dump_frontmatter(cfg, ""), encoding="utf-8")
    return cfg
```

- [ ] **Step 4: Create `__init__.py`** re-exporting the public surface. (Submodules added in later tasks get appended here.)

```python
"""Filesystem-as-database for grimoire: markdown files under ~/.grimoire/."""

from __future__ import annotations

from .config import DEFAULT_MODEL, DEFAULT_THEME, read_config, write_config
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home, now_iso, slugify, uniquify

__all__ = [
    "parse_frontmatter",
    "dump_frontmatter",
    "home",
    "ensure_home",
    "now_iso",
    "slugify",
    "uniquify",
    "read_config",
    "write_config",
    "DEFAULT_MODEL",
    "DEFAULT_THEME",
]
```

- [ ] **Step 5: Delete the old files**

```bash
git rm backend/src/grimoire/store.py backend/tests/test_conversation_store.py
```

- [ ] **Step 6: Run the surviving store tests — expect PASS**

Run (from `backend/`): `python -m pytest tests/test_frontmatter.py tests/test_config_store.py -v`
Expected: PASS (re-exports keep `store.parse_frontmatter` / `store.read_config` working; `importlib.reload(store)` reloads the package `__init__`).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store backend/tests
git commit -m "refactor(store): split store.py into a package; drop flat conversations"
```

---

## Task 2: `entities.py` — generic entity CRUD + hashing

**Files:**
- Create: `backend/src/grimoire/store/entities.py`
- Modify: `backend/src/grimoire/store/__init__.py` (export `entities` + its exceptions)
- Test: `backend/tests/test_entities_store.py`

**Interfaces:**
- Consumes: `frontmatter`, `paths.slugify`, `paths.uniquify`.
- Produces:
  - `entities.ENTITY_KINDS: tuple[str, ...]` = `("characters", "locations", "lore")`
  - `entities.EntityNotFound(Exception)`, `entities.UnknownKind(Exception)`
  - `list_entities(root: Path, kind: str) -> list[dict]` → each `{"id", "name", **frontmatter}`, sorted by id
  - `read_entity(root: Path, kind: str, eid: str) -> dict` → `{"meta": {"id", **fm}, "body": str}`
  - `create_entity(root: Path, kind: str, name: str, body: str = "") -> str` (returns eid; **id stable for life**)
  - `update_entity(root: Path, kind: str, eid: str, name: str | None = None, body: str | None = None) -> None`
  - `delete_entity(root: Path, kind: str, eid: str) -> None`
  - `entity_hash(root: Path, kind: str, eid: str) -> str | None` (sha256 hex of file text)
  - `all_refs(root: Path) -> list[tuple[str, str]]` → `(kind, eid)` across all kinds, sorted
  - `entity_counts(root: Path) -> dict[str, int]`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_entities_store.py
from pathlib import Path

import pytest

from grimoire.store import entities


def test_create_read_and_stable_id(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "characters", "Seraphine", "Keeper of the library.")
    assert eid == "seraphine"
    got = entities.read_entity(tmp_path, "characters", eid)
    assert got["meta"]["name"] == "Seraphine"
    assert got["body"].strip() == "Keeper of the library."
    # renaming keeps the id; only the name frontmatter changes
    entities.update_entity(tmp_path, "characters", eid, name="Seraphine the Drowned")
    assert eid == "seraphine"
    assert entities.read_entity(tmp_path, "characters", eid)["meta"]["name"] == "Seraphine the Drowned"


def test_collision_suffix(tmp_path: Path):
    a = entities.create_entity(tmp_path, "characters", "Echo")
    b = entities.create_entity(tmp_path, "characters", "Echo")
    assert a == "echo"
    assert b == "echo-2"


def test_unknown_kind_raises(tmp_path: Path):
    with pytest.raises(entities.UnknownKind):
        entities.create_entity(tmp_path, "weapons", "Sword")


def test_missing_entity_raises(tmp_path: Path):
    with pytest.raises(entities.EntityNotFound):
        entities.read_entity(tmp_path, "lore", "nope")


def test_hash_changes_only_with_content(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Salt Pact", "Old.")
    h1 = entities.entity_hash(tmp_path, "lore", eid)
    entities.update_entity(tmp_path, "lore", eid, body="Old.")  # no change
    assert entities.entity_hash(tmp_path, "lore", eid) == h1
    entities.update_entity(tmp_path, "lore", eid, body="New.")
    assert entities.entity_hash(tmp_path, "lore", eid) != h1
    assert entities.entity_hash(tmp_path, "lore", "absent") is None


def test_all_refs_and_counts(tmp_path: Path):
    entities.create_entity(tmp_path, "characters", "A")
    entities.create_entity(tmp_path, "locations", "B")
    assert set(entities.all_refs(tmp_path)) == {("characters", "a"), ("locations", "b")}
    assert entities.entity_counts(tmp_path) == {"characters": 1, "locations": 1, "lore": 0}
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: grimoire.store.entities`)

Run: `python -m pytest tests/test_entities_store.py -v`

- [ ] **Step 3: Implement `entities.py`**

```python
"""Generic entity CRUD + content hashing over an arbitrary container root.

A "container root" is a world dir or a campaign dir; entities live at
`<root>/<kind>/<id>.md`. Entity ids are stable for life (rename changes only the
`name` frontmatter) so sync refs `(kind, id)` line up across world and campaign.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify

ENTITY_KINDS: tuple[str, ...] = ("characters", "locations", "lore")


class EntityNotFound(Exception):
    pass


class UnknownKind(Exception):
    pass


def _check_kind(kind: str) -> None:
    if kind not in ENTITY_KINDS:
        raise UnknownKind(kind)


def _kind_dir(root: Path, kind: str) -> Path:
    return root / kind


def _entity_path(root: Path, kind: str, eid: str) -> Path:
    return root / kind / f"{eid}.md"


def list_entities(root: Path, kind: str) -> list[dict]:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    out: list[dict] = []
    if d.exists():
        for p in sorted(d.glob("*.md")):
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            out.append({"id": p.stem, "name": meta.get("name", p.stem), **meta})
    return out


def read_entity(root: Path, kind: str, eid: str) -> dict:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": {"id": eid, **meta}, "body": body}


def create_entity(root: Path, kind: str, name: str, body: str = "") -> str:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    d.mkdir(parents=True, exist_ok=True)
    eid = uniquify(slugify(name), lambda c: _entity_path(root, kind, c).exists())
    _entity_path(root, kind, eid).write_text(
        dump_frontmatter({"name": name}, body), encoding="utf-8"
    )
    return eid


def update_entity(
    root: Path, kind: str, eid: str, name: str | None = None, body: str | None = None
) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")


def delete_entity(root: Path, kind: str, eid: str) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    p.unlink()


def entity_hash(root: Path, kind: str, eid: str) -> str | None:
    p = _entity_path(root, kind, eid)
    if not p.exists():
        return None
    return hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def all_refs(root: Path) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    for kind in ENTITY_KINDS:
        d = _kind_dir(root, kind)
        if d.exists():
            for p in sorted(d.glob("*.md")):
                refs.append((kind, p.stem))
    return refs


def entity_counts(root: Path) -> dict[str, int]:
    counts: dict[str, int] = {}
    for kind in ENTITY_KINDS:
        d = _kind_dir(root, kind)
        counts[kind] = len(list(d.glob("*.md"))) if d.exists() else 0
    return counts
```

- [ ] **Step 4: Export from `__init__.py`** — add to the imports and `__all__`:

```python
from . import entities
from .entities import EntityNotFound, UnknownKind
```
(append `"entities"`, `"EntityNotFound"`, `"UnknownKind"` to `__all__`.)

- [ ] **Step 5: Run — expect PASS**

Run: `python -m pytest tests/test_entities_store.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/entities.py backend/src/grimoire/store/__init__.py backend/tests/test_entities_store.py
git commit -m "feat(store): generic entity CRUD + content hashing"
```

---

## Task 3: `worlds.py` — world meta CRUD + delete

**Files:**
- Create: `backend/src/grimoire/store/worlds.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_worlds_store.py`

**Interfaces:**
- Consumes: `paths`, `frontmatter`, `entities` (counts), `entities.all_refs`.
- Produces:
  - `worlds.WorldNotFound(Exception)`
  - `world_root(wid: str) -> Path`
  - `world_meta_path(wid: str) -> Path`
  - `list_worlds() -> list[dict]` → `{"id","name","created","updated","counts"}`, newest `updated` first
  - `create_world(name: str) -> str` (returns wid; **wid stable for life**)
  - `read_world(wid: str) -> dict` → `{"meta": {"id","name","created","updated"}, "body", "counts"}`
  - `rename_world(wid: str, name: str) -> None`
  - `delete_world(wid: str) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_worlds_store.py
import pytest

from grimoire.store import entities, worlds


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_create_list_read(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Drowned Realm")
    assert wid == "drowned-realm"
    entities.create_entity(worlds.world_root(wid), "characters", "Seraphine")
    listed = worlds.list_worlds()
    assert len(listed) == 1
    assert listed[0]["name"] == "Drowned Realm"
    assert listed[0]["counts"]["characters"] == 1
    w = worlds.read_world(wid)
    assert w["meta"]["id"] == wid
    assert w["counts"]["characters"] == 1


def test_rename_keeps_id(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Old")
    worlds.rename_world(wid, "New Name")
    assert worlds.read_world(wid)["meta"]["name"] == "New Name"  # id unchanged


def test_missing_world_raises(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        worlds.read_world("nope")
    with pytest.raises(worlds.WorldNotFound):
        worlds.rename_world("nope", "x")
    with pytest.raises(worlds.WorldNotFound):
        worlds.delete_world("nope")


def test_delete_removes_world(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Doomed")
    worlds.delete_world(wid)
    assert worlds.list_worlds() == []
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_worlds_store.py -v`

- [ ] **Step 3: Implement `worlds.py`**

```python
"""World meta CRUD. A world is a directory of entity kind-folders + world.md."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import entities
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home, now_iso, slugify, uniquify


class WorldNotFound(Exception):
    pass


def _worlds_dir() -> Path:
    return home() / "worlds"


def world_root(wid: str) -> Path:
    return _worlds_dir() / wid


def world_meta_path(wid: str) -> Path:
    return world_root(wid) / "world.md"


def list_worlds() -> list[dict]:
    ensure_home()
    out: list[dict] = []
    base = _worlds_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "world.md"
            if not d.is_dir() or not mp.exists():
                continue
            meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
            out.append({
                "id": d.name,
                "name": meta.get("name", d.name),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
                "counts": entities.entity_counts(d),
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def create_world(name: str) -> str:
    ensure_home()
    wid = uniquify(slugify(name), lambda c: world_root(c).exists())
    world_root(wid).mkdir(parents=True)
    now = now_iso()
    world_meta_path(wid).write_text(
        dump_frontmatter({"name": name, "created": now, "updated": now}, ""),
        encoding="utf-8",
    )
    return wid


def read_world(wid: str) -> dict:
    mp = world_meta_path(wid)
    if not mp.exists():
        raise WorldNotFound(wid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    return {"meta": {"id": wid, **meta}, "body": body, "counts": entities.entity_counts(world_root(wid))}


def rename_world(wid: str, name: str) -> None:
    mp = world_meta_path(wid)
    if not mp.exists():
        raise WorldNotFound(wid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["name"] = name
    meta["updated"] = now_iso()
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def delete_world(wid: str) -> None:
    root = world_root(wid)
    if not world_meta_path(wid).exists():
        raise WorldNotFound(wid)
    shutil.rmtree(root)
```

- [ ] **Step 4: Export from `__init__.py`** — add `from . import worlds` and `from .worlds import WorldNotFound`; append `"worlds"`, `"WorldNotFound"` to `__all__`.

- [ ] **Step 5: Run — expect PASS**

Run: `python -m pytest tests/test_worlds_store.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/worlds.py backend/src/grimoire/store/__init__.py backend/tests/test_worlds_store.py
git commit -m "feat(store): world meta CRUD"
```

---

## Task 4: `campaigns.py` — campaign meta CRUD + copy-on-create + manifest IO

**Files:**
- Create: `backend/src/grimoire/store/campaigns.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_campaigns_store.py`

**Interfaces:**
- Consumes: `paths`, `frontmatter`, `entities`, `worlds` (`world_root`, `world_meta_path`, `WorldNotFound`).
- Produces:
  - `campaigns.CampaignNotFound(Exception)`
  - `campaign_root(cid: str) -> Path`
  - `campaign_meta_path(cid: str) -> Path`
  - `list_campaigns() -> list[dict]` → `{"id","name","world","created","updated"}`, newest `updated` first
  - `create_campaign(name: str, world_id: str) -> str` — copy-on-create + writes `sync.md`; raises `worlds.WorldNotFound` if the world is missing
  - `read_campaign(cid: str) -> dict` → `{"meta": {"id","name","world","created","updated"}, "body"}`
  - `rename_campaign(cid: str, name: str) -> None`
  - `delete_campaign(cid: str) -> None`
  - `touch(cid: str) -> None` — bump `updated`
  - `read_manifest(cid: str) -> dict[str, str]`, `write_manifest(cid: str, manifest: dict[str, str]) -> None`

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_campaigns_store.py
import pytest

from grimoire.store import campaigns, entities, worlds


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def test_copy_on_create_copies_entities_and_writes_manifest(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    eid = entities.create_entity(worlds.world_root(wid), "characters", "Seraphine", "Keeper.")
    cid = campaigns.create_campaign("Run One", wid)
    # the entity was copied into the campaign verbatim
    copied = entities.read_entity(campaigns.campaign_root(cid), "characters", eid)
    assert copied["meta"]["name"] == "Seraphine"
    # the manifest base hash matches the world's current hash
    manifest = campaigns.read_manifest(cid)
    assert manifest["characters/seraphine"] == entities.entity_hash(worlds.world_root(wid), "characters", eid)


def test_create_against_missing_world_raises(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    with pytest.raises(worlds.WorldNotFound):
        campaigns.create_campaign("X", "no-such-world")


def test_empty_world_makes_empty_campaign(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("Empty")
    cid = campaigns.create_campaign("Run", wid)
    assert campaigns.read_manifest(cid) == {}
    assert (campaigns.campaign_root(cid) / "scenes").exists()


def test_list_read_rename_delete(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Old", wid)
    assert campaigns.list_campaigns()[0]["world"] == wid
    campaigns.rename_campaign(cid, "New")
    assert campaigns.read_campaign(cid)["meta"]["name"] == "New"  # id unchanged
    campaigns.delete_campaign(cid)
    assert campaigns.list_campaigns() == []
    with pytest.raises(campaigns.CampaignNotFound):
        campaigns.read_campaign(cid)


def test_manifest_roundtrip_with_slash_keys(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    campaigns.write_manifest(cid, {"characters/a": "deadbeef", "lore/salt-pact": "cafe"})
    assert campaigns.read_manifest(cid) == {"characters/a": "deadbeef", "lore/salt-pact": "cafe"}
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_campaigns_store.py -v`

- [ ] **Step 3: Implement `campaigns.py`**

```python
"""Campaign meta CRUD, copy-on-create from a world, and sync.md manifest IO."""

from __future__ import annotations

import shutil
from pathlib import Path

from . import entities, worlds
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import ensure_home, home, now_iso, slugify, uniquify


class CampaignNotFound(Exception):
    pass


def _campaigns_dir() -> Path:
    return home() / "campaigns"


def campaign_root(cid: str) -> Path:
    return _campaigns_dir() / cid


def campaign_meta_path(cid: str) -> Path:
    return campaign_root(cid) / "campaign.md"


def _manifest_path(cid: str) -> Path:
    return campaign_root(cid) / "sync.md"


def read_manifest(cid: str) -> dict[str, str]:
    p = _manifest_path(cid)
    if not p.exists():
        return {}
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta


def write_manifest(cid: str, manifest: dict[str, str]) -> None:
    _manifest_path(cid).write_text(dump_frontmatter(manifest, ""), encoding="utf-8")


def list_campaigns() -> list[dict]:
    ensure_home()
    out: list[dict] = []
    base = _campaigns_dir()
    if base.exists():
        for d in sorted(base.iterdir()):
            mp = d / "campaign.md"
            if not d.is_dir() or not mp.exists():
                continue
            meta, _ = parse_frontmatter(mp.read_text(encoding="utf-8"))
            out.append({
                "id": d.name,
                "name": meta.get("name", d.name),
                "world": meta.get("world", ""),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def create_campaign(name: str, world_id: str) -> str:
    ensure_home()
    if not worlds.world_meta_path(world_id).exists():
        raise worlds.WorldNotFound(world_id)
    cid = uniquify(slugify(name), lambda c: campaign_root(c).exists())
    root = campaign_root(cid)
    root.mkdir(parents=True)
    (root / "scenes").mkdir()
    now = now_iso()
    campaign_meta_path(cid).write_text(
        dump_frontmatter({"name": name, "world": world_id, "created": now, "updated": now}, ""),
        encoding="utf-8",
    )
    # copy-on-create: deep-copy world entities + record base hashes
    wroot = worlds.world_root(world_id)
    manifest: dict[str, str] = {}
    for kind, eid in entities.all_refs(wroot):
        src = wroot / kind / f"{eid}.md"
        dst_dir = root / kind
        dst_dir.mkdir(parents=True, exist_ok=True)
        (dst_dir / f"{eid}.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        manifest[f"{kind}/{eid}"] = entities.entity_hash(wroot, kind, eid)
    write_manifest(cid, manifest)
    return cid


def read_campaign(cid: str) -> dict:
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    return {"meta": {"id": cid, **meta}, "body": body}


def rename_campaign(cid: str, name: str) -> None:
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["name"] = name
    meta["updated"] = now_iso()
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def touch(cid: str) -> None:
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["updated"] = now_iso()
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def delete_campaign(cid: str) -> None:
    root = campaign_root(cid)
    if not campaign_meta_path(cid).exists():
        raise CampaignNotFound(cid)
    shutil.rmtree(root)
```

- [ ] **Step 4: Export from `__init__.py`** — add `from . import campaigns` and `from .campaigns import CampaignNotFound`; append `"campaigns"`, `"CampaignNotFound"` to `__all__`.

- [ ] **Step 5: Run — expect PASS**

Run: `python -m pytest tests/test_campaigns_store.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/campaigns.py backend/src/grimoire/store/__init__.py backend/tests/test_campaigns_store.py
git commit -m "feat(store): campaign meta CRUD + copy-on-create + sync manifest IO"
```

---

## Task 5: `sync.py` — the push/sync engine (critical)

**Files:**
- Create: `backend/src/grimoire/store/sync.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_sync_store.py`

**Interfaces:**
- Consumes: `campaigns` (`campaign_root`, `campaign_meta_path`, `read_campaign`, `read_manifest`, `write_manifest`, `touch`, `list_campaigns`, `CampaignNotFound`), `worlds` (`world_root`), `entities` (`entity_hash`, `read_entity`, `all_refs`, `ENTITY_KINDS`).
- Produces:
  - `incoming(cid: str) -> list[dict]` — each item `{"ref": {"kind","id"}, "status": "new"|"update"|"conflict", "world": {"name","body"}, "mine"?: {"name","body"}}`; raises `campaigns.CampaignNotFound` if missing.
  - `accept(cid: str, refs: list[dict]) -> None` — each ref `{"kind","id"}`; copies world content + advances base; idempotent no-op for non-pending refs; bumps campaign `updated`.
  - `reject(cid: str, refs: list[dict]) -> None` — advances base only (keep mine).
  - `campaigns_for_world(wid: str) -> list[dict]` — `{"id","name","pending": {"new","update","conflict"}}` per campaign using `wid`.

- [ ] **Step 1: Write the failing tests** (this is the heart — cover every row of the status table and the no-nag guarantee)

```python
# backend/tests/test_sync_store.py
import pytest

from grimoire.store import campaigns, entities, sync, worlds


def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))


def _setup(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    wid = worlds.create_world("W")
    entities.create_entity(worlds.world_root(wid), "characters", "Seraphine", "v1")
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid


def test_clean_campaign_has_no_incoming(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    assert sync.incoming(cid) == []  # base == world, nothing to offer


def test_world_adds_new_entity(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "locations", "Library", "halls")
    pend = sync.incoming(cid)
    assert len(pend) == 1
    assert pend[0]["ref"] == {"kind": "locations", "id": "library"}
    assert pend[0]["status"] == "new"
    assert "mine" not in pend[0]


def test_world_update_unmodified_local_is_update(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "characters", "seraphine", body="v2")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["update"]
    assert pend[0]["world"]["body"].strip() == "v2"
    assert pend[0]["mine"]["body"].strip() == "v1"


def test_both_changed_is_conflict(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "characters", "seraphine", body="world-edit")
    entities.update_entity(campaigns.campaign_root(cid), "characters", "seraphine", body="my-edit")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["conflict"]
    assert pend[0]["world"]["body"].strip() == "world-edit"
    assert pend[0]["mine"]["body"].strip() == "my-edit"


def test_local_only_change_is_not_offered(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(campaigns.campaign_root(cid), "characters", "seraphine", body="mine")
    assert sync.incoming(cid) == []  # world unchanged → nothing incoming


def test_accept_copies_and_clears(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "characters", "seraphine", body="v2")
    sync.accept(cid, [{"kind": "characters", "id": "seraphine"}])
    assert sync.incoming(cid) == []
    mine = entities.read_entity(campaigns.campaign_root(cid), "characters", "seraphine")
    assert mine["body"].strip() == "v2"


def test_accept_new_creates_file(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "the pact")
    sync.accept(cid, [{"kind": "lore", "id": "salt-pact"}])
    assert sync.incoming(cid) == []
    assert entities.read_entity(campaigns.campaign_root(cid), "lore", "salt-pact")["body"].strip() == "the pact"


def test_reject_keeps_mine_and_does_not_renag(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "characters", "seraphine", body="v2")
    sync.reject(cid, [{"kind": "characters", "id": "seraphine"}])
    # mine is untouched, and the change is no longer offered
    assert entities.read_entity(campaigns.campaign_root(cid), "characters", "seraphine")["body"].strip() == "v1"
    assert sync.incoming(cid) == []
    # but a FURTHER world change re-surfaces it
    entities.update_entity(worlds.world_root(wid), "characters", "seraphine", body="v3")
    assert [p["status"] for p in sync.incoming(cid)] == ["update"]


def test_reject_new_stays_absent_and_quiet(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "lore", "Salt Pact", "x")
    sync.reject(cid, [{"kind": "lore", "id": "salt-pact"}])
    assert sync.incoming(cid) == []
    with pytest.raises(entities.EntityNotFound):
        entities.read_entity(campaigns.campaign_root(cid), "lore", "salt-pact")


def test_accept_nonpending_is_noop(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    sync.accept(cid, [{"kind": "characters", "id": "ghost"}])  # not in world
    assert sync.incoming(cid) == []


def test_campaigns_for_world_counts(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.update_entity(worlds.world_root(wid), "characters", "seraphine", body="v2")
    entities.create_entity(worlds.world_root(wid), "lore", "Pact", "p")
    rows = sync.campaigns_for_world(wid)
    assert len(rows) == 1
    assert rows[0]["id"] == cid
    assert rows[0]["pending"] == {"new": 1, "update": 1, "conflict": 0}


def test_incoming_missing_campaign_raises(monkeypatch, tmp_path):
    home(monkeypatch, tmp_path)
    with pytest.raises(campaigns.CampaignNotFound):
        sync.incoming("nope")
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_sync_store.py -v`

- [ ] **Step 3: Implement `sync.py`**

```python
"""The push/sync engine: per-campaign incoming changes + accept/reject.

Compares three content hashes per ref (kind, id):
  world = world entity's current hash (or None)
  base  = campaign sync.md[ref]        (or None)
  mine  = campaign entity's current hash (or None)
An incoming change exists iff world is not None and world != base.
"""

from __future__ import annotations

from pathlib import Path

from . import campaigns, entities, worlds


def _world_id(cid: str) -> str:
    return campaigns.read_campaign(cid)["meta"].get("world", "")


def _ref_str(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _entity_blob(root: Path, kind: str, eid: str) -> dict:
    e = entities.read_entity(root, kind, eid)
    return {"name": e["meta"].get("name", eid), "body": e["body"]}


def incoming(cid: str) -> list[dict]:
    wid = _world_id(cid)  # raises CampaignNotFound if the campaign is missing
    wroot = worlds.world_root(wid)
    croot = campaigns.campaign_root(cid)
    manifest = campaigns.read_manifest(cid)

    refs: set[str] = set(manifest)
    if wroot.exists():
        refs |= {_ref_str(k, e) for k, e in entities.all_refs(wroot)}
    refs |= {_ref_str(k, e) for k, e in entities.all_refs(croot)}

    out: list[dict] = []
    for ref in sorted(refs):
        kind, _, eid = ref.partition("/")
        if kind not in entities.ENTITY_KINDS:
            continue
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        base_h = manifest.get(ref)
        if world_h is None or world_h == base_h:
            continue  # no incoming change (incl. world-side deletions, skipped)
        mine_h = entities.entity_hash(croot, kind, eid)
        if mine_h is None:
            status = "new"
        elif mine_h == base_h:
            status = "update"
        else:
            status = "conflict"
        item: dict = {"ref": {"kind": kind, "id": eid}, "status": status,
                      "world": _entity_blob(wroot, kind, eid)}
        if mine_h is not None:
            item["mine"] = _entity_blob(croot, kind, eid)
        out.append(item)
    return out


def _advance(cid: str, refs: list[dict], *, copy: bool) -> None:
    wid = _world_id(cid)
    wroot = worlds.world_root(wid)
    croot = campaigns.campaign_root(cid)
    manifest = campaigns.read_manifest(cid)
    changed = False
    for ref in refs:
        kind, eid = ref["kind"], ref["id"]
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        if world_h is None:
            continue  # nothing to accept/reject for this ref
        if copy:
            src = wroot / kind / f"{eid}.md"
            dst_dir = croot / kind
            dst_dir.mkdir(parents=True, exist_ok=True)
            (dst_dir / f"{eid}.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        manifest[_ref_str(kind, eid)] = world_h
        changed = True
    if changed:
        campaigns.write_manifest(cid, manifest)
        campaigns.touch(cid)


def accept(cid: str, refs: list[dict]) -> None:
    _advance(cid, refs, copy=True)


def reject(cid: str, refs: list[dict]) -> None:
    _advance(cid, refs, copy=False)


def campaigns_for_world(wid: str) -> list[dict]:
    out: list[dict] = []
    for c in campaigns.list_campaigns():
        if c.get("world") != wid:
            continue
        counts = {"new": 0, "update": 0, "conflict": 0}
        for p in incoming(c["id"]):
            counts[p["status"]] += 1
        out.append({"id": c["id"], "name": c["name"], "pending": counts})
    return out
```

- [ ] **Step 4: Export from `__init__.py`** — add `from . import sync`; append `"sync"` to `__all__`.

- [ ] **Step 5: Run — expect PASS**

Run: `python -m pytest tests/test_sync_store.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/sync.py backend/src/grimoire/store/__init__.py backend/tests/test_sync_store.py
git commit -m "feat(store): push/sync engine (incoming/accept/reject)"
```

---

## Task 6: `scenes.py` — re-home conversations under a campaign

**Files:**
- Create: `backend/src/grimoire/store/scenes.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Consumes: `paths`, `frontmatter`, `config.read_config`, `campaigns` (`campaign_root`, `campaign_meta_path`, `CampaignNotFound`).
- Produces:
  - `scenes.SceneNotFound(Exception)`
  - `create_scene(cid: str, title: str) -> str` (id `YYYY-MM-DD-<slug>`; raises `campaigns.CampaignNotFound` if campaign missing)
  - `list_scenes(cid: str) -> list[dict]` → `{"id","title","model","created","updated"}`, newest `updated` first
  - `read_scene(cid: str, sid: str) -> dict` → `{"meta": {"id", ...}, "messages": [{"role","content"}]}`
  - `rename_scene(cid: str, sid: str, title: str) -> str` (preserves date prefix; renames file; returns new sid)
  - `delete_scene(cid: str, sid: str) -> None`
  - `append_message(cid: str, sid: str, role: str, content: str) -> None`

- [ ] **Step 1: Write the failing tests** (port of `test_conversation_store.py`, now campaign-scoped)

```python
# backend/tests/test_scene_store.py
import pytest

from grimoire.store import campaigns, scenes, worlds


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    return campaigns.create_campaign("Run", wid)


def test_create_list_and_read_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "My First Scene")
    assert sid.endswith("my-first-scene")
    metas = scenes.list_scenes(cid)
    assert len(metas) == 1 and metas[0]["id"] == sid and metas[0]["title"] == "My First Scene"
    assert scenes.read_scene(cid, sid)["messages"] == []


def test_append_and_parse_roundtrip(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Roundtrip")
    scenes.append_message(cid, sid, "user", "Describe the keeper.\n\n**Not a real marker** still mine.")
    scenes.append_message(cid, sid, "assistant", "She is older than the salt.")
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "user", "content": "Describe the keeper.\n\n**Not a real marker** still mine."},
        {"role": "assistant", "content": "She is older than the salt."},
    ]


def test_unknown_scene_raises(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.read_scene(cid, "nope")


def test_create_in_missing_campaign_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    with pytest.raises(campaigns.CampaignNotFound):
        scenes.create_scene("no-campaign", "X")


def test_rename_changes_id_keeps_order(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Old Title")
    scenes.append_message(cid, sid, "user", "keep me")
    before = scenes.list_scenes(cid)[0]["updated"]
    new_sid = scenes.rename_scene(cid, sid, "Shiny New Name")
    assert new_sid != sid and new_sid.endswith("shiny-new-name")
    metas = scenes.list_scenes(cid)
    assert len(metas) == 1 and metas[0]["id"] == new_sid and metas[0]["updated"] == before
    assert scenes.read_scene(cid, new_sid)["messages"] == [{"role": "user", "content": "keep me"}]
    with pytest.raises(scenes.SceneNotFound):
        scenes.read_scene(cid, sid)


def test_delete_removes_scene(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "Doomed")
    scenes.delete_scene(cid, sid)
    assert scenes.list_scenes(cid) == []
    with pytest.raises(scenes.SceneNotFound):
        scenes.delete_scene(cid, sid)
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_scene_store.py -v`

- [ ] **Step 3: Implement `scenes.py`** (the old conversation logic, scoped to `<campaign>/scenes/`)

```python
"""Scene CRUD — chat transcripts living under <campaign>/scenes/."""

from __future__ import annotations

import re
from pathlib import Path

from . import campaigns
from .config import read_config
from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import now_iso, slugify, uniquify

ROLE_TO_LABEL = {"user": "You", "assistant": "Grimoire"}
LABEL_TO_ROLE = {"You": "user", "Grimoire": "assistant"}
_MARKER = re.compile(r"^\*\*(You|Grimoire):\*\*[ ]?", re.MULTILINE)


class SceneNotFound(Exception):
    pass


def _scenes_dir(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "scenes"


def _scene_path(cid: str, sid: str) -> Path:
    return _scenes_dir(cid) / f"{sid}.md"


def _require_campaign(cid: str) -> None:
    if not campaigns.campaign_meta_path(cid).exists():
        raise campaigns.CampaignNotFound(cid)


def create_scene(cid: str, title: str) -> str:
    _require_campaign(cid)
    d = _scenes_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    now = now_iso()
    base = f"{now[:10]}-{slugify(title)}"
    sid = uniquify(base, lambda c: _scene_path(cid, c).exists())
    meta = {"title": title, "model": read_config()["model"], "created": now, "updated": now}
    _scene_path(cid, sid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    return sid


def list_scenes(cid: str) -> list[dict]:
    _require_campaign(cid)
    out: list[dict] = []
    d = _scenes_dir(cid)
    if d.exists():
        for p in d.glob("*.md"):
            meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
            out.append({
                "id": p.stem,
                "title": meta.get("title", p.stem),
                "model": meta.get("model", ""),
                "created": meta.get("created", ""),
                "updated": meta.get("updated", ""),
            })
    out.sort(key=lambda m: m["updated"], reverse=True)
    return out


def _parse_messages(body: str) -> list[dict]:
    matches = list(_MARKER.finditer(body))
    messages = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        messages.append({"role": LABEL_TO_ROLE[m.group(1)], "content": body[start:end].strip()})
    return messages


def read_scene(cid: str, sid: str) -> dict:
    p = _scene_path(cid, sid)
    if not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {"meta": {"id": sid, **meta}, "messages": _parse_messages(body)}


def rename_scene(cid: str, sid: str, title: str) -> str:
    p = _scene_path(cid, sid)
    if not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["title"] = title
    prefix = meta.get("created", now_iso())[:10]
    new_sid = uniquify(
        f"{prefix}-{slugify(title)}",
        lambda c: c != sid and _scene_path(cid, c).exists(),
    )
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    if new_sid != sid:
        p.rename(_scene_path(cid, new_sid))
    return new_sid


def delete_scene(cid: str, sid: str) -> None:
    p = _scene_path(cid, sid)
    if not p.exists():
        raise SceneNotFound(sid)
    p.unlink()


def append_message(cid: str, sid: str, role: str, content: str) -> None:
    p = _scene_path(cid, sid)
    if not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    block = f"**{ROLE_TO_LABEL[role]}:** {content.strip()}\n"
    body = (body.rstrip() + "\n\n" + block) if body.strip() else block
    meta["updated"] = now_iso()
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

Note: `rename_scene`'s `uniquify` predicate excludes the current `sid`, so renaming to the same title is a no-op (matches the old conversation behavior).

- [ ] **Step 4: Export from `__init__.py`** — add `from . import scenes` and `from .scenes import SceneNotFound`; append `"scenes"`, `"SceneNotFound"` to `__all__`.

- [ ] **Step 5: Run — expect PASS**

Run: `python -m pytest tests/test_scene_store.py -v`

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/src/grimoire/store/__init__.py backend/tests/test_scene_store.py
git commit -m "feat(store): scenes re-homed under campaigns"
```

---

## Task 7: `routes.py` — new HTTP surface

Rewrite the router: worlds, campaigns, generic entity CRUD (shared path handler for world and campaign), sync (incoming/accept/reject + world push view), and scenes (the old chat/retry SSE, re-homed). The old `/api/conversations*` routes are removed. The SSE streaming helpers (`_chat_stream`, `_require_key`) are preserved.

**Files:**
- Modify (rewrite): `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py` (rewrite)

**Interfaces:**
- Consumes: everything from Tasks 2–6 via `from grimoire import store` then `store.worlds`, `store.campaigns`, `store.entities`, `store.sync`, `store.scenes`, `store.read_config`; `OpenRouterClient`/`OpenRouterError`; `get_openrouter` (unchanged — keep the same function object so `main`'s `dependency_overrides` key still matches).
- Produces: the HTTP surface from the spec.

- [ ] **Step 1: Write the failing tests**

```python
# backend/tests/test_routes.py
import importlib
import json

import pytest
from fastapi.testclient import TestClient

import grimoire.store as store
from grimoire import routes
from grimoire.main import create_app


class FakeOpenRouter:
    def __init__(self, deltas):
        self.deltas = deltas

    async def stream(self, messages, model, key):
        for d in self.deltas:
            yield d


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    importlib.reload(store)
    app = create_app()
    app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouter(["Hel", "lo"])
    return TestClient(app)


def _world(client, name="W"):
    return client.post("/api/worlds", json={"name": name}).json()["id"]


def _campaign(client, name="Run"):
    wid = _world(client)
    return wid, client.post("/api/campaigns", json={"name": name, "world": wid}).json()["id"]


# ---- config (unchanged behavior) ----
def test_config_never_leaks_key(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    body = client.get("/api/config").json()
    assert body["key_set"] is True
    assert "sk-or-secret" not in json.dumps(body)


# ---- worlds ----
def test_world_crud(client):
    wid = _world(client, "Drowned Realm")
    assert wid == "drowned-realm"
    assert [w["id"] for w in client.get("/api/worlds").json()] == [wid]
    client.put(f"/api/worlds/{wid}", json={"name": "Renamed"})
    assert client.get(f"/api/worlds/{wid}").json()["meta"]["name"] == "Renamed"
    assert client.delete(f"/api/worlds/{wid}").status_code == 200
    assert client.get("/api/worlds").json() == []


def test_world_entity_crud(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "body": "Keeper"}).json()["id"]
    assert eid == "seraphine"
    assert [e["id"] for e in client.get(f"/api/worlds/{wid}/characters").json()] == [eid]
    client.put(f"/api/worlds/{wid}/characters/{eid}", json={"body": "Updated"})
    assert client.get(f"/api/worlds/{wid}/characters/{eid}").json()["body"].strip() == "Updated"
    assert client.delete(f"/api/worlds/{wid}/characters/{eid}").status_code == 200


def test_unknown_kind_404(client):
    wid = _world(client)
    assert client.get(f"/api/worlds/{wid}/weapons").status_code == 404


# ---- campaigns ----
def test_campaign_create_copies_world(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "body": "Keeper"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/characters/seraphine").json()["body"].strip() == "Keeper"


def test_campaign_missing_world_400(client):
    assert client.post("/api/campaigns", json={"name": "X", "world": "nope"}).status_code == 400


# ---- sync ----
def test_incoming_and_accept_flow(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/lore", json={"name": "Salt Pact", "body": "pact"})
    pend = client.get(f"/api/campaigns/{cid}/incoming").json()
    assert [p["status"] for p in pend] == ["new"]
    client.post(f"/api/campaigns/{cid}/incoming/accept", json={"refs": [{"kind": "lore", "id": "salt-pact"}]})
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []
    assert client.get(f"/api/campaigns/{cid}/lore/salt-pact").json()["body"].strip() == "pact"


def test_reject_flow(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/lore", json={"name": "Pact", "body": "p"})
    client.post(f"/api/campaigns/{cid}/incoming/reject", json={"refs": [{"kind": "lore", "id": "pact"}]})
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []
    assert client.get(f"/api/campaigns/{cid}/lore/pact").status_code == 404


def test_world_push_view(client):
    wid = _world(client)
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    client.post(f"/api/worlds/{wid}/characters", json={"name": "A", "body": "a"})
    rows = client.get(f"/api/worlds/{wid}/campaigns").json()
    assert rows == [{"id": cid, "name": "Run", "pending": {"new": 1, "update": 0, "conflict": 0}}]


# ---- scenes (re-homed chat) ----
def test_chat_missing_key_returns_409(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})
    assert resp.status_code == 409 and resp.json()["kind"] == "missing_key"


def test_chat_streams_and_persists(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/chat", json={"content": "hi"})
    assert resp.status_code == 200
    assert 'data: {"delta": "Hel"}' in resp.text
    assert 'data: {"done": true}' in resp.text
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert msgs[-1] == {"role": "assistant", "content": "Hello"}


def test_retry_regenerates_without_adding_a_user_turn(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-secret"})
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "T"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "hi")
    resp = client.post(f"/api/campaigns/{cid}/scenes/{sid}/retry")
    assert resp.status_code == 200
    msgs = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_scene_rename_and_delete(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Old"}).json()["id"]
    new_id = client.put(f"/api/campaigns/{cid}/scenes/{sid}", json={"title": "New Name"}).json()["id"]
    assert new_id.endswith("new-name")
    assert client.put(f"/api/campaigns/{cid}/scenes/{new_id}", json={"title": "  "}).status_code == 400
    assert client.delete(f"/api/campaigns/{cid}/scenes/{new_id}").status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes").json() == []


def test_scene_missing_404(client):
    _wid, cid = _campaign(client)
    assert client.delete(f"/api/campaigns/{cid}/scenes/nope").status_code == 404
```

- [ ] **Step 2: Run — expect FAIL**

Run: `python -m pytest tests/test_routes.py -v`

- [ ] **Step 3: Implement the rewritten `routes.py`**

```python
"""HTTP surface for grimoire."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from . import store
from .openrouter import OpenRouterClient, OpenRouterError

router = APIRouter()
_openrouter = OpenRouterClient()


def get_openrouter() -> OpenRouterClient:
    return _openrouter


# ---- models ----
class ConfigUpdate(BaseModel):
    model: str | None = None
    theme: str | None = None
    openrouter_key: str | None = None


class NameBody(BaseModel):
    name: str


class NewCampaign(BaseModel):
    name: str
    world: str


class EntityCreate(BaseModel):
    name: str
    body: str = ""


class EntityUpdate(BaseModel):
    name: str | None = None
    body: str | None = None


class Ref(BaseModel):
    kind: str
    id: str


class RefList(BaseModel):
    refs: list[Ref]


class NewScene(BaseModel):
    title: str | None = None


class RenameScene(BaseModel):
    title: str


class ChatTurn(BaseModel):
    content: str


# ---- config ----
def _public_config(cfg: dict[str, str]) -> dict:
    return {"model": cfg["model"], "theme": cfg["theme"], "key_set": bool(cfg["openrouter_key"])}


@router.get("/config")
def get_config():
    return _public_config(store.read_config())


@router.put("/config")
def put_config(update: ConfigUpdate):
    fields = {k: v for k, v in update.model_dump().items() if v is not None}
    return _public_config(store.write_config(**fields))


# ---- worlds ----
@router.get("/worlds")
def get_worlds():
    return store.worlds.list_worlds()


@router.post("/worlds")
def post_world(body: NameBody):
    return {"id": store.worlds.create_world(body.name)}


@router.get("/worlds/{wid}")
def get_world(wid: str):
    try:
        return store.worlds.read_world(wid)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")


@router.put("/worlds/{wid}")
def put_world(wid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        store.worlds.rename_world(wid, name)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")
    return {"id": wid, "name": name}


@router.delete("/worlds/{wid}")
def delete_world(wid: str):
    try:
        store.worlds.delete_world(wid)
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=404, detail="world not found")
    return {"ok": True}


@router.get("/worlds/{wid}/campaigns")
def get_world_campaigns(wid: str):
    return store.sync.campaigns_for_world(wid)


# ---- generic entity CRUD (shared by worlds and campaigns) ----
def _world_root_or_404(wid: str):
    if not store.worlds.world_meta_path(wid).exists():
        raise HTTPException(status_code=404, detail="world not found")
    return store.worlds.world_root(wid)


def _campaign_root_or_404(cid: str):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    return store.campaigns.campaign_root(cid)


def _entity_list(root, kind: str):
    try:
        return store.entities.list_entities(root, kind)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _entity_create(root, kind: str, body: EntityCreate):
    try:
        return {"id": store.entities.create_entity(root, kind, body.name, body.body)}
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")


def _entity_read(root, kind: str, eid: str):
    try:
        return store.entities.read_entity(root, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")


def _entity_update(root, kind: str, eid: str, body: EntityUpdate):
    try:
        store.entities.update_entity(root, kind, eid, name=body.name, body=body.body)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


def _entity_delete(root, kind: str, eid: str):
    try:
        store.entities.delete_entity(root, kind, eid)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}


@router.get("/worlds/{wid}/{kind}")
def get_world_entities(wid: str, kind: str):
    return _entity_list(_world_root_or_404(wid), kind)


@router.post("/worlds/{wid}/{kind}")
def post_world_entity(wid: str, kind: str, body: EntityCreate):
    return _entity_create(_world_root_or_404(wid), kind, body)


@router.get("/worlds/{wid}/{kind}/{eid}")
def get_world_entity(wid: str, kind: str, eid: str):
    return _entity_read(_world_root_or_404(wid), kind, eid)


@router.put("/worlds/{wid}/{kind}/{eid}")
def put_world_entity(wid: str, kind: str, eid: str, body: EntityUpdate):
    return _entity_update(_world_root_or_404(wid), kind, eid, body)


@router.delete("/worlds/{wid}/{kind}/{eid}")
def delete_world_entity(wid: str, kind: str, eid: str):
    return _entity_delete(_world_root_or_404(wid), kind, eid)


# ---- campaigns ----
@router.get("/campaigns")
def get_campaigns():
    return store.campaigns.list_campaigns()


@router.post("/campaigns")
def post_campaign(body: NewCampaign):
    try:
        return {"id": store.campaigns.create_campaign(body.name, body.world)}
    except store.worlds.WorldNotFound:
        raise HTTPException(status_code=400, detail="world not found")


@router.get("/campaigns/{cid}")
def get_campaign(cid: str):
    try:
        return store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.put("/campaigns/{cid}")
def put_campaign(cid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    try:
        store.campaigns.rename_campaign(cid, name)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"id": cid, "name": name}


@router.delete("/campaigns/{cid}")
def delete_campaign(cid: str):
    try:
        store.campaigns.delete_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


# ---- campaign sync ----
@router.get("/campaigns/{cid}/incoming")
def get_incoming(cid: str):
    try:
        return store.sync.incoming(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/incoming/accept")
def post_accept(cid: str, body: RefList):
    try:
        store.sync.accept(cid, [r.model_dump() for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


@router.post("/campaigns/{cid}/incoming/reject")
def post_reject(cid: str, body: RefList):
    try:
        store.sync.reject(cid, [r.model_dump() for r in body.refs])
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    return {"ok": True}


# ---- campaign entity CRUD ----
@router.get("/campaigns/{cid}/{kind}")
def get_campaign_entities(cid: str, kind: str):
    return _entity_list(_campaign_root_or_404(cid), kind)


@router.post("/campaigns/{cid}/{kind}")
def post_campaign_entity(cid: str, kind: str, body: EntityCreate):
    return _entity_create(_campaign_root_or_404(cid), kind, body)


@router.get("/campaigns/{cid}/{kind}/{eid}")
def get_campaign_entity(cid: str, kind: str, eid: str):
    return _entity_read(_campaign_root_or_404(cid), kind, eid)


@router.put("/campaigns/{cid}/{kind}/{eid}")
def put_campaign_entity(cid: str, kind: str, eid: str, body: EntityUpdate):
    return _entity_update(_campaign_root_or_404(cid), kind, eid, body)


@router.delete("/campaigns/{cid}/{kind}/{eid}")
def delete_campaign_entity(cid: str, kind: str, eid: str):
    return _entity_delete(_campaign_root_or_404(cid), kind, eid)


# ---- scenes ----
def _require_key(cfg: dict[str, str]) -> None:
    if not cfg["openrouter_key"]:
        raise HTTPException(
            status_code=409,
            detail={"detail": "OpenRouter key not set", "kind": "missing_key"},
        )


def _chat_stream(cid: str, sid: str, messages: list[dict], cfg: dict, client: OpenRouterClient):
    async def event_stream():
        parts: list[str] = []
        try:
            async for delta in client.stream(messages, cfg["model"], cfg["openrouter_key"]):
                parts.append(delta)
                yield f"data: {json.dumps({'delta': delta})}\n\n"
            store.scenes.append_message(cid, sid, "assistant", "".join(parts))
            yield f"data: {json.dumps({'done': True})}\n\n"
        except OpenRouterError as exc:
            if parts:
                store.scenes.append_message(cid, sid, "assistant", "".join(parts))
            yield f"data: {json.dumps({'error': {'detail': exc.detail, 'kind': exc.kind}})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/campaigns/{cid}/scenes")
def get_scenes(cid: str):
    try:
        return store.scenes.list_scenes(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.post("/campaigns/{cid}/scenes")
def post_scene(cid: str, body: NewScene):
    title = body.title or "New scene"
    try:
        return {"id": store.scenes.create_scene(cid, title)}
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")


@router.get("/campaigns/{cid}/scenes/{sid}")
def get_scene(cid: str, sid: str):
    try:
        return store.scenes.read_scene(cid, sid)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")


@router.put("/campaigns/{cid}/scenes/{sid}")
def put_scene(cid: str, sid: str, body: RenameScene):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    try:
        new_sid = store.scenes.rename_scene(cid, sid, title)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")
    return {"id": new_sid, "title": title}


@router.delete("/campaigns/{cid}/scenes/{sid}")
def delete_scene(cid: str, sid: str):
    try:
        store.scenes.delete_scene(cid, sid)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")
    return {"ok": True}


def _require_scene(cid: str, sid: str) -> dict:
    try:
        return store.scenes.read_scene(cid, sid)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")


@router.post("/campaigns/{cid}/scenes/{sid}/chat")
def post_chat(cid: str, sid: str, turn: ChatTurn, client: OpenRouterClient = Depends(get_openrouter)):
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    store.scenes.append_message(cid, sid, "user", turn.content)
    messages = [{"role": m["role"], "content": m["content"]} for m in scene["messages"]]
    messages.append({"role": "user", "content": turn.content})
    return _chat_stream(cid, sid, messages, cfg, client)


@router.post("/campaigns/{cid}/scenes/{sid}/retry")
def post_retry(cid: str, sid: str, client: OpenRouterClient = Depends(get_openrouter)):
    scene = _require_scene(cid, sid)
    cfg = store.read_config()
    _require_key(cfg)
    messages = [{"role": m["role"], "content": m["content"]} for m in scene["messages"]]
    if not messages:
        raise HTTPException(status_code=400, detail="nothing to retry")
    return _chat_stream(cid, sid, messages, cfg, client)
```

**Route-ordering note:** FastAPI matches routes in declaration order. The literal `GET /campaigns/{cid}/incoming` and `/campaigns/{cid}/scenes...` routes are declared **before** the generic `GET /campaigns/{cid}/{kind}` so that `incoming`/`scenes` are never captured as a `kind`. Keep this ordering. (`incoming` would also 404 as an unknown kind, but the explicit ordering avoids surprises and keeps POST `/incoming/accept` reachable.)

- [ ] **Step 4: Run the full backend suite — expect PASS**

Run (from `backend/`): `python -m pytest -v`
Expected: all of `test_frontmatter`, `test_config_store`, `test_entities_store`, `test_worlds_store`, `test_campaigns_store`, `test_sync_store`, `test_scene_store`, `test_routes`, `test_openrouter` PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): worlds, campaigns, entities, sync, scenes; drop conversations"
```

---

## Self-Review

**Spec coverage:**
- Data layout (`worlds/`, `campaigns/`, entities, `sync.md`, `scenes/`) → Tasks 1–6. ✓
- Generic content-only entities, stable ids, kind allowlist → Task 2. ✓
- `sync.md` reuses frontmatter, `kind/id` keys → Task 4 (`read/write_manifest`, slash-key test). ✓
- Sync table (new/update/conflict/nothing) + accept/reject + no-nag + world-deletions-skipped → Task 5. ✓
- Copy-on-create writes matching base hashes; empty world → empty campaign → Task 4. ✓
- Push view (`/worlds/{wid}/campaigns`) → Task 5 `campaigns_for_world` + Task 7 route. ✓
- Scenes re-homed; old conversations removed → Tasks 1 & 6 & 7. ✓
- Error handling (404 world/campaign/entity/scene/unknown-kind; 400 missing-world/blank-name; idempotent accept/reject; missing `sync.md` = empty; chat 409) → Tasks 2–7. ✓
- No migration; `/api/conversations*` removed → Tasks 1 & 7. ✓
- One world per campaign; deleting a world leaves campaign copies intact (campaigns are independent dirs; `delete_world` only `rmtree`s the world) → Tasks 3 & 4. ✓

**Placeholder scan:** none — every code/test step is complete.

**Type consistency:** ref shape `{"kind","id"}` is consistent across `sync.incoming`/`accept`/`reject`, the `Ref`/`RefList` models, and tests. `entity_hash` returns `str | None` and every call site guards `None`. `world_meta_path`/`campaign_meta_path` are the existence checks used by routes and `create_campaign`. `__init__.py` re-exports accumulate across Tasks 1–6 (each task appends its module + exceptions).

## Out of scope (separate plans)

- **Frontend Phase 2** (nav; worlds & campaigns CRUD; create-campaign-from-world; scene chat re-homed end-to-end) — its own plan after this lands.
- **Frontend Phase 3** (shared entity editor; `IncomingReview` accept/reject + conflict diff; world push panel) — its own plan.
- Deferred per spec: entity schema, prompt injection, deletion propagation, multi-world composition, sub-field conflict resolution.
