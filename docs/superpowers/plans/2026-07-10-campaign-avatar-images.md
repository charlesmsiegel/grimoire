# Campaign-Scoped Character Avatar Images Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a character viewed in campaign scope — especially one with no
avatar yet — upload/replace/remove its avatar, manage its gallery, and adjust
its avatar crop, the same as a world-scoped character can today.

**Architecture:** Character image mutations (`put_image`, `delete_image`,
`promote_image`, `write_focus`, `copy_to_character`) already operate on a
generic `root` path. Mirror the existing world-scoped image routes with new
campaign-scoped ones (`_campaign_root_or_404(cid)` instead of
`_world_root_or_404(wid)`) — same pattern already used for location/lore
("entity") images. Then make the frontend API client and `CharacterEditor`
pass `scope` instead of a bare `wid` for these calls, and drop the
`worldScope`-only gating on the avatar-block, gallery shelf, and crop
control.

**Tech Stack:** FastAPI + pytest (backend), React/TypeScript + Vitest
(frontend).

## Global Constraints

- Design doc: `docs/superpowers/specs/2026-07-10-campaign-avatar-images-design.md`.
- No changes to `store/assets.py` or `store/image_subjects.py` — both are
  already root-generic.
- PCs are explicitly out of scope (tracked as
  [issue #948](https://github.com/charlesmsiegel/grimoire/issues/948)).
- `localizeControls` and version-management actions (Import version, Delete,
  Download version from URL, "+ New character") stay `worldScope`-gated —
  untouched by this plan.
- Backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Frontend tests: from `frontend/`, `npx vitest run`; type check `npx tsc -b`.
  Vitest must be run **from** `frontend/` (not `npx --prefix frontend`), or
  `vitest.config.ts` is skipped and `globals`/mocks break.

---

### Task 1: Backend — campaign-scoped character image routes

**Files:**
- Modify: `backend/src/grimoire/routes.py:1635-1642` (insert 4 new routes
  between `get_campaign_image` and `_campaign_wroot`)
- Test: `backend/tests/test_routes.py` (add 4 new tests near the existing
  `test_campaign_image_route_serves_copied_avatar` at line 154)

**Interfaces:**
- Consumes: `store.assets.put_image/delete_image/promote_image/write_focus/
  image_path` (`backend/src/grimoire/store/assets.py`, all `(root, cid, vid,
  name, ...)`, root-generic); `store.image_subjects.copy_to_character(root,
  gid, name, cid, vid, slot)`; `_campaign_root_or_404(cid)`
  (`routes.py:1019`); pydantic models `AvatarFocus` and `CopyFromGreeting`
  (already imported/used by the world routes at `routes.py:826-832,
  978-988`).
- Produces: routes `PUT/DELETE/POST .../images/{name}[/promote]`, `PUT
  .../images/avatar/focus`, `POST .../images/copy-from-greeting` under
  `/campaigns/{cid}/characters/{char}/versions/{vid}/...` — consumed by
  Task 2's `client.ts` changes.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_routes.py`, directly after
`test_campaign_image_route_serves_copied_avatar` (ends at line 163):

```python
def test_campaign_character_image_routes_isolated(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    world_base = f"/api/worlds/{wid}/characters/{chid}/versions/default/images"
    client.put(f"{world_base}/avatar", files={"file": ("a.png", io.BytesIO(b"world-bytes"), "image/png")})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})

    camp_base = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images"
    r = client.put(f"{camp_base}/avatar", files={"file": ("b.png", io.BytesIO(b"campaign-bytes"), "image/png")})
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}

    # campaign copy changed; world's shared copy untouched
    assert client.get(f"{camp_base}/avatar").content == b"campaign-bytes"
    assert client.get(f"{world_base}/avatar").content == b"world-bytes"

    assert client.delete(f"{camp_base}/avatar").status_code == 200
    assert client.get(f"{camp_base}/avatar").status_code == 404
    assert client.get(f"{world_base}/avatar").content == b"world-bytes"


def test_campaign_character_image_promote_swaps_avatar(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    base = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images"
    client.put(f"{base}/avatar", files={"file": ("a.png", io.BytesIO(b"old"), "image/png")})
    client.put(f"{base}/gallery_1", files={"file": ("g.png", io.BytesIO(b"new"), "image/png")})

    assert client.post(f"{base}/gallery_1/promote").status_code == 200
    assert client.get(f"{base}/avatar").content == b"new"
    assert client.get(f"{base}/gallery_1").content == b"old"
    assert client.post(f"{base}/gallery_9/promote").status_code == 404


def test_campaign_avatar_focus_endpoint_round_trip(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Sera"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    base = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images"

    assert client.put(f"{base}/avatar/focus", json={"focus": 30}).status_code == 404
    client.put(f"{base}/avatar", files={"file": ("a.png", io.BytesIO(b"img"), "image/png")})
    assert client.put(f"{base}/avatar/focus", json={"focus": 30}).json() == {"ok": True}
    detail = client.get(f"/api/campaigns/{cid}/characters/{chid}").json()
    assert detail["versions"][0]["avatar_focus"] == 30


def test_campaign_copy_image_from_greeting(client):
    wid, cid = _campaign(client)
    chid = client.post(f"/api/worlds/{wid}/characters", json={"name": "Mira"}).json()["character"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S1"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                json={"kind": "characters", "id": chid, "version": "default", "role": "npc"})
    gid = client.post(f"/api/campaigns/{cid}/greetings",
                      json={"name": "Opener", "character": chid, "version": "default"}).json()["id"]
    root = store.campaigns.campaign_root(cid)
    store.assets.put_image(root, gid, "default", "embed-abc123def456", b"art", "png", base="greetings")

    copy_url = f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/copy-from-greeting"
    r = client.post(copy_url, json={"gid": gid, "name": "embed-abc123def456", "slot": "avatar"})
    assert r.status_code == 200 and r.json() == {"name": "avatar", "ext": "png"}
    assert client.get(f"/api/campaigns/{cid}/characters/{chid}/versions/default/images/avatar").content == b"art"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "campaign_character_image or campaign_avatar_focus or campaign_copy_image_from_greeting"`
Expected: FAIL — 405/404 responses where the new tests expect 200 (routes don't exist yet).

- [ ] **Step 3: Implement the campaign-scoped image routes**

In `backend/src/grimoire/routes.py`, insert immediately after
`get_campaign_image` (line 1637) and before `def _campaign_wroot` (line 1640):

```python
@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
async def put_campaign_image(cid: str, char: str, vid: str, name: str, file: UploadFile = File(...)):
    root = _campaign_root_or_404(cid)
    data = await file.read()
    fn = file.filename or ""
    ext = fn.rsplit(".", 1)[-1] if "." in fn else ""
    try:
        stored = store.assets.put_image(root, char, vid, name, data, ext)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"name": name, "ext": stored}


@router.delete("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}")
def delete_campaign_image(cid: str, char: str, vid: str, name: str):
    store.assets.delete_image(_campaign_root_or_404(cid), char, vid, name)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/{name}/promote")
def promote_campaign_image(cid: str, char: str, vid: str, name: str):
    try:
        store.assets.promote_image(_campaign_root_or_404(cid), char, vid, name)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="image not found")
    return {"ok": True}


@router.put("/campaigns/{cid}/characters/{char}/versions/{vid}/images/avatar/focus")
def put_campaign_avatar_focus(cid: str, char: str, vid: str, body: AvatarFocus):
    root = _campaign_root_or_404(cid)
    if store.assets.image_path(root, char, vid, store.assets.AVATAR) is None:
        raise HTTPException(status_code=404, detail="image not found")
    store.assets.write_focus(root, char, vid, body.focus)
    return {"ok": True}


@router.post("/campaigns/{cid}/characters/{char}/versions/{vid}/images/copy-from-greeting")
def post_copy_campaign_image_from_greeting(cid: str, char: str, vid: str, body: CopyFromGreeting):
    root = _campaign_root_or_404(cid)
    try:
        stored = store.image_subjects.copy_to_character(root, body.gid, body.name, char, vid, body.slot)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="source image not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    p = store.assets.image_path(root, char, vid, stored)
    return {"name": stored, "ext": p.suffix.lstrip(".").lower() if p else ""}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "campaign_character_image or campaign_avatar_focus or campaign_copy_image_from_greeting"`
Expected: PASS (4 passed)

- [ ] **Step 5: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, no regressions.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(backend): campaign-scoped character image routes"
```

---

### Task 2: Frontend — scope-aware image API and CharacterEditor wiring

**Files:**
- Modify: `frontend/src/api/client.ts:388-401` (`putImage`, `deleteImage`,
  `promoteImage`, `setAvatarFocus`), `client.ts:478-481` (`copyGreetingImage`)
- Modify: `frontend/src/components/CharacterEditor.tsx:431,444,464,475,487,502`
  (call sites)
- Test: `frontend/src/components/CharacterEditor.test.tsx:106,202,809,879-880`

**Interfaces:**
- Consumes: `EntityScope` (`client.ts:80`, `{ kind: "world" | "campaign";
  id: string }`), `entityBase(scope)` (`client.ts:228-230`), the routes from
  Task 1.
- Produces: `api.putImage(scope, cid, vid, name, file)`,
  `api.deleteImage(scope, cid, vid, name)`, `api.promoteImage(scope, cid,
  vid, name)`, `api.setAvatarFocus(scope, cid, vid, focus)`,
  `api.copyGreetingImage(scope, cid, vid, body)` — consumed by Task 3's
  UI-gating changes (which reuse these same call sites, already fixed here).

- [ ] **Step 1: Update the failing frontend tests to expect scope objects**

In `frontend/src/components/CharacterEditor.test.tsx`:

Line 106 — change:
```typescript
  await waitFor(() => expect(api.promoteImage).toHaveBeenCalledWith("w", "seraphine", "default", "gallery_1"));
```
to:
```typescript
  await waitFor(() => expect(api.promoteImage).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine", "default", "gallery_1"));
```

Line 202 — change:
```typescript
  await waitFor(() => expect(api.putImage).toHaveBeenCalledWith("w", "seraphine", "default", "avatar", expect.any(File)));
```
to:
```typescript
  await waitFor(() => expect(api.putImage).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine", "default", "avatar", expect.any(File)));
```

Line 809 — change:
```typescript
  await waitFor(() => expect(api.setAvatarFocus).toHaveBeenCalledWith("w", "seraphine", "default", 80));
```
to:
```typescript
  await waitFor(() => expect(api.setAvatarFocus).toHaveBeenCalledWith({ kind: "world", id: "w" }, "seraphine", "default", 80));
```

Lines 879-880 — change:
```typescript
  await waitFor(() => expect(api.copyGreetingImage).toHaveBeenCalledWith(
    "w", "seraphine", "default", { gid: "sol-1", name: "embed-a", slot: "avatar" }));
```
to:
```typescript
  await waitFor(() => expect(api.copyGreetingImage).toHaveBeenCalledWith(
    { kind: "world", id: "w" }, "seraphine", "default", { gid: "sol-1", name: "embed-a", slot: "avatar" }));
```

- [ ] **Step 2: Run the frontend tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: FAIL on those 4 assertions — mocks still receive bare `"w"` from
the unchanged component code.

- [ ] **Step 3: Make the API client scope-aware**

In `frontend/src/api/client.ts`, replace lines 388-401:

```typescript
  putImage: (wid: string, cid: string, vid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ name: string; ext: string }>(
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}`, form, "PUT");
  },
  deleteImage: (wid: string, cid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}`),
  promoteImage: (wid: string, cid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/${name}/promote`),
  setAvatarFocus: (wid: string, cid: string, vid: string, focus: number) =>
    request<{ ok: boolean }>("PUT",
      `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/avatar/focus`, { focus }),
```

with:

```typescript
  putImage: (scope: EntityScope, cid: string, vid: string, name: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return requestForm<{ name: string; ext: string }>(
      `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${name}`, form, "PUT");
  },
  deleteImage: (scope: EntityScope, cid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("DELETE", `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${name}`),
  promoteImage: (scope: EntityScope, cid: string, vid: string, name: string) =>
    request<{ ok: boolean }>("POST", `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/${name}/promote`),
  setAvatarFocus: (scope: EntityScope, cid: string, vid: string, focus: number) =>
    request<{ ok: boolean }>("PUT",
      `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/avatar/focus`, { focus }),
```

Then replace lines 478-481:

```typescript
  copyGreetingImage: (wid: string, cid: string, vid: string,
                      body: { gid: string; name: string; slot: "avatar" | "gallery" }) =>
    request<{ name: string; ext: string }>(
      "POST", `/api/worlds/${wid}/characters/${cid}/versions/${vid}/images/copy-from-greeting`, body),
```

with:

```typescript
  copyGreetingImage: (scope: EntityScope, cid: string, vid: string,
                      body: { gid: string; name: string; slot: "avatar" | "gallery" }) =>
    request<{ name: string; ext: string }>(
      "POST", `${entityBase(scope)}/characters/${cid}/versions/${vid}/images/copy-from-greeting`, body),
```

- [ ] **Step 4: Update the CharacterEditor call sites**

In `frontend/src/components/CharacterEditor.tsx`:

Line 431, in `onAvatar` — change `api.putImage(wid, detail.meta.id, vid, "avatar", file)` to `api.putImage(scope, detail.meta.id, vid, "avatar", file)`.

Line 444, in `removeAvatar` — change `api.deleteImage(wid, detail.meta.id, vid, "avatar")` to `api.deleteImage(scope, detail.meta.id, vid, "avatar")`.

Line 464, in `promote` — change `api.promoteImage(wid, detail.meta.id, vid, name)` to `api.promoteImage(scope, detail.meta.id, vid, name)`.

Line 475, in `copyFromGreeting` — change `api.copyGreetingImage(wid, detail.meta.id, vid, { gid: a.gid, name: a.name, slot })` to `api.copyGreetingImage(scope, detail.meta.id, vid, { gid: a.gid, name: a.name, slot })`.

Line 487, in `saveFocus` — change `api.setAvatarFocus(wid, detail.meta.id, vid, f)` to `api.setAvatarFocus(scope, detail.meta.id, vid, f)`.

Line 502, in `onShelfAdd` — change `api.putImage(wid, detail.meta.id, vid, next, file)` to `api.putImage(scope, detail.meta.id, vid, next, file)`.

- [ ] **Step 5: Run the frontend tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: PASS

- [ ] **Step 6: Type-check**

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(frontend): scope-aware character image API calls"
```

---

### Task 3: Frontend — unlock avatar/gallery/crop controls in campaign scope

**Files:**
- Modify: `frontend/src/components/CharacterEditor.tsx:829,841-852,960,964-968,1092`
- Test: `frontend/src/components/CharacterEditor.test.tsx` (rewrite the test
  at line 927; add two new campaign-scope tests)

**Interfaces:**
- Consumes: `scope`-aware `api.putImage`/`deleteImage`/`promoteImage`/
  `setAvatarFocus` from Task 2; `worldScope` (`CharacterEditor.tsx:46`,
  `scope.kind === "world"`) — still used to gate version-management actions,
  just no longer gates image controls.
- Produces: none (leaf UI change).

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/CharacterEditor.test.tsx`, replace the test at
line 927 (`"campaign scope: the avatar crop control is absent (world-side
mutation)"`):

```typescript
test("campaign scope: the avatar crop control is absent (world-side mutation)", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [{ id: "young", name: "Young", images: ["avatar"],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } }],
  });
  (api.listAppearances as any).mockResolvedValue([]);
  const { container } = render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByText("Mara"));
  await screen.findByText("Images");
  expect(screen.queryByRole("button", { name: "Adjust avatar crop" })).toBeNull();
  expect(container.querySelector("img.detail-avatar")).not.toBeNull();  // still displayed read-only
});
```

with:

```typescript
test("campaign scope: the avatar crop control mutates the campaign's own copy", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [{ id: "young", name: "Young", images: ["avatar"], avatar_focus: null,
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } }],
  });
  (api.listAppearances as any).mockResolvedValue([]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByText("Mara"));
  fireEvent.click(await screen.findByRole("button", { name: /adjust avatar crop/i }));
  const slider = await screen.findByLabelText("Crop position");
  fireEvent.change(slider, { target: { value: "80" } });
  fireEvent.click(screen.getByRole("button", { name: /^save$/i }));
  await waitFor(() => expect(api.setAvatarFocus).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "mara", "young", 80));
});

test("campaign scope: uploading an avatar calls the scope-aware endpoint", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [{ id: "young", name: "Young", images: [],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } }],
  });
  (api.listAppearances as any).mockResolvedValue([]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  await openEditForm();
  const input = screen.getByLabelText("Upload avatar");
  fireEvent.change(input, { target: { files: [new File(["x"], "a.png", { type: "image/png" })] } });
  await waitFor(() => expect(api.putImage).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "mara", "young", "avatar", expect.any(File)));
});

test("campaign scope: gallery shelf allows adding an image and promoting to avatar", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "young", versions: [] },
  ]);
  (api.readCharacter as any).mockResolvedValue({
    meta: { id: "mara", name: "Mara", default_version: "young" },
    versions: [{ id: "young", name: "Young", images: ["avatar", "gallery_1"],
                 card: { spec: "chara_card_v3", spec_version: "3.0", data: { name: "Mara" } } }],
  });
  (api.listAppearances as any).mockResolvedValue([]);
  render(<CharacterEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  fireEvent.click(await screen.findByText("Mara"));
  await screen.findByText("Images");
  fireEvent.click(screen.getByRole("button", { name: /set as avatar/i }));
  await waitFor(() => expect(api.promoteImage).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "mara", "young", "gallery_1"));
  expect(screen.getByRole("button", { name: /\+ add/i })).toBeInTheDocument();
});
```

(`openEditForm` is the existing helper at `CharacterEditor.test.tsx:82`,
already in scope for this file.)

- [ ] **Step 2: Run the tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: FAIL — the rewritten crop test and the two new tests fail because
the controls are still `worldScope`-gated and don't render in campaign
scope.

- [ ] **Step 3: Unlock the avatar crop control**

In `frontend/src/components/CharacterEditor.tsx`, line 829, change:
```tsx
        {worldScope && cropOpen && hasAvatar && (
```
to:
```tsx
        {cropOpen && hasAvatar && (
```

Lines 841-852, change:
```tsx
              {hasAvatar && worldScope
                ? <button className="avatar-crop-btn" type="button" aria-label="Adjust avatar crop"
                          title="Adjust avatar crop" onClick={() => setCropOpen(true)}>
                    <img className="detail-avatar" alt="" style={focusStyle(avatarFocus)}
                         src={avatarSrc(detail.meta.id, vid, true)} />
                  </button>
                : hasAvatar
                ? <img className="detail-avatar" alt="" style={focusStyle(avatarFocus)}
                       src={avatarSrc(detail.meta.id, vid, true)} />
                : <div className="initials-avatar detail" aria-hidden>
                    {(card.data.name || detail.meta.name).split(/\s+/).slice(0, 2).map((w) => w[0] ?? "").join("")}
                  </div>}
```
to:
```tsx
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

- [ ] **Step 4: Unlock the gallery shelf controls**

Line 960, change:
```tsx
                      {worldScope && <button className="shelf-promote" onClick={() => promote(name)}>Set as avatar</button>}
```
to:
```tsx
                      <button className="shelf-promote" onClick={() => promote(name)}>Set as avatar</button>
```

Lines 964-968, change:
```tsx
                {worldScope && <>
                  <button className="shelf-add" onClick={() => shelfFileRef.current?.click()}>+ add</button>
                  <input ref={shelfFileRef} type="file" accept="image/*" hidden
                         aria-label="Add image" onChange={onShelfAdd} />
                </>}
```
to:
```tsx
                <button className="shelf-add" onClick={() => shelfFileRef.current?.click()}>+ add</button>
                <input ref={shelfFileRef} type="file" accept="image/*" hidden
                       aria-label="Add image" onChange={onShelfAdd} />
```

- [ ] **Step 5: Unlock the avatar-block (edit mode) controls**

Line 1092, change:
```tsx
            {worldScope && <div className="avatar-actions">
              <button className="subtle" type="button" onClick={() => avatarRef.current?.click()}>
                {hasAvatar ? "Replace" : "Upload"}
              </button>
              {hasAvatar && <button className="subtle" type="button" onClick={removeAvatar}>Remove</button>}
              <input ref={avatarRef} type="file" accept="image/*" hidden
                     aria-label="Upload avatar" onChange={onAvatar} />
            </div>}
```
to:
```tsx
            <div className="avatar-actions">
              <button className="subtle" type="button" onClick={() => avatarRef.current?.click()}>
                {hasAvatar ? "Replace" : "Upload"}
              </button>
              {hasAvatar && <button className="subtle" type="button" onClick={removeAvatar}>Remove</button>}
              <input ref={avatarRef} type="file" accept="image/*" hidden
                     aria-label="Upload avatar" onChange={onAvatar} />
            </div>
```

- [ ] **Step 6: Run the tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CharacterEditor.test.tsx`
Expected: PASS

- [ ] **Step 7: Run the full frontend suite and type check**

Run (from `frontend/`): `npx vitest run`
Expected: PASS, no regressions (other suites don't assert on
`worldScope`-gated image controls).

Run (from `frontend/`): `npx tsc -b`
Expected: no errors

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/CharacterEditor.tsx frontend/src/components/CharacterEditor.test.tsx
git commit -m "feat(frontend): allow avatar/gallery/crop editing in campaign scope"
```
