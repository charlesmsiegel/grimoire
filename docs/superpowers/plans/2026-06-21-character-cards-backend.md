# Character Cards (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn characters from generic markdown entities into multi-card SillyTavern-V3 containers, and add the campaign appearance/version-lock model, name-mention suggestions, and character-aware sync — backend + API only.

**Architecture:** A new `store/characters.py` owns container/version storage (one folder per character, one JSON card per named version) and is *not* part of the generic `entities.py` (which is narrowed to locations + lore). A new `store/cards.py` parses/serializes V3/V2 JSON, PNG-`tEXt`, and CHARX. A new `store/appearances.py` records, per campaign, which characters have appeared, their locked version, the sync base hash, and their scenes (stored as `appearances.json` because the existing frontmatter writer is string-scalar-only and cannot nest). `store/sync.py` is extended so `incoming/accept/reject` cover characters alongside locations/lore. Dedicated character routes are declared *before* the generic `/{kind}` catch-all routes.

**Tech Stack:** Python 3, FastAPI, pytest + `fastapi.testclient.TestClient`. Pure-stdlib for binary formats (`struct`, `zlib`, `base64`, `zipfile`) — no Pillow. Frontend (Vite/React/TS) is out of scope for this plan.

## Global Constraints

- **Card spec:** SillyTavern **V3** (`{spec: "chara_card_v3", spec_version: "3.0", data: {...}}`). V2 cards are upconverted to V3 on read; unknown fields preserved under `data.extensions`.
- **Entity kinds are now `("locations", "lore")`** — characters are handled by the dedicated module/routes, never the generic entity path.
- **No auto timestamps on cards.** A card's hash must change only when its content changes. All card writes serialize canonically via `cards.dumps()` (`json.dumps(card, indent=2, sort_keys=True, ensure_ascii=False)` + trailing newline) so identical content ⇒ identical bytes ⇒ identical hash on both world and campaign sides.
- **IDs:** slugify + uniquify (the existing `paths.slugify`/`paths.uniquify`), no date prefix, for character ids and version ids.
- **Characters never produce a sync `new` status** and world-side character deletions are skipped (consistent with the worlds/campaigns spec).
- **Locks are permanent:** once a campaign locks a character to a version, the version never changes (no re-version action this iteration).
- **All new routes live under `/api`.** Hashes are `sha256(file_text)` hex.
- Source layout: store modules in `backend/src/grimoire/store/`, routes in `backend/src/grimoire/routes.py`, tests in `backend/tests/`. Run tests from `backend/` with `pytest`.

---

### Task 1: Narrow generic entity kinds to locations + lore

Removing `characters` from `ENTITY_KINDS` is the keystone: it makes the generic routes 404 for characters, makes copy-on-create skip characters, and makes the existing sync engine ignore characters — all for free. Several existing tests use `characters` as a generic entity and must be migrated in the same task to keep the suite green.

**Files:**
- Modify: `backend/src/grimoire/store/entities.py:16`
- Modify (test migration): `backend/tests/test_entities_store.py`, `backend/tests/test_campaigns_store.py`, `backend/tests/test_sync_store.py`, `backend/tests/test_worlds_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `entities.ENTITY_KINDS == ("locations", "lore")`. `entities.create_entity(root, "characters", ...)` now raises `entities.UnknownKind`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_entities_store.py`:

```python
def test_characters_is_not_a_generic_kind():
    import pytest
    from grimoire.store import entities
    assert "characters" not in entities.ENTITY_KINDS
    with pytest.raises(entities.UnknownKind):
        entities.create_entity_unused = None  # noqa: keep import side-effect minimal
```

Replace that placeholder body with a real assertion once `tmp_path` is available — use:

```python
def test_characters_is_not_a_generic_kind(tmp_path):
    import pytest
    from grimoire.store import entities
    assert "characters" not in entities.ENTITY_KINDS
    with pytest.raises(entities.UnknownKind):
        entities.create_entity(tmp_path, "characters", "X")
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `cd backend && pytest tests/test_entities_store.py::test_characters_is_not_a_generic_kind -v`
Expected: FAIL (currently `characters` IS a kind, so no exception is raised).

- [ ] **Step 3: Make the change**

`backend/src/grimoire/store/entities.py:16` — change:

```python
ENTITY_KINDS: tuple[str, ...] = ("locations", "lore")
```

- [ ] **Step 4: Migrate existing tests off the `characters` generic kind**

These call sites use `characters` as a generic entity; swap them to `locations` (or `lore`) so they exercise the same code paths. Exact edits:

- `test_entities_store.py`: replace every `"characters"` with `"locations"`; in the counts test change the call that created the location to `lore` so both kinds appear, and update the assertion to `entities.entity_counts(tmp_path) == {"locations": 1, "lore": 1}` and `all_refs(tmp_path) == {("lore", "a"), ("locations", "b")}` (adjust to whatever two kinds you create).
- `test_campaigns_store.py`: replace `"characters"`/`"characters/..."` with `"locations"`/`"locations/..."`. The manifest round-trip test (`{"characters/a": ...}`) is just opaque strings — leave the keys or rename freely; they don't need to be valid kinds.
- `test_sync_store.py`: in `_setup` and every `update_entity(... "characters" ...)` / ref `{"kind": "characters", ...}`, replace `characters`→`locations` and `seraphine` stays as the id. (These tests now validate locations sync; character sync gets its own test file in Task 7.)
- `test_worlds_store.py`: the `counts["characters"]` assertions — change the created kind to `locations` and assert `counts["locations"] == 1`.
- `test_routes.py`: in `test_world_entity_crud` and `test_campaign_inherits_world_entities` (and any other), change the `/characters` generic-entity calls to `/locations`.

- [ ] **Step 5: Run the whole suite green**

Run: `cd backend && pytest -q`
Expected: PASS (no test references `characters` as a generic kind anymore).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/entities.py backend/tests
git commit -m "refactor(store): narrow generic entity kinds to locations+lore"
```

---

### Task 2: `characters.py` — container + version CRUD + hashing

**Files:**
- Create: `backend/src/grimoire/store/characters.py`
- Modify: `backend/src/grimoire/store/__init__.py` (re-export `characters`)
- Test: `backend/tests/test_characters_store.py`

**Interfaces:**
- Consumes: `paths.slugify`, `paths.uniquify`; `frontmatter.dump_frontmatter`/`parse_frontmatter`; `cards.dumps`/`cards.loads` (Task 4 — for Task 2 use a local minimal serializer, then Task 4 swaps to `cards`). To avoid an ordering dependency, Task 2 defines its own canonical `_dumps`/`_loads` here and Task 4 does not need to touch them.
- Produces:
  ```python
  CharacterNotFound(Exception); VersionNotFound(Exception)
  list_characters(root) -> list[dict]      # [{id, name, default_version, versions:[{id,name}]}]
  read_character(root, cid) -> dict         # {meta:{id,name,default_version}, versions:[{id,name,card}]}
  read_card(root, cid, vid) -> dict         # the V3 card object
  create_character(root, name, version_name="default", card=None) -> tuple[str,str]  # (cid, vid)
  create_version(root, cid, version_name, card) -> str   # vid
  update_version(root, cid, vid, card) -> None
  set_default_version(root, cid, vid) -> None
  delete_character(root, cid) -> None
  delete_version(root, cid, vid) -> None    # refuses to delete the last version
  card_hash(root, cid, vid) -> str | None
  character_count(root) -> int
  character_refs(root) -> list[str]         # ids present under <root>/characters/
  blank_card(name) -> dict                  # a minimal valid V3 card
  ```

Storage layout produced: `<root>/characters/<cid>/character.md` (frontmatter `name`, `default_version`) + `<root>/characters/<cid>/<vid>.json` per version + optional `<root>/characters/<cid>/assets/`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_characters_store.py`:

```python
import pytest

from grimoire.store import characters as ch


def test_create_and_read_single_card(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    assert cid == "seraphine"
    assert vid == "default"
    card = ch.read_card(tmp_path, cid, vid)
    assert card["spec"] == "chara_card_v3"
    assert card["data"]["name"] == "Seraphine"
    meta = ch.read_character(tmp_path, cid)
    assert meta["meta"]["default_version"] == "default"
    assert [v["id"] for v in meta["versions"]] == ["default"]


def test_add_second_version_and_set_default(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    v2 = ch.create_version(tmp_path, cid, "Corrupted", ch.blank_card("Seraphine"))
    assert v2 == "corrupted"
    ch.set_default_version(tmp_path, cid, v2)
    assert ch.read_character(tmp_path, cid)["meta"]["default_version"] == "corrupted"
    assert {v["id"] for v in ch.list_characters(tmp_path)[0]["versions"]} == {"default", "corrupted"}


def test_hash_is_content_stable(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    h1 = ch.card_hash(tmp_path, cid, vid)
    # rewriting identical content does not change the hash
    ch.update_version(tmp_path, cid, vid, ch.read_card(tmp_path, cid, vid))
    assert ch.card_hash(tmp_path, cid, vid) == h1
    # a content change changes the hash
    card = ch.read_card(tmp_path, cid, vid)
    card["data"]["description"] = "the drowned keeper"
    ch.update_version(tmp_path, cid, vid, card)
    assert ch.card_hash(tmp_path, cid, vid) != h1


def test_delete_last_version_refused(tmp_path):
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    with pytest.raises(ch.VersionNotFound):
        ch.delete_version(tmp_path, cid, "ghost")
    with pytest.raises(ValueError):
        ch.delete_version(tmp_path, cid, vid)  # last one


def test_missing_character_and_version(tmp_path):
    with pytest.raises(ch.CharacterNotFound):
        ch.read_character(tmp_path, "nobody")
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    with pytest.raises(ch.VersionNotFound):
        ch.read_card(tmp_path, cid, "nope")


def test_counts_and_refs(tmp_path):
    ch.create_character(tmp_path, "A")
    ch.create_character(tmp_path, "B")
    assert ch.character_count(tmp_path) == 2
    assert set(ch.character_refs(tmp_path)) == {"a", "b"}
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && pytest tests/test_characters_store.py -v`
Expected: FAIL with `ModuleNotFoundError: grimoire.store.characters`.

- [ ] **Step 3: Implement `characters.py`**

Create `backend/src/grimoire/store/characters.py`:

```python
"""Character containers: one folder per character, one JSON V3 card per version.

Unlike generic entities (one markdown file each), a character is a directory:
  <root>/characters/<cid>/character.md   # frontmatter: name, default_version
  <root>/characters/<cid>/<vid>.json     # a SillyTavern V3 card
  <root>/characters/<cid>/assets/        # optional images (from PNG/CHARX import)
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify


class CharacterNotFound(Exception):
    pass


class VersionNotFound(Exception):
    pass


def _safe(part: str) -> bool:
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _chars_dir(root: Path) -> Path:
    return root / "characters"


def _char_dir(root: Path, cid: str) -> Path:
    return _chars_dir(root) / cid


def _meta_path(root: Path, cid: str) -> Path:
    return _char_dir(root, cid) / "character.md"


def _card_path(root: Path, cid: str, vid: str) -> Path:
    return _char_dir(root, cid) / f"{vid}.json"


def _dumps(card: dict) -> str:
    return json.dumps(card, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def blank_card(name: str) -> dict:
    return {
        "spec": "chara_card_v3",
        "spec_version": "3.0",
        "data": {
            "name": name,
            "description": "",
            "personality": "",
            "scenario": "",
            "first_mes": "",
            "mes_example": "",
            "alternate_greetings": [],
            "tags": [],
            "extensions": {},
        },
    }


def _require_char(root: Path, cid: str) -> Path:
    d = _char_dir(root, cid)
    if not _safe(cid) or not _meta_path(root, cid).exists():
        raise CharacterNotFound(cid)
    return d


def create_character(root: Path, name: str, version_name: str = "default", card: dict | None = None) -> tuple[str, str]:
    _chars_dir(root).mkdir(parents=True, exist_ok=True)
    cid = uniquify(slugify(name), lambda c: _char_dir(root, c).exists())
    _char_dir(root, cid).mkdir(parents=True)
    vid = slugify(version_name)
    _card_path(root, cid, vid).write_text(_dumps(card or blank_card(name)), encoding="utf-8")
    _meta_path(root, cid).write_text(
        dump_frontmatter({"name": name, "default_version": vid}, ""), encoding="utf-8"
    )
    return cid, vid


def create_version(root: Path, cid: str, version_name: str, card: dict) -> str:
    _require_char(root, cid)
    vid = uniquify(slugify(version_name), lambda v: _card_path(root, cid, v).exists())
    _card_path(root, cid, vid).write_text(_dumps(card), encoding="utf-8")
    return vid


def update_version(root: Path, cid: str, vid: str, card: dict) -> None:
    _require_char(root, cid)
    p = _card_path(root, cid, vid)
    if not _safe(vid) or not p.exists():
        raise VersionNotFound(vid)
    p.write_text(_dumps(card), encoding="utf-8")


def set_default_version(root: Path, cid: str, vid: str) -> None:
    _require_char(root, cid)
    if not _safe(vid) or not _card_path(root, cid, vid).exists():
        raise VersionNotFound(vid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta["default_version"] = vid
    _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def _version_ids(root: Path, cid: str) -> list[str]:
    return sorted(p.stem for p in _char_dir(root, cid).glob("*.json"))


def read_card(root: Path, cid: str, vid: str) -> dict:
    _require_char(root, cid)
    p = _card_path(root, cid, vid)
    if not _safe(vid) or not p.exists():
        raise VersionNotFound(vid)
    return json.loads(p.read_text(encoding="utf-8"))


def read_character(root: Path, cid: str) -> dict:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    versions = []
    for vid in _version_ids(root, cid):
        card = read_card(root, cid, vid)
        versions.append({"id": vid, "name": card["data"].get("name", vid), "card": card})
    return {
        "meta": {"id": cid, "name": meta.get("name", cid), "default_version": meta.get("default_version", "")},
        "versions": versions,
    }


def list_characters(root: Path) -> list[dict]:
    out: list[dict] = []
    d = _chars_dir(root)
    if d.exists():
        for cd in sorted(p for p in d.iterdir() if p.is_dir() and (p / "character.md").exists()):
            cid = cd.name
            meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
            out.append({
                "id": cid,
                "name": meta.get("name", cid),
                "default_version": meta.get("default_version", ""),
                "versions": [{"id": v, "name": read_card(root, cid, v)["data"].get("name", v)}
                             for v in _version_ids(root, cid)],
            })
    return out


def delete_version(root: Path, cid: str, vid: str) -> None:
    _require_char(root, cid)
    p = _card_path(root, cid, vid)
    if not _safe(vid) or not p.exists():
        raise VersionNotFound(vid)
    if len(_version_ids(root, cid)) == 1:
        raise ValueError("cannot delete the last version of a character")
    p.unlink()
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    if meta.get("default_version") == vid:
        meta["default_version"] = _version_ids(root, cid)[0]
        _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def delete_character(root: Path, cid: str) -> None:
    import shutil
    _require_char(root, cid)
    shutil.rmtree(_char_dir(root, cid))


def card_hash(root: Path, cid: str, vid: str) -> str | None:
    p = _card_path(root, cid, vid)
    if not _safe(cid) or not _safe(vid) or not p.exists():
        return None
    return hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def character_count(root: Path) -> int:
    d = _chars_dir(root)
    return sum(1 for p in d.iterdir() if p.is_dir() and (p / "character.md").exists()) if d.exists() else 0


def character_refs(root: Path) -> list[str]:
    d = _chars_dir(root)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "character.md").exists())
```

- [ ] **Step 4: Re-export from the package**

`backend/src/grimoire/store/__init__.py` — add `characters` to the imports/`__all__` exactly as the existing modules are re-exported (mirror how `worlds`/`campaigns` appear there).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && pytest tests/test_characters_store.py -v`
Expected: PASS (all six tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/characters.py backend/src/grimoire/store/__init__.py backend/tests/test_characters_store.py
git commit -m "feat(store): character containers with versioned V3 cards"
```

---

### Task 3: World character routes + world counts include characters

**Files:**
- Modify: `backend/src/grimoire/routes.py` (add models + routes BEFORE the generic `/worlds/{wid}/{kind}` routes at line ~186)
- Modify: `backend/src/grimoire/store/worlds.py` (merge character count into `counts`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.characters.*` from Task 2; `_world_root_or_404` (existing helper).
- Produces routes: `GET/POST /worlds/{wid}/characters`, `GET/PUT/DELETE /worlds/{wid}/characters/{cid}`, `GET/POST /worlds/{wid}/characters/{cid}/versions`, `PUT/DELETE /worlds/{wid}/characters/{cid}/versions/{vid}`. World `counts` dict now includes a `"characters"` key.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routes.py`:

```python
def test_world_character_container_crud(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    cid = r.json()["character"]
    assert cid == "seraphine"
    # default version exists
    got = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert [v["id"] for v in got["versions"]] == ["default"]
    # add a version
    vid = client.post(f"/api/worlds/{wid}/characters/{cid}/versions",
                      json={"name": "Corrupted", "card": got["versions"][0]["card"]}).json()["version"]
    assert vid == "corrupted"
    # world counts include characters
    assert client.get(f"/api/worlds/{wid}").json()["counts"]["characters"] == 1
    # the generic entity route refuses characters now
    assert client.get(f"/api/worlds/{wid}/characters/{cid}/extra/nope").status_code == 404
    # delete
    assert client.delete(f"/api/worlds/{wid}/characters/{cid}").status_code == 200
```

- [ ] **Step 2: Run to confirm it fails**

Run: `cd backend && pytest tests/test_routes.py::test_world_character_container_crud -v`
Expected: FAIL (routes return data shaped for the generic entity handler / 404).

- [ ] **Step 3: Add request models**

In `routes.py` near the other `BaseModel`s (after `EntityUpdate`, ~line 46):

```python
class CharacterCreate(BaseModel):
    name: str
    version_name: str = "default"
    card: dict | None = None


class VersionCreate(BaseModel):
    name: str
    card: dict


class VersionUpdate(BaseModel):
    card: dict


class DefaultVersion(BaseModel):
    default_version: str
```

- [ ] **Step 4: Add the routes BEFORE `@router.get("/worlds/{wid}/{kind}")`**

Insert immediately above the `# ---- generic entity CRUD ----` section (so FastAPI matches these literal `characters` paths before the `/{kind}` catch-all):

```python
# ---- world characters (dedicated; declared before the generic /{kind} routes) ----
@router.get("/worlds/{wid}/characters")
def get_world_characters(wid: str):
    return store.characters.list_characters(_world_root_or_404(wid))


@router.post("/worlds/{wid}/characters")
def post_world_character(wid: str, body: CharacterCreate):
    cid, vid = store.characters.create_character(
        _world_root_or_404(wid), body.name, body.version_name, body.card
    )
    return {"character": cid, "version": vid}


@router.get("/worlds/{wid}/characters/{cid}")
def get_world_character(wid: str, cid: str):
    try:
        return store.characters.read_character(_world_root_or_404(wid), cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")


@router.put("/worlds/{wid}/characters/{cid}")
def put_world_character(wid: str, cid: str, body: DefaultVersion):
    try:
        store.characters.set_default_version(_world_root_or_404(wid), cid, body.default_version)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/worlds/{wid}/characters/{cid}")
def delete_world_character(wid: str, cid: str):
    try:
        store.characters.delete_character(_world_root_or_404(wid), cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"ok": True}


@router.post("/worlds/{wid}/characters/{cid}/versions")
def post_world_version(wid: str, cid: str, body: VersionCreate):
    try:
        vid = store.characters.create_version(_world_root_or_404(wid), cid, body.name, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"version": vid}


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}")
def put_world_version(wid: str, cid: str, vid: str, body: VersionUpdate):
    try:
        store.characters.update_version(_world_root_or_404(wid), cid, vid, body.card)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}")
def delete_world_version(wid: str, cid: str, vid: str):
    try:
        store.characters.delete_version(_world_root_or_404(wid), cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}
```

- [ ] **Step 5: Merge character count into world counts**

`backend/src/grimoire/store/worlds.py` — in both `list_worlds` (the `"counts": entities.entity_counts(d)` line, ~44) and `read_world` (~67), wrap the counts so characters are included. Add at the top: `from . import characters` (alongside `from . import entities`). Then replace `entities.entity_counts(d)` with `{**entities.entity_counts(d), "characters": characters.character_count(d)}` and likewise for `read_world` using `world_root(wid)`.

- [ ] **Step 6: Run tests**

Run: `cd backend && pytest tests/test_routes.py -v && pytest -q`
Expected: PASS (new test passes; full suite still green).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/routes.py backend/src/grimoire/store/worlds.py backend/tests/test_routes.py
git commit -m "feat(api): dedicated world character routes + character world counts"
```

---

### Task 4: `cards.py` — V3/V2 JSON, PNG-tEXt, CHARX import/export

**Files:**
- Create: `backend/src/grimoire/store/cards.py`
- Modify: `backend/src/grimoire/store/characters.py` (add `import_card`, `export_card` delegating to `cards`)
- Modify: `backend/src/grimoire/store/__init__.py` (re-export `cards`)
- Test: `backend/tests/test_cards.py`

**Interfaces:**
- Produces in `cards.py`:
  ```python
  class CardParseError(Exception): ...
  loads(data: bytes, fmt: str) -> dict        # fmt in {"json","png","charx"}; returns a V3 card; upconverts V2
  dumps(card: dict, fmt: str, avatar: bytes | None = None) -> bytes   # serialize to json|png|charx
  to_v3(obj: dict) -> dict                     # upconvert a V2 or bare-data object to V3
  ```
- Produces in `characters.py`:
  ```python
  import_card(root, data: bytes, fmt: str, into_cid: str | None = None, name: str | None = None) -> tuple[str,str]
  export_card(root, cid, vid, fmt: str) -> bytes
  ```

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_cards.py`:

```python
import base64
import json
import struct
import zipfile
import zlib
from io import BytesIO

import pytest

from grimoire.store import cards


def _v3():
    return {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "keeper", "extensions": {}}}


def _png_with_text(keyword: str, text: str) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    def chunk(typ: bytes, payload: bytes) -> bytes:
        body = typ + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    text_chunk = chunk(b"tEXt", keyword.encode("latin-1") + b"\x00" + text.encode("latin-1"))
    return sig + chunk(b"IHDR", ihdr) + text_chunk + chunk(b"IEND", b"")


def test_loads_bare_v3_json():
    card = cards.loads(json.dumps(_v3()).encode(), "json")
    assert card["spec"] == "chara_card_v3"
    assert card["data"]["name"] == "Seraphine"


def test_loads_v2_json_upconverts():
    v2 = {"spec": "chara_card_v2", "spec_version": "2.0",
          "data": {"name": "Old", "description": "d", "first_mes": "hi", "some_unknown": 7}}
    card = cards.loads(json.dumps(v2).encode(), "json")
    assert card["spec"] == "chara_card_v3"
    assert card["data"]["name"] == "Old"
    assert card["data"]["extensions"]["some_unknown"] == 7  # unknown preserved


def test_loads_png_ccv3_then_chara_fallback():
    text = base64.b64encode(json.dumps(_v3()).encode()).decode()
    png = _png_with_text("ccv3", text)
    assert cards.loads(png, "png")["data"]["name"] == "Seraphine"
    # chara fallback (V2 payload) upconverts
    v2 = {"spec": "chara_card_v2", "data": {"name": "Fall", "description": ""}}
    png2 = _png_with_text("chara", base64.b64encode(json.dumps(v2).encode()).decode())
    assert cards.loads(png2, "png")["data"]["name"] == "Fall"


def test_loads_charx():
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("card.json", json.dumps(_v3()))
    assert cards.loads(buf.getvalue(), "charx")["data"]["name"] == "Seraphine"


def test_garbage_raises():
    with pytest.raises(cards.CardParseError):
        cards.loads(b"not json", "json")
    with pytest.raises(cards.CardParseError):
        cards.loads(b"\x89PNG\r\n\x1a\n", "png")  # no tEXt chunk


def test_json_roundtrip():
    out = cards.dumps(_v3(), "json")
    assert cards.loads(out, "json")["data"]["name"] == "Seraphine"


def test_png_roundtrip():
    out = cards.dumps(_v3(), "png")
    assert cards.loads(out, "png")["data"]["name"] == "Seraphine"


def test_charx_roundtrip():
    out = cards.dumps(_v3(), "charx")
    assert cards.loads(out, "charx")["data"]["name"] == "Seraphine"
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_cards.py -v`
Expected: FAIL with `ModuleNotFoundError: grimoire.store.cards`.

- [ ] **Step 3: Implement `cards.py`**

Create `backend/src/grimoire/store/cards.py`:

```python
"""Import/export SillyTavern cards: V3/V2 JSON, PNG tEXt, and CHARX zip.

Pure stdlib (struct/zlib/base64/zipfile) — no Pillow. PNG export writes the card
into a `ccv3` tEXt chunk over a 1x1 placeholder (or a provided avatar passthrough
is out of scope here; avatars are preserved on the campaign side via assets/).
"""

from __future__ import annotations

import base64
import json
import struct
import zipfile
import zlib
from io import BytesIO

_V2_KNOWN = {
    "name", "description", "personality", "scenario", "first_mes", "mes_example",
    "creator_notes", "system_prompt", "post_history_instructions",
    "alternate_greetings", "character_book", "tags", "creator",
    "character_version", "extensions",
}


class CardParseError(Exception):
    pass


def to_v3(obj: dict) -> dict:
    """Normalize a V2/bare object into a V3 card; preserve unknown data fields."""
    if obj.get("spec") == "chara_card_v3":
        obj.setdefault("spec_version", "3.0")
        obj.setdefault("data", {}).setdefault("extensions", {})
        return obj
    data = dict(obj.get("data") or obj)  # V2 has .data; some exports are bare data
    known = {k: data[k] for k in _V2_KNOWN if k in data}
    extensions = dict(known.get("extensions") or {})
    for k, v in data.items():
        if k not in _V2_KNOWN:
            extensions[k] = v
    known["extensions"] = extensions
    known.setdefault("name", data.get("name", "Unnamed"))
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": known}


def _loads_json(data: bytes) -> dict:
    try:
        return to_v3(json.loads(data.decode("utf-8")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CardParseError(f"invalid card JSON: {exc}") from exc


def _iter_png_text(data: bytes):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise CardParseError("not a PNG")
    pos = 8
    while pos + 8 <= len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        ctype = data[pos + 4:pos + 8]
        payload = data[pos + 8:pos + 8 + length]
        pos += 12 + length  # 4 len + 4 type + payload + 4 crc
        if ctype == b"tEXt":
            keyword, _, text = payload.partition(b"\x00")
            yield keyword.decode("latin-1"), text.decode("latin-1")


def _loads_png(data: bytes) -> dict:
    chunks = dict(_iter_png_text(data))
    for key in ("ccv3", "chara"):
        if key in chunks:
            try:
                raw = base64.b64decode(chunks[key])
            except Exception as exc:  # noqa: BLE001
                raise CardParseError(f"bad base64 in {key}") from exc
            return to_v3(json.loads(raw.decode("utf-8")))
    raise CardParseError("no ccv3/chara tEXt chunk in PNG")


def _loads_charx(data: bytes) -> dict:
    try:
        with zipfile.ZipFile(BytesIO(data)) as z:
            return to_v3(json.loads(z.read("card.json").decode("utf-8")))
    except (KeyError, zipfile.BadZipFile, ValueError) as exc:
        raise CardParseError(f"invalid charx: {exc}") from exc


def loads(data: bytes, fmt: str) -> dict:
    if fmt == "json":
        return _loads_json(data)
    if fmt == "png":
        return _loads_png(data)
    if fmt == "charx":
        return _loads_charx(data)
    raise CardParseError(f"unknown format: {fmt}")


def _png_chunk(typ: bytes, payload: bytes) -> bytes:
    body = typ + payload
    return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)


def _placeholder_png_pixels() -> bytes:
    # 1x1 truecolor: IHDR + minimal IDAT + IEND, assembled by the caller via _png_chunk
    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    raw = b"\x00\x00\x00\x00"  # one filtered scanline (filter 0 + RGB black)
    idat = zlib.compress(raw)
    return _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat)


def dumps(card: dict, fmt: str, avatar: bytes | None = None) -> bytes:
    card = to_v3(card)
    if fmt == "json":
        return (json.dumps(card, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")
    if fmt == "png":
        text = base64.b64encode(json.dumps(card).encode("utf-8")).decode("latin-1")
        sig = b"\x89PNG\r\n\x1a\n"
        return sig + _placeholder_png_pixels() + _png_chunk(
            b"tEXt", b"ccv3\x00" + text.encode("latin-1")
        ) + _png_chunk(b"IEND", b"")
    if fmt == "charx":
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("card.json", json.dumps(card, ensure_ascii=False))
        return buf.getvalue()
    raise CardParseError(f"unknown format: {fmt}")
```

> Note: the PNG export assembles `IHDR`/`IDAT` before the `tEXt` chunk so the parser
> (which scans every `tEXt`) still finds `ccv3`. The placeholder pixels keep the file a
> valid PNG; real avatar passthrough is deferred (assets are preserved on import under
> `assets/` and not re-embedded this iteration).

- [ ] **Step 4: Add `import_card`/`export_card` to `characters.py`**

Append to `backend/src/grimoire/store/characters.py`:

```python
def import_card(root: Path, data: bytes, fmt: str, into_cid: str | None = None,
                name: str | None = None) -> tuple[str, str]:
    from . import cards
    card = cards.loads(data, fmt)  # raises cards.CardParseError on bad input
    cname = name or card["data"].get("name", "Imported")
    if into_cid is None:
        return create_character(root, cname, "default", card)
    vid = create_version(root, into_cid, card.get("data", {}).get("character_version") or cname, card)
    return into_cid, vid


def export_card(root: Path, cid: str, vid: str, fmt: str) -> bytes:
    from . import cards
    return cards.dumps(read_card(root, cid, vid), fmt)
```

- [ ] **Step 5: Re-export `cards`** from `store/__init__.py` (mirror the others).

- [ ] **Step 6: Run tests**

Run: `cd backend && pytest tests/test_cards.py tests/test_characters_store.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/cards.py backend/src/grimoire/store/characters.py backend/src/grimoire/store/__init__.py backend/tests/test_cards.py
git commit -m "feat(store): card import/export (V3/V2 json, png tEXt, charx)"
```

---

### Task 5: Import/export routes (multipart in, bytes out)

**Files:**
- Modify: `backend/src/grimoire/routes.py` (add import/export routes among the world character routes)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.characters.import_card`/`export_card`, `store.cards.CardParseError`.
- Produces routes: `POST /worlds/{wid}/characters/import` (multipart `file`, optional form `into`, `name`), `GET /worlds/{wid}/characters/{cid}/versions/{vid}/export?format=...`.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routes.py` (top-of-file imports already include `json`; add `import io`):

```python
def test_character_import_export_json(client):
    wid = _world(client)
    card = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Imported", "description": "x", "extensions": {}}}
    files = {"file": ("c.json", io.BytesIO(json.dumps(card).encode()), "application/json")}
    r = client.post(f"/api/worlds/{wid}/characters/import", files=files, data={"format": "json"})
    assert r.status_code == 200
    cid, vid = r.json()["character"], r.json()["version"]
    exp = client.get(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/export", params={"format": "json"})
    assert exp.status_code == 200
    assert json.loads(exp.content)["data"]["name"] == "Imported"


def test_character_import_garbage_400(client):
    wid = _world(client)
    files = {"file": ("c.json", io.BytesIO(b"nonsense"), "application/json")}
    r = client.post(f"/api/worlds/{wid}/characters/import", files=files, data={"format": "json"})
    assert r.status_code == 400
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_routes.py::test_character_import_export_json -v`
Expected: FAIL (route missing → 404/405).

- [ ] **Step 3: Implement the routes**

Add `UploadFile`, `File`, `Form` to the FastAPI import at the top of `routes.py`:
`from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile`
and `from fastapi.responses import Response, StreamingResponse`.

Add among the world character routes (after `delete_world_version`):

```python
_EXPORT_MEDIA = {"json": "application/json", "png": "image/png", "charx": "application/zip"}


@router.post("/worlds/{wid}/characters/import")
async def post_character_import(wid: str, file: UploadFile = File(...),
                                format: str = Form(...), into: str | None = Form(None),
                                name: str | None = Form(None)):
    root = _world_root_or_404(wid)
    data = await file.read()
    try:
        cid, vid = store.characters.import_card(root, data, format, into_cid=into, name=name)
    except store.cards.CardParseError as exc:
        raise HTTPException(status_code=400, detail=f"could not parse card: {exc}")
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"character": cid, "version": vid}


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/export")
def get_character_export(wid: str, cid: str, vid: str, format: str = "json"):
    root = _world_root_or_404(wid)
    if format not in _EXPORT_MEDIA:
        raise HTTPException(status_code=400, detail="unknown format")
    try:
        blob = store.characters.export_card(root, cid, vid, format)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return Response(content=blob, media_type=_EXPORT_MEDIA[format])
```

- [ ] **Step 4: Run tests**

Run: `cd backend && pytest tests/test_routes.py -k character_import -v && pytest -q`
Expected: PASS, full suite green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(api): character import (multipart) and export routes"
```

---

### Task 6: `appearances.py` — appear(), roster, locked-version state

**Files:**
- Create: `backend/src/grimoire/store/appearances.py`
- Modify: `backend/src/grimoire/store/__init__.py` (re-export `appearances`)
- Test: `backend/tests/test_appearances_store.py`

**Interfaces:**
- Consumes: `campaigns.campaign_root`/`read_campaign`, `worlds.world_root`, `characters.read_card`/`card_hash`, `characters._dumps` (reuse via `characters.read_card` + write path). Stores state in `<campaign>/appearances.json`.
- Produces:
  ```python
  class AppearError(Exception): ...                 # version mismatch / missing world card
  appear(cid, scene_id, char_id, version_id) -> None
  roster(cid) -> list[dict]                          # [{character, version, scenes:[...]}]
  record(cid) -> dict                                # raw appearances mapping {char: {version, base, scenes}}
  scene_cast(cid, scene_id) -> list[str]             # char ids whose scenes include scene_id
  is_appeared(cid, char_id) -> bool
  locked_version(cid, char_id) -> str | None
  ```

Note: `appearances.json` is JSON (not `.md`) because the frontmatter writer is string-scalar-only and cannot represent the nested `{version, base, scenes}` records.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_appearances_store.py`:

```python
import json

import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, worlds


def _world_with_char(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    card = characters.blank_card("Seraphine")
    card["data"]["description"] = "the drowned keeper"
    characters.create_character(worlds.world_root(wid), "Seraphine", "Corrupted", card)
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid


def test_appear_locks_copies_and_records(monkeypatch, tmp_path):
    wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "the-docks", "seraphine", "corrupted")
    # the locked card was copied into the campaign
    mine = characters.read_card(campaigns.campaign_root(cid), "seraphine", "corrupted")
    assert mine["data"]["description"] == "the drowned keeper"
    rec = ap.record(cid)["seraphine"]
    assert rec["version"] == "corrupted"
    assert rec["scenes"] == ["the-docks"]
    assert rec["base"] == characters.card_hash(worlds.world_root(wid), "seraphine", "corrupted")


def test_second_scene_appends_only(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "the-docks", "seraphine", "corrupted")
    ap.appear(cid, "the-reckoning", "seraphine", "corrupted")
    assert ap.record(cid)["seraphine"]["scenes"] == ["the-docks", "the-reckoning"]
    assert ap.scene_cast(cid, "the-reckoning") == ["seraphine"]


def test_mismatched_version_rejected(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "the-docks", "seraphine", "corrupted")
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "the-docks", "seraphine", "default")  # locked to corrupted


def test_appear_missing_world_version(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "the-docks", "seraphine", "ghost")
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_appearances_store.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `appearances.py`**

Create `backend/src/grimoire/store/appearances.py`:

```python
"""Per-campaign character appearance state: which characters appeared, the locked
version, the sync base hash, and the scenes they're in. Source of truth for
characters in a campaign (the generic sync.md covers only locations/lore).

Stored as <campaign>/appearances.json:
  {"seraphine": {"version": "corrupted", "base": "<hash>", "scenes": ["the-docks"]}}
"""

from __future__ import annotations

import json
import shutil

from . import campaigns, characters, worlds


class AppearError(Exception):
    pass


def _path(cid: str):
    return campaigns.campaign_root(cid) / "appearances.json"


def record(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(cid: str, data: dict) -> None:
    _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _world_id(cid: str) -> str:
    return campaigns.read_campaign(cid)["meta"].get("world", "")


def appear(cid: str, scene_id: str, char_id: str, version_id: str) -> None:
    data = record(cid)
    rec = data.get(char_id)
    if rec is not None:
        if rec["version"] != version_id:
            raise AppearError(f"{char_id} is locked to {rec['version']}, not {version_id}")
        if scene_id not in rec["scenes"]:
            rec["scenes"].append(scene_id)
            _write(cid, data)
        return

    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    base = characters.card_hash(wroot, char_id, version_id)
    if base is None:
        raise AppearError(f"world has no {char_id}/{version_id}")
    # copy only the locked version card (+ assets) into the campaign
    src_dir = wroot / "characters" / char_id
    dst_dir = croot / "characters" / char_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    (dst_dir / f"{version_id}.json").write_text(
        (src_dir / f"{version_id}.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    # a minimal container meta so campaign-side reads work
    (dst_dir / "character.md").write_text(
        (src_dir / "character.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    if (src_dir / "assets").exists():
        shutil.copytree(src_dir / "assets", dst_dir / "assets", dirs_exist_ok=True)
    data[char_id] = {"version": version_id, "base": base, "scenes": [scene_id]}
    _write(cid, data)
    campaigns.touch(cid)


def roster(cid: str) -> list[dict]:
    data = record(cid)
    return [{"character": c, "version": r["version"], "scenes": r["scenes"]}
            for c, r in sorted(data.items())]


def scene_cast(cid: str, scene_id: str) -> list[str]:
    return sorted(c for c, r in record(cid).items() if scene_id in r["scenes"])


def is_appeared(cid: str, char_id: str) -> bool:
    return char_id in record(cid)


def locked_version(cid: str, char_id: str) -> str | None:
    rec = record(cid).get(char_id)
    return rec["version"] if rec else None
```

> The campaign-side `character.md` copied here may name a `default_version` that isn't
> the locked one; that's harmless — campaign reads always go through the locked
> `<version_id>.json`. (A later cleanup could rewrite it to the locked version.)

- [ ] **Step 4: Re-export `appearances`** from `store/__init__.py`.

- [ ] **Step 5: Run tests**

Run: `cd backend && pytest tests/test_appearances_store.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/appearances.py backend/src/grimoire/store/__init__.py backend/tests/test_appearances_store.py
git commit -m "feat(store): campaign character appearances + version lock"
```

---

### Task 7: Cast + suggestion routes, scene `dismissed` set

**Files:**
- Modify: `backend/src/grimoire/store/appearances.py` (add `suggestions`)
- Modify: `backend/src/grimoire/store/scenes.py` (add `get_dismissed`/`add_dismissed`)
- Modify: `backend/src/grimoire/routes.py` (cast/suggestion routes, declared before generic `/campaigns/{cid}/{kind}`)
- Test: `backend/tests/test_appearances_store.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `appearances.scene_cast`/`record`/`appear`, `characters.read_card`/`list_characters`, `worlds`/`campaigns`.
- Produces:
  ```python
  # scenes.py
  get_dismissed(cid, sid) -> list[str]
  add_dismissed(cid, sid, char_id) -> None
  # appearances.py
  suggestions(cid, scene_id) -> list[dict]   # [{character, name, mentioned_by:[...]}]
  # routes
  GET  /campaigns/{cid}/appearances
  GET  /campaigns/{cid}/scenes/{sid}/cast
  POST /campaigns/{cid}/scenes/{sid}/cast            {character, version?}
  GET  /campaigns/{cid}/scenes/{sid}/suggestions
  POST /campaigns/{cid}/scenes/{sid}/suggestions/dismiss   {character}
  ```

The `dismissed` set lives in the scene's frontmatter as a comma-joined string (`dismissed: a,b,c`) — the frontmatter writer is string-scalar-only, so a list cannot round-trip; comma-join is the minimal faithful encoding (character ids are slugs, never contain commas).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_appearances_store.py`:

```python
def test_suggestions_scan_names(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    sera = characters.blank_card("Seraphine")
    sera["data"]["description"] = "She fears the Drowned King above all."
    characters.create_character(wroot, "Seraphine", "default", sera)
    characters.create_character(wroot, "Drowned King", "default", characters.blank_card("Drowned King"))
    characters.create_character(wroot, "Oracle", "default", characters.blank_card("Oracle"))
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "scene-1", "seraphine", "default")
    sugg = ap.suggestions(cid, "scene-1")
    ids = [s["character"] for s in sugg]
    assert "drowned-king" in ids       # mentioned by seraphine's card
    assert "oracle" not in ids          # not mentioned
    assert "seraphine" not in ids       # already appeared
```

Add to `backend/tests/test_routes.py`:

```python
def test_cast_and_suggestions_flow(client):
    wid = _world(client)
    sera = {"spec": "chara_card_v3", "spec_version": "3.0",
            "data": {"name": "Seraphine", "description": "She serves the Drowned King.", "extensions": {}}}
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine", "card": sera})
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Drowned King"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Docks"}).json()["id"]
    # manual appearance
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"character": "seraphine", "version": "default"}).status_code == 200
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json() == ["seraphine"]
    # suggestion surfaces drowned-king, then dismiss hides it
    sugg = client.get(f"/api/campaigns/{cid}/scenes/{sid}/suggestions").json()
    assert [s["character"] for s in sugg] == ["drowned-king"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/suggestions/dismiss", json={"character": "drowned-king"})
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/suggestions").json() == []
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_appearances_store.py::test_suggestions_scan_names tests/test_routes.py::test_cast_and_suggestions_flow -v`
Expected: FAIL.

- [ ] **Step 3: Add `dismissed` helpers to `scenes.py`**

Append to `backend/src/grimoire/store/scenes.py`:

```python
def get_dismissed(cid: str, sid: str) -> list[str]:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    raw = meta.get("dismissed", "")
    return [x for x in raw.split(",") if x]


def add_dismissed(cid: str, sid: str, char_id: str) -> None:
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    current = [x for x in meta.get("dismissed", "").split(",") if x]
    if char_id not in current:
        current.append(char_id)
    meta["dismissed"] = ",".join(current)
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

- [ ] **Step 4: Add `suggestions` to `appearances.py`**

```python
import re


def suggestions(cid: str, scene_id: str) -> list[dict]:
    from . import scenes
    croot = campaigns.campaign_root(cid)
    wroot = worlds.world_root(_world_id(cid))
    appeared = set(record(cid))
    dismissed = set(scenes.get_dismissed(cid, scene_id))
    cast = scene_cast(cid, scene_id)

    # gather text from the locked cards of characters in this scene
    text_parts: list[str] = []
    for char_id in cast:
        card = characters.read_card(croot, char_id, locked_version(cid, char_id))
        d = card.get("data", {})
        for field in ("description", "personality", "scenario", "first_mes", "mes_example"):
            v = d.get(field)
            if isinstance(v, str):
                text_parts.append(v)
    haystack = "\n".join(text_parts)

    out: list[dict] = []
    for c in characters.list_characters(wroot):
        cid_ = c["id"]
        if cid_ in appeared or cid_ in dismissed or cid_ in cast:
            continue
        if re.search(rf"\b{re.escape(c['name'])}\b", haystack, re.IGNORECASE):
            out.append({"character": cid_, "name": c["name"], "mentioned_by": cast})
    return out
```

- [ ] **Step 5: Add the routes (before the generic `/campaigns/{cid}/{kind}` block)**

Add request models near the others:

```python
class Appear(BaseModel):
    character: str
    version: str | None = None


class Dismiss(BaseModel):
    character: str
```

Insert these routes immediately before the `# ---- campaign entity CRUD (generic; declared last ...)` section so the literal sub-paths win over `/{kind}`:

```python
# ---- campaign cast & suggestions (declared before the generic /{kind} routes) ----
@router.get("/campaigns/{cid}/appearances")
def get_appearances(cid: str):
    _campaign_root_or_404(cid)
    return store.appearances.roster(cid)


@router.get("/campaigns/{cid}/scenes/{sid}/cast")
def get_scene_cast(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.scene_cast(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/cast")
def post_scene_cast(cid: str, sid: str, body: Appear):
    _require_scene(cid, sid)
    wid = store.campaigns.read_campaign(cid)["meta"].get("world", "")
    wroot = store.worlds.world_root(wid)
    version = body.version
    if version is None:
        try:
            version = store.characters.read_character(wroot, body.character)["meta"]["default_version"]
        except store.characters.CharacterNotFound:
            raise HTTPException(status_code=404, detail="character not found")
    try:
        store.appearances.appear(cid, sid, body.character, version)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.get("/campaigns/{cid}/scenes/{sid}/suggestions")
def get_scene_suggestions(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.suggestions(cid, sid)


@router.post("/campaigns/{cid}/scenes/{sid}/suggestions/dismiss")
def post_dismiss(cid: str, sid: str, body: Dismiss):
    _require_scene(cid, sid)
    store.scenes.add_dismissed(cid, sid, body.character)
    return {"ok": True}
```

- [ ] **Step 6: Run tests**

Run: `cd backend && pytest tests/test_appearances_store.py tests/test_routes.py -v && pytest -q`
Expected: PASS, full suite green.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/appearances.py backend/src/grimoire/store/scenes.py backend/src/grimoire/routes.py backend/tests
git commit -m "feat(api): campaign cast, name-mention suggestions, scene dismissed set"
```

---

### Task 8: Character-aware sync (incoming / accept / reject / push counts)

**Files:**
- Modify: `backend/src/grimoire/store/sync.py`
- Test: `backend/tests/test_character_sync.py`

**Interfaces:**
- Consumes: `appearances.record`/`locked_version`, `characters.card_hash`/`read_card`, `worlds`/`campaigns`.
- Produces: `sync.incoming(cid)` includes character refs `{"kind":"characters","id":<char>}` with `status` ∈ `update|conflict` and `world`/`mine` card blobs; `sync.accept`/`reject` handle character refs; `sync.campaigns_for_world` counts characters (update/conflict only).

Character sync rule: for each appeared character, compare `world` (world card hash of the **locked** version), `base` (`appearances[char].base`), `mine` (campaign card hash of the locked version). `world != base` ⇒ pending; `mine == base` ⇒ `update`, else `conflict`. A world *new version* is a different `vid`, so the locked-version hash is unchanged ⇒ correctly ignored. Not-yet-appeared characters have no record ⇒ nothing.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_character_sync.py`:

```python
import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, sync, worlds


def _setup(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    characters.create_character(worlds.world_root(wid), "Seraphine", "default",
                                characters.blank_card("Seraphine"))
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "seraphine", "default")
    return wid, cid


def _edit_world(wid, desc):
    wroot = worlds.world_root(wid)
    card = characters.read_card(wroot, "seraphine", "default")
    card["data"]["description"] = desc
    characters.update_version(wroot, "seraphine", "default", card)


def _edit_mine(cid, desc):
    croot = campaigns.campaign_root(cid)
    card = characters.read_card(croot, "seraphine", "default")
    card["data"]["description"] = desc
    characters.update_version(croot, "seraphine", "default", card)


def test_clean_has_no_incoming(monkeypatch, tmp_path):
    _wid, cid = _setup(monkeypatch, tmp_path)
    assert sync.incoming(cid) == []


def test_world_edit_is_update(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "moved")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["update"]
    assert pend[0]["ref"] == {"kind": "characters", "id": "seraphine"}


def test_both_edit_is_conflict(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "world")
    _edit_mine(cid, "mine")
    assert [p["status"] for p in sync.incoming(cid)] == ["conflict"]


def test_new_world_version_is_ignored(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    characters.create_version(worlds.world_root(wid), "seraphine", "Corrupted",
                              characters.blank_card("Seraphine"))
    assert sync.incoming(cid) == []  # locked to 'default'; new version irrelevant


def test_accept_copies_and_clears(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "moved")
    sync.accept(cid, [{"kind": "characters", "id": "seraphine"}])
    assert sync.incoming(cid) == []
    mine = characters.read_card(campaigns.campaign_root(cid), "seraphine", "default")
    assert mine["data"]["description"] == "moved"


def test_reject_keeps_mine(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "moved")
    sync.reject(cid, [{"kind": "characters", "id": "seraphine"}])
    assert sync.incoming(cid) == []
    mine = characters.read_card(campaigns.campaign_root(cid), "seraphine", "default")
    assert mine["data"]["description"] == ""  # unchanged
    # a further world edit re-surfaces as conflict (base advanced past mine)
    _edit_world(wid, "moved-again")
    assert [p["status"] for p in sync.incoming(cid)] == ["conflict"]


def test_push_counts_include_characters(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    _edit_world(wid, "moved")
    rows = sync.campaigns_for_world(wid)
    assert rows[0]["pending"] == {"new": 0, "update": 1, "conflict": 0}
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && pytest tests/test_character_sync.py -v`
Expected: FAIL (sync ignores characters entirely right now).

- [ ] **Step 3: Add a character-incoming helper and wire it into `incoming`**

In `backend/src/grimoire/store/sync.py` add the import and a helper, then extend `incoming`:

```python
from . import appearances, campaigns, characters, entities, worlds  # add appearances, characters
```

Add:

```python
def _card_blob(root, cid: str, vid: str) -> dict:
    card = characters.read_card(root, cid, vid)
    return {"name": card["data"].get("name", cid), "version": vid, "card": card}


def _character_incoming(cid: str) -> list[dict]:
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    out: list[dict] = []
    for char_id, rec in sorted(appearances.record(cid).items()):
        vid = rec["version"]
        world_h = characters.card_hash(wroot, char_id, vid)
        if world_h is None or world_h == rec["base"]:
            continue  # world unchanged (or locked version deleted, which we skip)
        mine_h = characters.card_hash(croot, char_id, vid)
        status = "update" if mine_h == rec["base"] else "conflict"
        item = {"ref": {"kind": "characters", "id": char_id}, "status": status,
                "world": _card_blob(wroot, char_id, vid)}
        if mine_h is not None:
            item["mine"] = _card_blob(croot, char_id, vid)
        out.append(item)
    return out
```

At the end of `incoming(cid)`, change `return out` to:

```python
    return out + _character_incoming(cid)
```

- [ ] **Step 4: Route character refs in `accept`/`reject`**

Add a character advance helper and dispatch from `_advance`:

```python
def _advance_character(cid: str, char_id: str, *, copy: bool) -> bool:
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    data = appearances.record(cid)
    rec = data.get(char_id)
    if rec is None:
        return False
    vid = rec["version"]
    world_h = characters.card_hash(wroot, char_id, vid)
    if world_h is None or rec["base"] == world_h:
        return False  # not pending
    if copy:
        src = wroot / "characters" / char_id / f"{vid}.json"
        dst = croot / "characters" / char_id / f"{vid}.json"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    rec["base"] = world_h
    appearances._write(cid, data)
    return True
```

In `_advance`, split refs by kind at the top of the loop:

```python
    for ref in refs:
        kind, eid = ref["kind"], ref["id"]
        if kind == "characters":
            if _advance_character(cid, eid, copy=copy):
                changed = True
            continue
        # ... existing locations/lore body unchanged ...
```

- [ ] **Step 5: Count characters in `campaigns_for_world`**

`incoming(c["id"])` already includes character items, and the counts dict has `update`/`conflict` keys, so `campaigns_for_world` needs **no change** — character pending items increment the same counters. Verify via the `test_push_counts_include_characters` test.

- [ ] **Step 6: Run tests**

Run: `cd backend && pytest tests/test_character_sync.py -v && pytest -q`
Expected: PASS, full suite green.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/sync.py backend/tests/test_character_sync.py
git commit -m "feat(store): character-aware sync (update/conflict, locked-version)"
```

---

## Deferred to a sibling plan: frontend

The character **frontend** is intentionally **not** in this plan. It depends on UI scaffolding that does not exist yet — there is no single-world `WorldView` route, no entity editor, and no `IncomingReview` component (the worlds/campaigns *frontend-sync* phase was never built). Building the character editor, the Add-Character roster + version picker, the cast panel, the suggested-cast strip, and character rows in `IncomingReview` should be planned **together with** that pending worlds/campaigns frontend work so the shared shell (WorldView route, review component) is built once. Backend delivered here is fully exercised by the route tests, so the frontend plan can proceed against a stable API.

## Self-Review notes (coverage)

- Spec §"Storage" (world container, campaign locked card) → Tasks 2, 6.
- Spec §"appearances.md" → Task 6 (as `appearances.json`; rationale: frontmatter is string-scalar-only — documented deviation).
- Spec §"Appearance model" (two paths, lock) → Tasks 6, 7.
- Spec §"Suggestion engine" → Task 7.
- Spec §"Sync for characters" (table, accept/reject, new-version-ignored, push counts) → Task 8.
- Spec §"Import/export" (JSON/PNG/CHARX, V2→V3) → Tasks 4, 5.
- Spec §"Backend modules" → Tasks 2 (`characters`), 4 (`cards`), 6 (`appearances`), 8 (`sync`).
- Spec §"API" character/cast/suggestion/import-export routes → Tasks 3, 5, 7; character refs in incoming/accept/reject → Task 8.
- Spec §"Error handling" (404s, 400 bad import, 409 version mismatch, idempotent non-pending) → Tasks 3, 5, 7, 8.
- Spec §"Frontend" → deferred (documented above).
- Documented spec deviations: `appearances.json` (not `.md`) and `dismissed` as a comma-joined frontmatter string — both forced by the string-scalar-only frontmatter writer.
