# Campaign Creation Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the inline campaign-create form with a 4-step `/campaigns/new` wizard that creates a campaign-local PC, optional locations, seats the PC into a first scene, and offers an opening.

**Architecture:** A campaign is created from a world (copy-on-create of world locations/lore). The wizard adds *campaign-local overlays*: a PC written directly into the campaign dir and locations created at campaign scope. The PC is seated as the player via the existing cast/appearance machinery, extended to tolerate actors that have no world source. Steps 1–3 hold pure form state; "Create campaign" commits the whole sequence, then step 4 operates on the live campaign.

**Tech Stack:** FastAPI + pytest (backend); Vite/React + react-router + vitest/testing-library (frontend).

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)` (route tests use the `client` fixture which already does this).
- Run backend: `backend/.venv/Scripts/python.exe -m pytest backend -q`
- Run frontend: `npx --prefix frontend vitest run` and `tsc -b` in `frontend/`.
- Frontend record-list pages follow the list/detail pattern in `CLAUDE.md`; the wizard is a new full-page flow, not a list/detail page.
- PC/Persona shape: persona is `{name, pronouns, summary, description}`; PC carries `tags: string[]` and `default_version`.
- Campaign-local PC tags are **free strings** (no world-vocabulary validation), unlike world PCs.

---

### Task 1: `appearances.appear()` tolerates campaign-local actors

**Files:**
- Modify: `backend/src/grimoire/store/appearances.py:96-118` (the `appear` function)
- Test: `backend/tests/test_appearances_store.py`

**Interfaces:**
- Consumes: `pcs.create_pc(root, name, tags, version_name="default", persona=None) -> (pid, vid)`, `pcs.blank_persona(name) -> dict`, `campaigns.campaign_root(cid)`, `worlds.world_root(wid)`.
- Produces: `appearances.appear(cid, scene_id, kind, actor_id, version_id, role)` now records an appearance for an actor that exists in the campaign but not the world, with `base == ""` and no copy step. `sync.incoming(cid)` returns `[]` for such an actor.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_appearances_store.py`:

```python
def test_campaign_local_pc_appears_without_world_source(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    # PC exists only in the campaign (overlay), never in the world
    pcs.create_pc(croot, "Mara", ["rebel"], persona={"name": "Mara", "pronouns": "she/her",
                  "summary": "outlaw", "description": "On the run."})
    ap.appear(cid, "s1", "pcs", "mara", "default", "player")
    assert ap.players_in_scene(cid, "s1") == [{"kind": "pcs", "id": "mara", "version": "default"}]
    assert ap.record(cid)["pcs/mara"]["base"] == ""
    assert pcs.version_hash(worlds.world_root(wid), "mara", "default") is None  # nothing in the world


def test_appear_raises_when_actor_in_neither_world_nor_campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    with pytest.raises(ap.AppearError):
        ap.appear(cid, "s1", "pcs", "ghost", "default", "player")


def test_sync_ignores_campaign_local_pc(monkeypatch, tmp_path):
    from grimoire.store import sync
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    wid = worlds.create_world("W")
    cid = campaigns.create_campaign("Run", wid)
    croot = campaigns.campaign_root(cid)
    pcs.create_pc(croot, "Mara", [], persona=pcs.blank_persona("Mara"))
    ap.appear(cid, "s1", "pcs", "mara", "default", "player")
    assert sync.incoming(cid) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_appearances_store.py -q -k "campaign_local or neither or sync_ignores"`
Expected: FAIL — `test_campaign_local_pc_appears_without_world_source` raises `AppearError("world has no pcs/mara/default")`.

- [ ] **Step 3: Implement the fallback**

In `backend/src/grimoire/store/appearances.py`, replace the post-`rec is None` body of `appear` (currently):

```python
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    base = actor_hash(wroot, kind, actor_id, version_id)
    if base is None:
        raise AppearError(f"world has no {ref}/{version_id}")
    _copy_actor(wroot, croot, kind, actor_id, version_id)
    data[ref] = {"version": version_id, "base": base, "scenes": [scene_id], "role": role}
    _write(cid, data)
    campaigns.touch(cid)
```

with:

```python
    wroot = worlds.world_root(_world_id(cid))
    croot = campaigns.campaign_root(cid)
    base = actor_hash(wroot, kind, actor_id, version_id)
    if base is None:
        # Campaign-local overlay actor: no world source, so it must already exist
        # in the campaign. Record an empty world-base; sync skips refs the world
        # lacks, so a local actor never surfaces as an incoming change.
        if actor_hash(croot, kind, actor_id, version_id) is None:
            raise AppearError(f"no {ref}/{version_id} in world or campaign")
        base = ""
    else:
        _copy_actor(wroot, croot, kind, actor_id, version_id)
    data[ref] = {"version": version_id, "base": base, "scenes": [scene_id], "role": role}
    _write(cid, data)
    campaigns.touch(cid)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_appearances_store.py -q`
Expected: PASS (all, including the pre-existing tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/appearances.py backend/tests/test_appearances_store.py
git commit -m "feat: appear() tolerates campaign-local actors with no world source"
```

---

### Task 2: Campaign-local PC routes + cast version fallback

**Files:**
- Modify: `backend/src/grimoire/routes.py` — add two routes after `get_appearances` (line ~945) and before the `# ---- campaign greetings / play` section; adjust `post_scene_cast` (lines ~959-981)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.pcs.create_pc`, `store.pcs.list_pcs`, `store.pcs.read_pc`, `store.pcs.PCNotFound`, `_campaign_root_or_404`, the existing `PCCreate` model, `store.appearances.appear` (from Task 1).
- Produces:
  - `POST /api/campaigns/{cid}/pcs` body `{name, tags?, version_name?, persona?}` → `{"pc": pid, "version": vid}`
  - `GET /api/campaigns/{cid}/pcs` → `list[PCSummary]`
  - `POST /api/campaigns/{cid}/scenes/{sid}/cast` with `kind:"pcs"` and **no** `version` now resolves the default version from the campaign when the PC is not in the world.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routes.py`:

```python
def test_campaign_local_pc_create_seat_and_sync(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    # create a campaign-local PC (overlay; not written to the world)
    r = client.post(f"/api/campaigns/{cid}/pcs", json={
        "name": "Mara", "tags": ["rebel"],
        "persona": {"name": "Mara", "pronouns": "she/her", "summary": "outlaw", "description": "On the run."}})
    assert r.status_code == 200
    assert r.json()["pc"] == "mara"
    # lists at campaign scope, absent at world scope
    assert [p["id"] for p in client.get(f"/api/campaigns/{cid}/pcs").json()] == ["mara"]
    assert client.get(f"/api/worlds/{wid}/pcs").json() == []
    # seat as player with explicit version
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast",
                       json={"kind": "pcs", "id": "mara", "version": "default"}).status_code == 200
    assert {"kind": "pcs", "id": "mara", "role": "player"} in \
        client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json()
    # re-seat in a second scene with version omitted -> resolved from the campaign
    sid2 = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S2"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid2}/cast",
                       json={"kind": "pcs", "id": "mara"}).status_code == 200
    # no spurious incoming sync change for the local PC
    assert client.get(f"/api/campaigns/{cid}/incoming").json() == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_campaign_local_pc_create_seat_and_sync -q`
Expected: FAIL — `POST /api/campaigns/{cid}/pcs` returns 422/404 (route not defined; falls through to the generic `/{kind}` handler).

- [ ] **Step 3: Add the campaign PC routes**

In `backend/src/grimoire/routes.py`, immediately after the `get_appearances` function (ends ~line 945), add:

```python
@router.get("/campaigns/{cid}/pcs")
def get_campaign_pcs(cid: str):
    return store.pcs.list_pcs(_campaign_root_or_404(cid))


@router.post("/campaigns/{cid}/pcs")
def post_campaign_pc(cid: str, body: PCCreate):
    # Campaign-local PC overlay: tags are free strings (no world-vocabulary check).
    root = _campaign_root_or_404(cid)
    pid, vid = store.pcs.create_pc(root, body.name, body.tags, body.version_name, body.persona)
    return {"pc": pid, "version": vid}
```

- [ ] **Step 4: Add the cast version fallback**

In `post_scene_cast` (~line 959), replace the version-resolution block (currently):

```python
    version = body.version
    try:
        if version is None:
            if body.kind == "characters":
                version = store.characters.read_character(wroot, body.id)["meta"]["default_version"]
            else:
                version = store.pcs.read_pc(wroot, body.id)["meta"]["default_version"]
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
```

with:

```python
    version = body.version
    croot = store.campaigns.campaign_root(cid)
    try:
        if version is None:
            if body.kind == "characters":
                version = store.characters.read_character(wroot, body.id)["meta"]["default_version"]
            else:
                try:
                    version = store.pcs.read_pc(wroot, body.id)["meta"]["default_version"]
                except store.pcs.PCNotFound:
                    version = store.pcs.read_pc(croot, body.id)["meta"]["default_version"]
    except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
        raise HTTPException(status_code=404, detail="actor not found")
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_campaign_local_pc_create_seat_and_sync -q`
Expected: PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (no regressions).

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat: campaign-local PC routes and campaign-scoped cast version resolution"
```

---

### Task 3: API client methods + Campaigns list "New campaign" button

**Files:**
- Modify: `frontend/src/api/client.ts` (add two methods in the `// pcs` block)
- Modify: `frontend/src/routes/CampaignsView.tsx` (replace inline picker with a button)
- Test: `frontend/src/routes/CampaignsView.test.tsx`

**Interfaces:**
- Consumes: `Persona`, `PCSummary` types (already defined in `client.ts`).
- Produces:
  - `api.createCampaignPC(cid, { name, tags?, persona? }) -> Promise<{ pc: string; version: string }>`
  - `api.listCampaignPCs(cid) -> Promise<PCSummary[]>`
  - `CampaignsView` renders a `+ New campaign` button that navigates to `/campaigns/new`, disabled when no worlds exist.

- [ ] **Step 1: Add the API client methods**

In `frontend/src/api/client.ts`, inside the `// pcs` block (after `listPCs`, ~line 251), add:

```ts
  createCampaignPC: (cid: string, body: { name: string; tags?: string[]; persona?: Persona }) =>
    request<{ pc: string; version: string }>("POST", `/api/campaigns/${cid}/pcs`, body),
  listCampaignPCs: (cid: string) => request<PCSummary[]>("GET", `/api/campaigns/${cid}/pcs`),
```

- [ ] **Step 2: Update the CampaignsView test**

Replace the body of `frontend/src/routes/CampaignsView.test.tsx` with:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CampaignsView from "./CampaignsView";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<any>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("../api/client", () => ({
  api: {
    listCampaigns: vi.fn(),
    listWorlds: vi.fn(),
    renameCampaign: vi.fn(),
    deleteCampaign: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listCampaigns as any).mockResolvedValue([]);
  (api.listWorlds as any).mockResolvedValue([
    { id: "w1", name: "Realm", created: "", updated: "", counts: {} },
  ]);
  (api.renameCampaign as any).mockResolvedValue({ id: "c1", name: "New" });
  (api.deleteCampaign as any).mockResolvedValue({ ok: true });
});

function renderView() {
  render(
    <MemoryRouter>
      <CampaignsView />
    </MemoryRouter>,
  );
}

test("lists campaigns", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Run One", world: "w1", created: "", updated: "" },
  ]);
  renderView();
  await screen.findByText("Run One");
});

test("New campaign button navigates to the wizard", async () => {
  renderView();
  await waitFor(() => expect(screen.getByRole("button", { name: /new campaign/i })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: /new campaign/i }));
  expect(navigate).toHaveBeenCalledWith("/campaigns/new");
});

test("New campaign is disabled with guidance when there are no worlds", async () => {
  (api.listWorlds as any).mockResolvedValue([]);
  renderView();
  await screen.findByText(/create a world first/i);
  expect(screen.getByRole("button", { name: /new campaign/i })).toBeDisabled();
});

test("deletes a campaign after confirm", async () => {
  (api.listCampaigns as any).mockResolvedValue([
    { id: "c1", name: "Doomed", world: "w1", created: "", updated: "" },
  ]);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderView();
  await screen.findByText("Doomed");
  fireEvent.click(screen.getByRole("button", { name: /delete/i }));
  await waitFor(() => expect(api.deleteCampaign).toHaveBeenCalledWith("c1"));
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `npx --prefix frontend vitest run src/routes/CampaignsView.test.tsx`
Expected: FAIL — no `new campaign` button (the inline "Create campaign" form is still there).

- [ ] **Step 4: Rewrite CampaignsView**

Replace `frontend/src/routes/CampaignsView.tsx` with:

```tsx
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, type CampaignMeta, type WorldMeta } from "../api/client";
import { EditableRow } from "../components/EditableRow";

export default function CampaignsView() {
  const navigate = useNavigate();
  const [campaigns, setCampaigns] = useState<CampaignMeta[]>([]);
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);

  useEffect(() => {
    api.listCampaigns().then(setCampaigns);
    api.listWorlds().then(setWorlds);
  }, []);

  async function rename(id: string, next: string) {
    await api.renameCampaign(id, next);
    setCampaigns(await api.listCampaigns());
  }

  async function remove(c: CampaignMeta) {
    if (!window.confirm(`Delete '${c.name}'?`)) return;
    await api.deleteCampaign(c.id);
    setCampaigns(await api.listCampaigns());
  }

  return (
    <div className="view">
      <h2>Campaigns</h2>

      <div className="picker">
        <button className="primary" onClick={() => navigate("/campaigns/new")} disabled={worlds.length === 0}>
          + New campaign
        </button>
      </div>
      {worlds.length === 0 && (
        <p className="muted">
          Create a world first in <Link to="/worlds">Worlds</Link>, then start a campaign from it.
        </p>
      )}

      <div className="list">
        {campaigns.map((c) => (
          <EditableRow
            key={c.id}
            label={c.name}
            subtitle={c.world}
            onSelect={() => navigate(`/campaigns/${c.id}`)}
            onRename={(next) => rename(c.id, next)}
            onDelete={() => remove(c)}
          />
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `npx --prefix frontend vitest run src/routes/CampaignsView.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/routes/CampaignsView.tsx frontend/src/routes/CampaignsView.test.tsx
git commit -m "feat: campaign PC client methods; Campaigns list New campaign button"
```

---

### Task 4: CampaignWizard component + route

**Files:**
- Create: `frontend/src/routes/CampaignWizard.tsx`
- Create: `frontend/src/routes/CampaignWizard.test.tsx`
- Modify: `frontend/src/App.tsx` (add the route)

**Interfaces:**
- Consumes: `api.listWorlds`, `api.listTags`, `api.createCampaign`, `api.createCampaignPC` (Task 3), `api.createScene`, `api.addToCast`, `api.createEntity`, `api.availableGreetings`, `api.startFromGreeting`, `api.opener`; types `WorldMeta`, `Persona`, `Availability`.
- Produces: a route component `CampaignWizard` (default export) taking `{ keySet: boolean }`, mounted at `/campaigns/new`.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/routes/CampaignWizard.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import CampaignWizard from "./CampaignWizard";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<any>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

vi.mock("../api/client", () => ({
  api: {
    listWorlds: vi.fn(),
    listTags: vi.fn(),
    createCampaign: vi.fn(),
    createCampaignPC: vi.fn(),
    createScene: vi.fn(),
    addToCast: vi.fn(),
    createEntity: vi.fn(),
    availableGreetings: vi.fn(),
    startFromGreeting: vi.fn(),
    opener: vi.fn(),
  },
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.listWorlds as any).mockResolvedValue([{ id: "w1", name: "Realm", created: "", updated: "", counts: {} }]);
  (api.listTags as any).mockResolvedValue({ t1: "rebel", t2: "scholar" });
  (api.createCampaign as any).mockResolvedValue({ id: "run" });
  (api.createCampaignPC as any).mockResolvedValue({ pc: "mara", version: "default" });
  (api.createScene as any).mockResolvedValue({ id: "s1" });
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.createEntity as any).mockResolvedValue({ id: "tavern" });
  (api.availableGreetings as any).mockResolvedValue([]);
  (api.startFromGreeting as any).mockResolvedValue({ ok: true });
});

function renderWizard() {
  render(
    <MemoryRouter>
      <CampaignWizard keySet={false} />
    </MemoryRouter>,
  );
}

async function fillBackdropAndPC() {
  await screen.findByText("Realm");
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
  // step 2: PC
  fireEvent.change(screen.getByLabelText(/character name/i), { target: { value: "Mara" } });
  fireEvent.click(screen.getByRole("button", { name: /next/i }));
}

test("Next is gated until campaign name is entered", async () => {
  renderWizard();
  await screen.findByText("Realm");
  expect(screen.getByRole("button", { name: /next/i })).toBeDisabled();
  fireEvent.change(screen.getByLabelText(/campaign name/i), { target: { value: "Run One" } });
  expect(screen.getByRole("button", { name: /next/i })).toBeEnabled();
});

test("Create campaign commits the full sequence in order", async () => {
  renderWizard();
  await fillBackdropAndPC();
  // step 3: add a location, then create
  fireEvent.change(screen.getByLabelText(/location name/i), { target: { value: "The Tavern" } });
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));

  await waitFor(() => expect(api.createCampaign).toHaveBeenCalledWith("Run One", "w1"));
  expect(api.createCampaignPC).toHaveBeenCalledWith("run", expect.objectContaining({
    name: "Mara", tags: [], persona: expect.objectContaining({ name: "Mara" }),
  }));
  expect(api.createScene).toHaveBeenCalledWith("run");
  expect(api.addToCast).toHaveBeenCalledWith("run", "s1", { kind: "pcs", id: "mara", version: "default" });
  expect(api.createEntity).toHaveBeenCalledWith(
    { kind: "campaign", id: "run" }, "locations", expect.objectContaining({ name: "The Tavern" }));
  // advanced to the opener step
  await screen.findByText(/opening/i);
});

test("Finish on the opener step navigates to the campaign", async () => {
  renderWizard();
  await fillBackdropAndPC();
  fireEvent.click(screen.getByRole("button", { name: /create campaign/i }));
  await screen.findByText(/opening/i);
  fireEvent.click(screen.getByRole("button", { name: /finish/i }));
  expect(navigate).toHaveBeenCalledWith("/campaigns/run");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx --prefix frontend vitest run src/routes/CampaignWizard.test.tsx`
Expected: FAIL — module `./CampaignWizard` does not exist.

- [ ] **Step 3: Create the CampaignWizard component**

Create `frontend/src/routes/CampaignWizard.tsx`:

```tsx
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  api, type Availability, type Persona, type WorldMeta,
} from "../api/client";
import type { ChatEvent } from "../api/stream";

type LocationDraft = { name: string; body: string; keys: string };
const blankPersona: Persona = { name: "", pronouns: "", summary: "", description: "" };

export default function CampaignWizard({ keySet }: { keySet: boolean }) {
  const navigate = useNavigate();
  const [step, setStep] = useState(1);
  const [error, setError] = useState<string | null>(null);

  // step 1
  const [worlds, setWorlds] = useState<WorldMeta[]>([]);
  const [name, setName] = useState("");
  const [world, setWorld] = useState("");

  // step 2
  const [persona, setPersona] = useState<Persona>(blankPersona);
  const [tags, setTags] = useState<string[]>([]);
  const [tagInput, setTagInput] = useState("");
  const [worldTags, setWorldTags] = useState<string[]>([]);

  // step 3
  const [locations, setLocations] = useState<LocationDraft[]>([{ name: "", body: "", keys: "" }]);

  // step 4 (live campaign)
  const [committed, setCommitted] = useState<{ cid: string; sid: string } | null>(null);
  const [avail, setAvail] = useState<Availability[]>([]);
  const [prompt, setPrompt] = useState("");
  const [opener, setOpener] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listWorlds().then((ws) => {
      setWorlds(ws);
      if (ws.length) setWorld(ws[0].id);
    });
  }, []);

  useEffect(() => {
    if (!world) return;
    api.listTags(world).then((m) => setWorldTags(Object.values(m))).catch(() => setWorldTags([]));
  }, [world]);

  function addTag() {
    const t = tagInput.trim();
    if (t && !tags.includes(t)) setTags([...tags, t]);
    setTagInput("");
  }

  function setLoc(i: number, patch: Partial<LocationDraft>) {
    setLocations(locations.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  }

  async function commit() {
    setError(null);
    setBusy(true);
    try {
      const { id: cid } = await api.createCampaign(name.trim(), world);
      const { pc, version } = await api.createCampaignPC(cid, {
        name: persona.name.trim(), tags, persona: { ...persona, name: persona.name.trim() },
      });
      const { id: sid } = await api.createScene(cid);
      await api.addToCast(cid, sid, { kind: "pcs", id: pc, version });
      for (const loc of locations.filter((l) => l.name.trim())) {
        await api.createEntity({ kind: "campaign", id: cid }, "locations",
          { name: loc.name.trim(), body: loc.body, keys: loc.keys });
      }
      setCommitted({ cid, sid });
      api.availableGreetings(cid).then(setAvail).catch(() => setAvail([]));
      setStep(4);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function startGreeting(gid: string) {
    if (!committed) return;
    setError(null);
    try {
      await api.startFromGreeting(committed.cid, committed.sid, gid);
      navigate(`/campaigns/${committed.cid}`);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    }
  }

  async function generate() {
    if (!committed || !prompt.trim() || busy) return;
    setError(null);
    setOpener("");
    setBusy(true);
    let acc = "";
    try {
      await api.opener(committed.cid, committed.sid, prompt, (e: ChatEvent) => {
        if (e.delta) { acc += e.delta; setOpener(acc); }
        else if (e.error) setError(e.error.detail);
      });
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  const canNext1 = name.trim() !== "" && world !== "";
  const canNext2 = persona.name.trim() !== "";

  return (
    <div className="view wizard">
      <h2>New campaign</h2>
      <div className="wizard-steps">
        <span className={step === 1 ? "on" : ""}>Backdrop</span> ›{" "}
        <span className={step === 2 ? "on" : ""}>Character</span> ›{" "}
        <span className={step === 3 ? "on" : ""}>Locations</span> ›{" "}
        <span className={step === 4 ? "on" : ""}>Opening</span>
      </div>
      {error && <div className="banner error-banner">{error}</div>}

      {step === 1 && (
        <div className="wizard-body">
          <label className="field">
            <span>Campaign name</span>
            <input aria-label="Campaign name" value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label className="field">
            <span>World</span>
            <select aria-label="World" value={world} onChange={(e) => setWorld(e.target.value)}>
              {worlds.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
            </select>
          </label>
          <div className="form-actions">
            <button className="primary" disabled={!canNext1} onClick={() => setStep(2)}>Next</button>
          </div>
        </div>
      )}

      {step === 2 && (
        <div className="wizard-body">
          <label className="field">
            <span>Character name</span>
            <input aria-label="Character name" value={persona.name}
                   onChange={(e) => setPersona({ ...persona, name: e.target.value })} />
          </label>
          <label className="field">
            <span>Pronouns</span>
            <input aria-label="Pronouns" value={persona.pronouns}
                   onChange={(e) => setPersona({ ...persona, pronouns: e.target.value })} />
          </label>
          <label className="field">
            <span>Summary</span>
            <input aria-label="Summary" value={persona.summary}
                   onChange={(e) => setPersona({ ...persona, summary: e.target.value })} />
          </label>
          <label className="field">
            <span>Description</span>
            <textarea aria-label="Description" rows={5} value={persona.description}
                      onChange={(e) => setPersona({ ...persona, description: e.target.value })} />
          </label>
          <div className="field">
            <span>Tags</span>
            <div className="chips">
              {tags.map((t) => (
                <button key={t} className="chip on" onClick={() => setTags(tags.filter((x) => x !== t))}>
                  {t} ✕
                </button>
              ))}
            </div>
            <div className="picker">
              <input aria-label="Add tag" list="wizard-tags" value={tagInput}
                     onChange={(e) => setTagInput(e.target.value)}
                     onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addTag(); } }} />
              <datalist id="wizard-tags">
                {worldTags.map((t) => <option key={t} value={t} />)}
              </datalist>
              <button onClick={addTag} disabled={!tagInput.trim()}>Add tag</button>
            </div>
          </div>
          <div className="form-actions">
            <button className="subtle" onClick={() => setStep(1)}>Back</button>
            <button className="primary" disabled={!canNext2} onClick={() => setStep(3)}>Next</button>
          </div>
        </div>
      )}

      {step === 3 && (
        <div className="wizard-body">
          <p className="muted">Add any locations relevant to {persona.name.trim() || "your character"}. Optional.</p>
          {locations.map((loc, i) => (
            <div className="wizard-location" key={i}>
              <input aria-label="Location name" placeholder="Location name…" value={loc.name}
                     onChange={(e) => setLoc(i, { name: e.target.value })} />
              <textarea aria-label="Location description" rows={3} placeholder="Description…" value={loc.body}
                        onChange={(e) => setLoc(i, { body: e.target.value })} />
              <input aria-label="Location keys" placeholder="keys (comma-separated, optional)" value={loc.keys}
                     onChange={(e) => setLoc(i, { keys: e.target.value })} />
              {locations.length > 1 && (
                <button className="subtle" onClick={() => setLocations(locations.filter((_, j) => j !== i))}>
                  Remove
                </button>
              )}
            </div>
          ))}
          <button className="subtle" onClick={() => setLocations([...locations, { name: "", body: "", keys: "" }])}>
            + Add another location
          </button>
          <div className="form-actions">
            <button className="subtle" onClick={() => setStep(2)} disabled={busy}>Back</button>
            <button className="primary" onClick={commit} disabled={busy}>
              {busy ? "Creating…" : "Create campaign"}
            </button>
          </div>
        </div>
      )}

      {step === 4 && committed && (
        <div className="wizard-body">
          <h3>Opening</h3>
          <div className="role">Start from a greeting</div>
          {avail.length === 0 && <div className="field-hint">No greetings available in this world.</div>}
          <div className="chips">
            {avail.map((g) => (
              <button key={g.id} className="chip" disabled={!g.available}
                      title={g.available ? "" : g.reasons.join("; ")} onClick={() => startGreeting(g.id)}>
                {g.name}
              </button>
            ))}
          </div>
          <div className="role">Generate an opener</div>
          {!keySet && <div className="field-hint">Set an OpenRouter key in Config to generate.</div>}
          <div className="picker">
            <input aria-label="Opener prompt" placeholder="A storm over the salt marshes…"
                   value={prompt} onChange={(e) => setPrompt(e.target.value)} />
            <button className="primary" disabled={!keySet || busy || !prompt.trim()} onClick={generate}>
              {busy ? "…" : "Generate"}
            </button>
          </div>
          {opener && <div className="opener-preview">{opener}</div>}
          <div className="form-actions">
            <button className="primary" onClick={() => navigate(`/campaigns/${committed.cid}`)}>Finish</button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Wire the route in App.tsx**

In `frontend/src/App.tsx`, add the import after the other route imports:

```tsx
import CampaignWizard from "./routes/CampaignWizard";
```

and add the route inside `<Routes>` (before or after `/campaigns/:cid`; react-router ranks the static segment first):

```tsx
        <Route path="/campaigns/new" element={<CampaignWizard keySet={keySet} />} />
```

- [ ] **Step 5: Run the wizard test to verify it passes**

Run: `npx --prefix frontend vitest run src/routes/CampaignWizard.test.tsx`
Expected: PASS.

- [ ] **Step 6: Typecheck**

Run: `cd frontend && npx tsc -b`
Expected: no errors.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/routes/CampaignWizard.tsx frontend/src/routes/CampaignWizard.test.tsx frontend/src/App.tsx
git commit -m "feat: campaign creation wizard route"
```

---

### Task 5: CastPanel merges campaign-local PCs

**Files:**
- Modify: `frontend/src/components/CastPanel.tsx:38-44` (the lazy world-actor load)
- Test: `frontend/src/components/CastPanel.test.tsx`

**Interfaces:**
- Consumes: `api.listCampaignPCs(cid)` (Task 3), `api.listPCs(world)`.
- Produces: the PC dropdown in CastPanel lists world PCs **and** campaign-local PCs (deduped by id).

- [ ] **Step 1: Write the failing test**

Add to `frontend/src/components/CastPanel.test.tsx` (match the file's existing mock setup; ensure `listCampaignPCs` is in the `api` mock object — add it if missing):

```tsx
test("PC dropdown includes campaign-local PCs", async () => {
  (api.listPCs as any).mockResolvedValue([{ id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  (api.listCampaignPCs as any).mockResolvedValue([{ id: "mara", name: "Mara", tags: [], default_version: "default", versions: [] }]);
  renderPanel(); // existing helper in this test file
  // switch the kind selector to PCs
  fireEvent.change(await screen.findByLabelText(/actor kind/i), { target: { value: "pcs" } });
  await screen.findByRole("option", { name: "Elara" });
  await screen.findByRole("option", { name: "Mara" });
});
```

If `CastPanel.test.tsx` has no `renderPanel` helper or `listCampaignPCs` mock, mirror the existing render/mocks in that file (add `listCampaignPCs: vi.fn()` to the `api` mock and default it to `mockResolvedValue([])` in `beforeEach`).

- [ ] **Step 2: Run the test to verify it fails**

Run: `npx --prefix frontend vitest run src/components/CastPanel.test.tsx`
Expected: FAIL — only "Elara" is listed; "Mara" is missing.

- [ ] **Step 3: Merge campaign-local PCs in CastPanel**

In `frontend/src/components/CastPanel.tsx`, replace the lazy-load effect (lines ~38-44):

```tsx
  // the world's characters/pcs are needed to add actors; load lazily from the campaign's world
  useEffect(() => {
    api.getCampaign(cid).then((c) => {
      api.listCharacters(c.meta.world).then(setChars);
      api.listPCs(c.meta.world).then(setPCs);
    });
  }, [cid]);
```

with:

```tsx
  // characters/pcs available to add: world assets plus the campaign's own PC overlays
  useEffect(() => {
    api.getCampaign(cid).then((c) => {
      api.listCharacters(c.meta.world).then(setChars);
      Promise.all([api.listPCs(c.meta.world), api.listCampaignPCs(cid)]).then(([worldPCs, localPCs]) => {
        const byId = new Map(worldPCs.map((p) => [p.id, p]));
        for (const p of localPCs) byId.set(p.id, p);
        setPCs([...byId.values()]);
      });
    });
  }, [cid]);
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npx --prefix frontend vitest run src/components/CastPanel.test.tsx`
Expected: PASS.

- [ ] **Step 5: Full frontend suite + typecheck**

Run: `npx --prefix frontend vitest run` then `cd frontend && npx tsc -b`
Expected: PASS, no type errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/CastPanel.tsx frontend/src/components/CastPanel.test.tsx
git commit -m "feat: CastPanel lists campaign-local PCs alongside world PCs"
```

---

## Final verification

- [ ] Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q` — all pass.
- [ ] Frontend: `npx --prefix frontend vitest run` — all pass.
- [ ] Types: `cd frontend && npx tsc -b` — clean.
- [ ] Manual smoke (optional): start the app, click **+ New campaign**, walk all four steps, confirm the campaign opens with the PC seated in the first scene.

## Self-review notes (coverage map)

- Spec "campaign-local PC" → Tasks 1, 2 (store + routes). 
- Spec "locations campaign-scoped, no backend change" → Task 4 commit step uses existing `createEntity` campaign scope.
- Spec "first scene with PC seated" → Task 4 `commit()` (createScene + addToCast).
- Spec "opener step (greeting / generate / skip)" → Task 4 step 4.
- Spec "appear() local fallback; sync ignores local PC" → Task 1.
- Spec "post_scene_cast version resolution" → Task 2.
- Spec "CastPanel merges local PCs" → Task 5.
- Spec "+ New campaign button, no-worlds guard" → Task 3.
- Spec "no rollback on commit failure" → Task 4 `commit()` surfaces error, stays on step 3.
