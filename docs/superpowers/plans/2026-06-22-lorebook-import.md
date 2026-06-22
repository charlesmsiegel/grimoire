# Lorebook / World-Info Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Import SillyTavern world-info (standalone lorebook `.json` or a card's embedded `character_book`) into grimoire as keyed Lore/location entities, via a stateless `parse` then a routed `commit`.

**Architecture:** A new `store/lorebook.py` translates ST world-info ↔ grimoire entities: `parse(data, fmt)` normalizes both ST entry schemas (standalone export vs `character_book`) into `{name, keys, body, category}` dicts (reusing `cards.loads` for card formats), and `commit(root, entries)` writes each via `entities.create_entity`. Two world routes expose them: multipart `parse`, JSON `import`.

**Tech Stack:** Python 3, FastAPI, pytest. No new dependencies.

## Global Constraints

- Run tests from `backend/` with `.venv/Scripts/python.exe -m pytest -q`. Suite green at **145**; keep it green every task.
- IDs via `slugify` + `uniquify` (already in `entities.create_entity`).
- Store modules: one responsibility; re-export in `store/__init__.py`; routes literal-before-generic in `routes.py`.
- Tests use temp `GRIMOIRE_HOME`; routes use the `client` fixture in `tests/test_routes.py`.
- Commit footer: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Do not push, open PRs, or touch `main`.

## File Structure

- Create `backend/src/grimoire/store/lorebook.py` — `LorebookError`, `parse`, `commit`, `_normalize`.
- Modify `backend/src/grimoire/store/__init__.py` — register module + exception.
- Modify `backend/src/grimoire/routes.py` — `parse` (multipart) + `import` (JSON) world routes.
- Create `backend/tests/test_lorebook_store.py`.
- Modify `backend/tests/test_routes.py` — route tests + builder-activation sanity check.

---

### Task 1: `lorebook.py` — parser (`_normalize` + `parse`)

**Files:**
- Create: `backend/src/grimoire/store/lorebook.py`
- Modify: `backend/src/grimoire/store/__init__.py`
- Test: `backend/tests/test_lorebook_store.py`

**Interfaces:**
- Consumes: `cards.loads(data, fmt)`, `cards.CardParseError`.
- Produces: `LorebookError`; `parse(data: bytes, fmt: str) -> list[dict]`; `_normalize(book: dict|list) -> list[dict]`. Each entry dict: `{name:str, keys:list[str], body:str, category:"lore"}`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lorebook_store.py
import json

import pytest

from grimoire.store import cards, characters, lorebook


def test_normalize_standalone_export_dict_entries():
    book = {"entries": {
        "0": {"key": ["pact", "salt"], "comment": "Salt Pact", "content": "The pact binds."},
        "1": {"key": ["king"], "comment": "Constant Lore", "content": "Always here.", "constant": True},
        "2": {"key": ["ghost"], "comment": "Disabled", "content": "skip me", "disable": True},
        "3": {"key": ["blank"], "comment": "Blank", "content": "   "},
    }}
    out = lorebook._normalize(book)
    by_name = {e["name"]: e for e in out}
    assert set(by_name) == {"Salt Pact", "Constant Lore"}      # disabled + blank dropped
    assert by_name["Salt Pact"]["keys"] == ["pact", "salt"]
    assert by_name["Salt Pact"]["body"] == "The pact binds."
    assert by_name["Salt Pact"]["category"] == "lore"
    assert by_name["Constant Lore"]["keys"] == []              # constant -> always-on (keyless)


def test_normalize_character_book_list_entries():
    book = {"entries": [
        {"keys": ["sea"], "name": "The Sea", "content": "salt water", "enabled": True},
        {"keys": ["off"], "name": "Off", "content": "nope", "enabled": False},
    ]}
    out = lorebook._normalize(book)
    assert [e["name"] for e in out] == ["The Sea"]             # enabled:false dropped
    assert out[0]["keys"] == ["sea"]


def test_normalize_name_falls_back_to_first_key():
    book = {"entries": [{"keys": ["solo"], "content": "x"}]}
    assert lorebook._normalize(book)[0]["name"] == "solo"


def test_parse_lorebook_and_card_and_errors():
    # standalone lorebook bytes
    data = json.dumps({"entries": {"0": {"key": ["a"], "content": "body", "comment": "A"}}}).encode()
    assert lorebook.parse(data, "lorebook")[0]["name"] == "A"
    # a card with an embedded character_book (json format)
    card = characters.blank_card("Hero")
    card["data"]["character_book"] = {"entries": [{"keys": ["k"], "content": "c", "name": "K"}]}
    assert lorebook.parse(json.dumps(card).encode(), "json")[0]["name"] == "K"
    # a card with no character_book -> []
    assert lorebook.parse(json.dumps(characters.blank_card("Z")).encode(), "json") == []
    # bad lorebook json -> LorebookError
    with pytest.raises(lorebook.LorebookError):
        lorebook.parse(b"not json", "lorebook")
    # bad card -> CardParseError
    with pytest.raises(cards.CardParseError):
        lorebook.parse(b"garbage", "json")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_lorebook_store.py -q`
Expected: FAIL (`ModuleNotFoundError: lorebook`).

- [ ] **Step 3: Write the parser**

```python
# backend/src/grimoire/store/lorebook.py
"""Translate SillyTavern world-info into grimoire entities.

Two ST entry schemas are normalized: the standalone world-info export (entries
keyed by index, fields `key`/`comment`/`disable`) and the V3 `character_book`
(entries as a list, fields `keys`/`name`/`enabled`). Both become editable
entities with a markdown body + comma-joined `keys` — the triggers the context
builder already consumes. `constant` -> keyless (always-on); disabled/blank
entries are skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import cards, entities


class LorebookError(Exception):
    pass


def _entries_container(book):
    if isinstance(book, dict):
        inner = book.get("entries", book)
    else:
        inner = book
    if isinstance(inner, dict):
        return list(inner.values())
    if isinstance(inner, list):
        return inner
    return []


def _normalize(book) -> list[dict]:
    out: list[dict] = []
    for e in _entries_container(book):
        if not isinstance(e, dict):
            continue
        enabled = e.get("enabled", True) and not e.get("disable", False)
        content = e.get("content", "")
        if not enabled or not isinstance(content, str) or not content.strip():
            continue
        keys = e.get("keys") or e.get("key") or []
        keys = [str(k) for k in keys if str(k).strip()]
        name = e.get("comment") or e.get("name") or (keys[0] if keys else "Imported entry")
        out.append({
            "name": name,
            "keys": [] if e.get("constant") else keys,
            "body": content,
            "category": "lore",
        })
    return out


def parse(data: bytes, fmt: str) -> list[dict]:
    if fmt == "lorebook":
        try:
            book = json.loads(data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise LorebookError(f"invalid lorebook JSON: {exc}") from exc
        return _normalize(book)
    if fmt in ("json", "png", "charx"):
        card = cards.loads(data, fmt)  # raises cards.CardParseError
        return _normalize(card.get("data", {}).get("character_book") or {})
    raise LorebookError(f"unknown format: {fmt}")
```

Register in `store/__init__.py`: add `lorebook` to the `from . import …` block, add `from .lorebook import LorebookError`, add `"lorebook"` and `"LorebookError"` to `__all__`.

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_lorebook_store.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Full suite + commit**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q` (expect 149 passed).
```bash
git add backend/src/grimoire/store/lorebook.py backend/src/grimoire/store/__init__.py backend/tests/test_lorebook_store.py
git commit -m "feat: lorebook parser — ST world-info -> normalized entries

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: `lorebook.commit` — route entries to entity categories

**Files:**
- Modify: `backend/src/grimoire/store/lorebook.py`
- Test: `backend/tests/test_lorebook_store.py`

**Interfaces:**
- Consumes: `entities.create_entity(root, kind, name, body, keys)`, `entities.ENTITY_KINDS`, `entities.read_entity`.
- Produces: `commit(root: Path, entries: list[dict]) -> list[dict]` returning `[{"kind": category, "id": eid}]`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_lorebook_store.py`:

```python
from grimoire.store import entities  # noqa: E402


def test_commit_routes_and_writes_keys(tmp_path):
    created = lorebook.commit(tmp_path, [
        {"name": "Salt Pact", "keys": ["pact", "salt"], "body": "binds", "category": "lore"},
        {"name": "The Docks", "keys": ["docks"], "body": "wet", "category": "locations"},
        {"name": "No Cat", "keys": [], "body": "x"},   # default category -> lore
    ])
    assert [c["kind"] for c in created] == ["lore", "locations", "lore"]
    lore_ids = [e["id"] for e in entities.list_entities(tmp_path, "lore")]
    assert created[0]["id"] in lore_ids and created[2]["id"] in lore_ids
    # keys round-trip as the builder reads them (comma-joined frontmatter)
    e = entities.read_entity(tmp_path, "lore", created[0]["id"])
    assert e["meta"]["keys"] == "pact,salt"
    assert e["body"].strip() == "binds"


def test_commit_unknown_category_raises(tmp_path):
    with pytest.raises(lorebook.LorebookError):
        lorebook.commit(tmp_path, [{"name": "X", "keys": [], "body": "y", "category": "bogus"}])
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_lorebook_store.py -q`
Expected: FAIL (`AttributeError: commit`).

- [ ] **Step 3: Add `commit`**

Append to `backend/src/grimoire/store/lorebook.py`:

```python
def commit(root: Path, entries: list[dict]) -> list[dict]:
    created: list[dict] = []
    for e in entries:
        category = e.get("category", "lore")
        if category not in entities.ENTITY_KINDS:
            raise LorebookError(f"unknown category: {category}")
        eid = entities.create_entity(root, category, e.get("name", "Imported entry"),
                                     e.get("body", ""), ",".join(e.get("keys", [])))
        created.append({"kind": category, "id": eid})
    return created
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_lorebook_store.py -q`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
```bash
git add backend/src/grimoire/store/lorebook.py backend/tests/test_lorebook_store.py
git commit -m "feat: lorebook commit — route entries to entity categories

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: routes — `parse` (multipart) + `import` (JSON)

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.lorebook.parse/commit`, `store.lorebook.LorebookError`, `store.cards.CardParseError`, `_world_root_or_404`.
- Produces: `POST /worlds/{wid}/lorebook/parse` → `{entries}`; `POST /worlds/{wid}/lorebook/import` → `{created}`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py`:

```python
def test_lorebook_parse_then_import(client):
    wid = _world(client)
    book = {"entries": {
        "0": {"key": ["pact"], "comment": "Salt Pact", "content": "It binds."},
        "1": {"key": ["docks"], "comment": "The Docks", "content": "Wet planks."},
    }}
    files = {"file": ("wi.json", io.BytesIO(json.dumps(book).encode()), "application/json")}
    parsed = client.post(f"/api/worlds/{wid}/lorebook/parse", files=files,
                         data={"format": "lorebook"})
    assert parsed.status_code == 200
    entries = parsed.json()["entries"]
    assert {e["name"] for e in entries} == {"Salt Pact", "The Docks"}
    # parse writes nothing
    assert client.get(f"/api/worlds/{wid}/lore").json() == []

    # route the docks entry to locations, keep the other as lore, then commit
    for e in entries:
        if e["name"] == "The Docks":
            e["category"] = "locations"
    created = client.post(f"/api/worlds/{wid}/lorebook/import", json={"entries": entries})
    assert created.status_code == 200
    kinds = {c["kind"] for c in created.json()["created"]}
    assert kinds == {"lore", "locations"}
    assert [e["name"] for e in client.get(f"/api/worlds/{wid}/lore").json()] == ["Salt Pact"]
    assert [e["name"] for e in client.get(f"/api/worlds/{wid}/locations").json()] == ["The Docks"]


def test_lorebook_parse_bad_file_400(client):
    wid = _world(client)
    files = {"file": ("x.json", io.BytesIO(b"not json"), "application/json")}
    r = client.post(f"/api/worlds/{wid}/lorebook/parse", files=files, data={"format": "lorebook"})
    assert r.status_code == 400


def test_lorebook_import_unknown_category_400(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/lorebook/import",
                    json={"entries": [{"name": "X", "keys": [], "body": "y", "category": "bogus"}]})
    assert r.status_code == 400


def test_lorebook_imported_key_activates_in_builder(client):
    # end-to-end sanity: an imported keyed entry feeds the context builder
    wid = _world(client)
    book = {"entries": {"0": {"key": ["leviathan"], "comment": "Leviathan", "content": "the beast"}}}
    files = {"file": ("wi.json", io.BytesIO(json.dumps(book).encode()), "application/json")}
    entries = client.post(f"/api/worlds/{wid}/lorebook/parse", files=files,
                          data={"format": "lorebook"}).json()["entries"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    # commit into the CAMPAIGN root via the store (campaign-scoped lore the builder reads)
    import grimoire.store as store
    store.lorebook.commit(store.campaigns.campaign_root(cid), entries)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    store.scenes.append_message(cid, sid, "user", "the leviathan rises")
    msgs = store.context.build_messages(cid, sid)
    assert any("the beast" in m["content"] for m in msgs if m["role"] == "system")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_routes.py -q -k lorebook`
Expected: FAIL (routes 404/405).

- [ ] **Step 3a: Add request models to `routes.py`**

Near the other `BaseModel`s:

```python
class LoreEntry(BaseModel):
    name: str
    keys: list[str] = []
    body: str = ""
    category: str = "lore"


class LorebookCommit(BaseModel):
    entries: list[LoreEntry]
```

- [ ] **Step 3b: Add the routes before the generic `/worlds/{wid}/{kind}` block**

Place next to the world greeting routes (after `delete_world_greeting`, before the generic entity section):

```python
# ---- world lorebook import (declared before the generic /{kind} routes) ----
@router.post("/worlds/{wid}/lorebook/parse")
async def post_lorebook_parse(wid: str, file: UploadFile = File(...), format: str = Form(...)):
    _world_root_or_404(wid)
    data = await file.read()
    try:
        return {"entries": store.lorebook.parse(data, format)}
    except (store.lorebook.LorebookError, store.cards.CardParseError) as exc:
        raise HTTPException(status_code=400, detail=f"could not parse: {exc}")


@router.post("/worlds/{wid}/lorebook/import")
def post_lorebook_import(wid: str, body: LorebookCommit):
    root = _world_root_or_404(wid)
    try:
        created = store.lorebook.commit(root, [e.model_dump() for e in body.entries])
    except store.lorebook.LorebookError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"created": created}
```

- [ ] **Step 4: Run to verify pass**

Run: `cd backend && .venv/Scripts/python.exe -m pytest tests/test_routes.py -q -k lorebook`
Expected: PASS.

- [ ] **Step 5: Full suite + commit**

Run: `cd backend && .venv/Scripts/python.exe -m pytest -q`
```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: lorebook parse + import routes (parse-then-commit)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Final steps (after Task 3)

- [ ] Whole-branch read-only review over `git diff greetings-plotmaps...HEAD`; fix Critical/Important.
- [ ] Update `.superpowers/sdd/progress.md`.
- [ ] Squash the whole branch to one commit (`git reset --soft greetings-plotmaps` then one commit), keep the branch.

## Self-Review

**Spec coverage:** two sources → one destination (Task 1 `parse` handles `lorebook` + card formats) ✓; field mapping incl. constant→keyless, skip disabled/blank, key/keys + comment/name (Task 1 `_normalize`) ✓; per-entry category routing (Task 2 `commit`, Task 3 `import`) ✓; parse-then-commit (Task 3 two routes) ✓; reuse card parser for `character_book` (Task 1 via `cards.loads`) ✓; no dedup / uniquify (inherited from `create_entity`) ✓; keys populate the builder's triggers (Task 3 activation sanity check) ✓.

**Placeholder scan:** none — every step has concrete code/commands.

**Type consistency:** `parse`/`_normalize` return `[{name,keys,body,category}]`; `commit` consumes the same dict shape and returns `[{kind,id}]`; routes wrap them as `{entries}`/`{created}`; `LoreEntry` fields match the entry dict keys. Consistent across tasks.
