# New Scene Chooser Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clicking **+ New Scene** opens a chooser modal offering up to 4 scene cards (available greetings — plot-map-unlocked first — plus LLM-generated premise cards) and a Create-manually option; nothing is created until a pick.

**Architecture:** Two data paths compose the chooser client-side: the (extended) fast availability route renders greeting cards instantly, and the existing `POST /scene-suggestions` fills generated cards async. `start_from_greeting` now stamps the originating greeting id into the scene's frontmatter so availability can flag what a given scene unlocks (`?after=<sid>`). The chooser supersedes CastPanel's greeting chips and "Suggest scenes" block.

**Tech Stack:** FastAPI + pytest (backend), React + vitest (frontend).

**Spec:** `docs/superpowers/specs/2026-07-04-new-scene-chooser-design.md`

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Backend test run: `backend/.venv/Scripts/python.exe -m pytest backend -q` (from repo root).
- Frontend runs **from `frontend/`**: `npx vitest run` and `npx tsc -b`. Never `npx --prefix frontend vitest run` — it skips `frontend/vitest.config.ts` and breaks `globals`.
- Scene frontmatter is string-scalar only; nested data goes in JSON sidecars. The greeting stamp is a plain string, so frontmatter is correct.
- Existing endpoints keep their no-param behavior: `GET /greetings/available` without `after` returns today's order (plus an ignorable `unlocked: false` on each item); `POST /scene-suggestions` is untouched.

---

### Task 1: Stamp the originating greeting on the scene

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py` (add `stamp_greeting` after `add_dismissed`, ~line 201)
- Modify: `backend/src/grimoire/store/playing.py:55-72` (`start_from_greeting`)
- Test: `backend/tests/test_playing_store.py`

**Interfaces:**
- Produces: `scenes.stamp_greeting(cid: str, sid: str, gid: str) -> None` (raises `SceneNotFound`); scenes started from a greeting carry `meta["greeting"] == gid`. Task 2 reads that key.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_playing_store.py`:

```python
def test_start_from_greeting_stamps_greeting(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    g = greetings.create_greeting(wroot, "Open", "seraphine", "default", body="Hi.")
    playing.start_from_greeting(cid, sid, g)
    assert scenes.read_scene(cid, sid)["meta"]["greeting"] == g


def test_stamp_greeting_missing_scene_raises(monkeypatch, tmp_path):
    _wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        scenes.stamp_greeting(cid, "nope", "g1")
```

- [ ] **Step 2: Run tests to verify they fail**

Run (repo root): `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py -q`
Expected: 2 FAIL — `KeyError: 'greeting'` and `AttributeError: module ... has no attribute 'stamp_greeting'`.

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/scenes.py`, after `add_dismissed` (~line 201):

```python
def stamp_greeting(cid: str, sid: str, gid: str) -> None:
    """Record the greeting this scene was started from (plot-map unlock linkage)."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        raise SceneNotFound(sid)
    meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))
    meta["greeting"] = gid
    p.write_text(dump_frontmatter(meta, body), encoding="utf-8")
```

In `backend/src/grimoire/store/playing.py`, inside `start_from_greeting`, add one line directly after `_mark_played(cid, gid)`:

```python
    _mark_played(cid, gid)
    scenes.stamp_greeting(cid, sid, gid)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py backend/tests/test_scene_store.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/src/grimoire/store/playing.py backend/tests/test_playing_store.py
git commit -m "feat(store): stamp the originating greeting on scenes started from one"
```

---

### Task 2: `available_greetings(cid, after=…)` — unlock flag + sort

**Files:**
- Modify: `backend/src/grimoire/store/playing.py:49-52` (`available_greetings`)
- Test: `backend/tests/test_playing_store.py`

**Interfaces:**
- Consumes: `scenes.read_scene(cid, sid)["meta"].get("greeting", "")` (Task 1); `greetings.edges_of(plotmap, gid)["leads_to"]`.
- Produces: `playing.available_greetings(cid: str, after: str | None = None) -> list[dict]` — each item `{id, name, available, reasons, unlocked}`; sorted unlocked-first (stable). `after` naming a missing scene raises `scenes.SceneNotFound`. Task 3 passes the query param through; Task 4's `Availability` type mirrors the shape.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_playing_store.py`:

```python
def test_available_greetings_after_flags_and_sorts_unlocked(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g1 = greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    g2 = greetings.create_greeting(wroot, "Omega", "s", "default", body="O.")
    g3 = greetings.create_greeting(wroot, "Middle", "s", "default", body="M.")
    greetings.set_edges(wroot, g1, leads_to=[g3])
    playing.start_from_greeting(cid, sid, g1)
    got = playing.available_greetings(cid, after=sid)
    assert got[0]["id"] == g3                      # the unlocked greeting sorts first
    assert {x["id"]: x["unlocked"] for x in got} == {g1: False, g2: False, g3: True}


def test_available_greetings_after_without_stamp_all_false(monkeypatch, tmp_path):
    wid, cid, sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    got = playing.available_greetings(cid, after=sid)   # scene never started from a greeting
    assert [x["unlocked"] for x in got] == [False]


def test_available_greetings_no_after_has_unlocked_false(monkeypatch, tmp_path):
    wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    assert [x["unlocked"] for x in playing.available_greetings(cid)] == [False]


def test_available_greetings_unknown_after_raises(monkeypatch, tmp_path):
    wid, cid, _sid = _campaign(monkeypatch, tmp_path)
    with pytest.raises(scenes.SceneNotFound):
        playing.available_greetings(cid, after="nope")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py -q`
Expected: the 4 new tests FAIL with `TypeError: available_greetings() got an unexpected keyword argument 'after'`.

- [ ] **Step 3: Implement**

Replace `available_greetings` in `backend/src/grimoire/store/playing.py`:

```python
def available_greetings(cid: str, after: str | None = None) -> list[dict]:
    wroot = _world_root(cid)
    plotmap = greetings.read_plotmap(wroot)
    out = greetings.availability(wroot, plotmap, read_played(cid), player_tags(cid))
    unlocked: set[str] = set()
    if after:
        gid = scenes.read_scene(cid, after)["meta"].get("greeting", "")
        if gid:
            unlocked = set(greetings.edges_of(plotmap, gid)["leads_to"])
    for g in out:
        g["unlocked"] = g["id"] in unlocked
    out.sort(key=lambda g: not g["unlocked"])  # stable: unlocked first, rest keep order
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py -q`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/playing.py backend/tests/test_playing_store.py
git commit -m "feat(store): flag + sort greetings unlocked by a given scene"
```

---

### Task 3: Route — `?after=` on `GET /campaigns/{cid}/greetings/available`

**Files:**
- Modify: `backend/src/grimoire/routes.py:1655-1658` (`get_available_greetings`)
- Test: `backend/tests/test_routes.py` (after `test_start_from_greeting_unknown_404`, ~line 1035)

**Interfaces:**
- Consumes: `playing.available_greetings(cid, after=…)` (Task 2).
- Produces: `GET /api/campaigns/{cid}/greetings/available?after=<sid>` → the Task 2 list; unknown `after` scene → `404 {"detail": "scene not found"}`. Task 4's client calls this.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_routes.py` after `test_start_from_greeting_unknown_404`:

```python
def test_available_greetings_after_param(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    g1 = client.post(f"/api/worlds/{wid}/greetings",
                     json={"name": "Alpha", "character": "seraphine", "version": "default",
                           "body": "A."}).json()["id"]
    g2 = client.post(f"/api/worlds/{wid}/greetings",
                     json={"name": "Reckoning", "character": "seraphine", "version": "default",
                           "body": "R."}).json()["id"]
    client.put(f"/api/worlds/{wid}/greetings/{g1}/edges", json={"leads_to": [g2]})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Opening"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting", json={"greeting": g1})
    avail = client.get(f"/api/campaigns/{cid}/greetings/available", params={"after": sid}).json()
    assert avail[0]["id"] == g2 and avail[0]["unlocked"] is True
    # no param: same shape, nothing flagged
    plain = client.get(f"/api/campaigns/{cid}/greetings/available").json()
    assert all(x["unlocked"] is False for x in plain)
    assert client.get(f"/api/campaigns/{cid}/greetings/available",
                      params={"after": "nope"}).status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_available_greetings_after_param -q`
Expected: FAIL — the `after=nope` call returns 200 (param ignored), or 500 once passed through unhandled.

- [ ] **Step 3: Implement**

Replace `get_available_greetings` in `backend/src/grimoire/routes.py`:

```python
@router.get("/campaigns/{cid}/greetings/available")
def get_available_greetings(cid: str, after: str | None = None):
    _campaign_root_or_404(cid)
    try:
        return store.playing.available_greetings(cid, after=after)
    except store.scenes.SceneNotFound:
        raise HTTPException(status_code=404, detail="scene not found")
```

- [ ] **Step 4: Run the backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS (existing availability assertions are key-based and unaffected by the new field).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(routes): ?after= unlock flag on available greetings"
```

---

### Task 4: `NewSceneChooser` component + api client + CSS

**Files:**
- Modify: `frontend/src/api/client.ts:135` (`Availability` type), `:426-427` (`availableGreetings`)
- Create: `frontend/src/components/NewSceneChooser.tsx`
- Modify: `frontend/src/index.css` (after the `.tagline-modal` block, ~line 693)
- Test: `frontend/src/components/NewSceneChooser.test.tsx` (new)

**Interfaces:**
- Consumes: `GET /greetings/available?after=` (Task 3); existing `api.sceneSuggestions`, `api.createScene`, `api.startFromGreeting`, `api.addToCast`, `api.setSceneLocation`.
- Produces: `NewSceneChooser` with props `{ cid: string; afterSid: string | null; keySet: boolean; onClose: () => void; onCreated: (sid: string, initialPrompt?: string) => void }`. Task 6 renders it. `Availability` gains `unlocked: boolean`; `availableGreetings(cid: string, after?: string)`.

- [ ] **Step 1: Update the api client**

In `frontend/src/api/client.ts`, change the `Availability` type (line 135):

```ts
export type Availability = {
  id: string; name: string; available: boolean; reasons: string[]; unlocked: boolean;
};
```

and the `availableGreetings` entry (lines 426-427):

```ts
  availableGreetings: (cid: string, after?: string) =>
    request<Availability[]>("GET",
      `/api/campaigns/${cid}/greetings/available${after ? `?after=${encodeURIComponent(after)}` : ""}`),
```

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/components/NewSceneChooser.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NewSceneChooser } from "./NewSceneChooser";

vi.mock("../api/client", () => ({
  api: {
    availableGreetings: vi.fn(), sceneSuggestions: vi.fn(), createScene: vi.fn(),
    startFromGreeting: vi.fn(), addToCast: vi.fn(), setSceneLocation: vi.fn(),
  },
}));
import { api } from "../api/client";

const GREETINGS = [
  { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true },
  { id: "open", name: "Open", available: true, reasons: [], unlocked: false },
  { id: "gala", name: "Gala", available: false, reasons: ["missing required tags"], unlocked: false },
  { id: "dawn", name: "Dawn", available: true, reasons: [], unlocked: false },
];
const SUGGESTION = {
  title: "The creditor", premise: "A debt-collector arrives.",
  cast: [{ kind: "characters", id: "doran", name: "Doran" }],
  location: { id: "keep", name: "The Keep" },
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.availableGreetings as any).mockResolvedValue(GREETINGS);
  (api.sceneSuggestions as any).mockResolvedValue({ suggestions: [SUGGESTION,
    { title: "Storm watch", premise: "Thunder over the marsh.", cast: [], location: null }] });
  (api.createScene as any).mockResolvedValue({ id: "s9" });
  (api.startFromGreeting as any).mockResolvedValue({ ok: true });
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: false, name: "" });
});

function renderChooser(props: Partial<{ afterSid: string | null; keySet: boolean;
                                        onClose: () => void; onCreated: (sid: string, p?: string) => void }> = {}) {
  render(<NewSceneChooser cid="c" afterSid={props.afterSid ?? "s1"} keySet={props.keySet ?? true}
                          onClose={props.onClose ?? (() => {})}
                          onCreated={props.onCreated ?? (() => {})} />);
}

test("renders 2 greeting cards (unlocked first) and generated cards once loaded", async () => {
  renderChooser();
  await screen.findByText("Reckoning");
  expect(screen.getByText("unlocked")).toBeInTheDocument();
  expect(screen.getByText("Open")).toBeInTheDocument();
  expect(screen.queryByText("Dawn")).toBeNull();        // capped at 2 when generation is on
  expect(screen.queryByText("Gala")).toBeNull();        // unavailable greetings never show
  await screen.findByText("The creditor");              // async generated card
  expect(screen.getByText("Storm watch")).toBeInTheDocument();
  expect(api.availableGreetings).toHaveBeenCalledWith("c", "s1");
});

test("picking a greeting creates a scene, starts it, and reports the sid", async () => {
  const onCreated = vi.fn();
  renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("Reckoning"));
  await waitFor(() => expect(api.startFromGreeting).toHaveBeenCalledWith("c", "s9", "reck"));
  expect(api.createScene).toHaveBeenCalledWith("c");
  expect(onCreated).toHaveBeenCalledWith("s9");
});

test("picking a generated card seeds cast + location and passes the premise", async () => {
  const onCreated = vi.fn();
  renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9", "A debt-collector arrives."));
  expect(api.addToCast).toHaveBeenCalledWith("c", "s9", { kind: "characters", id: "doran" });
  expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s9", "keep");
});

test("Create manually only creates the scene", async () => {
  const onCreated = vi.fn();
  renderChooser({ onCreated });
  fireEvent.click(await screen.findByRole("button", { name: /create manually/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9"));
  expect(api.startFromGreeting).not.toHaveBeenCalled();
  expect(api.addToCast).not.toHaveBeenCalled();
});

test("Cancel closes without creating anything", async () => {
  const onClose = vi.fn();
  renderChooser({ onClose });
  fireEvent.click(await screen.findByRole("button", { name: /cancel/i }));
  expect(onClose).toHaveBeenCalled();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("without a key: no suggestions fetch, hint shown, up to 4 greetings", async () => {
  renderChooser({ keySet: false });
  await screen.findByText("Reckoning");
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  expect(screen.getByText(/set an openrouter key/i)).toBeInTheDocument();
  expect(screen.getByText("Dawn")).toBeInTheDocument(); // slot cap grows to 4
});

test("no afterSid fetches availability without the param", async () => {
  renderChooser({ afterSid: null });
  await screen.findByText("Reckoning");
  expect(api.availableGreetings).toHaveBeenCalledWith("c", undefined);
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/NewSceneChooser.test.tsx`
Expected: FAIL — cannot resolve `./NewSceneChooser`.

- [ ] **Step 4: Implement the component**

Create `frontend/src/components/NewSceneChooser.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api, type Availability, type SceneSuggestion } from "../api/client";

// LLM-backed endpoints 502 with an object detail; coerce so it renders as text.
function errMsg(err: any): string {
  const d = err?.detail;
  return typeof d === "string" ? d : (d?.detail ?? String(err));
}

export function NewSceneChooser({ cid, afterSid, keySet, onClose, onCreated }: {
  cid: string;
  afterSid: string | null;          // ranking reference: the selected (or latest) scene
  keySet: boolean;
  onClose: () => void;
  onCreated: (sid: string, initialPrompt?: string) => void;
}) {
  const [greetings, setGreetings] = useState<Availability[]>([]);
  // null = still generating; [] = nothing to offer (no key, empty, or failed)
  const [suggestions, setSuggestions] = useState<SceneSuggestion[] | null>(keySet ? null : []);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.availableGreetings(cid, afterSid ?? undefined)
      .then((all) => setGreetings(all.filter((g) => g.available)))
      .catch(() => setGreetings([]));
  }, [cid, afterSid]);

  useEffect(() => {
    if (!keySet) return;
    api.sceneSuggestions(cid)
      .then((r) => setSuggestions(r.suggestions))
      .catch((err) => { setSuggestions([]); setError(errMsg(err)); });
  }, [cid, keySet]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 4 slots: 2 greetings + 2 generated; greetings grow to 4 when nothing will generate
  const wantGenerated = keySet && (suggestions === null || suggestions.length > 0);
  const greetingCards = greetings.slice(0, wantGenerated ? 2 : 4);
  const generatedCards = (suggestions ?? []).slice(0, 4 - greetingCards.length);

  async function create(seed: (sid: string) => Promise<string | undefined>) {
    setBusy(true);
    setError(null);
    try {
      const { id } = await api.createScene(cid);
      const prompt = await seed(id);
      if (prompt !== undefined) onCreated(id, prompt);
      else onCreated(id);
    } catch (err: any) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  }

  const pickManual = () => create(async () => undefined);
  const pickGreeting = (gid: string) => create(async (sid) => {
    await api.startFromGreeting(cid, sid, gid);
    return undefined;
  });
  const pickSuggestion = (s: SceneSuggestion) => create(async (sid) => {
    for (const c of s.cast) {
      try { await api.addToCast(cid, sid, { kind: c.kind, id: c.id }); }
      catch { /* already cast — keep seeding */ }
    }
    if (s.location) await api.setSceneLocation(cid, sid, s.location.id);
    return s.premise;
  });

  return (
    <div className="chooser-backdrop" role="dialog" aria-label="New scene" onClick={onClose}>
      <div className="chooser" onClick={(e) => e.stopPropagation()}>
        <h3>New scene</h3>
        {error && <div className="banner">{error}</div>}

        <div className="role">From a greeting</div>
        {greetingCards.length === 0 && <div className="field-hint">No available greetings.</div>}
        {greetingCards.map((g) => (
          <button className="chooser-card" key={g.id} disabled={busy} onClick={() => pickGreeting(g.id)}>
            <span className="chooser-card-title">{g.name}</span>
            {g.unlocked && <span className="chip on">unlocked</span>}
          </button>
        ))}

        <div className="role">Generated</div>
        {!keySet && <div className="field-hint">Set an OpenRouter key in Config to generate.</div>}
        {keySet && suggestions === null && <div className="field-hint">Generating…</div>}
        {generatedCards.map((s, i) => (
          <button className="chooser-card" key={i} disabled={busy} onClick={() => pickSuggestion(s)}>
            <span className="chooser-card-title">{s.title}</span>
            <span className="chooser-card-premise">{s.premise}</span>
            <span className="field-hint">
              {s.cast.map((c) => c.name).join(", ")}{s.location ? ` · ${s.location.name}` : ""}
            </span>
          </button>
        ))}

        <div className="form-actions">
          <button className="subtle" disabled={busy} onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy} onClick={pickManual}>Create manually</button>
        </div>
      </div>
    </div>
  );
}
```

Add to `frontend/src/index.css` directly after the `.tagline-modal textarea` rule (~line 693):

```css
.chooser-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,0.5); display: flex; align-items: center; justify-content: center; z-index: 30; }
.chooser { background: var(--panel); border: 1px solid var(--muted); padding: 20px; width: min(560px, 92vw); max-height: 80vh; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.chooser h3 { font-family: var(--fd); margin: 0 0 6px; }
.chooser-card { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; text-align: left; padding: 10px 12px; background: transparent; border: 1px solid var(--muted); color: inherit; cursor: pointer; }
.chooser-card:hover:not(:disabled) { border-color: var(--accent); }
.chooser-card:disabled { opacity: 0.6; cursor: default; }
.chooser-card-title { font-weight: 600; }
.chooser-card-premise { opacity: 0.85; }
```

- [ ] **Step 5: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/NewSceneChooser.test.tsx` then `npx tsc -b`
Expected: all PASS; tsc clean. (`CastPanel`/`CampaignWizard` still call `availableGreetings(cid)` — the new param is optional, so they compile.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/NewSceneChooser.tsx frontend/src/components/NewSceneChooser.test.tsx frontend/src/index.css
git commit -m "feat(frontend): NewSceneChooser modal - greetings + generated premises + manual"
```

---

### Task 5: CastPanel cleanup + `initialPrompt`

**Files:**
- Modify: `frontend/src/components/CastPanel.tsx`
- Modify: `frontend/src/routes/CampaignView.tsx:380` (drop the `sceneEmpty={true}` prop)
- Test: `frontend/src/components/CastPanel.test.tsx`

**Interfaces:**
- Produces: `CastPanel` props gain `initialPrompt?: string` (seeds the opener prompt) and lose `sceneEmpty` (dead once the greeting block goes — the panel only ever renders for empty scenes, so `CampaignView` always passed `true`). The "Start from a greeting" section and the "Suggest scenes" block are removed. Task 6 passes `initialPrompt`.

**Note:** dropping the `sceneEmpty` prop makes `CampaignView.tsx`'s `sceneEmpty={true}` a TS error, so this task also deletes that one line from `CampaignView.tsx:380` — the only cross-file edit here (no other component renders CastPanel).

- [ ] **Step 1: Update the tests**

In `frontend/src/components/CastPanel.test.tsx`:

1. Delete these tests: `"Suggest scenes fetches, renders, and a pick auto-seeds + prefills the prompt"`, `"starting from an available greeting seeds the scene; unavailable is disabled"`, `"start-from-greeting is disabled when the scene is not empty"`.
2. In the `vi.mock` factory, remove `availableGreetings: vi.fn(), ... startFromGreeting: vi.fn(),` and `sceneSuggestions: vi.fn(),` (keep `addToCast`).
3. In `beforeEach`, remove the `(api.availableGreetings as any).mockResolvedValue([...])` and `(api.startFromGreeting as any).mockResolvedValue({ ok: true });` lines.
4. Replace `renderPanel`: drop `sceneEmpty`, accept `initialPrompt`:

```tsx
function renderPanel(props: Partial<{ keySet: boolean; onSeeded: () => void;
                                      onSceneRenamed: (id: string) => void; initialPrompt: string }> = {}) {
  render(
    <CastPanel cid="c" sid="s" keySet={props.keySet ?? true}
               onSeeded={props.onSeeded ?? (() => {})} onSceneRenamed={props.onSceneRenamed}
               initialPrompt={props.initialPrompt} />,
  );
}
```

5. Add:

```tsx
test("initialPrompt seeds the opener prompt", async () => {
  renderPanel({ initialPrompt: "A debt-collector arrives." });
  await waitFor(() => expect(
    (screen.getByLabelText("Opener prompt") as HTMLInputElement).value,
  ).toBe("A debt-collector arrives."));
});
```

- [ ] **Step 2: Run tests to verify the new one fails**

Run (from `frontend/`): `npx vitest run src/components/CastPanel.test.tsx`
Expected: the `initialPrompt` test FAILS (prompt stays empty); the deleted tests are gone.

- [ ] **Step 3: Implement**

In `frontend/src/components/CastPanel.tsx`:

1. Props: add `initialPrompt`, drop `sceneEmpty` —

```tsx
export function CastPanel({
  cid, sid, keySet, onSeeded, onSceneRenamed, initialPrompt,
}: {
  cid: string;
  sid: string;
  keySet: boolean;
  onSeeded: () => void;
  onSceneRenamed?: (id: string) => void;
  initialPrompt?: string;
}) {
```

2. Remove the `avail` state, the `suggestions` state, and the `api.availableGreetings(...)` line plus `setSuggestions([]);` in the load effect.
3. Remove the `start`, `suggestScenes`, and `useSuggestion` functions.
4. Remove the `Start from a greeting` JSX block (the only consumer of `sceneEmpty`) and the `suggest-scenes` JSX block.
5. Trim now-unused imports: drop `Availability` and `SceneSuggestion` from the client import.
6. In `frontend/src/routes/CampaignView.tsx:380`, delete the `sceneEmpty={true}` line from the `<CastPanel …>` element (the prop no longer exists; no other component renders CastPanel).
7. Seed the prompt:

```tsx
  useEffect(() => {
    if (initialPrompt) setPrompt(initialPrompt);
  }, [initialPrompt]);
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/CastPanel.test.tsx` then `npx tsc -b`
Expected: all PASS; tsc clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/CastPanel.tsx frontend/src/components/CastPanel.test.tsx frontend/src/routes/CampaignView.tsx
git commit -m "refactor(frontend): CastPanel drops greeting/suggest blocks, gains initialPrompt"
```

---

### Task 6: CampaignView wiring

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx`
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: `NewSceneChooser` (Task 4) with `onCreated(sid, initialPrompt?)`; `CastPanel.initialPrompt` (Task 5).

- [ ] **Step 1: Write the failing tests**

In `frontend/src/routes/CampaignView.test.tsx`:

1. Add mocks next to the existing CastPanel mock (top of file) — replace the CastPanel mock line and add the chooser mock:

```tsx
vi.mock("../components/CastPanel", () => ({
  CastPanel: ({ initialPrompt }: any) => <div data-testid="cast-panel">{initialPrompt ?? ""}</div>,
}));
vi.mock("../components/NewSceneChooser", () => ({
  NewSceneChooser: ({ onCreated, onClose }: any) => (
    <div data-testid="scene-chooser">
      <button onClick={() => onCreated("s9", "A premise")}>stub-pick</button>
      <button onClick={() => onClose()}>stub-close</button>
    </div>
  ),
}));
```

2. Add tests:

```tsx
test("+ New Scene opens the chooser without creating a scene", async () => {
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  expect(await screen.findByTestId("scene-chooser")).toBeInTheDocument();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("a chooser pick refreshes the rail, selects the scene, and seeds the prompt", async () => {
  (api.listScenes as any)
    .mockResolvedValueOnce([])                       // initial load
    .mockResolvedValue([{ id: "s9", title: "New", model: "", created: "", updated: "" }]);
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-pick"));
  await waitFor(() => expect(api.getScene).toHaveBeenCalledWith("run", "s9"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  // the premise reaches the empty scene's CastPanel
  expect(await screen.findByText("A premise")).toBeInTheDocument();
});

test("closing the chooser creates nothing", async () => {
  renderCampaign();
  await screen.findByText(/Run One/);
  fireEvent.click(screen.getByRole("button", { name: /\+ new scene/i }));
  fireEvent.click(await screen.findByText("stub-close"));
  expect(screen.queryByTestId("scene-chooser")).toBeNull();
  expect(api.createScene).not.toHaveBeenCalled();
  expect(api.getScene).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx`
Expected: the 3 new tests FAIL — clicking `+ New Scene` calls `api.createScene` directly and no chooser appears.

- [ ] **Step 3: Implement**

In `frontend/src/routes/CampaignView.tsx`:

1. Import: `import { NewSceneChooser } from "../components/NewSceneChooser";`
2. State (with the other `useState` calls):

```tsx
  const [chooserOpen, setChooserOpen] = useState(false);
  const [seedPrompt, setSeedPrompt] = useState<{ sid: string; prompt: string } | null>(null);
```

3. Replace `newScene` (lines 92-96):

```tsx
  function newScene() {
    setChooserOpen(true);
  }

  async function sceneCreated(id: string, initialPrompt?: string) {
    setChooserOpen(false);
    if (initialPrompt) setSeedPrompt({ sid: id, prompt: initialPrompt });
    setScenes(await api.listScenes(cid));
    selectScene(id);
  }
```

4. Render the chooser inside the top-level `.workspace` div, right before the closing `</div>` (next to the `RecordDrawer` conditional):

```tsx
      {chooserOpen && (
        <NewSceneChooser cid={cid} afterSid={activeId} keySet={keySet}
                         onClose={() => setChooserOpen(false)} onCreated={sceneCreated} />
      )}
```

5. Pass the seed to the empty-scene panel — the `CastPanel` element gains one prop:

```tsx
            initialPrompt={seedPrompt?.sid === activeId ? seedPrompt.prompt : undefined}
```

- [ ] **Step 4: Run the full frontend suite**

Run (from `frontend/`): `npx vitest run` then `npx tsc -b`
Expected: all PASS; tsc clean. (`CampaignWizard` keeps its own `availableGreetings(cid)` call — untouched by design.)

- [ ] **Step 5: Run the backend suite once more (whole-feature check)**

Run (repo root): `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx
git commit -m "feat(frontend): + New Scene opens the chooser; create on pick"
```
