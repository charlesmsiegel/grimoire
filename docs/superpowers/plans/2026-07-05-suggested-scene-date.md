# Suggested Scene Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New scenes arrive with a suggested date pre-filled in the SceneInspector's date input, derived from the previous scene's date plus an LLM estimate of the in-world gap. Spec: `docs/superpowers/specs/2026-07-05-suggested-scene-date-design.md`.

**Architecture:** The New Scene chooser's existing scene-suggestions LLM call is extended to return a per-suggestion `date` and a top-level `next_date`. Whichever creation path the user takes, the hint is persisted in the scene's frontmatter (`suggested_date`), `GET /datetime` serves it as `suggested` (falling back to the latest chronicle date when no hint exists — this covers manual and keyless creations), and the SceneInspector pre-fills its date input from it. The user still confirms with "Set date"; nothing is auto-applied.

**Tech Stack:** FastAPI + pytest (backend), Vite/React + vitest (frontend). No new dependencies.

## Global Constraints

- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)` (route tests get this from the existing `client` fixture).
- Run backend tests: `backend/.venv/Scripts/python.exe -m pytest backend -q` (repo root).
- Run frontend tests **from `frontend/`**: `npx vitest run` and `npx tsc -b`. Never `npx --prefix frontend vitest run` — it skips `frontend/vitest.config.ts` and breaks every mock-based test.
- A suggested date is only a hint: invalid values are dropped silently everywhere, never surfaced as errors.
- Gregorian native date format is ISO `YYYY-MM-DD` (matches `<input type="date">`); canonical datetimes may carry a `Thh:mm` suffix which must never reach the date input.

---

### Task 1: LLM prompt + parse — per-suggestion `date` and top-level `next_date`

**Files:**
- Modify: `backend/src/grimoire/store/suggest.py` (INSTRUCTION block ~line 160, `build_prompt` ~line 199, `parse_output` ~line 234)
- Test: `backend/tests/test_suggest_store.py`

**Interfaces:**
- Consumes: `calendars.read_calendar(croot)`, `calendars.get_provider(cfg["primary"])`, `calendars.normalize(provider, s)` (all already imported in suggest.py); `snapshot["now"]` from `build_snapshot`.
- Produces: `parse_output(text, cid)` items gain key `"date"` (canonical native date or `""`); new function `parse_next_date(text: str, cid: str) -> str` (canonical native date or `""`); `build_prompt` requests the date keys only when `snapshot["now"]` is truthy.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_suggest_store.py` (helpers `_campaign` already exist at the top of the file):

```python
# ---- suggested dates (per-suggestion "date" + top-level "next_date") ----
def test_build_prompt_requests_dates_only_with_a_current_date():
    snap = {"now": "2026-01-01", "friendly": "Jan 1", "holidays_today": [], "upcoming": None,
            "birthdays": [], "open_threads": [], "absent_cast": [],
            "available_cast": [], "available_locations": []}
    assert "next_date" in suggest.build_prompt(snap)[0]["content"]
    snap["now"] = ""
    assert "next_date" not in suggest.build_prompt(snap)[0]["content"]


def test_parse_output_keeps_valid_dates_and_drops_bad_ones(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    text = ('{"suggestions": ['
            '{"title": "A", "premise": "P", "cast": [], "location": "", "date": "2026-07-10"},'
            '{"title": "B", "premise": "P", "cast": [], "location": "", "date": "2026-13-40"},'
            '{"title": "C", "premise": "P", "cast": [], "location": ""}]}')
    out = suggest.parse_output(text, cid)
    assert [s["date"] for s in out] == ["2026-07-10", "", ""]


def test_parse_next_date_validates_and_tolerates_garbage(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert suggest.parse_next_date('{"suggestions": [], "next_date": "2026-07-08"}', cid) == "2026-07-08"
    assert suggest.parse_next_date('{"suggestions": [], "next_date": "soonish"}', cid) == ""
    assert suggest.parse_next_date('{"suggestions": []}', cid) == ""
    assert suggest.parse_next_date("not json", cid) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q`
Expected: 3 FAIL — `KeyError: 'date'` / `AttributeError: module ... has no attribute 'parse_next_date'` / assert on `next_date` in prompt.

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/suggest.py`, add after the `INSTRUCTION` constant (~line 169):

```python
DATE_INSTRUCTION = (
    ' Each suggestion also includes key "date": the date that scene most plausibly opens '
    "on, given how much in-world time its premise implies has passed since the current "
    'date (same notation as the current date, date only). Also include a top-level key '
    '"next_date": your single best estimate of the next scene\'s date if none of the '
    "suggestions is used."
)
```

Change `build_prompt` to append it only when the snapshot has a current date:

```python
def build_prompt(snapshot: dict, greeting_candidates: list[dict] | None = None) -> list[dict]:
    instruction, content = INSTRUCTION, _render_snapshot(snapshot)
    if snapshot["now"]:  # no reference date -> no gap to estimate; don't invite invention
        instruction += DATE_INSTRUCTION
    if greeting_candidates:
        instruction += RANK_INSTRUCTION
        content += "\n\nAvailable greetings (use these ids):\n" + "\n".join(
            f"- {g['id']} = {g['name']}: {g['excerpt']}" for g in greeting_candidates)
    return [{"role": "system", "content": instruction},
            {"role": "user", "content": content}]
```

Add a best-effort date validator near `_valid_ids` (~line 209):

```python
def _date_normalizer(cid: str):
    """Canonical native date or "" — a suggested date is only a hint, so never raise."""
    try:
        provider = calendars.get_provider(
            calendars.read_calendar(campaigns.campaign_root(cid))["primary"])
    except (calendars.CalendarError, KeyError):
        return lambda _s: ""

    def norm(s: str) -> str:
        if not s:
            return ""
        try:
            return calendars.normalize(provider, s)
        except calendars.CalendarError:
            return ""
    return norm
```

In `parse_output`, build the normalizer once (next to `char_ids, player_tokens, loc_ids = _valid_ids(cid)` add `norm = _date_normalizer(cid)`) and extend the appended dict:

```python
        out.append({"title": title, "premise": premise, "cast": cast,
                    "location": loc if loc in loc_ids else "",
                    "date": norm(str(e.get("date", "")).strip())})
```

Add `parse_next_date` after `parse_output`:

```python
def parse_next_date(text: str, cid: str) -> str:
    """The model's general next-scene date estimate, validated; "" when absent/bad."""
    parsed = _extract_json(text)
    raw = parsed.get("next_date", "") if isinstance(parsed, dict) else ""
    return _date_normalizer(cid)(str(raw).strip())
```

- [ ] **Step 4: Run the suggest tests, then the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q` → all PASS.
Run: `backend/.venv/Scripts/python.exe -m pytest backend -q` → all PASS (existing `parse_output` tests don't inspect `date`, so they must still pass unchanged).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/suggest.py backend/tests/test_suggest_store.py
git commit -m "feat(backend): scene suggestions estimate per-card and next-scene dates"
```

---

### Task 2: Scene store — persist the hint, clear it on the first real date

**Files:**
- Modify: `backend/src/grimoire/store/scenes.py` (`create_scene` ~line 112, `set_datetime` ~line 370)
- Test: `backend/tests/test_scene_store.py`

**Interfaces:**
- Consumes: `calendars.read_calendar` / `get_provider` / `normalize` (calendars is already imported in scenes.py).
- Produces: `create_scene(cid: str, title: str, suggested_date: str | None = None) -> str` (stores valid hints as frontmatter `suggested_date`, ignores invalid ones); new `get_suggested_date(cid: str, sid: str) -> str` (`""` when absent/missing scene); `set_datetime` drops the `suggested_date` key whenever it writes.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_scene_store.py` (the `_campaign(monkeypatch, tmp_path)` helper already exists at the top of the file):

```python
def test_create_scene_stores_a_valid_suggested_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S", suggested_date="2026-07-10")
    assert scenes.get_suggested_date(cid, sid) == "2026-07-10"


def test_create_scene_ignores_an_invalid_suggested_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S", suggested_date="soonish")
    assert scenes.get_suggested_date(cid, sid) == ""


def test_get_suggested_date_missing_scene_is_empty(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert scenes.get_suggested_date(cid, "nope") == ""


def test_set_datetime_clears_the_suggested_date(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    sid = scenes.create_scene(cid, "S", suggested_date="2026-07-10")
    sid = scenes.set_datetime(cid, sid, "2026-07-12")["id"]
    assert scenes.get_suggested_date(cid, sid) == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q`
Expected: 4 FAIL — `TypeError: create_scene() got an unexpected keyword argument` / `AttributeError: ... no attribute 'get_suggested_date'`.

- [ ] **Step 3: Implement**

In `backend/src/grimoire/store/scenes.py`, change `create_scene`:

```python
def create_scene(cid: str, title: str, suggested_date: str | None = None) -> str:
    _require_campaign(cid)
    d = _scenes_dir(cid)
    d.mkdir(parents=True, exist_ok=True)
    number, width = _numbering(cid)
    if len(str(number)) > width:  # 999 -> 1000: widen the whole campaign first
        width = len(str(number))
        repad(cid, width)
    now = now_iso()
    base = scene_ids.format_sid(number, width, None, slugify(title))
    sid = uniquify(base, lambda c: _scene_path(cid, c).exists())
    meta = {"title": title, "model": read_config()["model"], "created": now, "updated": now}
    if suggested_date:
        try:
            provider = calendars.get_provider(
                calendars.read_calendar(campaigns.campaign_root(cid))["primary"])
            meta["suggested_date"] = calendars.normalize(provider, suggested_date)
        except (calendars.CalendarError, KeyError):
            pass  # only a hint — a bad one is dropped, never an error
    _scene_path(cid, sid).write_text(dump_frontmatter(meta, ""), encoding="utf-8")
    return sid
```

Add `get_suggested_date` next to `get_time_history` (~line 361):

```python
def get_suggested_date(cid: str, sid: str) -> str:
    """The creation-time date hint, if the scene still carries one. Missing ⇒ ""."""
    p = _scene_path(cid, sid)
    if not _safe_id(sid) or not p.exists():
        return ""
    meta, _ = parse_frontmatter(p.read_text(encoding="utf-8"))
    return meta.get("suggested_date", "")
```

In `set_datetime`, right after `meta, body = parse_frontmatter(p.read_text(encoding="utf-8"))` (~line 388), add:

```python
    meta.pop("suggested_date", None)  # the hint is stale once a real date exists
```

- [ ] **Step 4: Run the scene tests, then the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_scene_store.py -q` → all PASS.
Run: `backend/.venv/Scripts/python.exe -m pytest backend -q` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/scenes.py backend/tests/test_scene_store.py
git commit -m "feat(backend): scenes persist a creation-time suggested date hint"
```

---

### Task 3: Routes — accept the hint at creation, expose `suggested`, return the LLM dates

**Files:**
- Modify: `backend/src/grimoire/routes.py` (`NewScene` ~line 144, `post_scene` ~line 1313, `post_scene_suggestions` ~line 1342, `get_scene_datetime` ~line 1836)
- Test: `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `store.scenes.create_scene(cid, title, suggested_date)`, `store.scenes.get_suggested_date(cid, sid)` (Task 2); `store.suggest.parse_next_date(text, cid)` and `"date"` on parsed suggestions (Task 1); `store.chronicle.recent(cid, 1)`; `store.calendars.split_native(native)`.
- Produces: `POST /campaigns/{cid}/scenes` accepts optional `suggested_date`; `POST /campaigns/{cid}/scene-suggestions` response items gain `"date"` and the response gains `"next_date"`; `GET /campaigns/{cid}/scenes/{sid}/datetime` response gains `"suggested"` (native date string or `null`).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_routes.py` (fixtures `client`, `_campaign`, `FakeOpenRouterComplete` already exist there):

```python
def test_datetime_get_returns_creation_hint_as_suggested(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes",
                      json={"title": "S", "suggested_date": "2026-07-10"}).json()["id"]
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["current"] is None and got["suggested"] == "2026-07-10"


def test_datetime_suggested_falls_back_to_chronicle_date(client):
    _wid, cid = _campaign(client)
    store.chronicle.absorb(cid, {"id": "001--old", "one_line": "x", "summary": "y",
                                 "keywords": [], "cast": [], "location": "",
                                 "date": "2026-07-04T21:30"})
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    got = client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()
    assert got["suggested"] == "2026-07-04"  # time-of-day never reaches the date input


def test_datetime_suggested_is_null_without_signals_and_once_dated(client):
    _wid, cid = _campaign(client)
    sid = client.post(f"/api/campaigns/{cid}/scenes", json={"title": "S"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid}/datetime").json()["suggested"] is None
    sid2 = client.post(f"/api/campaigns/{cid}/scenes",
                       json={"title": "S2", "suggested_date": "2026-07-10"}).json()["id"]
    sid2 = client.put(f"/api/campaigns/{cid}/scenes/{sid2}/datetime",
                      json={"datetime": "2026-07-12"}).json()["id"]
    assert client.get(f"/api/campaigns/{cid}/scenes/{sid2}/datetime").json()["suggested"] is None


def test_scene_suggestions_include_dates_and_next_date(client):
    client.put("/api/config", json={"openrouter_key": "sk-or-x"})
    _wid, cid = _campaign(client)
    client.app.dependency_overrides[routes.get_openrouter] = lambda: FakeOpenRouterComplete(
        '{"suggestions": [{"title": "T", "premise": "P", "cast": [], "location": "",'
        ' "date": "2026-07-10"}], "next_date": "2026-07-08"}')
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert r.status_code == 200
    assert r.json()["suggestions"][0]["date"] == "2026-07-10"
    assert r.json()["next_date"] == "2026-07-08"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q -k "suggested or next_date"`
Expected: 4 FAIL — `KeyError: 'suggested'` / `KeyError: 'date'` / `KeyError: 'next_date'`.

- [ ] **Step 3: Implement**

In `backend/src/grimoire/routes.py`:

`NewScene` (~line 144) gains the field:

```python
class NewScene(BaseModel):
    title: str | None = None
    suggested_date: str | None = None
```

`post_scene` (~line 1313) passes it through:

```python
@router.post("/campaigns/{cid}/scenes")
def post_scene(cid: str, body: NewScene):
    title = body.title or "New scene"
    try:
        return {"id": store.scenes.create_scene(cid, title, body.suggested_date)}
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
```

In `post_scene_suggestions` (~line 1360), extend the response item and the return:

```python
    out = []
    for s in store.suggest.parse_output(text, cid):
        loc = {"id": s["location"], "name": loc_names.get(s["location"], s["location"])} if s["location"] else None
        out.append({"title": s["title"], "premise": s["premise"], "date": s["date"],
                    "cast": _resolve_cast(cid, s["cast"]), "location": loc})
    picks = (store.suggest.parse_greeting_picks(text, {c["id"] for c in candidates})
             if candidates else [])
    return {"suggestions": out, "greeting_picks": picks,
            "next_date": store.suggest.parse_next_date(text, cid)}
```

Replace `get_scene_datetime` (~line 1836):

```python
@router.get("/campaigns/{cid}/scenes/{sid}/datetime")
def get_scene_datetime(cid: str, sid: str):
    _require_scene(cid, sid)
    history = store.scenes.get_time_history(cid, sid)
    current = None
    suggested = None
    if history:
        cfg = store.calendars.read_calendar(store.campaigns.campaign_root(cid))
        native = history[-1]
        try:
            current = {"native": native, **store.calendars.today_facts(cfg, native),
                       "cast": store.context.cast_datetime_facts(cid, sid, native)}
        except store.calendars.CalendarError:
            current = None  # misconfigured calendar — surface "no date" rather than 500
    else:
        # dateless: offer a pre-fill — the creation-time hint, else where the story left off
        hint = store.scenes.get_suggested_date(cid, sid)
        if not hint:
            try:
                recent = store.chronicle.recent(cid, 1)
                hint = recent[-1].get("date", "") if recent else ""
            except Exception:  # noqa: BLE001 — garbled chronicle.json
                hint = ""
        if hint:
            suggested = store.calendars.split_native(hint)[0]
    return {"current": current, "history": history, "suggested": suggested}
```

- [ ] **Step 4: Run the route tests, then the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes.py -q` → all PASS (existing datetime and suggestions tests assert on subsets of the response, so the added keys must not break them; `test_scene_suggestions_returns_resolved` asserts only on `s["title"]`/`s["premise"]`/`s["cast"]`/`s["location"]`).
Run: `backend/.venv/Scripts/python.exe -m pytest backend -q` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes.py
git commit -m "feat(backend): scene datetime exposes a suggested pre-fill date"
```

---

### Task 4: Frontend — chooser passes dates to createScene

**Files:**
- Modify: `frontend/src/api/client.ts` (types ~lines 168, 190; `createScene` ~line 290; `sceneSuggestions` ~line 503)
- Modify: `frontend/src/components/NewSceneChooser.tsx`
- Test: `frontend/src/components/NewSceneChooser.test.tsx`

**Interfaces:**
- Consumes: backend `POST /scenes` `suggested_date` field, suggestions response `date`/`next_date` (Task 3).
- Produces: `api.createScene(cid: string, title?: string, suggestedDate?: string)`; `SceneSuggestion` gains `date?: string`; `sceneSuggestions` return type gains `next_date?: string`; `SceneDatetime` gains `suggested: string | null` (consumed by Task 5). Existing callers (`CampaignView.tsx:162`, `CampaignWizard.tsx:80`) call `createScene(cid)` and remain valid.

- [ ] **Step 1: Write the failing tests**

Append to `frontend/src/components/NewSceneChooser.test.tsx`:

```tsx
test("picking a generated card passes its suggested date to createScene", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [{ ...SUGGESTION, date: "2026-07-10" }], next_date: "2026-07-08" });
  renderChooser();
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", undefined, "2026-07-10"));
});

test("manual creation passes the general next_date once suggestions land", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [SUGGESTION], next_date: "2026-07-08", greeting_picks: [] });
  renderChooser();
  await screen.findByText("The creditor"); // suggestions resolved → nextDate is set
  fireEvent.click(screen.getByRole("button", { name: /create manually/i }));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", undefined, "2026-07-08"));
});

test("a greeting pick passes the general next_date when available", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [SUGGESTION], next_date: "2026-07-08", greeting_picks: [] });
  renderChooser();
  await screen.findByText("The creditor");
  fireEvent.click(screen.getByText("Reckoning"));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", undefined, "2026-07-08"));
});

test("a card without a date falls back to next_date", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [SUGGESTION], next_date: "2026-07-08", greeting_picks: [] });
  renderChooser();
  fireEvent.click(await screen.findByText("The creditor"));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", undefined, "2026-07-08"));
});
```

Note: the existing tests "picking a greeting…", "Create manually only creates the scene" keep asserting `toHaveBeenCalledWith("c")` — the beforeEach mock returns no `next_date`, so the chooser must keep calling `createScene(cid)` with one argument in that case. Do not modify them.

- [ ] **Step 2: Run the tests to verify they fail**

From `frontend/`: `npx vitest run src/components/NewSceneChooser.test.tsx`
Expected: the 4 new tests FAIL (`createScene` called with `("c")`, not with a date).

- [ ] **Step 3: Implement**

In `frontend/src/api/client.ts`:

```ts
export type SceneDatetime = { current: SceneDatetimeFacts | null; history: string[]; suggested: string | null };
```

```ts
export type SceneSuggestion = {
  title: string; premise: string; date?: string;
  cast: { kind: string; id: string; name: string }[];
  location: { id: string; name: string } | null;
};
```

```ts
  createScene: (cid: string, title?: string, suggestedDate?: string) =>
    request<{ id: string }>("POST", `/api/campaigns/${cid}/scenes`,
      { title, suggested_date: suggestedDate }),
```

```ts
  sceneSuggestions: (cid: string, after?: string) =>
    request<{ suggestions: SceneSuggestion[]; greeting_picks?: string[]; next_date?: string }>(
      "POST", `/api/campaigns/${cid}/scene-suggestions${after ? `?after=${encodeURIComponent(after)}` : ""}`),
```

In `frontend/src/components/NewSceneChooser.tsx`:

Add state next to `picks` (~line 21):

```tsx
  // the same call estimates when the next scene opens; undefined until it answers
  const [nextDate, setNextDate] = useState<string | undefined>(undefined);
```

In the suggestions effect (~line 33), capture it:

```tsx
    api.sceneSuggestions(cid, afterSid ?? undefined)
      .then((r) => { setSuggestions(r.suggestions); setPicks(r.greeting_picks ?? []); setNextDate(r.next_date || undefined); })
      .catch((err) => { setSuggestions([]); setPicks([]); setError(errMsg(err)); });
```

Thread a date through `create` (~line 56) — one argument when absent so keyless behavior (and its tests) are byte-identical:

```tsx
  async function create(seed: (sid: string) => Promise<string | undefined>, date?: string) {
    setBusy(true);
    setError(null);
    let created: string | null = null;
    try {
      const { id } = date ? await api.createScene(cid, undefined, date) : await api.createScene(cid);
      created = id;
      const prompt = await seed(id);
      if (prompt !== undefined) onCreated(id, prompt);
      else onCreated(id);
    } catch (err: any) {
      // a half-seeded scene would be a stray — remove it before surfacing the error
      if (created) await api.deleteScene(cid, created).catch(() => {});
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  }
```

Update the three pickers:

```tsx
  const pickManual = () => create(async () => undefined, nextDate);
  const pickGreeting = (gid: string) => create(async (sid) => {
    await api.startFromGreeting(cid, sid, gid);
    return undefined;
  }, nextDate);
  const pickSuggestion = (s: SceneSuggestion) => create(async (sid) => {
    if (s.cast.length) {
      // one request; members already cast come back in `skipped`, which is fine
      await api.addCastBatch(cid, sid, s.cast.map((c) => ({ kind: c.kind, id: c.id })));
    }
    if (s.location) await api.setSceneLocation(cid, sid, s.location.id);
    return s.premise;
  }, s.date || nextDate);
```

- [ ] **Step 4: Run the chooser tests, then the whole frontend suite and typecheck**

From `frontend/`:
- `npx vitest run src/components/NewSceneChooser.test.tsx` → all PASS (new and existing).
- `npx vitest run` → all PASS.
- `npx tsc -b` → clean. (`SceneDatetime.suggested` is a new required field, but SceneInspector only reads the type — mocks in tests are `as any` — so no other file should need edits; if tsc flags one, add `suggested: null` to the flagged literal.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/client.ts frontend/src/components/NewSceneChooser.tsx frontend/src/components/NewSceneChooser.test.tsx
git commit -m "feat(frontend): new-scene chooser passes suggested dates to scene creation"
```

---

### Task 5: Frontend — SceneInspector pre-fills the date input

**Files:**
- Modify: `frontend/src/components/SceneInspector.tsx` (`reloadWhen` ~line 48)
- Test: `frontend/src/components/SceneInspector.test.tsx`

**Interfaces:**
- Consumes: `SceneDatetime.suggested` (Task 4 type, Task 3 endpoint).
- Produces: user-visible behavior only — a dateless scene with a suggestion shows it in the "Scene date" input; the existing "Set date" button confirms it.

- [ ] **Step 1: Write the failing test**

In `frontend/src/components/SceneInspector.test.tsx`, update the `beforeEach` mock (~line 42) to the new response shape:

```tsx
  (api.getSceneDatetime as any).mockResolvedValue({ current: null, history: [], suggested: null });
```

Append:

```tsx
test("a dateless scene with a suggestion pre-fills the date input", async () => {
  (api.getSceneDatetime as any).mockResolvedValue(
    { current: null, history: [], suggested: "2026-07-06" });
  renderInspector();
  const input = await screen.findByLabelText("Scene date");
  await waitFor(() => expect((input as HTMLInputElement).value).toBe("2026-07-06"));
  fireEvent.click(screen.getByRole("button", { name: /set date/i }));
  await waitFor(() => expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s", "2026-07-06"));
});
```

- [ ] **Step 2: Run the test to verify it fails**

From `frontend/`: `npx vitest run src/components/SceneInspector.test.tsx`
Expected: the new test FAILS (input value stays `""`); all others PASS.

- [ ] **Step 3: Implement**

In `frontend/src/components/SceneInspector.tsx`, replace `reloadWhen` (~line 48):

```tsx
  const reloadWhen = useCallback(
    () => api.getSceneDatetime(cid, sid).then((w) => {
      setWhen(w);
      // dateless scene with a suggestion: pre-fill the input, but never clobber typing
      if (!w.current && w.suggested) setDateInput((prev) => prev || w.suggested!);
    }).catch(() => setWhen(null)),
    [cid, sid]);
```

- [ ] **Step 4: Run the inspector tests, then everything**

From `frontend/`:
- `npx vitest run src/components/SceneInspector.test.tsx` → all PASS.
- `npx vitest run` → all PASS.
- `npx tsc -b` → clean.

Run the backend suite one last time from the repo root: `backend/.venv/Scripts/python.exe -m pytest backend -q` → all PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SceneInspector.tsx frontend/src/components/SceneInspector.test.tsx
git commit -m "feat(frontend): scene inspector pre-fills the suggested date"
```
