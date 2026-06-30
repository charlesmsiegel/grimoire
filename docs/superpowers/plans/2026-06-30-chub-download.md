# Download from chub.ai Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three character-editor entry points — download a chub.ai card as a new character, download one as a new version of an already-open character (variant support), and manually link an already-imported character to a chub.ai URL with no download — all backed by a reverse-engineered, best-effort chub.ai API client.

**Architecture:** A new read-only `store/chub.py` client module (character/lorebook lookup, gallery listing — no filesystem writes) feeds a new `store/characters.import_from_chub` orchestration function that reuses the existing PNG-import, per-version image store, and lorebook-commit paths verbatim. Three new routes expose this; three new buttons in `CharacterEditor.tsx` call them via `window.prompt`, matching the file's existing prompt-driven UI pattern.

**Tech Stack:** FastAPI + pytest (backend), React + Vite + vitest (frontend), `httpx` for outbound chub.ai requests (already a dependency).

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)` (existing route tests use a `client(monkeypatch, tmp_path)` fixture that does this).
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q` (or scoped to a file/test with `::`).
- Run frontend tests from `frontend/`: `npx vitest run` (running from the repo root skips `vitest.config.ts` and breaks every mock-based test — always `cd`/invoke from `frontend/`).
- Run frontend type check from `frontend/`: `npx tsc -b`.
- The chub.ai endpoints used here (`api.chub.ai`, `gateway.chub.ai`) have no official docs and were reverse-engineered by live testing during design — see
  `docs/superpowers/specs/2026-06-30-chub-download-design.md` for the verified shapes. Every backend test in this plan mocks the network seam; nothing here makes a real request to chub.ai.
- The primary character fetch (chub lookup + PNG download) is **not** best-effort — a failure raises and nothing is created. Gallery downloads and linked-lorebook fetches **are** best-effort — a failure is counted and skipped, never raised, because the character import has already succeeded by that point.

---

## File Structure

- **Create** `backend/src/grimoire/store/chub.py` — pure chub.ai API client (parse a URL/path, fetch a character node, fetch a lorebook node, list gallery image URLs). No disk writes.
- **Create** `backend/tests/test_chub_store.py` — unit tests for `store/chub.py`.
- **Modify** `backend/src/grimoire/store/characters.py` — add `set_chub_source`/`clear_chub_source`, expose `chub_source` in `read_character`, add `import_from_chub` orchestration.
- **Modify** `backend/tests/test_characters_store.py` — tests for the above.
- **Modify** `backend/src/grimoire/store/__init__.py` — register the `chub` submodule and its exceptions, matching how `lorebook`/`LorebookError` are already registered.
- **Modify** `backend/src/grimoire/routes.py` — three new routes: `POST .../characters/import/chub`, `POST .../characters/{cid}/chub-source`, `DELETE .../characters/{cid}/chub-source`.
- **Modify** `backend/tests/test_routes.py` — route tests.
- **Modify** `frontend/src/api/client.ts` — `ChubImportResult` type, `chub_source` on `CharacterDetail.meta`, three new `api.*` functions.
- **Modify** `frontend/src/components/CharacterEditor.tsx` — three new buttons + handlers + a chub-source display block.
- **Modify** `frontend/src/components/CharacterEditor.test.tsx` — tests for the above.

---

### Task 1: `store/chub.py` — chub.ai API client

**Files:**
- Create: `backend/src/grimoire/store/chub.py`
- Test: `backend/tests/test_chub_store.py`

**Interfaces:**
- Produces: `chub.ChubParseError`, `chub.ChubFetchError` (exceptions, not raised by this module itself — `characters.import_from_chub` in Task 3 raises them); `chub.parse_full_path(url_or_path: str) -> str | None`; `chub.fetch_character_node(full_path: str) -> dict | None`; `chub.fetch_lorebook_node(lorebook_id: int) -> dict | None`; `chub.fetch_gallery_paths(project_id: int) -> list[str]`; the private seam `chub._get_json(url: str) -> dict | None` (the thing later tests/tasks monkeypatch).

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_chub_store.py`:

```python
from grimoire.store import chub


def test_parse_full_path_from_url():
    url = "https://chub.ai/characters/Vanlos1/lakshmi-white-snake-a17db356c017"
    assert chub.parse_full_path(url) == "Vanlos1/lakshmi-white-snake-a17db356c017"


def test_parse_full_path_strips_query_and_trailing_slash():
    assert chub.parse_full_path("https://chub.ai/characters/a/b/?ref=share") == "a/b"


def test_parse_full_path_from_bare_path():
    assert chub.parse_full_path("creator/slug") == "creator/slug"


def test_parse_full_path_rejects_garbage():
    assert chub.parse_full_path("not a url") is None
    assert chub.parse_full_path("https://example.com/characters/a/b") is None
    assert chub.parse_full_path("a/b/c") is None
    assert chub.parse_full_path("") is None


def test_fetch_character_node_returns_node(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: {"node": {"id": 1, "hasGallery": False}})
    assert chub.fetch_character_node("a/b") == {"id": 1, "hasGallery": False}


def test_fetch_character_node_requests_expected_url(monkeypatch):
    captured = {}

    def fake(url):
        captured["url"] = url
        return {"node": {}}

    monkeypatch.setattr(chub, "_get_json", fake)
    chub.fetch_character_node("creator/slug")
    assert captured["url"] == "https://api.chub.ai/api/characters/creator/slug?full=true"


def test_fetch_character_node_none_on_failure(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: None)
    assert chub.fetch_character_node("a/b") is None


def test_fetch_lorebook_node_requests_expected_url(monkeypatch):
    captured = {}

    def fake(url):
        captured["url"] = url
        return {"node": {"id": 7}}

    monkeypatch.setattr(chub, "_get_json", fake)
    assert chub.fetch_lorebook_node(7) == {"id": 7}
    assert captured["url"] == "https://api.chub.ai/api/lorebooks/7?full=true"


def test_fetch_lorebook_node_none_on_failure(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: None)
    assert chub.fetch_lorebook_node(7) is None


def test_fetch_gallery_paths_extracts_primary_image_path(monkeypatch):
    captured = {}

    def fake(url):
        captured["url"] = url
        return {"count": 2, "nodes": [
            {"primary_image_path": "https://x/1.jpg"},
            {"primary_image_path": "https://x/2.jpg"},
        ], "page": 1}

    monkeypatch.setattr(chub, "_get_json", fake)
    assert chub.fetch_gallery_paths(42) == ["https://x/1.jpg", "https://x/2.jpg"]
    assert captured["url"] == "https://gateway.chub.ai/api/gallery/project/42?limit=48&count=false"


def test_fetch_gallery_paths_skips_malformed_nodes(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: {"nodes": [
        {"primary_image_path": "https://x/1.jpg"},
        {"primary_image_path": ""},
        {},
        "not even a dict",
    ]})
    assert chub.fetch_gallery_paths(42) == ["https://x/1.jpg"]


def test_fetch_gallery_paths_empty_on_failure(monkeypatch):
    monkeypatch.setattr(chub, "_get_json", lambda url: None)
    assert chub.fetch_gallery_paths(42) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_chub_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.chub'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/grimoire/store/chub.py`:

```python
"""Read-only client for chub.ai's (undocumented) public API: character and
lorebook lookup, gallery listing. Never writes to disk. Endpoints were
reverse-engineered by live testing -- see
docs/superpowers/specs/2026-06-30-chub-download-design.md for the verified
shapes and the fragility risk that implies.
"""

from __future__ import annotations

import re

import certifi
import httpx

_TIMEOUT = 10.0
_UA = "Mozilla/5.0 (grimoire chub.ai import)"
_PATH_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_URL_RE = re.compile(r"^https?://(?:www\.)?chub\.ai/characters/([\w.-]+/[\w.-]+)/?$")


class ChubParseError(Exception):
    pass


class ChubFetchError(Exception):
    pass


def parse_full_path(url_or_path: str) -> str | None:
    """Accept a chub.ai character page URL or a bare "creator/slug" path."""
    s = url_or_path.strip()
    if s.startswith(("http://", "https://")):
        s = s.split("?", 1)[0].split("#", 1)[0]
        m = _URL_RE.match(s)
        return m.group(1) if m else None
    return s if _PATH_RE.match(s) else None


def _get_json(url: str) -> dict | None:
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    try:
        with httpx.Client(timeout=_TIMEOUT, verify=certifi.where(), headers=headers) as client:
            r = client.get(url)
            r.raise_for_status()
            return r.json()
    except Exception:  # noqa: BLE001 — best-effort; callers handle a None
        return None


def fetch_character_node(full_path: str) -> dict | None:
    data = _get_json(f"https://api.chub.ai/api/characters/{full_path}?full=true")
    return data.get("node") if data else None


def fetch_lorebook_node(lorebook_id: int) -> dict | None:
    data = _get_json(f"https://api.chub.ai/api/lorebooks/{lorebook_id}?full=true")
    return data.get("node") if data else None


def fetch_gallery_paths(project_id: int) -> list[str]:
    data = _get_json(f"https://gateway.chub.ai/api/gallery/project/{project_id}?limit=48&count=false")
    if not data:
        return []
    nodes = data.get("nodes") or []
    return [n["primary_image_path"] for n in nodes
            if isinstance(n, dict) and n.get("primary_image_path")]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_chub_store.py -q`
Expected: PASS — 13 passed

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/chub.py backend/tests/test_chub_store.py
git commit -m "feat: chub.ai API client (character/lorebook lookup, gallery listing)"
```

---

### Task 2: `chub_source` on characters

**Files:**
- Modify: `backend/src/grimoire/store/characters.py:113-119` (after `set_birthdate`), `:152-157` (`read_character`'s `meta` dict)
- Modify: `backend/tests/test_characters_store.py`

**Interfaces:**
- Produces: `characters.set_chub_source(root, cid, full_path: str) -> None`; `characters.clear_chub_source(root, cid) -> None`; `read_character(...)["meta"]["chub_source"]` (empty string when unset).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_characters_store.py`:

```python
def test_set_and_clear_chub_source(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == ""
    ch.set_chub_source(tmp_path, cid, "creator/slug")
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == "creator/slug"
    ch.clear_chub_source(tmp_path, cid)
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == ""


def test_clear_chub_source_when_absent_is_a_noop(tmp_path):
    cid, _ = ch.create_character(tmp_path, "Seraphine")
    ch.clear_chub_source(tmp_path, cid)  # must not raise
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == ""


def test_chub_source_setters_require_known_character(tmp_path):
    with pytest.raises(ch.CharacterNotFound):
        ch.set_chub_source(tmp_path, "nobody", "creator/slug")
    with pytest.raises(ch.CharacterNotFound):
        ch.clear_chub_source(tmp_path, "nobody")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_characters_store.py -k chub_source -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.characters' has no attribute 'set_chub_source'`

- [ ] **Step 3: Write the implementation**

In `backend/src/grimoire/store/characters.py`, add right after `set_birthdate` (which ends at line 118):

```python
def set_chub_source(root: Path, cid: str, full_path: str) -> None:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta["chub_source"] = full_path
    _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")


def clear_chub_source(root: Path, cid: str) -> None:
    _require_char(root, cid)
    meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
    meta.pop("chub_source", None)
    _meta_path(root, cid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")
```

Then in `read_character`, change the `meta` dict (currently):

```python
        "meta": {"id": cid, "name": meta.get("name", cid),
                 "default_version": meta.get("default_version", ""),
                 "birthdate": meta.get("birthdate", "")},
```

to:

```python
        "meta": {"id": cid, "name": meta.get("name", cid),
                 "default_version": meta.get("default_version", ""),
                 "birthdate": meta.get("birthdate", ""),
                 "chub_source": meta.get("chub_source", "")},
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_characters_store.py -q`
Expected: PASS — all tests in the file pass (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/characters.py backend/tests/test_characters_store.py
git commit -m "feat: chub_source field on characters"
```

---

### Task 3: `characters.import_from_chub` orchestration

**Files:**
- Modify: `backend/src/grimoire/store/characters.py` (top-level imports, plus a new function after `import_card`)
- Modify: `backend/tests/test_characters_store.py`

**Interfaces:**
- Consumes: `chub.parse_full_path`, `chub.fetch_character_node`, `chub.fetch_lorebook_node`, `chub.fetch_gallery_paths`, `chub.ChubParseError`, `chub.ChubFetchError` (Task 1); `fetch.download_url` (existing); `assets.put_image` (existing); `lorebook.from_character_book`, `lorebook.commit` (existing); `import_card` (existing, same file).
- Produces: `characters.import_from_chub(root: Path, url_or_path: str, into_cid: str | None = None) -> dict` returning
  `{"character": str, "version": str, "gallery": {"attempted": int, "stored": int}, "lore": {"lorebooks_found": int, "created": [{"kind": str, "id": str}, ...]}}`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_characters_store.py`:

```python
def test_import_from_chub_happy_path(tmp_path, monkeypatch):
    from grimoire.store import assets, cards, chub

    png = cards.dumps(ch.blank_card("Imp"), "png")
    node = {
        "id": 42, "fullPath": "creator/imp", "hasGallery": True,
        "related_lorebooks": [7, 7, -1],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    }
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: node)
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg", "https://g/2.jpg"])
    monkeypatch.setattr(chub, "fetch_lorebook_node", lambda lid: {
        "definition": {"embedded_lorebook": {"entries": [{"keys": ["k"], "content": "lore body"}]}},
    })

    def fake_get_bytes(url):
        if "/g/" in url:
            return (b"\xff\xd8\xffJPEGDATA", "image/jpeg")
        return (png, "image/png")

    monkeypatch.setattr(fetch, "_http_get_bytes", fake_get_bytes)

    result = ch.import_from_chub(tmp_path, "https://chub.ai/characters/creator/imp")

    cid, vid = result["character"], result["version"]
    assert ch.read_character(tmp_path, cid)["meta"]["chub_source"] == "creator/imp"
    assert assets.image_path(tmp_path, cid, vid, "avatar") is not None
    names = {i["name"] for i in assets.list_images(tmp_path, cid, vid)}
    assert names == {"avatar", "gallery_0", "gallery_1"}
    assert result["gallery"] == {"attempted": 2, "stored": 2}
    assert result["lore"]["lorebooks_found"] == 1  # [7, 7, -1] -> dedup'd to one positive id
    assert len(result["lore"]["created"]) == 1


def test_import_from_chub_bad_url_raises_parse_error(tmp_path):
    from grimoire.store import chub
    with pytest.raises(chub.ChubParseError):
        ch.import_from_chub(tmp_path, "not a url")


def test_import_from_chub_unreachable_character_raises_fetch_error(tmp_path, monkeypatch):
    from grimoire.store import chub
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: None)
    with pytest.raises(chub.ChubFetchError):
        ch.import_from_chub(tmp_path, "creator/missing")


def test_import_from_chub_png_download_failure_raises_fetch_error(tmp_path, monkeypatch):
    from grimoire.store import chub
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })

    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(fetch, "_http_get_bytes", boom)
    with pytest.raises(chub.ChubFetchError):
        ch.import_from_chub(tmp_path, "creator/imp")
    assert ch.character_count(tmp_path) == 0  # nothing partially created


def test_import_from_chub_gallery_failure_is_best_effort(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    png = cards.dumps(ch.blank_card("Imp"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": True, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(chub, "fetch_gallery_paths", lambda pid: ["https://g/1.jpg", "https://g/2.jpg"])

    def fake_get_bytes(url):
        if url == "https://g/1.jpg":
            raise RuntimeError("one image failed")
        if "/g/" in url:
            return (b"\xff\xd8\xffJPEGDATA", "image/jpeg")
        return (png, "image/png")

    monkeypatch.setattr(fetch, "_http_get_bytes", fake_get_bytes)
    result = ch.import_from_chub(tmp_path, "creator/imp")  # must not raise
    assert result["gallery"] == {"attempted": 2, "stored": 1}


def test_import_from_chub_lorebook_failure_is_best_effort(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    png = cards.dumps(ch.blank_card("Imp"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [7, 8],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))
    monkeypatch.setattr(chub, "fetch_lorebook_node", lambda lid: None if lid == 7 else {
        "definition": {"embedded_lorebook": {"entries": [{"keys": ["k"], "content": "x"}]}},
    })
    result = ch.import_from_chub(tmp_path, "creator/imp")  # must not raise
    assert result["lore"]["lorebooks_found"] == 2
    assert len(result["lore"]["created"]) == 1  # only id 8 resolved


def test_import_from_chub_into_existing_character_adds_a_version(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    cid, _ = ch.create_character(tmp_path, "Seraphine")
    png = cards.dumps(ch.blank_card("Variant"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/variant/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    result = ch.import_from_chub(tmp_path, "creator/variant", into_cid=cid)
    assert result["character"] == cid
    assert {v["id"] for v in ch.read_character(tmp_path, cid)["versions"]} == {"default", result["version"]}


def test_import_from_chub_into_unknown_character_raises(tmp_path, monkeypatch):
    from grimoire.store import cards, chub

    png = cards.dumps(ch.blank_card("Imp"), "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(fetch, "_http_get_bytes", lambda url: (png, "image/png"))
    with pytest.raises(ch.CharacterNotFound):
        ch.import_from_chub(tmp_path, "creator/imp", into_cid="nobody")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_characters_store.py -k import_from_chub -q`
Expected: FAIL — `AttributeError: module 'grimoire.store.characters' has no attribute 'import_from_chub'`

- [ ] **Step 3: Write the implementation**

In `backend/src/grimoire/store/characters.py`, change the top import line from:

```python
from . import assets, fetch
```

to:

```python
from . import assets, chub, fetch, lorebook
```

Then add this function after `import_card` (which ends right before `export_card`):

```python
def import_from_chub(root: Path, url_or_path: str, into_cid: str | None = None) -> dict:
    full_path = chub.parse_full_path(url_or_path)
    if full_path is None:
        raise chub.ChubParseError(url_or_path)
    node = chub.fetch_character_node(full_path)
    if node is None:
        raise chub.ChubFetchError(full_path)
    png = fetch.download_url(node.get("max_res_url") or "")
    if png is None:
        raise chub.ChubFetchError(full_path)

    cid, vid = import_card(root, png[0], "png", into_cid)
    set_chub_source(root, cid, full_path)

    gallery_attempted = 0
    gallery_stored = 0
    if node.get("hasGallery"):
        paths = chub.fetch_gallery_paths(node["id"])
        gallery_attempted = len(paths)
        for i, path in enumerate(paths):
            got = fetch.download_url(path)
            if got:
                assets.put_image(root, cid, vid, f"gallery_{i}", got[0], got[1])
                gallery_stored += 1

    lorebook_ids = [i for i in dict.fromkeys(node.get("related_lorebooks") or [])
                    if isinstance(i, int) and i > 0]
    created: list[dict] = []
    for lid in lorebook_ids:
        lb_node = chub.fetch_lorebook_node(lid)
        if not lb_node:
            continue
        book = (lb_node.get("definition") or {}).get("embedded_lorebook")
        if not book:
            continue
        created.extend(lorebook.commit(root, lorebook.from_character_book(book)))

    return {
        "character": cid, "version": vid,
        "gallery": {"attempted": gallery_attempted, "stored": gallery_stored},
        "lore": {"lorebooks_found": len(lorebook_ids), "created": created},
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_characters_store.py -q`
Expected: PASS — all tests in the file pass

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/characters.py backend/tests/test_characters_store.py
git commit -m "feat: import_from_chub orchestrates PNG + gallery + linked lorebooks"
```

---

### Task 4: Routes + module registration

**Files:**
- Modify: `backend/src/grimoire/store/__init__.py`
- Modify: `backend/src/grimoire/routes.py:74-76` (Pydantic models), `:413-419` (after `put_world_character_birthdate`), `:521` (after `post_character_import`)
- Modify: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.chub.ChubParseError`, `store.chub.ChubFetchError`, `store.characters.import_from_chub`, `store.characters.set_chub_source`, `store.characters.clear_chub_source`, `store.characters.CharacterNotFound` (Tasks 1-3).
- Produces: `POST /api/worlds/{wid}/characters/import/chub`, `POST /api/worlds/{wid}/characters/{cid}/chub-source`, `DELETE /api/worlds/{wid}/characters/{cid}/chub-source`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_routes.py` (near the other character-import tests):

```python
def test_chub_import_route(client, monkeypatch):
    from grimoire.store import cards, chub

    wid = _world(client)
    png = cards.dumps({"spec": "chara_card_v3", "spec_version": "3.0",
                        "data": {"name": "Imp", "extensions": {}}}, "png")
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: {
        "id": 1, "hasGallery": False, "related_lorebooks": [],
        "max_res_url": "https://avatars.charhub.io/avatars/creator/imp/chara_card_v2.png",
    })
    monkeypatch.setattr(store.fetch, "_http_get_bytes", lambda url: (png, "image/png"))

    r = client.post(f"/api/worlds/{wid}/characters/import/chub",
                     json={"url": "https://chub.ai/characters/creator/imp"})
    assert r.status_code == 200
    body = r.json()
    assert body["character"] and body["version"]
    assert body["gallery"] == {"attempted": 0, "stored": 0}
    assert body["lore"] == {"lorebooks_found": 0, "created": []}


def test_chub_import_route_bad_url(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/characters/import/chub", json={"url": "not a url"})
    assert r.status_code == 400


def test_chub_import_route_unreachable(client, monkeypatch):
    from grimoire.store import chub

    wid = _world(client)
    monkeypatch.setattr(chub, "fetch_character_node", lambda fp: None)
    r = client.post(f"/api/worlds/{wid}/characters/import/chub", json={"url": "creator/missing"})
    assert r.status_code == 404


def test_chub_source_routes(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]

    assert client.get(f"/api/worlds/{wid}/characters/{cid}").json()["meta"]["chub_source"] == ""

    r = client.post(f"/api/worlds/{wid}/characters/{cid}/chub-source", json={"url": "creator/slug"})
    assert r.status_code == 200 and r.json() == {"chub_source": "creator/slug"}
    assert client.get(f"/api/worlds/{wid}/characters/{cid}").json()["meta"]["chub_source"] == "creator/slug"

    r = client.delete(f"/api/worlds/{wid}/characters/{cid}/chub-source")
    assert r.status_code == 200 and r.json() == {"chub_source": ""}
    assert client.get(f"/api/worlds/{wid}/characters/{cid}").json()["meta"]["chub_source"] == ""


def test_chub_source_route_bad_url(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/chub-source", json={"url": "not a url"})
    assert r.status_code == 400


def test_chub_source_route_unknown_character(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/characters/nobody/chub-source", json={"url": "creator/slug"})
    assert r.status_code == 404
    r = client.delete(f"/api/worlds/{wid}/characters/nobody/chub-source")
    assert r.status_code == 404
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k chub -q`
Expected: FAIL — 404s (routes don't exist yet) / `AttributeError: 'module' object has no attribute 'chub'`

- [ ] **Step 3: Write the implementation**

In `backend/src/grimoire/store/__init__.py`, change the import block from:

```python
from . import (
    appearances, assets, briefs, campaigns, cards, characters, context, entities,
    fetch, greetings, localize, lorebook, pcs, playing, scenes, sync, tags,
    worlds,
)
```

to:

```python
from . import (
    appearances, assets, briefs, campaigns, cards, characters, chub, context,
    entities, fetch, greetings, localize, lorebook, pcs, playing, scenes, sync,
    tags, worlds,
)
```

Add `from .chub import ChubFetchError, ChubParseError` after the `from .characters import ...` line, and add `"chub"`, `"ChubParseError"`, `"ChubFetchError"` to `__all__` (next to `"characters"`, `"CharacterNotFound"`).

In `backend/src/grimoire/routes.py`, add two new Pydantic models after `CharacterBirthdate` (line 75):

```python
class ChubImportBody(BaseModel):
    url: str
    into: str | None = None


class ChubSourceBody(BaseModel):
    url: str
```

Add two routes right after `put_world_character_birthdate` (after line 419, before `delete_world_character`):

```python
@router.post("/worlds/{wid}/characters/{cid}/chub-source")
def post_world_character_chub_source(wid: str, cid: str, body: ChubSourceBody):
    root = _world_root_or_404(wid)
    full_path = store.chub.parse_full_path(body.url)
    if full_path is None:
        raise HTTPException(status_code=400, detail="not a valid chub.ai character URL or path")
    try:
        store.characters.set_chub_source(root, cid, full_path)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"chub_source": full_path}


@router.delete("/worlds/{wid}/characters/{cid}/chub-source")
def delete_world_character_chub_source(wid: str, cid: str):
    root = _world_root_or_404(wid)
    try:
        store.characters.clear_chub_source(root, cid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    return {"chub_source": ""}
```

Add the import route right after `post_character_import` (after line 521, before `get_character_export`):

```python
@router.post("/worlds/{wid}/characters/import/chub")
def post_character_import_chub(wid: str, body: ChubImportBody):
    root = _world_root_or_404(wid)
    try:
        return store.characters.import_from_chub(root, body.url, into_cid=body.into)
    except store.chub.ChubParseError:
        raise HTTPException(status_code=400, detail="not a valid chub.ai character URL or path")
    except store.chub.ChubFetchError:
        raise HTTPException(status_code=404, detail="could not fetch from chub.ai")
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
```

Note: no route ordering concern here — `POST /characters/import/chub` and `POST /characters/{cid}/chub-source` have different segment counts and a different literal second segment ("chub" vs "chub-source"), so neither can shadow the other or any existing route regardless of registration order.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS — full backend suite green

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/__init__.py backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: chub.ai import and chub-source routes"
```

---

### Task 5: Frontend API client

**Files:**
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Consumes: nothing new (HTTP only).
- Produces: `ChubImportResult` type; `CharacterDetail.meta.chub_source?: string`; `api.importCharacterFromChub(wid, url, into?) -> Promise<ChubImportResult>`; `api.setCharacterChubSource(wid, cid, url) -> Promise<{chub_source: string}>`; `api.clearCharacterChubSource(wid, cid) -> Promise<{chub_source: string}>`.

- [ ] **Step 1: Write the failing test**

This task has no standalone test (it's typed HTTP plumbing with no branching logic); it's exercised by Tasks 6-8's component tests, which mock `api.*` directly. Skip to implementation.

- [ ] **Step 2: Write the implementation**

In `frontend/src/api/client.ts`, change `CharacterDetail`'s `meta` type from:

```ts
export type CharacterDetail = {
  meta: { id: string; name: string; default_version: string; birthdate?: string };
  versions: { id: string; name: string; card: Card; images?: string[] }[];
};
```

to:

```ts
export type CharacterDetail = {
  meta: { id: string; name: string; default_version: string; birthdate?: string; chub_source?: string };
  versions: { id: string; name: string; card: Card; images?: string[] }[];
};
```

Add this type after `CharacterDetail`:

```ts
export type ChubImportResult = {
  character: string;
  version: string;
  gallery: { attempted: number; stored: number };
  lore: { lorebooks_found: number; created: { kind: string; id: string }[] };
};
```

Add these three functions right after `importCharacterBook` (which ends with `lorebook/import")` ):

```ts
  importCharacterFromChub: (wid: string, url: string, into?: string) =>
    request<ChubImportResult>(
      "POST", `/api/worlds/${wid}/characters/import/chub`, into ? { url, into } : { url }),
  setCharacterChubSource: (wid: string, cid: string, url: string) =>
    request<{ chub_source: string }>("POST", `/api/worlds/${wid}/characters/${cid}/chub-source`, { url }),
  clearCharacterChubSource: (wid: string, cid: string) =>
    request<{ chub_source: string }>("DELETE", `/api/worlds/${wid}/characters/${cid}/chub-source`),
```

- [ ] **Step 3: Run the type check**

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat: frontend client functions for chub.ai import and linking"
```

---

### Task 6: "Download from chub.ai" — new character

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`
- Modify: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `api.importCharacterFromChub` (Task 5).
- Produces: a `downloadFromChub` handler and a "Download from chub.ai" button in the grid toolbar; a shared `describeChubResult` helper reused by Task 7.

- [ ] **Step 1: Write the failing test**

In `frontend/src/components/CharacterEditor.test.tsx`, add `importCharacterFromChub: vi.fn(),` to the `vi.mock` block's `api` object (alongside `importCharacterBook: vi.fn(),`), and add this test:

```tsx
test("downloading from chub.ai creates a character and shows the result", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("https://chub.ai/characters/creator/imp");
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "imp", version: "default",
    gallery: { attempted: 2, stored: 2 },
    lore: { lorebooks_found: 1, created: [{ kind: "lore", id: "x" }] },
  });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /download from chub\.ai/i }));
  await waitFor(() =>
    expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "https://chub.ai/characters/creator/imp"));
  await screen.findByText(/downloaded from chub\.ai.*2\/2 gallery images.*1 lorebook \(1 entry\) added to world lore/i);
});

test("an empty chub.ai prompt makes no API call", async () => {
  vi.spyOn(window, "prompt").mockReturnValue(null);
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /download from chub\.ai/i }));
  expect(api.importCharacterFromChub).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx -t "chub"`
Expected: FAIL — no button named "download from chub.ai" found

- [ ] **Step 3: Write the implementation**

In `frontend/src/components/CharacterEditor.tsx`, add `type ChubImportResult` to the import line:

```ts
import { api, type Card, type CharacterDetail, type CharacterSummary, type ChubImportResult } from "../api/client";
```

Add this helper function above `CharacterEditor` (after the `TEXT_FIELDS` constant, before `type Mode = ...`):

```tsx
function describeChubResult(result: ChubImportResult): string {
  const parts: string[] = [];
  if (result.gallery.attempted > 0) {
    parts.push(`${result.gallery.stored}/${result.gallery.attempted} gallery image${result.gallery.attempted === 1 ? "" : "s"}`);
  }
  if (result.lore.lorebooks_found > 0) {
    const n = result.lore.created.length;
    parts.push(`${result.lore.lorebooks_found} lorebook${result.lore.lorebooks_found === 1 ? "" : "s"} (${n} ${n === 1 ? "entry" : "entries"}) added to world lore`);
  }
  return parts.length ? `Downloaded from chub.ai — ${parts.join(", ")}` : "Downloaded from chub.ai";
}
```

Add this handler inside `CharacterEditor`, right after `onImport` (which ends right before `const avatarSrc = ...`):

```tsx
  async function downloadFromChub() {
    const url = window.prompt("chub.ai character URL or path?")?.trim();
    if (!url) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.importCharacterFromChub(wid, url);
      await reload();
      await openDetail(result.character);
      setImportMsg(describeChubResult(result));
      await runLocalize(result.character, result.version);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

In the grid-mode JSX (the `mode === "grid"` block), add the button right after the existing `Import card` button + its hidden `<input>`:

```tsx
          <button className="subtle" onClick={() => fileRef.current?.click()}>Import card</button>
          <input ref={fileRef} type="file" accept=".json,.png,.charx" multiple hidden aria-label="Import character card" onChange={onImport} />
          <button className="subtle" onClick={downloadFromChub}>Download from chub.ai</button>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: PASS — full file green

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat: Download from chub.ai button creates a new character"
```

---

### Task 7: "Download version from chub.ai" — variant support

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`
- Modify: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `api.importCharacterFromChub` (Task 5), `describeChubResult` (Task 6).
- Produces: a `downloadVersionFromChub` handler and a "Download version from chub.ai" button in the edit form's version-picker row.

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/CharacterEditor.test.tsx`:

```tsx
test("downloading a version from chub.ai targets the open character", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("creator/imp-variant");
  (api.importCharacterFromChub as any).mockResolvedValue({
    character: "seraphine", version: "variant",
    gallery: { attempted: 0, stored: 0 },
    lore: { lorebooks_found: 0, created: [] },
  });
  render(<CharacterEditor wid="w" />);
  await openEditForm();
  fireEvent.click(screen.getByRole("button", { name: /download version from chub\.ai/i }));
  await waitFor(() =>
    expect(api.importCharacterFromChub).toHaveBeenCalledWith("w", "creator/imp-variant", "seraphine"));
  await screen.findByText(/^downloaded from chub\.ai$/i);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx -t "targets the open character"`
Expected: FAIL — no button named "download version from chub.ai" found

- [ ] **Step 3: Write the implementation**

Add this handler right after `downloadFromChub` (from Task 6):

```tsx
  async function downloadVersionFromChub() {
    if (!detail) return;
    const url = window.prompt("chub.ai character URL or path?")?.trim();
    if (!url) return;
    setError(null);
    setImportMsg(null);
    try {
      const result = await api.importCharacterFromChub(wid, url, detail.meta.id);
      const d = await api.readCharacter(wid, detail.meta.id);
      setDetail(d);
      loadVersion(d, result.version);
      await reload();
      setImportMsg(describeChubResult(result));
      await runLocalize(detail.meta.id, result.version);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

In the edit-mode JSX (`mode === "edit"`), the `.picker` div currently ends with:

```tsx
            <button className="subtle" onClick={setDefault}>Set default</button>
            <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>
          </div>
```

Change it to:

```tsx
            <button className="subtle" onClick={setDefault}>Set default</button>
            <button className="subtle" onClick={() => deleteCharacter(detail.meta.id, detail.meta.name)}>Delete</button>
            <button className="subtle" onClick={downloadVersionFromChub}>Download version from chub.ai</button>
            {importMsg && <span className="field-hint">{importMsg}</span>}
          </div>
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: PASS — full file green

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat: Download version from chub.ai attaches a variant to the open character"
```

---

### Task 8: Manual chub.ai link / unlink

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`
- Modify: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `api.setCharacterChubSource`, `api.clearCharacterChubSource` (Task 5).
- Produces: `linkChub` / `unlinkChub` handlers and a chub-source display block in the edit form, reading `detail.meta.chub_source`.

- [ ] **Step 1: Write the failing test**

In `frontend/src/components/CharacterEditor.test.tsx`, add `setCharacterChubSource: vi.fn(), clearCharacterChubSource: vi.fn(),` to the `vi.mock` block's `api` object, and add this test:

```tsx
test("linking a character to chub.ai shows the linked path and allows unlinking", async () => {
  vi.spyOn(window, "prompt").mockReturnValue("creator/imp");
  (api.setCharacterChubSource as any).mockResolvedValue({ chub_source: "creator/imp" });
  (api.clearCharacterChubSource as any).mockResolvedValue({ chub_source: "" });
  (api.readCharacter as any)
    .mockResolvedValueOnce(DETAIL) // openEditForm's initial select()
    .mockResolvedValueOnce({ ...DETAIL, meta: { ...DETAIL.meta, chub_source: "creator/imp" } }); // after linking

  render(<CharacterEditor wid="w" />);
  await openEditForm();
  expect(screen.queryByText(/linked to chub\.ai/i)).toBeNull();

  fireEvent.click(screen.getByRole("button", { name: /^link to chub\.ai$/i }));
  await waitFor(() => expect(api.setCharacterChubSource).toHaveBeenCalledWith("w", "seraphine", "creator/imp"));
  await screen.findByText(/linked to chub\.ai: creator\/imp/i);

  (api.readCharacter as any).mockResolvedValueOnce(DETAIL); // after unlinking, reverts
  fireEvent.click(screen.getByRole("button", { name: /^unlink$/i }));
  await waitFor(() => expect(api.clearCharacterChubSource).toHaveBeenCalledWith("w", "seraphine"));
  await screen.findByRole("button", { name: /^link to chub\.ai$/i });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx -t "linking a character"`
Expected: FAIL — no button named "link to chub.ai" found

- [ ] **Step 3: Write the implementation**

Add these handlers right after `downloadVersionFromChub` (from Task 7):

```tsx
  async function linkChub() {
    if (!detail) return;
    const url = window.prompt("chub.ai character URL or path?")?.trim();
    if (!url) return;
    setError(null);
    try {
      await api.setCharacterChubSource(wid, detail.meta.id, url);
      await select(detail.meta.id);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function unlinkChub() {
    if (!detail) return;
    await api.clearCharacterChubSource(wid, detail.meta.id);
    await select(detail.meta.id);
  }
```

In the edit-mode JSX, the `.picker` div (after Task 7) ends with and is immediately followed by `.avatar-block`:

```tsx
            <button className="subtle" onClick={downloadVersionFromChub}>Download version from chub.ai</button>
            {importMsg && <span className="field-hint">{importMsg}</span>}
          </div>

          <div className="avatar-block">
```

Insert a new block between them:

```tsx
            <button className="subtle" onClick={downloadVersionFromChub}>Download version from chub.ai</button>
            {importMsg && <span className="field-hint">{importMsg}</span>}
          </div>

          <div className="chub-source-block">
            {detail.meta.chub_source ? (
              <>
                <span className="field-hint">Linked to chub.ai: {detail.meta.chub_source}</span>
                <button className="subtle" type="button" onClick={unlinkChub}>Unlink</button>
              </>
            ) : (
              <button className="subtle" type="button" onClick={linkChub}>Link to chub.ai</button>
            )}
          </div>

          <div className="avatar-block">
```

- [ ] **Step 4: Run the tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: PASS — full file green

- [ ] **Step 5: Run the full frontend suite and type check**

Run (from `frontend/`): `npx vitest run && npx tsc -b`
Expected: PASS, no type errors

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat: manual chub.ai link/unlink for already-imported characters"
```

---

## Final Verification

- [ ] Run the full backend suite: `backend/.venv/Scripts/python.exe -m pytest backend -q` — expect all green.
- [ ] Run the full frontend suite from `frontend/`: `npx vitest run` — expect all green.
- [ ] Run the frontend type check from `frontend/`: `npx tsc -b` — expect no errors.
- [ ] Manually smoke-test against a real chub.ai URL (e.g. the two example cards used to verify the design) by running the app and using all three new buttons, since none of the automated tests make a real network call to chub.ai — see the spec's "fragility risk" note.
