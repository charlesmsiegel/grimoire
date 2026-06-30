# Character- & Location-Owned Lore Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a lore entry be owned by one or more characters/PCs/locations, so it is filed under its owner in the UI and only activates into chat context when an owner is present in the scene.

**Architecture:** Add one optional `owners` frontmatter field to lore entries (comma-separated `kind:id` refs, mirroring how `keys` is stored). `activate()` in the context builder gains presence-gating: an owned entry is silent unless an owner is in the scene's present set, after which the existing keyword rule applies. The frontend Lore tab gains an owners multi-select + rail grouping + clickable owner chips; the character, PC, and location editors gain a panel listing their owned lore with a "+ New" shortcut.

**Tech Stack:** FastAPI + Pydantic (backend, pytest), Vite/React + TypeScript (frontend, vitest + @testing-library/react).

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Run backend: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Run frontend: `npx --prefix frontend vitest run`; typecheck with `tsc -b` in `frontend/`.
- Follow the list/detail page pattern in CLAUDE.md; metadata that references other records renders as clickable `chip` buttons that navigate.
- `owners` is transported as a **comma-separated string** of `kind:id` refs (e.g. `characters:master-tanaka, locations:old-dojo`), exactly mirroring the existing `keys` field. `kind ∈ {characters, pcs, locations}`. Empty/absent = world-level entry (unchanged behavior).
- Additive only — no migration. Existing lore files load with no owners and behave exactly as before.

---

## Task 1: Backend — store `owners` on entities

**Files:**
- Modify: `backend/src/grimoire/store/entities.py:65-91` (`create_entity`, `update_entity`)
- Modify: `backend/src/grimoire/routes.py:41-50` (`EntityCreate`, `EntityUpdate`)
- Modify: `backend/src/grimoire/routes.py:727-750` (`_entity_create`, `_entity_update`)
- Test: `backend/tests/test_entities_store.py`

**Interfaces:**
- Produces: `entities.create_entity(root, kind, name, body="", keys="", owners="") -> str`;
  `entities.update_entity(root, kind, eid, name=None, body=None, keys=None, owners=None) -> None`.
  `owners` stored verbatim in frontmatter (only when non-empty), surfaced by the existing `read_entity`/`list_entities` `**meta` splat as `meta["owners"]`.
- Produces: `EntityCreate.owners: str = ""`, `EntityUpdate.owners: str | None = None`.

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_entities_store.py`, append:

```python
def test_owners_round_trip(tmp_path: Path):
    eid = entities.create_entity(
        tmp_path, "lore", "Tanaka's exile", "He was cast out.",
        keys="exile", owners="characters:master-tanaka, locations:old-dojo",
    )
    got = entities.read_entity(tmp_path, "lore", eid)
    assert got["meta"]["owners"] == "characters:master-tanaka, locations:old-dojo"
    assert got["meta"]["keys"] == "exile"


def test_owners_absent_when_empty(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "World fact", "Always true.")
    got = entities.read_entity(tmp_path, "lore", eid)
    assert "owners" not in got["meta"]  # mirror keys: omit when empty


def test_update_owners(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "lore", "Fact", "x")
    entities.update_entity(tmp_path, "lore", eid, owners="pcs:hero")
    assert entities.read_entity(tmp_path, "lore", eid)["meta"]["owners"] == "pcs:hero"
    # body/name untouched
    assert entities.read_entity(tmp_path, "lore", eid)["body"].strip() == "x"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_entities_store.py -q`
Expected: FAIL — `create_entity() got an unexpected keyword argument 'owners'`.

- [ ] **Step 3: Implement in `entities.py`**

Change `create_entity` (line 65) to accept and store `owners`:

```python
def create_entity(root: Path, kind: str, name: str, body: str = "", keys: str = "", owners: str = "") -> str:
    _check_kind(kind)
    d = _kind_dir(root, kind)
    d.mkdir(parents=True, exist_ok=True)
    eid = uniquify(slugify(name), lambda c: _entity_path(root, kind, c).exists())
    meta = {"name": name}
    if keys:
        meta["keys"] = keys
    if owners:
        meta["owners"] = owners
    _entity_path(root, kind, eid).write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return eid
```

Change `update_entity` (line 77) to accept and patch `owners`:

```python
def update_entity(
    root: Path, kind: str, eid: str, name: str | None = None,
    body: str | None = None, keys: str | None = None, owners: str | None = None,
) -> None:
    _check_kind(kind)
    p = _entity_path(root, kind, eid)
    if not _safe_id(eid) or not p.exists():
        raise EntityNotFound(f"{kind}/{eid}")
    meta, cur_body = parse_frontmatter(p.read_text(encoding="utf-8"))
    if name is not None:
        meta["name"] = name
    if keys is not None:
        meta["keys"] = keys
    if owners is not None:
        meta["owners"] = owners
    new_body = cur_body if body is None else body
    p.write_text(dump_frontmatter(meta, new_body), encoding="utf-8")
```

- [ ] **Step 4: Run the entities test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_entities_store.py -q`
Expected: PASS.

- [ ] **Step 5: Wire `owners` through the routes**

In `backend/src/grimoire/routes.py`, extend the models (lines 41-50):

```python
class EntityCreate(BaseModel):
    name: str
    body: str = ""
    keys: str = ""
    owners: str = ""


class EntityUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    keys: str | None = None
    owners: str | None = None
```

Update `_entity_create` (line 727) to pass `owners`:

```python
def _entity_create(root, kind: str, body: EntityCreate):
    try:
        return {"id": store.entities.create_entity(root, kind, body.name, body.body, body.keys, body.owners)}
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
```

Update `_entity_update` (line 743) to pass `owners`:

```python
def _entity_update(root, kind: str, eid: str, body: EntityUpdate):
    try:
        store.entities.update_entity(root, kind, eid, name=body.name, body=body.body,
                                     keys=body.keys, owners=body.owners)
    except store.entities.UnknownKind:
        raise HTTPException(status_code=404, detail="unknown kind")
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="entity not found")
    return {"ok": True}
```

- [ ] **Step 6: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (no regressions — GET routes already surface `owners` via the existing `**meta` splat).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/entities.py backend/src/grimoire/routes.py backend/tests/test_entities_store.py
git commit -m "feat: store owners frontmatter on lore entries"
```

---

## Task 2: Backend — presence-gated activation

**Files:**
- Modify: `backend/src/grimoire/store/context.py:14-24` (`activate`)
- Modify: `backend/src/grimoire/store/context.py:72-82` (`_world_info`)
- Modify: `backend/src/grimoire/store/context.py:278-288` (build the present set in `_assemble`)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `appearances.scene_cast(cid, sid) -> [{"kind","id","role"}]`; `scenes.get_location_history(cid, sid) -> [loc_id]`.
- Produces: `context.activate(entries, recent_text, present=frozenset()) -> list[dict]` — each entry may carry `owners: list[str]` of `kind:id` refs; an owned entry is dropped unless one of its owners is in `present`, then the keyword/always-on rule applies. `present` is a set of `kind:id` strings.
- Produces: `_world_info(croot, recent_text, exclude=frozenset(), present=frozenset())` threads `owners` per entry and passes `present` to `activate`.

- [ ] **Step 1: Write the failing unit tests**

In `backend/tests/test_context.py`, after `test_activate_whole_word_only` (line 20), append:

```python
def test_activate_owned_silent_when_owner_absent():
    entries = [{"name": "Backstory", "body": "b", "keys": [], "owners": ["characters:tanaka"]}]
    # keyless but owned -> NOT always-on; silent because owner not present
    assert context.activate(entries, "anything", present=frozenset()) == []


def test_activate_owned_on_when_owner_present_keyless():
    entries = [{"name": "Backstory", "body": "b", "keys": [], "owners": ["characters:tanaka"]}]
    out = context.activate(entries, "", present=frozenset({"characters:tanaka"}))
    assert [e["name"] for e in out] == ["Backstory"]


def test_activate_owned_present_still_needs_keyword():
    entries = [{"name": "Secret", "body": "s", "keys": ["duel"], "owners": ["characters:tanaka"]}]
    present = frozenset({"characters:tanaka"})
    assert context.activate(entries, "they talked", present=present) == []          # present, no keyword
    out = context.activate(entries, "the duel ended", present=present)              # present + keyword
    assert [e["name"] for e in out] == ["Secret"]


def test_activate_multi_owner_any_present():
    entries = [{"name": "Feud", "body": "f", "keys": [], "owners": ["characters:a", "characters:b"]}]
    out = context.activate(entries, "", present=frozenset({"characters:b"}))
    assert [e["name"] for e in out] == ["Feud"]


def test_activate_unowned_unchanged():
    entries = [{"name": "World", "body": "w", "keys": []}]  # no owners key at all
    assert [e["name"] for e in context.activate(entries, "x")] == ["World"]
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: FAIL — `activate() got an unexpected keyword argument 'present'`.

- [ ] **Step 3: Implement presence-gating in `activate`**

Replace `activate` (lines 14-24) with:

```python
def activate(entries: list[dict], recent_text: str, present: frozenset = frozenset()) -> list[dict]:
    """Select world-info entries. Owned entries (owners non-empty) are silent unless one
    owner ref is in `present`; then keyless = always-on, keyed = any key whole-word (ci) in
    recent_text. Unowned entries behave as before."""
    out: list[dict] = []
    for e in entries:
        owners = e.get("owners") or []
        if owners and not any(o in present for o in owners):
            continue  # owned but no owner in scene -> never leak
        keys = e.get("keys") or []
        if not keys:
            out.append(e)
            continue
        if any(re.search(rf"\b{re.escape(k)}\b", recent_text, re.IGNORECASE) for k in keys):
            out.append(e)
    return out
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS (including the pre-existing `test_activate_*` two-arg cases — `present` defaults to empty).

- [ ] **Step 5: Thread `owners` + `present` through `_world_info`**

Replace `_world_info` (lines 72-82) with:

```python
def _world_info(croot, recent_text: str, exclude: frozenset = frozenset(),
                present: frozenset = frozenset()) -> str:
    entries = []
    for kind in ("lore", "locations"):
        for meta in entities.list_entities(croot, kind):
            if kind == "locations" and meta["id"] in exclude:
                continue
            e = entities.read_entity(croot, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            owners = [o.strip() for o in e["meta"].get("owners", "").split(",") if o.strip()]
            entries.append({"name": e["meta"].get("name", meta["id"]),
                            "body": e["body"].strip(), "keys": keys, "owners": owners})
    selected = activate(entries, recent_text, present)
    return "\n\n".join(e["body"] for e in selected if e["body"])
```

- [ ] **Step 6: Build the present set in `_assemble` and pass it**

In `_assemble`, the current-location block ends at line 287. Replace the `add("World info", ...)` call (line 288) with a present-set computation followed by the call:

```python
    present = {f"{a['kind']}:{a['id']}" for a in cast}
    if current_loc:
        present |= {f"locations:{current_loc}"}
    add("World info", _world_info(croot, recent_text, exclude, frozenset(present)))
```

(`cast` is already bound at line 223; `current_loc` at line 279.)

`build_opener_messages` (line 110) calls `_world_info(croot, prompt)` with the default empty `present`, so owned lore stays silent in the character-less opener — no change needed there.

- [ ] **Step 7: Write the integration test**

In `backend/tests/test_context.py`, append (uses the existing `_campaign`/`_npc_card` helpers):

```python
def test_owned_lore_only_shows_when_owner_in_scene(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Tanaka", "default", _npc_card("Tanaka", description="sensei"))
    # owned, keyless lore for the character (id is the slug "tanaka")
    entities.create_entity(wroot, "lore", "Tanaka secret", "He was exiled.",
                           owners="characters:tanaka")
    scenes.append_message(cid, sid, "user", "hello")

    # owner NOT in scene -> lore absent
    assert "He was exiled." not in context.build_messages(cid, sid)[0]["content"]

    # bring the owner into the scene -> lore present
    ap.appear(cid, sid, "characters", "tanaka", "default", "npc")
    assert "He was exiled." in context.build_messages(cid, sid)[0]["content"]
```

- [ ] **Step 8: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat: presence-gate owned lore in context builder"
```

---

## Task 3: Frontend — API client owners support + owner candidates

**Files:**
- Modify: `frontend/src/api/client.ts:51-54` (entity types)
- Modify: `frontend/src/api/client.ts:206-216` (entity methods)
- Create: `frontend/src/api/loreOwners.ts` (owner-candidate helper)
- Test: `frontend/src/api/loreOwners.test.ts`

**Interfaces:**
- Produces: `EntitySummary`/`EntityDetail.meta` gain `owners?: string`; `createEntity`/`updateEntity` payloads gain `owners?: string`.
- Produces: `export type LoreOwner = { ref: string; label: string; kind: "characters" | "pcs" | "locations" }`
  and `export function loreOwnerOptions(wid: string): Promise<LoreOwner[]>` — concatenates the world's characters, PCs, and locations into selectable owner refs (`ref = "<kind>:<id>"`).

- [ ] **Step 1: Extend the entity types**

In `frontend/src/api/client.ts`, update lines 53-54:

```typescript
export type EntitySummary = { id: string; name: string; keys?: string; owners?: string };
export type EntityDetail = { meta: { id: string; name: string; keys?: string; owners?: string }; body: string };
```

Update the `createEntity`/`updateEntity` signatures (lines 208-214):

```typescript
  createEntity: (scope: EntityScope, kind: EntityKind, body: { name: string; body?: string; keys?: string; owners?: string }) =>
    request<{ id: string }>("POST", `${entityBase(scope)}/${kind}`, body),
  readEntity: (scope: EntityScope, kind: EntityKind, id: string) =>
    request<EntityDetail>("GET", `${entityBase(scope)}/${kind}/${id}`),
  updateEntity: (scope: EntityScope, kind: EntityKind, id: string,
                 patch: { name?: string; body?: string; keys?: string; owners?: string }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/${kind}/${id}`, patch),
```

- [ ] **Step 2: Write the failing helper test**

Create `frontend/src/api/loreOwners.test.ts`:

```typescript
import { loreOwnerOptions } from "./loreOwners";

vi.mock("./client", () => ({
  api: {
    listCharacters: vi.fn().mockResolvedValue([{ id: "tanaka", name: "Tanaka" }]),
    listPCs: vi.fn().mockResolvedValue([{ id: "hero", name: "Hero" }]),
    listEntities: vi.fn().mockResolvedValue([{ id: "old-dojo", name: "Old Dojo" }]),
  },
}));

test("collects characters, pcs, locations as owner refs", async () => {
  const opts = await loreOwnerOptions("w");
  expect(opts).toEqual([
    { ref: "characters:tanaka", label: "Tanaka", kind: "characters" },
    { ref: "pcs:hero", label: "Hero", kind: "pcs" },
    { ref: "locations:old-dojo", label: "Old Dojo", kind: "locations" },
  ]);
});
```

- [ ] **Step 3: Run to verify failure**

Run: `npx --prefix frontend vitest run src/api/loreOwners.test.ts`
Expected: FAIL — cannot find module `./loreOwners`.

- [ ] **Step 4: Implement the helper**

Create `frontend/src/api/loreOwners.ts`:

```typescript
import { api } from "./client";

export type LoreOwner = { ref: string; label: string; kind: "characters" | "pcs" | "locations" };

/** All records in a world that can own lore, as selectable owner refs. */
export async function loreOwnerOptions(wid: string): Promise<LoreOwner[]> {
  const [chars, pcs, locs] = await Promise.all([
    api.listCharacters(wid),
    api.listPCs(wid),
    api.listEntities({ kind: "world", id: wid }, "locations"),
  ]);
  return [
    ...chars.map((c) => ({ ref: `characters:${c.id}`, label: c.name, kind: "characters" as const })),
    ...pcs.map((p) => ({ ref: `pcs:${p.id}`, label: p.name, kind: "pcs" as const })),
    ...locs.map((l) => ({ ref: `locations:${l.id}`, label: l.name, kind: "locations" as const })),
  ];
}
```

- [ ] **Step 5: Run the helper test + typecheck**

Run: `npx --prefix frontend vitest run src/api/loreOwners.test.ts`
Expected: PASS.
Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/api/loreOwners.ts frontend/src/api/loreOwners.test.ts
git commit -m "feat: client owners field + lore-owner candidates helper"
```

---

## Task 4: Frontend — owners UI in the Lore tab (EntityEditor)

**Files:**
- Modify: `frontend/src/components/EntityEditor.tsx` (owners state, multi-select, rail grouping, owner chips)
- Test: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: `loreOwnerOptions(wid)`, `LoreOwner` (Task 3); `EntitySummary.owners`, `EntityDetail.meta.owners` (Task 3).
- Produces: EntityEditor accepts two new optional props used only for `kind === "lore"`:
  `nav?: { focusEntry?: string; newOwner?: string } | null` (inbound navigation — open an entry, or start a new entry pre-owned) and
  `onOpenOwner?: (ref: string) => void` (clicking an owner chip in the read-only view). Both default undefined; the locations tab passes neither and is unaffected.

Owners are held in component state as a `string[]` of refs and joined with `", "` for the API (mirroring how `keys` is a comma string).

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/EntityEditor.test.tsx`, extend the `vi.mock` at line 4 to also expose `listCharacters`/`listPCs` (needed by `loreOwnerOptions`), and add these `beforeEach` defaults plus tests:

```typescript
// add to the api mock object:
//   listCharacters: vi.fn(), listPCs: vi.fn(),
// add to beforeEach:
//   (api.listCharacters as any).mockResolvedValue([{ id: "tanaka", name: "Tanaka" }]);
//   (api.listPCs as any).mockResolvedValue([]);

test("creates a lore entry with a selected owner", async () => {
  (api.listEntities as any).mockImplementation((_s: any, kind: string) =>
    Promise.resolve(kind === "locations" ? [] : []));
  render(<EntityEditor wid="w" kind="lore" />);
  await screen.findByRole("button", { name: /\+ new lore entry/i });
  fireEvent.change(screen.getByLabelText("Name"), { target: { value: "Exile" } });
  fireEvent.change(await screen.findByLabelText("Tanaka"), { target: { checked: true } });
  fireEvent.click(screen.getByRole("button", { name: /create lore entry/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "lore",
      expect.objectContaining({ name: "Exile", owners: "characters:tanaka" })),
  );
});

test("groups the rail by owner with an Unowned group", async () => {
  (api.listEntities as any).mockImplementation((_s: any, kind: string) =>
    Promise.resolve(kind === "locations" ? [] : [
      { id: "a", name: "Owned A", owners: "characters:tanaka" },
      { id: "b", name: "World B" },
    ]));
  render(<EntityEditor wid="w" kind="lore" />);
  expect(await screen.findByText("Unowned (world)")).toBeInTheDocument();
  expect(await screen.findByText("Tanaka")).toBeInTheDocument(); // group heading
});

test("owner chip in the read-only view calls onOpenOwner", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "a", name: "Owned A", owners: "characters:tanaka" }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "a", name: "Owned A", owners: "characters:tanaka" }, body: "x" });
  const onOpenOwner = vi.fn();
  const { container } = render(<EntityEditor wid="w" kind="lore" onOpenOwner={onOpenOwner} />);
  fireEvent.click(await screen.findByText("Owned A"));
  const side = await waitFor(() => container.querySelector(".detail-sidebar") as HTMLElement);
  fireEvent.click(within(side).getByRole("button", { name: "Tanaka" }));
  expect(onOpenOwner).toHaveBeenCalledWith("characters:tanaka");
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx --prefix frontend vitest run src/components/EntityEditor.test.tsx`
Expected: FAIL — no owners checkbox / no "Unowned (world)" grouping / no owner chip button.

- [ ] **Step 3: Add owners state and option loading**

In `EntityEditor.tsx`, add imports and state. After the existing `import { Field }` line, add:

```typescript
import { loreOwnerOptions, type LoreOwner } from "../api/loreOwners";
```

Change the component signature (line 7) to accept the new optional props:

```typescript
export function EntityEditor({ wid, kind, nav, onOpenOwner }: {
  wid: string; kind: EntityKind;
  nav?: { focusEntry?: string; newOwner?: string } | null;
  onOpenOwner?: (ref: string) => void;
}) {
```

Add state next to the other `useState` calls (after line 13):

```typescript
  const [owners, setOwners] = useState<string[]>([]);          // selected owner refs
  const [ownerOpts, setOwnerOpts] = useState<LoreOwner[]>([]); // candidates for the picker
```

After the existing `reload`/`useEffect` (line 23), load owner options for lore:

```typescript
  useEffect(() => {
    if (kind === "lore") loreOwnerOptions(wid).then(setOwnerOpts);
  }, [wid, kind]);

  const ownerLabel = useCallback(
    (ref: string) => ownerOpts.find((o) => o.ref === ref)?.label ?? ref,
    [ownerOpts],
  );
```

- [ ] **Step 4: Carry owners through reset/select/save**

In `resetForm` (line 25) add `setOwners(nav?.newOwner ? [nav.newOwner] : []);` after `setKeys("")`.

In `select` (line 33) add, after `setKeys(...)`:

```typescript
    setOwners((e.meta.owners ?? "").split(",").map((o) => o.trim()).filter(Boolean));
```

In `save` (line 43), include owners in both payloads (join with ", "):

```typescript
      const ownerStr = owners.join(", ");
      if (editing) {
        await api.updateEntity(scope, kind, editing, { name, body, keys, owners: ownerStr });
        await reload();
        await select(editing);
      } else {
        await api.createEntity(scope, kind, { name, body, keys, owners: ownerStr });
        await reload();
        resetForm();
      }
```

- [ ] **Step 5: Honor inbound `nav` (focus an entry / start a pre-owned entry)**

After the owner-options `useEffect` from Step 3, add:

```typescript
  useEffect(() => {
    if (!nav) return;
    if (nav.focusEntry) select(nav.focusEntry);
    else resetForm(); // newOwner handled inside resetForm
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nav]);
```

- [ ] **Step 6: Render the Owners multi-select in the form (lore only)**

In the form (after the `Keys` `Field`, line 115), add:

```tsx
            {kind === "lore" && (
              <Field label="Owners" hint="lore activates only when an owner is in the scene; none = world-level">
                <div className="chips owner-picker">
                  {ownerOpts.map((o) => (
                    <label key={o.ref} className="owner-option">
                      <input
                        type="checkbox"
                        aria-label={o.label}
                        checked={owners.includes(o.ref)}
                        onChange={(e) =>
                          setOwners(e.target.checked ? [...owners, o.ref] : owners.filter((r) => r !== o.ref))
                        }
                      />
                      {o.label}
                    </label>
                  ))}
                  {ownerOpts.length === 0 && <span className="field-hint">No characters, PCs, or locations yet.</span>}
                </div>
              </Field>
            )}
```

- [ ] **Step 7: Render owner chips in the read-only sidebar (lore only)**

In the `.detail-sidebar` aside (after the Keys `.side-section`, line 101), add:

```tsx
              {kind === "lore" && (
                <div className="side-section">
                  <h4>Owners</h4>
                  {owners.length > 0 ? (
                    <div className="chips">
                      {owners.map((ref) => (
                        <button key={ref} className="chip" onClick={() => onOpenOwner?.(ref)}>
                          {ownerLabel(ref)}
                        </button>
                      ))}
                    </div>
                  ) : (
                    <div className="field-hint">world-level</div>
                  )}
                </div>
              )}
```

- [ ] **Step 8: Group the rail by owner (lore only)**

Replace the rail's flat `items.map(...)` list (lines 74-78) with a grouped render for lore, falling back to the flat list for locations. Add this helper above the `return` (after `keyList`, line 68):

```typescript
  // Group lore rows: "Unowned (world)" first, then one group per distinct owner ref.
  const groups: { key: string; label: string; rows: EntitySummary[] }[] = [];
  if (kind === "lore") {
    const unowned = items.filter((e) => !(e.owners ?? "").trim());
    if (unowned.length) groups.push({ key: "", label: "Unowned (world)", rows: unowned });
    const seen = new Set<string>();
    for (const e of items) {
      for (const ref of (e.owners ?? "").split(",").map((o) => o.trim()).filter(Boolean)) {
        if (seen.has(ref)) continue;
        seen.add(ref);
        groups.push({ key: ref, label: ownerLabel(ref), rows: items.filter((x) => (x.owners ?? "").includes(ref)) });
      }
    }
  }
```

Then in the `.editor-list`, replace lines 74-79 with:

```tsx
        {kind === "lore"
          ? groups.map((g) => (
              <div key={g.key} className="rail-group">
                <div className="rail-group-head">{g.label}</div>
                {g.rows.map((e) => (
                  <button key={e.id} className={"row" + (editing === e.id ? " active" : "")} onClick={() => select(e.id)}>
                    {e.name}
                  </button>
                ))}
              </div>
            ))
          : items.map((e) => (
              <button key={e.id} className={"row" + (editing === e.id ? " active" : "")} onClick={() => select(e.id)}>
                {e.name}
              </button>
            ))}
        {items.length === 0 && <div className="editor-empty">No {kind} yet.</div>}
```

- [ ] **Step 9: Run the EntityEditor tests + typecheck**

Run: `npx --prefix frontend vitest run src/components/EntityEditor.test.tsx`
Expected: PASS (existing tests still green — locations path unchanged, lore path adds owners).
Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/EntityEditor.tsx frontend/src/components/EntityEditor.test.tsx
git commit -m "feat: owners multi-select, rail grouping, owner chips in lore editor"
```

---

## Task 5: Frontend — owner-side lore panel + WorldView wiring

**Files:**
- Create: `frontend/src/components/OwnedLorePanel.tsx`
- Create: `frontend/src/components/OwnedLorePanel.test.tsx`
- Modify: `frontend/src/routes/WorldView.tsx` (lore nav state + tab switching)
- Modify: `frontend/src/components/CharacterEditor.tsx:386-441` (embed panel in detail mode)
- Modify: `frontend/src/components/PCEditor.tsx:101-156` (embed panel in the persona view)
- Modify: `frontend/src/components/EntityEditor.tsx` (embed panel in the locations read-only sidebar)

**Interfaces:**
- Consumes: `api.listEntities`, `EntitySummary.owners` (Task 3).
- Produces: `OwnedLorePanel({ wid, ownerRef, onOpenEntry, onNewEntry }: { wid: string; ownerRef: string; onOpenEntry: (id: string) => void; onNewEntry: () => void })` — lists the lore entries whose `owners` include `ownerRef` (clickable) and a "+ New lore" button. Both callbacks route the caller to the Lore tab.
- Produces: WorldView gains `loreNav` state and an `openLore(nav)` helper; passes `nav`/`onOpenOwner` into the lore `EntityEditor` (Task 4) and `openLore` down into the owner editors.

- [ ] **Step 1: Write the failing panel test**

Create `frontend/src/components/OwnedLorePanel.test.tsx`:

```typescript
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { OwnedLorePanel } from "./OwnedLorePanel";

vi.mock("../api/client", () => ({ api: { listEntities: vi.fn() } }));
import { api } from "../api/client";

beforeEach(() => {
  (api.listEntities as any).mockResolvedValue([
    { id: "a", name: "Exile", owners: "characters:tanaka" },
    { id: "b", name: "World fact" },
    { id: "c", name: "Duel", owners: "characters:tanaka, locations:dojo" },
  ]);
});

test("lists only entries owned by the ref and opens one", async () => {
  const onOpenEntry = vi.fn();
  render(<OwnedLorePanel wid="w" ownerRef="characters:tanaka" onOpenEntry={onOpenEntry} onNewEntry={vi.fn()} />);
  expect(await screen.findByText("Exile")).toBeInTheDocument();
  expect(await screen.findByText("Duel")).toBeInTheDocument();
  expect(screen.queryByText("World fact")).toBeNull();
  fireEvent.click(screen.getByText("Exile"));
  expect(onOpenEntry).toHaveBeenCalledWith("a");
});

test("the + New lore button fires onNewEntry", async () => {
  const onNewEntry = vi.fn();
  render(<OwnedLorePanel wid="w" ownerRef="characters:tanaka" onOpenEntry={vi.fn()} onNewEntry={onNewEntry} />);
  fireEvent.click(await screen.findByRole("button", { name: /\+ new lore/i }));
  expect(onNewEntry).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx --prefix frontend vitest run src/components/OwnedLorePanel.test.tsx`
Expected: FAIL — cannot find module `./OwnedLorePanel`.

- [ ] **Step 3: Implement the panel**

Create `frontend/src/components/OwnedLorePanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api, type EntitySummary } from "../api/client";

/** Lists the world lore entries owned by `ownerRef`, with a shortcut to create a new one.
 *  Editing happens in the Lore tab — the callbacks route there. */
export function OwnedLorePanel({ wid, ownerRef, onOpenEntry, onNewEntry }: {
  wid: string; ownerRef: string;
  onOpenEntry: (id: string) => void; onNewEntry: () => void;
}) {
  const [owned, setOwned] = useState<EntitySummary[]>([]);
  useEffect(() => {
    api.listEntities({ kind: "world", id: wid }, "lore").then((items) =>
      setOwned(items.filter((e) =>
        (e.owners ?? "").split(",").map((o) => o.trim()).includes(ownerRef))),
    );
  }, [wid, ownerRef]);

  return (
    <div className="side-section owned-lore">
      <h4>Lore</h4>
      {owned.length > 0 ? (
        <div className="chips">
          {owned.map((e) => (
            <button key={e.id} className="chip" onClick={() => onOpenEntry(e.id)}>{e.name}</button>
          ))}
        </div>
      ) : (
        <div className="field-hint">No lore yet.</div>
      )}
      <button className="subtle" onClick={onNewEntry}>+ New lore</button>
    </div>
  );
}
```

- [ ] **Step 4: Run the panel test**

Run: `npx --prefix frontend vitest run src/components/OwnedLorePanel.test.tsx`
Expected: PASS.

- [ ] **Step 5: Add lore-navigation plumbing to WorldView**

In `frontend/src/routes/WorldView.tsx`, add state and a helper next to `focusChar` (line 28):

```typescript
  const [loreNav, setLoreNav] = useState<{ focusEntry?: string; newOwner?: string } | null>(null);

  function openLore(nav: { focusEntry?: string; newOwner?: string }) {
    setLoreNav({ ...nav });
    setTab("lore");
  }

  // owner chip inside the Lore tab -> jump to that record's tab
  function openOwner(ref: string) {
    const [kind, id] = [ref.slice(0, ref.indexOf(":")), ref.slice(ref.indexOf(":") + 1)];
    if (kind === "characters") openCharacter(id, "");   // "" -> CharacterEditor falls back to default version
    else if (kind === "pcs") setTab("pcs");
    else if (kind === "locations") setTab("locations");
  }
```

Pass `nav`/`onOpenOwner` to the lore `EntityEditor` (line 67) and `openLore` to the owner editors:

```tsx
      {tab === "characters" && <CharacterEditor wid={wid} resetSignal={charReset} focus={focusChar} onOpenLore={openLore} />}
      {tab === "pcs" && <PCEditor wid={wid} onOpenLore={openLore} />}
      {tab === "tags" && <TagEditor wid={wid} />}
      {tab === "locations" && <EntityEditor wid={wid} kind="locations" onOpenLore={openLore} />}
      {tab === "lore" && (
        <>
          <details className="import-section">
            <summary>Import lorebook / world-info</summary>
            <LorebookImport wid={wid} onImported={() => setLoreReset((n) => n + 1)} />
          </details>
          <EntityEditor key={loreReset} wid={wid} kind="lore" nav={loreNav} onOpenOwner={openOwner} />
        </>
      )}
```

- [ ] **Step 6: Embed the panel in CharacterEditor detail mode**

In `frontend/src/components/CharacterEditor.tsx`, add `onOpenLore` to the props (line 18):

```typescript
export function CharacterEditor({ wid, resetSignal, focus, onOpenLore }:
  { wid: string; resetSignal?: number; focus?: { cid: string; vid: string } | null;
    onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void }) {
```

Add the import at the top:

```typescript
import { OwnedLorePanel } from "./OwnedLorePanel";
```

In `mode === "detail"`, after the `.detail-head` block closes (line 418) and before `{localizeControls(false)}` (line 420), render the panel:

```tsx
            {onOpenLore && (
              <OwnedLorePanel
                wid={wid}
                ownerRef={`characters:${detail.meta.id}`}
                onOpenEntry={(id) => onOpenLore({ focusEntry: id })}
                onNewEntry={() => onOpenLore({ newOwner: `characters:${detail.meta.id}` })}
              />
            )}
```

- [ ] **Step 7: Embed the panel in PCEditor**

In `frontend/src/components/PCEditor.tsx`, add `onOpenLore` to the props (line 7):

```typescript
export function PCEditor({ wid, onOpenLore }:
  { wid: string; onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void }) {
```

Add the import:

```typescript
import { OwnedLorePanel } from "./OwnedLorePanel";
```

Inside the persona form, after the `form-actions` div (line 154), add:

```tsx
            {onOpenLore && (
              <OwnedLorePanel
                wid={wid}
                ownerRef={`pcs:${detail.meta.id}`}
                onOpenEntry={(id) => onOpenLore({ focusEntry: id })}
                onNewEntry={() => onOpenLore({ newOwner: `pcs:${detail.meta.id}` })}
              />
            )}
```

- [ ] **Step 8: Embed the panel in the locations sidebar (EntityEditor)**

In `frontend/src/components/EntityEditor.tsx`, add `onOpenLore` to the props block from Task 4:

```typescript
export function EntityEditor({ wid, kind, nav, onOpenOwner, onOpenLore }: {
  wid: string; kind: EntityKind;
  nav?: { focusEntry?: string; newOwner?: string } | null;
  onOpenOwner?: (ref: string) => void;
  onOpenLore?: (nav: { focusEntry?: string; newOwner?: string }) => void;
}) {
```

Add the import:

```typescript
import { OwnedLorePanel } from "./OwnedLorePanel";
```

In the read-only `.detail-sidebar`, after the Keys `.side-section` (and the lore Owners section from Task 4), render the panel only for locations:

```tsx
              {kind === "locations" && editing && onOpenLore && (
                <OwnedLorePanel
                  wid={wid}
                  ownerRef={`locations:${editing}`}
                  onOpenEntry={(id) => onOpenLore({ focusEntry: id })}
                  onNewEntry={() => onOpenLore({ newOwner: `locations:${editing}` })}
                />
              )}
```

- [ ] **Step 9: Run the full frontend suite + typecheck**

Run: `npx --prefix frontend vitest run`
Expected: PASS.
Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 10: Commit**

```bash
git add frontend/src/components/OwnedLorePanel.tsx frontend/src/components/OwnedLorePanel.test.tsx \
        frontend/src/routes/WorldView.tsx frontend/src/components/CharacterEditor.tsx \
        frontend/src/components/PCEditor.tsx frontend/src/components/EntityEditor.tsx
git commit -m "feat: owned-lore panel on character, PC, and location editors"
```

---

## Task 6: Styling + manual verification

**Files:**
- Modify: `frontend/src/` stylesheet that defines `.editor`, `.chips`, `.side-section` (locate with a grep for `.rail-group` siblings, e.g. `.side-section`).

**Interfaces:** none (CSS only).

- [ ] **Step 1: Locate the stylesheet**

Run: `grep -rl "side-section" frontend/src`
Use the file that defines the editor styles.

- [ ] **Step 2: Add styles for the new elements**

Append rules for the rail groups, owner picker, and panel (match existing spacing/colors — copy values from neighboring rules):

```css
.rail-group-head { font-size: 0.75rem; text-transform: uppercase; opacity: 0.6; margin: 0.5rem 0 0.25rem; }
.owner-picker { flex-direction: column; align-items: flex-start; gap: 0.25rem; }
.owner-option { display: flex; align-items: center; gap: 0.4rem; cursor: pointer; }
.owned-lore .chip { cursor: pointer; }
```

- [ ] **Step 3: Manual verification**

Start the app (per CLAUDE.md / the `run` skill). Verify:
1. Lore tab: a new entry shows the **Owners** checklist (characters + PCs + locations); selecting one and saving groups it under that owner in the rail.
2. The read-only lore view shows owner chips; clicking one jumps to that record's tab.
3. A character's detail view shows a **Lore** panel listing its owned entries; **+ New lore** opens the Lore tab's form pre-owned by that character.
4. In a scene: an owned, keyless lore body appears in the context breakdown only when its owner is in the cast / is the current location (use the existing token-breakdown / context view to confirm).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/<stylesheet>
git commit -m "style: rail groups, owner picker, owned-lore panel"
```

---

## Self-Review

**Spec coverage:**
- Data model (`owners` frontmatter, `kind:id`, multi-owner, absent = world-level) → Task 1.
- Activation truth table (presence gates keywords; owned+absent silent; opener silent) → Task 2.
- API surface (`owners` on summary/detail via `**meta`; create/update accept it) → Tasks 1 & 3. *Refinement vs spec:* `owners` is transported as a comma-separated **string** mirroring `keys`, not a JSON list — chosen for consistency with the existing `keys` field and to avoid list/string conversion in the routes. The context layer parses it to a list internally.
- Owner candidates helper → Task 3.
- Lore tab: owners multi-select, rail grouping ("Unowned (world)" + per-owner), owner chips navigate → Task 4.
- Owner editors (character, PC, location) get a Lore panel with "+ New" pre-fill → Task 5.
- Testing (backend truth table + round-trip + integration; frontend form/grouping/chips/panel) → Tasks 1, 2, 3, 4, 5.
- No migration → guaranteed by additive optional field (Task 1).

**Placeholder scan:** none — every code/test step shows full content.

**Type consistency:** `owners` is a comma-separated string at the storage/API/client boundary; held as `string[]` only inside EntityEditor (joined with `", "` on save) and parsed to a list inside `_world_info`/`activate`. `nav` shape `{ focusEntry?; newOwner? }` and `onOpenLore`/`onOpenOwner` signatures match across WorldView, EntityEditor, CharacterEditor, PCEditor, and OwnedLorePanel. The `present` set is `kind:id` strings on both producer (`_assemble`) and consumer (`activate`).
