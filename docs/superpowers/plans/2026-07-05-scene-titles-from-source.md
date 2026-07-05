# Scene Titles From Source Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A scene started from a greeting takes the greeting's name as its title; a scene created from a generated suggestion card takes the card's generated title; manual scenes stay "New scene".

**Architecture:** The backend owns the greeting case — `store.playing.start_from_greeting` renames the scene to the greeting's name as its **last** step and returns the new sid, which the route surfaces as `id`. The frontend owns the suggestion case — `NewSceneChooser` passes `s.title` into `createScene`, and adopts the renamed id from the start-from-greeting response.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend).

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)` (store tests) or the existing `client` fixture (route tests).
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Run frontend tests **from `frontend/`**: `npx vitest run` and `npx tsc -b` (never `npx --prefix frontend` — it skips `vitest.config.ts`).
- Commit after each task.

---

### Task 1: `start_from_greeting` retitles the scene and returns the new sid

**Files:**
- Modify: `backend/src/grimoire/store/playing.py` (function `start_from_greeting`, end of file)
- Test: `backend/tests/test_playing_store.py`

**Interfaces:**
- Consumes: `scenes.rename_scene(cid, sid, title) -> str` (already exists; re-slugs the filename and repoints all referencing stores).
- Produces: `playing.start_from_greeting(cid: str, sid: str, gid: str) -> str` — returns the post-rename sid. Task 2's route depends on this return value.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_playing_store.py` (after `test_start_from_greeting_stamps_greeting`):

```python
def test_start_from_greeting_takes_greeting_title(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "Seraphine", "default", characters.blank_card("Seraphine"))
    g = greetings.create_greeting(wroot, "A Chance Meeting", "seraphine", "default", body="Hi.")
    cid, sid = _campaign_after_seed(wid)
    new_sid = playing.start_from_greeting(cid, sid, g)
    assert new_sid != sid and "a-chance-meeting" in new_sid
    scene = scenes.read_scene(cid, new_sid)
    assert scene["meta"]["title"] == "A Chance Meeting"
    assert scene["meta"]["greeting"] == g            # stamp survived the rename
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py::test_start_from_greeting_takes_greeting_title -q`
Expected: FAIL — `new_sid` is `None` (function currently returns nothing).

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/playing.py`, change the signature and the tail of `start_from_greeting`:

```python
def start_from_greeting(cid: str, sid: str, gid: str) -> str:
```

and replace the final line

```python
    scenes.append_message(cid, sid, "assistant", text)
```

with

```python
    scenes.append_message(cid, sid, "assistant", text)
    # retitle last: any earlier failure leaves the caller's sid valid for cleanup
    return scenes.rename_scene(cid, sid, g["name"])
```

- [ ] **Step 4: Update existing tests that reuse the old sid after a start**

In `backend/tests/test_playing_store.py`, four tests keep using `sid` after `start_from_greeting`; adopt the returned sid:

- `test_start_from_greeting_seeds_appears_marks`: `playing.start_from_greeting(cid, sid, g)` → `sid = playing.start_from_greeting(cid, sid, g)` (line ~71; the read and the second-start `PlayError` block below it keep working).
- `test_start_from_greeting_stamps_greeting`: same change, then the existing `scenes.read_scene(cid, sid)` assertion stands.
- `test_available_greetings_after_flags_and_sorts_unlocked`: `playing.start_from_greeting(cid, sid, g1)` → `sid = playing.start_from_greeting(cid, sid, g1)` (the `after=sid` call below needs the live id).
- `test_campaign_play_isolated_from_world_edits`: `playing.start_from_greeting(cid, sid, g)` → `sid = playing.start_from_greeting(cid, sid, g)`.

(`test_start_from_greeting_casts_all_present` and `test_start_from_greeting_locked_version_wins` never touch `sid` afterwards — leave them.)

- [ ] **Step 5: Run the store test file**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/playing.py backend/tests/test_playing_store.py
git commit -m "feat(backend): starting from a greeting retitles the scene to the greeting's name"
```

---

### Task 2: start-from-greeting route returns the renamed id

**Files:**
- Modify: `backend/src/grimoire/routes.py` (function `post_start_from_greeting`)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.playing.start_from_greeting(...) -> str` from Task 1.
- Produces: `POST /campaigns/{cid}/scenes/{sid}/start-from-greeting` → `{"ok": true, "id": "<new sid>"}`. Task 3's `api.startFromGreeting` return type mirrors this.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_routes.py` (after `test_start_from_greeting_unknown_404`):

```python
def test_start_from_greeting_retitles_scene(client):
    wid, cid = _campaign(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Vex"})
    ver = client.get(f"/api/worlds/{wid}/characters/vex").json()["meta"]["default_version"]
    g = client.post(f"/api/campaigns/{cid}/greetings", json={
        "name": "A Chance Meeting", "character": "vex", "version": ver,
        "body": "Hi."}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                    json={"greeting": g})
    assert r.status_code == 200
    new_sid = r.json()["id"]
    assert new_sid != sid and "a-chance-meeting" in new_sid
    scene = client.get(f"/api/campaigns/{cid}/scenes/{new_sid}").json()
    assert scene["meta"]["title"] == "A Chance Meeting"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py::test_start_from_greeting_retitles_scene -q`
Expected: FAIL — response JSON has no `"id"` key (`KeyError`).

- [ ] **Step 3: Implement**

In `backend/src/grimoire/routes.py`, `post_start_from_greeting`:

```python
@router.post("/campaigns/{cid}/scenes/{sid}/start-from-greeting")
def post_start_from_greeting(cid: str, sid: str, body: StartFromGreeting):
    _require_scene(cid, sid)
    try:
        new_sid = store.playing.start_from_greeting(cid, sid, body.greeting)
    except store.greetings.GreetingNotFound:
        raise HTTPException(status_code=404, detail="greeting not found")
    except (store.playing.PlayError, store.appearances.AppearError) as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"ok": True, "id": new_sid}
```

- [ ] **Step 4: Update existing route tests that reuse the old sid**

In `backend/tests/test_routes.py`, three call sites read the scene (or pass `after=`) with the pre-rename sid; adopt the response id:

- The lorebook-import end-to-end test (~line 1051): after `assert r.status_code == 200` insert `sid = r.json()["id"]` before `scene = client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()`. The follow-up 409 second-start below it then also uses the live sid.
- `test_available_greetings_after_param` (~line 1080): `client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting", json={"greeting": g1})` → `sid = client.post(...same...).json()["id"]` (the `params={"after": sid}` call below needs the live id).
- `test_offscreen_greeting_stamps_scene_and_substitutes_pc_name` (~line 2314): capture the response — replace the bare `assert client.post(...).status_code == 200` with:

```python
    r = client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                    json={"greeting": g})
    assert r.status_code == 200
    sid = r.json()["id"]
```

(`test_start_from_greeting_unknown_404`, `test_campaign_greeting_mark_played_conflicts`, `test_pc_greeting_cannot_start_an_offscreen_scene`, and `test_offscreen_greeting_rejects_a_scene_with_players` never reuse the sid after a **successful** start — leave them.)

- [ ] **Step 5: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(backend): start-from-greeting responds with the retitled scene id"
```

---

### Task 3: chooser passes suggestion titles and adopts the renamed greeting-scene id

**Files:**
- Modify: `frontend/src/api/client.ts` (`startFromGreeting` return type)
- Modify: `frontend/src/components/NewSceneChooser.tsx` (`create`, `pickManual`, `pickGreeting`, `pickSuggestion`)
- Test: `frontend/src/components/NewSceneChooser.test.tsx`

**Interfaces:**
- Consumes: `{"ok": true, "id": string}` from Task 2's route.
- Produces: no downstream consumers — `CampaignWizard.tsx` also calls `startFromGreeting` but only navigates to the campaign afterwards, so it needs **no change** (the extra `id` field is ignored there).

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/NewSceneChooser.test.tsx`:

1. In `beforeEach`, give the default mock the new response shape (keeps every existing greeting test passing unchanged):

```tsx
  (api.startFromGreeting as any).mockResolvedValue({ ok: true, id: "s9" });
```

2. Add two tests (after `"picking a generated card seeds cast + location and passes the premise"`):

```tsx
test("a greeting pick adopts the renamed scene id", async () => {
  (api.startFromGreeting as any).mockResolvedValue({ ok: true, id: "s9-reckoning" });
  const onCreated = vi.fn();
  await renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("Reckoning"));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9-reckoning"));
});

test("picking a generated card passes its title to createScene", async () => {
  const onCreated = vi.fn();
  await renderChooser({ onCreated });
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  expect(api.createScene).toHaveBeenCalledWith("c", "The creditor", undefined, false);
});
```

3. Update the two existing date tests whose `createScene` assertions now carry a title (`"picking a generated card passes its suggested date to createScene"` and `"a card without a date falls back to next_date"`):

```tsx
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", "The creditor", "2026-07-10", false));
```

```tsx
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", "The creditor", "2026-07-08", false));
```

(Manual-create and greeting-pick assertions keep `undefined` as the title argument — those tests are untouched.)

- [ ] **Step 2: Run tests to verify the new ones fail**

From `frontend/`: `npx vitest run src/components/NewSceneChooser.test.tsx`
Expected: the two new tests FAIL (title argument is `undefined`; `onCreated` gets `"s9"` not `"s9-reckoning"`); the pre-existing ones PASS.

- [ ] **Step 3: Implement**

In `frontend/src/api/client.ts`:

```tsx
  startFromGreeting: (cid: string, sid: string, greeting: string) =>
    request<{ ok: boolean; id: string }>("POST", `/api/campaigns/${cid}/scenes/${sid}/start-from-greeting`, { greeting }),
```

In `frontend/src/components/NewSceneChooser.tsx`, replace `create` and the three pickers. The seed callback now returns an optional replacement sid (the post-rename id) and the optional initial prompt; `create` gains a title parameter:

```tsx
  async function create(seed: (sid: string) => Promise<{ id?: string; prompt?: string }>,
                        title?: string, date?: string) {
    setBusy(true);
    setError(null);
    let created: string | null = null;
    try {
      const { id } = await api.createScene(cid, title, date, mode === "offscreen");
      created = id;
      const r = await seed(id);
      if (r.prompt !== undefined) onCreated(r.id ?? id, r.prompt);
      else onCreated(r.id ?? id);
    } catch (err: any) {
      // a half-seeded scene would be a stray — remove it before surfacing the error
      if (created) await api.deleteScene(cid, created).catch(() => {});
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  }

  const pickManual = () => create(async () => ({}), undefined, nextDate);
  const pickGreeting = (gid: string) => create(async (sid) => {
    // the backend retitles the scene to the greeting's name; adopt the new id
    const { id } = await api.startFromGreeting(cid, sid, gid);
    return { id };
  }, undefined, nextDate);
  const pickSuggestion = (s: SceneSuggestion) => create(async (sid) => {
    if (s.cast.length) {
      // one request; members already cast come back in `skipped`, which is fine
      await api.addCastBatch(cid, sid, s.cast.map((c) => ({ kind: c.kind, id: c.id })));
    }
    if (s.location) await api.setSceneLocation(cid, sid, s.location.id);
    return { prompt: s.premise };
  }, s.title, s.date || nextDate);
```

- [ ] **Step 4: Run the frontend suite and typecheck**

From `frontend/`: `npx vitest run` then `npx tsc -b`
Expected: all PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/NewSceneChooser.tsx frontend/src/components/NewSceneChooser.test.tsx
git commit -m "feat(frontend): new scenes take their title from the greeting or suggestion"
```

---

### Task 4: full verification

- [ ] **Step 1: Run both suites**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
From `frontend/`: `npx vitest run` and `npx tsc -b`
Expected: everything PASS.

- [ ] **Step 2: Commit any stragglers**

Only if a fix was needed; otherwise nothing to commit.
