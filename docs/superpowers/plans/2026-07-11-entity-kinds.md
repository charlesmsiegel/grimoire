# Entity Kinds & Typed Entity Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add first-class `items` / `groups` / `creatures` entity kinds (#36), minimal typed per-kind fields (#37), a world Overview tab (#39), and campaign-scoped group state with absorb write-back (#47).

**Architecture:** Extend the kind tuple in `store/entities.py` — everything downstream (generic routes, overlay, sync, counts, lorebook categories, images) keys off it. A new `store/entity_schema.py` carries per-kind field descriptors; a new `store/groupstate.py` mirrors `playstate.py` for the five group-state sections; the Overview tab is a client-side composition of existing endpoints.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend), Jinja2 templates under repo-root `templates/`.

**Spec:** `docs/superpowers/specs/2026-07-11-entity-kinds-design.md`

## Global Constraints

- No new dependencies; pydantic usage stays v1/v2-agnostic: plain `BaseModel` fields only — no `model_dump()`, `Field`, validators, `ConfigDict` (Android/Chaquopy rule).
- Filesystem access only via `store.paths` / `worlds.world_root` / `campaigns.campaign_root`; never assume a checkout layout.
- Every string sent to the LLM lives in `templates/` — never inline prompt text in Python.
- Test fixtures use invented placeholder names only (Seraphine, Mara, Winifred, Saltmarch, Salt Circle, …) — never real world/campaign/character names (repo is public).
- Backend tests isolate the store: `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))`.
- Backend test run: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Frontend: run `npx vitest run` and `npx tsc -b` **from `frontend/`** (never `npx --prefix`).
- Commit after every task; message format `feat(scope): ... (#NN)`.

---

### Task 1: Backend — the three new kinds (#36)

**Files:**
- Modify: `backend/src/grimoire/store/entities.py:17`
- Modify: `backend/src/grimoire/store/context.py:107-121` (`_world_info` kind loop)
- Test: `backend/tests/test_entities_store.py`, `backend/tests/test_sync_store.py`, `backend/tests/test_context.py`, `backend/tests/test_lorebook_store.py`

**Interfaces:**
- Produces: `entities.ENTITY_KINDS == ("locations", "lore", "items", "groups", "creatures")`. All CRUD, `SYNCED_KINDS`, `entity_counts`, `all_refs`, overlay, sync, lorebook categories, and the generic `/{kind}` + image routes accept the new kinds with no further change.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_entities_store.py`:

```python
def test_new_kinds_are_generic_entities(tmp_path: Path):
    for kind, name in (("items", "Salt Knife"), ("groups", "Salt Circle"), ("creatures", "Marsh Wyrm")):
        eid = entities.create_entity(tmp_path, kind, name, "body text", keys="salt")
        got = entities.read_entity(tmp_path, kind, eid)
        assert got["meta"]["name"] == name
        assert got["meta"]["keys"] == "salt"
    assert entities.entity_counts(tmp_path) == {
        "locations": 0, "lore": 0, "items": 1, "groups": 1, "creatures": 1}
```

Update the existing `test_all_refs_and_counts` (its equality assertion breaks when the counts dict grows):

```python
def test_all_refs_and_counts(tmp_path: Path):
    entities.create_entity(tmp_path, "lore", "A")
    entities.create_entity(tmp_path, "locations", "B")
    assert set(entities.all_refs(tmp_path)) == {("lore", "a"), ("locations", "b")}
    assert entities.entity_counts(tmp_path) == {
        "locations": 1, "lore": 1, "items": 0, "groups": 0, "creatures": 0}
```

Append to `backend/tests/test_sync_store.py` (mirrors the existing update-flow tests; proves new kinds ride overlay + sync end to end):

```python
def test_new_kind_flows_live_and_syncs_updates(monkeypatch, tmp_path):
    wid, cid = _setup(monkeypatch, tmp_path)
    entities.create_entity(worlds.world_root(wid), "items", "Salt Knife", "v1")
    # brand-new world record: inherited live via the overlay, nothing incoming
    assert sync.incoming(cid) == []
    assert overlay.read_entity(cid, "items", "salt-knife")["body"].strip() == "v1"
    # materialized-then-world-updated: offered as an update, like locations/lore
    overlay.materialize_entity(cid, "items", "salt-knife")
    entities.update_entity(worlds.world_root(wid), "items", "salt-knife", body="v2")
    pend = sync.incoming(cid)
    assert [p["status"] for p in pend] == ["update"]
    assert pend[0]["world"]["body"].strip() == "v2"
```

Append to `backend/tests/test_context.py` (after the existing owned-lore tests, reusing `_campaign`):

```python
def test_new_kinds_activate_like_lore(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")          # keyless -> always-on
    entities.create_entity(croot, "creatures", "Marsh Wyrm", "Sleeps in brine.", keys="wyrm")
    entities.create_entity(croot, "items", "Salt Knife", "Cuts anything.", keys="knife")
    scenes.append_message(cid, sid, "user", "The wyrm stirs.")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "A quiet cabal." in text          # keyless group always-on
    assert "Sleeps in brine." in text        # 'wyrm' key matched
    assert "Cuts anything." not in text      # 'knife' key absent
```

Append to `backend/tests/test_lorebook_store.py`:

```python
def test_commit_accepts_new_kind_categories(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import lorebook
    created = lorebook.commit(tmp_path, [
        {"name": "Salt Knife", "keys": ["knife"], "body": "sharp", "category": "items"}])
    assert created == [{"kind": "items", "id": "salt-knife"}]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_entities_store.py backend/tests/test_sync_store.py backend/tests/test_context.py backend/tests/test_lorebook_store.py -q`
Expected: FAIL — `UnknownKind: items` (and the updated counts assertion fails).

- [ ] **Step 3: Implement**

`backend/src/grimoire/store/entities.py:17`:

```python
ENTITY_KINDS: tuple[str, ...] = ("locations", "lore", "items", "groups", "creatures")
```

`backend/src/grimoire/store/context.py` in `_world_info`, change the kind loop (keep lore first, locations' keyless-suppression special case stays locations-only):

```python
    for kind in ("lore", "locations", "items", "groups", "creatures"):
```

- [ ] **Step 4: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS. (If any other test asserts on the exact `entity_counts` dict or `ENTITY_KINDS`, update it to the five-kind reality — that is the only sanctioned reason to touch other tests here.)

Also verify no route-segment collision: `items`/`groups`/`creatures` must not appear as literal path segments before the generic `/{kind}` routes in `routes.py`. Run: `grep -nE "worlds/\{wid\}/(items|groups|creatures)|campaigns/\{cid\}/(items|groups|creatures)" backend/src/grimoire/routes.py` — Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/entities.py backend/src/grimoire/store/context.py backend/tests
git commit -m "feat(entities): first-class items/groups/creatures kinds (#36)"
```

---

### Task 2: Frontend — surface the new kinds (#36)

**Files:**
- Modify: `frontend/src/api/client.ts:79` (`EntityKind`)
- Modify: `frontend/src/routes/WorldView.tsx:11-18` (`TABS`), render block
- Modify: `frontend/src/components/EntityEditor.tsx:32` (`label`)
- Modify: `frontend/src/components/LorebookImport.tsx:85-88` (category options)
- Modify: `frontend/src/routes/WorldsView.tsx:5-9` (`footerLabel`)
- Test: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: backend kinds from Task 1.
- Produces: `EntityKind = "locations" | "lore" | "items" | "groups" | "creatures"`; `KIND_LABELS: Record<EntityKind, string>` exported from `EntityEditor.tsx`.

- [ ] **Step 1: Write failing test**

Append to `frontend/src/components/EntityEditor.test.tsx`:

```tsx
test("new kinds render the list/detail pattern with their own label", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt-circle", name: "Salt Circle" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "salt-circle", name: "Salt Circle" }, body: "A quiet **cabal**" });
  const { container } = render(<EntityEditor wid="w" kind="groups" />);
  expect(await screen.findByRole("button", { name: /\+ new group/i })).toBeInTheDocument();
  fireEvent.click(screen.getByText("Salt Circle"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "groups", "salt-circle"));
  expect(screen.getByText("cabal")).toBeInTheDocument();       // markdown rendered, read-only
  expect(container.querySelector("textarea")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: /^edit$/i }));
  expect(container.querySelector("textarea")).not.toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/EntityEditor.test.tsx`
Expected: FAIL — TypeScript/runtime: `"groups"` not assignable to `EntityKind`, and no "+ New group" button.

- [ ] **Step 3: Implement**

`frontend/src/api/client.ts:79`:

```ts
export type EntityKind = "locations" | "lore" | "items" | "groups" | "creatures";
```

`frontend/src/components/EntityEditor.tsx` — replace line 32 (`const label = kind === "lore" ? ...`) with an exported map + lookup:

```ts
export const KIND_LABELS: Record<EntityKind, string> = {
  locations: "location", lore: "lore entry", items: "item", groups: "group", creatures: "creature",
};
```
```ts
  const label = KIND_LABELS[kind];
```

`frontend/src/routes/WorldView.tsx` — `TABS`:

```ts
const TABS = [
  { key: "characters", label: "Characters" },
  { key: "pcs", label: "PCs" },
  { key: "tags", label: "Tags" },
  { key: "locations", label: "Locations" },
  { key: "lore", label: "Lore" },
  { key: "items", label: "Items" },
  { key: "groups", label: "Groups" },
  { key: "creatures", label: "Creatures" },
  { key: "greetings", label: "Greetings" },
] as const;
```

and after the locations render line (114), add:

```tsx
      {tab === "items" && <EntityEditor wid={wid} scope={scope} kind="items" />}
      {tab === "groups" && <EntityEditor wid={wid} scope={scope} kind="groups" />}
      {tab === "creatures" && <EntityEditor wid={wid} scope={scope} kind="creatures" />}
```

`frontend/src/components/LorebookImport.tsx` — add options inside the category `<select>`:

```tsx
                        <option value="items">items</option>
                        <option value="groups">groups</option>
                        <option value="creatures">creatures</option>
```

`frontend/src/routes/WorldsView.tsx` — `footerLabel` (new kinds shown only when non-zero, so existing cards are unchanged):

```ts
function footerLabel(counts: Record<string, number> | undefined): string {
  const c = counts ?? {};
  const chars = (c.characters ?? 0) + (c.pcs ?? 0);
  const parts = [`${c.locations ?? 0} LOCATIONS`, `${chars} CHARACTERS`, `${c.lore ?? 0} LORE`];
  for (const [key, label] of [["items", "ITEMS"], ["groups", "GROUPS"], ["creatures", "CREATURES"]] as const) {
    if (c[key]) parts.push(`${c[key]} ${label}`);
  }
  return parts.join(" · ");
}
```

- [ ] **Step 4: Run tests and typecheck**

Run (from `frontend/`): `npx vitest run && npx tsc -b`
Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): items/groups/creatures tabs, labels, counts (#36)"
```

---

### Task 3: Frontend — images for every entity kind

**Files:**
- Modify: `frontend/src/components/EntityEditor.tsx` (drop the locations-only gates)
- Test: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: backend asset routes are already kind-generic (`routes.py:1218-1244`, `_entity_kind_or_404` keys off `ENTITY_KINDS`); `_entity_list` already returns `has_image`/`image_v` for every kind. No backend change.

- [ ] **Step 1: Write failing test**

Append to `frontend/src/components/EntityEditor.test.tsx`:

```tsx
test("image shelf renders for non-location kinds", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "salt-knife", name: "Salt Knife", has_image: true }]);
  (api.readEntity as any).mockResolvedValue({ meta: { id: "salt-knife", name: "Salt Knife" }, body: "sharp" });
  (api.listEntityImages as any).mockResolvedValue([{ name: "avatar", v: "1" }]);
  const { container } = render(<EntityEditor wid="w" kind="items" />);
  fireEvent.click(await screen.findByText("Salt Knife"));
  await waitFor(() => expect(api.listEntityImages).toHaveBeenCalledWith({ kind: "world", id: "w" }, "items", "salt-knife"));
  expect(screen.getByText("Images")).toBeInTheDocument();            // shelf present
  expect(container.querySelector(".loc-row-img")).not.toBeNull();    // rail thumbnail
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/EntityEditor.test.tsx`
Expected: FAIL — `listEntityImages` never called for kind `items`, no "Images" section.

- [ ] **Step 3: Implement**

In `frontend/src/components/EntityEditor.tsx`:

1. `reloadImages` (line 70-76): delete the `if (kind !== "locations") { setImages([]); return; }` guard — always fetch.
2. Rail row (line 190-198): change the thumbnail condition from `kind === "locations" && e.has_image` to `e.has_image`, and the row class from `(kind === "locations" ? " loc-row" : "")` to `(e.has_image ? " loc-row" : "")`.
3. Detail view: change `kind === "locations" && editing && hasPrimary` (line 234) to `editing && hasPrimary`, and the shelf block condition `kind === "locations" && editing` (line 243) to `editing` — the Images shelf renders for every kind.

(The `OwnedLorePanel` gate at line 307 stays `kind === "locations"` — lore ownership by other kinds is deferred, issue #220.)

- [ ] **Step 4: Run tests and typecheck**

Run (from `frontend/`): `npx vitest run && npx tsc -b`
Expected: PASS. If a pre-existing test asserts that lore has *no* image shelf, update it — all kinds have images now, by design.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): image shelf and rail thumbnails for every entity kind"
```

---

### Task 4: Backend — per-kind typed fields (#37)

**Files:**
- Create: `backend/src/grimoire/store/entity_schema.py`
- Modify: `backend/src/grimoire/store/entities.py` (`create_entity`/`update_entity` gain `fields`)
- Modify: `backend/src/grimoire/store/overlay.py:122-139` (pass-through)
- Modify: `backend/src/grimoire/store/__init__.py` (import `entity_schema`)
- Modify: `backend/src/grimoire/routes.py:76-87` (`EntityCreate`/`EntityUpdate`), `_entity_create`/`_entity_update`/`_campaign_entity_create`/`_campaign_entity_update`
- Test: `backend/tests/test_entity_schema.py` (new), `backend/tests/test_routes.py`

**Interfaces:**
- Produces:
  - `entity_schema.FIELDS: dict[str, tuple[dict[str, str], ...]]` — kind → ordered `{key, label, widget}` specs.
  - `entity_schema.invalid_keys(kind: str, fields: dict) -> list[str]`.
  - `entities.create_entity(..., fields: dict[str, str] | None = None)` — non-empty values become frontmatter scalars.
  - `entities.update_entity(..., fields: dict[str, str] | None = None)` — provided keys set; empty string removes the key (mirrors omit-when-empty).
  - `overlay.create_entity`/`overlay.update_entity` gain the same `fields` kwarg.
  - HTTP: `POST/PUT` entity bodies accept `"fields": {"rarity": "rare"}`; undeclared keys → 400.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_entity_schema.py`:

```python
from pathlib import Path

from grimoire.store import entities, entity_schema


def test_descriptor_shape():
    assert [f["key"] for f in entity_schema.FIELDS["items"]] == ["item_type", "rarity"]
    assert [f["key"] for f in entity_schema.FIELDS["groups"]] == ["group_type"]
    assert [f["key"] for f in entity_schema.FIELDS["creatures"]] == ["creature_type", "threat"]
    assert all(f["widget"] == "text" for fs in entity_schema.FIELDS.values() for f in fs)


def test_invalid_keys():
    assert entity_schema.invalid_keys("items", {"rarity": "rare"}) == []
    assert entity_schema.invalid_keys("items", {"holder": "mara", "rarity": "x"}) == ["holder"]
    assert entity_schema.invalid_keys("lore", {"rarity": "x"}) == ["rarity"]  # lore declares no fields


def test_fields_round_trip(tmp_path: Path):
    eid = entities.create_entity(tmp_path, "items", "Salt Knife", "sharp",
                                 fields={"item_type": "weapon", "rarity": ""})
    got = entities.read_entity(tmp_path, "items", eid)
    assert got["meta"]["item_type"] == "weapon"
    assert "rarity" not in got["meta"]                       # empty omitted on create
    entities.update_entity(tmp_path, "items", eid, fields={"rarity": "rare"})
    assert entities.read_entity(tmp_path, "items", eid)["meta"]["rarity"] == "rare"
    entities.update_entity(tmp_path, "items", eid, fields={"rarity": ""})
    got = entities.read_entity(tmp_path, "items", eid)
    assert "rarity" not in got["meta"]                       # empty clears on update
    assert got["meta"]["item_type"] == "weapon"              # untouched key preserved
    assert got["body"].strip() == "sharp"


def test_fields_survive_in_list_summaries(tmp_path: Path):
    entities.create_entity(tmp_path, "creatures", "Marsh Wyrm", "x", fields={"threat": "apex"})
    assert entities.list_entities(tmp_path, "creatures")[0]["threat"] == "apex"
```

Append to `backend/tests/test_routes.py` (uses that file's `client` fixture and `_world` helper):

```python
def test_entity_fields_http_round_trip_and_validation(client):
    wid = _world(client)
    r = client.post(f"/api/worlds/{wid}/items",
                    json={"name": "Salt Knife", "body": "sharp", "fields": {"rarity": "rare"}})
    assert r.status_code == 200
    eid = r.json()["id"]
    assert client.get(f"/api/worlds/{wid}/items/{eid}").json()["meta"]["rarity"] == "rare"
    # undeclared key -> 400 naming the offender
    r = client.post(f"/api/worlds/{wid}/items",
                    json={"name": "Bad", "fields": {"holder": "mara"}})
    assert r.status_code == 400
    assert "holder" in r.json()["detail"]
    # empty value clears the key on update
    r = client.put(f"/api/worlds/{wid}/items/{eid}", json={"fields": {"rarity": ""}})
    assert r.status_code == 200
    assert "rarity" not in client.get(f"/api/worlds/{wid}/items/{eid}").json()["meta"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_entity_schema.py -q`
Expected: FAIL — `ModuleNotFoundError: grimoire.store.entity_schema`.

- [ ] **Step 3: Implement**

Create `backend/src/grimoire/store/entity_schema.py`:

```python
"""Per-kind typed field descriptors (#37).

Fields are extra string scalars in the entity's frontmatter, so entity_hash,
sync conflict detection, and campaign copy-on-write cover them for free. The
frontend mirrors this table as ENTITY_FIELDS in frontend/src/api/client.ts —
keep the two in sync. Widgets are "text" only for now; ref-valued fields and
game mechanics are deferred (issues #221/#222).
"""

from __future__ import annotations

FIELDS: dict[str, tuple[dict[str, str], ...]] = {
    "items": (
        {"key": "item_type", "label": "Type", "widget": "text"},
        {"key": "rarity", "label": "Rarity", "widget": "text"},
    ),
    "groups": (
        {"key": "group_type", "label": "Type", "widget": "text"},
    ),
    "creatures": (
        {"key": "creature_type", "label": "Type", "widget": "text"},
        {"key": "threat", "label": "Threat", "widget": "text"},
    ),
}


def field_keys(kind: str) -> tuple[str, ...]:
    return tuple(f["key"] for f in FIELDS.get(kind, ()))


def invalid_keys(kind: str, fields: dict) -> list[str]:
    allowed = set(field_keys(kind))
    return sorted(k for k in fields if k not in allowed)
```

`backend/src/grimoire/store/entities.py` — `create_entity` gains `fields=None` (after `taken`); before the `write_text`:

```python
    for k, v in (fields or {}).items():
        if v:
            meta[k] = v
```

`update_entity` gains `fields: dict[str, str] | None = None`; before composing `new_body`:

```python
    for k, v in (fields or {}).items():
        if v:
            meta[k] = v
        else:
            meta.pop(k, None)
```

`backend/src/grimoire/store/overlay.py` — `create_entity` gains `fields: dict | None = None`, passed through to `entities.create_entity(..., fields=fields)`; `update_entity` gains `fields: dict | None = None`, passed to `entities.update_entity(..., fields=fields)`.

`backend/src/grimoire/store/__init__.py` — add `entity_schema` to the module import list (match the file's existing style).

`backend/src/grimoire/routes.py`:

```python
class EntityCreate(BaseModel):
    name: str
    body: str = ""
    keys: str = ""
    owners: str = ""
    fields: dict | None = None


class EntityUpdate(BaseModel):
    name: str | None = None
    body: str | None = None
    keys: str | None = None
    owners: str | None = None
    fields: dict | None = None
```

Add a helper next to `_entity_create` and call it at the top of all four create/update handlers (`_entity_create`, `_entity_update`, `_campaign_entity_create`, `_campaign_entity_update`):

```python
def _check_fields(kind: str, fields: dict | None) -> None:
    bad = store.entity_schema.invalid_keys(kind, fields or {})
    if bad:
        raise HTTPException(status_code=400, detail=f"unknown fields for {kind}: {', '.join(bad)}")
```

then thread `fields=body.fields` into the `store.entities.create_entity` / `update_entity` / `store.overlay.create_entity` / `update_entity` calls.

- [ ] **Step 4: Run the backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(entities): per-kind typed frontmatter fields with validation (#37)"
```

---

### Task 5: Frontend — typed fields in the entity editor (#37)

**Files:**
- Modify: `frontend/src/api/client.ts` (`ENTITY_FIELDS`, `EntitySummary`/`EntityDetail`, create/update payloads)
- Modify: `frontend/src/components/EntityEditor.tsx` (form inputs + sidebar chips)
- Test: `frontend/src/components/EntityEditor.test.tsx`

**Interfaces:**
- Consumes: Task 4's HTTP contract (`fields` in POST/PUT bodies; values echoed in entity meta).
- Produces: `ENTITY_FIELDS: Record<EntityKind, { key: string; label: string }[]>` exported from `client.ts`.

- [ ] **Step 1: Write failing tests**

Append to `frontend/src/components/EntityEditor.test.tsx`:

```tsx
test("typed fields render in the form and are sent on create", async () => {
  render(<EntityEditor wid="w" kind="items" />);
  fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Salt Knife" } });
  fireEvent.change(screen.getByLabelText("Type"), { target: { value: "weapon" } });
  fireEvent.change(screen.getByLabelText("Rarity"), { target: { value: "rare" } });
  fireEvent.click(screen.getByRole("button", { name: /create item/i }));
  await waitFor(() =>
    expect(api.createEntity).toHaveBeenCalledWith({ kind: "world", id: "w" }, "items", {
      name: "Salt Knife", body: "", keys: "", owners: "",
      fields: { item_type: "weapon", rarity: "rare" },
    }),
  );
});

test("typed field values show as chips in the detail sidebar", async () => {
  (api.listEntities as any).mockResolvedValue([{ id: "marsh-wyrm", name: "Marsh Wyrm" }]);
  (api.readEntity as any).mockResolvedValue({
    meta: { id: "marsh-wyrm", name: "Marsh Wyrm", creature_type: "wyrm", threat: "apex" }, body: "old" });
  const { container } = render(<EntityEditor wid="w" kind="creatures" />);
  fireEvent.click(await screen.findByText("Marsh Wyrm"));
  await waitFor(() => expect(api.readEntity).toHaveBeenCalled());
  const side = container.querySelector(".detail-sidebar") as HTMLElement;
  expect(within(side).getByText(/Type: wyrm/)).toBeInTheDocument();
  expect(within(side).getByText(/Threat: apex/)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/EntityEditor.test.tsx`
Expected: FAIL — no "Type"/"Rarity" inputs, `createEntity` called without `fields`.

- [ ] **Step 3: Implement**

`frontend/src/api/client.ts` — next to `EntityKind`:

```ts
// Mirrors backend/src/grimoire/store/entity_schema.py — keep in sync.
export const ENTITY_FIELDS: Record<EntityKind, { key: string; label: string }[]> = {
  locations: [],
  lore: [],
  items: [{ key: "item_type", label: "Type" }, { key: "rarity", label: "Rarity" }],
  groups: [{ key: "group_type", label: "Type" }],
  creatures: [{ key: "creature_type", label: "Type" }, { key: "threat", label: "Threat" }],
};
```

Widen the entity types so field scalars type-check:

```ts
export type EntitySummary = { id: string; name: string; keys?: string; owners?: string;
  has_image?: boolean; image_v?: string | null } & Record<string, unknown>;
export type EntityDetail = {
  meta: { id: string; name: string; keys?: string; owners?: string; sd_prompt?: string } & Record<string, unknown>;
  body: string;
};
```

Extend the create/update payload types:

```ts
  createEntity: (scope: EntityScope, kind: EntityKind,
                 body: { name: string; body?: string; keys?: string; owners?: string; fields?: Record<string, string> }) =>
    request<{ id: string }>("POST", `${entityBase(scope)}/${kind}`, body),
  updateEntity: (scope: EntityScope, kind: EntityKind, id: string,
                 patch: { name?: string; body?: string; keys?: string; owners?: string; fields?: Record<string, string> }) =>
    request<{ ok: boolean }>("PUT", `${entityBase(scope)}/${kind}/${id}`, patch),
```

`frontend/src/components/EntityEditor.tsx`:

1. Import `ENTITY_FIELDS` from `../api/client`; add state:

```ts
  const fieldSpecs = ENTITY_FIELDS[kind];
  const [fields, setFields] = useState<Record<string, string>>({});
```

2. In `select()`: `setFields(Object.fromEntries(fieldSpecs.map((f) => [f.key, String((e.meta as any)[f.key] ?? "")])));`
3. In `resetForm()` and the `nav` new-entry branch: `setFields({});`
4. In `save()`, add `...(fieldSpecs.length ? { fields } : {})` to both the `updateEntity` and `createEntity` payloads.
5. In the form, after the Keys field:

```tsx
            {fieldSpecs.map((f) => (
              <Field key={f.key} label={f.label}>
                <input type="text" value={fields[f.key] ?? ""}
                       onChange={(e) => setFields({ ...fields, [f.key]: e.target.value })} />
              </Field>
            ))}
```

6. In the detail sidebar (after the Keys side-section):

```tsx
              {fieldSpecs.some((f) => fields[f.key]) && (
                <div className="side-section">
                  <h4>Details</h4>
                  <div className="chips">
                    {fieldSpecs.filter((f) => fields[f.key]).map((f) => (
                      <span key={f.key} className="chip on">{f.label}: {fields[f.key]}</span>
                    ))}
                  </div>
                </div>
              )}
```

- [ ] **Step 4: Run tests and typecheck**

Run (from `frontend/`): `npx vitest run && npx tsc -b`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): typed per-kind fields in entity forms and sidebar (#37)"
```

---

### Task 6: Backend — greetings count in world counts (#39)

**Files:**
- Modify: `backend/src/grimoire/store/greetings.py` (add `greeting_count`)
- Modify: `backend/src/grimoire/store/worlds.py:50-51,76-77` (counts in `list_worlds`/`read_world`)
- Test: `backend/tests/test_worlds_store.py`

**Interfaces:**
- Produces: `greetings.greeting_count(root: Path) -> int`; world payload `counts` gains `"greetings"`.

- [ ] **Step 1: Write failing test**

Append to `backend/tests/test_worlds_store.py` (match the file's existing `GRIMOIRE_HOME` setup style):

```python
def test_world_counts_include_greetings(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    from grimoire.store import greetings, worlds
    wid = worlds.create_world("Saltmarch")
    root = worlds.world_root(wid)
    (root / "greetings").mkdir()
    (root / "greetings" / "gala.md").write_text("---\nname: Gala\n---\n", encoding="utf-8")
    assert worlds.read_world(wid)["counts"]["greetings"] == 1
    assert worlds.list_worlds()[0]["counts"]["greetings"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_worlds_store.py -q`
Expected: FAIL — `KeyError: 'greetings'`.

- [ ] **Step 3: Implement**

`backend/src/grimoire/store/greetings.py` — next to `list_greetings`:

```python
def greeting_count(root: Path) -> int:
    d = _greetings_dir(root)
    return len(list(d.glob("*.md"))) if d.exists() else 0
```

`backend/src/grimoire/store/worlds.py` — add `greetings` to the module import line (`from . import characters, entities, greetings, pcs`; if that creates an import cycle, import inside the two functions instead), and extend both counts dicts:

```python
                "counts": {**entities.entity_counts(d), "characters": characters.character_count(d),
                           "pcs": pcs.pc_count(d), "greetings": greetings.greeting_count(d)},
```

(same pattern with `root` in `read_world`).

- [ ] **Step 4: Run the backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(worlds): greetings count in world payloads (#39)"
```

---

### Task 7: Frontend — Overview tab (#39)

**Files:**
- Create: `frontend/src/components/WorldOverview.tsx`
- Modify: `frontend/src/routes/WorldView.tsx` (Overview tab, default for worlds, hidden for campaigns)
- Test: `frontend/src/components/WorldOverview.test.tsx` (new)

**Interfaces:**
- Consumes: `api.getWorld` (counts incl. `greetings` from Task 6), `api.listGreetings`, `api.readGreeting` (plotmap edges), `api.listCharacters` (`tagline` on `CharacterSummary`), `api.listUntaggedImages`.
- Produces: `WorldOverview({ wid, onNavigate })` — `onNavigate(tabKey: string)` is `WorldView.setTab`.

- [ ] **Step 1: Write failing test**

Create `frontend/src/components/WorldOverview.test.tsx`:

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { WorldOverview } from "./WorldOverview";

vi.mock("../api/client", () => ({
  api: {
    getWorld: vi.fn(), listGreetings: vi.fn(), readGreeting: vi.fn(),
    listCharacters: vi.fn(), listUntaggedImages: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getWorld as any).mockResolvedValue({ meta: { id: "w", name: "Saltmarch" }, body: "",
    counts: { characters: 2, pcs: 1, locations: 3, lore: 5, items: 0, groups: 1, creatures: 0, greetings: 2 } });
  (api.listGreetings as any).mockResolvedValue([{ id: "g1", name: "Gala" }, { id: "g2", name: "Docks" }]);
  (api.readGreeting as any).mockResolvedValue({ meta: { id: "g1" }, body: "", predecessors: [],
    edges: { leads_to: ["g2"], excludes: [] } });
  (api.listCharacters as any).mockResolvedValue([
    { id: "mara", name: "Mara", default_version: "default", versions: [], tagline: "a smuggler" },
    { id: "winifred", name: "Winifred", default_version: "default", versions: [] },  // no tagline
  ]);
  (api.listUntaggedImages as any).mockResolvedValue([]);
});

test("renders count tiles that navigate to their tab", async () => {
  const nav = vi.fn();
  render(<WorldOverview wid="w" onNavigate={nav} />);
  fireEvent.click(await screen.findByRole("button", { name: /3\s+Locations/i }));
  expect(nav).toHaveBeenCalledWith("locations");
  fireEvent.click(screen.getByRole("button", { name: /1\s+Groups/i }));
  expect(nav).toHaveBeenCalledWith("groups");
});

test("derives the setup checklist", async () => {
  const nav = vi.fn();
  render(<WorldOverview wid="w" onNavigate={nav} />);
  expect(await screen.findByText(/plot map has connections/i)).toBeInTheDocument();
  const missing = screen.getByText(/1 character missing a tagline/i);
  fireEvent.click(missing);                       // next-action: jump to Characters
  expect(nav).toHaveBeenCalledWith("characters");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/WorldOverview.test.tsx`
Expected: FAIL — module `./WorldOverview` does not exist.

- [ ] **Step 3: Implement**

Create `frontend/src/components/WorldOverview.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api } from "../api/client";

const TILES = [
  { key: "characters", label: "Characters", tab: "characters" },
  { key: "pcs", label: "PCs", tab: "pcs" },
  { key: "locations", label: "Locations", tab: "locations" },
  { key: "lore", label: "Lore", tab: "lore" },
  { key: "items", label: "Items", tab: "items" },
  { key: "groups", label: "Groups", tab: "groups" },
  { key: "creatures", label: "Creatures", tab: "creatures" },
  { key: "greetings", label: "Greetings", tab: "greetings" },
] as const;

type Check = { label: string; ok: boolean; tab: string };

export function WorldOverview({ wid, onNavigate }: { wid: string; onNavigate: (tab: string) => void }) {
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [checks, setChecks] = useState<Check[]>([]);

  useEffect(() => {
    let live = true;
    (async () => {
      const scope = { kind: "world" as const, id: wid };
      const [w, greetings, chars, untagged] = await Promise.all([
        api.getWorld(wid), api.listGreetings(scope), api.listCharacters(scope), api.listUntaggedImages(wid),
      ]);
      const details = await Promise.all(greetings.map((g) => api.readGreeting(scope, g.id)));
      if (!live) return;
      const c = w.counts ?? {};
      const hasEdges = details.some((d) => d.edges.leads_to.length > 0 || d.edges.excludes.length > 0);
      const noTagline = chars.filter((ch) => !ch.tagline).length;
      setCounts(c);
      setChecks([
        { label: "Has a player character", ok: (c.pcs ?? 0) > 0, tab: "pcs" },
        { label: "Has a location", ok: (c.locations ?? 0) > 0, tab: "locations" },
        { label: "Has a greeting", ok: greetings.length > 0, tab: "greetings" },
        { label: "Plot map has connections", ok: hasEdges, tab: "greetings" },
        { label: noTagline
            ? `${noTagline} character${noTagline === 1 ? "" : "s"} missing a tagline`
            : "All characters have taglines", ok: noTagline === 0, tab: "characters" },
        { label: untagged.length
            ? `${untagged.length} untagged greeting image${untagged.length === 1 ? "" : "s"}`
            : "All greeting images tagged", ok: untagged.length === 0, tab: "greetings" },
      ]);
    })();
    return () => { live = false; };
  }, [wid]);

  return (
    <div className="world-overview">
      <div className="overview-tiles">
        {TILES.map((t) => (
          <button key={t.key} className="overview-tile" onClick={() => onNavigate(t.tab)}>
            <span className="overview-count">{counts[t.key] ?? 0}</span>
            <span className="overview-label">{t.label}</span>
          </button>
        ))}
      </div>
      <div className="side-section">
        <h4>Setup checklist</h4>
        <ul className="overview-checklist">
          {checks.map((c) => (
            <li key={c.label}>
              <button className={"check-row" + (c.ok ? " ok" : "")} onClick={() => onNavigate(c.tab)}>
                {c.ok ? "✓" : "○"} {c.label}
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
```

Add matching styles where the app's component styles live (follow how `.editor`/`.side-section` styles are organized — likely `frontend/src/index.css` or `App.css`): a responsive `overview-tiles` grid (`display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr)); gap:8px`), `.overview-tile` as a bordered button with the count large above the label, `.check-row` as a borderless full-width text button, `.check-row.ok` in the muted/success color.

`frontend/src/routes/WorldView.tsx`:

1. Import `WorldOverview`; add `{ key: "overview", label: "Overview" }` as the FIRST entry of `TABS`.
2. Default tab: `const [tab, setTab] = useState<TabKey>(campaign ? "characters" : "overview");`
3. Campaign filter (line 50): `const tabs = campaign ? TABS.filter((t) => t.key !== "tags" && t.key !== "overview") : TABS;`
4. Render: `{!campaign && tab === "overview" && <WorldOverview wid={wid} onNavigate={(t) => setTab(t as TabKey)} />}`

- [ ] **Step 4: Run tests and typecheck**

Run (from `frontend/`): `npx vitest run && npx tsc -b`
Expected: PASS. If an existing `WorldView` test assumed the Characters tab is the world default, update it to click the Characters tab first.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "feat(frontend): world Overview tab with count tiles and setup checklist (#39)"
```

---

### Task 8: Backend — `store/groupstate.py` (#47)

**Files:**
- Create: `backend/src/grimoire/store/groupstate.py`
- Modify: `backend/src/grimoire/store/__init__.py` (import `groupstate`)
- Test: `backend/tests/test_groupstate_store.py` (new)

**Interfaces:**
- Produces:
  - `groupstate.LABELS: dict[str, str]` — ordered `{"goals": "Goals", "resources": "Resources", "focus": "Focus", "public_perception": "Public perception", "secrets": "Secrets"}`.
  - `groupstate.FIELDS: tuple[str, ...] == tuple(LABELS)`.
  - `groupstate.state_path(root, gid) -> Path` — `<root>/groups/<gid>/state.md`.
  - `groupstate.read_state(root, gid) -> dict | None` — the five fields + `"updated"`.
  - `groupstate.write_state(root, gid, body) -> None`.
  - `groupstate.compose_body(values: dict[str, str]) -> str` — bare prose when only `goals` is set, `## `-headed sections otherwise; empty sections omitted.

- [ ] **Step 1: Write failing tests**

Create `backend/tests/test_groupstate_store.py`:

```python
from grimoire.store import campaigns, groupstate, worlds


def _croot(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    return campaigns.campaign_root(campaigns.create_campaign("Run", wid))


def test_read_missing_is_none(monkeypatch, tmp_path):
    assert groupstate.read_state(_croot(monkeypatch, tmp_path), "salt-circle") is None


def test_write_then_read_roundtrip(monkeypatch, tmp_path):
    root = _croot(monkeypatch, tmp_path)
    groupstate.write_state(root, "salt-circle",
                           "## Goals\nFind the ledger.\n\n## Secrets\nThe abbot is a member.")
    st = groupstate.read_state(root, "salt-circle")
    assert st["goals"] == "Find the ledger."
    assert st["secrets"] == "The abbot is a member."
    assert st["resources"] == "" and st["focus"] == "" and st["public_perception"] == ""
    assert st["updated"]


def test_unheaded_body_reads_as_goals(monkeypatch, tmp_path):
    root = _croot(monkeypatch, tmp_path)
    groupstate.write_state(root, "salt-circle", "Quietly expanding.")
    st = groupstate.read_state(root, "salt-circle")
    assert st["goals"] == "Quietly expanding."
    assert st["secrets"] == ""


def test_prose_containing_fake_header_not_split(monkeypatch, tmp_path):
    root = _croot(monkeypatch, tmp_path)
    body = "Expanding.\n## Secrets\nnot a real section"  # doesn't START with a header
    groupstate.write_state(root, "salt-circle", body)
    assert groupstate.read_state(root, "salt-circle")["goals"] == body


def test_compose_body_bare_when_only_goals():
    assert groupstate.compose_body({"goals": "Expand."}) == "Expand."


def test_compose_body_headed_and_ordered():
    body = groupstate.compose_body({"secrets": "S.", "goals": "G.", "focus": "F."})
    assert body.index("## Goals\nG.") < body.index("## Focus\nF.") < body.index("## Secrets\nS.")
    assert "## Resources" not in body


def test_compose_body_empty_when_all_blank():
    assert groupstate.compose_body({}) == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_groupstate_store.py -q`
Expected: FAIL — `ImportError: cannot import name 'groupstate'`.

- [ ] **Step 3: Implement**

Create `backend/src/grimoire/store/groupstate.py`:

```python
"""Per-group campaign state stored beside the campaign's group records at
<root>/groups/<gid>/state.md (a sibling directory of the flat groups/<gid>.md,
like <kind>/<eid>/assets/): a standing snapshot in optional `## `-headed prose
sections. A body whose first non-empty line is not a recognized header is read
wholesale as `goals`. Snapshot only — rewritten each absorb. Mirrors
playstate.py; state is campaign-local by definition (never world-side).
"""

from __future__ import annotations

from pathlib import Path

from .frontmatter import dump_frontmatter, parse_frontmatter
from .paths import now_iso

LABELS: dict[str, str] = {
    "goals": "Goals", "resources": "Resources", "focus": "Focus",
    "public_perception": "Public perception", "secrets": "Secrets",
}
FIELDS: tuple[str, ...] = tuple(LABELS)
_HEADERS = {label.lower(): key for key, label in LABELS.items()}


def state_path(root: Path, gid: str) -> Path:
    return root / "groups" / gid / "state.md"


def _is_header(line: str) -> str | None:
    stripped = line.strip()
    if stripped.startswith("## ") and stripped[3:].strip().lower() in _HEADERS:
        return _HEADERS[stripped[3:].strip().lower()]
    return None


def _parse_body(body: str) -> dict:
    fields = {k: "" for k in FIELDS}
    lines = body.splitlines()
    first = next((ln for ln in lines if ln.strip()), "")
    if _is_header(first) is None:
        fields["goals"] = body.strip()
        return fields

    cur, buf = None, []

    def flush():
        if cur is not None:
            fields[cur] = "\n".join(buf).strip()

    for line in lines:
        head = _is_header(line)
        if head is not None:
            flush()
            cur, buf = head, []
            continue
        buf.append(line)
    flush()
    return fields


def compose_body(values: dict[str, str]) -> str:
    vals = {k: (values.get(k, "") or "").strip() for k in FIELDS}
    non_empty = [k for k in FIELDS if vals[k]]
    if non_empty == ["goals"]:
        return vals["goals"]
    return "\n\n".join(f"## {LABELS[k]}\n{vals[k]}" for k in non_empty)


def read_state(root: Path, gid: str) -> dict | None:
    p = state_path(root, gid)
    if not p.exists():
        return None
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    return {**_parse_body(body), "updated": meta.get("updated", "")}


def write_state(root: Path, gid: str, body: str) -> None:
    p = state_path(root, gid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dump_frontmatter({"updated": now_iso()}, body.strip() + "\n"),
                 encoding="utf-8")
```

Add `groupstate` to `backend/src/grimoire/store/__init__.py`'s import list.

- [ ] **Step 4: Run tests**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_groupstate_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests
git commit -m "feat(groupstate): campaign-scoped group state store (#47)"
```

---

### Task 9: Backend — group state in the scene context (#47)

**Files:**
- Modify: `backend/src/grimoire/store/context.py` (`_world_info` returns activated entries; `_assemble` gathers group states; `_SECTIONS`)
- Create: `templates/scene/sections/group_state.j2`
- Modify: `templates/scene/system.j2` (include the new section after world_info)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `groupstate.read_state` (Task 8); activation from Task 1.
- Produces: `_world_info(cid, recent_text, exclude, present) -> list[dict]` — activated entries as `{"body": str, "kind": str, "id": str}` (was `list[str]` of bodies). Template var `group_states: [{name, goals, resources, focus, public_perception, secrets}]`; token-inspector section label `"Group state"`.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_context.py`:

```python
from grimoire.store import groupstate  # noqa: E402


def test_group_state_rides_group_activation(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")  # keyless -> always-on
    groupstate.write_state(croot, "salt-circle", "## Goals\nFind the ledger.")
    scenes.append_message(cid, sid, "user", "hello")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "A quiet cabal." in text
    assert "Find the ledger." in text
    assert "# Group state" in text


def test_keyed_group_state_absent_when_group_inactive(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.", keys="cabal")
    groupstate.write_state(croot, "salt-circle", "## Goals\nFind the ledger.")
    scenes.append_message(cid, sid, "user", "nothing relevant")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "A quiet cabal." not in text
    assert "Find the ledger." not in text


def test_group_without_state_adds_no_section(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")
    scenes.append_message(cid, sid, "user", "hello")
    text = context.build_messages(cid, sid)[0]["content"]
    assert "A quiet cabal." in text
    assert "# Group state" not in text


def test_group_state_in_context_sections(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")
    groupstate.write_state(croot, "salt-circle", "## Goals\nFind the ledger.")
    scenes.append_message(cid, sid, "user", "hello")
    labels = [s["label"] for s in context.context_sections(cid, sid)]
    assert "Group state" in labels
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: FAIL — state text absent, no "# Group state".

- [ ] **Step 3: Implement**

`backend/src/grimoire/store/context.py`:

1. Import `groupstate` in the `from . import (...)` list.
2. `_world_info` — carry refs through and return the activated entries (the docstring and return type change):

```python
def _world_info(cid: str, recent_text: str, exclude: frozenset = frozenset(),
                present: frozenset = frozenset()) -> list[dict]:
    """Activated lore/location/item/group/creature entries as
    {"body", "kind", "id"} dicts — _assemble renders the bodies and uses the
    refs (e.g. activated groups pull their campaign state into context)."""
    entries = []
    for kind in ("lore", "locations", "items", "groups", "creatures"):
        for meta in overlay.list_entities(cid, kind):
            if kind == "locations" and meta["id"] in exclude:
                continue
            e = overlay.read_entity(cid, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            owners = [o.strip() for o in e["meta"].get("owners", "").split(",") if o.strip()]
            if kind == "locations" and not keys:
                continue  # a keyless location surfaces only as the current setting, never always-on
            entries.append({"body": e["body"].strip(), "keys": keys, "owners": owners,
                            "kind": kind, "id": meta["id"],
                            "name": e["meta"].get("name", meta["id"])})
    return activate(entries, recent_text, present)
```

3. New helper below `_character_states`:

```python
def _group_states(cid: str, croot, activated: list[dict]) -> list[dict]:
    """State for each activated group that has a state.md — same failure policy
    as _character_states: a garbled file omits the block, never crashes."""
    try:
        out = []
        for e in activated:
            if e["kind"] != "groups":
                continue
            st = groupstate.read_state(croot, e["id"])
            if st and any(st[k] for k in groupstate.FIELDS):
                out.append({"name": e["name"], **st})
        return out
    except Exception:  # noqa: BLE001 — garbled state: omit, don't crash the context build
        return []
```

4. In `_assemble`, replace the `world_info_bodies` line with:

```python
    activated_wi = _world_info(cid, recent_text, exclude, frozenset(present))
```
and in the `data` dict:
```python
        "world_info_bodies": [e["body"] for e in activated_wi],
        "group_states": _group_states(cid, croot, activated_wi),
```

5. `_SECTIONS`: after the `("World info", ...)` entry add:

```python
    ("Group state", "scene/sections/group_state.j2", False),
```

Create `templates/scene/sections/group_state.j2`:

```jinja
{#- Campaign group state for groups activated into world-info: "Name:" then
    indented "Label: value" lines per non-empty section (multi-line values
    re-indented). Vars: group_states ([{name, goals, resources, focus,
    public_perception, secrets}]). -#}
{%- set lines = [] -%}
{%- for st in group_states -%}
{%- set parts = [] -%}
{%- for label, value in [("Goals", st.goals), ("Resources", st.resources), ("Focus", st.focus), ("Public perception", st.public_perception), ("Secrets", st.secrets)] -%}
{%- if value.strip() -%}{%- set _ = parts.append("  " ~ label ~ ": " ~ value.strip().replace("\n", "\n    ")) -%}{%- endif -%}
{%- endfor -%}
{%- if parts -%}
{%- set _ = lines.append(st.name ~ ":") -%}
{%- for p in parts -%}{%- set _ = lines.append(p) -%}{%- endfor -%}
{%- endif -%}
{%- endfor -%}
{%- if lines %}# Group state
{{ lines | join("\n") }}{% endif -%}
```

`templates/scene/system.j2` — after the `world_info.j2` include block (lines 63-64), add:

```jinja
{%- set s -%}{%- include "scene/sections/group_state.j2" -%}{%- endset -%}
{%- if s.strip() -%}{%- set _ = sections.append(s.strip()) -%}{%- endif -%}
```

and add `group_states` to the data-vars list in the header comment.

- [ ] **Step 4: Run the backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (the opener path reuses `_assemble`, so `build_opener_messages` gets group state for free).

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests templates
git commit -m "feat(context): inject activated groups' campaign state into scenes (#47)"
```

---

### Task 10: Backend — absorb write-back for group state (#47)

**Files:**
- Modify: `backend/src/grimoire/store/absorb.py` (`build_prompt`, `parse_output`, `materialize`, `apply_edits`, new `group_snapshot`)
- Modify: `templates/absorb/system.j2` (schema), `templates/absorb/user.j2` (Groups context line)
- Modify: `backend/src/grimoire/routes.py:1597-1600` (pass `group_snapshot` into `build_prompt`)
- Test: `backend/tests/test_absorb_store.py`

**Interfaces:**
- Consumes: `groupstate` (Task 8), overlay groups (Task 1).
- Produces:
  - `absorb.group_snapshot(cid: str) -> str` — one line per campaign group: `- groups/<gid> (<Name>): <state summary | (no state)>`.
  - `absorb.build_prompt(transcript, facts, state_snapshot=None, rel_snapshot=None, plot_snapshot=None, group_snapshot=None)`.
  - Parsed key `group_state_edits: [{"id", ...only-provided-fields}]` (keep-on-omit for all five fields).
  - StagedEdit kind `"group_state"` with `id "group_state:<gid>"`, `target {"kind": "groups", "id": gid}`; `apply_edits` writes it via `groupstate.write_state`.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_absorb_store.py` (match the file's existing fixture style for campaign/scene setup; the shape below is the contract):

```python
import json

from grimoire.store import absorb, campaigns, entities, groupstate, scenes, worlds


def _campaign_with_group(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("Saltmarch")
    cid = campaigns.create_campaign("Run", wid)
    sid = scenes.create_scene(cid, "S")
    croot = campaigns.campaign_root(cid)
    entities.create_entity(croot, "groups", "Salt Circle", "A quiet cabal.")
    return cid, sid, croot


def test_parse_output_group_state_keeps_key_presence():
    parsed = absorb.parse_output(json.dumps({
        "group_state_edits": [{"id": "groups/salt-circle", "goals": "New goal.", "secrets": ""}]}))
    row = parsed["group_state_edits"][0]
    assert row["id"] == "groups/salt-circle"
    assert row["goals"] == "New goal."
    assert row["secrets"] == ""          # explicit "" carried (clears)
    assert "resources" not in row        # omitted key absent (keep-on-omit)


def test_group_state_materialize_merges_and_applies(monkeypatch, tmp_path):
    cid, sid, croot = _campaign_with_group(monkeypatch, tmp_path)
    groupstate.write_state(croot, "salt-circle", "## Goals\nOld goal.\n\n## Secrets\nThe abbot.")
    parsed = absorb.parse_output(json.dumps({
        "group_state_edits": [{"id": "groups/salt-circle", "goals": "New goal."}]}))
    edits = absorb.materialize(cid, sid, parsed)
    gs = [e for e in edits if e["kind"] == "group_state"]
    assert len(gs) == 1
    assert gs[0]["id"] == "group_state:salt-circle"
    assert "New goal." in gs[0]["after"]
    assert "The abbot." in gs[0]["after"]          # omitted secrets preserved
    assert "Old goal." in gs[0]["before"]
    absorb.apply_edits(cid, gs)
    st = groupstate.read_state(croot, "salt-circle")
    assert st["goals"] == "New goal."
    assert st["secrets"] == "The abbot."


def test_group_state_edit_for_unknown_group_dropped(monkeypatch, tmp_path):
    cid, sid, croot = _campaign_with_group(monkeypatch, tmp_path)
    parsed = absorb.parse_output(json.dumps({
        "group_state_edits": [{"id": "groups/no-such", "goals": "x"}]}))
    assert [e for e in absorb.materialize(cid, sid, parsed) if e["kind"] == "group_state"] == []


def test_group_snapshot_lists_ids_and_state(monkeypatch, tmp_path):
    cid, sid, croot = _campaign_with_group(monkeypatch, tmp_path)
    groupstate.write_state(croot, "salt-circle", "## Goals\nExpand.")
    snap = absorb.group_snapshot(cid)
    assert "groups/salt-circle" in snap
    assert "Salt Circle" in snap
    assert "Goals: Expand." in snap
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_absorb_store.py -q`
Expected: FAIL — `parse_output` returns no `group_state_edits`, `group_snapshot` missing.

- [ ] **Step 3: Implement**

`backend/src/grimoire/store/absorb.py`:

1. Import `groupstate` alongside the other store imports.
2. `build_prompt` gains `group_snapshot: str | None = None` and passes it to the template render (`group_snapshot=group_snapshot`).
3. In `parse_output`, after the `cs_edits` block (line 75):

```python
    # Same key-presence rule as character_state_edits, for all five sections.
    gs_edits = []
    for e in obj.get("group_state_edits", []):
        if not isinstance(e, dict):
            continue
        row = {"id": _str(e, "id")}
        for k in groupstate.FIELDS:
            if k in e:
                row[k] = _str(e, k)
        gs_edits.append(row)
```

and add `"group_state_edits": gs_edits,` to the returned dict (after `"character_state_edits"`).

4. In `materialize`, after the `character_state_edits` loop:

```python
    for e in parsed.get("group_state_edits", []):
        raw_id = e.get("id", "")
        if not raw_id:
            continue
        kind, sep, rest = raw_id.partition("/")
        if not sep:
            kind, _, rest = raw_id.partition(":")
        gid = rest if kind == "groups" else raw_id
        try:
            name = overlay.read_entity(cid, "groups", gid)["meta"].get("name", gid)
        except entities.EntityNotFound:
            continue
        st = groupstate.read_state(croot, gid)
        cur = {k: (st[k] if st else "") for k in groupstate.FIELDS}
        new = {k: (e[k] if k in e else cur[k]) for k in groupstate.FIELDS}
        after = groupstate.compose_body(new)
        if not after:
            continue
        before = groupstate.compose_body(cur) if st else ""
        if before == after:
            continue
        out.append({"id": f"group_state:{gid}", "kind": "group_state",
                    "target": {"kind": "groups", "id": gid},
                    "label": f"{name} — group state", "field": "group_state",
                    "before": before, "after": after, "authored": False})
```

5. In `apply_edits`, after the `character_state` branch:

```python
            elif kind == "group_state":
                groupstate.write_state(croot, target["id"], after)
```

6. New function next to `state_snapshot`:

```python
def group_snapshot(cid: str) -> str:
    """Every campaign group with its current state — feeds the absorb prompt so
    the model uses real ids and rewrites from stored values, not from memory."""
    try:
        croot = campaigns.campaign_root(cid)
        lines = []
        for meta in overlay.list_entities(cid, "groups"):
            st = groupstate.read_state(croot, meta["id"])
            parts = [f"{groupstate.LABELS[k]}: {st[k]}" for k in groupstate.FIELDS
                     if st and st.get(k, "").strip()] if st else []
            state = " | ".join(parts) if parts else "(no state)"
            lines.append(f"- groups/{meta['id']} ({meta.get('name', meta['id'])}): {state}")
        return "\n".join(lines)
    except Exception:  # noqa: BLE001 — garbled store: omit, don't fail the extraction
        return ""
```

`templates/absorb/user.j2` — add `group_snapshot` to the header-comment vars and, after the `rel_snapshot` line (line 20):

```jinja
{%- if group_snapshot -%}{%- set _ = head.append("Groups:\n" ~ group_snapshot) -%}{%- endif -%}
```

`templates/absorb/system.j2` — after the `"character_state_edits"` clause, insert (single line, same style):

```
"group_state_edits" (list of {"id","goals","resources","focus","public_perception","secrets"} — for each group from the "Groups:" context line whose standing changed this scene, the FULL rewritten sections; include only the fields you are rewriting: an omitted field keeps its stored value, an explicit "" clears it; standing facts only, not a running log),
```

`backend/src/grimoire/routes.py:1597-1600` — extend the `build_prompt` call:

```python
    messages = store.absorb.build_prompt(
        transcript, facts,
        store.absorb.state_snapshot(cid, sid), store.absorb.relationships_snapshot(cid, sid),
        store.absorb.plot_snapshot(cid), store.absorb.group_snapshot(cid))
```

- [ ] **Step 4: Run the backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src backend/tests templates
git commit -m "feat(absorb): group_state_edits write-back through StagedEdit review (#47)"
```

---

### Task 11: Group state routes + campaign UI panel (#47)

**Files:**
- Modify: `backend/src/grimoire/routes.py` (GET/PUT `/campaigns/{cid}/groups/{gid}/state`, declared next to the campaign entity helpers)
- Modify: `frontend/src/api/client.ts` (`GroupState` type, `getGroupState`/`putGroupState`)
- Create: `frontend/src/components/GroupStatePanel.tsx`
- Modify: `frontend/src/components/EntityEditor.tsx` (mount the panel for campaign-scoped groups)
- Test: `backend/tests/test_routes.py`, `frontend/src/components/GroupStatePanel.test.tsx` (new)

**Interfaces:**
- Consumes: `groupstate` (Task 8); `overlay.read_entity` for 404s.
- Produces:
  - `GET /campaigns/{cid}/groups/{gid}/state` → `{goals, resources, focus, public_perception, secrets, updated}` (all-empty when no state file).
  - `PUT /campaigns/{cid}/groups/{gid}/state` body `{goals?, resources?, focus?, public_perception?, secrets?}` → `{ok: true}`; 404 for unknown campaign/group.
  - `client.ts`: `GroupState` type; `api.getGroupState(cid, gid)`, `api.putGroupState(cid, gid, state)`.
  - `GroupStatePanel({ cid, gid })` — sidebar panel, view + edit.

- [ ] **Step 1: Write failing tests**

Append to `backend/tests/test_routes.py` (uses that file's `client` fixture and `_campaign` helper):

```python
def test_group_state_routes_round_trip(client):
    _wid, cid = _campaign(client)
    gid = client.post(f"/api/campaigns/{cid}/groups",
                      json={"name": "Salt Circle", "body": "A quiet cabal."}).json()["id"]
    # no state file yet -> all fields empty
    r = client.get(f"/api/campaigns/{cid}/groups/{gid}/state")
    assert r.status_code == 200
    assert r.json()["goals"] == "" and r.json()["secrets"] == ""
    # write, then read back
    r = client.put(f"/api/campaigns/{cid}/groups/{gid}/state",
                   json={"goals": "Expand.", "secrets": "The abbot."})
    assert r.json() == {"ok": True}
    st = client.get(f"/api/campaigns/{cid}/groups/{gid}/state").json()
    assert st["goals"] == "Expand." and st["secrets"] == "The abbot." and st["updated"]
    # PUT is a full snapshot: an omitted field defaults to "" and clears
    client.put(f"/api/campaigns/{cid}/groups/{gid}/state", json={"goals": "Expand."})
    assert client.get(f"/api/campaigns/{cid}/groups/{gid}/state").json()["secrets"] == ""
    # unknown group -> 404 on both verbs
    assert client.get(f"/api/campaigns/{cid}/groups/no-such/state").status_code == 404
    assert client.put(f"/api/campaigns/{cid}/groups/no-such/state", json={}).status_code == 404
```

Create `frontend/src/components/GroupStatePanel.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { GroupStatePanel } from "./GroupStatePanel";

vi.mock("../api/client", () => ({
  api: { getGroupState: vi.fn(), putGroupState: vi.fn() },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.getGroupState as any).mockResolvedValue({
    goals: "Expand.", resources: "", focus: "", public_perception: "", secrets: "The abbot.", updated: "t" });
  (api.putGroupState as any).mockResolvedValue({ ok: true });
});

test("shows non-empty sections read-only; edit + save round-trips", async () => {
  render(<GroupStatePanel cid="c" gid="salt-circle" />);
  expect(await screen.findByText("Expand.")).toBeInTheDocument();
  expect(screen.getByText("The abbot.")).toBeInTheDocument();
  expect(screen.queryByLabelText("Goals")).toBeNull();            // read-only by default
  fireEvent.click(screen.getByRole("button", { name: /edit state/i }));
  fireEvent.change(screen.getByLabelText("Goals"), { target: { value: "Consolidate." } });
  fireEvent.click(screen.getByRole("button", { name: /save state/i }));
  await waitFor(() => expect(api.putGroupState).toHaveBeenCalledWith("c", "salt-circle", {
    goals: "Consolidate.", resources: "", focus: "", public_perception: "", secrets: "The abbot.",
  }));
});

test("empty state shows a hint instead of sections", async () => {
  (api.getGroupState as any).mockResolvedValue({
    goals: "", resources: "", focus: "", public_perception: "", secrets: "", updated: "" });
  render(<GroupStatePanel cid="c" gid="salt-circle" />);
  expect(await screen.findByText(/no campaign state yet/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q` — FAIL (404 route missing).
Run (from `frontend/`): `npx vitest run src/components/GroupStatePanel.test.tsx` — FAIL (module missing).

- [ ] **Step 3: Implement**

`backend/src/grimoire/routes.py` — model near the other BaseModels:

```python
class GroupStateSave(BaseModel):
    goals: str = ""
    resources: str = ""
    focus: str = ""
    public_perception: str = ""
    secrets: str = ""
```

Routes next to the campaign entity helpers (path shape `groups/{gid}/state` cannot collide with the generic `/{kind}/{eid}` or `/{kind}/{eid}/images/...` routes):

```python
@router.get("/campaigns/{cid}/groups/{gid}/state")
def get_group_state(cid: str, gid: str):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    try:
        store.overlay.read_entity(cid, "groups", gid)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="group not found")
    st = store.groupstate.read_state(store.campaigns.campaign_root(cid), gid)
    if st is None:
        return {**{k: "" for k in store.groupstate.FIELDS}, "updated": ""}
    return st


@router.put("/campaigns/{cid}/groups/{gid}/state")
def put_group_state(cid: str, gid: str, body: GroupStateSave):
    if not store.campaigns.campaign_meta_path(cid).exists():
        raise HTTPException(status_code=404, detail="campaign not found")
    try:
        store.overlay.read_entity(cid, "groups", gid)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="group not found")
    values = {"goals": body.goals, "resources": body.resources, "focus": body.focus,
              "public_perception": body.public_perception, "secrets": body.secrets}
    store.groupstate.write_state(store.campaigns.campaign_root(cid), gid,
                                 store.groupstate.compose_body(values))
    return {"ok": True}
```

`frontend/src/api/client.ts`:

```ts
// campaign group state (#47)
export type GroupState = {
  goals: string; resources: string; focus: string;
  public_perception: string; secrets: string; updated?: string;
};
```
```ts
  getGroupState: (cid: string, gid: string) =>
    request<GroupState>("GET", `/api/campaigns/${cid}/groups/${gid}/state`),
  putGroupState: (cid: string, gid: string, state: Omit<GroupState, "updated">) =>
    request<{ ok: boolean }>("PUT", `/api/campaigns/${cid}/groups/${gid}/state`, state),
```

Create `frontend/src/components/GroupStatePanel.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api, type GroupState } from "../api/client";
import { Field } from "./Field";

const SECTIONS = [
  { key: "goals", label: "Goals" },
  { key: "resources", label: "Resources" },
  { key: "focus", label: "Focus" },
  { key: "public_perception", label: "Public perception" },
  { key: "secrets", label: "Secrets" },
] as const;

const EMPTY: Omit<GroupState, "updated"> = {
  goals: "", resources: "", focus: "", public_perception: "", secrets: "",
};

export function GroupStatePanel({ cid, gid }: { cid: string; gid: string }) {
  const [state, setState] = useState<Omit<GroupState, "updated">>(EMPTY);
  const [draft, setDraft] = useState<Omit<GroupState, "updated">>(EMPTY);
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    api.getGroupState(cid, gid).then(({ updated: _u, ...s }) => { setState(s); setDraft(s); });
    setEditing(false);
  }, [cid, gid]);

  async function save() {
    await api.putGroupState(cid, gid, draft);
    setState(draft);
    setEditing(false);
  }

  const hasAny = SECTIONS.some((s) => state[s.key]);
  return (
    <div className="side-section">
      <h4>Campaign state</h4>
      {editing ? (
        <>
          {SECTIONS.map((s) => (
            <Field key={s.key} label={s.label}>
              <textarea rows={2} value={draft[s.key]}
                        onChange={(e) => setDraft({ ...draft, [s.key]: e.target.value })} />
            </Field>
          ))}
          <div className="form-actions">
            <button className="subtle" onClick={() => { setDraft(state); setEditing(false); }}>Cancel</button>
            <button className="primary" onClick={save}>Save state</button>
          </div>
        </>
      ) : (
        <>
          {hasAny ? SECTIONS.filter((s) => state[s.key]).map((s) => (
            <div key={s.key}>
              <div className="section-label">{s.label}</div>
              <div className="field-hint">{state[s.key]}</div>
            </div>
          )) : <div className="field-hint">No campaign state yet.</div>}
          <div className="form-actions">
            <button className="subtle" onClick={() => setEditing(true)}>Edit state</button>
          </div>
        </>
      )}
    </div>
  );
}
```

`frontend/src/components/EntityEditor.tsx` — import `GroupStatePanel` and mount it in the detail sidebar `<aside>` (next to the `OwnedLorePanel` mount):

```tsx
              {kind === "groups" && scope.kind === "campaign" && editing && (
                <GroupStatePanel cid={scope.id} gid={editing} />
              )}
```

- [ ] **Step 4: Run everything**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q` — Expected: PASS.
Run (from `frontend/`): `npx vitest run && npx tsc -b` — Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend frontend/src
git commit -m "feat(groups): campaign group-state routes and sidebar panel (#47)"
```

---

### Task 12: Final verification sweep

**Files:** none new — verification only.

- [ ] **Step 1: Full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all tests pass, zero failures.

- [ ] **Step 2: Full frontend suite + typecheck**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: all tests pass, no type errors.

- [ ] **Step 3: Grep hygiene checks**

- `grep -rn "factions\|monsters" backend/src frontend/src templates` — Expected: no hits (the kinds are `groups`/`creatures` everywhere).
- Confirm no real-world names slipped into fixtures: new tests use only Seraphine/Mara/Winifred/Saltmarch/Salt Circle/Marsh Wyrm/Salt Knife.

- [ ] **Step 4: Close the loop**

Use the superpowers:finishing-a-development-branch skill to decide merge/PR handling. The four issues #36, #37, #39, #47 are closable when this lands; deferred follow-ups already filed as #220–#223.
