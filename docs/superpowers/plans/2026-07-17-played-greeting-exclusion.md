# Played/Completed Greeting Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Greetings marked `played` or `completed` become unavailable for starting new scenes (hard block, server-side); `skipped` already is.

**Architecture:** One self-exclusion rule in `playing.available_greetings` flips marked greetings to `available: false`, which every consumer (New Scene chooser, LLM greeting ranking, `start_from_greeting`) already filters on. Two companion fixes prevent regressions the hard block would introduce: `_mark_played` moves to the end of `start_from_greeting` (a failed start must not consume the greeting), and greeting deletion purges the id from the mark sets (a recreated same-slug greeting must not inherit an unclearable `played` mark).

**Tech Stack:** FastAPI backend (pytest), React frontend (vitest). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-17-played-greeting-exclusion-design.md`

## Global Constraints

- Reason strings are exactly `"already played"` and `"marked complete"`.
- Test fixtures use the repo's placeholder names only (Seraphine, Mara, Winifred, Realm, Saltmarch) — never real world/campaign/character names.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))` (the existing `_world` helper does this).
- Backend test run: `backend/.venv/Scripts/python.exe -m pytest backend -q`. Frontend: run `npx vitest run` and `npx tsc -b` **from `frontend/`** (never `npx --prefix`).
- pydantic stays v1/v2-agnostic; no new model fields are needed anywhere in this plan.

---

### Task 1: Availability self-exclusion for played/completed greetings

**Files:**
- Modify: `backend/src/grimoire/store/playing.py:76-94` (`available_greetings`)
- Test: `backend/tests/test_playing_store.py`
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: existing `playing.available_greetings(cid, after=None)`, `playing.mark_greeting(cid, gid, status)`, `playing.start_from_greeting(cid, sid, gid)`.
- Produces: `available_greetings` list items now have `available: False` and a reason (`"already played"` / `"marked complete"`) whenever `mark` is set. `start_from_greeting` consequently raises `PlayError` for marked greetings (it validates against the `available` flag). Tasks 2–4 rely on this behavior.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_playing_store.py` (the file's `_world` / `_campaign_after_seed` helpers already exist at the top):

```python
def test_available_greetings_excludes_played_and_completed(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g1 = greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    g2 = greetings.create_greeting(wroot, "Beta", "s", "default", body="B.")
    g3 = greetings.create_greeting(wroot, "Gamma", "s", "default", body="C.")
    greetings.set_edges(wroot, g1, leads_to=[g3])
    cid, sid = _campaign_after_seed(wid)
    playing.start_from_greeting(cid, sid, g1)
    playing.mark_greeting(cid, g2, "completed")
    got = {x["id"]: x for x in playing.available_greetings(cid)}
    assert got[g1]["available"] is False and "already played" in got[g1]["reasons"]
    assert got[g2]["available"] is False and "marked complete" in got[g2]["reasons"]
    assert got[g3]["available"] is True        # the play still unlocks its successor
    playing.mark_greeting(cid, g2, "none")     # clearing the mark restores it
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g2] is True


def test_start_from_greeting_refuses_played_and_completed(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g1 = greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    g2 = greetings.create_greeting(wroot, "Beta", "s", "default", body="B.")
    cid, sid = _campaign_after_seed(wid)
    playing.start_from_greeting(cid, sid, g1)
    fresh = scenes.create_scene(cid, "S2")
    with pytest.raises(playing.PlayError):     # replay of a played greeting
        playing.start_from_greeting(cid, fresh, g1)
    playing.mark_greeting(cid, g2, "completed")
    with pytest.raises(playing.PlayError):     # direct start of a completed greeting
        playing.start_from_greeting(cid, fresh, g2)


def test_legacy_played_list_excludes_and_blocks(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    (campaigns.campaign_root(cid) / "played.json").write_text(f'["{g}"]', encoding="utf-8")
    got = {x["id"]: x for x in playing.available_greetings(cid)}
    assert got[g]["available"] is False and "already played" in got[g]["reasons"]
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid, g)
```

Append to `backend/tests/test_routes.py`, next to `test_campaign_greeting_mark_played_conflicts` (~line 2359; `_world` and the campaign/scene POST idioms are used throughout that file):

```python
def test_start_from_marked_greeting_409(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Mara"})
    g = client.post(f"/api/worlds/{wid}/greetings",
                    json={"name": "Gala", "character": "mara", "version": "default",
                          "body": "Hi."}).json()["id"]
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    r = client.post(f"/api/campaigns/{cid}/greetings/{g}/mark", json={"status": "completed"})
    assert r.status_code == 200
    avail = client.get(f"/api/campaigns/{cid}/greetings/available").json()
    assert [x["available"] for x in avail if x["id"] == g] == [False]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.post(f"/api/campaigns/{cid}/scenes/{sid}/start-from-greeting",
                       json={"greeting": g}).status_code == 409
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py backend/tests/test_routes.py::test_start_from_marked_greeting_409 -q`
Expected: the three new playing-store tests and the route test FAIL on the availability/`PlayError` assertions (marked greetings currently come back `available: True`).

- [ ] **Step 3: Implement the self-exclusion**

In `backend/src/grimoire/store/playing.py`, `available_greetings`, replace the mark-attachment loop:

```python
    mark_of = {gid: "played" for gid in marks["played"]}
    mark_of.update({gid: "completed" for gid in marks["completed"]})
    for g in out:
        g["mark"] = mark_of.get(g["id"])
```

with:

```python
    mark_of = {gid: "played" for gid in marks["played"]}
    mark_of.update({gid: "completed" for gid in marks["completed"]})
    for g in out:
        g["mark"] = mark_of.get(g["id"])
        # a used greeting is not startable again; start_from_greeting trusts
        # this flag, so the replay block is enforced here, server-side
        if g["mark"] == "played":
            g["available"] = False
            g["reasons"].append("already played")
        elif g["mark"] == "completed":
            g["available"] = False
            g["reasons"].append("marked complete")
```

The pure `greetings.availability()` is untouched — plot gating still runs on the merged played∪completed set.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, including the whole pre-existing suite (no existing test asserts a marked greeting stays available — verified during planning).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/playing.py backend/tests/test_playing_store.py backend/tests/test_routes.py
git commit -m "feat(playing): played/completed greetings are unavailable for new scenes"
```

---

### Task 2: Mark played on success, not up front

**Files:**
- Modify: `backend/src/grimoire/store/playing.py:97-133` (`start_from_greeting`)
- Test: `backend/tests/test_playing_store.py`

**Interfaces:**
- Consumes: Task 1's self-exclusion (a leftover `played` mark would now permanently block the greeting — this task removes the way such a leftover could occur).
- Produces: `start_from_greeting` records the `played` mark only after every other mutation (stamp, macro expansion, opener append, rename) has succeeded. Signature unchanged.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_playing_store.py`:

```python
def test_failed_start_leaves_greeting_startable(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "Alpha", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)

    def boom(cid, sid, title):
        raise RuntimeError("rename failed")

    monkeypatch.setattr(scenes, "rename_scene", boom)
    with pytest.raises(RuntimeError):
        playing.start_from_greeting(cid, sid, g)
    monkeypatch.undo()
    # the failed start consumed nothing: unmarked, still available, still startable
    assert g not in playing.read_played(cid)
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is True
    fresh = scenes.create_scene(cid, "S2")
    playing.start_from_greeting(cid, fresh, g)
    assert g in playing.read_played(cid)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py::test_failed_start_leaves_greeting_startable -q`
Expected: FAIL — `g in playing.read_played(cid)` after the raise (the mark is currently written before the rename), so the second start raises `PlayError`.

- [ ] **Step 3: Move the mark to the end of `start_from_greeting`**

In `backend/src/grimoire/store/playing.py`, delete the `_mark_played(cid, gid)` line that precedes `scenes.stamp_greeting(...)`, and change the function's tail from:

```python
    _mark_played(cid, gid)
    scenes.stamp_greeting(cid, sid, gid)
    text = context.expand_macros(overlay.read_greeting(cid, gid)["body"],
                                 context.scene_substitutions(cid, sid), cid, sid)
    scenes.append_message(cid, sid, "assistant", text)
    # retitle last: any earlier failure leaves the caller's sid valid for cleanup
    return scenes.rename_scene(cid, sid, g["name"])
```

to:

```python
    scenes.stamp_greeting(cid, sid, gid)
    text = context.expand_macros(overlay.read_greeting(cid, gid)["body"],
                                 context.scene_substitutions(cid, sid), cid, sid)
    scenes.append_message(cid, sid, "assistant", text)
    # retitle late: any earlier failure leaves the caller's sid valid for cleanup.
    # The played mark is written last of all — a failed start must not consume
    # the greeting (the mark is unclearable and now blocks availability).
    new_sid = scenes.rename_scene(cid, sid, g["name"])
    _mark_played(cid, gid)
    return new_sid
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (the mark is keyed by gid, not sid, so marking after the rename is safe; existing start tests assert the mark only after a successful start).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/playing.py backend/tests/test_playing_store.py
git commit -m "fix(playing): record the played mark only after the start fully succeeds"
```

---

### Task 3: Purge marks when a greeting is deleted

**Files:**
- Modify: `backend/src/grimoire/store/playing.py` (new `forget_greeting`)
- Modify: `backend/src/grimoire/store/overlay.py:218-231` (`delete_greeting`)
- Test: `backend/tests/test_playing_store.py`

**Interfaces:**
- Consumes: Task 1's self-exclusion (a stale mark on a reused slug would otherwise permanently block the recreated greeting).
- Produces: `playing.forget_greeting(cid: str, gid: str) -> None` — drops `gid` from all three mark sets, no-op when unmarked. Called by `overlay.delete_greeting` on every successful delete.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_playing_store.py`:

```python
def test_delete_greeting_purges_marks_and_frees_the_slug(monkeypatch, tmp_path):
    from grimoire.store import overlay
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    cid, sid = _campaign_after_seed(wid)
    g = overlay.create_greeting(cid, "Gala", "s", "default", body="Hi.")   # campaign-local
    playing.start_from_greeting(cid, sid, g)
    overlay.delete_greeting(cid, g)
    marks = playing.read_marks(cid)
    assert all(g not in s for s in marks.values())
    # same name -> same slug (no world file, no tombstone); it must start fresh
    g2 = overlay.create_greeting(cid, "Gala", "s", "default", body="Hi again.")
    assert g2 == g
    got = {x["id"]: x for x in playing.available_greetings(cid)}
    assert got[g2]["available"] is True and got[g2]["mark"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_playing_store.py::test_delete_greeting_purges_marks_and_frees_the_slug -q`
Expected: FAIL — the recreated greeting inherits the old `played` mark (`available` is `False` / `mark` is `"played"`).

- [ ] **Step 3: Implement `forget_greeting` and hook it into the delete**

In `backend/src/grimoire/store/playing.py`, after `mark_greeting`:

```python
def forget_greeting(cid: str, gid: str) -> None:
    """Drop every mark for gid. A deleted greeting must not leave a mark
    behind: `played` is unclearable, and (campaign-local ids have no
    tombstone) a recreated same-slug greeting would inherit it and be
    permanently unavailable. Safe for successor unlocking — the delete
    already removed the greeting's plotmap edges."""
    marks = read_marks(cid)
    if not any(gid in s for s in marks.values()):
        return
    for s in marks.values():
        s.discard(gid)
    _write_marks(cid, marks)
```

In `backend/src/grimoire/store/overlay.py`, at the end of `delete_greeting` (after the `if in_world: add_deleted(cid, ref)` line):

```python
    from . import playing  # lazy: playing imports overlay; keep the import graph flat
    playing.forget_greeting(cid, gid)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (inherited/world greeting deletes also purge — harmless there, the tombstone already blocks slug reuse).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/playing.py backend/src/grimoire/store/overlay.py backend/tests/test_playing_store.py
git commit -m "fix(overlay): deleting a greeting purges its campaign marks"
```

---

### Task 4: Frontend — completed-mark hint copy and contract tests

**Files:**
- Modify: `frontend/src/components/GreetingEditor.tsx:235`
- Test: `frontend/src/components/GreetingEditor.test.tsx`
- Test: `frontend/src/components/NewSceneChooser.test.tsx:160-167`

**Interfaces:**
- Consumes: Task 1's server contract (marked greetings arrive `available: false`). No component logic changes — `NewSceneChooser` already filters on `available`.
- Produces: user-visible copy only.

- [ ] **Step 1: Update the two tests**

In `frontend/src/components/NewSceneChooser.test.tsx`, replace the test at line 160 (`"renders exactly the server-filtered greeting list (skipped absent, marks tolerated)"`) with:

```tsx
test("marked greetings arrive unavailable and are not offered", async () => {
  (api.availableGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", available: false, reasons: ["marked complete"],
      unlocked: false, mark: "completed" },
  ]);
  await renderChooser();
  await screen.findByText("No available greetings.");
  expect(screen.queryByText("Gala")).toBeNull();
});
```

In `frontend/src/components/GreetingEditor.test.tsx`, next to `"campaign scope: played greetings show a disabled status control"` (~line 349), add (reusing that test's mock shape with `mark: "completed"`):

```tsx
test("campaign scope: completed hint says it is withheld from new scenes", async () => {
  (api.listGreetings as any).mockResolvedValue([
    { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
      requires_tags: [], predecessor_join: "all", mark: "completed" },
  ]);
  (api.readGreeting as any).mockResolvedValue({
    meta: { id: "g1", name: "Gala", character: "seraphine", version: "default", present: [],
            requires_tags: [], predecessor_join: "all" },
    body: "Hi.", edges: { leads_to: [], excludes: [] }, predecessors: [],
  });
  const { container } = render(<GreetingEditor scope={{ kind: "campaign", id: "run" }} wid="w" />);
  const rail = await waitFor(() => container.querySelector(".editor-list") as HTMLElement);
  fireEvent.click(await within(rail).findByText("Gala"));
  await screen.findByText("Marked complete: successors are unlocked; it won't be offered for new scenes.");
});
```

- [ ] **Step 2: Run the tests to verify the new one fails**

From `frontend/`, run: `npx vitest run src/components/GreetingEditor.test.tsx src/components/NewSceneChooser.test.tsx`
Expected: the GreetingEditor hint test FAILS (old copy: "Marked complete: successors are unlocked."). The rewritten NewSceneChooser test passes already (the component filters on `available`) — it pins the new server contract.

- [ ] **Step 3: Update the hint copy**

In `frontend/src/components/GreetingEditor.tsx` line 235, change:

```tsx
                      : mark === "completed" ? "Marked complete: successors are unlocked."
```

to:

```tsx
                      : mark === "completed" ? "Marked complete: successors are unlocked; it won't be offered for new scenes."
```

- [ ] **Step 4: Run the frontend suite and typecheck**

From `frontend/`, run: `npx vitest run` then `npx tsc -b`
Expected: all PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/GreetingEditor.tsx frontend/src/components/GreetingEditor.test.tsx frontend/src/components/NewSceneChooser.test.tsx
git commit -m "feat(greetings): completed-mark hint notes exclusion from new scenes"
```

---

### Final verification (after all tasks)

- [ ] Full backend suite: `backend/.venv/Scripts/python.exe -m pytest backend -q` — PASS
- [ ] Full frontend suite from `frontend/`: `npx vitest run` and `npx tsc -b` — PASS
- [ ] Run the Codex implementation gates per CLAUDE.md: `/codex:review` against the diff, then a final `/codex:adversarial-review` against the diff **and** the spec (does the diff implement the spec?)
