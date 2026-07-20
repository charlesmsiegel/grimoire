# Collapsible campaign layout + editable active cast Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every part of the campaign page's chrome collapsible (sidebar sections, scene rail, inspector sidebar, topbar, campaign subheader) with persisted state, and let the "Active characters" sidebar section add/remove cast members at any point in a scene.

**Architecture:** Backend gains one new store function (`appearances.leave()`) plus narration parity in `appear()`, and one new `DELETE` route. Everything else is frontend-only: a small local `SideSection` wrapper component in `SceneInspector.tsx` drives per-section collapse, two booleans in `CampaignView.tsx` drive rail/inspector panel collapse via CSS classes (never inline styles — see Task 5), and a small always-visible "chrome bar" plus route-scoped state in `App.tsx` drives topbar/subheader collapse. All collapse state persists via plain `localStorage` calls (no wrapper hook — five call sites, not worth abstracting).

**Tech Stack:** FastAPI + pytest (backend), React/Vite + vitest + Testing Library (frontend), plain CSS (no CSS-in-JS).

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- Run frontend tests from `frontend/`: `npx vitest run` (not `npx --prefix frontend vitest run` — that skips `frontend/vitest.config.ts` and disables `globals`, failing every mock-based test).
- Run frontend typecheck from `frontend/`: `npx tsc -b`.
- Placeholder names in any fixtures/examples follow the repo convention (Seraphine, Mara, Winifred, Realm, Saltmarch) — never real content.
- Collapse state is UI chrome, not campaign data: it lives in `localStorage`, never in the `~/.grimoire` store.
- Spec: `docs/superpowers/specs/2026-07-20-campaign-sidebar-collapsible-design.md` — read it for the full rationale (including two rounds of Codex adversarial-review findings and why locking was deliberately not added for cast mutations).
- Several tasks edit `CampaignView.tsx`/`SceneInspector.tsx` sequentially, so a later task's cited `path:line-range` will have drifted from earlier tasks' insertions by the time it's implemented. Line numbers are approximate anchors from the pre-implementation snapshot; the shown code blocks (and the surrounding-content descriptions, e.g. "the `<div className="subheader">...</div>` block") are the authoritative way to locate each edit — search the file's current state for that content rather than trusting the literal line number.

---

## Task 1: Backend — `appearances.leave()` + narration parity in `appear()`

**Files:**
- Modify: `backend/src/grimoire/store/appearances.py:178-195` (the `appear()` function)
- Modify: `backend/src/grimoire/store/appearances.py` (add `leave()` after `appear()`, i.e. after line 195)
- Test: `backend/tests/test_appearances_store.py` (add tests after the existing `test_character_appears_locks_version_and_role`/`test_second_scene_appends_only` tests, i.e. after line 48)

**Interfaces:**
- Consumes: `campaigns.campaign_root`, `_actor_name`, `_ref`, `record`, `_write` — all already defined in `appearances.py`. `scenes.read_scene(cid, sid)["messages"]` and `scenes.append_message(cid, sid, role, content)` from `store/scenes.py`, imported lazily inside the function bodies (matching the existing lazy `from . import scenes` pattern already used in `appearances.suggestions()`).
- Produces: `appearances.leave(cid: str, scene_id: str, kind: str, actor_id: str) -> None`, used by Task 2's route. `appear()`'s public signature is unchanged; only its body gains narration.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_appearances_store.py`, directly after `test_second_scene_appends_only` (after line 48):

```python
def test_leave_removes_scene_but_keeps_appearance_record(monkeypatch, tmp_path):
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    ap.appear(cid, "s2", "characters", "seraphine", "corrupted", "npc")
    ap.leave(cid, "s1", "characters", "seraphine")
    assert ap.record(cid)["characters/seraphine"]["scenes"] == ["s2"]
    assert ap.scene_cast(cid, "s1") == []
    assert ap.scene_cast(cid, "s2") == [
        {"kind": "characters", "id": "seraphine", "role": "npc", "name": "Seraphine"}]


def test_leave_on_actor_not_cast_is_a_silent_no_op(monkeypatch, tmp_path):
    """Idempotency: a retried DELETE (lost response, double-click) must not fail
    just because the first attempt already landed."""
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    ap.leave(cid, "s1", "characters", "seraphine")  # never cast at all
    assert ap.record(cid) == {}
    ap.appear(cid, "s1", "characters", "seraphine", "corrupted", "npc")
    ap.leave(cid, "s1", "characters", "seraphine")
    ap.leave(cid, "s1", "characters", "seraphine")  # repeat call: still a no-op
    assert ap.record(cid)["characters/seraphine"]["scenes"] == []


def test_leave_narrates_once_scene_has_messages(monkeypatch, tmp_path):
    from grimoire.store import scenes
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")
    ap.leave(cid, sid, "characters", "seraphine")
    assert scenes.read_scene(cid, sid)["messages"] == []  # still empty: silent

    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")
    scenes.append_message(cid, sid, "user", "hi")
    ap.leave(cid, sid, "characters", "seraphine")
    assert scenes.read_scene(cid, sid)["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "*Seraphine leaves the scene.*"},
    ]


def test_appear_narrates_join_once_scene_has_messages(monkeypatch, tmp_path):
    from grimoire.store import scenes
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    # fresh lock, empty scene: silent (matches CastPanel's pre-scene setup today)
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")
    assert scenes.read_scene(cid, sid)["messages"] == []

    scenes.append_message(cid, sid, "user", "hi")
    sid2 = scenes.create_scene(cid, "S2")
    # already-locked actor rejoining a *different*, non-empty scene: narrates
    ap.appear(cid, sid2, "characters", "seraphine", "corrupted", "npc")
    assert scenes.read_scene(cid, sid)["messages"] == [{"role": "user", "content": "hi"}]  # untouched
    scenes.append_message(cid, sid2, "user", "hi")
    ap.leave(cid, sid2, "characters", "seraphine")
    ap.appear(cid, sid2, "characters", "seraphine", "corrupted", "npc")
    assert scenes.read_scene(cid, sid2)["messages"][-1] == \
        {"role": "assistant", "content": "*Seraphine joins the scene.*"}


def test_appear_rejoin_same_scene_is_a_noop_no_duplicate_narration(monkeypatch, tmp_path):
    from grimoire.store import scenes
    _wid, cid = _world_with_char(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S")
    scenes.append_message(cid, sid, "user", "hi")
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")
    before = scenes.read_scene(cid, sid)["messages"]
    ap.appear(cid, sid, "characters", "seraphine", "corrupted", "npc")  # already in this scene
    assert scenes.read_scene(cid, sid)["messages"] == before  # no second join line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_appearances_store.py -k "leave or narrat" -v`
Expected: FAIL — `AttributeError: module 'grimoire.store.appearances' has no attribute 'leave'` (and the `appear()` tests fail on missing narration lines).

- [ ] **Step 3: Implement `leave()` and add narration to `appear()`**

Replace `appear()` in `backend/src/grimoire/store/appearances.py:178-195`:

```python
def appear(cid: str, scene_id: str, kind: str, actor_id: str, version_id: str, role: str) -> None:
    data = record(cid)
    ref = _ref(kind, actor_id)
    rec = data.get(ref)
    if rec is not None:
        if rec["version"] != version_id:
            raise AppearError(f"{ref} is locked to version {rec['version']}, not {version_id}")
        if rec["role"] != role:
            raise AppearError(f"{ref} is locked to role {rec['role']}, not {role}")
        if scene_id not in rec["scenes"]:
            rec["scenes"].append(scene_id)
            _write(cid, data)
        else:
            return  # already in this scene: no-op, no narration
    else:
        base = _lock(cid, kind, actor_id, version_id)  # lazy pick: first appearance locks
        data[ref] = {"version": version_id, "base": base, "scenes": [scene_id], "role": role}
        _write(cid, data)
        campaigns.touch(cid)

    from . import scenes  # lazy: scenes <-> appearances is already a lazy pair
    if scenes.read_scene(cid, scene_id)["messages"]:
        name = _actor_name(campaigns.campaign_root(cid), kind, actor_id, version_id) or actor_id
        scenes.append_message(cid, scene_id, "assistant", f"*{name} joins the scene.*")


def leave(cid: str, scene_id: str, kind: str, actor_id: str) -> None:
    """Drop `scene_id` from the actor's appearance record. The actor stays
    appeared campaign-wide (other scenes, roster) -- only this scene's cast
    loses them. Narrates a transition line once the scene already has
    messages; silent while the scene is still in pre-first-message setup,
    matching appear()'s silent-first-add.

    Idempotent: an actor already absent from this scene's cast (never cast,
    or a repeat call after a lost response / retry) is a silent no-op, not
    an error -- a retried DELETE must not fail just because the first
    attempt already landed."""
    data = record(cid)
    ref = _ref(kind, actor_id)
    rec = data.get(ref)
    if rec is None or scene_id not in rec.get("scenes", []):
        return
    from . import scenes  # lazy: scenes <-> appearances is already a lazy pair
    name = _actor_name(campaigns.campaign_root(cid), kind, actor_id, rec["version"]) or actor_id
    rec["scenes"].remove(scene_id)
    _write(cid, data)
    if scenes.read_scene(cid, scene_id)["messages"]:
        scenes.append_message(cid, scene_id, "assistant", f"*{name} leaves the scene.*")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_appearances_store.py -v`
Expected: PASS (all tests in the file, including the pre-existing ones — `appear()`'s behavior for scenes with no messages is unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/appearances.py backend/tests/test_appearances_store.py
git commit -m "feat(backend): add appearances.leave() with join/leave transition narration"
```

---

## Task 2: Backend — `DELETE` cast route

**Files:**
- Modify: `backend/src/grimoire/routes.py` (add route after `post_scene_cast`, i.e. after line 2890)
- Test: `backend/tests/test_routes.py` (add tests after `test_cast_and_suggestions_flow`, i.e. after line 1180)

**Interfaces:**
- Consumes: `store.appearances.leave` (Task 1), `store.appearances.ACTOR_KINDS`, `_require_scene` (already defined at `routes.py:2359`).
- Produces: `DELETE /api/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}` → `{"ok": true}`, consumed by Task 4's `api.removeFromCast`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_routes.py`, directly after `test_cast_and_suggestions_flow` (after line 1180):

```python
def test_delete_cast_removes_member_and_narrates_when_scene_has_messages(client):
    wid = _world(client)
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    cid = client.post("/api/campaigns", json={"name": "Run", "world": wid}).json()["id"]
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "Docks"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/scenes/{sid}/cast", json={"kind": "characters", "id": "seraphine"})
    store.scenes.append_message(cid, sid, "user", "hi")

    r = client.delete(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/seraphine")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/cast").json() == []
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}").json()["messages"][-1] == \
        {"role": "assistant", "content": "*Seraphine leaves the scene.*"}


def test_delete_cast_unknown_kind_404(client):
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.delete(f"/api/campaigns/{cid}/scenes/{sid}/cast/monsters/x").status_code == 404


def test_delete_cast_not_currently_cast_is_a_200_noop(client):
    """Idempotency: retrying the DELETE (or double-clicking remove) must not 404."""
    wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.delete(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/ghost").status_code == 200
    assert client.delete(f"/api/campaigns/{cid}/scenes/{sid}/cast/characters/ghost").status_code == 200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -k "delete_cast" -v`
Expected: FAIL with 404/405 ("Method Not Allowed") — the route doesn't exist yet.

- [ ] **Step 3: Implement the route**

Add to `backend/src/grimoire/routes.py`, directly after `post_scene_cast` (after line 2890, before the `AppearBatch` class at line 2893):

```python
@router.delete("/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}")
def delete_scene_cast(cid: str, sid: str, kind: str, id: str):
    _require_scene(cid, sid)
    if kind not in store.appearances.ACTOR_KINDS:
        raise HTTPException(status_code=404, detail="unknown actor kind")
    store.appearances.leave(cid, sid, kind, id)
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -v`
Expected: PASS (full file — confirms no route ordering/collision with the existing `GET .../cast/{kind}/{id}` cast-detail route).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(backend): add DELETE cast route for removing a scene's active characters"
```

---

## Task 3: Frontend — collapsible sidebar sections in `SceneInspector`

**Files:**
- Modify: `frontend/src/components/SceneInspector.tsx` (add `SideSection`, wrap all six sections)
- Modify: `frontend/src/index.css` (new `.side-section-head` rules, after line 680)
- Test: `frontend/src/components/SceneInspector.test.tsx` (add `localStorage.clear()` to `beforeEach`, add new tests)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: the `SideSection` local component (not exported — only used within this file). Task 4 extends the "Active characters" `SideSection` instance with add/remove controls; it must use the same `id="cast"` and the same `collapsed`/`toggleSection` state this task introduces.

- [ ] **Step 1: Write the failing tests**

Add to `frontend/src/components/SceneInspector.test.tsx`. First, add `localStorage.clear();` as the first line of the existing `beforeEach` (before `vi.clearAllMocks();`, at line 42) so section-collapse state from one test never leaks into the next. Then add these tests at the end of the file:

```tsx
test("clicking a section header collapses its body and toggles aria-expanded", async () => {
  renderInspector();
  await screen.findByText("They first met.");
  const header = screen.getByRole("button", { name: /story so far/i });
  expect(header).toHaveAttribute("aria-expanded", "true");
  fireEvent.click(header);
  expect(header).toHaveAttribute("aria-expanded", "false");
  expect(screen.queryByText("They first met.")).not.toBeInTheDocument();
  fireEvent.click(header);
  expect(header).toHaveAttribute("aria-expanded", "true");
  await screen.findByText("They first met.");
});

test("section collapse state persists across a remount", async () => {
  const { unmount } = render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("They first met.");
  fireEvent.click(screen.getByRole("button", { name: /story so far/i }));
  expect(screen.queryByText("They first met.")).not.toBeInTheDocument();
  expect(JSON.parse(localStorage.getItem("grimoire.inspector.sections")!)).toEqual({ story: true });
  unmount();

  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  await screen.findByText("Active characters"); // sanity: the inspector rendered
  expect(screen.queryByText("They first met.")).not.toBeInTheDocument(); // stayed collapsed
});

test("the Context section header still shows the percentage badge and collapses as a whole", async () => {
  renderInspector();
  await screen.findByText(/World info/);
  const header = screen.getByRole("button", { name: /context/i });
  fireEvent.click(header);
  expect(screen.queryByText(/World info/)).not.toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx`
Expected: FAIL — no element has `role="button"` with an accessible name matching `/story so far/i` (headers are plain `<h4>` today, not buttons).

- [ ] **Step 3: Implement `SideSection` and wrap every section**

In `frontend/src/components/SceneInspector.tsx`, change the import on line 1 to add `type ReactNode`:

```tsx
import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
```

Add the `SideSection` component directly above `export function SceneInspector` (before line 11):

```tsx
const SECTIONS_KEY = "grimoire.inspector.sections";

function loadSectionCollapse(): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(SECTIONS_KEY) ?? "{}");
  } catch {
    return {};
  }
}

function SideSection({ id, title, collapsed, onToggle, extra, children }: {
  id: string; title: string; collapsed: boolean; onToggle: (id: string) => void;
  extra?: ReactNode; children: ReactNode;
}) {
  return (
    <div className="side-section">
      <button className="side-section-head" aria-expanded={!collapsed} onClick={() => onToggle(id)}>
        <h4>{title}</h4>
        <span className="side-section-head-right">
          {extra}
          <span className="side-section-chev">{collapsed ? "▸" : "▾"}</span>
        </span>
      </button>
      {!collapsed && <div className="side-section-body">{children}</div>}
    </div>
  );
}
```

Inside `SceneInspector`, add state right after the existing `const [error, setError] = useState<string | null>(null);` (line 32):

```tsx
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>(loadSectionCollapse);
  const toggleSection = useCallback((id: string) => {
    setCollapsed((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      localStorage.setItem(SECTIONS_KEY, JSON.stringify(next));
      return next;
    });
  }, []);
```

Replace the whole `return (...)` block (lines 146-288) with:

```tsx
  return (
    <aside className="inspector">
      {pcless && (
        <SideSection id="offscreen" title="Offscreen scene" collapsed={!!collapsed.offscreen} onToggle={toggleSection}>
          <div className="field-hint">No player character — you direct the NPCs.</div>
        </SideSection>
      )}
      {recap.length > 0 && (
        <SideSection id="story" title="Story so far" collapsed={!!collapsed.story} onToggle={toggleSection}>
          {[...recap].reverse().map((r) => (
            <div className="field-hint" key={r.id}>{r.one_line || r.summary}</div>
          ))}
        </SideSection>
      )}
      <SideSection id="cast" title="Active characters" collapsed={!!collapsed.cast} onToggle={toggleSection}>
        {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
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
      </SideSection>

      <SideSection id="location" title="Location" collapsed={!!collapsed.location} onToggle={toggleSection}>
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
        {locations.length > 0 && (
          <div className="picker">
            <select aria-label="Move to location" value={locPick}
                    onChange={(e) => setLocPick(e.target.value)}>
              <option value="">Move to…</option>
              {locations
                .filter((l) => l.id !== setting?.current?.id)
                .map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            <button className="primary" onClick={moveTo} disabled={!locPick}>Move to</button>
          </div>
        )}
      </SideSection>

      <SideSection id="style" title="Prose style" collapsed={!!collapsed.style} onToggle={toggleSection}>
        <select aria-label="Prose style" value={styleId} onChange={(e) => chooseStyle(e.target.value)}>
          <option value="">— use campaign default —</option>
          {styleOptions.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
        </select>
      </SideSection>

      <SideSection id="when" title="When" collapsed={!!collapsed.when} onToggle={toggleSection}>
        {error && <div className="banner">{error}</div>}
        {when?.current ? (
          <>
            <div className="field-hint">{when.current.friendly} ({when.current.weekday})</div>
            {when.current.holidays_today.length > 0 && (
              <div className="field-hint">Holidays: {when.current.holidays_today.join(", ")}</div>
            )}
            <div className="picker">
              <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                                  onChange={setDateInput} ariaLabel="Scene date" />
              <button className="primary" onClick={applyDatetime} disabled={!dateInput}>Advance to</button>
            </div>
          </>
        ) : cfg && !cfg.confirmed ? (
          <>
            <div className="field-hint">Select a calendar to track dates.</div>
            <div className="picker">
              <select aria-label="Calendar" value={provider} onChange={(e) => setProvider(e.target.value)}>
                {calendars.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
              <button className="primary" onClick={chooseCalendar}>Use this calendar</button>
            </div>
          </>
        ) : (
          <>
            <div className="field-hint">No date</div>
            <div className="picker">
              <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={dateInput}
                                  onChange={setDateInput} ariaLabel="Scene date" />
              <button className="primary" onClick={applyDatetime} disabled={!dateInput}>Set date</button>
            </div>
          </>
        )}
      </SideSection>

      <SideSection id="context" title="Context" collapsed={!!collapsed.context} onToggle={toggleSection}
                   extra={ctx && ctxLen > 0 ? <span className="ctx-pct">{pctNumber(ctx.total_tokens)}%</span> : undefined}>
        {ctx && (
          <>
            <div className="ctx-bar">
              <div className="ctx-bar-fill" style={{ width: `${Math.min(100, pctNumber(ctx.total_tokens))}%` }} />
            </div>
            <div className="ctx-tokens">
              {ctx.total_tokens.toLocaleString()}{ctxLen > 0 ? ` / ${ctxLen.toLocaleString()}` : ""} tok
            </div>
            <div className="ctx-caption">Breakdown · click a row to inspect</div>
          </>
        )}
        {ctx?.sections.map((s) => (
          <details className="ctx-section" key={s.label}>
            <summary>
              <span className={"ctx-dot" + (s.label.toLowerCase().includes("transcript") ? " hot" : "")} />
              <span className="ctx-label">{s.label}</span>
              <span className="ctx-meta">{s.tokens.toLocaleString()}{pct(s.tokens)}</span>
            </summary>
            <div className="ctx-mini">
              <div style={{ width: `${Math.min(100, pctNumber(s.tokens))}%` }} />
            </div>
            <pre className="ctx-text">{s.text}</pre>
          </details>
        ))}
      </SideSection>

      {drawer && <RecordDrawer cid={cid} sid={sid} target={drawer} onClose={() => setDrawer(null)} />}
    </aside>
  );
}
```

Note this removes the old `<div className="ctx-head">` wrapper — `SideSection`'s built-in head now carries both the title and the percentage badge via the `extra` prop.

Add to `frontend/src/index.css`, directly after `.inspector .side-section:last-of-type { border-bottom: none; }` (after line 680):

```css
.inspector .side-section-head {
  display: flex; align-items: center; justify-content: space-between; width: 100%;
  background: none; border: none; padding: 0; margin: 0 0 6px; cursor: pointer; text-align: left;
}
.inspector .side-section-head h4 { margin: 0; }
.inspector .side-section-head-right { display: flex; align-items: center; gap: 6px; }
.inspector .side-section-chev { font-family: var(--fm); font-size: 11px; color: var(--muted); }
.inspector .side-section-head:hover .side-section-chev { color: var(--accent); }
```

Remove the now-dead `.ctx-head` rule (it was `display: flex; justify-content: space-between; align-items: baseline;` — superseded by `.side-section-head`/`.side-section-head-right`, and nothing references `ctx-head` in JSX anymore after this task's markup change):

```css
.ctx-head { display: flex; justify-content: space-between; align-items: baseline; }
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx`
Expected: PASS — all pre-existing tests (e.g. "shows the story-so-far recap", "context section expands to show the text") still pass because every section starts expanded by default (`collapsed.<id>` is `undefined` → falsy when `localStorage` is empty), plus the three new tests pass.

Also run: `npx tsc -b` (from `frontend/`) — confirm no type errors from the `SideSection` props or the `ReactNode` import.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SceneInspector.tsx frontend/src/components/SceneInspector.test.tsx frontend/src/index.css
git commit -m "feat(frontend): make every SceneInspector sidebar section collapsible"
```

---

## Task 4: Frontend — Active characters add/remove

**Files:**
- Modify: `frontend/src/api/client.ts` (add `removeFromCast`, after `addToCast` at line 659)
- Modify: `frontend/src/components/SceneInspector.tsx` (add/remove UI inside the `cast` `SideSection` from Task 3)
- Modify: `frontend/src/index.css` (row-remove button styles)
- Test: `frontend/src/components/SceneInspector.test.tsx` (new tests + extend the `api` mock)

**Interfaces:**
- Consumes: `SideSection`, `collapsed`, `toggleSection` from Task 3 (same file). `DELETE /api/campaigns/{cid}/scenes/{sid}/cast/{kind}/{id}` from Task 2.
- Produces: `api.removeFromCast(cid: string, sid: string, kind: string, id: string): Promise<{ ok: boolean }>`, callable from anywhere else that imports `api`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/components/SceneInspector.test.tsx`, extend the `api` mock object (inside `vi.mock("../api/client", ...)`, in the object at lines 8-19) to add two entries — `addToCast: vi.fn(), removeFromCast: vi.fn(),` — and add their default resolutions in `beforeEach` (after the existing `(api.getCast as any).mockResolvedValue(...)` at line 43):

```tsx
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.removeFromCast as any).mockResolvedValue({ ok: true });
```

Then add these tests at the end of the file:

```tsx
test("removing a cast member calls removeFromCast, reloads cast, and notifies the scene changed", async () => {
  const onSceneChanged = vi.fn();
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={onSceneChanged} />);
  await screen.findByText("Seraphine");
  fireEvent.click(screen.getByRole("button", { name: /remove seraphine from scene/i }));
  await waitFor(() => expect(api.removeFromCast).toHaveBeenCalledWith("c", "s", "characters", "seraphine"));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
  expect(api.getCast).toHaveBeenCalledTimes(2); // initial load + reload after remove
});

test("adding a character posts kind + id + role, reloads cast, and notifies the scene changed", async () => {
  (api.listCharacters as any).mockResolvedValue([
    { id: "seraphine", name: "Seraphine", default_version: "default", versions: [] },
    { id: "mara", name: "Mara", default_version: "default", versions: [] },
  ]);
  const onSceneChanged = vi.fn();
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={onSceneChanged} />);
  await screen.findByRole("option", { name: "Mara" });
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "mara" } });
  fireEvent.change(screen.getByLabelText("Role for new cast member"), { target: { value: "player" } });
  fireEvent.click(screen.getByRole("button", { name: /\+ add/i }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "characters", id: "mara", role: "player" }));
  await waitFor(() => expect(onSceneChanged).toHaveBeenCalled());
});

test("adding a PC omits the role picker and forces role=player", async () => {
  (api.listCampaignPCs as any).mockResolvedValue([
    { id: "elara", name: "Elara", tags: [], default_version: "default", versions: [] }]);
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} />);
  fireEvent.change(await screen.findByLabelText("Cast kind to add"), { target: { value: "pcs" } });
  await screen.findByRole("option", { name: "Elara" });
  expect(screen.queryByLabelText("Role for new cast member")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "elara" } });
  fireEvent.click(screen.getByRole("button", { name: /\+ add/i }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "pcs", id: "elara", role: "player" }));
});

test("offscreen scene hides the kind and role pickers, forcing npc characters only", async () => {
  render(<SceneInspector cid="c" sid="s" refreshKey={0} onSceneChanged={() => {}} pcless />);
  await screen.findByLabelText("Character or PC to add");
  expect(screen.queryByLabelText("Cast kind to add")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Role for new cast member")).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Character or PC to add"), { target: { value: "seraphine" } });
  fireEvent.click(screen.getByRole("button", { name: /\+ add/i }));
  await waitFor(() => expect(api.addToCast).toHaveBeenCalledWith(
    "c", "s", { kind: "characters", id: "seraphine", role: "npc" }));
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx`
Expected: FAIL — `api.removeFromCast` doesn't exist on the client yet, and there's no remove button or add form in the "Active characters" section.

- [ ] **Step 3: Implement**

In `frontend/src/api/client.ts`, add directly after `addToCast` (after line 659):

```ts
  removeFromCast: (cid: string, sid: string, kind: string, id: string) =>
    request<{ ok: boolean }>("DELETE", `/api/campaigns/${cid}/scenes/${sid}/cast/${kind}/${id}`),
```

In `frontend/src/components/SceneInspector.tsx`, add `CharacterSummary` and `PCSummary` to the existing `api/client` import (line 2-5):

```tsx
import {
  api, type Actor, type SceneContext, type SceneLocation, type ChronicleEntry,
  type CalendarConfig, type RosterEntry, type SceneDatetime, type Style,
  type CharacterSummary, type PCSummary,
} from "../api/client";
```

Add state and a `reloadCast` callback. Replace the current inline cast fetch inside the main `useEffect` (line 65: `api.getCast(cid, sid).then(setCast).catch(() => setCast([]));`) — first add, right after the `reloadStyle` callback (after line 62):

```tsx
  const reloadCast = useCallback(
    () => api.getCast(cid, sid).then(setCast).catch(() => setCast([])),
    [cid, sid]);
```

Then in the `useEffect` at lines 64-73, replace the direct `api.getCast(...)` call with `reloadCast();` and add `reloadCast` to the dependency array:

```tsx
  useEffect(() => {
    reloadCast();
    api.listAppearances(cid).then(setRoster).catch(() => setRoster([]));
    api.getSceneLocation(cid, sid).then(setSetting).catch(() => setSetting(null));
    api.getSceneContext(cid, sid).then(setCtx).catch(() => setCtx(null));
    api.getChronicle(cid).then(setRecap).catch(() => setRecap([]));
    reloadWhen();
    reloadCfg();
    reloadStyle();
  }, [cid, sid, refreshKey, reloadWhen, reloadCfg, reloadStyle, reloadCast]);
```

Add the actor-picker state and options lists, plus a new effect to fetch them, right after the existing `useEffect` at lines 34-48 (the one fetching `names`/`models`/`locations`/`calendars`/`styleOptions`):

```tsx
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [pcs, setPcs] = useState<PCSummary[]>([]);
  const [addKind, setAddKind] = useState<"characters" | "pcs">("characters");
  const [addActorId, setAddActorId] = useState("");
  const [addRole, setAddRole] = useState<"player" | "npc">("npc");

  useEffect(() => {
    api.listCharacters({ kind: "campaign", id: cid }).then(setChars).catch(() => setChars([]));
    api.listCampaignPCs(cid).then(setPcs).catch(() => setPcs([]));
  }, [cid]);

  const addOptions = addKind === "characters" ? chars : pcs;

  async function addCastMember() {
    if (!addActorId) return;
    await api.addToCast(cid, sid, {
      kind: addKind, id: addActorId,
      role: pcless ? "npc" : addKind === "pcs" ? "player" : addRole,
    });
    setAddActorId("");
    await reloadCast();
    onSceneChanged();
  }

  async function removeCastMember(a: Actor) {
    await api.removeFromCast(cid, sid, a.kind, a.id);
    await reloadCast();
    onSceneChanged();
  }
```

Replace the `cast` `SideSection` body from Task 3 (the `<SideSection id="cast" ...>` block) with:

```tsx
      <SideSection id="cast" title="Active characters" collapsed={!!collapsed.cast} onToggle={toggleSection}>
        {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
        {cast.map((a) => {
          const ver = a.kind === "characters"
            ? roster.find((r) => r.kind === "characters" && r.id === a.id)?.version
            : undefined;
          const pc = a.role === "player";
          return (
            <div className="inspector-row-item" key={`${a.kind}/${a.id}`}>
              <button className={"inspector-row" + (pc ? " pc" : "")}
                      onClick={() => setDrawer({ type: "actor", kind: a.kind, id: a.id })}>
                <Portrait src={ver ? api.campaignImageUrl(cid, a.id, ver, "avatar") : null}
                          name={nameOf(a)} />
                <span className="inspector-name">{nameOf(a)}</span>
                <span className="role-chip">{pc ? "player" : "npc"}</span>
              </button>
              <button className="inspector-row-remove" aria-label={`Remove ${nameOf(a)} from scene`}
                      onClick={() => removeCastMember(a)}>✕</button>
            </div>
          );
        })}
        <div className="picker">
          {!pcless && (
            <select aria-label="Cast kind to add" value={addKind}
                    onChange={(e) => { setAddKind(e.target.value as "characters" | "pcs"); setAddActorId(""); }}>
              <option value="characters">Character</option>
              <option value="pcs">PC</option>
            </select>
          )}
          <select aria-label="Character or PC to add" value={addActorId}
                  onChange={(e) => setAddActorId(e.target.value)}>
            <option value="">— pick —</option>
            {addOptions.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
          </select>
          {addKind === "characters" && !pcless && (
            <select aria-label="Role for new cast member" value={addRole}
                    onChange={(e) => setAddRole(e.target.value as "player" | "npc")}>
              <option value="npc">npc</option>
              <option value="player">player</option>
            </select>
          )}
          <button className="primary" onClick={addCastMember} disabled={!addActorId}>+ Add</button>
        </div>
      </SideSection>
```

Add to `frontend/src/index.css`, directly after the block added in Task 3 (`.inspector .side-section-head:hover .side-section-chev { color: var(--accent); }`):

```css
.inspector .inspector-row-item { display: flex; align-items: center; gap: 4px; }
.inspector .inspector-row-item .inspector-row { flex: 1; width: auto; }
.inspector-row-remove {
  flex: none; background: none; border: none; color: var(--muted); cursor: pointer;
  font-size: 13px; padding: 4px 6px; line-height: 1;
}
.inspector-row-remove:hover { color: var(--accent); }
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/components/SceneInspector.test.tsx`
Expected: PASS — all tests, including the pre-existing "clicking a cast row opens the drawer" test (the row button's `onClick` behavior is unchanged; it's just now a sibling of the new remove button instead of the section's only element).

Also run: `npx tsc -b` (from `frontend/`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/SceneInspector.tsx frontend/src/components/SceneInspector.test.tsx frontend/src/index.css
git commit -m "feat(frontend): add/remove active characters from the sidebar at any point in a scene"
```

---

## Task 5: Frontend — collapsible scene rail (left) and inspector (right) panels

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx` (rail/inspector collapse state + JSX restructuring)
- Modify: `frontend/src/index.css` (grid collapse classes, edge tabs, narrow-viewport media query)
- Test: `frontend/src/routes/CampaignView.test.tsx` (add `localStorage.clear()` to `beforeEach`, add `addToCast`/`removeFromCast` to the api mock, add new tests)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: nothing new consumed elsewhere — self-contained to `CampaignView.tsx`.

- [ ] **Step 1: Write the failing tests**

In `frontend/src/routes/CampaignView.test.tsx`, add `localStorage.clear();` as the first line of `beforeEach` (before `vi.clearAllMocks();`, at line 68). Also add `addToCast: vi.fn(), removeFromCast: vi.fn(),` to the `api` mock object (inside the object at lines 30-58, near the other cast-related entries at line 48), since the real (unmocked) `SceneInspector` now depends on them per Task 4, and default their resolutions in `beforeEach`:

```tsx
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.removeFromCast as any).mockResolvedValue({ ok: true });
```

Then add these tests at the end of the file:

```tsx
test("collapsing the scene rail hides it and shows an edge tab; clicking the tab restores it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Old");
  fireEvent.click(screen.getByRole("button", { name: /collapse scene list/i }));
  expect(screen.queryByText("Old")).not.toBeInTheDocument();
  const tab = screen.getByRole("button", { name: /expand scene list/i });
  fireEvent.click(tab);
  await screen.findByText("Old");
});

test("collapsing the inspector hides it and shows an edge tab; clicking the tab restores it", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  renderCampaign();
  await screen.findByText("Active characters");
  fireEvent.click(screen.getByRole("button", { name: /collapse sidebar/i }));
  expect(screen.queryByText("Active characters")).not.toBeInTheDocument();
  const tab = screen.getByRole("button", { name: /expand sidebar/i });
  fireEvent.click(tab);
  await screen.findByText("Active characters");
});

test("rail and inspector collapse state persist across a remount", async () => {
  (api.listScenes as any).mockResolvedValue(ONE_SCENE);
  const { unmount } = renderCampaign();
  await screen.findByText("Old");
  fireEvent.click(screen.getByRole("button", { name: /collapse scene list/i }));
  expect(localStorage.getItem("grimoire.rail.collapsed")).toBe("1");
  unmount();

  renderCampaign();
  await screen.findByText("Active characters"); // inspector still renders...
  expect(screen.queryByText("Old")).not.toBeInTheDocument(); // ...but the rail stayed collapsed
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx`
Expected: FAIL — no "Collapse scene list"/"Collapse sidebar" buttons exist yet.

- [ ] **Step 3: Implement**

In `frontend/src/routes/CampaignView.tsx`, add state right after the existing `const streamRef = useRef<HTMLDivElement>(null);` (line 114):

```tsx
  const [railCollapsed, setRailCollapsed] = useState(
    () => localStorage.getItem("grimoire.rail.collapsed") === "1");
  const [inspectorCollapsed, setInspectorCollapsed] = useState(
    () => localStorage.getItem("grimoire.inspector.collapsed") === "1");

  function toggleRail() {
    setRailCollapsed((v) => {
      localStorage.setItem("grimoire.rail.collapsed", v ? "0" : "1");
      return !v;
    });
  }
  function toggleInspector() {
    setInspectorCollapsed((v) => {
      localStorage.setItem("grimoire.inspector.collapsed", v ? "0" : "1");
      return !v;
    });
  }
```

Replace the `<div className="layout">` opening tag and the `<aside className="scene-rail">...</aside>` block (lines 504-546):

```tsx
      <div className={"layout" + (railCollapsed ? " rail-collapsed" : "") + (inspectorCollapsed ? " inspector-collapsed" : "")}>
      {railCollapsed ? (
        <button className="rail-tab" aria-label="Expand scene list" onClick={toggleRail}>›</button>
      ) : (
      <aside className="scene-rail">
        <button className="rail-collapse" aria-label="Collapse scene list" onClick={toggleRail}>‹</button>
        <div className="rail-counter">Scenes / {String(scenes.length).padStart(2, "0")}</div>
        <button className="btn-chrome rail-new" onClick={newScene}>+ New Scene</button>
        <select className="rail-sort" aria-label="Sort scenes by" value={sceneSort}
                onChange={(e) => setSceneSort(e.target.value as SceneSort)}>
          <option value="updated">Sort: Last updated</option>
          <option value="date">Sort: Scene date</option>
          <option value="order">Sort: Order</option>
        </select>
        <div className="rail-scenes">
          {sortScenes(scenes, sceneSort).map((s, i) => (
            <EditableRow
              key={s.id}
              label={s.title}
              prefix={String(sceneNumber(s.id, scenes.length - i)).padStart(2, "0")}
              subtitle={s.pcless ? "Offscreen" : undefined}
              active={s.id === activeId}
              onSelect={() => selectScene(s.id)}
              onRename={(title) => renameScene(s.id, title)}
              onDelete={() => deleteScene(s)}
            />
          ))}
        </div>
        <div className="rail-foot">
          <button className="btn-outline rail-world" onClick={() => navigate(`/campaigns/${cid}/world`)}>
            Campaign World ↗
          </button>
          {dt?.current && (
            <button className="rail-date" onClick={() => setShowCalendar((v) => !v)}
                    title="Calendar settings">
              {dt.current.weekday} {dt.current.friendly}
              {dt.current.holidays_today.length > 0 && (
                <span className="rail-holiday">✦ {dt.current.holidays_today[0]}</span>
              )}
            </button>
          )}
          <button className="rail-date" onClick={() => setShowStyle((v) => !v)}
                  title="Prose style settings">
            Prose style
          </button>
        </div>
      </aside>
      )}
```

Replace the `SceneInspector` render block (lines 935-939):

```tsx
      {inspectorCollapsed ? (
        <button className="inspector-tab" aria-label="Expand sidebar" onClick={toggleInspector}>‹</button>
      ) : (
        <div className="inspector-slot">
          <button className="inspector-collapse" aria-label="Collapse sidebar" onClick={toggleInspector}>›</button>
          {activeId && (
            <SceneInspector cid={cid} sid={activeId} refreshKey={ctxKey}
                            onSceneChanged={() => selectScene(activeId)}
                            onSceneRenamed={sceneRenamed} pcless={activePcless} />
          )}
        </div>
      )}
```

(The rest of `.layout`'s children — `.main`, `{drawer && ...}`, `{chooserOpen && ...}`, and the closing `</div>` — are unchanged.)

In `frontend/src/index.css`, replace the `.layout` rule (line 178):

```css
.layout { display: grid; grid-template-columns: 236px 1fr 286px; flex: 1; min-height: 0; }
.layout.rail-collapsed { grid-template-columns: 28px 1fr 286px; }
.layout.inspector-collapsed { grid-template-columns: 236px 1fr 28px; }
.layout.rail-collapsed.inspector-collapsed { grid-template-columns: 28px 1fr 28px; }
```

Add after the `.rail-holiday` rule (after line 191):

```css
.rail-tab, .inspector-tab {
  background: var(--panel); border: none; cursor: pointer; color: var(--muted);
  font-family: var(--fm); font-size: 14px; display: flex; align-items: center; justify-content: center;
}
.rail-tab { border-right: var(--rw) solid var(--rule); }
.inspector-tab { border-left: var(--rw) solid var(--rule); }
.rail-tab:hover, .inspector-tab:hover { color: var(--accent); }
.rail-collapse, .inspector-collapse {
  background: none; border: none; cursor: pointer; color: var(--muted); align-self: flex-end;
  font-family: var(--fm); font-size: 12px; padding: 2px 4px; margin-bottom: 6px;
}
.rail-collapse:hover, .inspector-collapse:hover { color: var(--accent); }
.inspector-slot { display: flex; flex-direction: column; min-height: 0; }
.inspector-slot .inspector { flex: 1; }
```

Replace the narrow-viewport media query (line 726) — **this must stay positioned after** the four `.layout...` rules above (same selector specificity per matched pair; the later declaration wins on a tie, which is how the plain `.layout` rule already overrode the base rule before this change):

```css
@media (max-width: 1100px) {
  .inspector, .inspector-tab { display: none; }
  .layout, .layout.inspector-collapsed { grid-template-columns: 236px 1fr; }
  .layout.rail-collapsed, .layout.rail-collapsed.inspector-collapsed { grid-template-columns: 28px 1fr; }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/routes/CampaignView.test.tsx`
Expected: PASS — full file, including all pre-existing tests (the rail and inspector's inner content is unchanged; only wrapped in new collapse scaffolding that defaults to expanded).

Also run: `npx tsc -b` (from `frontend/`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx frontend/src/index.css
git commit -m "feat(frontend): collapsible scene rail and inspector panels with persisted state"
```

---

## Task 6: Frontend — collapsible headers (topbar + campaign subheader)

**Files:**
- Modify: `frontend/src/App.tsx` (topbar collapse state, route-scoped application, prop pass-through)
- Modify: `frontend/src/routes/CampaignView.tsx` (accept new props, subheader collapse state, chrome-bar JSX)
- Modify: `frontend/src/index.css` (`.chrome-bar`, `.chrome-toggle`, `.topbar.collapsed`)
- Test: `frontend/src/App.test.tsx` (mock `CampaignView`, add `localStorage.clear()`, add new tests)
- Test: `frontend/src/routes/CampaignView.test.tsx` (add new test for the subheader toggle)

**Interfaces:**
- Consumes: nothing new from other tasks.
- Produces: `CampaignView`'s new optional props `topbarCollapsed?: boolean` and `onToggleTopbar?: () => void` (defaulted so every existing `<CampaignView ready={...} />` call site — including the one in `CampaignView.test.tsx:122` — keeps compiling and passing unchanged).

- [ ] **Step 1: Write the failing tests**

In `frontend/src/App.test.tsx`, add `localStorage.clear();` as the first line of the existing `beforeEach` (before `vi.clearAllMocks();`, at line 22). Add a mock for `CampaignView` directly after the existing `vi.mock("./api/client", ...)` block (after line 12):

```tsx
vi.mock("./routes/CampaignView", () => ({
  default: ({ topbarCollapsed, onToggleTopbar }: any) => (
    <div data-testid="campaign-view">
      <button onClick={onToggleTopbar}>toggle-topbar</button>
      <span>{topbarCollapsed ? "collapsed" : "expanded"}</span>
    </div>
  ),
}));
```

Then add these tests at the end of the file:

```tsx
test("the topbar collapses only while viewing a campaign, via CampaignView's own toggle", async () => {
  render(<MemoryRouter initialEntries={["/campaigns/run"]}><App /></MemoryRouter>);
  const view = await screen.findByTestId("campaign-view");
  expect(within(view).getByText("expanded")).toBeInTheDocument();
  expect(screen.getByRole("banner")).not.toHaveClass("collapsed");

  fireEvent.click(within(view).getByText("toggle-topbar"));
  expect(within(view).getByText("collapsed")).toBeInTheDocument();
  expect(screen.getByRole("banner")).toHaveClass("collapsed");
});

test("a previously-collapsed topbar preference does not apply on non-campaign routes", async () => {
  localStorage.setItem("grimoire.topbar.collapsed", "1");
  render(<MemoryRouter initialEntries={["/worlds"]}><App /></MemoryRouter>);
  await screen.findByText(/GRIMOIRE/);
  expect(screen.getByRole("banner")).not.toHaveClass("collapsed");
  const topbar = within(screen.getByRole("banner"));
  expect(topbar.getByRole("link", { name: /worlds/i })).toBeInTheDocument();
});
```

In `frontend/src/routes/CampaignView.test.tsx`, add this test at the end of the file:

```tsx
test("the chrome bar toggles the subheader independently of the topbar toggle", async () => {
  renderCampaign();
  await screen.findByText("Run One");
  fireEvent.click(screen.getByRole("button", { name: "▴ Bar" }));
  expect(screen.queryByText("Run One")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "▾ Bar" }));
  await screen.findByText("Run One");
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `npx vitest run src/App.test.tsx src/routes/CampaignView.test.tsx`
Expected: FAIL — `App` doesn't pass `topbarCollapsed`/`onToggleTopbar` to `CampaignView` yet, and there's no chrome-bar with "▴ Bar"/"▾ Bar" buttons.

- [ ] **Step 3: Implement**

In `frontend/src/App.tsx`, add state and route-scoping logic inside `App`, right after `const location = useLocation();` (line 21):

```tsx
  const [topbarCollapsed, setTopbarCollapsed] = useState(
    () => localStorage.getItem("grimoire.topbar.collapsed") === "1");
  const isCampaignRoute = /^\/campaigns\/[^/]+$/.test(location.pathname);

  function toggleTopbar() {
    setTopbarCollapsed((v) => {
      localStorage.setItem("grimoire.topbar.collapsed", v ? "0" : "1");
      return !v;
    });
  }
```

Change the `<header>` opening tag (line 38):

```tsx
      <header className={"topbar" + (isCampaignRoute && topbarCollapsed ? " collapsed" : "")}>
```

Change the campaign route (line 73):

```tsx
        <Route path="/campaigns/:cid" element={
          <CampaignView ready={ready} topbarCollapsed={topbarCollapsed} onToggleTopbar={toggleTopbar} />} />
```

(`isCampaignRoute` is always true whenever this route element is mounted, so `topbarCollapsed` can be passed directly without re-guarding.)

In `frontend/src/routes/CampaignView.tsx`, change the component signature (line 68):

```tsx
export default function CampaignView({ ready, topbarCollapsed = false, onToggleTopbar = () => {} }: {
  ready: boolean; topbarCollapsed?: boolean; onToggleTopbar?: () => void;
}) {
```

Add state right after the `toggleInspector` function from Task 5:

```tsx
  const [subheaderCollapsed, setSubheaderCollapsed] = useState(
    () => localStorage.getItem("grimoire.subheader.collapsed") === "1");
  function toggleSubheader() {
    setSubheaderCollapsed((v) => {
      localStorage.setItem("grimoire.subheader.collapsed", v ? "0" : "1");
      return !v;
    });
  }
```

Replace the start of the `return` block — the `<div className="workspace">` opening and the `<div className="subheader">...</div>` block (lines 472-503) — with:

```tsx
  return (
    <div className="workspace">
      <div className="chrome-bar">
        <button className="chrome-toggle" aria-pressed={!topbarCollapsed} onClick={onToggleTopbar}>
          {topbarCollapsed ? "▾ Nav" : "▴ Nav"}
        </button>
        <button className="chrome-toggle" aria-pressed={!subheaderCollapsed} onClick={toggleSubheader}>
          {subheaderCollapsed ? "▾ Bar" : "▴ Bar"}
        </button>
      </div>
      {!subheaderCollapsed && (
      <div className="subheader">
        <Link to="/" className="sub-back">‹ Campaigns</Link>
        <span className="sub-divider" />
        <span className="sub-name">{name}</span>
        {worldName && (
          <Link to={`/campaigns/${cid}/world`} className="sub-world">World ▸ {worldName} ↗</Link>
        )}
        <div className="sub-actions">
          <details className="sub-export-menu">
            <summary className="sub-export">Export</summary>
            <div className="sub-export-options">
              <a href={`/api/campaigns/${cid}/export.epub`} download>EPUB</a>
              <a href={`/api/campaigns/${cid}/export.md.zip`} download>Markdown</a>
              <a href={`/api/campaigns/${cid}/export.html`} download>HTML</a>
              <a href={`/api/campaigns/${cid}/export.txt`} download>Plain text</a>
              <a href={`/api/campaigns/${cid}/export.json`} download>JSON</a>
            </div>
          </details>
          <button className="sub-changes" onClick={() => setShowChanges((v) => !v)}>
            {showChanges ? "Close" : "Changes"}
          </button>
          <button className="sub-mechanics" onClick={() => setShowMechanics((v) => !v)}>
            {showMechanics ? "Close" : "Mechanics"}
          </button>
          <button className="sub-end" onClick={endScene}
                  disabled={!activeId || absorbing || busy}>
            {absorbing ? "Ending…" : "End scene"}
          </button>
        </div>
      </div>
      )}
```

(The `<div className="layout">` and everything after it — Tasks 5's rail/inspector work, `.main`, `{drawer && ...}`, `{chooserOpen && ...}`, and the closing tags — is unchanged.)

In `frontend/src/index.css`, add after the `.topbar-right .config-link.active` rule (after line 155):

```css
.topbar.collapsed nav, .topbar.collapsed .topbar-right { display: none; }
```

Add near the `.subheader` rule (after line 159), or anywhere in the file — placement doesn't matter for these two standalone selectors:

```css
.chrome-bar { display: flex; gap: 10px; padding: 3px 14px; background: var(--panel); border-bottom: 1px solid var(--rule-soft); }
.chrome-toggle {
  background: none; border: none; cursor: pointer; color: var(--muted);
  font-family: var(--fm); font-size: 10px; letter-spacing: 0.1em; text-transform: uppercase; padding: 2px 0;
}
.chrome-toggle:hover { color: var(--accent); }
```

- [ ] **Step 4: Run tests to verify they pass**

Run (from `frontend/`): `npx vitest run src/App.test.tsx src/routes/CampaignView.test.tsx`
Expected: PASS — full files, including every pre-existing `App.test.tsx` test (they never navigate to a campaign route, so the `CampaignView` mock is never exercised there) and every pre-existing `CampaignView.test.tsx` test (the subheader's contents are unchanged, just now conditionally wrapped, and default to expanded).

Run the complete frontend suite to catch any cross-file regression: `npx vitest run` (from `frontend/`).
Run the typecheck: `npx tsc -b` (from `frontend/`).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/App.tsx frontend/src/App.test.tsx frontend/src/routes/CampaignView.tsx frontend/src/routes/CampaignView.test.tsx frontend/src/index.css
git commit -m "feat(frontend): collapsible topbar and campaign subheader, scoped to the campaign page"
```

---

## Final verification

- [ ] Run the full backend suite: `backend/.venv/Scripts/python.exe -m pytest backend -q`
- [ ] Run the full frontend suite (from `frontend/`): `npx vitest run`
- [ ] Run the frontend typecheck (from `frontend/`): `npx tsc -b`
- [ ] Manually exercise the feature in a browser per this repo's "For UI or frontend changes..." convention: open a campaign, collapse/expand each sidebar section, collapse/expand the rail and inspector panels (confirm the edge tabs work and layout doesn't leave dead space), collapse/expand the topbar and subheader via the chrome bar, add and remove a character from an in-progress scene's Active characters section and confirm the narrated transition line appears in the transcript, then reload the page and confirm every collapse choice persisted.
