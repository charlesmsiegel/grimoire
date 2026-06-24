# Character Card Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a general per-version character-image store (avatar as one named image), finish the card editor (creator / creator_notes / free-form tags / a real alternate-greetings editor), and a one-click import of a card's embedded `character_book` into world lore.

**Architecture:** A new root-based `store/assets.py` holds images at `characters/<cid>/assets/<vid>/<name>.<ext>`; the avatar is the image named `avatar`. `characters.import_card` populates the avatar from a PNG upload or a best-effort URL download (`httpx`). Image-generic FastAPI routes (world + campaign) serve/edit images. `lorebook.from_character_book` + one route reuse the existing lorebook commit path. The frontend wires avatar display (world=default version, campaign=locked version, editor=selected version), the new fields, a repeatable greetings list, and the import-book button.

**Tech Stack:** Python 3 / FastAPI / pytest (backend); React + TypeScript / Vitest + Testing Library (frontend). `httpx>=0.27` is already a dependency.

## Global Constraints

- **No new dependencies.** Use stdlib + `httpx` (already in `backend/pyproject.toml`).
- **Suite stays green after every task.** Backend was green before this work; keep it so.
- **Backend style:** `from __future__ import annotations`; modules small and single-responsibility; path ids validated against `""`, `.`, `..`, and `/`/`\` (mirror `characters.py._safe`).
- **No timestamps in card/image content** — images are never hashed into the card; `card_hash` must stay the `<vid>.json` text only.
- **Frontend:** components reference **theme tokens only** — no hardcoded colors or fonts. Match existing `CharacterEditor.tsx` idioms (`Field`, `api`, plain `fetch` via `api/client.ts`).
- **Card `tags` are free-form** discovery labels — never wired to the world gating vocabulary (`tags.md`).
- **Image extension allowlist:** `png, jpg, jpeg, gif, webp`.

---

### Task 1: `store/assets.py` — per-version image store

**Files:**
- Create: `backend/src/grimoire/store/assets.py`
- Test: `backend/tests/test_assets_store.py`

**Interfaces:**
- Consumes: `grimoire.store.paths` (nothing else).
- Produces:
  - `AVATAR = "avatar"`
  - `list_images(root: Path, cid: str, vid: str) -> list[dict]` → `[{"name","ext"}, …]` sorted
  - `image_path(root, cid, vid, name) -> Path | None`
  - `put_image(root, cid, vid, name, data: bytes, ext: str) -> str` (returns stored ext; raises `ValueError` on unsafe id / unsupported ext; replaces any prior-ext file of the same name)
  - `delete_image(root, cid, vid, name) -> None` (no-op if absent)

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_assets_store.py
import pytest

from grimoire.store import assets


def test_put_list_get_round_trip(tmp_path):
    assert assets.list_images(tmp_path, "sera", "default") == []
    ext = assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"\x89PNG", "png")
    assert ext == "png"
    assert assets.list_images(tmp_path, "sera", "default") == [{"name": "avatar", "ext": "png"}]
    p = assets.image_path(tmp_path, "sera", "default", "avatar")
    assert p is not None and p.read_bytes() == b"\x89PNG"


def test_replace_with_different_ext_leaves_one_file(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"b", "jpg")
    imgs = assets.list_images(tmp_path, "sera", "default")
    assert imgs == [{"name": "avatar", "ext": "jpg"}]  # exactly one, new ext
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"b"


def test_delete_and_absent(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.delete_image(tmp_path, "sera", "default", assets.AVATAR)
    assert assets.image_path(tmp_path, "sera", "default", "avatar") is None
    assets.delete_image(tmp_path, "sera", "default", "ghost")  # no error


def test_unsafe_and_unsupported_rejected(tmp_path):
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "../x", b"a", "png")
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "a.b", b"a", "png")  # dot in name
    with pytest.raises(ValueError):
        assets.put_image(tmp_path, "sera", "default", "avatar", b"a", "svg")  # not allowlisted
    assert assets.image_path(tmp_path, "..", "default", "avatar") is None  # unsafe cid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_assets_store.py -v`
Expected: FAIL — `ModuleNotFoundError: grimoire.store.assets`

- [ ] **Step 3: Write the module**

```python
# backend/src/grimoire/store/assets.py
"""Per-version character image store: characters/<cid>/assets/<vid>/<name>.<ext>.

The avatar is the image named AVATAR. Other image kinds (emotions, backgrounds,
…) drop into the same per-version folder with no schema change. Images are never
hashed into the card, so character sync is untouched by image edits.
"""

from __future__ import annotations

from pathlib import Path

AVATAR = "avatar"
_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}


def _safe(part: str) -> bool:
    return part not in ("", ".", "..") and "/" not in part and "\\" not in part


def _safe_name(name: str) -> bool:
    return _safe(name) and "." not in name


def _norm_ext(ext: str) -> str:
    ext = ext.lstrip(".").lower()
    return ext if ext in _EXTS else ""


def _dir(root: Path, cid: str, vid: str) -> Path:
    return root / "characters" / cid / "assets" / vid


def image_path(root: Path, cid: str, vid: str, name: str) -> Path | None:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        return None
    d = _dir(root, cid, vid)
    if not d.exists():
        return None
    matches = sorted(d.glob(f"{name}.*"))
    return matches[0] if matches else None


def list_images(root: Path, cid: str, vid: str) -> list[dict]:
    if not (_safe(cid) and _safe(vid)):
        return []
    d = _dir(root, cid, vid)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix:
            out.append({"name": p.stem, "ext": p.suffix.lstrip(".").lower()})
    return out


def put_image(root: Path, cid: str, vid: str, name: str, data: bytes, ext: str) -> str:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        raise ValueError("unsafe image id")
    ext = _norm_ext(ext)
    if not ext:
        raise ValueError("unsupported image type")
    d = _dir(root, cid, vid)
    d.mkdir(parents=True, exist_ok=True)
    for p in d.glob(f"{name}.*"):  # drop any prior-ext file of this name
        p.unlink()
    (d / f"{name}.{ext}").write_bytes(data)
    return ext


def delete_image(root: Path, cid: str, vid: str, name: str) -> None:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        return
    d = _dir(root, cid, vid)
    if d.exists():
        for p in d.glob(f"{name}.*"):
            p.unlink()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && python -m pytest tests/test_assets_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/assets.py backend/tests/test_assets_store.py
git commit -m "feat: per-version character image store (assets.py)"
```

---

### Task 2: Register `assets` + expose `images`/`has_avatar` on the card store

**Files:**
- Modify: `backend/src/grimoire/store/__init__.py` (add `assets` to the import tuple and `__all__`)
- Modify: `backend/src/grimoire/store/characters.py` (top import + `read_character` + `list_characters`)
- Test: `backend/tests/test_characters_store.py` (append)

**Interfaces:**
- Consumes: `assets.list_images`, `assets.image_path`, `assets.AVATAR` (Task 1).
- Produces:
  - `read_character` versions each gain `"images": list[str]`
  - `list_characters` items each gain `"has_avatar": bool` (default version)

- [ ] **Step 1: Write the failing test (append to `test_characters_store.py`)**

```python
def test_read_exposes_images_and_list_has_avatar(tmp_path):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Seraphine")
    # no images yet
    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == []
    assert ch.list_characters(tmp_path)[0]["has_avatar"] is False
    # add an avatar to the default version
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"img", "png")
    assert ch.read_character(tmp_path, cid)["versions"][0]["images"] == ["avatar"]
    assert ch.list_characters(tmp_path)[0]["has_avatar"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && python -m pytest tests/test_characters_store.py::test_read_exposes_images_and_list_has_avatar -v`
Expected: FAIL — `KeyError: 'images'`

- [ ] **Step 3: Register the module in `store/__init__.py`**

Change the import tuple to include `assets` (alphabetical), and add it to `__all__`:

```python
from . import (
    appearances, assets, campaigns, cards, characters, context, entities,
    greetings, lorebook, pcs, playing, scenes, sync, tags, worlds,
)
```

Add `"assets",` to the `__all__` list (next to `"cards",`).

- [ ] **Step 4: Wire `characters.py`**

At the top of `backend/src/grimoire/store/characters.py`, add to the existing imports:

```python
from . import assets
```

In `read_character`, the version-append loop becomes:

```python
    for vid in _version_ids(root, cid):
        card = read_card(root, cid, vid)
        versions.append({
            "id": vid,
            "name": card["data"].get("name", vid),
            "card": card,
            "images": [i["name"] for i in assets.list_images(root, cid, vid)],
        })
```

In `list_characters`, the `out.append({...})` gains `has_avatar`:

```python
            default = meta.get("default_version", "")
            out.append({
                "id": cid,
                "name": meta.get("name", cid),
                "default_version": default,
                "has_avatar": assets.image_path(root, cid, default, assets.AVATAR) is not None,
                "versions": [{"id": v, "name": read_card(root, cid, v)["data"].get("name", v)}
                             for v in _version_ids(root, cid)],
            })
```

- [ ] **Step 5: Run the characters + assets suites**

Run: `cd backend && python -m pytest tests/test_characters_store.py tests/test_assets_store.py -v`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/__init__.py backend/src/grimoire/store/characters.py backend/tests/test_characters_store.py
git commit -m "feat: expose character images + has_avatar on the card store"
```

---

### Task 3: Avatar population in `import_card` (PNG bytes + best-effort URL download)

**Files:**
- Modify: `backend/src/grimoire/store/characters.py` (helpers + `import_card`)
- Test: `backend/tests/test_characters_store.py` (append)

**Interfaces:**
- Consumes: `assets.put_image` (Task 1), `httpx`.
- Produces:
  - `_avatar_url(card: dict) -> str | None`
  - `_http_get_bytes(url: str) -> tuple[bytes, str | None]` (returns `(content, content_type)`; the seam tests monkeypatch)
  - `_download_avatar(card: dict) -> tuple[bytes, str] | None` (best-effort; never raises)
  - `import_card(...)` unchanged signature, now stores the avatar.

- [ ] **Step 1: Write the failing tests (append to `test_characters_store.py`)**

```python
def test_png_import_saves_avatar(tmp_path):
    from grimoire.store import assets
    png = (b"\x89PNG\r\n\x1a\n" + b"x" * 16)  # only the bytes matter here; loader handled separately
    # build a real importable PNG card via cards.dumps so loads() succeeds
    from grimoire.store import cards
    blob = cards.dumps(ch.blank_card("Imp"), "png")
    cid, vid = ch.import_card(tmp_path, blob, "png")
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is not None
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR).read_bytes() == blob


def test_json_import_downloads_avatar_url(tmp_path, monkeypatch):
    from grimoire.store import assets
    card = ch.blank_card("Imp")
    card["data"]["assets"] = [{"type": "icon", "uri": "https://x/pic.png", "name": "main", "ext": "png"}]
    import json as _json
    monkeypatch.setattr(ch, "_http_get_bytes", lambda url: (b"DOWNLOADED", "image/png"))
    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")
    p = assets.image_path(tmp_path, cid, vid, assets.AVATAR)
    assert p is not None and p.read_bytes() == b"DOWNLOADED" and p.suffix == ".png"


def test_json_import_download_failure_is_swallowed(tmp_path, monkeypatch):
    from grimoire.store import assets
    card = ch.blank_card("Imp")
    card["data"]["assets"] = [{"type": "icon", "uri": "https://x/pic.png"}]
    import json as _json
    def boom(url): raise RuntimeError("network down")
    monkeypatch.setattr(ch, "_http_get_bytes", boom)
    cid, vid = ch.import_card(tmp_path, _json.dumps(card).encode(), "json")  # must not raise
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is None


def test_json_import_no_url_makes_no_call(tmp_path, monkeypatch):
    import json as _json
    def boom(url): raise AssertionError("should not be called")
    monkeypatch.setattr(ch, "_http_get_bytes", boom)
    cid, vid = ch.import_card(tmp_path, _json.dumps(ch.blank_card("Imp")).encode(), "json")
    from grimoire.store import assets
    assert assets.image_path(tmp_path, cid, vid, assets.AVATAR) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_characters_store.py -k "avatar or download or no_url" -v`
Expected: FAIL — `AttributeError: module 'grimoire.store.characters' has no attribute '_http_get_bytes'` / avatar absent after PNG import

- [ ] **Step 3: Add helpers + extend `import_card`**

At the top of `characters.py` add:

```python
import httpx
```

Add the helpers (near the bottom, above `import_card`):

```python
_AVATAR_MAX_BYTES = 8 * 1024 * 1024
_CT_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
           "image/gif": "gif", "image/webp": "webp"}


def _avatar_url(card: dict) -> str | None:
    data = card.get("data", {})
    for a in data.get("assets") or []:
        if isinstance(a, dict) and a.get("type") in ("icon", "avatar"):
            uri = a.get("uri", "")
            if isinstance(uri, str) and uri.startswith(("http://", "https://")):
                return uri
    av = data.get("avatar")
    return av if isinstance(av, str) and av.startswith(("http://", "https://")) else None


def _http_get_bytes(url: str) -> tuple[bytes, str | None]:
    r = httpx.get(url, timeout=10.0, follow_redirects=True)
    r.raise_for_status()
    return r.content, r.headers.get("content-type")


def _download_avatar(card: dict) -> tuple[bytes, str] | None:
    url = _avatar_url(card)
    if not url:
        return None
    try:
        content, ctype = _http_get_bytes(url)
    except Exception:  # noqa: BLE001 — best-effort; import never fails on download
        return None
    if not content or len(content) > _AVATAR_MAX_BYTES:
        return None
    ct = (ctype or "").split(";")[0].strip().lower()
    if ct and not ct.startswith("image/"):
        return None
    ext = _CT_EXT.get(ct) or url.rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "gif", "webp"):
        ext = "png"
    return content, ext
```

Replace `import_card` with:

```python
def import_card(root: Path, data: bytes, fmt: str, into_cid: str | None = None,
                name: str | None = None) -> tuple[str, str]:
    from . import cards
    card = cards.loads(data, fmt)  # raises cards.CardParseError on bad input
    cname = name or card["data"].get("name", "Imported")
    if into_cid is None:
        cid, vid = create_character(root, cname, "default", card)
    else:
        cid = into_cid
        vid = create_version(root, into_cid, card.get("data", {}).get("character_version") or cname, card)
    if fmt == "png":
        assets.put_image(root, cid, vid, assets.AVATAR, data, "png")  # the PNG is the avatar
    else:
        dl = _download_avatar(card)
        if dl:
            assets.put_image(root, cid, vid, assets.AVATAR, dl[0], dl[1])
    return cid, vid
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_characters_store.py tests/test_cards.py -v`
Expected: PASS (existing + new). `cards.dumps(..., "png")` produces a parseable PNG card so `test_png_import_saves_avatar` round-trips.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/characters.py backend/tests/test_characters_store.py
git commit -m "feat: populate avatar on import (PNG bytes + best-effort URL download)"
```

---

### Task 4: Image routes (world list/get/put/delete + campaign get)

**Files:**
- Modify: `backend/src/grimoire/routes.py` (add routes + two small helpers near the character routes, before line ~453 `# ---- world greetings`)
- Test: `backend/tests/test_routes.py` (append)

**Interfaces:**
- Consumes: `store.assets`, `_world_root_or_404`, `_campaign_root_or_404`, `Response`, `UploadFile`, `File`.
- Produces routes:
  - `GET /worlds/{wid}/characters/{cid}/versions/{vid}/images` → `[{name,ext}]`
  - `GET /worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}` → bytes (404)
  - `PUT /worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}` (multipart `file`) → `{name,ext}` (400 on bad type)
  - `DELETE /worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}` → `{ok:true}`
  - `GET /campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}` → bytes (404)

- [ ] **Step 1: Write the failing test (append to `test_routes.py`)**

```python
def test_character_image_routes(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images"
    # absent
    assert client.get(base).json() == []
    assert client.get(f"{base}/avatar").status_code == 404
    # upload
    files = {"file": ("a.png", b"\x89PNGdata", "image/png")}
    r = client.put(f"{base}/avatar", files=files)
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    assert client.get(base).json() == [{"name": "avatar", "ext": "png"}]
    got = client.get(f"{base}/avatar")
    assert got.status_code == 200 and got.content == b"\x89PNGdata" and got.headers["content-type"].startswith("image/png")
    # bad type -> 400
    bad = client.put(f"{base}/avatar", files={"file": ("a.svg", b"<svg/>", "image/svg+xml")})
    assert bad.status_code == 400
    # delete
    assert client.delete(f"{base}/avatar").status_code == 200
    assert client.get(f"{base}/avatar").status_code == 404


def test_campaign_image_route_serves_copied_avatar(client):
    wid, cid = _campaign(client)
    # create + cast a character so the campaign holds a copy with assets
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    client.put(f"/api/worlds/{wid}/characters/{chid}/versions/default/images/avatar",
               files={"file": ("a.png", b"PNGBYTES", "image/png")})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": chid, "role": "npc"})
    got = client.get(f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/avatar")
    assert got.status_code == 200 and got.content == b"PNGBYTES"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd backend && python -m pytest tests/test_routes.py -k "image" -v`
Expected: FAIL — 404/405 (routes not defined)

- [ ] **Step 3: Add the helper + routes**

In `routes.py`, just above the `# ---- world greetings` comment (~line 453), add:

```python
_IMAGE_MEDIA = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "gif": "image/gif", "webp": "image/webp"}


def _serve_image(root, cid: str, vid: str, name: str):
    p = store.assets.image_path(root, cid, vid, name)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    ext = p.suffix.lstrip(".").lower()
    return Response(content=p.read_bytes(), media_type=_IMAGE_MEDIA.get(ext, "application/octet-stream"))


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images")
def list_world_images(wid: str, cid: str, vid: str):
    return store.assets.list_images(_world_root_or_404(wid), cid, vid)


@router.get("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def get_world_image(wid: str, cid: str, vid: str, name: str):
    return _serve_image(_world_root_or_404(wid), cid, vid, name)


@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
async def put_world_image(wid: str, cid: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _world_root_or_404(wid)
    data = await file.read()
    fn = file.filename or ""
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    try:
        stored = store.assets.put_image(root, cid, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}")
def delete_world_image(wid: str, cid: str, vid: str, name: str):
    store.assets.delete_image(_world_root_or_404(wid), cid, vid, name)
    return {"ok": True}
```

For the campaign read route, add it next to the campaign cast routes (just after the `GET /campaigns/{cid}/appearances` route, ~line 815):

```python
@router.get("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def get_campaign_image(cid: str, char: str, vid: str, name: str):
    return _serve_image(_campaign_root_or_404(cid), char, vid, name)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd backend && python -m pytest tests/test_routes.py -k "image" -v`
Expected: PASS (2 tests). If the scene-create or cast payload shape differs, align with the existing cast tests in `test_routes.py` (search `scenes` / `cast`).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: character image routes (world CRUD + campaign read)"
```

---

### Task 5: Embedded `character_book` → world lore import

**Files:**
- Modify: `backend/src/grimoire/store/lorebook.py` (add `from_character_book`)
- Modify: `backend/src/grimoire/routes.py` (add one route near the character routes)
- Test: `backend/tests/test_lorebook_store.py` (append) and `backend/tests/test_routes.py` (append)

**Interfaces:**
- Consumes: `lorebook._normalize`, `lorebook.commit`, `store.characters.read_card`.
- Produces:
  - `lorebook.from_character_book(book) -> list[dict]`
  - `POST /worlds/{wid}/characters/{cid}/versions/{vid}/lorebook/import` → `{created: [{kind,id}]}`

- [ ] **Step 1: Write the failing tests**

Append to `test_lorebook_store.py`:

```python
def test_from_character_book_normalizes():
    from grimoire.store import lorebook
    book = {"entries": [
        {"keys": ["pact"], "content": "the salt pact", "name": "Pact", "enabled": True},
        {"keys": ["off"], "content": "skip me", "enabled": False},
    ]}
    out = lorebook.from_character_book(book)
    assert out == [{"name": "Pact", "keys": ["pact"], "body": "the salt pact", "category": "lore"}]
    assert lorebook.from_character_book(None) == []
```

Append to `test_routes.py`:

```python
def test_character_book_import_route(client):
    wid = _world(client)
    card = {"spec": "chara_card_v3", "spec_version": "3.0", "data": {
        "name": "Sera",
        "character_book": {"entries": [{"keys": ["pact"], "content": "the salt pact", "name": "Pact"}]},
        "extensions": {},
    }}
    cid = client.post(f"/api/worlds/{wid}/characters",
                      json={"name": "Sera", "card": card}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/lorebook/import")
    assert r.status_code == 200
    created = r.json()["created"]
    assert len(created) == 1 and created[0]["kind"] == "lore"
    # the entry now exists as a world lore entity
    assert any(e["id"] == created[0]["id"] for e in client.get(f"/api/worlds/{wid}/lore").json())


def test_character_book_import_empty(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/default/lorebook/import")
    assert r.status_code == 200 and r.json() == {"created": []}
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd backend && python -m pytest tests/test_lorebook_store.py::test_from_character_book_normalizes tests/test_routes.py -k "character_book" -v`
Expected: FAIL — `AttributeError: from_character_book` / 404 on the route

- [ ] **Step 3: Add `from_character_book`**

Append to `lorebook.py` (after `parse`):

```python
def from_character_book(book) -> list[dict]:
    """Normalize a card's embedded character_book into commit-ready entries."""
    return _normalize(book or {})
```

- [ ] **Step 4: Add the route**

In `routes.py`, just after the character export route (~line 451), add:

```python
@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/lorebook/import")
def post_character_lorebook_import(wid: str, cid: str, vid: str):
    root = _world_root_or_404(wid)
    try:
        card = store.characters.read_card(root, cid, vid)
    except store.characters.CharacterNotFound:
        raise HTTPException(status_code=404, detail="character not found")
    except store.characters.VersionNotFound:
        raise HTTPException(status_code=404, detail="version not found")
    book = card.get("data", {}).get("character_book") or {}
    created = store.lorebook.commit(root, store.lorebook.from_character_book(book))
    return {"created": created}
```

- [ ] **Step 5: Run to verify they pass + full backend suite**

Run: `cd backend && python -m pytest -q`
Expected: PASS (entire suite green)

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/lorebook.py backend/src/grimoire/routes.py backend/tests/test_lorebook_store.py backend/tests/test_routes.py
git commit -m "feat: import a character's embedded character_book into world lore"
```

---

### Task 6: API client — types + image/lorebook functions

**Files:**
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Consumes: backend routes from Tasks 4–5; existing `request`, `requestForm`.
- Produces (new `api.*` members + type fields):
  - `CardData` gains `creator?: string; creator_notes?: string; tags?: string[]; character_book?: { entries?: unknown[] }`
  - version objects in `CharacterDetail` gain `images?: string[]`; `CharacterSummary` gains `has_avatar?: boolean`
  - `api.imageUrl(wid, cid, vid, name) -> string`
  - `api.campaignImageUrl(cid, char, vid, name) -> string`
  - `api.putImage(wid, cid, vid, name, file) -> Promise<{name,ext}>`
  - `api.deleteImage(wid, cid, vid, name) -> Promise<{ok:boolean}>`
  - `api.importCharacterBook(wid, cid, vid) -> Promise<{created:{kind:string;id:string}[]}>`

- [ ] **Step 1: Extend the types**

In `CardData` (lines 57–68) add before `[k: string]: unknown;`:

```typescript
  creator?: string;
  creator_notes?: string;
  tags?: string[];
  character_book?: { entries?: unknown[] };
```

Update `CharacterSummary` and `CharacterDetail`:

```typescript
export type CharacterSummary = { id: string; name: string; default_version: string; has_avatar?: boolean; versions: VersionRef[] };
export type CharacterDetail = {
  meta: { id: string; name: string; default_version: string };
  versions: { id: string; name: string; card: Card; images?: string[] }[];
};
```

- [ ] **Step 2: Add the API functions**

Right after `importCharacter` (line 224) inside the `api` object, add:

```typescript
  imageUrl: (wid: string, cid: string, vid: string, name: string) =>
    `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}`,
  campaignImageUrl: (cid: string, char: string, vid: string, name: string) =>
    `/api/campaigns/${cid}/characters/${char}/versions/${vid}/images/${name}`,
  putImage: (wid: string, cid: string, vid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ name: string; ext: string }>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}`, form, "PUT");
  },
  deleteImage: (wid: string, cid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}`),
  importCharacterBook: (wid: string, cid: string, vid: string) =>
    request<{ created: { kind: string; id: string }[] }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/lorebook/import`),
```

- [ ] **Step 3: Confirm `requestForm` supports a method argument**

Read `frontend/src/api/client.ts` near the `requestForm` definition. It is currently used as `requestForm(url, form)` (POST). If its signature is `requestForm<T>(url, form)`, extend it to accept an optional method:

```typescript
function requestForm<T>(url: string, form: FormData, method = "POST"): Promise<T> {
  // change the fetch call to use `method` instead of a hardcoded "POST"
}
```

Make the matching edit in the `fetch(..., { method: "POST", ... })` call → `{ method, ... }`. (If `requestForm` already takes a method, skip this step.)

- [ ] **Step 4: Typecheck**

Run: `cd frontend && npx tsc --noEmit`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat: api client image + character_book functions and card types"
```

---

### Task 7: CharacterEditor — creator / creator_notes / tags + repeatable greetings

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: existing `api.updateVersion`, `Card`, `Field`.
- Produces: editor state `greetings: string[]` (replaces the `alt` string), plus `creator`/`creator_notes`/`tags` fields driven by `setField`. `buildCard` writes `alternate_greetings` from the array and `tags` from the parsed input.

- [ ] **Step 1: Update the existing greetings test + add a field test**

In `CharacterEditor.test.tsx`, the mock object (lines 4–10) is unchanged. Replace the greetings test (lines 39–52) with the repeatable-list version and add a tags/creator test:

```typescript
test("editing description + alternate greetings (repeatable) saves a rebuilt card", async () => {
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByLabelText("Description");
  fireEvent.change(screen.getByLabelText("Description"), { target: { value: "cold keeper" } });
  // the seed card has one greeting "hi"; add a second and edit both
  fireEvent.click(screen.getByRole("button", { name: /add greeting/i }));
  const areas = screen.getAllByLabelText(/greeting \d+/i);
  fireEvent.change(areas[0], { target: { value: "line one\nstill one" } });
  fireEvent.change(areas[1], { target: { value: "two" } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => {
    const card = (api.updateVersion as any).mock.calls[0][3];
    expect(card.data.description).toBe("cold keeper");
    expect(card.data.alternate_greetings).toEqual(["line one\nstill one", "two"]);
  });
});

test("editing creator and tags saves them", async () => {
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByLabelText("Creator");
  fireEvent.change(screen.getByLabelText("Creator"), { target: { value: "anon" } });
  fireEvent.change(screen.getByLabelText("Tags"), { target: { value: "fantasy, oc " } });
  fireEvent.click(screen.getByRole("button", { name: /save version/i }));
  await waitFor(() => {
    const card = (api.updateVersion as any).mock.calls[0][3];
    expect(card.data.creator).toBe("anon");
    expect(card.data.tags).toEqual(["fantasy", "oc"]);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/CharacterEditor.test.tsx`
Expected: FAIL — no "Add greeting" button / no "Creator" label.

- [ ] **Step 3: Replace greetings state + add fields in `CharacterEditor.tsx`**

Replace the `alt` state and its uses. Change:

```typescript
  const [alt, setAlt] = useState("");
```
to
```typescript
  const [greetings, setGreetings] = useState<string[]>([]);
```

In `loadVersion`, replace the `setAlt(...)` line with:

```typescript
    setGreetings(v.card.data.alternate_greetings ?? []);
```

Replace `buildCard`:

```typescript
  function buildCard(): Card {
    return { ...card!, data: { ...card!.data, alternate_greetings: greetings.filter((g) => g.trim() !== "") } };
  }
```

Add `creator` to the line fields and `creator_notes` to `TEXT_FIELDS`. Extend the `TEXT_FIELDS` array (line 5) with:

```typescript
  { key: "creator_notes", label: "Creator notes", area: true },
```

After the Name `<Field>` (line 146–148), add the Creator + Tags fields:

```typescript
            <Field label="Creator">
              <input type="text" value={card.data.creator ?? ""} onChange={(e) => setField("creator", e.target.value)} />
            </Field>
            <Field label="Tags" hint="comma-separated">
              <input
                type="text"
                value={(card.data.tags ?? []).join(", ")}
                onChange={(e) => setField("tags", e.target.value.split(",").map((t) => t.trim()).filter(Boolean) as any)}
              />
            </Field>
```

`setField` currently types `value: string`; the tags input passes an array. Loosen the signature:

```typescript
  function setField(key: string, value: unknown) {
    if (!card) return;
    setCard({ ...card, data: { ...card.data, [key]: value } });
  }
```

Replace the single "Alternate greetings" `<Field>` (lines 158–160) with the repeatable list:

```typescript
            <Field label="Alternate greetings" hint="each greeting may span multiple lines">
              <div className="greeting-list">
                {greetings.map((g, i) => (
                  <div className="greeting-row" key={i}>
                    <textarea
                      aria-label={`Greeting ${i + 1}`}
                      value={g}
                      rows={3}
                      onChange={(e) => setGreetings(greetings.map((x, j) => (j === i ? e.target.value : x)))}
                    />
                    <button className="subtle" type="button"
                            onClick={() => setGreetings(greetings.filter((_, j) => j !== i))}>
                      Remove
                    </button>
                  </div>
                ))}
                <button className="subtle" type="button" onClick={() => setGreetings([...greetings, ""])}>
                  + Add greeting
                </button>
              </div>
            </Field>
```

- [ ] **Step 4: Run to verify pass**

Run: `cd frontend && npx vitest run src/components/CharacterEditor.test.tsx`
Expected: PASS (including the unchanged create/import tests).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat: editor creator/creator_notes/tags fields + repeatable greetings"
```

---

### Task 8: CharacterEditor — avatar block + world roster thumbnails

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `api.imageUrl`, `api.putImage`, `api.deleteImage`, the version's `images` array, `CharacterSummary.has_avatar`.
- Produces: an avatar `<img>`/placeholder + upload/remove controls for the selected version; a thumbnail next to each character in the list.

- [ ] **Step 1: Add the mock + test**

In `CharacterEditor.test.tsx`, add `putImage`, `deleteImage`, and `imageUrl` to the `vi.mock` api object (lines 4–10):

```typescript
    putImage: vi.fn(), deleteImage: vi.fn(),
    imageUrl: (w: string, c: string, v: string, n: string) => `/img/${w}/${c}/${v}/${n}`,
```

Give `DETAIL`'s version an `images` array and the list mock a `has_avatar`:

```typescript
  versions: [{ id: "default", name: "default", card: CARD, images: ["avatar"] }],
```
and in `beforeEach` set `listCharacters` to return `has_avatar: true`:

```typescript
  (api.listCharacters as any).mockResolvedValue([{ id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: true, versions: [] }]);
  (api.putImage as any).mockResolvedValue({ name: "avatar", ext: "png" });
  (api.deleteImage as any).mockResolvedValue({ ok: true });
```

Add the test:

```typescript
test("uploads an avatar for the selected version", async () => {
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByLabelText("Description");
  const input = screen.getByLabelText("Upload avatar");
  fireEvent.change(input, { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
  await waitFor(() => expect(api.putImage).toHaveBeenCalledWith("w", "seraphine", "default", "avatar", expect.any(File)));
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/CharacterEditor.test.tsx`
Expected: FAIL — no "Upload avatar" control.

- [ ] **Step 3: Add an avatar ref + handlers**

Add near the other refs/state:

```typescript
  const avatarRef = useRef<HTMLInputElement>(null);
  const [avatarBust, setAvatarBust] = useState(0);

  const hasAvatar = (detail && card)
    ? (detail.versions.find((v) => v.id === vid)?.images ?? []).includes("avatar")
    : false;

  async function onAvatar(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    try {
      await api.putImage(wid, detail.meta.id, vid, "avatar", file);
      await select(detail.meta.id);
      setAvatarBust((n) => n + 1);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
  }

  async function removeAvatar() {
    if (!detail) return;
    await api.deleteImage(wid, detail.meta.id, vid, "avatar");
    await select(detail.meta.id);
    setAvatarBust((n) => n + 1);
  }
```

Add the avatar block at the top of the `<div className="form">` (just under the `picker` div, before the Name field):

```typescript
            <div className="avatar-block">
              {hasAvatar ? (
                <img className="avatar" alt="avatar"
                     src={`${api.imageUrl(wid, detail.meta.id, vid, "avatar")}?v=${avatarBust}`} />
              ) : (
                <div className="avatar avatar-empty" aria-label="no avatar">no avatar</div>
              )}
              <div className="avatar-actions">
                <button className="subtle" type="button" onClick={() => avatarRef.current?.click()}>
                  {hasAvatar ? "Replace" : "Upload"}
                </button>
                {hasAvatar && <button className="subtle" type="button" onClick={removeAvatar}>Remove</button>}
                <input ref={avatarRef} type="file" accept="image/*" hidden
                       aria-label="Upload avatar" onChange={onAvatar} />
              </div>
            </div>
```

Add a thumbnail to each character button in the list (replace the list `<button>` body, lines 116–124):

```typescript
        {chars.map((c) => (
          <button
            key={c.id}
            className={"row" + (detail?.meta.id === c.id ? " active" : "")}
            onClick={() => select(c.id)}
          >
            {c.has_avatar
              ? <img className="row-avatar" alt="" src={api.imageUrl(wid, c.id, c.default_version, "avatar")} />
              : <span className="row-avatar row-avatar-empty" aria-hidden="true" />}
            {c.name}
          </button>
        ))}
```

- [ ] **Step 4: Add minimal styles**

Append to the project stylesheet (find it via the existing `.editor`/`.avatar`-free CSS — likely `frontend/src/index.css` or the file importing `.editor`). Add theme-token-based rules:

```css
.avatar-block { display: flex; gap: var(--space-2, 0.5rem); align-items: center; margin-bottom: var(--space-2, 0.5rem); }
.avatar { width: 96px; height: 96px; object-fit: cover; border-radius: var(--radius, 6px); border: 1px solid var(--border); }
.avatar-empty { display: flex; align-items: center; justify-content: center; color: var(--text-muted, var(--text)); font-size: 0.8em; background: var(--surface, transparent); }
.row-avatar { width: 20px; height: 20px; object-fit: cover; border-radius: 50%; margin-right: var(--space-1, 0.25rem); vertical-align: middle; }
.row-avatar-empty { display: inline-block; background: var(--border); }
.greeting-row { display: flex; gap: var(--space-1, 0.25rem); margin-bottom: var(--space-1, 0.25rem); }
.greeting-row textarea { flex: 1; }
```

(If the project defines different token names, match them — grep an existing component's CSS for the real variable names and reuse those.)

- [ ] **Step 5: Run to verify pass + typecheck**

Run: `cd frontend && npx vitest run src/components/CharacterEditor.test.tsx && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx frontend/src/index.css
git commit -m "feat: avatar block + roster thumbnails in the character editor"
```

---

### Task 9: CastPanel — campaign locked-version avatar

**Files:**
- Modify: `frontend/src/components/CastPanel.tsx`
- Test: `frontend/src/components/CastPanel.test.tsx`

**Interfaces:**
- Consumes: `api.listAppearances` (already exists, returns `RosterEntry[]` with `version`), `api.campaignImageUrl`.
- Produces: each character cast row shows the locked-version avatar (or a placeholder).

- [ ] **Step 1: Add the test**

Open `CastPanel.test.tsx`, ensure the mocked `api` includes `listAppearances` and `campaignImageUrl`:

```typescript
    listAppearances: vi.fn().mockResolvedValue([{ kind: "characters", id: "sera", version: "default", role: "npc", scenes: ["s1"] }]),
    campaignImageUrl: (c: string, ch: string, v: string, n: string) => `/cimg/${c}/${ch}/${v}/${n}`,
```

Add a test that a cast character row renders an avatar img with the locked version in its src (adapt to the existing test's render/setup for `getCast` returning `[{kind:"characters", id:"sera", role:"npc"}]`):

```typescript
test("character cast row shows the locked-version avatar", async () => {
  render(<CastPanel cid="c" sid="s1" sceneEmpty={false} keySet={false} onSeeded={() => {}} />);
  const img = await screen.findByAltText("sera avatar");
  expect(img.getAttribute("src")).toContain("/cimg/c/sera/default/avatar");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/CastPanel.test.tsx`
Expected: FAIL — no avatar img.

- [ ] **Step 3: Load the roster + render the thumbnail**

Add roster state and a load effect:

```typescript
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  useEffect(() => { api.listAppearances(cid).then(setRoster).catch(() => setRoster([])); }, [cid, sid]);
```

(Add `RosterEntry` to the import from `../api/client`, and `useEffect`/`useState` already imported.)

In the cast-row render (lines 113–118), prepend the avatar for characters:

```typescript
          {cast.map((a) => {
            const ver = a.kind === "characters"
              ? roster.find((r) => r.kind === "characters" && r.id === a.id)?.version
              : undefined;
            return (
              <div className="cast-row" key={`${a.kind}/${a.id}`}>
                {ver
                  ? <img className="row-avatar" alt={`${a.id} avatar`}
                         src={api.campaignImageUrl(cid, a.id, ver, "avatar")}
                         onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
                  : null}
                <span>{a.id}</span>
                <span className="role">{a.kind === "pcs" ? "PC" : "character"} · {a.role}</span>
              </div>
            );
          })}
```

The `onError` hides the thumbnail when a character has no avatar (the route 404s) — no extra round-trip needed.

- [ ] **Step 4: Run to verify pass + typecheck**

Run: `cd frontend && npx vitest run src/components/CastPanel.test.tsx && npx tsc --noEmit`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CastPanel.tsx frontend/src/components/CastPanel.test.tsx
git commit -m "feat: locked-version avatar in the campaign cast panel"
```

---

### Task 10: CharacterEditor — import embedded character_book button

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `api.importCharacterBook`, `card.data.character_book?.entries`.
- Produces: a button shown only when the selected version's card carries a non-empty `character_book`; on click it imports and reports the count.

- [ ] **Step 1: Add the mock + test**

Add `importCharacterBook: vi.fn()` to the `vi.mock` api object and in `beforeEach`:

```typescript
  (api.importCharacterBook as any).mockResolvedValue({ created: [{ kind: "lore", id: "pact" }] });
```

Add to `CARD.data` a book so the button appears:

```typescript
  data: { name: "Seraphine", description: "keeper", alternate_greetings: ["hi"], extensions: {},
          character_book: { entries: [{ keys: ["pact"], content: "x" }] } },
```

Add the test:

```typescript
test("imports an embedded character_book and shows the result", async () => {
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByLabelText("Description");
  fireEvent.click(screen.getByRole("button", { name: /import .* lore/i }));
  await waitFor(() => expect(api.importCharacterBook).toHaveBeenCalledWith("w", "seraphine", "default"));
  await screen.findByText(/imported 1/i);
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npx vitest run src/components/CharacterEditor.test.tsx`
Expected: FAIL — no import-lore button.

- [ ] **Step 3: Add state + handler + button**

Add state and handler:

```typescript
  const [bookMsg, setBookMsg] = useState<string | null>(null);

  const bookCount = (card?.data.character_book?.entries?.length ?? 0);

  async function importBook() {
    if (!detail) return;
    setBookMsg(null);
    try {
      const { created } = await api.importCharacterBook(wid, detail.meta.id, vid);
      setBookMsg(`Imported ${created.length} entr${created.length === 1 ? "y" : "ies"} to world lore`);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

Reset `bookMsg` inside `loadVersion` (so it clears on version switch): add `setBookMsg(null);` there.

Render the control just above `<div className="form-actions">`:

```typescript
            {bookCount > 0 && (
              <div className="book-import">
                <button className="subtle" type="button" onClick={importBook}>
                  Import {bookCount} embedded lore {bookCount === 1 ? "entry" : "entries"} to world
                </button>
                {bookMsg && <span className="field-hint">{bookMsg}</span>}
              </div>
            )}
```

- [ ] **Step 4: Run to verify pass + full frontend suite + typecheck**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: PASS (all frontend tests), no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat: import a character's embedded lorebook from the editor"
```

---

### Task 11: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Backend suite**

Run: `cd backend && python -m pytest -q`
Expected: all green.

- [ ] **Step 2: Frontend suite + typecheck**

Run: `cd frontend && npx vitest run && npx tsc --noEmit`
Expected: all green, no type errors.

- [ ] **Step 3: Manual smoke (optional but recommended)**

Start the app, open a world, import a PNG card → avatar appears; edit greetings/creator/tags → save → reload persists; a card with a `character_book` shows the import button → import → entries appear in world Lore; cast the character in a campaign scene → its avatar shows in the cast panel.

---

## Self-Review

**Spec coverage:**
- General per-version image store → Task 1 (`assets.py`), exposure Task 2. ✓
- Avatar sources: PNG import / URL download / manual upload → Task 3 (import) + Task 4 (`PUT`) + Task 8 (UI). ✓
- World=default / campaign=locked / editor=selected display → Task 8 (roster + editor) + Task 9 (cast). ✓
- No-avatar is fine → placeholder in Task 8, `onError` hide in Task 9, routes 404. ✓
- creator / creator_notes / free-form tags → Task 7. ✓
- Repeatable alternate-greetings editor → Task 7. ✓
- character_book → world lore (blind, reuse lorebook) → Task 5 (backend) + Task 10 (UI). ✓
- `appear()` carries assets unchanged → verified by Task 4's campaign-image test. ✓
- No new deps / suite green / theme tokens → Global Constraints + Task 11. ✓

**Placeholder scan:** none — every code step shows full content.

**Type consistency:** `put_image` returns `str` (ext) in Task 1, consumed by the route in Task 4 and reflected in `api.putImage`'s `{name,ext}` return in Task 6. `images: string[]` and `has_avatar: boolean` defined in Task 2, typed in Task 6, consumed in Task 8. `from_character_book` defined in Task 5, used by the route in the same task and `api.importCharacterBook` in Task 6/10. `imageUrl`/`campaignImageUrl`/`putImage`/`deleteImage`/`importCharacterBook` names match across Tasks 6, 8, 9, 10.

**Note for the implementer:** before editing `requestForm` (Task 6, Step 3), read its current signature — if it already accepts a method argument, skip that edit. Likewise confirm the cast/scene-create payload shapes in Task 4's campaign test against existing `test_routes.py` cast tests, and the real CSS token names in Task 8 against an existing styled component.
