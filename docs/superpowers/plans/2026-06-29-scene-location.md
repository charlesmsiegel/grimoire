# Scene Location Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give each scene a current location (setting) that is reliably injected into the context prompt, changeable mid-scene with a transcript transition line.

**Architecture:** A scene references campaign `locations` entities by id via an ordered `location_history` frontmatter scalar (last = current). Changing the setting appends an assistant transition message and records the move. `context.build_messages` injects the current location as an always-on `# Current setting` block and excludes it from the keyword-activated world-info pool to avoid duplication.

**Tech Stack:** FastAPI + pytest (backend); Vite/React + vitest/testing-library (frontend).

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`; route tests use the `client` fixture which already does this.
- Run backend: `backend/.venv/Scripts/python.exe -m pytest backend -q`
- Run frontend tests: `npx --prefix frontend vitest run --root frontend` (the `--root frontend` is required so vitest finds `frontend/vite.config.ts` with `globals: true`).
- Typecheck: from `frontend/`, `npx tsc -b`.
- Transcript roles are only `user` / `assistant`; the transition line is an assistant message `*The scene moves to {name}.*`.
- `location_history` is a comma-joined ordered id list in scene frontmatter; the last id is current.

---

### Task 1: Scene location history + set_location (store)

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py` (add `entities` import; add `get_location_history`, `set_location`)
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Consumes: `entities.read_entity(root, kind, eid)` (raises `entities.EntityNotFound`), `entities.create_entity`, `campaigns.campaign_root`, existing `append_message`, `parse_frontmatter`, `dump_frontmatter`.
- Produces:
  - `scenes.get_location_history(cid, sid) -> list[str]` (missing scene ⇒ `[]`)
  - `scenes.set_location(cid, sid, eid) -> {"moved": bool, "name": str}` — raises `SceneNotFound` (bad scene) or `entities.EntityNotFound` (bad location).

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_scene_store.py`:

```python
def test_set_location_first_is_silent_then_move_announces(monkeypatch, tmp_path):
    from grimoire.store import entities
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    a = entities.create_entity(croot, "locations", "Salt Cathedral", "A drowned basilica.")
    b = entities.create_entity(croot, "locations", "Drowned Market", "Stalls in the shallows.")
    sid = scenes.create_scene(cid, "S")
    # first set: silent
    assert scenes.set_location(cid, sid, a) == {"moved": False, "name": "Salt Cathedral"}
    assert scenes.get_location_history(cid, sid) == [a]
    assert scenes.read_scene(cid, sid)["messages"] == []
    # change: announces and records
    assert scenes.set_location(cid, sid, b) == {"moved": True, "name": "Drowned Market"}
    assert scenes.get_location_history(cid, sid) == [a, b]
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "assistant", "content": "*The scene moves to Drowned Market.*"}]
    # re-select current: no-op
    assert scenes.set_location(cid, sid, b) == {"moved": False, "name": "Drowned Market"}
    assert scenes.get_location_history(cid, sid) == [a, b]
    assert len(scenes.read_scene(cid, sid)["messages"]) == 1


def test_set_location_unknown_id_raises(monkeypatch, tmp_path):
    from grimoire.store import entities
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    with pytest.raises(entities.EntityNotFound):
        scenes.set_location(cid, sid, "nowhere")


def test_get_location_history_missing_scene_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scenes.get_location_history(cid, "nope") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q -k "location"`
Expected: FAIL — `module 'grimoire.store.scenes' has no attribute 'set_location'`.

- [ ] **Step 3: Implement the store functions**

In `backend/src/grimoire/store/scenes.py`, change the import line:

```python
from . import campaigns
```

to:

```python
from . import campaigns, entities
```

Then add at the end of the file:

```python
def get_location_history(cid: str, sid: str) -> list[str]:
    """Ordered campaign-location ids this scene has been at; last is current. Missing ⇒ []."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return []
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return [x for x in meta.get("location_history", "").split(",") if x]


def set_location(cid: str, sid: str, eid: str) -> dict:
    """Make campaign location `eid` the scene's current setting.

    First setting on a location-less scene is silent; a real change appends an
    assistant transition line. Re-selecting the current location is a no-op.
    Returns {"moved": bool, "name": str}.
    """
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    croot = campaigns.campaign_root(cid)
    name = entities.read_entity(croot, "locations", eid)["meta"].get("name", eid)  # raises EntityNotFound
    history = get_location_history(cid, sid)
    if history and history[-1] == eid:
        return {"moved": False, "name": name}
    moved = bool(history)
    if moved:
        append_message(cid, sid, "assistant", f"*The scene moves to {name}.*")
    # re-read after the possible append_message rewrite, then record the new current
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    history.append(eid)
    meta["location_history"] = ",".join(history)
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
    return {"moved": moved, "name": name}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q`
Expected: PASS (all, including pre-existing).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/tests/test_scene_store.py
git commit -m "feat: scene location history with set_location transition"
```

---

### Task 2: Inject the current setting into context

**Files:**
- Modify: `backend/src/grimoire/store/context.py` (`_world_info` gains `exclude`; `build_messages` adds the setting block)
- Test: `backend/tests/test_context.py`

**Interfaces:**
- Consumes: `scenes.get_location_history` (Task 1), `entities.read_entity`, `entities.EntityNotFound`.
- Produces: `build_messages` output containing a `# Current setting\n{body}` block when a setting is present, with that location excluded from the keyed world-info pool.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_context.py`:

```python
def test_current_setting_injected_once(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    loc = entities.create_entity(croot, "locations", "Salt Cathedral", "A drowned basilica of black salt.")
    scenes.set_location(cid, sid, loc)
    scenes.append_message(cid, sid, "user", "look around")
    sys = context.build_messages(cid, sid)[0]["content"]
    assert "# Current setting" in sys
    # keyless location would otherwise be always-on in world-info too; exclude prevents a double-inject
    assert sys.count("A drowned basilica of black salt.") == 1


def test_no_setting_block_when_unset(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    scenes.append_message(cid, sid, "user", "hi")
    msgs = context.build_messages(cid, sid)
    sys = msgs[0]["content"] if msgs and msgs[0]["role"] == "system" else ""
    assert "# Current setting" not in sys
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q -k "setting"`
Expected: FAIL — `test_current_setting_injected_once` (no `# Current setting` in output).

- [ ] **Step 3: Add `exclude` to `_world_info`**

In `backend/src/grimoire/store/context.py`, replace `_world_info`:

```python
def _world_info(croot, recent_text: str) -> str:
    entries = []
    for kind in ("lore", "locations"):
        for meta in entities.list_entities(croot, kind):
            e = entities.read_entity(croot, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            entries.append({"name": e["meta"].get("name", meta["id"]), "body": e["body"].strip(), "keys": keys})
    selected = activate(entries, recent_text)
    return "\n\n".join(e["body"] for e in selected if e["body"])
```

with:

```python
def _world_info(croot, recent_text: str, exclude: frozenset = frozenset()) -> str:
    entries = []
    for kind in ("lore", "locations"):
        for meta in entities.list_entities(croot, kind):
            if kind == "locations" and meta["id"] in exclude:
                continue
            e = entities.read_entity(croot, kind, meta["id"])
            keys = [k.strip() for k in e["meta"].get("keys", "").split(",") if k.strip()]
            entries.append({"name": e["meta"].get("name", meta["id"]), "body": e["body"].strip(), "keys": keys})
    selected = activate(entries, recent_text)
    return "\n\n".join(e["body"] for e in selected if e["body"])
```

- [ ] **Step 4: Add the setting block in `build_messages`**

In `backend/src/grimoire/store/context.py`, find this block in `build_messages`:

```python
    wi = _world_info(croot, recent_text)
    if wi:
        parts.append(wi)
```

and replace it with:

```python
    history = scenes.get_location_history(cid, sid)
    current_loc = history[-1] if history else None
    exclude: frozenset = frozenset()
    if current_loc:
        try:
            loc_body = entities.read_entity(croot, "locations", current_loc)["body"].strip()
            exclude = frozenset({current_loc})
            if loc_body:
                parts.append("# Current setting\n" + loc_body)
        except entities.EntityNotFound:
            pass  # referenced location was deleted — omit the setting block
    wi = _world_info(croot, recent_text, exclude)
    if wi:
        parts.append(wi)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_context.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/context.py backend/tests/test_context.py
git commit -m "feat: inject current scene setting into context, excluded from keyed world-info"
```

---

### Task 3: Scene location routes

**Files:**
- Modify: `backend/src/grimoire/routes.py` (add `SceneLocation` model; add GET/PUT location routes)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.scenes.get_location_history`, `store.scenes.set_location` (Task 1), `store.entities.read_entity`, `store.entities.EntityNotFound`, `_require_scene`, `_campaign` test helper.
- Produces:
  - `PUT /api/campaigns/{cid}/scenes/{sid}/location` body `{location: eid}` → `{"ok": True, "moved": bool, "name": str}`; 404 on bad scene/location.
  - `GET /api/campaigns/{cid}/scenes/{sid}/location` → `{"current": {"id","name"}|null, "visited": [{"id","name"}...]}`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_routes.py`:

```python
def test_scene_location_set_get_and_move(client):
    wid, cid = _campaign(client)
    a = client.post(f"/api/campaigns/{cid}/locations",
                    json={"name": "Salt Cathedral", "body": "A drowned basilica."}).json()["id"]
    b = client.post(f"/api/campaigns/{cid}/locations",
                    json={"name": "Drowned Market", "body": "Shallow stalls."}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # first set: silent
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/location", json={"location": a}).json() == \
        {"ok": True, "moved": False, "name": "Salt Cathedral"}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == []
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/location").json() == \
        {"current": {"id": a, "name": "Salt Cathedral"}, "visited": []}
    # move: announces
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/location", json={"location": b}).json() == \
        {"ok": True, "moved": True, "name": "Drowned Market"}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"] == [
        {"role": "assistant", "content": "*The scene moves to Drowned Market.*"}]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/location").json() == \
        {"current": {"id": b, "name": "Drowned Market"}, "visited": [{"id": a, "name": "Salt Cathedral"}]}


def test_scene_location_unknown_404(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.put(f"/api/campaigns/{cid}/scenes/{sid}/location",
                      json={"location": "nope"}).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "scene_location"`
Expected: FAIL — PUT returns 422/405 (route not defined).

- [ ] **Step 3: Add the request model**

In `backend/src/grimoire/routes.py`, after the `class Dismiss` model (search for `class Appear`; add near the other scene models, e.g. right after `class Appear(BaseModel):` block ends). Add:

```python
class SceneLocation(BaseModel):
    location: str
```

- [ ] **Step 4: Add the routes**

In `backend/src/grimoire/routes.py`, immediately after the `post_dismiss` function (the `POST .../suggestions/dismiss` route), add:

```python
@router.get("/campaigns/{cid}/scenes/{sid}/location")
def get_scene_location(cid: str, sid: str):
    _require_scene(cid, sid)
    croot = store.campaigns.campaign_root(cid)
    history = store.scenes.get_location_history(cid, sid)

    def ref(eid: str) -> dict:
        try:
            name = store.entities.read_entity(croot, "locations", eid)["meta"].get("name", eid)
        except store.entities.EntityNotFound:
            name = eid
        return {"id": eid, "name": name}

    return {"current": ref(history[-1]) if history else None,
            "visited": [ref(e) for e in history[:-1]]}


@router.put("/campaigns/{cid}/scenes/{sid}/location")
def put_scene_location(cid: str, sid: str, body: SceneLocation):
    _require_scene(cid, sid)
    try:
        result = store.scenes.set_location(cid, sid, body.location)
    except store.entities.EntityNotFound:
        raise HTTPException(status_code=404, detail="location not found")
    return {"ok": True, **result}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "scene_location"`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: scene location GET/PUT routes"
```

---

### Task 4: Frontend — Setting section in CastPanel

**Files:**
- Modify: `frontend/src/api/client.ts` (types + two methods)
- Modify: `frontend/src/components/CastPanel.tsx` (Setting section)
- Test: `frontend/src/components/CastPanel.test.tsx`

**Interfaces:**
- Consumes: `api.listEntities` (existing), `api.getSceneLocation`, `api.setSceneLocation` (new); types `EntitySummary`.
- Produces: `api.getSceneLocation(cid, sid) -> SceneLocation`, `api.setSceneLocation(cid, sid, location) -> {ok, moved, name}`; a Setting section that shows the current setting, lists campaign locations, and sets/moves the scene location, refreshing the stream via `onSeeded`.

- [ ] **Step 1: Add the API client types and methods**

In `frontend/src/api/client.ts`, add near the `// cast` types block (after `RosterEntry`):

```ts
export type SceneLocationRef = { id: string; name: string };
export type SceneLocation = { current: SceneLocationRef | null; visited: SceneLocationRef[] };
```

and in the `// campaign cast & play` methods block (after `startFromGreeting`), add:

```ts
  getSceneLocation: (cid: string, sid: string) =>
    request<SceneLocation>("GET", `/api/campaigns/${cid}/scenes/${sid}/location`),
  setSceneLocation: (cid: string, sid: string, location: string) =>
    request<{ ok: boolean; moved: boolean; name: string }>(
      "PUT", `/api/campaigns/${cid}/scenes/${sid}/location`, { location }),
```

- [ ] **Step 2: Write the failing tests**

In `frontend/src/components/CastPanel.test.tsx`, add the new methods to the `api` mock object (in the `vi.mock("../api/client", ...)` block) so it includes:

```ts
    listEntities: vi.fn(), getSceneLocation: vi.fn(), setSceneLocation: vi.fn(),
```

Add these defaults inside `beforeEach` (after the existing `mockResolvedValue` lines):

```ts
  (api.listEntities as any).mockResolvedValue([]);
  (api.getSceneLocation as any).mockResolvedValue({ current: null, visited: [] });
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: false, name: "" });
```

Then add these tests at the end of the file:

```tsx
test("shows the current setting and lists campaign locations", async () => {
  (api.getSceneLocation as any).mockResolvedValue({ current: { id: "crypt", name: "The Crypt" }, visited: [] });
  (api.listEntities as any).mockResolvedValue([{ id: "crypt", name: "The Crypt" }, { id: "market", name: "Market" }]);
  renderPanel();
  await screen.findByText("The Crypt");
  await screen.findByRole("option", { name: "Market" });
});

test("changing the setting calls setSceneLocation and refreshes the stream", async () => {
  const onSeeded = vi.fn();
  (api.listEntities as any).mockResolvedValue([{ id: "market", name: "Market" }]);
  renderPanel({ onSeeded });
  fireEvent.change(await screen.findByLabelText("Location"), { target: { value: "market" } });
  fireEvent.click(screen.getByRole("button", { name: /set location/i }));
  await waitFor(() => expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s", "market"));
  await waitFor(() => expect(onSeeded).toHaveBeenCalled());
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `npx --prefix frontend vitest run --root frontend src/components/CastPanel.test.tsx`
Expected: FAIL — no "The Crypt" / no `Location` select / `set location` button.

- [ ] **Step 4: Add the Setting section to CastPanel**

In `frontend/src/components/CastPanel.tsx`, update the import from `../api/client` to also pull `EntitySummary` and `SceneLocation`:

```tsx
import {
  api, type Actor, type Availability, type CharacterSummary, type EntitySummary,
  type PCSummary, type RosterEntry, type SceneLocation,
} from "../api/client";
```

Add state near the other `useState` hooks:

```tsx
  const [locations, setLocations] = useState<EntitySummary[]>([]);
  const [setting, setSetting] = useState<SceneLocation | null>(null);
  const [locId, setLocId] = useState("");
```

Add a reload callback next to `reloadCast`:

```tsx
  const reloadSetting = useCallback(
    () => api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null)),
    [cid, sid]);
```

Add `reloadSetting()` to the scene-scoped effect and `reloadSetting` to its deps:

```tsx
  useEffect(() => {
    reloadCast();
    api.availableGreetings(cid).then(setAvail).catch(() => setAvail([]));
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    reloadSetting();
  }, [cid, sid, reloadCast, reloadSetting]);
```

Load campaign locations in the campaign-scoped effect (add the line after the `getCampaign` call):

```tsx
    api.listEntities({ kind: "campaign", id: cid }, "locations").then(setLocations).catch(() => setLocations([]));
```

Add the handler near `add`:

```tsx
  async function setLocation() {
    if (!locId) return;
    setError(null);
    try {
      await api.setSceneLocation(cid, sid, locId);
      setLocId("");
      await reloadSetting();
      onSeeded(); // refresh the stream so the transition line shows
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }
```

Add the Setting section as the first child inside `<div className="panel-body">` (right after the `{error && ...}` line):

```tsx
        <div>
          <div className="role">Setting</div>
          <div className="field-hint">{setting?.current ? setting.current.name : "No setting"}</div>
          <div className="picker">
            <select aria-label="Location" value={locId} onChange={(e) => setLocId(e.target.value)}>
              <option value="">— pick —</option>
              {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            <button className="primary" onClick={setLocation}
                    disabled={!locId || locId === setting?.current?.id}>
              {setting?.current ? "Move here" : "Set location"}
            </button>
          </div>
        </div>
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `npx --prefix frontend vitest run --root frontend src/components/CastPanel.test.tsx`
Expected: PASS.

- [ ] **Step 6: Full frontend suite + typecheck**

Run: `npx --prefix frontend vitest run --root frontend`
Then from `frontend/`: `npx tsc -b`
Expected: all tests pass; tsc exits 0.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/CastPanel.tsx frontend/src/components/CastPanel.test.tsx
git commit -m "feat: scene Setting picker in CastPanel"
```

---

## Final verification

- [ ] Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` — all pass.
- [ ] Frontend: `npx --prefix frontend vitest run --root frontend` — all pass.
- [ ] Types: from `frontend/`, `npx tsc -b` — clean.

## Self-review notes (coverage map)

- Spec "scene references a location by id, ordered history" → Task 1 (`location_history`, `get_location_history`).
- Spec "first set silent; change appends transition; re-select no-op" → Task 1 `set_location` + tests.
- Spec "transition is an assistant message `*The scene moves to {name}.*`" → Task 1.
- Spec "current setting always-on `# Current setting` block; excluded from keyed pool" → Task 2.
- Spec "missing/deleted location tolerated" → Task 2 (`except EntityNotFound: pass`) and Task 3 `ref()` name fallback.
- Spec "PUT/GET location routes; 404 on bad ids" → Task 3.
- Spec "CastPanel Setting section; onSeeded refresh; dropdown from campaign locations" → Task 4.
- Spec "visited not injected as a block" → Task 2 (only current is appended; visited ids are not added to `parts`).
