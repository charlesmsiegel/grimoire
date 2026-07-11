# Scene-suggestion Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ground next-scene suggestions so the model stops assuming absent characters are present and mis-gendering them, by feeding per-character tagline + presence status, a story-so-far anchor, and per-thread dormancy.

**Architecture:** All changes are deterministic assembly in `store/suggest.py` plus the Jinja templates that render it. `build_snapshot` unifies the old `absent_cast`/`available_cast` into one status-annotated `cast` list, adds `story_so_far`, and annotates each open thread with `dormancy`. The two templates render the richer snapshot and reframe the instruction. Single LLM call — `parse_output` and the route are untouched.

**Tech Stack:** Python 3 / FastAPI backend, pytest; Jinja2 prompt templates rendered via `grimoire.prompts.render`.

## Global Constraints

- Single LLM call — no new model calls, no change to `parse_output`/route shape.
- Cast/location validation stays id-based in `parse_output`; annotating the list must not change which ids validate.
- Gender is carried implicitly by the tagline; **no** structured gender field is added.
- Taglines come from `taglines.read(croot, id)` (tolerant of a missing file → `""`).
- Backend tests run: `backend/.venv/Scripts/python.exe -m pytest backend -q`.
- End commit messages with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## File Structure

- `backend/src/grimoire/store/plot.py` — `open_threads()` gains `last_scene` in each entry (needed to compute dormancy).
- `backend/src/grimoire/store/suggest.py` — `build_snapshot()` rewritten: unified `cast`, `story_so_far`, thread `dormancy`; dead `_recent_char_ids`/`RECENT_WINDOW` removed.
- `templates/scene_suggestions/user.j2` — renders story-so-far, grouped cast, dormancy; drops the two old cast sections.
- `templates/scene_suggestions/instruction/standard.j2` & `offscreen.j2` — reframed goal + presence/gender discipline.
- `backend/tests/test_suggest_store.py` — updated + new coverage.

---

## Task 1: Snapshot assembly — unified cast, story-so-far, thread dormancy

**Files:**
- Modify: `backend/src/grimoire/store/plot.py` (`open_threads`, ~line 67-75)
- Modify: `backend/src/grimoire/store/suggest.py` (`RECENT_WINDOW`, `_recent_char_ids`, `build_snapshot`, ~lines 16-133)
- Test: `backend/tests/test_suggest_store.py`

**Interfaces:**
- Consumes: `chronicle.recent(cid, n)` (ascending, newest last), `chronicle.read_chronicle(cid)` (dict keyed by scene id), `appearances.roster(cid)` (`[{kind,id,version,role,scenes}]`), `characters.list_characters(croot)`, `taglines.read(croot, id)`, `plot.open_threads(cid)`.
- Produces: `build_snapshot(cid, offscreen=False) -> dict` with keys `now, friendly, holidays_today, upcoming, birthdays, story_so_far, open_threads, cast, available_locations`.
  - `story_so_far`: `list[{one_line, location, date}]`, newest first, ≤3.
  - `open_threads`: each entry `{id, title, status, last_scene, latest_beat, dormancy}` where `dormancy: int` = scenes after `last_scene` (0 = advanced in the most recent scene).
  - `cast`: `list[{token, name, tagline, status, role}]` where `status ∈ {"present","appeared","unseen"}` and `role ∈ {"npc","player"}`. `present` = in the most recent scene's cast; `appeared` = has an appearance record but not present; `unseen` = no appearance record.

- [ ] **Step 1: Update the existing snapshot tests to the new shape (failing tests first)**

In `backend/tests/test_suggest_store.py`, replace `test_build_snapshot_gathers_signals`, `test_build_snapshot_tolerates_empty_campaign`, and `test_build_snapshot_dedupes_available_cast` with:

```python
def test_build_snapshot_classifies_cast_and_annotates_threads(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    absent = _char(wroot, "Doran")        # appears in s1 but not in s1's chronicle cast
    present = _char(wroot, "Seraphine")   # in the most recent scene's cast
    unseen = _char(wroot, "Mira")         # never on screen
    taglines.write(wroot, absent, "a quiet sellsword")   # seeded before the fork
    taglines.write(wroot, unseen, "a wandering oracle")
    cid = campaigns.create_campaign("Run", wid)
    s1 = scenes.create_scene(cid, "One")
    appearances.appear(cid, s1, "characters", absent, "main", "npc")
    appearances.appear(cid, s1, "characters", present, "main", "npc")
    chronicle.absorb(cid, {"id": s1, "one_line": "They gathered at dusk.", "summary": "y",
                           "keywords": [], "cast": [f"characters/{present}"],
                           "location": "The Hall", "date": "2026-01-02"})
    plot.set_movement(cid, "the-map", "The map", "advanced", "It is a forgery.", s1)

    snap = suggest.build_snapshot(cid)
    by_name = {c["name"]: c for c in snap["cast"]}
    assert by_name["Seraphine"]["status"] == "present"
    assert by_name["Doran"]["status"] == "appeared" and by_name["Doran"]["tagline"] == "a quiet sellsword"
    assert by_name["Mira"]["status"] == "unseen" and by_name["Mira"]["tagline"] == "a wandering oracle"
    assert [t["title"] for t in snap["open_threads"]] == ["The map"]
    assert snap["open_threads"][0]["dormancy"] == 0            # advanced in the most recent scene
    assert snap["story_so_far"][0]["one_line"] == "They gathered at dusk."
    assert snap["story_so_far"][0]["location"] == "The Hall"


def test_build_snapshot_tolerates_empty_campaign(monkeypatch, tmp_path):
    cid = _campaign(monkeypatch, tmp_path)
    snap = suggest.build_snapshot(cid)  # no scenes/chronicle/plot/calendar
    assert snap["open_threads"] == [] and snap["cast"] == []
    assert snap["story_so_far"] == []
    assert snap["now"] == "" and snap["birthdays"] == []


def test_build_snapshot_dedupes_cast(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    hero = _char(wroot, "Hero")
    cid = campaigns.create_campaign("Run", wid)
    s1 = scenes.create_scene(cid, "One")
    appearances.appear(cid, s1, "characters", hero, "main", "player")  # campaign char AND roster player
    cast = suggest.build_snapshot(cid)["cast"]
    tokens = [c["token"] for c in cast]
    assert tokens.count(f"characters:{hero}") == 1                    # listed once, not duplicated
    assert next(c for c in cast if c["token"] == f"characters:{hero}")["role"] == "player"
```

- [ ] **Step 2: Run the updated tests to verify they fail**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q -k "classifies_cast or tolerates_empty or dedupes_cast"`
Expected: FAIL — `KeyError: 'cast'` / `KeyError: 'story_so_far'` (snapshot still emits `absent_cast`/`available_cast`).

- [ ] **Step 3: Add `last_scene` to `plot.open_threads`**

In `backend/src/grimoire/store/plot.py`, in `open_threads`, add `last_scene` to each dict:

```python
def open_threads(cid: str) -> list[dict]:
    items = [(pid, t) for pid, t in read(cid).items() if t.get("status") != "closed"]
    items.sort(key=lambda kt: (kt[1].get("last_scene", ""), kt[0]))
    out = []
    for pid, t in items:
        beats = t.get("beats") or []
        out.append({"id": pid, "title": t.get("title", pid), "status": t.get("status", "open"),
                    "last_scene": t.get("last_scene", ""),
                    "latest_beat": beats[-1]["text"] if beats else ""})
    return out
```

- [ ] **Step 4: Rewrite `build_snapshot` and remove dead helpers in `suggest.py`**

In `backend/src/grimoire/store/suggest.py`: delete the `RECENT_WINDOW = 5` constant and the entire `_recent_char_ids` function (both become unused). Add a `_tok` helper and replace `build_snapshot` with:

```python
def _tok(ref: str) -> str:
    kind, _, aid = str(ref).partition("/")
    return f"{kind}:{aid}"


def build_snapshot(cid: str, offscreen: bool = False) -> dict:
    croot = campaigns.campaign_root(cid)
    roster = appearances.roster(cid)

    try:
        open_threads = plot.open_threads(cid)
    except Exception:  # noqa: BLE001 — garbled plot.json
        open_threads = []

    try:
        recent = chronicle.recent(cid, 3)
    except Exception:  # noqa: BLE001 — garbled chronicle.json
        recent = []
    now = recent[-1].get("date", "") if recent else ""
    story_so_far = [{"one_line": r.get("one_line", ""), "location": r.get("location", ""),
                     "date": r.get("date", "")} for r in reversed(recent)]

    try:
        scene_ids = sorted(chronicle.read_chronicle(cid).keys())
    except Exception:  # noqa: BLE001 — garbled chronicle.json
        scene_ids = []

    def _dormancy(last_scene: str) -> int:
        if last_scene and last_scene in scene_ids:
            return len(scene_ids) - 1 - scene_ids.index(last_scene)
        return len(scene_ids)  # unknown/missing -> maximally cold

    for t in open_threads:
        t["dormancy"] = _dormancy(t.get("last_scene", ""))

    friendly, holidays_today, upcoming = "", [], None
    if now:
        try:
            facts = calendars.today_facts(calendars.read_calendar(croot), now)
            friendly, holidays_today, upcoming = facts["friendly"], facts["holidays_today"], facts["upcoming"]
        except (calendars.CalendarError, KeyError):
            pass

    present = {_tok(ref) for ref in (recent[-1].get("cast") or [])} if recent else set()
    roster_tokens = {f"{a['kind']}:{a['id']}" for a in roster}
    player_tokens = {f"{a['kind']}:{a['id']}" for a in roster if a["role"] == "player"}

    def _status(tok: str) -> str:
        if tok in present:
            return "present"
        if tok in roster_tokens:
            return "appeared"
        return "unseen"

    cast, seen = [], set()
    for c in characters.list_characters(croot):
        tok = f"characters:{c['id']}"
        seen.add(tok)
        cast.append({"token": tok, "name": c.get("name", c["id"]),
                     "tagline": taglines.read(croot, c["id"]),
                     "status": _status(tok),
                     "role": "player" if tok in player_tokens else "npc"})
    if not offscreen:  # offscreen scenes never cast the player
        for a in roster:
            if a["role"] != "player":
                continue
            tok = f"{a['kind']}:{a['id']}"
            if tok in seen:
                continue
            seen.add(tok)
            try:
                name = (pcs.read_pc(croot, a["id"])["meta"].get("name", a["id"])
                        if a["kind"] == "pcs" else _char_name(croot, a["id"]))
            except pcs.PCNotFound:
                name = a["id"]
            cast.append({"token": tok, "name": name, "tagline": "",
                         "status": _status(tok), "role": "player"})

    available_locations = [{"id": e["id"], "name": e.get("name", e["id"])}
                           for e in entities.list_entities(croot, "locations")]

    return {"now": now, "friendly": friendly, "holidays_today": holidays_today,
            "upcoming": upcoming, "birthdays": _birthdays(croot, now, roster),
            "story_so_far": story_so_far, "open_threads": open_threads,
            "cast": cast, "available_locations": available_locations}
```

- [ ] **Step 5: Run the updated snapshot tests to verify they pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q -k "classifies_cast or tolerates_empty or dedupes_cast or garbled"`
Expected: PASS (the `test_build_snapshot_tolerates_garbled_chronicle` test still passes — `now == ""`).

- [ ] **Step 6: Commit**

```bash
git add backend/src/grimoire/store/plot.py backend/src/grimoire/store/suggest.py backend/tests/test_suggest_store.py
git commit -m "$(cat <<'EOF'
feat(suggest): ground snapshot with cast status, story-so-far, thread dormancy

Unify absent_cast/available_cast into one status-annotated cast list
(present/appeared/unseen, each with its tagline), add a story-so-far
anchor, and annotate open threads with dormancy. Removes the now-dead
_recent_char_ids/RECENT_WINDOW.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Situation template — render story-so-far, grouped cast, dormancy

**Files:**
- Modify: `templates/scene_suggestions/user.j2`
- Test: `backend/tests/test_suggest_store.py`

**Interfaces:**
- Consumes: the `build_snapshot` dict from Task 1 (`story_so_far`, `open_threads[*].dormancy`, `cast[*].{token,name,tagline,status,role}`).
- Produces: no signature change; `build_prompt(snapshot, greeting_candidates=None, offscreen=False)` renders the new sections into the user message.

- [ ] **Step 1: Update the prompt tests to the new snapshot shape (failing first)**

In `backend/tests/test_suggest_store.py`, replace `test_build_prompt_includes_signals` and fix the hand-built snapshot in `test_build_prompt_requests_dates_only_with_a_current_date`:

```python
def test_build_prompt_includes_signals():
    snap = {"now": "2026-01-01", "friendly": "Jan 1", "holidays_today": ["New Year"],
            "upcoming": {"name": "Festival", "in_days": 5},
            "birthdays": [{"name": "Ann", "age": 30, "when": "today"}],
            "story_so_far": [{"one_line": "They met at the keep.", "location": "The Keep", "date": "2026-01-01"}],
            "open_threads": [{"id": "the-map", "title": "The map", "status": "open",
                              "latest_beat": "found it", "dormancy": 2}],
            "cast": [{"token": "characters:ann", "name": "Ann", "tagline": "a healer",
                      "status": "present", "role": "npc"},
                     {"token": "characters:doran", "name": "Doran", "tagline": "a sellsword",
                      "status": "unseen", "role": "npc"}],
            "available_locations": [{"id": "keep", "name": "The Keep"}]}
    user = suggest.build_prompt(snap)[1]["content"]
    assert "The map" in user and "cold — 2 scenes" in user
    assert "Ann" in user and "a healer" in user
    assert "Doran" in user and "Not yet appeared" in user
    assert "The Keep" in user and "New Year" in user and "today" in user
    assert "They met at the keep." in user
```

And in `test_build_prompt_requests_dates_only_with_a_current_date`, change the snapshot's cast keys:

```python
    snap = {"now": "2026-01-01", "friendly": "Jan 1", "holidays_today": [], "upcoming": None,
            "birthdays": [], "open_threads": [], "story_so_far": [],
            "cast": [], "available_locations": []}
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py::test_build_prompt_includes_signals -q`
Expected: FAIL — `"cold — 2 scenes"` / `"Not yet appeared"` not in the rendered user message (template still renders the old sections).

- [ ] **Step 3: Rewrite the cast/thread sections of `user.j2`**

In `templates/scene_suggestions/user.j2`, update the docstring var list to reflect `story_so_far`, `open_threads` (with `dormancy`), and `cast` (with `status`/`role`/`tagline`). Replace the **Open plot threads** block (currently lines ~20-24), and the **absent_cast** + **available_cast** blocks (currently ~25-34), with the following. Insert the **Story so far** block as the first `parts.append` (right after `{%- set parts = [] -%}`):

```jinja2
{%- if s.story_so_far -%}
{%- set lines = [] -%}
{%- for r in s.story_so_far -%}
{%- set tail = [] -%}
{%- if r.location -%}{%- set _ = tail.append(r.location) -%}{%- endif -%}
{%- if r.date -%}{%- set _ = tail.append(r.date) -%}{%- endif -%}
{%- set suffix = (" (" ~ (tail | join(", ")) ~ ")") if tail else "" -%}
{%- set _ = lines.append("- " ~ r.one_line ~ suffix) -%}
{%- endfor -%}
{%- set _ = parts.append("Story so far (most recent first):\n" ~ (lines | join("\n"))) -%}
{%- endif -%}
```

Open plot threads (replaces the existing threads block) — dormancy phrase in the parens:

```jinja2
{%- if s.open_threads -%}
{%- set lines = [] -%}
{%- for t in s.open_threads -%}
{%- if t.dormancy is defined and t.dormancy == 0 -%}{%- set dz = "advanced last scene" -%}
{%- elif t.dormancy is defined and t.dormancy == 1 -%}{%- set dz = "cold — 1 scene" -%}
{%- elif t.dormancy is defined -%}{%- set dz = "cold — " ~ t.dormancy ~ " scenes" -%}
{%- else -%}{%- set dz = t.status -%}{%- endif -%}
{%- set _ = lines.append(("- " ~ t.title ~ " (" ~ dz ~ "): " ~ t.latest_beat).rstrip(": ")) -%}
{%- endfor -%}
{%- set _ = parts.append("Open plot threads (cold threads are especially worth reviving):\n" ~ (lines | join("\n"))) -%}
{%- endif -%}
```

Cast, grouped by status (replaces both the absent_cast and available_cast blocks):

```jinja2
{%- if s.cast -%}
{%- set present = [] -%}{%- set offstage = [] -%}{%- set unseen = [] -%}
{%- for c in s.cast -%}
{%- set label = c.token ~ " = " ~ c.name ~ (" (the player character)" if c.role == "player" else ((" — " ~ c.tagline) if c.tagline else "")) -%}
{%- if c.status == "present" -%}{%- set _ = present.append(label) -%}
{%- elif c.status == "appeared" -%}{%- set _ = offstage.append(label) -%}
{%- else -%}{%- set _ = unseen.append(label) -%}{%- endif -%}
{%- endfor -%}
{%- set blocks = [] -%}
{%- if present -%}{%- set _ = blocks.append("In the most recent scene (present):\n- " ~ (present | join("\n- "))) -%}{%- endif -%}
{%- if offstage -%}{%- set _ = blocks.append("Appeared earlier, now offstage:\n- " ~ (offstage | join("\n- "))) -%}{%- endif -%}
{%- if unseen -%}{%- set _ = blocks.append("Not yet appeared — introduce only with an in-world reason:\n- " ~ (unseen | join("\n- "))) -%}{%- endif -%}
{%- set _ = parts.append("Available cast (use these tokens):\n" ~ (blocks | join("\n"))) -%}
{%- endif -%}
```

Leave the calendar/birthday blocks and the trailing greeting-candidates block unchanged.

- [ ] **Step 4: Run to verify pass**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q -k "includes_signals or requests_dates or lists_greeting"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add templates/scene_suggestions/user.j2 backend/tests/test_suggest_store.py
git commit -m "$(cat <<'EOF'
feat(suggest): render story-so-far, status-grouped cast, thread dormancy

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Instruction reframe — presence + gender discipline

**Files:**
- Modify: `templates/scene_suggestions/instruction/standard.j2`
- Modify: `templates/scene_suggestions/instruction/offscreen.j2`
- Test: `backend/tests/test_suggest_store.py`

**Interfaces:**
- Consumes: nothing new (static instruction text selected by `offscreen`).
- Produces: system-message text that forbids assuming presence and directs the model to respect taglines/gender and revive cold threads.

- [ ] **Step 1: Write the failing instruction tests**

Add to `backend/tests/test_suggest_store.py`:

```python
def test_standard_instruction_enforces_presence_and_gender():
    snap = {"now": "", "friendly": "", "holidays_today": [], "upcoming": None, "birthdays": [],
            "story_so_far": [], "open_threads": [], "cast": [], "available_locations": []}
    system = suggest.build_prompt(snap)[0]["content"]
    assert "Never assume a character is present" in system
    assert "gender" in system and "reviving" in system


def test_offscreen_instruction_keeps_presence_discipline():
    snap = {"now": "", "friendly": "", "holidays_today": [], "upcoming": None, "birthdays": [],
            "story_so_far": [], "open_threads": [], "cast": [], "available_locations": []}
    system = suggest.build_prompt(snap, offscreen=True)[0]["content"]
    assert "OFFSCREEN" in system
    assert "Never include the player character" in system
    assert "Do not assume a character is present" in system
```

- [ ] **Step 2: Run to verify failure**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q -k "instruction"`
Expected: FAIL — `"Never assume a character is present"` not in the current instruction text.

- [ ] **Step 3: Rewrite `standard.j2`**

Replace the full contents of `templates/scene_suggestions/instruction/standard.j2` with:

```jinja2
You help a game master start the next scene of a role-play campaign. Given the current situation below, propose 3-4 DISTINCT scene openings. Each should advance an open plot thread (a thread that has gone cold — several scenes without attention — is especially worth reviving), bring an offstage character back into play, or land on an upcoming date or birthday. Never assume a character is present unless they are listed under "present"; an offstage or not-yet-appeared character may enter a scene only with a plausible in-world reason for their arrival. Respect each character's tagline for who they are, including their gender. Reply with ONLY a JSON object with key "suggestions": a list of {"title" (a short label), "premise" (2-3 sentences the GM can open on), "cast" (list of "<kind>:<id>" tokens chosen ONLY from the available cast below), "location" (one location id from the available locations, or "")}. Use only the ids given; do not invent ids.
```

- [ ] **Step 4: Rewrite `offscreen.j2`**

Replace the full contents of `templates/scene_suggestions/instruction/offscreen.j2` with:

```jinja2
You help a game master write OFFSCREEN scenes for a role-play campaign — scenes that happen away from the player character, showing what NPCs do, plan, and want when the player is not there. Given the current situation below, propose 3-4 DISTINCT offscreen scene openings. Each should advance an open plot thread (a thread that has gone cold — several scenes without attention — is especially worth reviving), reveal an NPC's motivations, or land on an upcoming date or birthday. Never include the player character in the cast. Do not assume a character is present unless they are listed under "present"; an offstage or not-yet-appeared NPC may enter a scene only with a plausible in-world reason for their arrival. Respect each character's tagline for who they are, including their gender. Reply with ONLY a JSON object with key "suggestions": a list of {"title" (a short label), "premise" (2-3 sentences the GM can open on), "cast" (list of "<kind>:<id>" tokens chosen ONLY from the available cast below), "location" (one location id from the available locations, or "")}. Use only the ids given; do not invent ids.
```

- [ ] **Step 5: Run the instruction tests, then the full suggest suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend/tests/test_suggest_store.py -q`
Expected: PASS (all tests in the file).

- [ ] **Step 6: Commit**

```bash
git add templates/scene_suggestions/instruction/standard.j2 templates/scene_suggestions/instruction/offscreen.j2 backend/tests/test_suggest_store.py
git commit -m "$(cat <<'EOF'
feat(suggest): reframe instruction — presence + gender discipline

Stop pushing the model toward unknown characters; forbid assuming
presence and direct it to respect taglines/gender and revive cold threads.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Full regression + real-campaign smoke check

**Files:** none (verification only).

- [ ] **Step 1: Run the full backend suite**

Run: `backend/.venv/Scripts/python.exe -m pytest backend -q`
Expected: PASS (no other module reads `absent_cast`/`available_cast` from `build_snapshot`; `verify_templates.py`-style rendering still succeeds).

- [ ] **Step 2: Smoke the real snapshot for hollow-manor (manual, read-only)**

Run:
```bash
cd backend && ./.venv/Scripts/python.exe -c "import json; from grimoire.store import suggest; s=suggest.build_snapshot('hollow-manor'); print(json.dumps({'story_so_far': s['story_so_far'], 'threads': [(t['title'], t['dormancy']) for t in s['open_threads']], 'marisol': next(c for c in s['cast'] if c['token']=='characters:marisol')}, ensure_ascii=False, indent=2)); print(suggest.build_prompt(s)[1]['content'][:1200])"
```
Expected: Marisol shows `status: "unseen"` with her tagline (which names her as a woman); the scene-1 one-liner appears under "Story so far"; threads carry a dormancy count. Confirms the hollow-manor scene-2 failure is grounded away. (Read-only — does not write to the store.)

- [ ] **Step 3: No commit** — verification task; nothing to record beyond Tasks 1-3.

---

## Self-Review

**Spec coverage:**
- Tagline + presence status per castable character → Task 1 (`cast` with `status`/`tagline`), Task 2 (grouped render). ✓
- Story-so-far anchor → Task 1 (`story_so_far`), Task 2 (render). ✓
- Per-thread dormancy → Task 1 (`plot.open_threads` + `_dormancy`), Task 2 (render). ✓
- Instruction reframed (presence + gender, no "revisit long-absent") → Task 3. ✓
- Merge `absent_cast`+`available_cast` → Task 1 (unified `cast`; old keys removed). ✓
- Non-goals (groups, gender field, relationships, second call) → untouched. ✓
- `parse_output` unchanged; validation stays id-based → confirmed (no task edits it). ✓

**Placeholder scan:** none — every step carries concrete code/commands.

**Type consistency:** `cast` entry keys (`token,name,tagline,status,role`) and `status` values (`present/appeared/unseen`) match between Task 1 (producer) and Task 2 (template consumer + tests). `open_threads` `dormancy` key matches between Task 1 and the Task 2 template guard `t.dormancy is defined`. `story_so_far` entry keys (`one_line,location,date`) match between Task 1 and the Task 2 template.
