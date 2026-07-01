# Suggested Next Scenes (Phase 5b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** At scene creation, offer 3–4 model-proposed openings (from open plot threads + long-absent cast + calendar), and let the user pick one to auto-seed the scene's cast/location and prefill the opener prompt.

**Architecture:** A new read-only `store/suggest.py` assembles a deterministic snapshot (threads, chronicle "now", calendar/birthday facts, absent cast, seedable ids), builds a one-shot prompt, and parses the model's JSON openings (validating ids). A campaign-level `POST /scene-suggestions` route runs the LLM call (non-streaming, key-gated) and resolves ids→names. The empty-scene `CastPanel` gains a "Suggest scenes" button; picking a card auto-seeds via the existing `addToCast`/`setSceneLocation` APIs and prefills the opener prompt.

**Tech Stack:** FastAPI backend (Python, pytest), Vite/React frontend (vitest/tsc).

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-01-scene-suggestions-design.md`.
- Backend tests isolate the store via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- Run backend: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q` (from repo root).
- Frontend runs **from** `frontend/`: `npx vitest run` and `npx tsc -b`.
- LLM output is untrusted: coerce to stripped `str`; validate every id and drop unknowns.
- All snapshot pieces are tolerant (a missing/garbled source contributes nothing, never raises).
- Ephemeral: the suggestion call persists nothing; auto-seed happens client-side via existing endpoints.
- Recent-chronicle window for "long-absent": the last **5** scenes (`suggest.RECENT_WINDOW = 5`).
- Cast tokens are `"<kind>:<id>"`; seedable NPCs = any world character, players = roster players, locations = campaign locations.

---

### Task 1: `suggest.build_snapshot` — the deterministic signal assembly

**Files:**
- Create: `backend/src/grimoire/store/suggest.py`
- Test: `backend/tests/test_suggest_store.py`

**Interfaces:**
- Consumes: `plot.open_threads`, `chronicle.recent`, `appearances.roster`, `characters.list_characters`/`read_character`, `pcs.read_pc`/`read_persona`, `entities.list_entities`, `briefs.read_brief`, `calendars` (`read_calendar`/`get_provider`/`today_facts`/`age`/`is_anniversary`/`fixed_of`/`UPCOMING_WINDOW_DAYS`).
- Produces: `RECENT_WINDOW = 5`; `build_snapshot(cid) -> dict` with keys `now, friendly, holidays_today, upcoming, birthdays, open_threads, absent_cast, available_cast, available_locations`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_suggest_store.py`:

```python
from grimoire.store import (appearances, campaigns, characters, chronicle, plot,
                            scenes, suggest, worlds)


def _campaign(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    return campaigns.create_campaign("Run", worlds.create_world("W"))


def _char(croot, name, birthdate=""):
    card = characters.blank_card(name)
    if birthdate:
        card["data"]["birthdate"] = birthdate  # not used by snapshot; meta drives it
    cid_ = characters.create_character(croot, name, "main", card)[0]
    if birthdate:
        meta = characters.read_character(croot, cid_)["meta"]
        meta["birthdate"] = birthdate
        characters.write_character_meta(croot, cid_, meta)
    return cid_


def test_build_snapshot_gathers_signals(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    absent = _char(croot, "Doran")            # in roster, absent from recent chronicle
    present = _char(croot, "Seraphine")
    # roster: both appear in some scene (so they're on the roster), present also in chronicle
    s1 = scenes.create_scene(cid, "One")
    appearances.appear(cid, s1, "characters", absent, "main", "npc")
    appearances.appear(cid, s1, "characters", present, "main", "npc")
    chronicle.absorb(cid, {"id": s1, "one_line": "x", "summary": "y", "keywords": [],
                           "cast": [f"characters/{present}"], "location": "", "date": ""})
    plot.set_movement(cid, "the-map", "The map", "advanced", "It is a forgery.", s1)

    snap = suggest.build_snapshot(cid)
    assert [t["title"] for t in snap["open_threads"]] == ["The map"]
    absent_names = [a["name"] for a in snap["absent_cast"]]
    assert "Doran" in absent_names and "Seraphine" not in absent_names   # present is not absent
    tokens = [c["token"] for c in snap["available_cast"]]
    assert f"characters:{absent}" in tokens and f"characters:{present}" in tokens


def test_build_snapshot_tolerates_empty_campaign(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    snap = suggest.build_snapshot(cid)  # no scenes/chronicle/plot/calendar
    assert snap["open_threads"] == [] and snap["absent_cast"] == []
    assert snap["now"] == "" and snap["birthdays"] == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'grimoire.store.suggest'` (and possibly `AttributeError: write_character_meta` — if so, see Step 3's note).

- [ ] **Step 3: Implement `build_snapshot`**

First confirm the meta-writer name the test uses:

Run: `grep -n "def write_character_meta\|def update_character\|def write_meta" backend/src/grimoire/store/characters.py`

Use whichever exists to set `birthdate` in the test helper (rename `write_character_meta` in the test to the real function if different; the assertions don't depend on birthdate, so if there's no simple meta-writer, drop the `birthdate` lines from `_char` and the birthday assertions entirely — they are covered by Task-1 Step 6 below only if a writer exists).

Create `backend/src/grimoire/store/suggest.py`:

```python
"""Ephemeral scene-suggestion helper: assemble deterministic campaign signals (open plot
threads, long-absent cast, calendar facts at the current moment, seedable ids), build the
one-shot prompt, and parse the model's proposed openings. Assembly + prompt/parse only;
the LLM call lives in the route (mirrors absorb.py / briefs.py).
"""

from __future__ import annotations

import json

from . import (appearances, briefs, calendars, campaigns, characters, chronicle,
               entities, pcs, plot, worlds)

RECENT_WINDOW = 5


def _world_root(cid: str):
    return worlds.world_root(campaigns.read_campaign(cid)["meta"].get("world", ""))


def _char_name(croot, aid: str) -> str:
    try:
        return characters.read_character(croot, aid)["meta"].get("name", aid)
    except characters.CharacterNotFound:
        return aid


def _recent_char_ids(cid: str) -> set[str]:
    ids: set[str] = set()
    for r in chronicle.recent(cid, RECENT_WINDOW):
        for ref in r.get("cast", []) or []:
            kind, _, aid = str(ref).partition("/")
            if kind == "characters" and aid:
                ids.add(aid)
    return ids


def _birthdays(cid: str, croot, now: str) -> list[dict]:
    if not now:
        return []
    try:
        cfg = calendars.read_calendar(croot)
        provider = calendars.get_provider(cfg["primary"])
        now_fixed = calendars.fixed_of(provider, now)
    except (calendars.CalendarError, KeyError):
        return []
    out: list[dict] = []
    for a in appearances.roster(cid):
        try:
            if a["kind"] == "pcs":
                birth = pcs.read_persona(croot, a["id"], a["version"]).get("birthdate", "")
                name = pcs.read_pc(croot, a["id"])["meta"].get("name", a["id"])
            else:
                birth = characters.read_character(croot, a["id"])["meta"].get("birthdate", "")
                name = _char_name(croot, a["id"])
        except (characters.CharacterNotFound, pcs.PCNotFound, pcs.PCVersionNotFound):
            continue
        if not birth:
            continue
        try:
            when = None
            for d in range(0, calendars.UPCOMING_WINDOW_DAYS + 1):
                if calendars.is_anniversary(provider, birth, provider.format(now_fixed + d)):
                    when = "today" if d == 0 else f"in {d} days"
                    break
            if when is None:
                continue
            out.append({"name": name, "age": calendars.age(provider, birth, now), "when": when})
        except calendars.CalendarError:
            continue
    return out


def build_snapshot(cid: str) -> dict:
    croot = campaigns.campaign_root(cid)
    wroot = _world_root(cid)

    try:
        open_threads = plot.open_threads(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json
        open_threads = []

    recent = chronicle.recent(cid, 1)
    now = recent[-1].get("date", "") if recent else ""

    friendly, holidays_today, upcoming = "", [], None
    if now:
        try:
            facts = calendars.today_facts(calendars.read_calendar(croot), now)
            friendly, holidays_today, upcoming = facts["friendly"], facts["holidays_today"], facts["upcoming"]
        except (calendars.CalendarError, KeyError):
            pass

    recent_ids = _recent_char_ids(cid)
    absent_cast = []
    for a in appearances.roster(cid):
        if a["kind"] != "characters" or a["role"] != "npc" or a["id"] in recent_ids:
            continue
        b = briefs.read_brief(croot, a["id"])
        absent_cast.append({"name": _char_name(croot, a["id"]),
                            "tagline": (b["tagline"] if b else "") or ""})

    available_cast = [{"token": f"characters:{c['id']}", "name": c.get("name", c["id"])}
                      for c in characters.list_characters(wroot)]
    for a in appearances.roster(cid):
        if a["role"] != "player":
            continue
        try:
            name = (pcs.read_pc(croot, a["id"])["meta"].get("name", a["id"])
                    if a["kind"] == "pcs" else _char_name(croot, a["id"]))
        except pcs.PCNotFound:
            name = a["id"]
        available_cast.append({"token": f"{a['kind']}:{a['id']}", "name": name})

    available_locations = [{"id": e["id"], "name": e.get("name", e["id"])}
                           for e in entities.list_entities(croot, "locations")]

    return {"now": now, "friendly": friendly, "holidays_today": holidays_today,
            "upcoming": upcoming, "birthdays": _birthdays(cid, croot, now),
            "open_threads": open_threads, "absent_cast": absent_cast,
            "available_cast": available_cast, "available_locations": available_locations}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q`
Expected: PASS (2 tests). If `_char`'s meta-writer name was wrong, fix it per Step 3's grep, or drop the birthdate lines (unused by these two tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/suggest.py backend/tests/test_suggest_store.py
git commit -m "feat(suggest): deterministic scene-suggestion snapshot (threads/absent/calendar/ids)"
```

---

### Task 2: `suggest.build_prompt` + `parse_output` — prompt and validated parse

**Files:**
- Modify: `backend/src/grimoire/store/suggest.py`
- Test: `backend/tests/test_suggest_store.py`

**Interfaces:**
- Consumes: `build_snapshot` output; `characters.list_characters`, `appearances.roster`, `entities.list_entities`.
- Produces:
  - `build_prompt(snapshot: dict) -> list[dict]` (system + user messages).
  - `parse_output(text: str, cid: str) -> list[dict]` — `[{title, premise, cast: ["<kind>:<id>"], location: "<id>"}]`, ids validated & unknowns dropped, `[]` on garble.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_suggest_store.py`:

```python
def test_build_prompt_includes_signals():
    snap = {"now": "2026-01-01", "friendly": "Jan 1", "holidays_today": ["New Year"],
            "upcoming": {"name": "Festival", "in_days": 5}, "birthdays": [{"name": "Ann", "age": 30, "when": "today"}],
            "open_threads": [{"id": "the-map", "title": "The map", "status": "open", "latest_beat": "found it"}],
            "absent_cast": [{"name": "Doran", "tagline": "a sellsword"}],
            "available_cast": [{"token": "characters:ann", "name": "Ann"}],
            "available_locations": [{"id": "keep", "name": "The Keep"}]}
    msgs = suggest.build_prompt(snap)
    assert msgs[0]["role"] == "system"
    user = msgs[1]["content"]
    assert "The map" in user and "Doran" in user and "Ann" in user and "The Keep" in user
    assert "New Year" in user and "today" in user


def test_parse_output_validates_ids(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    croot = campaigns.campaign_root(cid)
    wroot = worlds.world_root(campaigns.read_campaign(cid)["meta"]["world"])
    ann = characters.create_character(wroot, "Ann", "main", characters.blank_card("Ann"))[0]
    entities.create_entity(croot, "locations", "The Keep")
    text = ('{"suggestions": ['
            f'{{"title": "T", "premise": "P", "cast": ["characters:{ann}", "characters:ghost"], "location": "the-keep"}},'
            '{"title": "", "premise": "no title", "cast": [], "location": ""},'
            '{"title": "Bad loc", "premise": "P2", "cast": [], "location": "nowhere"}]}')
    out = suggest.parse_output(text, cid)
    assert [s["title"] for s in out] == ["T", "Bad loc"]          # title-less dropped
    assert out[0]["cast"] == [f"characters:{ann}"]                # ghost dropped
    assert out[0]["location"] == "the-keep" and out[1]["location"] == ""  # unknown loc -> ""


def test_parse_output_tolerates_garbage(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    assert suggest.parse_output("not json", cid) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q -k "prompt or parse_output"`
Expected: FAIL — `build_prompt`/`parse_output` undefined.

- [ ] **Step 3: Implement `build_prompt` + `parse_output`**

Append to `backend/src/grimoire/store/suggest.py`:

```python
INSTRUCTION = (
    "You help a game master start the next scene of a role-play campaign. Given the "
    "current situation below, propose 3-4 DISTINCT scene openings that each advance an "
    "open plot thread, revisit a long-absent character, or land on an upcoming date or "
    "birthday. Reply with ONLY a JSON object with key \"suggestions\": a list of "
    '{"title" (a short label), "premise" (2-3 sentences the GM can open on), '
    '"cast" (list of "<kind>:<id>" tokens chosen ONLY from the available cast below), '
    '"location" (one location id from the available locations, or "")}. Use only the ids '
    "given; do not invent ids."
)


def _render_snapshot(s: dict) -> str:
    parts: list[str] = []
    if s["now"]:
        when = s["friendly"] or s["now"]
        line = f"Current date: {when}."
        if s["holidays_today"]:
            line += " Today: " + ", ".join(s["holidays_today"]) + "."
        if s["upcoming"]:
            line += f" Upcoming: {s['upcoming']['name']} in {s['upcoming']['in_days']} days."
        parts.append(line)
    if s["birthdays"]:
        parts.append("Birthdays: " + "; ".join(
            f"{b['name']} (age {b['age']}) {b['when']}" for b in s["birthdays"]))
    if s["open_threads"]:
        parts.append("Open plot threads:\n" + "\n".join(
            f"- {t['title']} ({t['status']}): {t['latest_beat']}".rstrip(": ") for t in s["open_threads"]))
    if s["absent_cast"]:
        parts.append("Long-absent characters:\n" + "\n".join(
            f"- {a['name']}: {a['tagline']}".rstrip(": ") for a in s["absent_cast"]))
    if s["available_cast"]:
        parts.append("Available cast (use these tokens):\n" + "\n".join(
            f"- {c['token']} = {c['name']}" for c in s["available_cast"]))
    if s["available_locations"]:
        parts.append("Available locations (use these ids):\n" + "\n".join(
            f"- {loc['id']} = {loc['name']}" for loc in s["available_locations"]))
    return "\n\n".join(parts) if parts else "No campaign history yet; propose fresh openings."


def build_prompt(snapshot: dict) -> list[dict]:
    return [{"role": "system", "content": INSTRUCTION},
            {"role": "user", "content": _render_snapshot(snapshot)}]


def _valid_ids(cid: str):
    croot = campaigns.campaign_root(cid)
    wroot = _world_root(cid)
    char_ids = {c["id"] for c in characters.list_characters(wroot)}
    player_tokens = {f"{a['kind']}:{a['id']}" for a in appearances.roster(cid) if a["role"] == "player"}
    loc_ids = {e["id"] for e in entities.list_entities(croot, "locations")}
    return char_ids, player_tokens, loc_ids


def parse_output(text: str, cid: str) -> list[dict]:
    start, end = text.find("{"), text.rfind("}")
    try:
        obj = json.loads(text[start:end + 1]) if start != -1 and end > start else {}
    except (json.JSONDecodeError, TypeError):
        obj = {}
    if not isinstance(obj, dict):
        return []
    char_ids, player_tokens, loc_ids = _valid_ids(cid)

    def _valid_token(tok: str) -> bool:
        kind, _, aid = tok.partition(":")
        return (kind == "characters" and aid in char_ids) or tok in player_tokens

    out: list[dict] = []
    for e in obj.get("suggestions", []):
        if not isinstance(e, dict):
            continue
        title, premise = str(e.get("title", "")).strip(), str(e.get("premise", "")).strip()
        if not title or not premise:
            continue
        raw_cast = e.get("cast", [])
        cast = ([t for t in (str(x).strip() for x in raw_cast) if _valid_token(t)]
                if isinstance(raw_cast, list) else [])
        loc = str(e.get("location", "")).strip()
        out.append({"title": title, "premise": premise, "cast": cast,
                    "location": loc if loc in loc_ids else ""})
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q`
Expected: PASS (all suggest tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/suggest.py backend/tests/test_suggest_store.py
git commit -m "feat(suggest): one-shot prompt + id-validated parse of scene openings"
```

---

### Task 3: Route — `POST /campaigns/{cid}/scene-suggestions`

**Files:**
- Modify: `backend/src/grimoire/routes.py` (new endpoint near `post_scene`)
- Test: `backend/tests/test_routes_suggestions.py` (new)

**Interfaces:**
- Consumes: `suggest.build_snapshot`, `suggest.build_prompt`, `suggest.parse_output`; `client.complete`; `_require_key`.
- Produces: `POST /campaigns/{cid}/scene-suggestions` → `{"suggestions": [{title, premise, cast: [{kind,id,name}], location: {id,name}|null}]}`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_routes_suggestions.py` (mirror an existing route test's client/mocking setup — inspect one first):

Run: `grep -rln "OpenRouterClient\|get_openrouter\|def test.*absorb\|client.complete" backend/tests | head`

Then write, using the same app/client-override pattern that file uses:

```python
# Pattern: build a FastAPI TestClient with get_openrouter overridden to a stub whose
# .complete returns a canned JSON string; set an API key in config so _require_key passes.
# (Copy the fixture/override style from the existing absorb/opener route test.)

def test_scene_suggestions_returns_resolved(client_with_stub, campaign_with_key):
    cid, ann_id = campaign_with_key  # a world character "Ann" exists
    stub_json = ('{"suggestions": [{"title": "T", "premise": "P",'
                 f' "cast": ["characters:{ann_id}"], "location": ""}}]}}')
    client_with_stub.set_completion(stub_json)
    r = client_with_stub.post(f"/api/campaigns/{cid}/scene-suggestions")
    assert r.status_code == 200
    body = r.json()
    assert body["suggestions"][0]["title"] == "T"
    assert body["suggestions"][0]["cast"][0] == {"kind": "characters", "id": ann_id, "name": "Ann"}
    assert body["suggestions"][0]["location"] is None


def test_scene_suggestions_requires_key(client_no_key, campaign_id):
    r = client_no_key.post(f"/api/campaigns/{campaign_id}/scene-suggestions")
    assert r.status_code == 409
```

If the existing route tests use plain helper functions rather than fixtures, replicate that exact style instead (the two behaviors to assert are unchanged: resolved 200 body, and 409 without a key).

- [ ] **Step 2: Run the test to verify it fails**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes_suggestions.py -q`
Expected: FAIL — 404 (route absent) or import error.

- [ ] **Step 3: Add the endpoint**

In `backend/src/grimoire/routes.py`, add after `post_scene` (the `POST /campaigns/{cid}/scenes` handler):

```python
def _resolve_cast(cid: str, tokens: list[str]) -> list[dict]:
    croot = store.campaigns.campaign_root(cid)
    out = []
    for tok in tokens:
        kind, _, aid = tok.partition(":")
        try:
            if kind == "pcs":
                name = store.pcs.read_pc(croot, aid)["meta"].get("name", aid)
            else:
                name = store.characters.read_character(croot, aid)["meta"].get("name", aid)
        except (store.characters.CharacterNotFound, store.pcs.PCNotFound):
            name = aid
        out.append({"kind": kind, "id": aid, "name": name})
    return out


@router.post("/campaigns/{cid}/scene-suggestions")
async def post_scene_suggestions(cid: str, client: OpenRouterClient = Depends(get_openrouter)):
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    cfg = store.read_config()
    _require_key(cfg)
    snapshot = store.suggest.build_snapshot(cid)
    messages = store.suggest.build_prompt(snapshot)
    try:
        text = await client.complete(messages, cfg["model"], cfg["openrouter_key"])
    except OpenRouterError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    croot = store.campaigns.campaign_root(cid)
    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.entities.list_entities(croot, "locations")}
    out = []
    for s in store.suggest.parse_output(text, cid):
        loc = {"id": s["location"], "name": loc_names.get(s["location"], s["location"])} if s["location"] else None
        out.append({"title": s["title"], "premise": s["premise"],
                    "cast": _resolve_cast(cid, s["cast"]), "location": loc})
    return {"suggestions": out}
```

Ensure `store.suggest` is importable — add `suggest` to the `from .store import (...)`/`import` surface if `store/__init__.py` enumerates modules (check with `grep -n "suggest\|^from .store\|import" backend/src/grimoire/store/__init__.py`); the sibling `store.absorb` access shows the pattern.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend/tests/test_routes_suggestions.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/routes.py backend/tests/test_routes_suggestions.py
git commit -m "feat(routes): POST scene-suggestions (key-gated, name-resolved)"
```

---

### Task 4: Frontend API client — `sceneSuggestions` + type

**Files:**
- Modify: `frontend/src/api/client.ts`

**Interfaces:**
- Produces: `SceneSuggestion` type and `api.sceneSuggestions(cid)`.

- [ ] **Step 1: Add the type and method**

In `frontend/src/api/client.ts`, add the type near `SceneAbsorb`:

```ts
export type SceneSuggestion = {
  title: string; premise: string;
  cast: { kind: string; id: string; name: string }[];
  location: { id: string; name: string } | null;
};
```

and add to the `api` object (near `getScene`):

```ts
  sceneSuggestions: (cid: string) =>
    request<{ suggestions: SceneSuggestion[] }>("POST", `/api/campaigns/${cid}/scene-suggestions`),
```

- [ ] **Step 2: Verify types compile**

Run (from `frontend/`): `npx tsc -b`
Expected: exit 0.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/client.ts
git commit -m "feat(api): sceneSuggestions client + SceneSuggestion type"
```

---

### Task 5: `CastPanel` — "Suggest scenes" UI + auto-seed on pick

**Files:**
- Modify: `frontend/src/components/CastPanel.tsx`
- Test: `frontend/src/components/CastPanel.test.tsx` (add a test; create if absent)

**Interfaces:**
- Consumes: `api.sceneSuggestions`, `api.addToCast`, `api.setSceneLocation`, the panel's `setPrompt`/`onSeeded`.

- [ ] **Step 1: Write the failing test**

Inspect the existing CastPanel test setup first:

Run: `sed -n '1,40p' frontend/src/components/CastPanel.test.tsx 2>/dev/null || echo "NO TEST FILE"`

Add a test mirroring that file's render/mocks (or, if absent, mirror `GreetingEditor.test.tsx`'s structure). The behavior to assert:

```ts
test("Suggest scenes fetches, renders, and a pick auto-seeds + prefills the prompt", async () => {
  (api.sceneSuggestions as any).mockResolvedValue({ suggestions: [
    { title: "The creditor", premise: "A debt-collector arrives.",
      cast: [{ kind: "characters", id: "doran", name: "Doran" }],
      location: { id: "keep", name: "The Keep" } }] });
  (api.addToCast as any).mockResolvedValue({ ok: true });
  (api.setSceneLocation as any).mockResolvedValue({ ok: true });
  // render CastPanel with sceneEmpty + keySet, then:
  fireEvent.click(screen.getByRole("button", { name: /Suggest scenes/ }));
  await screen.findByText("The creditor");
  fireEvent.click(screen.getByRole("button", { name: /Use this scene/ }));
  await waitFor(() => {
    expect(api.addToCast).toHaveBeenCalledWith("c", "s", { kind: "characters", id: "doran" });
    expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s", "keep");
  });
  expect((screen.getByLabelText("Opener prompt") as HTMLInputElement).value).toBe("A debt-collector arrives.");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run (from `frontend/`): `npx vitest run src/components/CastPanel.test.tsx`
Expected: FAIL — no "Suggest scenes" button.

- [ ] **Step 3: Implement the UI + auto-seed**

In `frontend/src/components/CastPanel.tsx`, add state near the other `useState`s:

```ts
  const [suggestions, setSuggestions] = useState<import("../api/client").SceneSuggestion[]>([]);
```

Add the fetch + pick handlers (near `generate`):

```ts
  async function suggestScenes() {
    setBusy(true);
    setError(null);
    try {
      const r = await api.sceneSuggestions(cid);
      setSuggestions(r.suggestions);
    } catch (err: any) {
      setError(err.detail ?? String(err));
    } finally {
      setBusy(false);
    }
  }

  async function useSuggestion(s: import("../api/client").SceneSuggestion) {
    setBusy(true);
    try {
      for (const c of s.cast) {
        try { await api.addToCast(cid, sid, { kind: c.kind, id: c.id }); } catch { /* already present */ }
      }
      if (s.location) await api.setSceneLocation(cid, sid, s.location.id);
      setPrompt(s.premise);
      setSuggestions([]);
      onSeeded();
    } finally {
      setBusy(false);
    }
  }
```

Render a suggestions block inside the empty-scene section, just above the "Generate an opener" block:

```tsx
      <div className="suggest-scenes">
        <button className="subtle" onClick={suggestScenes} disabled={!keySet || busy}>Suggest scenes</button>
        {suggestions.map((s, i) => (
          <div className="suggestion" key={i}>
            <div className="suggestion-title">{s.title}</div>
            <div className="suggestion-premise">{s.premise}</div>
            <div className="field-hint">
              {s.cast.map((c) => c.name).join(", ")}
              {s.location ? ` · ${s.location.name}` : ""}
            </div>
            <button className="primary" onClick={() => useSuggestion(s)} disabled={busy}>Use this scene</button>
          </div>
        ))}
      </div>
```

- [ ] **Step 4: Run the frontend checks**

Run (from `frontend/`): `npx vitest run src/components/CastPanel.test.tsx` then `npx tsc -b`
Expected: PASS; `tsc` exits 0.

- [ ] **Step 5: Full frontend suite + commit**

Run (from `frontend/`): `npx vitest run`
Expected: PASS (baseline + 1).

```bash
git add frontend/src/components/CastPanel.tsx frontend/src/components/CastPanel.test.tsx
git commit -m "feat(cast-panel): Suggest scenes — fetch, render, auto-seed on pick"
```

---

### Task 6: Full-suite verification

- [ ] **Step 1: Backend suite**

Run: `C:/Users/charl/github/grimoire/backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS, ≥ the 458 baseline + new suggest/route tests. Fix any regression's cause.

- [ ] **Step 2: Frontend suite + types**

Run (from `frontend/`): `npx vitest run` and `npx tsc -b`
Expected: PASS; `tsc` exits 0.

---

## Self-Review

**Spec coverage:**
- Ephemeral, non-streaming, campaign-level endpoint → Task 3. ✓
- "Now" = latest chronicled scene + calendar/birthday facts → Task 1 (`build_snapshot`/`_birthdays`). ✓
- Seedable ids drawn wide (world chars / roster players / campaign locations), validated → Tasks 1 (available_*) + 2 (`parse_output`/`_valid_ids`). ✓
- Open threads + long-absent cast signals → Task 1. ✓
- Prompt + tolerant structured parse → Task 2. ✓
- Auto-seed on client via existing endpoints + prompt prefill → Task 5. ✓
- API client type/method → Task 4. ✓
- Tolerance (empty/garbled sources) → Task 1 (per-piece guards) + Task 2 (`[]` on garble). ✓

**Placeholder scan:** Tasks 3 and 5 reference existing test-setup files to mirror (route-test fixture style; CastPanel test harness) with the exact grep/sed to locate them and the concrete behaviors to assert — the one unavoidable lookup, since those harnesses already exist and must be matched rather than reinvented. No TBD/TODO in product code steps.

**Type consistency:** `build_snapshot` dict keys are consumed verbatim by `_render_snapshot` (Task 2) and the route (Task 3); `parse_output(text, cid)` shape `{title,premise,cast,location}` is consumed by the route's resolve loop; `SceneSuggestion` (Task 4) matches the route's response (`cast:{kind,id,name}[]`, `location:{id,name}|null`) and is consumed by Task 5. `RECENT_WINDOW` defined once in Task 1.

## Notes for the executor

- `build_snapshot` pieces are each independently tolerant; do not wrap the whole thing in one try/except (that would hide a real bug). The route does not guard `build_snapshot`/`parse_output` beyond the LLM call — they must not raise on normal (empty) campaigns, which Task 1/2 tests assert.
- Auto-seed tolerates a duplicate `addToCast` (already-present actor → the per-call `try/catch` in `useSuggestion` swallows the 409); do not fail the whole pick on one.
- Only existing ids are seedable by construction (`parse_output` drops unknowns), so the client never posts a bogus cast/location id.
