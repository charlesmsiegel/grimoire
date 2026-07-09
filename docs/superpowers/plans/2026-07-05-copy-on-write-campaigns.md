# Copy-on-Write Campaigns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Campaigns stop deep-copying their world; a record is materialized into the
campaign only when it diverges (edit, version lock, delete, campaign-local create) and
everything else reads through to the world live. Spec:
`docs/superpowers/specs/2026-07-05-copy-on-write-campaigns-design.md`.

**Architecture:** A new `store/overlay.py` implements campaign-else-world resolution
(flat records per file, actors per dir keyed on `character.md`/`pc.md`, assets per
file), tombstones in `<campaign>/deleted.json`, and materialize-on-write that records
sync bases. Campaign routes and campaign-reading store modules switch to overlay calls
(behavior-neutral while campaigns are still fat), then `create_campaign` goes thin,
sync shrinks to materialized records, a lazy `ensure_campaign_slim` migrates existing
campaigns, and world deletion is blocked while campaigns reference the world.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend). No new
dependencies (stdlib `filecmp` for the slim pass).

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`
  (route tests get it from the existing `client` fixture).
- Run backend tests from the repo root: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Run frontend tests **from `frontend/`**: `npx vitest run` and `npx tsc -b`. Never
  `npx --prefix frontend vitest run`.
- The 07-04 lock invariants are preserved: a locked actor's campaign dir is
  authoritative and holds exactly the picked version; purged versions must never
  resurrect through the overlay (whole-dir resolution, never a union of version files).
- `dossier.md` / `state.md` / `tagline.md` live *inside* `croot/characters/<id>/`;
  their presence must NOT count as materialization (resolution keys on
  `character.md` / `pc.md` presence only).
- Tombstoned ids count as **taken** for id uniquification — no resurrection semantics.
- Task 5 flips campaign lists to live world reads; two existing sync-store tests that
  assert "a world record added after campaign creation is invisible until accepted"
  change expectation there (called out in the task).

---

### Task 1: `store/overlay.py` — tombstones + flat-record (locations/lore) resolution

**Files:**
- Create: `backend/src/grimoire/store/overlay.py`
- Modify: `backend/src/grimoire/store/entities.py` (`create_entity`, ~line 70)
- Modify: `backend/src/grimoire/store/__init__.py` (add `overlay` to the submodule imports)
- Test: `backend/tests/test_overlay.py` (new)

**Interfaces:**
- Consumes: `campaigns.campaign_root/read_campaign/read_manifest/write_manifest`,
  `worlds.world_root`, `entities.*`, `paths` helpers.
- Produces (used by every later task):
  `overlay.croot_of(cid) -> Path`, `overlay.wroot_of(cid) -> Path`,
  `overlay.deleted(cid) -> set[str]`, `overlay.add_deleted(cid, ref) -> None`,
  `overlay.list_entities(cid, kind) -> list[dict]`,
  `overlay.read_entity(cid, kind, eid) -> dict`,
  `overlay.create_entity(cid, kind, name, body="", keys="", owners="") -> str`,
  `overlay.update_entity(cid, kind, eid, *, name=None, body=None, keys=None, owners=None) -> None`,
  `overlay.delete_entity(cid, kind, eid) -> None`,
  `overlay.materialize_entity(cid, kind, eid) -> None`.
  Also `entities.create_entity(..., taken: Callable[[str], bool] | None = None)`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_overlay.py`:

```python
import pytest

from grimoire.store import campaigns, entities, overlay, worlds


def _pair(monkeypatch, tmp_path):
    """A world with one lore entry + a campaign on it (campaigns are still full
    copies at this task — tests delete the copy to simulate a thin campaign)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "The Sword", "world text")
    cid = campaigns.create_campaign("C", wid)
    return wid, wroot, cid, eid


def _thin(cid, kind, eid):
    (campaigns.campaign_root(cid) / kind / f"{eid}.md").unlink()
    manifest = campaigns.read_manifest(cid)
    manifest.pop(f"{kind}/{eid}", None)
    campaigns.write_manifest(cid, manifest)


def test_read_falls_through_to_world_when_not_materialized(monkeypatch, tmp_path):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world text"


def test_materialized_copy_shadows_world(monkeypatch, tmp_path):
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    entities.update_entity(wroot, "lore", eid, body="world v2")
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world text"  # copy wins


def test_list_merges_campaign_wins_and_tombstones_hide(monkeypatch, tmp_path):
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    other = entities.create_entity(wroot, "lore", "World Only")
    local = overlay.create_entity(cid, "lore", "Campaign Only")
    overlay.delete_entity(cid, "lore", other)         # inherited -> tombstone
    ids = [e["id"] for e in overlay.list_entities(cid, "lore")]
    assert eid in ids and local in ids and other not in ids


def test_delete_inherited_does_not_resurrect_on_world_edit(monkeypatch, tmp_path):
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    overlay.delete_entity(cid, "lore", eid)
    entities.update_entity(wroot, "lore", eid, body="edited after delete")
    with pytest.raises(entities.EntityNotFound):
        overlay.read_entity(cid, "lore", eid)
    assert eid not in [e["id"] for e in overlay.list_entities(cid, "lore")]


def test_delete_materialized_tombstones_and_drops_base(monkeypatch, tmp_path):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    overlay.delete_entity(cid, "lore", eid)   # copy exists (full-copy campaign)
    assert f"lore/{eid}" in overlay.deleted(cid)
    assert f"lore/{eid}" not in campaigns.read_manifest(cid)
    with pytest.raises(entities.EntityNotFound):
        overlay.read_entity(cid, "lore", eid)


def test_update_inherited_materializes_and_records_base(monkeypatch, tmp_path):
    _wid, wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    base_before = entities.entity_hash(wroot, "lore", eid)
    overlay.update_entity(cid, "lore", eid, body="campaign text")
    assert (campaigns.campaign_root(cid) / "lore" / f"{eid}.md").exists()
    assert campaigns.read_manifest(cid)[f"lore/{eid}"] == base_before
    assert overlay.read_entity(cid, "lore", eid)["body"] == "campaign text"
    assert entities.read_entity(wroot, "lore", eid)["body"] == "world text"  # world untouched


def test_create_uniquifies_against_world_and_tombstones(monkeypatch, tmp_path):
    _wid, _wroot, cid, eid = _pair(monkeypatch, tmp_path)
    _thin(cid, "lore", eid)
    assert overlay.create_entity(cid, "lore", "The Sword") == f"{eid}-2"
    overlay.delete_entity(cid, "lore", eid)  # tombstone the inherited one
    assert overlay.create_entity(cid, "lore", "The Sword") == f"{eid}-3"
```

- [ ] **Step 2: Run to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_overlay.py -q`
Expected: FAIL — `ImportError: cannot import name 'overlay'`.

- [ ] **Step 3: Implement**

Add the `taken` hook to `entities.create_entity` (backend/src/grimoire/store/entities.py:70):

```python
def create_entity(root: Path, kind: str, name: str, body: str = "", keys: str = "",
                  owners: str = "", taken=None) -> str:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    d.mkdir(parents=True, exist_ok=True)

    def exists(c: str) -> bool:
        # `taken` widens the id namespace (overlay: world files + tombstones)
        return _entity_path(root, kind, c).exists() or (taken is not None and taken(c))

    eid = uniquify(slugify(name), exists)
    meta = {"name": name}
    if keys:
        meta["keys"] = keys
    if owners:
        meta["owners"] = owners
    _entity_path(root, kind, eid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return eid
```

Create `backend/src/grimoire/store/overlay.py`:

```python
"""Campaign-over-world copy-on-write resolution.

A campaign materializes a record only when it diverges from its world (edit,
version lock, delete, campaign-local create); everything else reads through to
the world live. Rules:

- Flat records (locations/lore/greetings, plotmap.json): campaign file wins;
  else a tombstone means absent; else the world file.
- Actors (characters/pcs): whole-dir, keyed on character.md / pc.md existing in
  the campaign — a materialized actor is authoritative for meta + versions, so
  lock-purged versions stay purged. Sidecars (tagline.md) and assets still
  overlay per file.
- sync.md holds base hashes for materialized records only. Tombstones live in
  <campaign>/deleted.json (a sorted JSON list of refs); a tombstoned id counts
  as taken for uniquify, so nothing ever resurrects under a reused id.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import campaigns, entities, worlds


def croot_of(cid: str) -> Path:
    return campaigns.campaign_root(cid)


def wroot_of(cid: str) -> Path:
    """The campaign's world root. May not exist (world deleted before the
    guard existed) — resolution treats a missing world as an empty one."""
    return worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))


# ---- tombstones ----

def _deleted_path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "deleted.json"


def deleted(cid: str) -> set[str]:
    p = _deleted_path(cid)
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    return set(data) if isinstance(data, list) else set()


def add_deleted(cid: str, ref: str) -> None:
    _deleted_path(cid).write_text(
        json.dumps(sorted(deleted(cid) | {ref}), indent=2) + "\n", encoding="utf-8")


# ---- flat records (locations / lore; greetings + plotmap join in Task 2) ----

def _flat_ref(kind: str, eid: str) -> str:
    return f"{kind}/{eid}"


def _flat_path(root: Path, kind: str, eid: str) -> Path:
    return root / kind / f"{eid}.md"


def _materialize_flat(cid: str, kind: str, eid: str) -> bool:
    """Copy an inherited flat record into the campaign and record its sync
    base. True if the campaign file exists afterwards. Assets are never
    copied — they overlay per file. Tombstoned records don't materialize."""
    croot = croot_of(cid)
    if _flat_path(croot, kind, eid).exists():
        return True
    wroot = wroot_of(cid)
    src = _flat_path(wroot, kind, eid)
    if not src.exists() or _flat_ref(kind, eid) in deleted(cid):
        return False
    dst = _flat_path(croot, kind, eid)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = campaigns.read_manifest(cid)
    manifest[_flat_ref(kind, eid)] = entities.entity_hash(wroot, kind, eid) or ""
    campaigns.write_manifest(cid, manifest)
    return True


def _drop_manifest_ref(cid: str, ref: str) -> None:
    manifest = campaigns.read_manifest(cid)
    if manifest.pop(ref, None) is not None:
        campaigns.write_manifest(cid, manifest)


def materialize_entity(cid: str, kind: str, eid: str) -> None:
    if not _materialize_flat(cid, kind, eid):
        raise entities.EntityNotFound(f"{kind}/{eid}")


def list_entities(cid: str, kind: str) -> list[dict]:
    mine = entities.list_entities(croot_of(cid), kind)
    have = {e["id"] for e in mine}
    gone = deleted(cid)
    inherited = [e for e in entities.list_entities(wroot_of(cid), kind)
                 if e["id"] not in have and _flat_ref(kind, e["id"]) not in gone]
    return sorted(mine + inherited, key=lambda e: e["id"])


def read_entity(cid: str, kind: str, eid: str) -> dict:
    try:
        return entities.read_entity(croot_of(cid), kind, eid)
    except entities.EntityNotFound:
        if _flat_ref(kind, eid) in deleted(cid):
            raise
        return entities.read_entity(wroot_of(cid), kind, eid)


def create_entity(cid: str, kind: str, name: str, body: str = "", keys: str = "",
                  owners: str = "") -> str:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(eid: str) -> bool:
        return _flat_path(wroot, kind, eid).exists() or _flat_ref(kind, eid) in gone

    return entities.create_entity(croot_of(cid), kind, name, body, keys, owners, taken=taken)


def update_entity(cid: str, kind: str, eid: str, *, name: str | None = None,
                  body: str | None = None, keys: str | None = None,
                  owners: str | None = None) -> None:
    croot = croot_of(cid)
    if not _flat_path(croot, kind, eid).exists():
        materialize_entity(cid, kind, eid)
    entities.update_entity(croot, kind, eid, name=name, body=body, keys=keys, owners=owners)


def delete_entity(cid: str, kind: str, eid: str) -> None:
    ref = _flat_ref(kind, eid)
    in_world = _flat_path(wroot_of(cid), kind, eid).exists() and ref not in deleted(cid)
    try:
        entities.delete_entity(croot_of(cid), kind, eid)
        _drop_manifest_ref(cid, ref)
    except entities.EntityNotFound:
        if not in_world:
            raise
    if in_world:
        add_deleted(cid, ref)   # keep the world's copy from showing through
```

Add `overlay` to the imports in `backend/src/grimoire/store/__init__.py` (alphabetical,
same style as the existing submodule imports).

- [ ] **Step 4: Run the new tests, then the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_overlay.py -q` → all PASS.
Run: `backend/.venv/Scripts/python.exe -m pytest backend -q` → all PASS (nothing else uses overlay yet; `create_entity` change is opt-in).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/overlay.py backend/src/grimoire/store/entities.py backend/src/grimoire/store/__init__.py backend/tests/test_overlay.py
git commit -m "feat(store): overlay module — copy-on-write resolution for flat records"
```

---

### Task 2: Overlay greetings + plotmap

**Files:**
- Modify: `backend/src/grimoire/store/overlay.py`
- Modify: `backend/src/grimoire/store/greetings.py` (`create_greeting` ~line 79 gains `taken`; extract `remove_from_plotmap` from `delete_greeting` ~line 173)
- Test: `backend/tests/test_overlay.py`

**Interfaces:**
- Produces: `overlay.list_greetings(cid) -> list[dict]`,
  `overlay.read_greeting(cid, gid) -> dict`,
  `overlay.create_greeting(cid, name, character, version, body="", requires_tags=None, predecessor_join="all", present=None, pcless=False) -> str`,
  `overlay.update_greeting(cid, gid, *, name=None, body=None, requires_tags=None, predecessor_join=None, present=None, pcless=None) -> None`,
  `overlay.delete_greeting(cid, gid) -> None`,
  `overlay.read_plotmap(cid) -> dict`, `overlay.materialize_plotmap(cid) -> None`,
  `overlay.set_edges(cid, gid, leads_to=None, excludes=None) -> None`.
  Also `greetings.remove_from_plotmap(root, gid) -> None` (public; `delete_greeting` calls it).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_overlay.py` (import `greetings` too):

```python
def _greeting_pair(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    gid = greetings.create_greeting(wroot, "Opening", "hero", "default", "hi {{user}}")
    greetings.set_edges(wroot, gid, leads_to=["next"], excludes=[])
    cid = campaigns.create_campaign("C", wid)
    # thin: strip the copies so fallthrough is exercised before Task 6 lands
    (campaigns.campaign_root(cid) / "greetings" / f"{gid}.md").unlink()
    (campaigns.campaign_root(cid) / "plotmap.json").unlink()
    manifest = campaigns.read_manifest(cid)
    manifest.pop(f"greetings/{gid}", None)
    manifest.pop("plotmap", None)
    campaigns.write_manifest(cid, manifest)
    return wroot, cid, gid


def test_greeting_and_plotmap_fall_through(monkeypatch, tmp_path):
    wroot, cid, gid = _greeting_pair(monkeypatch, tmp_path)
    assert overlay.read_greeting(cid, gid)["body"] == "hi {{user}}"
    assert overlay.read_plotmap(cid)[gid]["leads_to"] == ["next"]


def test_greeting_update_materializes(monkeypatch, tmp_path):
    wroot, cid, gid = _greeting_pair(monkeypatch, tmp_path)
    overlay.update_greeting(cid, gid, body="campaign body")
    assert overlay.read_greeting(cid, gid)["body"] == "campaign body"
    assert greetings.read_greeting(wroot, gid)["body"] == "hi {{user}}"
    assert f"greetings/{gid}" in campaigns.read_manifest(cid)


def test_set_edges_materializes_plotmap(monkeypatch, tmp_path):
    wroot, cid, gid = _greeting_pair(monkeypatch, tmp_path)
    overlay.set_edges(cid, gid, leads_to=["other"])
    assert overlay.read_plotmap(cid)[gid]["leads_to"] == ["other"]
    assert greetings.read_plotmap(wroot)[gid]["leads_to"] == ["next"]   # world untouched
    assert "plotmap" in campaigns.read_manifest(cid)


def test_delete_inherited_greeting_tombstones_and_cleans_edges(monkeypatch, tmp_path):
    wroot, cid, gid = _greeting_pair(monkeypatch, tmp_path)
    other = greetings.create_greeting(wroot, "Second", "hero", "default", "x")
    greetings.set_edges(wroot, other, leads_to=[gid])
    overlay.delete_greeting(cid, gid)
    assert gid not in [g["id"] for g in overlay.list_greetings(cid)]
    assert gid not in overlay.read_plotmap(cid).get(other, {}).get("leads_to", [])
    assert greetings.read_plotmap(wroot)[other]["leads_to"] == [gid]    # world untouched
```

- [ ] **Step 2: Run to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_overlay.py -q`
Expected: new tests FAIL — `AttributeError: module ... has no attribute 'read_greeting'`.

- [ ] **Step 3: Implement**

In `greetings.py`: add `taken=None` to `create_greeting` exactly like
`entities.create_entity` (OR-ed into the uniquify lambda), and extract the plotmap
edge-cleanup from `delete_greeting` into a public function it then calls:

```python
def remove_from_plotmap(root: Path, gid: str) -> None:
    """Drop gid's own edges and every reference to it from other greetings' edges."""
    data = read_plotmap(root)
    changed = data.pop(gid, None) is not None
    for e in data.values():
        for key in ("leads_to", "excludes"):
            if gid in e.get(key, []):
                e[key] = [x for x in e[key] if x != gid]
                changed = True
    if changed:
        _write_plotmap(root, data)
```

In `overlay.py` (import `greetings`):

```python
# ---- greetings + plot map ----

def list_greetings(cid: str) -> list[dict]:
    mine = greetings.list_greetings(croot_of(cid))
    have = {g["id"] for g in mine}
    gone = deleted(cid)
    inherited = [g for g in greetings.list_greetings(wroot_of(cid))
                 if g["id"] not in have and _flat_ref("greetings", g["id"]) not in gone]
    out = mine + inherited
    out.sort(key=lambda m: natural_key(m["name"]))
    return out


def read_greeting(cid: str, gid: str) -> dict:
    try:
        return greetings.read_greeting(croot_of(cid), gid)
    except greetings.GreetingNotFound:
        if _flat_ref("greetings", gid) in deleted(cid):
            raise
        return greetings.read_greeting(wroot_of(cid), gid)


def create_greeting(cid: str, name: str, character: str, version: str, body: str = "",
                    requires_tags=None, predecessor_join: str = "all",
                    present=None, pcless: bool = False) -> str:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(gid: str) -> bool:
        return _flat_path(wroot, "greetings", gid).exists() or _flat_ref("greetings", gid) in gone

    return greetings.create_greeting(croot_of(cid), name, character, version, body,
                                     requires_tags, predecessor_join, present=present,
                                     pcless=pcless, taken=taken)


def update_greeting(cid: str, gid: str, **kwargs) -> None:
    if not _materialize_flat(cid, "greetings", gid):
        raise greetings.GreetingNotFound(gid)
    greetings.update_greeting(croot_of(cid), gid, **kwargs)


def delete_greeting(cid: str, gid: str) -> None:
    ref = _flat_ref("greetings", gid)
    in_world = _flat_path(wroot_of(cid), "greetings", gid).exists() and ref not in deleted(cid)
    if in_world:
        materialize_plotmap(cid)   # edge cleanup must land campaign-side
    try:
        greetings.delete_greeting(croot_of(cid), gid)
        _drop_manifest_ref(cid, ref)
    except greetings.GreetingNotFound:
        if not in_world:
            raise
        greetings.remove_from_plotmap(croot_of(cid), gid)
    if in_world:
        add_deleted(cid, ref)


def read_plotmap(cid: str) -> dict:
    croot = croot_of(cid)
    if (croot / "plotmap.json").exists() or "plotmap" in deleted(cid):
        return greetings.read_plotmap(croot)
    return greetings.read_plotmap(wroot_of(cid))


def materialize_plotmap(cid: str) -> None:
    croot, wroot = croot_of(cid), wroot_of(cid)
    if (croot / "plotmap.json").exists() or "plotmap" in deleted(cid):
        return
    src = wroot / "plotmap.json"
    if not src.exists():
        return   # nothing to copy; set_edges will create a fresh campaign map
    (croot / "plotmap.json").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = campaigns.read_manifest(cid)
    manifest["plotmap"] = greetings.plotmap_hash(wroot) or ""
    campaigns.write_manifest(cid, manifest)


def set_edges(cid: str, gid: str, leads_to=None, excludes=None) -> None:
    materialize_plotmap(cid)
    greetings.set_edges(croot_of(cid), gid, leads_to, excludes)
```

(Import `natural_key` from `.paths`.)

- [ ] **Step 4: Run tests** — overlay file then full backend suite, all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/overlay.py backend/src/grimoire/store/greetings.py backend/tests/test_overlay.py
git commit -m "feat(store): overlay greetings + plotmap with tombstones and edge cleanup"
```

---

### Task 3: Overlay actors (characters / PCs) + taglines

**Files:**
- Modify: `backend/src/grimoire/store/overlay.py`
- Modify: `backend/src/grimoire/store/pcs.py` (`create_pc` ~line 85 gains `taken`, same pattern as entities)
- Test: `backend/tests/test_overlay.py`

**Interfaces:**
- Produces: `overlay.actor_root(cid, kind, aid) -> Path` (croot when materialized or
  tombstoned — the caller's read then NotFounds there — else wroot);
  `overlay.char_root(cid, aid)` / `overlay.pc_root(cid, aid)` conveniences;
  `overlay.materialize_actor(cid, kind, aid) -> None` (meta + all version files, no
  assets/sidecars; records `dir_hash` base; raises the module NotFound when absent or
  tombstoned); `overlay.ensure_actor_writable(cid, kind, aid) -> Path` (materialize if
  needed, return croot); `overlay.dematerialize_actor(cid, kind, aid) -> None` (delete
  meta + version files only; keep sidecars/assets; rmdir if emptied);
  `overlay.list_characters(cid) -> list[dict]` / `overlay.list_pcs(cid) -> list[dict]`
  (merged, campaign wins, tombstone-aware; character items get asset/tagline fields
  patched in Task 4 — here they pass through);
  `overlay.character_refs(cid) -> list[str]`; `overlay.tagline(cid, char_id) -> str`
  (croot else wroot); `overlay.create_pc(cid, name, tags, version_name="default",
  persona=None) -> tuple[str, str]` (merged-namespace uniquify).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_overlay.py` (import `characters`, `pcs`, `taglines`):

```python
def _actor_pair(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, _ = characters.create_character(wroot, "Hero")
    characters.create_version(wroot, aid, "dark", characters.blank_card("Hero"))
    taglines.write(wroot, aid, "A hero of legend.")
    cid = campaigns.create_campaign("C", wid)
    import shutil
    shutil.rmtree(campaigns.campaign_root(cid) / "characters" / aid)
    manifest = campaigns.read_manifest(cid)
    manifest.pop(f"characters/{aid}", None)
    campaigns.write_manifest(cid, manifest)
    return wroot, cid, aid


def test_actor_root_falls_through_until_materialized(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assert overlay.char_root(cid, aid) == wroot
    overlay.materialize_actor(cid, "characters", aid)
    assert overlay.char_root(cid, aid) == campaigns.campaign_root(cid)
    detail = characters.read_character(overlay.char_root(cid, aid), aid)
    assert [v["id"] for v in detail["versions"]] == ["dark", "default"]
    assert f"characters/{aid}" in campaigns.read_manifest(cid)
    # assets/sidecars are NOT copied
    assert not (campaigns.campaign_root(cid) / "characters" / aid / "tagline.md").exists()


def test_sidecars_do_not_count_as_materialization(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    d = campaigns.campaign_root(cid) / "characters" / aid
    d.mkdir(parents=True)
    (d / "dossier.md").write_text("seen in scene 1\n", encoding="utf-8")
    assert overlay.char_root(cid, aid) == wroot          # still inherited
    assert aid in [c["id"] for c in overlay.list_characters(cid)]   # and not duplicated


def test_list_characters_merges_and_hides_tombstoned(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    overlay.add_deleted(cid, f"characters/{aid}")
    assert aid not in [c["id"] for c in overlay.list_characters(cid)]


def test_tagline_falls_through(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assert overlay.tagline(cid, aid) == "A hero of legend."
    taglines.write(campaigns.campaign_root(cid), aid, "Campaign-specific.")
    assert overlay.tagline(cid, aid) == "Campaign-specific."


def test_dematerialize_keeps_sidecars_and_assets(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    overlay.materialize_actor(cid, "characters", aid)
    d = campaigns.campaign_root(cid) / "characters" / aid
    (d / "dossier.md").write_text("standing paragraph\n", encoding="utf-8")
    overlay.dematerialize_actor(cid, "characters", aid)
    assert not (d / "character.md").exists() and not list(d.glob("*.json"))
    assert (d / "dossier.md").exists()
    assert overlay.char_root(cid, aid) == wroot
```

- [ ] **Step 2: Run to verify they fail** — `AttributeError` on the new names.

- [ ] **Step 3: Implement**

`pcs.create_pc` gains `taken=None` OR-ed into its uniquify lambda (same as entities).

In `overlay.py` (import `characters`, `pcs`, `taglines`):

```python
# ---- actors (characters / pcs): whole-dir resolution keyed on the container meta ----

def _actor_meta(kind: str) -> str:
    return "character.md" if kind == "characters" else "pc.md"


def _actor_not_found(kind: str, aid: str) -> Exception:
    return characters.CharacterNotFound(aid) if kind == "characters" else pcs.PCNotFound(aid)


def actor_root(cid: str, kind: str, aid: str) -> Path:
    """Root whose <kind>/<aid> dir is authoritative for meta + version files.
    Tombstoned actors resolve to the campaign, where the caller's read raises
    its usual NotFound."""
    croot = croot_of(cid)
    if (croot / kind / aid / _actor_meta(kind)).exists():
        return croot
    if _flat_ref(kind, aid) in deleted(cid):
        return croot
    return wroot_of(cid)


def char_root(cid: str, aid: str) -> Path:
    return actor_root(cid, "characters", aid)


def pc_root(cid: str, aid: str) -> Path:
    return actor_root(cid, "pcs", aid)


def materialize_actor(cid: str, kind: str, aid: str) -> None:
    """Copy meta + every version file (never assets or sidecars) from the world
    and record the whole-actor sync base. No-op when already materialized."""
    croot, wroot = croot_of(cid), wroot_of(cid)
    if (croot / kind / aid / _actor_meta(kind)).exists():
        return
    src = wroot / kind / aid
    if not (src / _actor_meta(kind)).exists() or _flat_ref(kind, aid) in deleted(cid):
        raise _actor_not_found(kind, aid)
    dst = croot / kind / aid
    dst.mkdir(parents=True, exist_ok=True)
    ext = "json" if kind == "characters" else "md"
    for p in sorted(src.glob(f"*.{ext}")):
        (dst / p.name).write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    meta_src = src / _actor_meta(kind)
    (dst / meta_src.name).write_text(meta_src.read_text(encoding="utf-8"), encoding="utf-8")
    base = (characters.dir_hash if kind == "characters" else pcs.dir_hash)(wroot, aid)
    manifest = campaigns.read_manifest(cid)
    manifest[_flat_ref(kind, aid)] = base or ""
    campaigns.write_manifest(cid, manifest)


def ensure_actor_writable(cid: str, kind: str, aid: str) -> Path:
    """Materialize an inherited actor and return the campaign root writes target."""
    croot = croot_of(cid)
    if not (croot / kind / aid / _actor_meta(kind)).exists():
        materialize_actor(cid, kind, aid)
    return croot


def dematerialize_actor(cid: str, kind: str, aid: str) -> None:
    """Remove meta + version files so the actor reverts to inherited. Sidecars
    (tagline/dossier/state) and assets stay — they overlay per file. PCs carry
    no sidecar .md files, so all *.md go."""
    d = croot_of(cid) / kind / aid
    if not d.exists():
        return
    if kind == "characters":
        targets = list(d.glob("*.json")) + [d / "character.md"]
    else:
        targets = list(d.glob("*.md"))
    for p in targets:
        if p.exists():
            p.unlink()
    if not any(d.iterdir()):
        d.rmdir()


def list_characters(cid: str) -> list[dict]:
    mine = characters.list_characters(croot_of(cid))
    # dossier/state-only dirs have no character.md and are filtered by
    # characters.list_characters itself (it requires the meta file)
    have = {c["id"] for c in mine}
    gone = deleted(cid)
    inherited = [c for c in characters.list_characters(wroot_of(cid))
                 if c["id"] not in have and _flat_ref("characters", c["id"]) not in gone]
    return sorted(mine + inherited, key=lambda c: c["id"])


def list_pcs(cid: str) -> list[dict]:
    mine = pcs.list_pcs(croot_of(cid))
    have = {p["id"] for p in mine}
    gone = deleted(cid)
    inherited = [p for p in pcs.list_pcs(wroot_of(cid))
                 if p["id"] not in have and _flat_ref("pcs", p["id"]) not in gone]
    return sorted(mine + inherited, key=lambda p: p["id"])


def character_refs(cid: str) -> list[str]:
    return [c["id"] for c in list_characters(cid)]


def create_pc(cid: str, name: str, tags: list[str], version_name: str = "default",
              persona: dict | None = None) -> tuple[str, str]:
    wroot, gone = wroot_of(cid), deleted(cid)

    def taken(pid: str) -> bool:
        return (wroot / "pcs" / pid / "pc.md").exists() or _flat_ref("pcs", pid) in gone

    return pcs.create_pc(croot_of(cid), name, tags, version_name, persona, taken=taken)


def tagline(cid: str, char_id: str) -> str:
    return taglines.read(croot_of(cid), char_id) or taglines.read(wroot_of(cid), char_id)
```

- [ ] **Step 4: Run tests** — overlay file then full backend suite, all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/overlay.py backend/src/grimoire/store/pcs.py backend/tests/test_overlay.py
git commit -m "feat(store): overlay actor resolution — whole-dir, lock-safe, sidecar-aware"
```

---

### Task 4: Overlay assets (images, focus) + payload patching

**Files:**
- Modify: `backend/src/grimoire/store/overlay.py`
- Test: `backend/tests/test_overlay.py`

**Interfaces:**
- Consumes: `assets.list_images/image_path/read_focus/delete_image/promote_image/AVATAR/FOCUS_FILE`.
- Produces: `overlay.list_images(cid, aid, vid, base="characters") -> list[dict]` (union,
  campaign wins per name, minus asset tombstones `assets/<base>/<aid>/<vid>/<name>`);
  `overlay.image_root(cid, aid, vid, name, base="characters") -> Path` (root
  `_serve_image` should read from); `overlay.read_focus(cid, aid, vid, base="characters") -> int | None`
  (campaign-side avatar file or campaign focus.json makes campaign authoritative, else
  world); `overlay.delete_image(cid, aid, vid, name, base="characters") -> None`
  (delete campaign file if any; tombstone when the world still has one);
  `overlay.promote_image(cid, aid, vid, name, base="characters") -> None` (copy-up the
  two affected files, then `assets.promote_image` on croot);
  `overlay.read_character(cid, char_id) -> dict` and the Task-3 `list_characters`
  extended to patch asset-derived fields (`images`, `avatar_focus`, `has_avatar`,
  `gallery_count`, `localized_count`, `tagline`) from the union.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_overlay.py` (import `assets`):

```python
PNG = b"\x89PNG\r\n\x1a\nfake"


def test_images_union_campaign_wins_and_tombstones(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    assets.put_image(wroot, aid, "default", "gallery_0", PNG, "png")
    names = {i["name"] for i in overlay.list_images(cid, aid, "default")}
    assert names == {"avatar", "gallery_0"}
    assert overlay.image_root(cid, aid, "default", "avatar") == wroot
    croot = campaigns.campaign_root(cid)
    assets.put_image(croot, aid, "default", "avatar", PNG + b"2", "png")
    assert overlay.image_root(cid, aid, "default", "avatar") == croot
    overlay.delete_image(cid, aid, "default", "gallery_0")
    assert {i["name"] for i in overlay.list_images(cid, aid, "default")} == {"avatar"}
    # the tombstone makes serving 404 at the campaign root, not fall through
    assert overlay.image_root(cid, aid, "default", "gallery_0") == croot


def test_focus_world_fallback_until_campaign_avatar(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    assets.write_focus(wroot, aid, "default", 80)
    assert overlay.read_focus(cid, aid, "default") == 80
    assets.put_image(campaigns.campaign_root(cid), aid, "default", "avatar", PNG + b"2", "png")
    assert overlay.read_focus(cid, aid, "default") is None   # new avatar, campaign focus unset


def test_read_character_patches_images_from_union(monkeypatch, tmp_path):
    wroot, cid, aid = _actor_pair(monkeypatch, tmp_path)
    assets.put_image(wroot, aid, "default", "avatar", PNG, "png")
    overlay.materialize_actor(cid, "characters", aid)   # cards in campaign, assets in world
    detail = overlay.read_character(cid, aid)
    default = next(v for v in detail["versions"] if v["id"] == "default")
    assert "avatar" in default["images"]
    listed = next(c for c in overlay.list_characters(cid) if c["id"] == aid)
    assert listed["has_avatar"] is True
    assert listed["tagline"] == "A hero of legend."
```

- [ ] **Step 2: Run to verify they fail** — `AttributeError` on new names / assertion on `images == []`.

- [ ] **Step 3: Implement**

In `overlay.py` (import `assets`):

```python
# ---- assets: per-file union, campaign wins ----

def _asset_ref(base: str, aid: str, vid: str, name: str) -> str:
    return f"assets/{base}/{aid}/{vid}/{name}"


def list_images(cid: str, aid: str, vid: str, base: str = "characters") -> list[dict]:
    mine = assets.list_images(croot_of(cid), aid, vid, base)
    have = {i["name"] for i in mine}
    gone = deleted(cid)
    inherited = [i for i in assets.list_images(wroot_of(cid), aid, vid, base)
                 if i["name"] not in have and _asset_ref(base, aid, vid, i["name"]) not in gone]
    return sorted(mine + inherited, key=lambda i: i["name"])


def image_root(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> Path:
    croot = croot_of(cid)
    if assets.image_path(croot, aid, vid, name, base) is not None:
        return croot
    if _asset_ref(base, aid, vid, name) in deleted(cid):
        return croot   # absent there -> the serve route 404s, no fallthrough
    return wroot_of(cid)


def read_focus(cid: str, aid: str, vid: str, base: str = "characters") -> int | None:
    croot = croot_of(cid)
    focus_file = croot / base / aid / "assets" / vid / assets.FOCUS_FILE
    if assets.image_path(croot, aid, vid, assets.AVATAR, base) is not None or focus_file.exists():
        return assets.read_focus(croot, aid, vid, base)
    return assets.read_focus(wroot_of(cid), aid, vid, base)


def delete_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    assets.delete_image(croot_of(cid), aid, vid, name, base)   # no-op when absent
    if assets.image_path(wroot_of(cid), aid, vid, name, base) is not None:
        add_deleted(cid, _asset_ref(base, aid, vid, name))


def promote_image(cid: str, aid: str, vid: str, name: str, base: str = "characters") -> None:
    """Copy-up the named image and the current avatar, then swap campaign-side."""
    croot, wroot = croot_of(cid), wroot_of(cid)
    for n in (name, assets.AVATAR):
        if (assets.image_path(croot, aid, vid, n, base) is None
                and _asset_ref(base, aid, vid, n) not in deleted(cid)):
            src = assets.image_path(wroot, aid, vid, n, base)
            if src is not None:
                assets.put_image(croot, aid, vid, n, src.read_bytes(),
                                 src.suffix.lstrip("."), base)
    assets.promote_image(croot, aid, vid, name, base)


# ---- payload patching: asset-derived fields come from the union ----

def read_character(cid: str, char_id: str) -> dict:
    detail = characters.read_character(char_root(cid, char_id), char_id)
    for v in detail["versions"]:
        v["images"] = [i["name"] for i in list_images(cid, char_id, v["id"])]
        v["avatar_focus"] = read_focus(cid, char_id, v["id"])
    return detail
```

Extend Task 3's `overlay.list_characters` to patch every item before returning:

```python
def _patch_char_item(cid: str, item: dict) -> dict:
    names = [i["name"] for i in list_images(cid, item["id"], item["default_version"])]
    return {**item,
            "has_avatar": assets.AVATAR in names,
            "avatar_focus": read_focus(cid, item["id"], item["default_version"]),
            "gallery_count": sum(1 for n in names if n.startswith("gallery_")),
            "localized_count": sum(1 for n in names if n.startswith("embed-")),
            "tagline": tagline(cid, item["id"])}
```

…and in `list_characters` return
`sorted([_patch_char_item(cid, c) for c in mine + inherited], key=lambda c: c["id"])`.

- [ ] **Step 4: Run tests** — overlay file then full backend suite, all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/overlay.py backend/tests/test_overlay.py
git commit -m "feat(store): overlay asset union, focus fallback, patched actor payloads"
```

---

### Task 5: Switch campaign routes + consumers to overlay (behavior-neutral while fat)

**Files:**
- Modify: `backend/src/grimoire/routes.py` (campaign sections, see list below)
- Modify: `backend/src/grimoire/store/context.py`, `store/playing.py`,
  `store/appearances.py`, `store/absorb.py`, `store/chronicle.py`, `store/suggest.py`,
  `store/greetings.py` (`availability` signature)
- Test: existing suites; `backend/tests/test_playing_store.py`, `test_sync_store.py`
  expectation updates called out below.

**Interfaces:**
- Consumes: everything produced by Tasks 1–4.
- Produces: `greetings.availability(items: list[dict], plotmap, played, player_tags, skipped=frozenset())`
  — first param becomes the pre-listed greeting metas instead of a root
  (`playing.available_greetings` passes `overlay.list_greetings(cid)`).

This task is mechanical: campaign-scoped reads/writes route through overlay. The
pattern, applied everywhere below — reads swap `croot` for the overlay resolver;
writes materialize first:

```python
# read, flat:      store.entities.read_entity(croot, kind, eid)      -> store.overlay.read_entity(cid, kind, eid)
# read, actor:     store.characters.read_character(croot, aid)       -> store.characters.read_character(store.overlay.char_root(cid, aid), aid)
# write, flat:     store.entities.update_entity(croot, ...)          -> store.overlay.update_entity(cid, ...)
# write, version:  store.characters.update_version(croot, aid, ...)  -> store.characters.update_version(store.overlay.ensure_actor_writable(cid, "characters", aid), aid, ...)
```

- [ ] **Step 1: Swap the campaign routes in `routes.py`**

Entity CRUD + images (lines ~2075–2125): `get_campaign_entities` →
`store.overlay.list_entities(cid, kind)` (keep the 404-on-unknown-kind mapping the
`_entity_list` helper does — add campaign variants of the tiny `_entity_*` helpers that
call overlay and reuse the same exception→HTTP mapping);
`post_campaign_entity` → `overlay.create_entity`; `get/put/delete_campaign_entity` →
`overlay.read/update/delete_entity`; `list_campaign_entity_images` →
`overlay.list_images(cid, eid, "default", base=kind)`; `get_campaign_entity_image` →
`_serve_image(store.overlay.image_root(cid, eid, "default", name, base=kind), ...)`;
`delete_campaign_entity_image` → `overlay.delete_image`;
`promote_campaign_entity_image` → `overlay.promote_image`. Image PUT keeps writing to
`_campaign_root_or_404(cid)` (campaign file wins by resolution).

Characters/PCs (lines ~1598–1737): `get_campaign_characters` →
`store.overlay.list_characters(cid)`; `get_campaign_character` →
`store.overlay.read_character(cid, char)`; `get_campaign_pcs` →
`store.overlay.list_pcs(cid)`; `get_campaign_pc` →
`store.pcs.read_pc(store.overlay.pc_root(cid, pid), pid)`; `post_campaign_pc` →
`store.overlay.create_pc(cid, ...)`; the character/PC **write** routes
(`put_campaign_character`, `post/put/delete_campaign_character_version`,
`put_campaign_pc`, `post/put/delete_campaign_pc_version`) replace
`root = _campaign_root_or_404(cid)` with
`_campaign_root_or_404(cid); root = store.overlay.ensure_actor_writable(cid, "characters"|"pcs", char|pid)`
wrapped so `CharacterNotFound`/`PCNotFound` from materialization maps to the existing
404. `get_campaign_image` (~1611) serves from
`store.overlay.image_root(cid, char, vid, name)`.

Pick/seat (~1740–1802): the `post_pick_version` existence check uses
`store.overlay.actor_root(cid, kind, aid)` instead of croot. `_seat_cast_member` drops
its `wroot`/`croot` params and try/except pairs:

```python
    if version is None:
        try:
            if body.kind == "characters":
                version = store.characters.read_character(
                    store.overlay.char_root(cid, body.id), body.id)["meta"]["default_version"]
            else:
                version = store.pcs.read_pc(
                    store.overlay.pc_root(cid, body.id), body.id)["meta"]["default_version"]
        except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
            raise HTTPException(status_code=404, detail="actor not found")
```

(and its two callers stop computing/passing wroot/croot). Scene location name lookup
(~1855) → `store.overlay.read_entity(cid, "locations", eid)`.

Greetings (~1961–2025): `get_campaign_greetings` → `store.overlay.list_greetings(cid)`;
`post_campaign_greeting` → `store.overlay.create_greeting(cid, ...)`;
`get_campaign_greeting` → `store.overlay.read_greeting(cid, gid)` +
`store.overlay.read_plotmap(cid)`; `put_campaign_greeting` →
`store.overlay.update_greeting(cid, gid, ...)`; `put_campaign_greeting_edges` →
existence via `store.overlay.read_greeting` then `store.overlay.set_edges(cid, gid, ...)`;
`delete_campaign_greeting` → `store.overlay.delete_greeting(cid, gid)`.

- [ ] **Step 2: Swap the store consumers**

- `greetings.availability` (~line 204): first param `items: list[dict]` replaces
  `world_root`; drop the internal `list_greetings` call
  (`items = [g for g in items if g["id"] not in skipped]`).
- `playing.py`: `available_greetings` builds
  `overlay.availability_items = overlay.list_greetings(cid)` and
  `plotmap = overlay.read_plotmap(cid)`; `mark_greeting`'s existence check →
  `overlay.read_greeting(cid, gid)`; `start_from_greeting` reads the greeting via
  `overlay.read_greeting(cid, gid)` (both call sites) and co-present default versions
  via `characters.read_character(overlay.char_root(cid, actor), actor)`;
  `player_tags` reads via `pcs.read_pc(overlay.pc_root(cid, a["id"]), a["id"])`.
- `appearances.py`: `suggestions` lists candidates via `overlay.list_characters(cid)`
  and reads in-scene cards via `characters.read_card(overlay.char_root(cid, char_id), ...)`;
  `pick_version`'s guard `actor_hash(croot, ...)` → `actor_hash(overlay.actor_root(cid, kind, actor_id), ...)`
  (appearances may import overlay — overlay does not import appearances, no cycle).
- `context.py`: `_world_info` iterates `overlay.list_entities(cid, kind)` /
  `overlay.read_entity(cid, kind, ...)` (pass `cid` down — `_world_info(cid, recent_text, ...)`);
  the current-setting lookup in `_assemble` → `overlay.read_entity(cid, "locations", current_loc)`;
  `_cast_directory_data` uses `overlay.character_refs(cid)`, `overlay.tagline(cid, ...)`,
  `characters.read_character(overlay.char_root(cid, char_id), char_id)`, and `_char_name`
  gains the overlay root the same way. Locked-cast card/persona reads (in `_assemble`,
  `scene_substitutions`, `_campaign_player_refs`, `cast_datetime_facts`,
  `_character_states`) stay on `croot` — lock ⇒ materialized is an invariant.
- `absorb.py`: `_entity_kind` and the lore/entity reads in `materialize()` →
  `overlay.read_entity`; wherever `apply_edits` writes entities, swap
  `entities.update_entity(croot, ...)` → `overlay.update_entity(cid, ...)` (card edits
  target locked actors and stay on croot).
- `chronicle.py` (~line 90): the location read → `overlay.read_entity(cid, "locations", ...)`.
- `suggest.py`: campaign candidate lists (`characters.list_characters(croot)`,
  `entities.list_entities(croot, "locations")`, etc. in `build_snapshot`/`_valid_ids`)
  → the overlay equivalents.

- [ ] **Step 3: Run the full backend suite; update the two live-read expectations**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`

Campaigns are still full copies, so overlay resolves everything to the campaign and
behavior is unchanged — **except** records the world gained *after* campaign creation
now appear in campaign lists immediately. Update the specific assertions in
`backend/tests/test_sync_store.py` / `test_playing_store.py` that create a world
record after the campaign and assert it is absent from campaign reads until accepted:
the record is now visible immediately (sync still lists it as `new` until Task 7).
Everything else must pass unmodified.

- [ ] **Step 4: Commit**

```bash
git add backend/src/grimoire/routes.py backend/src/grimoire/store/*.py backend/tests
git commit -m "refactor(backend): campaign reads and writes go through the overlay"
```

---

### Task 6: Thin `create_campaign`; locks stop copying assets; guards accept world versions

**Files:**
- Modify: `backend/src/grimoire/store/campaigns.py` (`create_campaign` ~line 67)
- Modify: `backend/src/grimoire/store/appearances.py` (`_copy_actor` ~line 78)
- Test: `backend/tests/test_campaigns_store.py`, `backend/tests/test_appearances_store.py`

**Interfaces:**
- Produces: `create_campaign` writes `world_copy: overlay`, an **empty** manifest, and
  copies only the calendar; `appearances._copy_actor` no longer copies `assets/`.

- [ ] **Step 1: Write the failing tests**

In `backend/tests/test_campaigns_store.py` add:

```python
def test_create_campaign_is_thin(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    entities.create_entity(wroot, "lore", "L", "body")
    characters.create_character(wroot, "Hero")
    cid = campaigns.create_campaign("C", wid)
    root = campaigns.campaign_root(cid)
    assert not (root / "lore").exists()
    assert not (root / "characters").exists()
    assert not (root / "plotmap.json").exists()
    assert campaigns.read_manifest(cid) == {}
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "overlay"
    # …but everything is readable through the overlay
    assert overlay.list_entities(cid, "lore")
    assert overlay.list_characters(cid)
```

In `backend/tests/test_appearances_store.py` add:

```python
def test_lock_materializes_card_but_not_assets(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    aid, vid = characters.create_character(wroot, "Hero")
    assets.put_image(wroot, aid, vid, "avatar", b"\x89PNG\r\n\x1a\nx", "png")
    cid = campaigns.create_campaign("C", wid)
    appearances.appear(cid, "s1", "characters", aid, vid, "npc")
    d = campaigns.campaign_root(cid) / "characters" / aid
    assert (d / f"{vid}.json").exists()
    assert not (d / "assets").exists()
    # serving still finds the world file
    assert overlay.image_root(cid, aid, vid, "avatar") == wroot
```

- [ ] **Step 2: Run to verify they fail** (campaign dir contains copies; assets dir exists).

- [ ] **Step 3: Implement**

`create_campaign` becomes:

```python
def create_campaign(name: str, world_id: str, region: str | None = None,
                     calendar: str | None = None) -> str:
    ensure_home()
    if not worlds.world_meta_path(world_id).exists():
        raise worlds.WorldNotFound(world_id)
    if calendar is not None:
        calendars.get_provider({"provider": calendar})  # unknown id -> CalendarError before anything is created
    cid = uniquify(slugify(name), lambda c: campaign_root(c).exists())
    root = campaign_root(cid)
    root.mkdir(parents=True)
    (root / "scenes").mkdir()
    now = now_iso()
    campaign_meta_path(cid).write_text(
        dump_frontmatter({"name": name, "world": world_id, "created": now, "updated": now,
                          "world_copy": "overlay"}, ""),
        encoding="utf-8",
    )
    # copy-on-write: nothing is copied up front; records materialize on divergence
    # (store/overlay.py) and sync.md tracks bases for materialized records only
    write_manifest(cid, {})
    calendars.copy_calendar(worlds.world_root(world_id), root)
    if region is not None or calendar is not None:
        cfg = calendars.read_calendar(root)
        if calendar is not None:
            cfg["primary"]["provider"] = calendar
            cfg["confirmed"] = True   # an explicit wizard choice
        if region is not None:
            cfg["primary"]["region"] = region
        calendars.validate_calendar(cfg)   # unknown provider -> CalendarError
        calendars.write_calendar(root, cfg)
    return cid
```

In `appearances._copy_actor`, delete the trailing block:

```python
    if kind == "characters":
        if (src_dir / "assets").exists():
            shutil.copytree(src_dir / "assets", dst_dir / "assets", dirs_exist_ok=True)
```

(and the now-unused `shutil` import if nothing else uses it).

- [ ] **Step 4: Run the full backend suite; update creation-shape tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`

Tests that assert copy-on-create contents (in `test_campaigns_store.py`,
`test_character_sync.py`, `test_routes.py` fixtures that reach into the campaign tree)
now assert the overlay-visible equivalents: replace "file exists under campaign root"
with the corresponding overlay/API read. Do **not** weaken lock-invariant tests —
picked-version purge still materializes files and those assertions stand.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/campaigns.py backend/src/grimoire/store/appearances.py backend/tests
git commit -m "feat(store): thin campaign creation — records materialize on divergence"
```

---

### Task 7: Sync rework — materialized records only; accept reverts to inherited

**Files:**
- Modify: `backend/src/grimoire/store/sync.py` (`incoming` ~line 34, `_plotmap_incoming` ~line 79, `_unpicked_incoming` ~line 130, `_advance` ~line 176)
- Test: `backend/tests/test_sync_store.py`

**Interfaces:**
- Consumes: `overlay.dematerialize_actor` (sync imports overlay; no cycle — overlay
  does not import sync).
- Produces: unchanged public API (`incoming`, `accept`, `reject`,
  `campaigns_for_world`) with the new semantics.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_sync_store.py`:

```python
def test_inherited_world_edit_produces_no_incoming(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "L", "v1")
    cid = campaigns.create_campaign("C", wid)
    entities.update_entity(wroot, "lore", eid, body="v2")
    assert sync.incoming(cid) == []
    assert overlay.read_entity(cid, "lore", eid)["body"] == "v2"   # live


def test_materialized_edit_conflicts_and_accept_dematerializes(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "L", "v1")
    cid = campaigns.create_campaign("C", wid)
    overlay.update_entity(cid, "lore", eid, body="campaign v1")   # diverge
    entities.update_entity(wroot, "lore", eid, body="world v2")
    items = sync.incoming(cid)
    assert [i["status"] for i in items] == ["conflict"]
    sync.accept(cid, [{"kind": "lore", "id": eid}])
    assert not (campaigns.campaign_root(cid) / "lore" / f"{eid}.md").exists()
    assert f"lore/{eid}" not in campaigns.read_manifest(cid)
    assert overlay.read_entity(cid, "lore", eid)["body"] == "world v2"


def test_reject_keeps_divergence_and_advances_base(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    eid = entities.create_entity(wroot, "lore", "L", "v1")
    cid = campaigns.create_campaign("C", wid)
    overlay.update_entity(cid, "lore", eid, body="campaign v1")
    entities.update_entity(wroot, "lore", eid, body="world v2")
    sync.reject(cid, [{"kind": "lore", "id": eid}])
    assert sync.incoming(cid) == []
    assert overlay.read_entity(cid, "lore", eid)["body"] == "campaign v1"
```

- [ ] **Step 2: Run to verify they fail** (world edit of an inherited record currently shows as `new`).

- [ ] **Step 3: Implement**

Rewrite `incoming`'s flat pass to iterate the manifest only (drop the world/campaign
`synced_refs` union):

```python
    refs: set[str] = set(manifest)
    out: list[dict] = []
    for ref in sorted(refs):
        kind, _, eid = ref.partition("/")
        if kind not in entities.SYNCED_KINDS:
            continue  # actor refs + plotmap are handled by their own passes
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        base_h = manifest.get(ref)
        if world_h is None or world_h == base_h:
            continue  # no incoming change (incl. world-side deletions, skipped)
        mine_h = entities.entity_hash(croot, kind, eid)
        if mine_h is None:
            continue  # copy gone since materialization: nothing to reconcile
        status = "update" if mine_h == base_h else "conflict"
        out.append({"ref": {"kind": kind, "id": eid}, "status": status,
                    "world": _entity_blob(wroot, kind, eid),
                    "mine": _entity_blob(croot, kind, eid)})
```

`_plotmap_incoming`: return `[]` unless `"plotmap" in manifest` **and** the campaign
plotmap file exists; the rest of its logic stands (mine is then never None).
`_unpicked_incoming`: build `refs` from the manifest only (delete the
`if wroot.exists(): refs |= …` world sweep); skip when `mine_h is None`.
`_actor_incoming` (locked) unchanged.

In `_advance`, the `copy=True` branches change from copy-world-file to
revert-to-inherited:

```python
        if kind == "plotmap":
            world_h = greetings.plotmap_hash(wroot) if wroot.exists() else None
            pending = ("plotmap" in manifest and world_h is not None
                       and manifest["plotmap"] != world_h)
            if not pending:
                continue
            if copy:   # take world: drop our copy, revert to inherited
                (croot / "plotmap.json").unlink(missing_ok=True)
                manifest.pop("plotmap", None)
            else:
                manifest["plotmap"] = world_h
            manifest_changed = touched = True
            continue
        if kind in appearances.ACTOR_KINDS:
            if appearances.record(cid).get(_ref_str(kind, eid)) is not None:
                if _advance_actor(cid, kind, eid, copy=copy):   # locked flow: unchanged
                    touched = True
                continue
            world_h = _dir_hash(wroot, kind, eid) if wroot.exists() else None
            if world_h is None or manifest.get(_ref_str(kind, eid)) == world_h:
                continue
            if copy:
                overlay.dematerialize_actor(cid, kind, eid)
                manifest.pop(_ref_str(kind, eid), None)
            else:
                manifest[_ref_str(kind, eid)] = world_h
            manifest_changed = touched = True
            continue
        world_h = entities.entity_hash(wroot, kind, eid) if wroot.exists() else None
        if world_h is None or manifest.get(_ref_str(kind, eid)) == world_h:
            continue
        if copy:
            (croot / kind / f"{eid}.md").unlink(missing_ok=True)
            manifest.pop(_ref_str(kind, eid), None)
        else:
            manifest[_ref_str(kind, eid)] = world_h
        manifest_changed = touched = True
```

- [ ] **Step 4: Run the full backend suite; update sync-store expectations**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`

Existing tests that asserted `new` items for world-side additions, or accept-copies
behavior, update to the new semantics (no item / accept dematerializes). The
locked-actor tests must pass byte-identically.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/sync.py backend/tests/test_sync_store.py
git commit -m "feat(sync): diff materialized records only; accept reverts to inherited"
```

---

### Task 8: `ensure_campaign_slim` migration + call-site swap

**Files:**
- Modify: `backend/src/grimoire/store/campaigns.py` (replace `ensure_campaign_copy` ~line 128)
- Modify: `backend/src/grimoire/routes.py` (the five `ensure_campaign_copy` call sites: 1008, 1226, 1263, 1272, 1282)
- Test: `backend/tests/test_campaigns_store.py`

**Interfaces:**
- Produces: `campaigns.ensure_campaign_slim(cid) -> None` (idempotent; done-marker
  `world_copy: overlay`; skips unmarked when the world dir is missing).
  `ensure_campaign_copy` is deleted (its tests convert to slim tests).

- [ ] **Step 1: Write the failing tests**

```python
def _fat_campaign(monkeypatch, tmp_path):
    """A pre-overlay full-copy campaign: build thin, then hand-copy the world
    like the old create_campaign did, stamp world_copy: full. Three lore
    entries cover the slim cases: `same` (redundant copy), `diverged`
    (campaign body differs), `removed` (user deleted the copy, base ref kept)."""
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    same = entities.create_entity(wroot, "lore", "Same", "same")
    diverged = entities.create_entity(wroot, "lore", "Diverged", "world text")
    removed = entities.create_entity(wroot, "lore", "Removed", "removed text")
    aid, vid = characters.create_character(wroot, "Hero")
    assets.put_image(wroot, aid, vid, "avatar", b"\x89PNG\r\n\x1a\nx", "png")
    cid = campaigns.create_campaign("C", wid)
    croot = campaigns.campaign_root(cid)
    manifest = {}
    (croot / "lore").mkdir()
    for xid in (same, diverged, removed):
        (croot / "lore" / f"{xid}.md").write_text(
            (wroot / "lore" / f"{xid}.md").read_text(encoding="utf-8"), encoding="utf-8")
        manifest[f"lore/{xid}"] = entities.entity_hash(wroot, "lore", xid)
    import shutil
    shutil.copytree(wroot / "characters" / aid, croot / "characters" / aid)
    manifest[f"characters/{aid}"] = characters.dir_hash(wroot, aid)
    campaigns.write_manifest(cid, manifest)
    entities.update_entity(croot, "lore", diverged, body="campaign text")
    (croot / "lore" / f"{removed}.md").unlink()
    mp = campaigns.campaign_meta_path(cid)
    meta, body = frontmatter.parse_frontmatter(mp.read_text(encoding="utf-8"))
    meta["world_copy"] = "full"
    mp.write_text(frontmatter.dump_frontmatter(meta, body), encoding="utf-8")
    return wroot, cid, same, diverged, removed, aid, vid


def test_slim_deletes_redundant_keeps_diverged_and_deletions(monkeypatch, tmp_path):
    wroot, cid, same, diverged, removed, aid, vid = _fat_campaign(monkeypatch, tmp_path)
    campaigns.ensure_campaign_slim(cid)
    croot = campaigns.campaign_root(cid)
    assert not (croot / "lore" / f"{same}.md").exists()                 # slimmed
    assert f"lore/{same}" not in campaigns.read_manifest(cid)
    assert overlay.read_entity(cid, "lore", same)["body"] == "same"     # inherited now
    assert (croot / "lore" / f"{diverged}.md").exists()                 # kept
    assert f"lore/{removed}" in overlay.deleted(cid)                    # deletion preserved
    assert not (croot / "characters" / aid).exists() \
        or not (croot / "characters" / aid / "character.md").exists()   # actor dematerialized
    assert overlay.image_root(cid, aid, vid, "avatar") == wroot         # asset dupe pruned
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "overlay"
    campaigns.ensure_campaign_slim(cid)                                 # second run: no-op


def test_slim_skips_when_world_missing(monkeypatch, tmp_path):
    wroot, cid, *_ = _fat_campaign(monkeypatch, tmp_path)
    import shutil
    shutil.rmtree(wroot)
    campaigns.ensure_campaign_slim(cid)
    assert campaigns.read_campaign(cid)["meta"]["world_copy"] == "full"  # untouched, retried later
```

- [ ] **Step 2: Run to verify they fail** — `AttributeError: no attribute 'ensure_campaign_slim'`.

- [ ] **Step 3: Implement**

Delete `ensure_campaign_copy` and add (imports: `filecmp` at module top):

```python
def ensure_campaign_slim(cid: str) -> None:
    """One-time lazy migration of a full-copy campaign to the overlay layout.
    Deletes campaign files that are provably redundant — flat/actor content
    whose hash equals both the recorded sync base and the current world hash,
    plus byte-identical asset/sidecar copies — tombstones refs whose copy the
    user had deleted, and stamps world_copy: overlay. Skips (unmarked) while
    the world dir is missing so a late-syncing store slims on a later access.
    Locked actors keep their cards (the lock invariant needs them); diverged
    records and campaign-local files are never touched."""
    mp = campaign_meta_path(cid)
    if not mp.exists():
        raise CampaignNotFound(cid)
    meta, body = parse_frontmatter(mp.read_text(encoding="utf-8"))
    if meta.get("world_copy") == "overlay":
        return
    root = campaign_root(cid)
    wroot = worlds.world_root(meta.get("world", ""))
    if not wroot.exists():
        return
    from . import appearances, characters, greetings, overlay, pcs  # campaigns is imported by these

    locked = set(appearances.record(cid))
    manifest = read_manifest(cid)
    for ref, base in sorted(list(manifest.items())):
        kind, _, eid = ref.partition("/")
        if ref == "plotmap":
            p = root / "plotmap.json"
            if not p.exists():
                manifest.pop(ref)
            elif greetings.plotmap_hash(root) == base == greetings.plotmap_hash(wroot):
                p.unlink()
                manifest.pop(ref)
            continue
        if kind in appearances.ACTOR_KINDS:
            if ref in locked:
                manifest.pop(ref)   # a lock owns its base in appearances.json
                continue
            dh = characters.dir_hash if kind == "characters" else pcs.dir_hash
            mine_h = dh(root, eid)
            if mine_h is None:
                manifest.pop(ref)
            elif mine_h == base == dh(wroot, eid):
                overlay.dematerialize_actor(cid, kind, eid)
                manifest.pop(ref)
            continue
        p = root / kind / f"{eid}.md"
        if not p.exists():
            if (wroot / kind / f"{eid}.md").exists():
                overlay.add_deleted(cid, ref)   # keep the user's deletion deleted
            manifest.pop(ref)
        elif entities.entity_hash(root, kind, eid) == base == entities.entity_hash(wroot, kind, eid):
            p.unlink()
            manifest.pop(ref)
    write_manifest(cid, manifest)
    _prune_duplicate_files(root, wroot)
    meta["world_copy"] = "overlay"
    mp.write_text(dump_frontmatter(meta, body), encoding="utf-8")


def _prune_duplicate_files(root: Path, wroot: Path) -> None:
    """Delete campaign files byte-identical to the same relative path in the
    world: asset files and actor sidecars (tagline.md; focus.json lives under
    assets/). The file-level overlay serves them from the world afterwards.
    Campaign-only or diverged files stay; emptied dirs are removed."""
    for kind in ("characters", "pcs", "locations", "lore"):
        base = root / kind
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root)
            if "assets" not in rel.parts and p.name != "tagline.md":
                continue
            w = wroot / rel
            if w.exists() and filecmp.cmp(p, w, shallow=False):
                p.unlink()
        for d in sorted((x for x in base.rglob("*") if x.is_dir()), reverse=True):
            if not any(d.iterdir()):
                d.rmdir()
        if base.exists() and not any(base.iterdir()):
            base.rmdir()
```

Swap the five `store.campaigns.ensure_campaign_copy(cid)` call sites in `routes.py`
to `store.campaigns.ensure_campaign_slim(cid)` (routes.py:1008 comment becomes
"lazy slim of pre-overlay campaigns").

- [ ] **Step 4: Run the full backend suite; convert `ensure_campaign_copy` tests**

The old backfill tests in `test_campaigns_store.py` (and any in `test_routes.py`)
delete; their scenarios are covered by the new slim tests plus overlay fallthrough.
All suites PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/campaigns.py backend/src/grimoire/routes.py backend/tests
git commit -m "feat(store): ensure_campaign_slim — lazy migration of full-copy campaigns"
```

---

### Task 9: World-deletion guard

**Files:**
- Modify: `backend/src/grimoire/store/worlds.py` (`delete_world` ~line 94)
- Modify: `backend/src/grimoire/routes.py` (the `DELETE /worlds/{wid}` route)
- Test: `backend/tests/test_campaigns_store.py` (or `test_routes.py` for the 409)

**Interfaces:**
- Produces: `worlds.WorldInUse(wid, names: list[str])` exception; `delete_world`
  raises it when any campaign's `world` meta references the world; the route maps it
  to HTTP 409 `{"detail": "world is used by campaigns: <names>"}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_delete_world_blocked_while_campaigns_reference_it(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("C", wid)
    with pytest.raises(worlds.WorldInUse):
        worlds.delete_world(wid)
    campaigns.delete_campaign(cid)
    worlds.delete_world(wid)   # now allowed
    assert not worlds.world_root(wid).exists()
```

And a route test asserting `DELETE /api/worlds/{wid}` → 409 with the campaign name in
`detail`, then 200 after the campaign is deleted.

- [ ] **Step 2: Run to verify it fails** (`AttributeError: WorldInUse` / world deleted anyway).

- [ ] **Step 3: Implement**

```python
class WorldInUse(Exception):
    def __init__(self, wid: str, names: list[str]):
        self.names = names
        super().__init__(f"world is used by campaigns: {', '.join(names)}")


def delete_world(wid: str) -> None:
    root = world_root(wid)
    if not world_meta_path(wid).exists():
        raise WorldNotFound(wid)
    from . import campaigns  # function-level: campaigns imports worlds at module level
    used_by = [c["name"] for c in campaigns.list_campaigns() if c.get("world") == wid]
    if used_by:
        raise WorldInUse(wid, used_by)
    shutil.rmtree(root)
```

Route: wrap the existing delete handler's call in
`except store.worlds.WorldInUse as exc: raise HTTPException(status_code=409, detail=str(exc))`.

- [ ] **Step 4: Run the full backend suite** — PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/worlds.py backend/src/grimoire/routes.py backend/tests
git commit -m "feat(backend): block deleting a world that campaigns still read through"
```

---

### Task 10: Frontend — surface the world-delete 409; final verification

**Files:**
- Modify: `frontend/src/routes/WorldsView.tsx` (`remove`, line 36)
- Test: `frontend/src/routes/WorldsView.test.tsx`

**Interfaces:**
- Consumes: `DELETE /api/worlds/{wid}` 409 (Task 9). No API-shape changes anywhere
  else, so no other frontend work is required.

- [ ] **Step 1: Write the failing test**

```tsx
test("a blocked world delete shows the server's message", async () => {
  (api.deleteWorld as any).mockRejectedValue(new Error("world is used by campaigns: C"));
  const alert = vi.spyOn(window, "alert").mockImplementation(() => {});
  render(<MemoryRouter><WorldsView /></MemoryRouter>);
  fireEvent.click(await screen.findByLabelText("Delete W1"));
  await waitFor(() => expect(alert).toHaveBeenCalledWith(
    expect.stringContaining("world is used by campaigns")));
  alert.mockRestore();
});
```

(Match the file's existing render/mocking helpers; the delete flow already stubs
`window.confirm` — reuse that pattern.)

- [ ] **Step 2: Run to verify it fails**

From `frontend/`: `npx vitest run src/routes/WorldsView.test.tsx` — new test FAILS
(unhandled rejection / alert not called).

- [ ] **Step 3: Implement**

```tsx
  async function remove(w: WorldMeta) {
    if (!window.confirm(`Delete world '${w.name}'?`)) return;
    try {
      await api.deleteWorld(w.id);
    } catch (err: any) {
      window.alert(err?.message ?? "Could not delete the world.");
      return;
    }
    setWorlds(await api.listWorlds());
  }
```

(The old confirm text claimed "Campaigns already made from it keep their copies" —
no longer true; drop it.)

- [ ] **Step 4: Full verification**

- From `frontend/`: `npx vitest run` → all PASS; `npx tsc -b` → clean.
- From repo root: `backend/.venv/Scripts/python.exe -m pytest backend -q` → all PASS.
- End-to-end with the `verify` skill (isolated store, mocked OpenRouter):
  1. Create a world with a character (with avatar) and a lore entry; create a campaign.
  2. Confirm the campaign dir on disk holds only `campaign.md`, `sync.md`, `scenes/`,
     the calendar file — no `lore/`, `characters/`, `plotmap.json`.
  3. Browse the campaign's Characters/Lore tabs: inherited records render, the avatar
     serves.
  4. Edit the lore entry campaign-side → the file materializes; edit it world-side →
     the Sync page shows one conflict; accept → the campaign copy is gone and the
     world text shows.
  5. Start a scene with the character → one version JSON materializes, no `assets/`
     dir appears; the scene plays.
  6. Try deleting the world → blocked with the campaign name; delete the campaign,
     then the world deletes.
  7. Point `GRIMOIRE_HOME` at a **copy** of a real fat store, open a campaign, confirm
     `ensure_campaign_slim` shrank it (compare `du` before/after) and scenes still play.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/WorldsView.tsx frontend/src/routes/WorldsView.test.tsx
git commit -m "feat(frontend): surface blocked world deletion; drop stale copy claim"
```
