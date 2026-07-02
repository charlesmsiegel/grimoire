# Character Avatar Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate character avatars (and location imagery) throughout grimoire: speaker plates in the scene stream, portrait rows in the scene inspector, unified "Images" shelves with avatar promotion on character/location detail pages, and owner-avatar stacks on lore.

**Architecture:** Backend first — generalize the per-version asset store (`store/assets.py`) with a `base` path segment so locations/lore get image folders too, add a promote (swap-with-avatar) operation, and expose entity-image + promote endpoints. Frontend second — a shared `Portrait` component (img with initials fallback), then rework `CampaignView` (speaker plates + gutter icons replacing the vertical spine), `SceneInspector` (portrait roster + location thumb), `CharacterEditor` (hero + Images shelf), and `EntityEditor` (location primary image + lore owner avatars).

**Tech Stack:** FastAPI + pytest (backend), React + TypeScript + vitest/testing-library (frontend), class-based CSS in `frontend/src/index.css`, theme tokens in `frontend/src/theme/themes/*.ts`.

## Global Constraints

- Backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q` (store isolated via `GRIMOIRE_HOME`).
- Frontend tests: run **from `frontend/`**: `npx vitest run` and `npx tsc -b` (never `npx --prefix frontend`).
- Use only existing theme tokens plus ONE new token `--on-quote` (codex `#ffffff`, manuscript `#efe4c9`, astral `#041014`). No per-theme code.
- Do NOT ship the placeholder artwork from the design bundle.
- Key measurements (final): plate avatar 44px · post indent 56px (44 + 12 gap) · inspector avatar 34px · drawer portrait 180px · detail hero 150px · shelf tiles 96px (characters) / 154px @ 16:10 (locations) · lore owner stack 26px / chips 22px.
- The avatar/primary image is always the asset named `avatar` (UI labels it "primary" for locations); gallery images are `gallery_N`.
- Promotion **swaps** `gallery_N` with `avatar` — no image is ever lost.
- Every avatar/thumb `<img>` needs an `onError` fallback (hide image / show initials).
- Keep the existing `RenderedMarkdown` message rendering and `.msg-edit-form` inline-edit behavior unchanged.
- Commit after each task with a conventional message; end commit messages with the Claude Code trailer.

---

### Task 1: Generalize the asset store + promote operation

**Files:**
- Modify: `backend/src/grimoire/store/assets.py`
- Test: `backend/tests/test_assets_store.py`

**Interfaces:**
- Consumes: existing `assets` module functions.
- Produces: every public function gains a keyword arg `base: str = "characters"` (`image_path`, `list_images`, `put_image`, `delete_image`), plus new `promote_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None` that swaps `<name>` with `avatar` (each file keeps its own extension); raises `FileNotFoundError` if `<name>` doesn't exist; no-op if `name == "avatar"`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_assets_store.py` (it already does `from grimoire.store import assets` and uses `pytest`; mirror the existing imports at the top of the file):

```python
def test_promote_swaps_gallery_with_avatar(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "avatar", b"old", "png")
    assets.put_image(tmp_path, "sera", "default", "gallery_2", b"new", "webp")
    assets.promote_image(tmp_path, "sera", "default", "gallery_2")
    av = assets.image_path(tmp_path, "sera", "default", "avatar")
    gal = assets.image_path(tmp_path, "sera", "default", "gallery_2")
    assert av.read_bytes() == b"new" and av.suffix == ".webp"
    assert gal.read_bytes() == b"old" and gal.suffix == ".png"


def test_promote_without_existing_avatar_renames(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"n", "png")
    assets.promote_image(tmp_path, "sera", "default", "gallery_1")
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"n"
    assert assets.image_path(tmp_path, "sera", "default", "gallery_1") is None


def test_promote_missing_image_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        assets.promote_image(tmp_path, "sera", "default", "gallery_9")


def test_promote_avatar_itself_is_a_noop(tmp_path):
    assets.put_image(tmp_path, "sera", "default", "avatar", b"a", "png")
    assets.promote_image(tmp_path, "sera", "default", "avatar")
    assert assets.image_path(tmp_path, "sera", "default", "avatar").read_bytes() == b"a"


def test_base_param_roots_other_kinds(tmp_path):
    assets.put_image(tmp_path, "docks", "default", "avatar", b"i", "png", base="locations")
    p = assets.image_path(tmp_path, "docks", "default", "avatar", base="locations")
    assert p is not None and "locations" in p.parts
    # not visible under the default characters/ base
    assert assets.image_path(tmp_path, "docks", "default", "avatar") is None
    assert assets.list_images(tmp_path, "docks", "default", base="locations") == [
        {"name": "avatar", "ext": "png"}]
    assets.delete_image(tmp_path, "docks", "default", "avatar", base="locations")
    assert assets.image_path(tmp_path, "docks", "default", "avatar", base="locations") is None
```

If the top of the file lacks `import pytest`, add it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_assets_store.py -q`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'base'` / `AttributeError: ... 'promote_image'`.

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/assets.py`, thread `base` through `_dir` and all public functions, and add `promote_image`:

```python
def _dir(root: Path, cid: str, vid: str, base: str = "characters") -> Path:
    return root / base / cid / "assets" / vid


def image_path(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> Path | None:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        return None
    d = _dir(root, cid, vid, base)
    if not d.exists():
        return None
    matches = sorted(d.glob(f"{name}.*"))
    return matches[0] if matches else None


def list_images(root: Path, cid: str, vid: str, base: str = "characters") -> list[dict]:
    if not (_safe(cid) and _safe(vid)):
        return []
    d = _dir(root, cid, vid, base)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.iterdir()):
        if p.is_file() and p.suffix:
            out.append({"name": p.stem, "ext": p.suffix.lstrip(".").lower()})
    return out


def put_image(root: Path, cid: str, vid: str, name: str, data: bytes, ext: str,
              base: str = "characters") -> str:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        raise ValueError("unsafe image id")
    ext = _norm_ext(ext)
    if not ext:
        raise ValueError("unsupported image type")
    d = _dir(root, cid, vid, base)
    d.mkdir(parents=True, exist_ok=True)
    for p in d.glob(f"{name}.*"):  # drop any prior-ext file of this name
        p.unlink()
    (d / f"{name}.{ext}").write_bytes(data)
    return ext


def delete_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    if not (_safe(cid) and _safe(vid) and _safe_name(name)):
        return
    d = _dir(root, cid, vid, base)
    if d.exists():
        for p in d.glob(f"{name}.*"):
            p.unlink()


def promote_image(root: Path, cid: str, vid: str, name: str, base: str = "characters") -> None:
    """Make <name> the avatar; the old avatar takes <name>'s slot (swap, nothing lost)."""
    if name == AVATAR:
        return
    src = image_path(root, cid, vid, name, base)
    if src is None:
        raise FileNotFoundError(name)
    cur = image_path(root, cid, vid, AVATAR, base)
    d = _dir(root, cid, vid, base)
    tmp = d / f"promote-tmp{src.suffix}"
    src.rename(tmp)
    if cur is not None:
        cur.rename(d / f"{name}{cur.suffix}")
    tmp.rename(d / f"{AVATAR}{src.suffix}")
```

(Note `promote-tmp` contains `-`, which `_safe_name` allows but which cannot collide with `avatar`/`gallery_N` uploads.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_assets_store.py backend/tests/test_characters_store.py -q`
Expected: PASS (character-store tests confirm the default `base` keeps existing callers working).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/assets.py backend/tests/test_assets_store.py
git commit -m "feat(assets): base-parameterized image store with avatar promote swap"
```

---

### Task 2: Character promote endpoint + no-cache image serving

**Files:**
- Modify: `backend/src/grimoire/routes.py` (around the image routes at ~line 703–741)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.assets.promote_image` (Task 1).
- Produces: `POST /api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/promote` → `{"ok": true}` (404 if the image is missing). `_serve_image` responses now carry `Cache-Control: no-cache` so a promoted avatar shows everywhere without a reload.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_routes.py` (helpers `_world(client)` exists; images are uploaded as multipart with `files={"file": (filename, bytes, mime)}` — check an existing image test in the file and mirror it; `import io` is already at the top):

```python
def test_character_image_promote_swaps_avatar(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    vid = client.get(f"/api/worlds/{wid}/characters/{cid}").json()["meta"]["default_version"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/images"
    client.put(f"{base}/avatar", files={"file": ("a.png", b"old", "image/png")})
    client.put(f"{base}/gallery_1", files={"file": ("g.png", b"new", "image/png")})

    r = client.post(f"{base}/gallery_1/promote")
    assert r.status_code == 200

    got = client.get(f"{base}/avatar")
    assert got.content == b"new"
    assert got.headers["cache-control"] == "no-cache"
    assert client.get(f"{base}/gallery_1").content == b"old"


def test_character_image_promote_missing_404(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    vid = client.get(f"/api/worlds/{wid}/characters/{cid}").json()["meta"]["default_version"]
    r = client.post(f"/api/worlds/{wid}/characters/{cid}/versions/{vid}/images/gallery_9/promote")
    assert r.status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k promote`
Expected: FAIL (405/404 — route doesn't exist).

- [ ] **Step 3: Implement**

In `routes.py`, add the header to `_serve_image` and the promote route right after `delete_world_image`:

```python
def _serve_image(root, cid: str, vid: str, name: str, base: str = "characters"):
    p = store.assets.image_path(root, cid, vid, name, base)
    if p is None:
        raise HTTPException(status_code=404, detail="image not found")
    ext = p.suffix.lstrip(".").lower()
    # no-cache: promotions swap file contents under stable URLs
    return Response(content=p.read_bytes(),
                    media_type=_IMAGE_MEDIA.get(ext, "application/octet-stream"),
                    headers={"Cache-Control": "no-cache"})


@router.post("/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/promote")
def promote_world_image(wid: str, cid: str, vid: str, name: str):
    try:
        store.assets.promote_image(_world_root_or_404(wid), cid, vid, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): character avatar promote endpoint; no-cache image serving"
```

---

### Task 3: Entity image endpoints (locations/lore, world + campaign) with `has_image`

**Files:**
- Modify: `backend/src/grimoire/routes.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.assets.*` with `base=kind` (Task 1), `store.entities.ENTITY_KINDS`.
- Produces (both `/api/worlds/{wid}/…` and `/api/campaigns/{cid}/…` scopes; `kind` must be in `ENTITY_KINDS` else 404):
  - `GET    …/{kind}/{eid}/images` → `[{"name","ext"}]`
  - `GET    …/{kind}/{eid}/images/{name}` → image bytes
  - `PUT    …/{kind}/{eid}/images/{name}` (multipart `file`) → `{"name","ext"}`
  - `DELETE …/{kind}/{eid}/images/{name}` → `{"ok": true}`
  - `POST   …/{kind}/{eid}/images/{name}/promote` → `{"ok": true}`
  - Entity list items (`_entity_list`) gain `"has_image": bool` (an `avatar` asset exists). Entity assets live at `<root>/<kind>/<eid>/assets/default/` (version segment fixed to `"default"`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py`:

```python
def test_entity_images_crud_promote_and_has_image(client):
    wid = _world(client)
    eid = client.post(f"/api/worlds/{wid}/locations", json={"name": "Warehouse Nine"}).json()["id"]
    base = f"/api/worlds/{wid}/locations/{eid}/images"

    assert client.get(f"/api/worlds/{wid}/locations").json()[0]["has_image"] is False
    assert client.get(base).json() == []

    r = client.put(f"{base}/avatar", files={"file": ("w.png", b"day", "image/png")})
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    client.put(f"{base}/gallery_1", files={"file": ("n.png", b"night", "image/png")})

    assert client.get(f"/api/worlds/{wid}/locations").json()[0]["has_image"] is True
    assert {i["name"] for i in client.get(base).json()} == {"avatar", "gallery_1"}
    assert client.get(f"{base}/avatar").content == b"day"

    assert client.post(f"{base}/gallery_1/promote").status_code == 200
    assert client.get(f"{base}/avatar").content == b"night"
    assert client.get(f"{base}/gallery_1").content == b"day"

    assert client.delete(f"{base}/gallery_1").status_code == 200
    assert client.get(f"{base}/gallery_1").status_code == 404


def test_entity_images_unknown_kind_404(client):
    wid = _world(client)
    assert client.get(f"/api/worlds/{wid}/potions/x/images").status_code == 404
    assert client.put(f"/api/worlds/{wid}/potions/x/images/avatar",
                      files={"file": ("a.png", b"x", "image/png")}).status_code == 404


def test_campaign_entity_images_served(client):
    _, cid = _campaign(client)
    eid = client.post(f"/api/campaigns/{cid}/locations", json={"name": "Crypt"}).json()["id"]
    base = f"/api/campaigns/{cid}/locations/{eid}/images"
    client.put(f"{base}/avatar", files={"file": ("c.png", b"img", "image/png")})
    assert client.get(f"{base}/avatar").content == b"img"
    assert client.get(f"/api/campaigns/{cid}/locations").json()[0]["has_image"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k entity_images`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `routes.py`:

1. Extend `_entity_list` (root also has the assets):

```python
def _entity_list(root, kind: str):
    try:
        items = store.entities.list_entities(root, kind)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    for it in items:
        it["has_image"] = store.assets.image_path(
            root, it["id"], "default", store.assets.AVATAR, base=kind) is not None
    return items
```

2. Add shared entity-image helpers + routes for both scopes. Place the world-scope routes next to the other `/worlds/{wid}/{kind}` routes and the campaign-scope ones next to `/campaigns/{cid}/{kind}` (route shapes don't collide with the character/version image routes — different literal segments):

```python
def _entity_kind_or_404(kind: str) -> None:
    if kind not in store.entities.ENTITY_KINDS:
        raise HTTPException(status_code=404, detail="unknown kind")


def _entity_images_list(root, kind: str, eid: str):
    _entity_kind_or_404(kind)
    return store.assets.list_images(root, eid, "default", base=kind)


async def _entity_image_put(root, kind: str, eid: str, name: str, file: UploadFile):
    _entity_kind_or_404(kind)
    data = await file.read()
    fn = file.filename or ""
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    try:
        stored = store.assets.put_image(root, eid, "default", name, data, ext, base=kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


def _entity_image_promote(root, kind: str, eid: str, name: str):
    _entity_kind_or_404(kind)
    try:
        store.assets.promote_image(root, eid, "default", name, base=kind)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    return {"ok": True}


@router.get("/worlds/{wid}/{kind}/{eid}/images")
def list_world_entity_images(wid: str, kind: str, eid: str):
    return _entity_images_list(_world_root_or_404(wid), kind, eid)


@router.get("/worlds/{wid}/{kind}/{eid}/images/{name}")
def get_world_entity_image(wid: str, kind: str, eid: str, name: str):
    _entity_kind_or_404(kind)
    return _serve_image(_world_root_or_404(wid), eid, "default", name, base=kind)


@router.put("/worlds/{wid}/{kind}/{eid}/images/{name}")
async def put_world_entity_image(wid: str, kind: str, eid: str, name: str, file: UploadFile = File(...)):
    return await _entity_image_put(_world_root_or_404(wid), kind, eid, name, file)


@router.delete("/worlds/{wid}/{kind}/{eid}/images/{name}")
def delete_world_entity_image(wid: str, kind: str, eid: str, name: str):
    _entity_kind_or_404(kind)
    store.assets.delete_image(_world_root_or_404(wid), eid, "default", name, base=kind)
    return {"ok": True}


@router.post("/worlds/{wid}/{kind}/{eid}/images/{name}/promote")
def promote_world_entity_image(wid: str, kind: str, eid: str, name: str):
    return _entity_image_promote(_world_root_or_404(wid), kind, eid, name)
```

…and the same five for campaigns (`/campaigns/{cid}/{kind}/{eid}/images…` using `_campaign_root_or_404(cid)`), named `list_campaign_entity_images`, `get_campaign_entity_image`, `put_campaign_entity_image`, `delete_campaign_entity_image`, `promote_campaign_entity_image`.

- [ ] **Step 4: Run the backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (nothing else regresses — sync/entity tests still see `.md` files only).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): entity image CRUD + promote at world and campaign scope"
```

---

### Task 4: Tagline in the character list

**Files:**
- Modify: `backend/src/grimoire/store/characters.py` (`list_characters`, ~line 193)
- Test: `backend/tests/test_characters_store.py`

**Interfaces:**
- Produces: `list_characters` items gain `"tagline": str` (empty string when unset), read via `store.taglines.read`.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_characters_store.py`:

```python
def test_list_characters_includes_tagline(tmp_path):
    from grimoire.store import taglines
    cid, _ = ch.create_character(tmp_path, "Sera")
    assert ch.list_characters(tmp_path)[0]["tagline"] == ""
    taglines.write(tmp_path, cid, "Keeper of the salt ledgers.")
    assert ch.list_characters(tmp_path)[0]["tagline"] == "Keeper of the salt ledgers."
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_characters_store.py -q -k tagline`
Expected: FAIL with `KeyError: 'tagline'`.

- [ ] **Step 3: Implement**

In `store/characters.py`, import the module (top of file, with the other relative imports): `from . import taglines as _taglines` and inside the `list_characters` item dict add:

```python
                "tagline": _taglines.read(root, cid),
```

(If a `taglines` import would be circular — it isn't; `taglines.py` imports nothing from `characters` — plain `from . import taglines` is fine; match the file's import style, e.g. how `assets` is imported.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/characters.py backend/tests/test_characters_store.py
git commit -m "feat(characters): include tagline in the character list"
```

---

### Task 5: Frontend API client — promote + entity images + new fields

**Files:**
- Modify: `frontend/src/api/client.ts`
- Test: `frontend/src/api/client.test.ts`

**Interfaces:**
- Produces (on `api`):
  - `promoteImage(wid, cid, vid, name)` → `POST /api/worlds/{wid}/characters/{cid}/versions/{vid}/images/{name}/promote`
  - `entityImageUrl(scope: EntityScope, kind: EntityKind, eid: string, name: string): string`
  - `listEntityImages(scope, kind, eid)` → `Promise<{name: string; ext: string}[]>`
  - `putEntityImage(scope, kind, eid, name, file: File)` (multipart PUT)
  - `deleteEntityImage(scope, kind, eid, name)`
  - `promoteEntityImage(scope, kind, eid, name)`
- Types: `CharacterSummary` gains `tagline?: string`; `EntitySummary` gains `has_image?: boolean`.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/api/client.test.ts` (uses the `jsonOk` helper already in the file):

```ts
test("promoteImage POSTs the promote route", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonOk({ ok: true }));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.promoteImage("w", "sera", "v1", "gallery_2");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/characters/sera/versions/v1/images/gallery_2/promote",
    expect.objectContaining({ method: "POST" }),
  );
});

test("entity image helpers hit the scope-aware routes", async () => {
  const scope = { kind: "campaign", id: "run" } as const;
  expect(api.entityImageUrl(scope, "locations", "crypt", "avatar"))
    .toBe("/api/campaigns/run/locations/crypt/images/avatar");
  const fetchMock = vi.fn().mockResolvedValue(jsonOk([]));
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  await api.listEntityImages({ kind: "world", id: "w" }, "locations", "crypt");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/locations/crypt/images",
    expect.objectContaining({ method: "GET" }),
  );
  await api.promoteEntityImage({ kind: "world", id: "w" }, "locations", "crypt", "gallery_1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/worlds/w/locations/crypt/images/gallery_1/promote",
    expect.objectContaining({ method: "POST" }),
  );
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/api/client.test.ts`
Expected: FAIL — functions don't exist.

- [ ] **Step 3: Implement**

In `client.ts`:

```ts
export type EntitySummary = { id: string; name: string; keys?: string; owners?: string; has_image?: boolean };
export type CharacterSummary = { id: string; name: string; default_version: string; has_avatar?: boolean; tagline?: string; versions: VersionRef[] };
```

Next to the existing image helpers (after `deleteImage`):

```ts
  promoteImage: (wid: string, cid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}/promote`),
  entityImageUrl: (scope: EntityScope, kind: EntityKind, eid: string, name: string) =>
    `${entityBase(scope)}/${kind}/${eid}/images/${name}`,
  listEntityImages: (scope: EntityScope, kind: EntityKind, eid: string) =>
    request<{ name: string; ext: string }[]>("GET", `${entityBase(scope)}/${kind}/${eid}/images`),
  putEntityImage: (scope: EntityScope, kind: EntityKind, eid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ name: string; ext: string }>(
      `${entityBase(scope)}/${kind}/${eid}/images/${name}`, form, "PUT");
  },
  deleteEntityImage: (scope: EntityScope, kind: EntityKind, eid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/${kind}/${eid}/images/${name}`),
  promoteEntityImage: (scope: EntityScope, kind: EntityKind, eid: string, name: string) =>
    request<{ ok: boolean }>("POST", `${entityBase(scope)}/${kind}/${eid}/images/${name}/promote`),
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/api/client.test.ts && npx tsc -b`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/client.test.ts
git commit -m "feat(api): promote + entity image endpoints; tagline/has_image fields"
```

---

### Task 6: `--on-quote` theme token

**Files:**
- Modify: `frontend/src/theme/themes/codex.ts`, `manuscript.ts`, `astral.ts`
- Test: `frontend/src/theme/themes/tokens.test.ts`

**Interfaces:**
- Produces: token `--on-quote` in every theme (text color on `--quote` backgrounds, used by the pc role chip).

- [ ] **Step 1: Write the failing test** — in `tokens.test.ts`, add `"--on-quote"` to the `REQUIRED` array.

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/theme/themes/tokens.test.ts`
Expected: FAIL — `codex missing --on-quote`.

- [ ] **Step 3: Implement** — add to each theme's `tokens` (next to `--quote`):
  - codex.ts: `"--on-quote": "#ffffff",`
  - manuscript.ts: `"--on-quote": "#efe4c9",`
  - astral.ts: `"--on-quote": "#041014",`

- [ ] **Step 4: Run test to verify it passes**

Run (from `frontend/`): `npx vitest run src/theme/themes/tokens.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/theme/themes/codex.ts frontend/src/theme/themes/manuscript.ts frontend/src/theme/themes/astral.ts frontend/src/theme/themes/tokens.test.ts
git commit -m "feat(theme): --on-quote token for pc role chips"
```

---

### Task 7: Shared `Portrait` component

**Files:**
- Create: `frontend/src/components/Portrait.tsx`
- Modify: `frontend/src/index.css` (base portrait styles)
- Test: `frontend/src/components/Portrait.test.tsx`

**Interfaces:**
- Produces: `Portrait({ src, name }: { src: string | null; name: string })` — renders `<img class="portrait" alt="<name> portrait">`, or `<span class="portrait-initials">` with the first letters of the first two words of `name`, uppercased, when `src` is null or the image errors. Also exports `initialsOf(name: string): string`. Sizing is applied by context CSS (`.plate-avatar .portrait { width:44px… }` etc.), not by the component.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/components/Portrait.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { Portrait, initialsOf } from "./Portrait";

test("initialsOf takes the first letters of the first two words", () => {
  expect(initialsOf("Maren Voss")).toBe("MV");
  expect(initialsOf("odo")).toBe("O");
  expect(initialsOf("Brother Aldous the Grey")).toBe("BA");
});

test("renders the image when src is given", () => {
  render(<Portrait src="/img/a.png" name="Maren Voss" />);
  expect(screen.getByAltText("Maren Voss portrait")).toHaveAttribute("src", "/img/a.png");
});

test("falls back to initials when src is null", () => {
  render(<Portrait src={null} name="Maren Voss" />);
  expect(screen.getByText("MV")).toBeInTheDocument();
});

test("falls back to initials when the image fails to load", () => {
  render(<Portrait src="/img/broken.png" name="Maren Voss" />);
  fireEvent.error(screen.getByAltText("Maren Voss portrait"));
  expect(screen.getByText("MV")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/Portrait.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement**

Create `frontend/src/components/Portrait.tsx`:

```tsx
import { useEffect, useState } from "react";

export function initialsOf(name: string): string {
  return name.trim().split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("").toUpperCase();
}

/** Square portrait with an initials fallback (no src, or the file 404s). */
export function Portrait({ src, name }: { src: string | null; name: string }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [src]);
  if (!src || broken) {
    return <span className="portrait-initials" aria-hidden>{initialsOf(name)}</span>;
  }
  return <img className="portrait" alt={`${name} portrait`} src={src}
              onError={() => setBroken(true)} />;
}
```

Add to `index.css` (near the existing `.initials-avatar` block):

```css
/* ---- shared portrait (img + initials fallback); sized by context ---- */
.portrait { object-fit: cover; object-position: top; display: block; flex: none; }
.portrait-initials {
  display: flex; align-items: center; justify-content: center; flex: none;
  background: var(--subtle); color: var(--on-accent);
  font-family: var(--fd); font-weight: 900; text-transform: uppercase;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/Portrait.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/Portrait.tsx frontend/src/components/Portrait.test.tsx frontend/src/index.css
git commit -m "feat(components): Portrait with initials fallback"
```

---

### Task 8: Scene stream — speaker plates + gutter icons (remove the spine)

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`, `frontend/src/index.css`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `Portrait`/`initialsOf` (Task 7), `api.listAppearances` (`RosterEntry[]`), `api.getCast` (`Actor[]`), `api.campaignImageUrl`, `RecordDrawer` + `DrawerTarget`.
- Produces: grouped speaker-plate stream. Consecutive messages by the same resolved speaker form one run; a `.plate` header renders once per run; each post is a `.msg` row with a 44px `.msg-gutter` (hover-reveal ✎/↻ icons) and the existing `.msg-body`. The vertical `.spine`/`.spine-col`/`.spine-icons` markup **and CSS are removed**. Plate avatar/name click opens `RecordDrawer`.

Behavior spec:
- Resolved speaker of a message: `m.speaker ?? (m.role === "user" ? playerName ?? labels.user : labels.assistant)`.
- A run is `{ speaker, pc, actor, posts }`; `actor` = cast entry whose `name` equals the speaker; `pc` = `actor.role === "player"`, else `m.role === "user"` for actor-less messages.
- Plate avatar src: only for `actor.kind === "characters"` with a roster version — `api.campaignImageUrl(cid, actor.id, version, "avatar")`; otherwise initials.
- Role chip text: `pc` / `npc`.
- ✎ on every post (opens the unchanged `.msg-edit-form`); ↻ only on the last post when `canReroll` (logic unchanged); the reroll guidance popover anchors to the gutter.
- Streaming: if the last message's resolved speaker differs from `labels.assistant` (or there are no messages), render an initials plate for `labels.assistant`, then the streaming `.msg` with the blinking `.cursor`.

- [ ] **Step 1: Update + write failing tests**

In `CampaignView.test.tsx`:

1. Add to the `vi.mock("../api/client")` api object: `listAppearances: vi.fn(),` and in `beforeEach`: `(api.listAppearances as any).mockResolvedValue([]);`.
2. **Replace** the test `renders vertical speaker spines with configured labels and message speakers` with:

```tsx
test("groups consecutive posts under one speaker plate", async () => {
  (api.getConfig as any).mockResolvedValue({
    model: "m", theme: "codex", key_set: true, system_prompt: "", quote_color: "off",
    user_label: "Kestrel", assistant_label: "Grimoire",
  });
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [
      { role: "user", content: "I open the door." },
      { role: "assistant", content: "She waits.", speaker: "Seraphine Vale" },
      { role: "assistant", content: "She smiles.", speaker: "Seraphine Vale" },
    ],
  });
  renderCampaign();
  await screen.findByText("Kestrel");
  // one plate for the two-message Seraphine run
  expect(screen.getAllByText("Seraphine Vale")).toHaveLength(1);
  expect(document.querySelectorAll(".plate")).toHaveLength(2);
  expect(document.querySelector(".spine")).toBeNull();
  // initials fallback (no cast/roster mocked): first letters of first two words
  expect(screen.getByText("SV")).toBeInTheDocument();
});

test("plates mark PC speakers and show avatars from the roster", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "v1", role: "npc", scenes: ["s1"] },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [
      { role: "user", content: "Hello.", speaker: "Yara" },
      { role: "assistant", content: "She waits.", speaker: "Seraphine Vale" },
    ],
  });
  renderCampaign();
  await screen.findByText("Seraphine Vale");
  expect(document.querySelector(".plate.pc")).not.toBeNull();          // Yara run
  expect(screen.getByText("pc")).toBeInTheDocument();
  expect(screen.getByText("npc")).toBeInTheDocument();
  expect(screen.getByAltText("Seraphine Vale portrait")).toBeInTheDocument();
});

test("clicking a plate name opens the record drawer", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine Vale" },
  ]);
  (api.getScene as any).mockResolvedValue({
    meta: { id: "s1", title: "Old" },
    messages: [{ role: "assistant", content: "She waits.", speaker: "Seraphine Vale" }],
  });
  (api.getCastDetail as any).mockResolvedValue({
    kind: "characters", id: "seraphine", name: "Seraphine Vale", version: "v1", body: "keeper" });
  renderCampaign();
  fireEvent.click(await screen.findByRole("button", { name: "Seraphine Vale" }));
  await screen.findByText("keeper");
});
```

3. Existing edit/reroll tests: they find buttons by `aria-label` (`Edit message N`, `Reroll`) — those labels are kept, so they should pass unchanged. If any test asserts on `.spine`, update it to `.plate`.

- [ ] **Step 2: Run tests to verify the new ones fail**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx`
Expected: new tests FAIL (no `.plate`), old spine test removed.

- [ ] **Step 3: Implement `CampaignView.tsx`**

1. Imports: add `useMemo` to the react import; add `type Actor, type RosterEntry` to the client type imports; add `import { Portrait } from "../components/Portrait";` and `import { RecordDrawer, type DrawerTarget } from "../components/RecordDrawer";`.
2. Replace the `playerName` state with cast/roster state + a derived value, and add drawer state:

```tsx
  const [cast, setCast] = useState<Actor[]>([]);
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [drawer, setDrawer] = useState<DrawerTarget | null>(null);
  // unstamped user lines fall back to the sole player's name
  const playerName = useMemo(() => {
    const players = cast.filter((a) => a.role === "player");
    return players.length === 1 ? players[0].name : null;
  }, [cast]);
```

In `selectScene`, replace the `getCast(...)` block with:

```tsx
    api.getCast(cid, id).then(setCast).catch(() => setCast([]));
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
```

3. Run grouping + helpers (above the `return`):

```tsx
  const speakerOf = (m: Message) =>
    m.speaker ?? (m.role === "user" ? playerName ?? labels.user : labels.assistant);

  type Run = { speaker: string; pc: boolean; actor: Actor | undefined;
               posts: { m: Message; index: number }[] };
  const runs: Run[] = [];
  messages.forEach((m, index) => {
    const speaker = speakerOf(m);
    const last = runs[runs.length - 1];
    if (last && last.speaker === speaker) {
      last.posts.push({ m, index });
      return;
    }
    const actor = cast.find((a) => a.name === speaker);
    runs.push({ speaker, pc: actor ? actor.role === "player" : m.role === "user",
                actor, posts: [{ m, index }] });
  });

  function plateAvatar(run: Run): string | null {
    if (!run.actor || run.actor.kind !== "characters") return null;
    const ver = roster.find((r) => r.kind === "characters" && r.id === run.actor!.id)?.version;
    return ver ? api.campaignImageUrl(cid, run.actor.id, ver, "avatar") : null;
  }
```

4. Replace the `messages.map(...)` block inside `.stream` with:

```tsx
          {runs.map((run) => (
            <div className={"run" + (run.pc ? " pc" : "")} key={run.posts[0].index}>
              <div className={"plate" + (run.pc ? " pc" : "")}>
                {run.actor ? (
                  <>
                    <button className="plate-avatar" aria-label={`Open ${run.speaker} record`}
                            onClick={() => setDrawer({ type: "actor", kind: run.actor!.kind, id: run.actor!.id })}>
                      <Portrait src={plateAvatar(run)} name={run.speaker} />
                    </button>
                    <button className="plate-name"
                            onClick={() => setDrawer({ type: "actor", kind: run.actor!.kind, id: run.actor!.id })}>
                      {run.speaker}
                    </button>
                  </>
                ) : (
                  <>
                    <span className="plate-avatar"><Portrait src={null} name={run.speaker} /></span>
                    <span className="plate-name">{run.speaker}</span>
                  </>
                )}
                <span className="role-chip">{run.pc ? "pc" : "npc"}</span>
              </div>
              {run.posts.map(({ m, index }) => (
                <div className={`msg ${m.role}`} key={index}>
                  <span className="msg-gutter">
                    {editing?.index !== index && !busy && (
                      <span className="gutter-icons">
                        {index === messages.length - 1 && canReroll && (
                          <button className="msg-edit" title="Reroll" aria-label="Reroll"
                                  onClick={() => setRerollPrompt("")}>↻</button>
                        )}
                        <button className="msg-edit" title="Edit message" aria-label={`Edit message ${index + 1}`}
                                onClick={() => setEditing({ index, text: m.content })}>✎</button>
                      </span>
                    )}
                    {rerollPrompt !== null && !busy &&
                     index === messages.length - 1 && canReroll && (
                      <span className="reroll-pop">
                        <input
                          autoFocus
                          placeholder="Guide the reroll (optional)…"
                          aria-label="Reroll guidance"
                          value={rerollPrompt}
                          onChange={(e) => setRerollPrompt(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") reroll();
                            if (e.key === "Escape") setRerollPrompt(null);
                          }}
                        />
                        <button className="btn-chrome" onClick={reroll}>Reroll ▸</button>
                      </span>
                    )}
                  </span>
                  <div className="msg-body">
                    {editing?.index === index ? (
                      <div className="msg-edit-form">
                        <textarea aria-label="Edit message" rows={4} value={editing.text}
                                  onChange={(e) => setEditing({ index, text: e.target.value })} />
                        <div className="form-actions">
                          <button className="subtle" onClick={() => setEditing(null)}>Cancel</button>
                          <button className="primary" onClick={saveEdit}>Save</button>
                        </div>
                      </div>
                    ) : (
                      <RenderedMarkdown content={m.content} />
                    )}
                  </div>
                </div>
              ))}
            </div>
          ))}
```

5. Replace the streaming block with:

```tsx
          {streaming && (
            <div className="run">
              {(messages.length === 0 ||
                speakerOf(messages[messages.length - 1]) !== labels.assistant) && (
                <div className="plate">
                  <span className="plate-avatar"><Portrait src={null} name={labels.assistant} /></span>
                  <span className="plate-name">{labels.assistant}</span>
                  <span className="role-chip">npc</span>
                </div>
              )}
              <div className="msg assistant">
                <span className="msg-gutter" />
                <div className="msg-body">
                  <RenderedMarkdown content={streaming} />
                  <span className="cursor" />
                </div>
              </div>
            </div>
          )}
```

6. Render the drawer just before the closing `</div>` of `.layout` (next to the `<SceneInspector …/>` line):

```tsx
      {drawer && activeId && (
        <RecordDrawer cid={cid} sid={activeId} target={drawer} onClose={() => setDrawer(null)} />
      )}
```

- [ ] **Step 4: CSS**

In `index.css`: **delete** the `.spine` rule, the `.msg.user .spine` rule, and the whole `/* ---- spine column ---- */` block's `.spine-col` and `.spine-icons` rules (keep `.reroll-pop`, `.msg-edit`, `.msg-edit-form`). Update/add:

```css
.msg { display: flex; margin: 0 0 14px; position: relative; }
.msg-body { flex: 1; min-width: 0; font-family: var(--fb); font-size: 16px; line-height: 1.62; color: var(--page-ink); }

/* ---- speaker plates + post gutter ---- */
.run { margin-bottom: 12px; }
.plate { display: flex; align-items: center; gap: 12px; margin: 0 0 8px; }
.plate-avatar { flex: none; padding: 0; background: none; border: none; }
button.plate-avatar, button.plate-name { cursor: pointer; }
.plate-avatar .portrait, .plate-avatar .portrait-initials {
  width: 44px; height: 44px; border: var(--rw) solid var(--rule); box-shadow: var(--sh2);
}
.plate.pc .plate-avatar .portrait, .plate.pc .plate-avatar .portrait-initials { border-color: var(--accent); }
.plate-avatar .portrait-initials { font-size: 19px; }
.plate-name {
  background: none; border: none; padding: 0;
  font-family: var(--fd); font-weight: 800; font-size: 19px;
  text-transform: uppercase; color: var(--page-ink); text-align: left;
}
.plate.pc .plate-name { color: var(--quote); }
.role-chip {
  font-family: var(--fm); font-size: 9px; letter-spacing: 0.08em; text-transform: uppercase;
  padding: 2px 7px; color: var(--page-muted); border: var(--rw2) solid var(--rule-soft); flex: none;
}
.plate.pc .role-chip, .role-chip.pc { background: var(--quote); color: var(--on-quote); border: none; }
.msg-gutter {
  width: 44px; flex: none; margin-right: 12px; position: relative;
  display: flex; flex-direction: column; align-items: center;
}
.gutter-icons { display: flex; flex-direction: column; gap: 4px; opacity: 0; transition: opacity .15s; }
.msg:hover .gutter-icons, .msg:focus-within .gutter-icons { opacity: 1; }
```

(The `.reroll-pop` absolute positioning now anchors to `.msg-gutter`, which is `position: relative` — keep its existing rule.)

- [ ] **Step 5: Run tests**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx && npx tsc -b`
Expected: PASS (all — including the untouched edit/reroll/absorb tests).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx frontend/src/index.css
git commit -m "feat(stream): speaker plates with grouped runs and gutter icons replace the spine"
```

---

### Task 9: Scene inspector — portrait roster + location thumbnail

**Files:**
- Modify: `frontend/src/components/SceneInspector.tsx`, `frontend/src/index.css`
- Test: `frontend/src/components/SceneInspector.test.tsx`

**Interfaces:**
- Consumes: `Portrait` (Task 7), `api.listAppearances`, `api.campaignImageUrl`, `api.listEntityImages` / `api.entityImageUrl` (Task 5).
- Produces: cast rows with 34px portraits + role chips (`player`/`npc`); location section shows a 16:10 thumbnail above the name when the location has a primary image. Drawer behavior unchanged. `.drawer-avatar` grows to 180px.

- [ ] **Step 1: Write the failing tests**

In `SceneInspector.test.tsx`, add to the api mock: `listAppearances: vi.fn(), listEntityImages: vi.fn(), entityImageUrl: () => "/loc-img",` and in `beforeEach`:

```ts
  (api.listAppearances as any).mockResolvedValue([]);
  (api.listEntityImages as any).mockResolvedValue([]);
```

Add tests:

```tsx
test("cast rows show portraits with roster versions and role chips", async () => {
  (api.getCast as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", role: "npc", name: "Seraphine" },
    { kind: "pcs", id: "yara", role: "player", name: "Yara" },
  ]);
  (api.listAppearances as any).mockResolvedValue([
    { kind: "characters", id: "seraphine", version: "v2", role: "npc", scenes: ["s"] },
  ]);
  renderInspector();
  await screen.findByText("Seraphine");
  expect(screen.getByAltText("Seraphine portrait")).toBeInTheDocument(); // roster version found
  expect(screen.getByText("Y")).toBeInTheDocument();                     // PC initials fallback
  expect(screen.getByText("player")).toBeInTheDocument();
  expect(screen.getByText("npc")).toBeInTheDocument();
});

test("location with a primary image renders a clickable thumbnail", async () => {
  (api.listEntityImages as any).mockResolvedValue([{ name: "avatar", ext: "png" }]);
  renderInspector();
  const thumb = await screen.findByAltText("The Crypt");
  expect(thumb.closest("button")).not.toBeNull();
  await waitFor(() => expect(api.listEntityImages).toHaveBeenCalledWith(
    { kind: "campaign", id: "c" }, "locations", "crypt"));
});

test("location without an image keeps the text row", async () => {
  renderInspector();
  const row = await screen.findByRole("button", { name: "The Crypt" });
  expect(row.querySelector("img")).toBeNull();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

In `SceneInspector.tsx`:

1. Imports: add `type RosterEntry` to the client type import; `import { Portrait } from "./Portrait";`.
2. State: `const [roster, setRoster] = useState<RosterEntry[]>([]);` and `const [locImages, setLocImages] = useState<string[]>([]);`.
3. In the per-scene load effect add: `api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));`
4. Location images effect (after the load effect):

```tsx
  useEffect(() => {
    const loc = setting?.current;
    if (!loc) { setLocImages([]); return; }
    api.listEntityImages({ kind: "campaign", id: cid }, "locations", loc.id)
      .then((imgs) => setLocImages(imgs.map((i) => i.name)))
      .catch(() => setLocImages([]));
  }, [cid, setting]);
```

5. Replace the cast rows:

```tsx
        {cast.map((a) => {
          const ver = a.kind === "characters"
            ? roster.find((r) => r.kind === "characters" && r.id === a.id)?.version
            : undefined;
          const pc = a.role === "player";
          return (
            <button key={`${a.kind}/${a.id}`} className={"inspector-row" + (pc ? " pc" : "")}
                    onClick={() => setDrawer({ type: "actor", kind: a.kind, id: a.id })}>
              <Portrait src={ver ? api.campaignImageUrl(cid, a.id, ver, "avatar") : null}
                        name={nameOf(a)} />
              <span className="inspector-name">{nameOf(a)}</span>
              <span className="role-chip">{pc ? "player" : "npc"}</span>
            </button>
          );
        })}
```

6. Replace the location section body:

```tsx
        {setting?.current
          ? <button className={"inspector-row" + (locImages.includes("avatar") ? " inspector-loc" : "")}
                    onClick={() => setDrawer({ type: "location", id: setting.current!.id })}>
              {locImages.includes("avatar") && (
                <img className="inspector-loc-thumb" alt={setting.current.name}
                     src={api.entityImageUrl({ kind: "campaign", id: cid }, "locations", setting.current.id, "avatar")}
                     onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
              )}
              <span>{setting.current.name}</span>
            </button>
          : <div className="field-hint">No setting</div>}
```

- [ ] **Step 4: CSS**

In `index.css`, replace the `.inspector-row .role` rule and add:

```css
.inspector-row { gap: 10px; padding: 8px 0; }
.inspector-row .portrait, .inspector-row .portrait-initials {
  width: 34px; height: 34px; border: var(--rw2) solid var(--rule);
}
.inspector-row.pc .portrait, .inspector-row.pc .portrait-initials { border-color: var(--accent); }
.inspector-row .portrait-initials { font-size: 15px; }
.inspector-name { flex: 1; font-family: var(--fb); font-size: 15px; color: var(--ink); }
.inspector .role-chip { color: var(--muted); border-color: var(--rule); }
.inspector .inspector-row.pc .role-chip { background: var(--quote); color: var(--on-quote); border: none; }
.inspector-loc { flex-direction: column; align-items: stretch; gap: 6px; }
.inspector-loc-thumb {
  width: 100%; aspect-ratio: 16 / 10; object-fit: cover; display: block;
  border: var(--rw2) solid var(--rule); box-shadow: var(--sh2);
}
```

Also bump the drawer portrait: change `.drawer-avatar { max-width: 160px; …` to `max-width: 180px;`.

- [ ] **Step 5: Run tests**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx src/routes/CampaignView.test.tsx && npx tsc -b`
Expected: PASS (CampaignView embeds the inspector — its mock also needs `listEntityImages: vi.fn()` returning `[]` and `entityImageUrl: () => "/loc-img"`; add them to that file's api mock + beforeEach as in Step 1).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SceneInspector.tsx frontend/src/components/SceneInspector.test.tsx frontend/src/routes/CampaignView.test.tsx frontend/src/index.css
git commit -m "feat(inspector): portrait roster rows and location primary-image thumbnail"
```

---

### Task 10: Character detail — 150px hero + Images shelf + grid taglines

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx`, `frontend/src/index.css`
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `api.promoteImage` (Task 5), `CharacterSummary.tagline` (Tasks 4–5).
- Produces: detail-mode "Images" shelf replacing the old Gallery block — avatar tile (96px, accent border, caption `avatar`; dashed `no avatar` placeholder when absent), gallery tiles with a **Set as avatar** button, a dashed **+ add** upload tile; promotion swaps and busts the avatar cache. Hero avatar 150px. Grid cards gain an ellipsized italic tagline line. Edit-mode Upload/Replace/Remove controls unchanged.

- [ ] **Step 1: Write the failing tests**

In `CharacterEditor.test.tsx`, add `promoteImage: vi.fn(),` to the api mock and `(api.promoteImage as any).mockResolvedValue({ ok: true });` to `beforeEach` (also ensure the `listCharacters` fixture rows include `tagline` where the test needs it). Add tests (mirror the file's existing fixtures for `readCharacter` — a detail with `versions: [{ id, name, card, images }]`):

```tsx
test("grid cards show the tagline under the name", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "sera", name: "Sera", default_version: "v1", has_avatar: false,
      tagline: "Keeper of the salt ledgers.", versions: [{ id: "v1", name: "v1" }] },
  ]);
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Keeper of the salt ledgers.");
});

test("detail shows the Images shelf with avatar tile, gallery promote, and add tile", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "sera", name: "Sera", default_version: "v1", has_avatar: true, versions: [{ id: "v1", name: "v1" }] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "sera", name: "Sera", default_version: "v1" },
    versions: [{ id: "v1", name: "v1", images: ["avatar", "gallery_1"],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Sera" } } }],
  });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Sera"));
  await screen.findByText("Images");
  expect(screen.getByText("avatar")).toBeInTheDocument();               // shelf caption
  fireEvent.click(screen.getByRole("button", { name: /set as avatar/i }));
  await waitFor(() => expect(api.promoteImage).toHaveBeenCalledWith("w", "sera", "v1", "gallery_1"));
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});

test("detail without avatar shows the dashed placeholder tile", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "sera", name: "Sera", default_version: "v1", has_avatar: false, versions: [{ id: "v1", name: "v1" }] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "sera", name: "Sera", default_version: "v1" },
    versions: [{ id: "v1", name: "v1", images: [],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Sera" } } }],
  });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Sera"));
  await screen.findByText("no avatar");
});
```

(Adapt the `render(...)` call and mock names to how the file's existing tests render `CharacterEditor` — copy a neighboring test's setup verbatim, including required mocks like `getCharacterTagline`.)

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

In `CharacterEditor.tsx`:

1. Add a ref: `const shelfFileRef = useRef<HTMLInputElement>(null);`
2. Handlers (near `onAvatar`):

```tsx
  // Reload the open version in place (select() would snap back to the default version).
  async function refreshVersion() {
    if (!detail) return;
    const d = await api.readCharacter(wid, detail.meta.id);
    setDetail(d);
    loadVersion(d, vid);
    await reload();
    setAvatarBust((n) => n + 1);
  }

  async function promote(name: string) {
    if (!detail) return;
    setError(null);
    try {
      await api.promoteImage(wid, detail.meta.id, vid, name);
      await refreshVersion();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function onShelfAdd(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !detail) return;
    setError(null);
    const next = hasAvatar
      ? `gallery_${galleryImages.reduce((m, n) => Math.max(m, Number(n.slice("gallery_".length))), 0) + 1}`
      : "avatar";
    try {
      await api.putImage(wid, detail.meta.id, vid, next, file);
      await refreshVersion();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
  }
```

3. In detail mode, **replace** the whole `{galleryImages.length > 0 && (… Gallery …)}` block with the shelf (placed in the same spot):

```tsx
            <div className="detail-field">
              <div className="section-label">Images</div>
              <div className="images-shelf">
                {hasAvatar ? (
                  <figure className="shelf-tile avatar-tile">
                    <a href={avatarSrc(detail.meta.id, vid, true)} target="_blank" rel="noreferrer">
                      <img alt="avatar" src={avatarSrc(detail.meta.id, vid, true)} />
                    </a>
                    <figcaption>avatar</figcaption>
                  </figure>
                ) : (
                  <div className="shelf-tile shelf-empty">no avatar</div>
                )}
                {galleryImages.map((name) => {
                  const src = `${api.imageUrl(wid, detail.meta.id, vid, name)}?v=${avatarBust}`;
                  return (
                    <div className="shelf-tile" key={name}>
                      <a href={src} target="_blank" rel="noreferrer"><img alt={name} src={src} /></a>
                      <button className="shelf-promote" onClick={() => promote(name)}>Set as avatar</button>
                    </div>
                  );
                })}
                <button className="shelf-add" onClick={() => shelfFileRef.current?.click()}>+ add</button>
                <input ref={shelfFileRef} type="file" accept="image/*" hidden
                       aria-label="Add image" onChange={onShelfAdd} />
              </div>
            </div>
```

4. Grid cards — inside `.char-card-main`, after the name:

```tsx
                  <span className="char-card-name">{c.name}</span>
                  {c.tagline ? <span className="char-card-tagline">{c.tagline}</span> : null}
```

- [ ] **Step 4: CSS**

In `index.css`:
- `.detail-avatar` and `.initials-avatar.detail`: `width: 150px; height: 150px;`; set `.initials-avatar.detail { font-size: 56px; }`.
- Delete `.gallery-grid` / `.gallery-thumb` rules (no longer referenced).
- Add:

```css
/* ---- images shelf (record detail pages) ---- */
.images-shelf { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-start; }
.shelf-tile { display: flex; flex-direction: column; gap: 4px; margin: 0; }
.shelf-tile img { width: 96px; height: 96px; object-fit: cover; display: block; border: var(--rw2) solid var(--rule); }
.shelf-tile.avatar-tile img { border: var(--rw) solid var(--accent); }
.shelf-tile figcaption {
  font-family: var(--fm); font-size: 8.5px; font-weight: 700; letter-spacing: 0.08em;
  color: var(--accent); text-transform: uppercase; text-align: center;
}
.shelf-promote {
  font-family: var(--fm); font-size: 8.5px; letter-spacing: 0.06em; text-transform: uppercase;
  background: none; border: var(--rw2) solid var(--rule-soft); color: var(--subtle);
  padding: 3px 6px; cursor: pointer;
}
.shelf-promote:hover { color: var(--accent); border-color: var(--accent); }
.shelf-empty, .shelf-add {
  width: 96px; height: 96px; display: flex; align-items: center; justify-content: center;
  border: var(--rw) dashed var(--rule-soft); background: none; color: var(--muted);
  font-family: var(--fm); font-size: 10px; text-transform: uppercase;
}
.shelf-add { cursor: pointer; }
.shelf-add:hover { color: var(--accent); border-color: var(--accent); }
/* wide (16:10) variant for locations */
.images-shelf.wide .shelf-tile img, .images-shelf.wide .shelf-empty, .images-shelf.wide .shelf-add { width: 154px; }
.images-shelf.wide .shelf-tile img { height: auto; aspect-ratio: 16 / 10; }
.images-shelf.wide .shelf-empty, .images-shelf.wide .shelf-add { height: 96px; }

.char-card-tagline {
  font-family: var(--fb); font-style: italic; font-size: 12.5px; color: var(--muted);
  padding: 0 14px 12px; text-align: left; white-space: nowrap; overflow: hidden;
  text-overflow: ellipsis; max-width: 100%;
}
```

- [ ] **Step 5: Run tests**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx && npx tsc -b`
Expected: PASS (if an existing test asserted the old "Gallery" heading, update it to the shelf).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx frontend/src/index.css
git commit -m "feat(characters): images shelf with avatar promotion; 150px hero; grid taglines"
```

---

### Task 11: Location detail — primary image header, shelf, and rail cards

**Files:**
- Modify: `frontend/src/components/EntityEditor.tsx`, `frontend/src/index.css`
- Test: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: `api.listEntityImages` / `entityImageUrl` / `putEntityImage` / `promoteEntityImage` (Task 5), `EntitySummary.has_image` (Task 3).
- Produces (locations only; lore untouched here): rail rows become image-topped cards when `has_image`; the read-only detail view gains a header image (340px wide, 16:10) beside the title and an "Images" shelf (154px 16:10 tiles, first tile captioned **primary**, buttons **Set as primary**, dashed **+ add** upload tile).

- [ ] **Step 1: Write the failing tests**

In `EntityEditor.test.tsx` add to the api mock: `listEntityImages: vi.fn(), putEntityImage: vi.fn(), promoteEntityImage: vi.fn(), entityImageUrl: (_s: any, k: string, e: string, n: string) => `/img/${k}/${e}/${n}`,` and in `beforeEach`:

```ts
  (api.listEntityImages as any).mockResolvedValue([]);
  (api.putEntityImage as any).mockResolvedValue({ name: "avatar", ext: "png" });
  (api.promoteEntityImage as any).mockResolvedValue({ ok: true });
```

Add tests:

```tsx
test("location rail rows show the primary image when one exists", async () => {
  (api.listEntities as any).mockResolvedValue([
    { id: "warehouse", name: "Warehouse Nine", has_image: true },
    { id: "reeds", name: "The Reeds", has_image: false },
  ]);
  const { container } = render(<EntityEditor wid="w" kind="locations" />);
  await screen.findByText("Warehouse Nine");
  expect(container.querySelectorAll(".loc-row-img")).toHaveLength(1);
});

test("location detail shows the primary image header and Images shelf with promote", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "warehouse", name: "Warehouse Nine", has_image: true }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "warehouse", name: "Warehouse Nine" }, body: "docks" });
  (api.listEntityImages as any).mockResolvedValue([
    { name: "avatar", ext: "png" }, { name: "gallery_1", ext: "png" },
  ]);
  render(<EntityEditor wid="w" kind="locations" />);
  fireEvent.click(await screen.findByText("Warehouse Nine"));
  await screen.findByText("Images");
  expect(screen.getByText("primary")).toBeInTheDocument();               // shelf caption
  expect(screen.getByAltText("Warehouse Nine primary")).toBeInTheDocument(); // header image
  fireEvent.click(screen.getByRole("button", { name: /set as primary/i }));
  await waitFor(() => expect(api.promoteEntityImage).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "locations", "warehouse", "gallery_1"));
});

test("location detail without images shows the add tile only", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "reeds", name: "The Reeds", has_image: false }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "reeds", name: "The Reeds" }, body: "marsh" });
  render(<EntityEditor wid="w" kind="locations" />);
  fireEvent.click(await screen.findByText("The Reeds"));
  await screen.findByText("no image");
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/EntityEditor.test.tsx`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

In `EntityEditor.tsx`:

1. Imports: add `useRef` to the react import.
2. State + helpers:

```tsx
  const [images, setImages] = useState<string[]>([]);
  const [imgBust, setImgBust] = useState(0);
  const shelfFileRef = useRef<HTMLInputElement>(null);

  const reloadImages = useCallback((id: string) => {
    if (kind !== "locations") { setImages([]); return; }
    api.listEntityImages(scope, kind, id)
      .then((imgs) => setImages(imgs.map((i) => i.name)))
      .catch(() => setImages([]));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, scope.kind, scope.id]);
```

In `select(id)`, after `setMode("view")`: `reloadImages(id);`. In `resetForm()`: `setImages([]);`.

3. Shelf helpers (below `save`):

```tsx
  const hasPrimary = images.includes("avatar");
  const galleryNames = images
    .filter((n) => n.startsWith("gallery_"))
    .sort((a, b) => Number(a.slice("gallery_".length)) - Number(b.slice("gallery_".length)));
  const imgSrc = (n: string) => `${api.entityImageUrl(scope, kind, editing ?? "", n)}?v=${imgBust}`;

  async function promoteImage(name: string) {
    if (!editing) return;
    setError(null);
    try {
      await api.promoteEntityImage(scope, kind, editing, name);
      reloadImages(editing);
      await reload();
      setImgBust((n) => n + 1);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function onShelfAdd(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || !editing) return;
    setError(null);
    const next = hasPrimary
      ? `gallery_${galleryNames.reduce((m, n) => Math.max(m, Number(n.slice("gallery_".length))), 0) + 1}`
      : "avatar";
    try {
      await api.putEntityImage(scope, kind, editing, next, file);
      reloadImages(editing);
      await reload();
      setImgBust((n) => n + 1);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      e.target.value = "";
    }
  }
```

4. Rail rows — replace the `row` const:

```tsx
  const row = (e: EntitySummary) => (
    <button key={e.id}
            className={"row" + (kind === "locations" ? " loc-row" : "") + (editing === e.id ? " active" : "")}
            onClick={() => select(e.id)}>
      {kind === "locations" && e.has_image && (
        <img className="loc-row-img" alt="" src={api.entityImageUrl(scope, kind, e.id, "avatar")}
             onError={(ev) => { (ev.currentTarget as HTMLImageElement).style.display = "none"; }} />
      )}
      <span className="row-name">{e.name}</span>
    </button>
  );
```

5. Detail view — replace `<h3>{name}</h3>` in `.detail-main` with a header that shows the primary image for locations, and insert the shelf between the header and `.detail-rendered`:

```tsx
            <div className="detail-main">
              {kind === "locations" && editing && hasPrimary ? (
                <div className="loc-head">
                  <img className="loc-head-img" alt={`${name} primary`} src={imgSrc("avatar")}
                       onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />
                  <h3>{name}</h3>
                </div>
              ) : (
                <h3>{name}</h3>
              )}
              {kind === "locations" && editing && (
                <>
                  <div className="section-label">Images</div>
                  <div className="images-shelf wide">
                    {hasPrimary ? (
                      <figure className="shelf-tile avatar-tile">
                        <a href={imgSrc("avatar")} target="_blank" rel="noreferrer">
                          <img alt="primary image" src={imgSrc("avatar")} />
                        </a>
                        <figcaption>primary</figcaption>
                      </figure>
                    ) : (
                      <div className="shelf-tile shelf-empty">no image</div>
                    )}
                    {galleryNames.map((n) => (
                      <div className="shelf-tile" key={n}>
                        <a href={imgSrc(n)} target="_blank" rel="noreferrer"><img alt={n} src={imgSrc(n)} /></a>
                        <button className="shelf-promote" onClick={() => promoteImage(n)}>Set as primary</button>
                      </div>
                    ))}
                    <button className="shelf-add" onClick={() => shelfFileRef.current?.click()}>+ add</button>
                    <input ref={shelfFileRef} type="file" accept="image/*" hidden
                           aria-label="Add image" onChange={onShelfAdd} />
                  </div>
                </>
              )}
              <div className="detail-rendered">
                <Markdown remarkPlugins={[remarkGfm]}>{body}</Markdown>
              </div>
            </div>
```

- [ ] **Step 4: CSS**

Add to `index.css`:

```css
/* location rail cards + detail header image */
.row.loc-row { flex-direction: column; align-items: stretch; gap: 6px; padding: 0 0 8px; }
.loc-row-img { width: 100%; aspect-ratio: 16 / 10; object-fit: cover; display: block; }
.row.loc-row .row-name { font-family: var(--fd); font-weight: 700; font-size: 24px; padding: 6px 10px 0; }
.loc-head { display: flex; gap: 18px; align-items: flex-start; margin-bottom: 12px; }
.loc-head-img {
  width: 340px; max-width: 55%; aspect-ratio: 16 / 10; object-fit: cover; display: block;
  border: var(--rw) solid var(--rule); box-shadow: var(--sh5); flex: none;
}
.loc-head h3 { margin: 4px 0 0; }
```

- [ ] **Step 5: Run tests**

Run (from `frontend/`): `npx vitest run src/components/EntityEditor.test.tsx && npx tsc -b`
Expected: PASS (existing lore/location CRUD tests unchanged — `listEntityImages` resolves `[]` by default).

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/EntityEditor.tsx frontend/src/components/EntityEditor.test.tsx frontend/src/index.css
git commit -m "feat(locations): primary-image rail cards, detail header, and Images shelf"
```

---

### Task 12: Lore — owner avatar stacks and owner chips

**Files:**
- Modify: `frontend/src/api/loreOwners.ts`, `frontend/src/components/EntityEditor.tsx`, `frontend/src/index.css`
- Test: `frontend/src/api/loreOwners.test.ts`, `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: `CharacterSummary.has_avatar` + `api.imageUrl`; `Portrait` (Task 7).
- Produces: `LoreOwner` gains `avatar?: string` (set for characters with `has_avatar`, via `api.imageUrl(wid, id, default_version, "avatar")`). Lore rail rows get a right-aligned overlapped stack of 26px owner avatars (owners without avatars omitted). The detail sidebar "Owned by" chips gain a 22px avatar or initials block, padding `3px 10px 3px 3px`.

- [ ] **Step 1: Write the failing tests**

In `loreOwners.test.ts`, extend/add (mirror the file's existing mock of `./client` — it mocks `api.listCharacters`, `api.listPCs`, `api.listEntities`; add `imageUrl: (w: string, c: string, v: string, n: string) => `/api/worlds/${w}/characters/${c}/versions/${v}/images/${n}`` to the mock):

```ts
test("characters with avatars get an avatar url; others get none", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "maren", name: "Maren", default_version: "v1", has_avatar: true, versions: [] },
    { id: "hedde", name: "Hedde", default_version: "v1", has_avatar: false, versions: [] },
  ]);
  (api.listPCs as any).mockResolvedValue([]);
  (api.listEntities as any).mockResolvedValue([]);
  const opts = await loreOwnerOptions("w");
  expect(opts.find((o) => o.ref === "characters:maren")?.avatar)
    .toBe("/api/worlds/w/characters/maren/versions/v1/images/avatar");
  expect(opts.find((o) => o.ref === "characters:hedde")?.avatar).toBeUndefined();
});
```

In `EntityEditor.test.tsx` add:

```tsx
test("lore rows stack owner avatars; owners without avatars are omitted", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "maren", name: "Maren", default_version: "v1", has_avatar: true, versions: [] },
    { id: "hedde", name: "Hedde", default_version: "v1", has_avatar: false, versions: [] },
  ]);
  (api.listEntities as any).mockResolvedValue([
    { id: "smuggling", name: "Smuggling", owners: "characters:maren, characters:hedde" },
  ]);
  const { container } = render(<EntityEditor wid="w" kind="lore" />);
  await screen.findByText("Smuggling");
  await waitFor(() => expect(container.querySelectorAll(".owner-stack-img")).toHaveLength(1));
  expect(container.querySelector(".owner-stack-img")).toHaveAttribute("title", "Maren");
});

test("lore detail owner chips include an avatar or initials", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "maren", name: "Maren Voss", default_version: "v1", has_avatar: false, versions: [] },
  ]);
  (api.listEntities as any).mockResolvedValue([{ id: "smuggling", name: "Smuggling", owners: "characters:maren" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "smuggling", name: "Smuggling", owners: "characters:maren" }, body: "quiet boats" });
  render(<EntityEditor wid="w" kind="lore" />);
  fireEvent.click(await screen.findByText("Smuggling"));
  await screen.findByText("quiet boats");
  expect(await screen.findByText("MV")).toBeInTheDocument(); // initials inside the owner chip
});
```

(The lore rail groups rows by owner, so `Smuggling` may render once per owner group — if `findByText` throws on multiples, use `findAllByText` and take the first.)

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/api/loreOwners.test.ts src/components/EntityEditor.test.tsx`
Expected: new tests FAIL.

- [ ] **Step 3: Implement**

`loreOwners.ts`:

```ts
export type LoreOwner = { ref: string; label: string; kind: "characters" | "pcs" | "locations"; avatar?: string };

export async function loreOwnerOptions(wid: string): Promise<LoreOwner[]> {
  const [chars, pcs, locs] = await Promise.all([
    api.listCharacters(wid),
    api.listPCs(wid),
    api.listEntities({ kind: "world", id: wid }, "locations"),
  ]);
  return [
    ...chars.map((c) => ({
      ref: `characters:${c.id}`, label: c.name, kind: "characters" as const,
      ...(c.has_avatar ? { avatar: api.imageUrl(wid, c.id, c.default_version, "avatar") } : {}),
    })),
    ...pcs.map((p) => ({ ref: `pcs:${p.id}`, label: p.name, kind: "pcs" as const })),
    ...locs.map((l) => ({ ref: `locations:${l.id}`, label: l.name, kind: "locations" as const })),
  ];
}
```

`EntityEditor.tsx`:

1. `import { Portrait } from "./Portrait";`
2. In the `row` const from Task 11, add the stack for lore (after `row-name`):

```tsx
      {kind === "lore" && (
        <span className="owner-stack">
          {ownersOf(e).map((ref) => {
            const o = ownerOpts.find((x) => x.ref === ref);
            return o?.avatar ? (
              <img key={ref} className="owner-stack-img" alt="" title={o.label} src={o.avatar}
                   onError={(ev) => { (ev.currentTarget as HTMLImageElement).style.display = "none"; }} />
            ) : null;
          })}
        </span>
      )}
```

3. Owner chips in the detail sidebar — replace the chip button:

```tsx
                      {owners.map((ref) => (
                        <button key={ref} className="chip owner-chip" onClick={() => onOpenOwner?.(ref)}>
                          <Portrait src={ownerOpts.find((x) => x.ref === ref)?.avatar ?? null}
                                    name={ownerLabel(ref)} />
                          {ownerLabel(ref)}
                        </button>
                      ))}
```

- [ ] **Step 4: CSS**

```css
/* lore owner avatar stacks + owner chips */
.owner-stack { display: flex; margin-left: auto; flex: none; }
.owner-stack-img { width: 26px; height: 26px; object-fit: cover; border: var(--rw2) solid var(--rule); }
.owner-stack-img + .owner-stack-img { margin-left: -6px; }
.chip.owner-chip { display: inline-flex; align-items: center; gap: 6px; padding: 3px 10px 3px 3px; }
.owner-chip .portrait, .owner-chip .portrait-initials { width: 22px; height: 22px; font-size: 10px; }
```

- [ ] **Step 5: Run tests**

Run (from `frontend/`): `npx vitest run src/api/loreOwners.test.ts src/components/EntityEditor.test.tsx && npx tsc -b`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/loreOwners.ts frontend/src/api/loreOwners.test.ts frontend/src/components/EntityEditor.tsx frontend/src/components/EntityEditor.test.tsx frontend/src/index.css
git commit -m "feat(lore): owner avatar stacks on rows and avatars in owner chips"
```

---

### Task 13: Full verification

- [ ] **Step 1: Backend suite** — `backend/.venv/Scripts/python.exe -m pytest backend -q` → all PASS.
- [ ] **Step 2: Frontend suite** — from `frontend/`: `npx vitest run` → all PASS; `npx tsc -b` → clean.
- [ ] **Step 3: Grep for leftovers** — `spine` should have no remaining matches in `frontend/src` (`.spine`, `.spine-col`, `.spine-icons` removed); `gallery-grid` should have no remaining matches.
- [ ] **Step 4: Manual smoke (if a store with data is available)** — run the app, open a campaign scene: plates group runs, hover reveals ✎/↻, reroll popover anchors to the gutter; inspector shows portraits; character detail shows the shelf and promotion swaps + updates everywhere; location detail shows the header image and shelf; lore rows show owner stacks.
- [ ] **Step 5: Commit any test-only fixups** and stop for review.

---

## Notes / deviations from the design bundle

- **Locations keep the two-pane list/detail pattern** (CLAUDE.md's canonical `EntityEditor` structure); the prototype's full-page location grid is realized as image-topped cards in the rail. The README maps §4/§5 onto `EntityEditor.tsx`, which confirms this adaptation.
- **Location taglines are omitted** — entities have no tagline field; only characters do (shown on grid cards).
- **Streaming plate** uses the assistant label + initials; the real speaker is only known after the reply is persisted and split into per-speaker posts (existing behavior re-fetches the scene at stream end).
- Entity assets live at `<root>/<kind>/<eid>/assets/default/` (not `entities/<eid>/…`) so the store mirrors the character layout and ids can't collide across kinds.
- `Cache-Control: no-cache` on image responses replaces cross-page cache-bust plumbing (the query-param `avatarBust` pattern is kept within editors for same-page updates).
