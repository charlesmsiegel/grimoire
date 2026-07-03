# Character Display Tweaks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Avatar crop-focus picker (click the profile portrait to choose which square of the image displays), gallery/localized image-count badges on character cards, and markdown-rendered greetings on the character detail page.

**Architecture:** The crop is non-destructive: a focus value 0–100 (percent along the image's long axis) stored as a `focus.json` sidecar in the version's assets dir, surfaced as `avatar_focus` on the detail and list endpoints, applied in the UI as `object-position: f% f%` (with `object-fit: cover` only the long axis has slack, so one value works for both orientations). Badges come from two new counts on the list endpoint. Greetings switch from plain text to the app-standard react-markdown rendering.

**Tech Stack:** FastAPI + pytest (backend), React + TypeScript + vitest + react-markdown (frontend).

**Spec:** `docs/superpowers/specs/2026-07-03-character-display-tweaks-design.md`

## Global Constraints

- Backend tests isolate the store via `GRIMOIRE_HOME` (the `client` fixture in `backend/tests/test_routes.py` and plain `tmp_path` in store tests already do this).
- Backend test command (repo root): `backend/.venv/Scripts/python.exe -m pytest backend -q`
- Frontend commands MUST run from `frontend/`: `npx vitest run`, `npx tsc -b`. (`npx --prefix` breaks vitest config.)
- Focus values are ints clamped to 0–100; `None`/absent means "no stored focus" (center crop, today's behavior).
- Localized images are assets named `embed-<hash>`; gallery images are `gallery_<n>`; the avatar asset is named `avatar`.

---

### Task 1: Assets store — focus sidecar

**Files:**
- Modify: `backend/src/grimoire/store/assets.py`
- Test: `backend/tests/test_assets_store.py`

**Interfaces:**
- Produces: `assets.read_focus(root, cid, vid, base="characters") -> int | None`, `assets.write_focus(root, cid, vid, focus: int, base="characters") -> None` (clamps to 0–100), `assets.clear_focus(root, cid, vid, base="characters") -> None`. `put_image`/`delete_image` on the avatar and `promote_image` clear any stored focus. `list_images` only lists real image files (the sidecar never appears).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_assets_store.py`:

```python
def test_focus_round_trip_and_clamp(tmp_path):
    assert assets.read_focus(tmp_path, "sera", "default") is None
    assets.write_focus(tmp_path, "sera", "default", 62)
    assert assets.read_focus(tmp_path, "sera", "default") == 62
    assets.write_focus(tmp_path, "sera", "default", 250)
    assert assets.read_focus(tmp_path, "sera", "default") == 100
    assets.clear_focus(tmp_path, "sera", "default")
    assert assets.read_focus(tmp_path, "sera", "default") is None


def test_focus_sidecar_not_listed_as_image(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.write_focus(tmp_path, "sera", "default", 30)
    assert assets.list_images(tmp_path, "sera", "default") == [{"name": "avatar", "ext": "png"}]


def test_focus_cleared_when_avatar_changes(tmp_path):
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"a", "png")
    assets.write_focus(tmp_path, "sera", "default", 30)
    assets.put_image(tmp_path, "sera", "default", assets.AVATAR, b"b", "png")  # re-upload clears
    assert assets.read_focus(tmp_path, "sera", "default") is None

    assets.write_focus(tmp_path, "sera", "default", 30)
    assets.put_image(tmp_path, "sera", "default", "gallery_1", b"g", "png")  # non-avatar keeps it
    assert assets.read_focus(tmp_path, "sera", "default") == 30

    assets.promote_image(tmp_path, "sera", "default", "gallery_1")  # promote clears
    assert assets.read_focus(tmp_path, "sera", "default") is None

    assets.write_focus(tmp_path, "sera", "default", 30)
    assets.delete_image(tmp_path, "sera", "default", assets.AVATAR)  # delete clears
    assert assets.read_focus(tmp_path, "sera", "default") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run (repo root): `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_assets_store.py -q`
Expected: 3 FAILs with `AttributeError: module 'grimoire.store.assets' has no attribute 'read_focus'` (and similar).

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/assets.py`:

Add `import json` below `from pathlib import Path`, and a constant next to `AVATAR`:

```python
import json
from pathlib import Path

AVATAR = "avatar"
FOCUS_FILE = "focus.json"
_EXTS = {"png", "jpg", "jpeg", "gif", "webp"}
```

Change the file filter in `list_images` (line ~55) so only allowlisted image extensions are listed:

```python
        if p.is_file() and _norm_ext(p.suffix):
            out.append({"name": p.stem, "ext": p.suffix.lstrip(".").lower()})
```

Add the focus functions after `list_images`:

```python
def read_focus(root: Path, cid: str, vid: str, base: str = "characters") -> int | None:
    """Avatar crop focus: 0-100 along the image's long axis; None = center."""
    if not (_safe(cid) and _safe(vid)):
        return None
    p = _dir(root, cid, vid, base) / FOCUS_FILE
    if not p.exists():
        return None
    try:
        val = json.loads(p.read_text(encoding="utf-8")).get(AVATAR)
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return max(0, min(100, int(val)))


def write_focus(root: Path, cid: str, vid: str, focus: int, base: str = "characters") -> None:
    if not (_safe(cid) and _safe(vid)):
        raise ValueError("unsafe image id")
    d = _dir(root, cid, vid, base)
    d.mkdir(parents=True, exist_ok=True)
    (d / FOCUS_FILE).write_text(json.dumps({AVATAR: max(0, min(100, int(focus)))}),
                                encoding="utf-8")


def clear_focus(root: Path, cid: str, vid: str, base: str = "characters") -> None:
    if not (_safe(cid) and _safe(vid)):
        return
    p = _dir(root, cid, vid, base) / FOCUS_FILE
    if p.exists():
        p.unlink()
```

Clear the focus when the avatar's pixels change. At the end of `put_image` (before `return ext`):

```python
    (d / f"{name}.{ext}").write_bytes(data)
    if name == AVATAR:
        clear_focus(root, cid, vid, base)
    return ext
```

At the end of `delete_image`:

```python
    if d.exists():
        for p in d.glob(f"{name}.*"):
            p.unlink()
    if name == AVATAR:
        clear_focus(root, cid, vid, base)
```

At the end of `promote_image` (after `tmp.rename(...)`):

```python
    tmp.rename(d / f"{AVATAR}{src.suffix}")
    clear_focus(root, cid, vid, base)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_assets_store.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/assets.py backend/tests/test_assets_store.py
git commit -m "feat(assets): avatar crop-focus sidecar"
```

---

### Task 2: Characters store — expose focus and image counts

**Files:**
- Modify: `backend/src/grimoire/store/characters.py:161-210`
- Test: `backend/tests/test_characters_store.py`

**Interfaces:**
- Consumes: `assets.read_focus`, `assets.list_images`, `assets.AVATAR` (Task 1).
- Produces: `read_character(...)["versions"][i]["avatar_focus"]: int | None`; `list_characters(...)` rows gain `avatar_focus: int | None`, `gallery_count: int`, `localized_count: int` (counted on the default version; the avatar itself is never counted).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_characters_store.py`:

```python
def test_list_characters_counts_gallery_and_localized(tmp_path):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Sera")
    row = ch.list_characters(tmp_path)[0]
    assert (row["gallery_count"], row["localized_count"]) == (0, 0)
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"a", "png")
    row = ch.list_characters(tmp_path)[0]  # avatar alone counts as nothing
    assert (row["gallery_count"], row["localized_count"]) == (0, 0)
    assets.put_image(tmp_path, cid, vid, "gallery_1", b"g", "png")
    assets.put_image(tmp_path, cid, vid, "gallery_2", b"g", "png")
    assets.put_image(tmp_path, cid, vid, "embed-abc123def456", b"e", "png")
    row = ch.list_characters(tmp_path)[0]
    assert (row["gallery_count"], row["localized_count"]) == (2, 1)
    assert row["has_avatar"] is True


def test_avatar_focus_exposed_on_read_and_list(tmp_path):
    from grimoire.store import assets
    cid, vid = ch.create_character(tmp_path, "Sera")
    assert ch.read_character(tmp_path, cid)["versions"][0]["avatar_focus"] is None
    assert ch.list_characters(tmp_path)[0]["avatar_focus"] is None
    assets.put_image(tmp_path, cid, vid, assets.AVATAR, b"a", "png")
    assets.write_focus(tmp_path, cid, vid, 20)
    assert ch.read_character(tmp_path, cid)["versions"][0]["avatar_focus"] == 20
    assert ch.list_characters(tmp_path)[0]["avatar_focus"] == 20
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_characters_store.py -q`
Expected: 2 FAILs with `KeyError: 'gallery_count'` / `KeyError: 'avatar_focus'`.

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/characters.py`, `read_character` (line ~177): add `avatar_focus` to the version dict:

```python
        versions.append({
            "id": vid,
            "name": _version_label(card, vid),
            "card": card,
            "images": [i["name"] for i in assets.list_images(root, cid, vid)],
            "avatar_focus": assets.read_focus(root, cid, vid),
            "chub_source": chub_source,
            "is_chub": bool(chub_source) and chub.parse_full_path(chub_source) is not None,
        })
```

In `list_characters` (line ~199), derive everything from one `list_images` call (replacing the `image_path`-based `has_avatar`):

```python
            meta, _ = parse_frontmatter(_meta_path(root, cid).read_text(encoding="utf-8"))
            default = meta.get("default_version", "")
            names = [i["name"] for i in assets.list_images(root, cid, default)]
            out.append({
                "id": cid,
                "name": meta.get("name", cid),
                "default_version": default,
                "has_avatar": assets.AVATAR in names,
                "avatar_focus": assets.read_focus(root, cid, default),
                "gallery_count": sum(1 for n in names if n.startswith("gallery_")),
                "localized_count": sum(1 for n in names if n.startswith("embed-")),
                "tagline": taglines.read(root, cid),
                "versions": [{"id": v, "name": _version_label(read_card(root, cid, v), v)}
                             for v in _version_ids(root, cid)],
            })
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_characters_store.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/characters.py backend/tests/test_characters_store.py
git commit -m "feat(characters): expose avatar focus and image counts"
```

---

### Task 3: Route — PUT avatar focus

**Files:**
- Modify: `backend/src/grimoire/routes.py` (model near line 98, route after `promote_world_image` ~line 753)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.assets.write_focus`, `store.assets.image_path`, `store.assets.AVATAR` (Task 1).
- Produces: `PUT /api/worlds/{wid}/characters/{cid}/versions/{vid}/images/avatar/focus` with body `{"focus": <int>}` → `{"ok": true}`; 404 when the version has no avatar. (No conflict with `PUT .../images/{name}` — `{name}` matches a single path segment.)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_routes.py` after `test_character_image_promote_missing_404` (line ~171):

```python
def test_avatar_focus_endpoint_round_trip(client):
    wid = _world(client)
    cid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    base = f"/api/worlds/{wid}/characters/{cid}/versions/default/images"
    # no avatar yet -> 404
    assert client.put(f"{base}/avatar/focus", json={"focus": 30}).status_code == 404
    client.put(f"{base}/avatar", files={"file": ("a.png", io.BytesIO(b"img"), "image/png")})
    assert client.put(f"{base}/avatar/focus", json={"focus": 30}).json() == {"ok": True}
    detail = client.get(f"/api/worlds/{wid}/characters/{cid}").json()
    assert detail["versions"][0]["avatar_focus"] == 30
    chars = client.get(f"/api/worlds/{wid}/characters").json()
    assert chars[0]["avatar_focus"] == 30
    assert chars[0]["gallery_count"] == 0 and chars[0]["localized_count"] == 0
    # promoting a new image invalidates the crop
    client.put(f"{base}/gallery_1", files={"file": ("g.png", io.BytesIO(b"g"), "image/png")})
    client.post(f"{base}/gallery_1/promote")
    assert client.get(f"/api/worlds/{wid}/characters/{cid}").json()["versions"][0]["avatar_focus"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_avatar_focus_endpoint_round_trip -q`
Expected: FAIL — the first focus PUT returns 405/404-mismatch (route missing), assertion on `.json() == {"ok": True}` fails.

- [ ] **Step 3: Implement**

In `backend/src/grimoire/routes.py`, add the body model next to `TaglineSave` (line ~98):

```python
class AvatarFocus(BaseModel):
    focus: int
```

Add the route directly after `promote_world_image` (line ~753):

```python
@router.put("/worlds/{wid}/characters/{cid}/versions/{vid}/images/avatar/focus")
def put_world_avatar_focus(wid: str, cid: str, vid: str, body: AvatarFocus):
    root = _world_root_or_404(wid)
    if store.assets.image_path(root, cid, vid, store.assets.AVATAR) is None:
        raise HTTPException(status_code=404, detail="image not found")
    store.assets.write_focus(root, cid, vid, body.focus)
    return {"ok": True}
```

- [ ] **Step 4: Run the backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(api): PUT avatar crop focus endpoint"
```

---

### Task 4: API client — types and setAvatarFocus

**Files:**
- Modify: `frontend/src/api/client.ts:87-91` (types) and `:327` (endpoints, after `promoteImage`)

**Interfaces:**
- Consumes: the Task 3 endpoint.
- Produces: `CharacterSummary` gains `avatar_focus?: number | null; gallery_count?: number; localized_count?: number`; `CharacterDetail` versions gain `avatar_focus?: number | null`; `api.setAvatarFocus(wid: string, cid: string, vid: string, focus: number)` → `{ ok: boolean }`.

- [ ] **Step 1: Implement**

Update the types:

```ts
export type CharacterSummary = {
  id: string; name: string; default_version: string; has_avatar?: boolean;
  avatar_focus?: number | null; gallery_count?: number; localized_count?: number;
  tagline?: string; versions: VersionRef[];
};
export type CharacterDetail = {
  meta: { id: string; name: string; default_version: string; birthdate?: string };
  versions: { id: string; name: string; card: Card; images?: string[];
              avatar_focus?: number | null; chub_source?: string; is_chub?: boolean }[];
};
```

Add after `promoteImage` (line ~327):

```ts
  setAvatarFocus: (wid: string, cid: string, vid: string, focus: number) =>
    request<{ ok: boolean }>("PUT",
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/avatar/focus`, { focus }),
```

- [ ] **Step 2: Typecheck**

Run (from `frontend/`): `npx tsc -b`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(client): avatar focus + image-count fields"
```

---

### Task 5: Character-card badges

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx:678-697` (grid card), `frontend/src/index.css` (after `.char-card-tagline`, line ~516)
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `CharacterSummary.gallery_count` / `.localized_count` (Task 4).
- Produces: `.char-card-badges` row with `.chip` spans reading `N gallery` / `N localized`; a badge renders only when its count > 0.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/CharacterEditor.test.tsx`:

```tsx
test("grid cards show gallery/localized badges only when nonzero", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "a", name: "Aya", default_version: "default", has_avatar: true,
      gallery_count: 3, localized_count: 0, versions: [] },
    { id: "b", name: "Bea", default_version: "default", has_avatar: true,
      gallery_count: 0, localized_count: 2, versions: [] },
    { id: "c", name: "Cyn", default_version: "default", has_avatar: true,
      gallery_count: 0, localized_count: 0, versions: [] },
  ]);
  render(<CharacterEditor wid="w" />);
  await screen.findByText("3 gallery");
  await screen.findByText("2 localized");
  expect(screen.queryByText("0 gallery")).toBeNull();
  expect(screen.queryByText("0 localized")).toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: the new test FAILs (`Unable to find an element with the text: 3 gallery`); existing tests PASS.

- [ ] **Step 3: Implement**

In the grid card (`CharacterEditor.tsx`, inside `.char-card-main` after the tagline line 688):

```tsx
                  {c.tagline ? <span className="char-card-tagline">{c.tagline}</span> : null}
                  {((c.gallery_count ?? 0) > 0 || (c.localized_count ?? 0) > 0) && (
                    <span className="char-card-badges">
                      {(c.gallery_count ?? 0) > 0 && <span className="chip">{c.gallery_count} gallery</span>}
                      {(c.localized_count ?? 0) > 0 && <span className="chip">{c.localized_count} localized</span>}
                    </span>
                  )}
```

In `frontend/src/index.css`, after the `.char-card-tagline` rule (line ~516):

```css
.char-card-badges { display: flex; gap: 6px; padding: 6px 14px 4px; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/index.css frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(characters): image-count badges on grid cards"
```

---

### Task 6: Markdown-rendered greetings

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx:1-6` (imports), `:818-833` (detail text fields + greetings)
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: nothing new — `react-markdown`/`remark-gfm` are existing deps (see `GreetingEditor.tsx:143-144`).
- Produces: `first_mes` and each alternate greeting render inside `.detail-rendered` via `<Markdown remarkPlugins={[remarkGfm]}>`; all other card text fields stay plain.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/components/CharacterEditor.test.tsx`:

```tsx
test("first message and alternate greetings render markdown images; other fields stay plain", async () => {
  const card = {
    ...CARD,
    data: {
      ...CARD.data,
      first_mes: "hello ![scene](/img/w/seraphine/default/embed-abc)",
      alternate_greetings: ["alt ![alt-pic](/img/w/seraphine/default/embed-def)"],
      description: "plain **stars** stay literal",
    },
  };
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card, images: ["avatar"] }],
  });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  await screen.findByRole("img", { name: "scene" });
  await screen.findByRole("img", { name: "alt-pic" });
  expect(screen.getByText("plain **stars** stay literal")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: new test FAILs (`Unable to find role="img"` for "scene").

- [ ] **Step 3: Implement**

Add imports at the top of `CharacterEditor.tsx`:

```tsx
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
```

Replace the detail text-fields loop (line ~818-826):

```tsx
            {TEXT_FIELDS.map((f) => {
              const val = (card.data[f.key] as string) ?? "";
              if (!val.trim()) return null;
              return (
                <div className="detail-field" key={f.key}>
                  <div className="section-label">{f.label}</div>
                  {f.key === "first_mes"
                    ? <div className="detail-rendered"><Markdown remarkPlugins={[remarkGfm]}>{val}</Markdown></div>
                    : <div className="detail-text">{val}</div>}
                </div>
              );
            })}
```

Replace the alternate-greetings block (line ~828-833):

```tsx
            {greetings.length > 0 && (
              <div className="detail-field">
                <div className="section-label">Alternate greetings</div>
                {greetings.map((g, i) => (
                  <blockquote className="greeting-quote" key={i}>
                    <div className="detail-rendered"><Markdown remarkPlugins={[remarkGfm]}>{g}</Markdown></div>
                  </blockquote>
                ))}
              </div>
            )}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(characters): render greetings as markdown"
```

---

### Task 7: Avatar crop picker + object-position rendering

**Files:**
- Create: `frontend/src/components/AvatarFocusPicker.tsx`
- Modify: `frontend/src/components/CharacterEditor.tsx` (state, detail head ~line 716, grid avatar ~line 683), `frontend/src/components/Portrait.tsx`, `frontend/src/index.css`
- Test: `frontend/src/components/CharacterEditor.test.tsx`

**Interfaces:**
- Consumes: `api.setAvatarFocus` (Task 4), `avatar_focus` on detail versions and summaries (Tasks 2–4).
- Produces: `AvatarFocusPicker({ src, initial, onSave, onClose })` — modal with a draggable crop window and an `aria-label="Crop position"` range input; Save calls `onSave(focus: number)`. `Portrait` gains optional `focus?: number | null`. Detail profile portrait becomes a button `aria-label="Adjust avatar crop"`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/CharacterEditor.test.tsx`, add `setAvatarFocus: vi.fn(),` to the `vi.mock` api object (after `promoteImage: vi.fn(),` on line 9) and `(api.setAvatarFocus as any).mockResolvedValue({ ok: true });` in `beforeEach` (after the `promoteImage` mock on line 46). Then append:

```tsx
test("clicking the profile avatar opens the crop picker and saves the focus", async () => {
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: ["avatar"], avatar_focus: null }],
  });
  render(<CharacterEditor wid="w" />);
  fireEvent.click(await screen.findByText("Seraphine"));
  fireEvent.click(await screen.findByRole("button", { name: /adjust avatar crop/i }));
  const slider = await screen.findByLabelText("Crop position");
  fireEvent.change(slider, { target: { value: "80" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.setAvatarFocus).toHaveBeenCalledWith("w", "seraphine", "default", 80));
});

test("stored focus is applied as object-position on detail and grid avatars", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", has_avatar: true,
      avatar_focus: 25, versions: [] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "seraphine", name: "Seraphine", default_version: "default" },
    versions: [{ id: "default", name: "default", card: CARD, images: ["avatar"], avatar_focus: 25 }],
  });
  render(<CharacterEditor wid="w" />);
  await screen.findByText("Seraphine");
  const cardImg = document.querySelector(".char-card-avatar") as HTMLElement;
  expect(cardImg.style.objectPosition).toBe("25% 25%");
  fireEvent.click(screen.getByText("Seraphine"));
  await screen.findByRole("button", { name: /adjust avatar crop/i });
  const detailImg = document.querySelector(".detail-avatar") as HTMLElement;
  expect(detailImg.style.objectPosition).toBe("25% 25%");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: both new tests FAIL (no button named "Adjust avatar crop"; `objectPosition` empty).

- [ ] **Step 3: Create the picker component**

Create `frontend/src/components/AvatarFocusPicker.tsx`:

```tsx
import { useRef, useState } from "react";

/** Modal for choosing which square of the avatar shows in square crops.
 *  Focus is 0-100 along the image's long axis; object-fit: cover has no
 *  slack on the short axis, so one number fully describes the crop. */
export function AvatarFocusPicker({ src, initial, onSave, onClose }:
  { src: string; initial: number; onSave: (focus: number) => void; onClose: () => void }) {
  const [focus, setFocus] = useState(initial);
  const [portrait, setPortrait] = useState(true);
  const [squareFrac, setSquareFrac] = useState(1); // short side / long side
  const boxRef = useRef<HTMLDivElement>(null);

  function onLoad(e: React.SyntheticEvent<HTMLImageElement>) {
    const img = e.currentTarget;
    if (img.naturalWidth > 0 && img.naturalHeight > 0) {
      setPortrait(img.naturalHeight >= img.naturalWidth);
      setSquareFrac(Math.min(img.naturalWidth, img.naturalHeight) /
                    Math.max(img.naturalWidth, img.naturalHeight));
    }
  }

  function fromPointer(e: React.PointerEvent) {
    const rect = boxRef.current?.getBoundingClientRect();
    if (!rect || !rect.width || !rect.height) return;
    const frac = portrait
      ? (e.clientY - rect.top) / rect.height
      : (e.clientX - rect.left) / rect.width;
    setFocus(Math.round(Math.max(0, Math.min(1, frac)) * 100));
  }

  // the window's leading edge sits at focus% of the long-axis slack
  const lead = (focus / 100) * (1 - squareFrac) * 100;
  const windowStyle: React.CSSProperties = portrait
    ? { top: `${lead}%`, left: 0, width: "100%", height: `${squareFrac * 100}%` }
    : { left: `${lead}%`, top: 0, height: "100%", width: `${squareFrac * 100}%` };

  return (
    <div className="tagline-modal-backdrop" role="dialog" aria-label="Adjust avatar crop">
      <div className="tagline-modal focus-modal">
        <h3>Avatar crop</h3>
        <p className="field-hint">Drag on the image (or use the slider) to choose which square is shown.</p>
        <div className="focus-scroll">
          <div className="focus-preview" ref={boxRef}
               onPointerDown={fromPointer}
               onPointerMove={(e) => { if (e.buttons) fromPointer(e); }}>
            <img src={src} alt="avatar full view" draggable={false} onLoad={onLoad} />
            <div className="focus-window" style={windowStyle} />
          </div>
        </div>
        <input type="range" min={0} max={100} value={focus} aria-label="Crop position"
               onChange={(e) => setFocus(Number(e.target.value))} />
        <div className="form-actions">
          <button className="primary" type="button" onClick={() => onSave(focus)}>Save</button>
          <button className="subtle" type="button" onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Wire it into CharacterEditor**

In `CharacterEditor.tsx`:

Import it (with the other component imports):

```tsx
import { AvatarFocusPicker } from "./AvatarFocusPicker";
```

Add a module-level helper above the component (near `describeChubResult`):

```tsx
function focusStyle(f?: number | null): React.CSSProperties | undefined {
  return f == null ? undefined : { objectPosition: `${f}% ${f}%` };
}
```

Add state next to the other useState calls:

```tsx
  const [cropOpen, setCropOpen] = useState(false);
```

Add the current-version focus next to the `hasAvatar` derivation (line ~85):

```tsx
  const avatarFocus = detail?.versions.find((v) => v.id === vid)?.avatar_focus ?? null;
```

Add a save handler next to `promote` (line ~396):

```tsx
  async function saveFocus(f: number) {
    if (!detail) return;
    setCropOpen(false);
    setError(null);
    try {
      await api.setAvatarFocus(wid, detail.meta.id, vid, f);
      await refreshVersion();
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

In the grid card (line ~683), apply the focus style:

```tsx
                    ? <img className="char-card-avatar" alt="" style={focusStyle(c.avatar_focus)}
                           src={avatarSrc(c.id, c.default_version, true)} />
```

In the detail head (line ~716-721), make the portrait a button and render the modal:

```tsx
            <div className="detail-head">
              {hasAvatar
                ? <button className="avatar-crop-btn" type="button" aria-label="Adjust avatar crop"
                          title="Adjust avatar crop" onClick={() => setCropOpen(true)}>
                    <img className="detail-avatar" alt="" style={focusStyle(avatarFocus)}
                         src={avatarSrc(detail.meta.id, vid, true)} />
                  </button>
                : <div className="initials-avatar detail" aria-hidden>
                    {(card.data.name || detail.meta.name).split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("")}
                  </div>}
```

Directly inside the detail-mode `<div className="character-editor">` (next to the `TaglinePrompt` block, line ~706):

```tsx
        {cropOpen && hasAvatar && (
          <AvatarFocusPicker src={avatarSrc(detail.meta.id, vid, true)}
                             initial={avatarFocus ?? 50}
                             onSave={saveFocus}
                             onClose={() => setCropOpen(false)} />
        )}
```

Also close the picker when switching versions — in `loadVersion` (line ~91), add:

```tsx
    setCropOpen(false);
```

- [ ] **Step 5: Portrait prop and CSS**

`frontend/src/components/Portrait.tsx` — add the optional prop (no callers change yet; campaign data doesn't carry focus):

```tsx
export function Portrait({ src, name, focus }:
  { src: string | null; name: string; focus?: number | null }) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [src]);
  if (!src || broken) {
    return <span className="portrait-initials" aria-hidden>{initialsOf(name)}</span>;
  }
  return <img className="portrait" alt={`${name} portrait`} src={src}
              style={focus == null ? undefined : { objectPosition: `${focus}% ${focus}%` }}
              onError={() => setBroken(true)} />;
}
```

`frontend/src/index.css` — after the `.tagline-modal` rules (line ~687):

```css
.avatar-crop-btn { padding: 0; border: none; background: transparent; cursor: pointer; flex: none; display: block; }
.focus-modal { width: min(420px, 92vw); }
.focus-scroll { max-height: 60vh; overflow-y: auto; margin: 8px 0; }
.focus-preview { position: relative; cursor: crosshair; touch-action: none; }
.focus-preview img { width: 100%; display: block; user-select: none; }
.focus-window { position: absolute; border: 2px solid var(--accent); box-shadow: 0 0 0 999px rgba(0, 0, 0, 0.45); pointer-events: none; }
.focus-modal input[type="range"] { width: 100%; }
```

- [ ] **Step 6: Run tests and typecheck**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx` then `npx tsc -b`
Expected: all tests PASS, no type errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/AvatarFocusPicker.tsx frontend/src/components/CharacterEditor.tsx frontend/src/components/Portrait.tsx frontend/src/index.css frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(characters): avatar crop-focus picker"
```

---

### Task 8: Full verification

- [ ] **Step 1: Backend suite**

Run (repo root): `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS.

- [ ] **Step 2: Frontend suite + typecheck**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: all PASS, no type errors.

- [ ] **Step 3: Commit any stragglers**

Only if the verification steps required fixes; otherwise nothing to commit.
