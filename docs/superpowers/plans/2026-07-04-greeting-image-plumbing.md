# Greeting-Image Plumbing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give world greetings a served asset home (`<world>/greetings/<gid>/assets/default/`) and a store-layer localize pass that downloads image refs in a greeting body and rewrites them to local URLs.

**Architecture:** Two thin additions, no new storage code. (1) `store/localize.py` gains `localize_greeting(root, gid, wid)` — a plain (non-generator) function reusing the existing `find_refs`/`_store` machinery, storing into `assets.py`'s existing `base="greetings"` layout and persisting via `greetings.update_greeting`. (2) The two generic **GET** entity-image routes in `routes.py` accept `greetings` as a kind in addition to `ENTITY_KINDS`; PUT/DELETE/promote stay entity-only (greeting images are read-only over HTTP for now).

**Tech Stack:** FastAPI backend, pytest (`fastapi.testclient`), file store under `GRIMOIRE_HOME`.

**Spec:** `docs/superpowers/specs/2026-07-04-scenario-card-extraction-design.md` (Part 1 only — Parts 2/3 are supervised one-off extraction, not product code).

## Global Constraints

- Backend tests run with: `backend/.venv/Scripts/python.exe -m pytest backend -q` (store isolated via `GRIMOIRE_HOME`; the `client` fixture in `backend/tests/test_routes.py` already does this).
- Best-effort semantics: a failed/skipped image download must never raise out of `localize_greeting` (mirror `localize_card`).
- Serving URL shape: `/api/worlds/{wid}/greetings/{gid}/images/{name}` — this is what rewritten markdown must contain and what the widened GET route must serve.
- `localize_card` behavior must be completely unchanged (its `_store` calls keep `base="characters"` semantics by default).
- No frontend changes; run `npx vitest run` and `npx tsc -b` from `frontend/` once at the end as regression only.

---

### Task 1: `localize_greeting` in `store/localize.py`

**Files:**
- Modify: `backend/src/grimoire/store/localize.py` (imports at ~line 14, `_store` at ~line 135, new function at end of file)
- Test: `backend/tests/test_localize_store.py` (append)

**Interfaces:**
- Consumes: `greetings.read_greeting(root, gid) -> {"meta", "body"}`, `greetings.update_greeting(root, gid, *, body=...)`, `greetings.create_greeting(root, name, character, version, body)` (tests), `assets.put_image(..., base=)`, existing `find_refs` / `_fetch.decode_data_uri`.
- Produces: `localize_greeting(root, gid, wid, *, fetch=None, cap=10) -> dict` returning `{"total": int, "localized": int, "skipped": int, "failed": int, "capped": bool}`. Stored files are named `embed-<sha256(raw)[:12]>.<ext>` under `<root>/greetings/<gid>/assets/default/`. Task 2's route test and the Part-3 apply script rely on this exact signature and layout.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_localize_store.py` (module already imports `assets` and `localize`; add `greetings` to that import):

```python
from grimoire.store import assets, greetings, localize  # top of file: add greetings


def _greeting_with(tmp_path, body):
    gid = greetings.create_greeting(tmp_path, "Opener", "mira", "v1", body)
    return gid


def test_localize_greeting_rewrites_body_and_stores_assets(tmp_path):
    gid = _greeting_with(tmp_path, "look ![art](https://h/a.png) done")
    fetch = lambda url: (b"png-bytes", "png")
    summary = localize.localize_greeting(tmp_path, gid, "w1", fetch=fetch)
    assert summary == {"total": 1, "localized": 1, "skipped": 0,
                       "failed": 0, "capped": False}
    body = greetings.read_greeting(tmp_path, gid)["body"]
    m = _re.search(r"!\[art\]\((/api/worlds/w1/greetings/%s/images/(embed-\w{12}))\)" % gid, body)
    assert m, body
    name = m.group(2)
    p = assets.image_path(tmp_path, gid, "default", name, base="greetings")
    assert p is not None and p.read_bytes() == b"png-bytes"
    assert p.parent == tmp_path / "greetings" / gid / "assets" / "default"


def test_localize_greeting_data_uri_and_failed_fetch(tmp_path):
    # data-uri decodes and stores; the http ref fails; nothing raises
    body = ("pic ![d](data:image/png;base64,aGk=) and "
            "![b](https://h/broken.png) end")
    gid = _greeting_with(tmp_path, body)

    def boom(url):
        raise OSError("down")

    summary = localize.localize_greeting(tmp_path, gid, "w1", fetch=boom)
    assert summary["total"] == 2 and summary["localized"] == 1
    assert summary["failed"] == 1 and summary["skipped"] == 0
    new_body = greetings.read_greeting(tmp_path, gid)["body"]
    assert f"/api/worlds/w1/greetings/{gid}/images/embed-" in new_body
    assert "https://h/broken.png" in new_body  # failed ref left untouched


def test_localize_greeting_skips_local_refs_and_is_idempotent(tmp_path):
    gid = _greeting_with(tmp_path, "x ![a](https://h/a.png) y")
    fetch = lambda url: (b"raw", "png")
    localize.localize_greeting(tmp_path, gid, "w1", fetch=fetch)
    second = localize.localize_greeting(tmp_path, gid, "w1", fetch=fetch)
    assert second == {"total": 0, "localized": 0, "skipped": 0,
                      "failed": 0, "capped": False}


def test_localize_greeting_respects_cap_and_dedups(tmp_path):
    # same URL twice = one download; cap=1 still localizes both spans
    gid = _greeting_with(
        tmp_path, "![a](https://h/a.png) ![b](https://h/a.png) ![c](https://h/c.png)")
    calls = []

    def fetch(url):
        calls.append(url)
        return (b"raw-" + url.encode(), "png")

    summary = localize.localize_greeting(tmp_path, gid, "w1", fetch=fetch, cap=1)
    assert calls == ["https://h/a.png"]
    assert summary == {"total": 3, "localized": 2, "skipped": 1,
                       "failed": 0, "capped": True}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_localize_store.py -q -k localize_greeting`
Expected: 4 FAIL/ERROR with `AttributeError: module 'grimoire.store.localize' has no attribute 'localize_greeting'`

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/localize.py`:

Add to the imports block (after `from . import fetch as _fetch`):

```python
from . import greetings as _greetings
```

(`greetings.py` does not import `localize`, so no cycle.)

Give `_store` a `base` parameter (signature + the `put_image` call; body otherwise unchanged):

```python
def _store(root, cid, vid, got, base: str = "characters") -> str | None:
    """Store downloaded bytes under a content-hash name; None if the store
    rejects them (keeps localization best-effort even for an odd fetcher)."""
    raw, ext = got
    name = "embed-" + hashlib.sha256(raw).hexdigest()[:12]
    try:
        assets.put_image(root, cid, vid, name, raw, ext, base=base)
    except Exception:  # noqa: BLE001 — e.g. unsupported ext from a caller's fetch
        return None
    return name
```

Append at end of file:

```python
def localize_greeting(root, gid, wid, *, fetch=None, cap=10) -> dict:
    """Download every image referenced in a world greeting's body into the
    per-greeting asset store (<root>/greetings/<gid>/assets/default/) and
    rewrite the body to the local serving URLs. Best-effort per ref, like
    localize_card; persists via greetings.update_greeting only when at least
    one ref localized. Returns {"total","localized","skipped","failed","capped"}.
    """
    if fetch is None:
        fetch = _fetch.download_url
    text = _greetings.read_greeting(root, gid)["body"]
    refs = find_refs(text)
    localized = skipped = failed = downloads = 0
    capped = False
    seen: dict[str, str] = {}  # raw url/data-uri -> stored asset name
    items: list[tuple[Ref, str]] = []
    for ref in refs:
        name = None
        if ref.url in seen:
            name = seen[ref.url]
        elif ref.url.startswith("data:"):
            got = _fetch.decode_data_uri(ref.url)
            if got is None:
                skipped += 1
            else:
                name = _store(root, gid, "default", got, base="greetings")
                if name is None:
                    failed += 1  # bytes decoded but the store rejected them
        elif downloads >= cap:
            capped = True
            skipped += 1
        else:
            downloads += 1
            try:
                got = fetch(ref.url)
            except Exception:  # noqa: BLE001 — best-effort; a miss never breaks the greeting
                got = None
                failed += 1
            else:
                if got is None:
                    skipped += 1  # download returned None (non-image / blocked host)
            if got is not None:
                name = _store(root, gid, "default", got, base="greetings")
                if name is None:
                    failed += 1  # downloaded but the store rejected the bytes
        if name is not None:
            seen[ref.url] = name
            items.append((ref, name))
            localized += 1
    for ref, name in sorted(items, key=lambda it: it[0].span[0], reverse=True):
        local = f"/api/worlds/{wid}/greetings/{gid}/images/{name}"
        start, end = ref.span
        repl = f"![]({local})" if ref.as_markdown else local
        text = text[:start] + repl + text[end:]
    if items:
        _greetings.update_greeting(root, gid, body=text)
    return {"total": len(refs), "localized": localized, "skipped": skipped,
            "failed": failed, "capped": capped}
```

Note the dedup detail: a `seen` hit does **not** count as a download, so cap=1 with a repeated URL localizes both spans of that URL and skips only genuinely new URLs — exactly what `test_localize_greeting_respects_cap_and_dedups` asserts.

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_localize_store.py -q`
Expected: all PASS (new tests plus the existing `localize_card` suite — proves `_store`'s default `base` kept card behavior identical)

- [ ] **Step 5: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/localize.py backend/tests/test_localize_store.py
git commit -m "feat(store): localize_greeting — greeting-body images into per-greeting assets"
```

---

### Task 2: Serve greeting images through the generic GET entity-image routes

**Files:**
- Modify: `backend/src/grimoire/routes.py:959-999` (the entity-image helper block and the two world GET routes)
- Test: `backend/tests/test_routes.py` (append near `test_entity_images_unknown_kind_404`)

**Interfaces:**
- Consumes: Task 1's on-disk layout (`<world>/greetings/<gid>/assets/default/<name>.<ext>`); existing `_serve_image(root, cid, vid, name, base)` at `routes.py:724`; `store.assets.put_image` / `list_images`; the `client` fixture and `_world` helper already in `test_routes.py`.
- Produces: `GET /api/worlds/{wid}/greetings/{gid}/images` and `GET /api/worlds/{wid}/greetings/{gid}/images/{name}` serve stored greeting images. Write routes (PUT/DELETE/promote) still 404 for `greetings`. This is the URL `localize_greeting` writes into bodies.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_routes.py`:

```python
def test_greeting_images_served_readonly(client):
    wid = _world(client)
    gid = client.post(f"/api/worlds/{wid}/greetings",
                      json={"name": "Opener", "character": "mira", "version": "v1"}).json()["id"]
    # no PUT route for greeting images: store the asset directly
    root = store.worlds.world_root(wid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png",
                           base="greetings")

    base = f"/api/worlds/{wid}/greetings/{gid}/images"
    assert [i["name"] for i in client.get(base).json()] == ["embed-abc123def456"]
    r = client.get(f"{base}/embed-abc123def456")
    assert r.status_code == 200 and r.content == b"art"
    assert r.headers["content-type"] == "image/png"

    # write surface stays entity-only: greetings is not an accepted kind
    assert client.put(f"{base}/other",
                      files={"file": ("a.png", io.BytesIO(b"x"), "image/png")}).status_code == 404
    assert client.delete(f"{base}/embed-abc123def456").status_code == 404
    assert client.post(f"{base}/embed-abc123def456/promote").status_code == 404
    # and unknown kinds still 404 on GET
    assert client.get(f"/api/worlds/{wid}/potions/x/images").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_greeting_images_served_readonly -q`
Expected: FAIL — the list GET returns 404 (`unknown kind`), so the `[i["name"] ...]` line raises/asserts

- [ ] **Step 3: Implement**

In `backend/src/grimoire/routes.py`, in the entity-images block (~line 959):

Add below `_entity_kind_or_404`:

```python
_IMAGE_KINDS = store.entities.ENTITY_KINDS + ("greetings",)


def _image_kind_or_404(kind: str) -> None:
    # read side only: greeting images are stored by localize_greeting / scripts,
    # not uploaded over HTTP, so the write routes keep the strict entity check
    if kind not in _IMAGE_KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")
```

Change only the two **world GET** routes to use it (they stop going through
`_entity_images_list`, which keeps the strict guard for its campaign callers):

```python
@router.get("/worlds/{wid}/{kind}/{eid}/images")
def list_world_entity_images(wid: str, kind: str, eid: str):
    _image_kind_or_404(kind)
    return store.assets.list_images(_world_root_or_404(wid), eid, "default", base=kind)


@router.get("/worlds/{wid}/{kind}/{eid}/images/{name}")
def get_world_entity_image(wid: str, kind: str, eid: str, name: str):
    _image_kind_or_404(kind)
    return _serve_image(_world_root_or_404(wid), eid, "default", name, base=kind)
```

PUT/DELETE/promote world routes and all campaign routes are untouched (they
still call `_entity_kind_or_404` via their helpers, so `greetings` 404s there).

- [ ] **Step 4: Run test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q`
Expected: all PASS (including `test_entity_images_crud_promote_and_has_image` and `test_entity_images_unknown_kind_404` — entity behavior unchanged)

- [ ] **Step 5: Run the full backend suite + frontend regression**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS

From `frontend/`: `npx vitest run` then `npx tsc -b`
Expected: PASS (no frontend changes; regression only)

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): serve greeting images via the generic entity-image GET routes"
```

---

## After the plan (not tasks — supervised one-off, per spec Parts 2–3)

Extraction and apply are driven interactively in-session: parse the card with
`cards.loads`, draft the proposal doc for user review, then run the apply
script (store-layer calls: `create_character` / `create_entity` /
`create_greeting` / `localize_greeting` / `put_image`) against the live store
once the user approves and names the target world. Not part of this plan's
task list because each step needs user review, not a fresh engineer.
