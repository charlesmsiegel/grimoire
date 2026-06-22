# PCs, Tags & Actor Roles (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add player characters (PCs), a world tag vocabulary, and an actor `role` dimension, by generalizing the just-built character cast/appearance/sync engine to handle a second actor kind and a player/npc role — backend + API only, no prompt injection.

**Architecture:** A new `store/tags.py` holds the world tag vocabulary. A new `store/pcs.py` mirrors `characters.py` but stores a simpler markdown persona payload and PC-level tags. The existing `store/appearances.py` is generalized so records key `"<kind>/<id>"` (kind ∈ `characters`|`pcs`) and carry a `role` (`player`|`npc`); `appear()`, `scene_cast()`, `roster()`, suggestions, and the character-sync engine all become actor-kind-aware. PCs reuse that one engine (appear-on-use, locked-version sync) rather than getting a parallel system.

**Tech Stack:** Python 3, FastAPI, pytest + `fastapi.testclient.TestClient`. Pure stdlib. Frontend out of scope.

## Global Constraints

- **PC payload = name + pronouns + summary + description.** PC version files are **markdown**:
  frontmatter `name`/`pronouns`/`summary` + body `description`. (This is the "simpler payload" vs
  the Character's V3 JSON card.)
- **Tags live on PCs only this iteration.** World tag vocabulary in `worlds/<wid>/tags.md`
  (frontmatter `tag-id: Display Name`). A PC tag not in the vocabulary → `400`.
- **Role is decoupled from kind.** `pcs` are always `role="player"`; `characters` default to
  `role="npc"` but may be cast `role="player"`. **Role and version both lock per campaign at first
  appearance**; a later `appear()` with a different role or version → `409`.
- **PCs are not copied on campaign create.** Like characters, they **appear on use** and sync via
  the appearances engine; only locations/lore copy-on-create.
- **No prompt injection.** The `player` role is recorded but NOT consumed; scene chat must behave
  exactly as today (no system message injected). `{{user}}` is explicitly out of scope.
- **Actor kinds** are `("characters", "pcs")`. Hashes are `sha256(file_text)` hex; markdown and
  JSON version files are both hashed as their raw file text.
- IDs (pc, version, tag) slugify + uniquify, no date prefix. Store modules in
  `backend/src/grimoire/store/`, routes in `routes.py`, tests in `backend/tests/`. Run tests from
  `backend/` with `.venv/Scripts/python.exe -m pytest`.

---

### Task 1: World tag vocabulary (`tags.py` + routes)

**Files:**
- Create: `backend/src/grimoire/store/tags.py`
- Modify: `backend/src/grimoire/store/__init__.py` (re-export `tags`, `TagNotFound`)
- Modify: `backend/src/grimoire/routes.py` (tag routes before the generic `/worlds/{wid}/{kind}`)
- Test: `backend/tests/test_tags_store.py`, add to `backend/tests/test_routes.py`

**Interfaces:**
- Produces:
  ```python
  class TagNotFound(Exception): ...
  read_tags(root) -> dict[str, str]          # {tag-id: display}
  add_tag(root, name) -> str                  # tag-id (slug, uniquified)
  rename_tag(root, tag_id, name) -> None      # TagNotFound if absent; id unchanged
  delete_tag(root, tag_id) -> None
  has_tag(root, tag_id) -> bool
  ```
  Routes: `GET/POST /worlds/{wid}/tags`, `PUT/DELETE /worlds/{wid}/tags/{tid}`.

- [ ] **Step 1: Write the failing store test**

Create `backend/tests/test_tags_store.py`:

```python
import pytest

from grimoire.store import tags


def test_add_read_rename_delete(tmp_path):
    tid = tags.add_tag(tmp_path, "Student")
    assert tid == "student"
    assert tags.read_tags(tmp_path) == {"student": "Student"}
    assert tags.has_tag(tmp_path, "student")
    # rename keeps the id, changes the display
    tags.rename_tag(tmp_path, "student", "Pupil")
    assert tags.read_tags(tmp_path) == {"student": "Pupil"}
    tags.delete_tag(tmp_path, "student")
    assert tags.read_tags(tmp_path) == {}


def test_collision_uniquifies(tmp_path):
    assert tags.add_tag(tmp_path, "Hannah's Father") == "hannah-s-father"
    assert tags.add_tag(tmp_path, "Hannah's Father") == "hannah-s-father-2"


def test_rename_missing_raises(tmp_path):
    with pytest.raises(tags.TagNotFound):
        tags.rename_tag(tmp_path, "ghost", "X")
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_tags_store.py -v`
Expected: FAIL with `ModuleNotFoundError: grimoire.store.tags`.

- [ ] **Step 3: Implement `tags.py`**

Create `backend/src/grimoire/store/tags.py`:

```python
"""World tag vocabulary: tag-id -> display name, stored in <world>/tags.md frontmatter."""

from __future__ import annotations

from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify


class TagNotFound(Exception):
    pass


def _path(root: Path) -> Path:
    return root / "tags.md"


def read_tags(root: Path) -> dict[str, str]:
    p = _path(root)
    if not p.exists():
        return {}
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta


def _write(root: Path, vocab: dict[str, str]) -> None:
    _path(root).write_text(dump_frontmatter(vocab, ""), encoding="utf-8")


def has_tag(root: Path, tag_id: str) -> bool:
    return tag_id in read_tags(root)


def add_tag(root: Path, name: str) -> str:
    vocab = read_tags(root)
    tag_id = uniquify(slugify(name), lambda c: c in vocab)
    vocab[tag_id] = name
    _write(root, vocab)
    return tag_id


def rename_tag(root: Path, tag_id: str, name: str) -> None:
    vocab = read_tags(root)
    if tag_id not in vocab:
        raise TagNotFound(tag_id)
    vocab[tag_id] = name
    _write(root, vocab)


def delete_tag(root: Path, tag_id: str) -> None:
    vocab = read_tags(root)
    if tag_id not in vocab:
        raise TagNotFound(tag_id)
    del vocab[tag_id]
    _write(root, vocab)
```

- [ ] **Step 4: Re-export from the package**

In `backend/src/grimoire/store/__init__.py`: add `tags` to the `from . import …` line and
`from .tags import TagNotFound`, and add `"tags"` and `"TagNotFound"` to `__all__` (mirror the
existing entries).

- [ ] **Step 5: Run the store test to pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_tags_store.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Write the failing route test**

Add to `backend/tests/test_routes.py` (after `test_world_character_container_crud`):

```python
def test_world_tag_vocabulary_crud(client):
    wid = _world(client)
    tid = client.post(f"/api/worlds/{wid}/tags", json={"name": "Student"}).json()["id"]
    assert tid == "student"
    assert client.get(f"/api/worlds/{wid}/tags").json() == {"student": "Student"}
    client.put(f"/api/worlds/{wid}/tags/{tid}", json={"name": "Pupil"})
    assert client.get(f"/api/worlds/{wid}/tags").json() == {"student": "Pupil"}
    assert client.delete(f"/api/worlds/{wid}/tags/{tid}").status_code == 200
    assert client.get(f"/api/worlds/{wid}/tags").json() == {}
    assert client.put(f"/api/worlds/{wid}/tags/ghost", json={"name": "X"}).status_code == 404
```

- [ ] **Step 7: Add the routes (before the `# ---- world characters` block)**

`NameBody` already exists (`{name}`). Insert in `routes.py` immediately above the
`# ---- world characters` section:

```python
# ---- world tags (declared before the generic /{kind} routes) ----
@router.get("/worlds/{wid}/tags")
def get_world_tags(wid: str):
    return store.tags.read_tags(_world_root_or_404(wid))


@router.post("/worlds/{wid}/tags")
def post_world_tag(wid: str, body: NameBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    return {"id": store.tags.add_tag(_world_root_or_404(wid), name)}


@router.put("/worlds/{wid}/tags/{tid}")
def put_world_tag(wid: str, tid: str, body: NameBody):
    try:
        store.tags.rename_tag(_world_root_or_404(wid), tid, body.name.strip())
    except store.tags.TagNotFound:
        raise HTTPException(status_code=404, detail="tag not found")
    return {"id": tid, "name": body.name.strip()}


@router.delete("/worlds/{wid}/tags/{tid}")
def delete_world_tag(wid: str, tid: str):
    try:
        store.tags.delete_tag(_world_root_or_404(wid), tid)
    except store.tags.TagNotFound:
        raise HTTPException(status_code=404, detail="tag not found")
    return {"ok": True}
```

> `_world_root_or_404` is defined later in the file; it is only called at request time, so the
> forward reference is fine (the character routes already rely on this).

- [ ] **Step 8: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: PASS (existing 96 + tag store/route tests).

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/store/tags.py backend/src/grimoire/store/__init__.py backend/src/grimoire/routes.py backend/tests/test_tags_store.py backend/tests/test_routes.py
git commit -m "feat(store): world tag vocabulary + routes"
```

---

### Task 2: PC containers (`pcs.py` + world routes + counts)

**Files:**
- Create: `backend/src/grimoire/store/pcs.py`
- Modify: `backend/src/grimoire/store/__init__.py` (re-export `pcs`, `PCNotFound`, `PCVersionNotFound`)
- Modify: `backend/src/grimoire/store/worlds.py` (counts include `pcs`)
- Modify: `backend/src/grimoire/routes.py` (PC routes before the generic `/worlds/{wid}/{kind}`; tag-validated create)
- Test: `backend/tests/test_pcs_store.py`, add to `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `tags.has_tag` (route-level tag validation).
- Produces:
  ```python
  class PCNotFound(Exception): ...
  class PCVersionNotFound(Exception): ...
  blank_persona(name) -> dict                          # {name, pronouns, summary, description}
  list_pcs(root) -> [{id, name, tags:[...], default_version, versions:[{id,name}]}]
  read_pc(root, pid) -> {meta:{id,name,tags,default_version}, versions:[{id,name,persona}]}
  read_persona(root, pid, vid) -> dict                 # {name, pronouns, summary, description}
  create_pc(root, name, tags, version_name="default", persona=None) -> (pid, vid)
  create_version(root, pid, version_name, persona) -> vid
  update_version(root, pid, vid, persona) -> None
  set_default_version(root, pid, vid) -> None
  set_tags(root, pid, tags) -> None
  delete_version(root, pid, vid) -> None               # refuses the last version
  delete_pc(root, pid) -> None
  version_hash(root, pid, vid) -> str | None
  pc_count(root) -> int ; pc_refs(root) -> [id]
  ```

Storage: `<root>/pcs/<pid>/pc.md` (frontmatter `name`, `tags` comma-joined, `default_version`) +
`<root>/pcs/<pid>/<vid>.md` (frontmatter `name`/`pronouns`/`summary` + body description).

- [ ] **Step 1: Write the failing store test**

Create `backend/tests/test_pcs_store.py`:

```python
import pytest

from grimoire.store import pcs


def test_create_read_single_version(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", ["student"])
    assert (pid, vid) == ("elara", "default")
    pc = pcs.read_pc(tmp_path, pid)
    assert pc["meta"]["name"] == "Elara"
    assert pc["meta"]["tags"] == ["student"]
    assert pc["versions"][0]["persona"]["name"] == "Elara"


def test_persona_fields_round_trip(tmp_path):
    persona = {"name": "Elara", "pronouns": "she/her", "summary": "scholar", "description": "A wanderer."}
    pid, vid = pcs.create_pc(tmp_path, "Elara", [], persona=persona)
    assert pcs.read_persona(tmp_path, pid, vid) == persona


def test_versions_and_default(tmp_path):
    pid, _ = pcs.create_pc(tmp_path, "Elara", [])
    v2 = pcs.create_version(tmp_path, pid, "Older", pcs.blank_persona("Elara"))
    assert v2 == "older"
    pcs.set_default_version(tmp_path, pid, v2)
    assert pcs.read_pc(tmp_path, pid)["meta"]["default_version"] == "older"


def test_hash_stable_then_changes(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    h1 = pcs.version_hash(tmp_path, pid, vid)
    pcs.update_version(tmp_path, pid, vid, pcs.read_persona(tmp_path, pid, vid))
    assert pcs.version_hash(tmp_path, pid, vid) == h1
    p = pcs.read_persona(tmp_path, pid, vid)
    p["description"] = "changed"
    pcs.update_version(tmp_path, pid, vid, p)
    assert pcs.version_hash(tmp_path, pid, vid) != h1


def test_set_tags_and_counts(tmp_path):
    pid, _ = pcs.create_pc(tmp_path, "Elara", ["student"])
    pcs.set_tags(tmp_path, pid, ["student", "hannah-s-father"])
    assert pcs.read_pc(tmp_path, pid)["meta"]["tags"] == ["student", "hannah-s-father"]
    pcs.create_pc(tmp_path, "Rook", [])
    assert pcs.pc_count(tmp_path) == 2
    assert set(pcs.pc_refs(tmp_path)) == {"elara", "rook"}


def test_delete_last_version_refused_and_missing(tmp_path):
    pid, vid = pcs.create_pc(tmp_path, "Elara", [])
    with pytest.raises(pcs.PCVersionNotFound):
        pcs.read_persona(tmp_path, pid, "ghost")
    with pytest.raises(ValueError):
        pcs.delete_version(tmp_path, pid, vid)
    with pytest.raises(pcs.PCNotFound):
        pcs.read_pc(tmp_path, "nobody")
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_pcs_store.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `pcs.py`**

Create `backend/src/grimoire/store/pcs.py`:

```python
"""Player-character containers: one folder per PC, one markdown persona per version.

Mirrors characters.py but with a simpler payload:
  <root>/pcs/<pid>/pc.md          # frontmatter: name, tags (comma-joined), default_version
  <root>/pcs/<pid>/<vid>.md       # frontmatter: name, pronouns, summary ; body: description
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import slugify, uniquify

PERSONA_FIELDS = ("name", "pronouns", "summary")  # frontmatter scalars; description is the body


class PCNotFound(Exception):
    pass


class PCVersionNotFound(Exception):
    pass


def _safe(part: str) -> bool:
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _pcs_dir(root: Path) -> Path:
    return root / "pcs"


def _pc_dir(root: Path, pid: str) -> Path:
    return _pcs_dir(root) / pid


def _meta_path(root: Path, pid: str) -> Path:
    return _pc_dir(root, pid) / "pc.md"


def _version_path(root: Path, pid: str, vid: str) -> Path:
    return _pc_dir(root, pid) / f"{vid}.md"


def blank_persona(name: str) -> dict:
    return {"name": name, "pronouns": "", "summary": "", "description": ""}


def _dump_persona(persona: dict) -> str:
    meta = {f: persona.get(f, "") for f in PERSONA_FIELDS}
    return dump_frontmatter(meta, persona.get("description", ""))


def _load_persona(text: str) -> dict:
    meta, body = parse_frontmatter(text)
    return {**{f: meta.get(f, "") for f in PERSONA_FIELDS}, "description": body.strip()}


def _require_pc(root: Path, pid: str) -> Path:
    if not _safe(pid) or not _meta_path(root, pid).exists():
        raise PCNotFound(pid)
    return _pc_dir(root, pid)


def _read_meta(root: Path, pid: str) -> dict:
    meta, _ = parse_frontmatter(_meta_path(root, pid).read_text(encoding="utf-8"))
    return meta


def _write_meta(root: Path, pid: str, name: str, tags: list[str], default_version: str) -> None:
    _meta_path(root, pid).write_text(
        dump_frontmatter({"name": name, "tags": ",".join(tags), "default_version": default_version}, ""),
        encoding="utf-8",
    )


def _tags_of(meta: dict) -> list[str]:
    return [t for t in meta.get("tags", "").split(",") if t]


def create_pc(root: Path, name: str, tags: list[str], version_name: str = "default",
              persona: dict | None = None) -> tuple[str, str]:
    _pcs_dir(root).mkdir(parents=True, exist_ok=True)
    pid = uniquify(slugify(name), lambda c: _pc_dir(root, c).exists())
    _pc_dir(root, pid).mkdir(parents=True)
    vid = slugify(version_name)
    _version_path(root, pid, vid).write_text(_dump_persona(persona or blank_persona(name)), encoding="utf-8")
    _write_meta(root, pid, name, tags, vid)
    return pid, vid


def create_version(root: Path, pid: str, version_name: str, persona: dict) -> str:
    _require_pc(root, pid)
    vid = uniquify(slugify(version_name), lambda v: _version_path(root, pid, v).exists())
    _version_path(root, pid, vid).write_text(_dump_persona(persona), encoding="utf-8")
    return vid


def update_version(root: Path, pid: str, vid: str, persona: dict) -> None:
    _require_pc(root, pid)
    p = _version_path(root, pid, vid)
    if not _safe(vid) or not p.exists():
        raise PCVersionNotFound(vid)
    p.write_text(_dump_persona(persona), encoding="utf-8")


def set_default_version(root: Path, pid: str, vid: str) -> None:
    _require_pc(root, pid)
    if not _safe(vid) or not _version_path(root, pid, vid).exists():
        raise PCVersionNotFound(vid)
    meta = _read_meta(root, pid)
    _write_meta(root, pid, meta.get("name", pid), _tags_of(meta), vid)


def set_tags(root: Path, pid: str, tags: list[str]) -> None:
    _require_pc(root, pid)
    meta = _read_meta(root, pid)
    _write_meta(root, pid, meta.get("name", pid), tags, meta.get("default_version", ""))


def _version_ids(root: Path, pid: str) -> list[str]:
    return sorted(p.stem for p in _pc_dir(root, pid).glob("*.md") if p.name != "pc.md")


def read_persona(root: Path, pid: str, vid: str) -> dict:
    _require_pc(root, pid)
    p = _version_path(root, pid, vid)
    if not _safe(vid) or not p.exists():
        raise PCVersionNotFound(vid)
    return _load_persona(p.read_text(encoding="utf-8"))


def read_pc(root: Path, pid: str) -> dict:
    _require_pc(root, pid)
    meta = _read_meta(root, pid)
    versions = [{"id": v, "name": read_persona(root, pid, v)["name"], "persona": read_persona(root, pid, v)}
                for v in _version_ids(root, pid)]
    return {"meta": {"id": pid, "name": meta.get("name", pid), "tags": _tags_of(meta),
                     "default_version": meta.get("default_version", "")}, "versions": versions}


def list_pcs(root: Path) -> list[dict]:
    out: list[dict] = []
    d = _pcs_dir(root)
    if d.exists():
        for pd in sorted(p for p in d.iterdir() if p.is_dir() and (p / "pc.md").exists()):
            pid = pd.name
            meta = _read_meta(root, pid)
            out.append({"id": pid, "name": meta.get("name", pid), "tags": _tags_of(meta),
                        "default_version": meta.get("default_version", ""),
                        "versions": [{"id": v, "name": read_persona(root, pid, v)["name"]}
                                     for v in _version_ids(root, pid)]})
    return out


def delete_version(root: Path, pid: str, vid: str) -> None:
    _require_pc(root, pid)
    p = _version_path(root, pid, vid)
    if not _safe(vid) or not p.exists():
        raise PCVersionNotFound(vid)
    if len(_version_ids(root, pid)) == 1:
        raise ValueError("cannot delete the last version of a PC")
    p.unlink()
    meta = _read_meta(root, pid)
    if meta.get("default_version") == vid:
        _write_meta(root, pid, meta.get("name", pid), _tags_of(meta), _version_ids(root, pid)[0])


def delete_pc(root: Path, pid: str) -> None:
    _require_pc(root, pid)
    shutil.rmtree(_pc_dir(root, pid))


def version_hash(root: Path, pid: str, vid: str) -> str | None:
    p = _version_path(root, pid, vid)
    if not _safe(pid) or not _safe(vid) or not p.exists():
        return None
    return hashlib.sha256(p.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def pc_count(root: Path) -> int:
    d = _pcs_dir(root)
    return sum(1 for p in d.iterdir() if p.is_dir() and (p / "pc.md").exists()) if d.exists() else 0


def pc_refs(root: Path) -> list[str]:
    d = _pcs_dir(root)
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and (p / "pc.md").exists())
```

- [ ] **Step 4: Re-export + world counts**

In `store/__init__.py`: add `pcs` to imports and `from .pcs import PCNotFound, PCVersionNotFound`,
plus `"pcs"`, `"PCNotFound"`, `"PCVersionNotFound"` in `__all__`.

In `store/worlds.py`: add `pcs` to `from . import characters, entities` → `from . import
characters, entities, pcs`, and include PCs in both counts dicts:
- `list_worlds`: `"counts": {**entities.entity_counts(d), "characters": characters.character_count(d), "pcs": pcs.pc_count(d)}`
- `read_world`: same, using `root`.

- [ ] **Step 5: Run store test + suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_pcs_store.py -q`
Expected: PASS (6 tests).

- [ ] **Step 6: Write the failing route test**

Add to `backend/tests/test_routes.py`:

```python
def test_world_pc_crud_and_tag_validation(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/tags", json={"name": "Student"})
    # create with a valid tag
    r = client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara", "tags": ["student"]})
    pid = r.json()["pc"]
    assert pid == "elara"
    assert client.get(f"/api/worlds/{wid}/pcs/{pid}").json()["meta"]["tags"] == ["student"]
    # world counts include pcs
    assert client.get(f"/api/worlds/{wid}").json()["counts"]["pcs"] == 1
    # a tag outside the vocabulary is rejected
    assert client.post(f"/api/worlds/{wid}/pcs", json={"name": "Rook", "tags": ["ghost"]}).status_code == 400
    assert client.delete(f"/api/worlds/{wid}/pcs/{pid}").status_code == 200
```

- [ ] **Step 7: Add the PC routes + models**

In `routes.py` near the other models add:

```python
class PCCreate(BaseModel):
    name: str
    tags: list[str] = []
    version_name: str = "default"
    persona: dict | None = None


class PCUpdate(BaseModel):
    default_version: str | None = None
    tags: list[str] | None = None


class PersonaVersionCreate(BaseModel):
    name: str
    persona: dict


class PersonaVersionUpdate(BaseModel):
    persona: dict
```

Add a tag-validation helper and the routes immediately above the `# ---- world characters` block:

```python
def _validate_tags(root, tags: list[str]) -> None:
    for t in tags:
        if not store.tags.has_tag(root, t):
            raise HTTPException(status_code=400, detail=f"unknown tag: {t}")


@router.get("/worlds/{wid}/pcs")
def get_world_pcs(wid: str):
    return store.pcs.list_pcs(_world_root_or_404(wid))


@router.post("/worlds/{wid}/pcs")
def post_world_pc(wid: str, body: PCCreate):
    root = _world_root_or_404(wid)
    _validate_tags(root, body.tags)
    pid, vid = store.pcs.create_pc(root, body.name, body.tags, body.version_name, body.persona)
    return {"pc": pid, "version": vid}


@router.get("/worlds/{wid}/pcs/{pid}")
def get_world_pc(wid: str, pid: str):
    try:
        return store.pcs.read_pc(_world_root_or_404(wid), pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")


@router.put("/worlds/{wid}/pcs/{pid}")
def put_world_pc(wid: str, pid: str, body: PCUpdate):
    root = _world_root_or_404(wid)
    try:
        if body.tags is not None:
            _validate_tags(root, body.tags)
            store.pcs.set_tags(root, pid, body.tags)
        if body.default_version is not None:
            store.pcs.set_default_version(root, pid, body.default_version)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/worlds/{wid}/pcs/{pid}")
def delete_world_pc(wid: str, pid: str):
    try:
        store.pcs.delete_pc(_world_root_or_404(wid), pid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    return {"ok": True}


@router.post("/worlds/{wid}/pcs/{pid}/versions")
def post_pc_version(wid: str, pid: str, body: PersonaVersionCreate):
    try:
        vid = store.pcs.create_version(_world_root_or_404(wid), pid, body.name, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    return {"version": vid}


@router.put("/worlds/{wid}/pcs/{pid}/versions/{vid}")
def put_pc_version(wid: str, pid: str, vid: str, body: PersonaVersionUpdate):
    try:
        store.pcs.update_version(_world_root_or_404(wid), pid, vid, body.persona)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    return {"ok": True}


@router.delete("/worlds/{wid}/pcs/{pid}/versions/{vid}")
def delete_pc_version(wid: str, pid: str, vid: str):
    try:
        store.pcs.delete_version(_world_root_or_404(wid), pid, vid)
    except store.pcs.PCNotFound:
        raise HTTPException(status_code=404, detail="pc not found")
    except store.pcs.PCVersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True}
```

- [ ] **Step 8: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/store/pcs.py backend/src/grimoire/store/__init__.py backend/src/grimoire/store/worlds.py backend/src/grimoire/routes.py backend/tests/test_pcs_store.py backend/tests/test_routes.py
git commit -m "feat(store): PC containers (simple persona, tags) + world routes/counts"
```

---

### Task 3: Actor-kind + role generalization (appearances + sync + cast routes)

This is one atomic refactor: changing `appearances.json` keys to `"<kind>/<id>"` with a `role`
breaks `sync.py`, the cast route, and the suggestion scan at once, so all move together to keep
the suite green. It delivers PC cast, PC sync, character-as-player, and role locking.

**Files:**
- Modify: `backend/src/grimoire/store/appearances.py` (rewrite — actor-kind+role aware)
- Modify: `backend/src/grimoire/store/sync.py` (actor-incoming + actor-advance)
- Modify: `backend/src/grimoire/routes.py` (`Appear` model + `post_scene_cast` + `get_scene_cast` + `get_appearances`)
- Modify: `backend/tests/test_appearances_store.py`, `backend/tests/test_character_sync.py`, `backend/tests/test_routes.py` (migrate to new signatures)
- Test (new cases): the same three test files

**Interfaces:**
- Produces:
  ```python
  # appearances.py
  ACTOR_KINDS = ("characters", "pcs")
  actor_hash(root, kind, actor_id, vid) -> str | None        # dispatches characters.card_hash / pcs.version_hash
  appear(cid, scene_id, kind, actor_id, version_id, role)    # role/version lock; AppearError on mismatch
  scene_cast(cid, scene_id) -> [{kind, id, role}]
  roster(cid) -> [{kind, id, version, role, scenes}]
  players_in_scene(cid, scene_id) -> [{kind, id, version}]   # role == player
  locked_version(cid, kind, actor_id) -> str | None
  set_base(cid, kind, actor_id, base) -> None
  record(cid) -> dict                                         # keys "<kind>/<id>"
  # sync.py unchanged public surface: incoming/accept/reject route characters|pcs to the actor engine
  ```
- Record shape: `{"characters/seraphine": {"version","base","scenes","role"}, "pcs/elara": {...}}`.

- [ ] **Step 1: Write the failing store tests (new behaviors)**

Replace the body of `backend/tests/test_appearances_store.py` with the migrated + new tests:

```python
import pytest

from grimoire.store import appearances as ap
from grimoire.store import campaigns, characters, pcs, worlds


def _world_with_char(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    card = characters.blank_card("Seraphine")
    card["data"]["description"] = "the drowned keeper"
    characters.create_character(worlds.world_root(wid), "Seraphine", "Corrupted", card)
    cid = campaigns.create_campaign("Run", wid)
    return wid, cid


def test_character_appears_locks_version_and_role(monkeypatch, tmp_path):
    wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "the-docks", "characters", "seraphine", "corrupted", "npc")
    mine = characters.read_card(campaigns.campaign_root(cid), "seraphine", "corrupted")
    assert mine["data"]["description"] == "the drowned keeper"
    rec = ap.record(cid)["characters/seraphine"]
    assert rec == {"version": "corrupted", "base": rec["base"], "scenes": ["the-docks"], "role": "npc"}
    assert rec["base"] == characters.card_hash(worlds.world_root(wid), "seraphine", "corrupted")


def test_second_scene_appends_only(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    ap.appear(cid, "s2", "characters", "seraphine", "corrupted", "npc")
    assert ap.record(cid)["characters/seraphine"]["scenes"] == ["s1", "s2"]
    assert ap.scene_cast(cid, "s2") == [{"kind": "characters", "id": "seraphine", "role": "npc"}]


def test_version_or_role_mismatch_rejected(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "s1", "characters", "seraphine", "default", "npc")   # version differs
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "player")  # role differs


def test_pc_appears_as_player(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    pcs.create_pc(worlds.world_root(wid), "Elara", [], persona={"name": "Elara", "pronouns": "she/her",
                                                                "summary": "scholar", "description": "A wanderer."})
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "pcs", "elara", "default", "player")
    assert ap.players_in_scene(cid, "s1") == [{"kind": "pcs", "id": "elara", "version": "default"}]
    # the PC version markdown was copied into the campaign
    assert pcs.read_persona(campaigns.campaign_root(cid), "elara", "default")["description"] == "A wanderer."


def test_character_cast_as_player(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "player")
    assert ap.players_in_scene(cid, "s1") == [{"kind": "characters", "id": "seraphine", "version": "corrupted"}]


def test_suggestions_still_scan_character_names(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    wroot = worlds.world_root(wid)
    sera = characters.blank_card("Seraphine")
    sera["data"]["description"] = "She fears the Drowned King."
    characters.create_character(wroot, "Seraphine", "default", sera)
    characters.create_character(wroot, "Drowned King", "default", characters.blank_card("Drowned King"))
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "characters", "seraphine", "default", "npc")
    sugg = ap.suggestions(cid, "s1")
    assert sugg == [{"character": "drowned-king", "name": "Drowned King", "mentioned_by": ["seraphine"]}]
```

- [ ] **Step 2: Run to confirm failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_appearances_store.py -q`
Expected: FAIL (old `appear()` signature / bare-id record shape).

- [ ] **Step 3: Rewrite `appearances.py`**

Replace the whole file with the generalized version:

```python
"""Per-campaign actor appearance state: which actors (characters or PCs) appeared, the
locked version, role, sync base hash, and scenes. Source of truth for actors in a
campaign (the generic sync.md covers only locations/lore).

Stored as <campaign>/appearances.json, keyed "<kind>/<id>":
  {"characters/seraphine": {"version":"corrupted","base":"<h>","scenes":["s1"],"role":"npc"},
   "pcs/elara":            {"version":"default","base":"<h>","scenes":["s1"],"role":"player"}}
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import campaigns, characters, pcs, worlds
from .frontmatter import dump_frontmatter, parse_frontmatter

ACTOR_KINDS = ("characters", "pcs")


class AppearError(Exception):
    pass


def _ref(kind: str, actor_id: str) -> str:
    return f"{kind}/{actor_id}"


def _split(ref: str) -> tuple[str, str]:
    kind, _, actor_id = ref.partition("/")
    return kind, actor_id


def _path(cid: str) -> Path:
    return campaigns.campaign_root(cid) / "appearances.json"


def record(cid: str) -> dict:
    p = _path(cid)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _write(cid: str, data: dict) -> None:
    _path(cid).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def set_base(cid: str, kind: str, actor_id: str, base: str) -> None:
    """Advance the recorded sync base hash for an appeared actor (sync uses this)."""
    data = record(cid)
    ref = _ref(kind, actor_id)
    if ref in data:
        data[ref]["base"] = base
        _write(cid, data)


def _world_id(cid: str) -> str:
    return campaigns.read_campaign(cid)["meta"].get("world", "")


def actor_hash(root: Path, kind: str, actor_id: str, vid: str) -> str | None:
    if kind == "characters":
        return characters.card_hash(root, actor_id, vid)
    return pcs.version_hash(root, actor_id, vid)


def _version_ext(kind: str) -> str:
    return "json" if kind == "characters" else "md"


def _meta_name(kind: str) -> str:
    return "character.md" if kind == "characters" else "pc.md"


def _copy_actor(wroot: Path, croot: Path, kind: str, actor_id: str, vid: str) -> None:
    src_dir = wroot / kind / actor_id
    dst_dir = croot / kind / actor_id
    dst_dir.mkdir(parents=True, exist_ok=True)
    ext = _version_ext(kind)
    (dst_dir / f"{vid}.{ext}").write_text((src_dir / f"{vid}.{ext}").read_text(encoding="utf-8"), encoding="utf-8")
    # container meta so campaign-side reads work; default_version points at the copied version
    meta, _ = parse_frontmatter((src_dir / _meta_name(kind)).read_text(encoding="utf-8"))
    meta["default_version"] = vid
    (dst_dir / _meta_name(kind)).write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    if kind == "characters" and (src_dir / "assets").exists():
        shutil.copytree(src_dir / "assets", dst_dir / "assets", dirs_exist_ok=True)


def appear(cid: str, scene_id: str, kind: str, actor_id: str, version_id: str, role: str) -> None:
    data = record(cid)
    ref = _ref(kind, actor_id)
    rec = data.get(ref)
    if rec is not None:
        if rec["version"] != version_id:
            raise AppearError(f"{ref} is locked to version {rec['version']}, not {version_id}")
        if rec["role"] != role:
            raise AppearError(f"{ref} is locked to role {rec['role']}, not {role}")
        if scene_id not in rec["scenes"]:
            rec["scenes"].append(scene_id)
            _write(cid, data)
        return

    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    base = actor_hash(wroot, kind, actor_id, version_id)
    if base is None:
        raise AppearError(f"world has no {ref}/{version_id}")
    _copy_actor(wroot, croot, kind, actor_id, version_id)
    data[ref] = {"version": version_id, "base": base, "scenes": [scene_id], "role": role}
    _write(cid, data)
    campaigns.touch(cid)


def roster(cid: str) -> list[dict]:
    out = []
    for ref, r in sorted(record(cid).items()):
        kind, actor_id = _split(ref)
        out.append({"kind": kind, "id": actor_id, "version": r["version"], "role": r["role"], "scenes": r["scenes"]})
    return out


def scene_cast(cid: str, scene_id: str) -> list[dict]:
    out = []
    for ref, r in record(cid).items():
        if scene_id in r["scenes"]:
            kind, actor_id = _split(ref)
            out.append({"kind": kind, "id": actor_id, "role": r["role"]})
    return sorted(out, key=lambda a: (a["kind"], a["id"]))


def players_in_scene(cid: str, scene_id: str) -> list[dict]:
    out = []
    for ref, r in record(cid).items():
        if scene_id in r["scenes"] and r["role"] == "player":
            kind, actor_id = _split(ref)
            out.append({"kind": kind, "id": actor_id, "version": r["version"]})
    return sorted(out, key=lambda a: (a["kind"], a["id"]))


def is_appeared(cid: str, kind: str, actor_id: str) -> bool:
    return _ref(kind, actor_id) in record(cid)


def locked_version(cid: str, kind: str, actor_id: str) -> str | None:
    rec = record(cid).get(_ref(kind, actor_id))
    return rec["version"] if rec else None


def suggestions(cid: str, scene_id: str) -> list[dict]:
    from . import scenes
    croot = campaigns.campaign_root(cid)
    wroot = worlds.world_root(_world_id(cid))
    appeared_chars = {actor_id for ref in record(cid) for k, actor_id in [_split(ref)] if k == "characters"}
    dismissed = set(scenes.get_dismissed(cid, scene_id))
    in_scene_chars = [a["id"] for a in scene_cast(cid, scene_id) if a["kind"] == "characters"]
    candidates = [c for c in characters.list_characters(wroot)
                  if c["id"] not in appeared_chars and c["id"] not in dismissed and c["id"] not in in_scene_chars]

    mentioned_by: dict[str, list[str]] = {}
    for char_id in in_scene_chars:
        card = characters.read_card(croot, char_id, locked_version(cid, "characters", char_id))
        d = card.get("data", {})
        text = "\n".join(d.get(f) for f in ("description", "personality", "scenario", "first_mes", "mes_example")
                         if isinstance(d.get(f), str))
        for c in candidates:
            if re.search(rf"\b{re.escape(c['name'])}\b", text, re.IGNORECASE):
                mentioned_by.setdefault(c["id"], []).append(char_id)

    return [{"character": c["id"], "name": c["name"], "mentioned_by": sorted(set(mentioned_by[c["id"]]))}
            for c in candidates if c["id"] in mentioned_by]
```

- [ ] **Step 4: Run the appearances tests**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_appearances_store.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Generalize `sync.py` (actor-incoming + actor-advance)**

In `sync.py`, change the import to include `pcs` and `appearances` (already imports
`appearances, campaigns, characters, entities, worlds`); add `pcs`:
`from . import appearances, campaigns, characters, entities, pcs, worlds`.

Replace the character-specific helpers with actor-kind-aware ones. **Replace** `_card_blob` and
`_character_incoming` with:

```python
def _actor_blob(root, kind: str, actor_id: str, vid: str) -> dict:
    if kind == "characters":
        card = characters.read_card(root, actor_id, vid)
        return {"name": card["data"].get("name", actor_id), "version": vid, "card": card}
    persona = pcs.read_persona(root, actor_id, vid)
    return {"name": persona.get("name", actor_id), "version": vid, "persona": persona}


def _actor_incoming(cid: str) -> list[dict]:
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    out: list[dict] = []
    for ref, rec in sorted(appearances.record(cid).items()):
        kind, actor_id = ref.split("/", 1)
        vid = rec["version"]
        world_h = appearances.actor_hash(wroot, kind, actor_id, vid)
        if world_h is None or world_h == rec["base"]:
            continue
        mine_h = appearances.actor_hash(croot, kind, actor_id, vid)
        status = "update" if mine_h == rec["base"] else "conflict"
        item = {"ref": {"kind": kind, "id": actor_id}, "status": status,
                "world": _actor_blob(wroot, kind, actor_id, vid)}
        if mine_h is not None:
            item["mine"] = _actor_blob(croot, kind, actor_id, vid)
        out.append(item)
    return out
```

Update the tail of `incoming()` from `return out + _character_incoming(cid)` to
`return out + _actor_incoming(cid)`.

**Replace** `_advance_character` with an actor-kind-aware version, and update the dispatch in
`_advance`:

```python
def _advance_actor(cid: str, kind: str, actor_id: str, *, copy: bool) -> bool:
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    rec = appearances.record(cid).get(f"{kind}/{actor_id}")
    if rec is None:
        return False
    vid = rec["version"]
    world_h = appearances.actor_hash(wroot, kind, actor_id, vid)
    if world_h is None or rec["base"] == world_h:
        return False
    if copy:
        ext = "json" if kind == "characters" else "md"
        src = wroot / kind / actor_id / f"{vid}.{ext}"
        dst = croot / kind / actor_id / f"{vid}.{ext}"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    appearances.set_base(cid, kind, actor_id, world_h)
    return True
```

In `_advance`, change the character branch to cover both actor kinds:

```python
        if kind in appearances.ACTOR_KINDS:
            if _advance_actor(cid, kind, eid, copy=copy):
                touched = True
            continue
```

- [ ] **Step 6: Migrate `test_character_sync.py` to the new signatures**

In `backend/tests/test_character_sync.py`, update `_setup` and the ref dicts:
- `ap.appear(cid, "s1", "seraphine", "default")` → `ap.appear(cid, "s1", "characters", "seraphine", "default", "npc")`
- every `{"kind": "characters", "id": "seraphine"}` stays as-is (the sync ref shape is unchanged).

Add a PC-sync test to the same file:

```python
def test_pc_sync_update(monkeypatch, tmp_path):
    from grimoire.store import pcs
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    pcs.create_pc(worlds.world_root(wid), "Elara", [])
    cid = campaigns.create_campaign("Run", wid)
    ap.appear(cid, "s1", "pcs", "elara", "default", "player")
    # edit the world PC
    persona = pcs.read_persona(worlds.world_root(wid), "elara", "default")
    persona["description"] = "moved"
    pcs.update_version(worlds.world_root(wid), "elara", "default", persona)
    pend = sync.incoming(cid)
    assert [(p["ref"], p["status"]) for p in pend] == [({"kind": "pcs", "id": "elara"}, "update")]
    sync.accept(cid, [{"kind": "pcs", "id": "elara"}])
    assert sync.incoming(cid) == []
    assert pcs.read_persona(campaigns.campaign_root(cid), "elara", "default")["description"] == "moved"
```

- [ ] **Step 7: Run the sync tests**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_character_sync.py -q`
Expected: PASS (existing 7 migrated + the new PC-sync test).

- [ ] **Step 8: Generalize the cast route + appearances/roster route**

In `routes.py` replace the `Appear` model:

```python
class Appear(BaseModel):
    kind: str = "characters"
    id: str
    version: str | None = None
    role: str | None = None
```

Replace `post_scene_cast` and `get_scene_cast`, and update `get_appearances` to return the roster
(it already calls `store.appearances.roster`):

```python
@router.post("/campaigns/{cid}/scenes/{sid}/cast")
def post_scene_cast(cid: str, sid: str, body: Appear):
    _require_scene(cid, sid)
    if body.kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    wroot = store.worlds.world_root(store.campaigns.read_campaign(cid)["meta"].get("world", ""))
    role = "player" if body.kind == "pcs" else (body.role or "npc")
    if role not in ("player", "npc"):
        raise HTTPException(status_code=400, detail="role must be player or npc")
    version = body.version
    try:
        if version is None:
            if body.kind == "characters":
                version = store.characters.read_character(wroot, body.id)["meta"]["default_version"]
            else:
                version = store.pcs.read_pc(wroot, body.id)["meta"]["default_version"]
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
    try:
        store.appearances.appear(cid, sid, body.kind, body.id, version, role)
    except store.appearances.AppearError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True}


@router.get("/campaigns/{cid}/scenes/{sid}/cast")
def get_scene_cast(cid: str, sid: str):
    _require_scene(cid, sid)
    return store.appearances.scene_cast(cid, sid)
```

- [ ] **Step 9: Migrate the route cast test + add character-as-player**

In `backend/tests/test_routes.py`, update `test_cast_and_suggestions_flow`'s cast call and
assertion:
- `json={"character": "seraphine", "version": "default"}` → `json={"kind": "characters", "id": "seraphine", "version": "default"}`
- `client.get(...cast).json() == ["seraphine"]` → `== [{"kind": "characters", "id": "seraphine", "role": "npc"}]`

Add a PC + character-as-player route test:

```python
def test_cast_pc_and_character_as_player(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "desmond"})
    client.post(f"/api/worlds/{wid}/pcs", json={"name": "Elara"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # a PC casts as player automatically
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "pcs", "id": "elara"}).status_code == 200
    # a character cast explicitly as player
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "characters", "id": "desmond", "role": "player"}).status_code == 200
    cast = client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json()
    assert {"kind": "pcs", "id": "elara", "role": "player"} in cast
    assert {"kind": "characters", "id": "desmond", "role": "player"} in cast
    roster = client.get(f"/api/campaigns/{cid}/appearances").json()
    assert {r["kind"] for r in roster} == {"pcs", "characters"}
```

- [ ] **Step 10: Run the full suite**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
Expected: PASS (all prior + new actor/role/PC tests). No remaining references to the old
`appear(cid, scene, char_id, version)` signature or bare-id records.

- [ ] **Step 11: Commit**

```bash
git add backend/src/grimoire/store/appearances.py backend/src/grimoire/store/sync.py backend/src/grimoire/routes.py backend/tests/test_appearances_store.py backend/tests/test_character_sync.py backend/tests/test_routes.py
git commit -m "feat(store): actor-kind+role appearances (PCs, character-as-player) + actor sync"
```

---

## Self-Review notes (coverage)

- Spec §"Storage" tags.md / pcs → Tasks 1, 2. PC version markdown payload (name/pronouns/summary +
  description body) → Task 2 `_dump_persona`/`_load_persona`.
- Spec §"appearances.json generalization" (keys `<kind>/<id>` + role) → Task 3.
- Spec §"Actor model" (kind table, role default npc / pc forced player, character-as-player) →
  Task 3 (`post_scene_cast` role rules + tests).
- Spec §"Sync (PCs reuse the character engine)" (actor-incoming, accept/reject by kind, PCs never
  `new`, push counts) → Task 3 (`_actor_incoming`/`_advance_actor`; counts unchanged since the
  items carry `update`/`conflict`).
- Spec §"Decisions" role+version lock at first appearance / 409 → Task 3
  (`test_version_or_role_mismatch_rejected`, route `AppearError` → 409).
- Spec §"Error handling" (PCNotFound/PCVersionNotFound/TagNotFound 404; unknown kind 404; bad tag
  400) → Tasks 1, 2, 3.
- Spec §"Non-goals: no prompt injection" → no `prompt.py`/chat changes in this plan; scene chat
  untouched.
- Spec §"Testing" bullets → Tasks 1–3 cover tag CRUD, PC round-trip+hash, appear locks
  version+role, scene_cast both kinds, `players_in_scene` filters player, PC sync update, bad-tag
  400, character-as-player.
- Documented deviation: none (the spec already accounts for the `appearances.json` format change).
