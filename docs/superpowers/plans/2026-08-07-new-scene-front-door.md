# New-scene front door — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the commit-on-click new-scene picker into mode → pick → confirm → create, resolving #315, #316, #317, #89, #90 and #23.

**Architecture:** The backend gains three small things — played greetings self-exclude in `greetings.availability()` (with the recovery path that exclusion makes necessary), `POST /scene-suggestions` gains `direction` and `rank`, and a new `POST /scene-intent` extracts metadata from typed text. The frontend splits `NewSceneChooser.tsx` into an orchestrator, a picker, a confirm form, and a suggestions hook, with a `SceneDraft` discriminated union as the seam. Nothing is written until the user presses Create.

**Tech Stack:** FastAPI + pytest (`backend/`), Vite/React + vitest (`frontend/`), Jinja2 prompt templates in `templates/`.

**Spec:** `docs/superpowers/specs/2026-08-07-new-scene-front-door-design.md`

## Global Constraints

- **Never commit anything under the data store**, and never use a real world/campaign/character name as an example. Reuse existing placeholders: Seraphine, Mara, Winifred, Realm, Saltmarch.
- **Backend tests isolate the store** via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)`.
- **pydantic stays v1/v2-agnostic**: plain `BaseModel` fields only, no `Field`, validators, `ConfigDict`, or `model_dump()`. Dump via `routes.common._dump`.
- **Imports in `backend/src/grimoire/` are module-scope and acyclic.** Inside `store/`, a cross-package import binds a *submodule*: `from ..campaigns import read` then `read.world_refs()`. Never bind a function by value off a sibling package's leaf module.
- **Every store write goes through `store.atomic`** (`test_atomic_guard.py`).
- **Run vitest FROM `frontend/`.** `npx --prefix frontend vitest run` executes from the repo root, skips `frontend/vitest.config.ts`, disables `globals`, and fails every mock-based test.
- **Gate is `make check`** — the same targets CI runs. `make check-py` (pytest), `check-web`, `check-lint` (ruff), `check-templates` (`verify_templates.py`), `check-pydantic1`.
- After editing anything in `templates/`, `verify_templates.py` must pass — builders and templates agree byte-for-byte.

---

## File Structure

**Backend, modified:**
- `backend/src/grimoire/store/greetings.py` — `availability()` gains self-exclusion (Task 1)
- `backend/src/grimoire/store/playing.py` — `_mark_played` reorder, `stamping_scene()`, `mark_greeting` clearing rule (Task 1)
- `backend/src/grimoire/store/suggest.py` — shared `_token_ok`, offscreen snapshot fix (Task 2); `direction` in `build_prompt` (Task 3); `build_intent_prompt` + `parse_intent` (Task 4)
- `backend/src/grimoire/routes/scenes.py` — `direction`/`rank` params (Task 3); `post_scene_intent` (Task 4)
- `backend/src/grimoire/routes/models.py` — `SceneIntent` body model (Task 4)
- `scripts/verify_templates.py` — direction variants (Task 3), `scene_intent` (Task 4)

**Templates, created:**
- `templates/scene_suggestions/instruction/direction_addendum.j2` (Task 3)
- `templates/scene_intent/system.j2`, `templates/scene_intent/user.j2` (Task 4)

**Frontend, created:**
- `frontend/src/components/errMsg.ts` — the error coercion currently private to `NewSceneChooser` (Task 5)
- `frontend/src/components/sceneDraft.ts` — the `SceneDraft` union and its four constructors (Task 5)
- `frontend/src/components/useSceneSuggestions.ts` — fetch/refresh state machine with request sequencing (Task 6)
- `frontend/src/components/SceneIdeaPicker.tsx` — the pick pane (Task 7)
- `frontend/src/components/SceneConfirmForm.tsx` — the confirm pane and the create sequence (Task 8)

**Frontend, modified:**
- `frontend/src/api/client.ts` — `sceneSuggestions` params, `sceneIntent` (Tasks 3, 4)
- `frontend/src/components/NewSceneChooser.tsx` — reduced to the orchestrator (Task 9)
- `frontend/src/index.css` — styles for the direction row and confirm form (Task 9)

---

## Task 1: Played greetings self-exclude, with the recovery path

Both halves land together. Shipping the one-line exclusion alone leaves a trap: an interrupted greeting start marks the greeting played, the chooser deletes the scene, `mark_greeting` refuses to clear a played mark, and the greeting becomes permanently unstartable.

**Files:**
- Modify: `backend/src/grimoire/store/greetings.py` (`availability`, ~line 270)
- Modify: `backend/src/grimoire/store/playing.py` (`mark_greeting` ~line 55, new `stamping_scene`, `start_from_greeting` ~line 132)
- Test: `backend/tests/test_playing_store.py`, `backend/tests/test_greetings_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `playing.stamping_scene(cid: str, gid: str) -> str | None`. `availability()` output unchanged in shape; a played or completed greeting now carries `available: False` and `"already played"` in `reasons`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_playing_store.py`:

```python
def test_played_greeting_is_not_available(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g1 = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    g2 = greetings.create_greeting(wroot, "B", "s", "default", body="B.")
    cid, sid = _campaign_after_seed(wid)
    playing.start_from_greeting(cid, sid, g1)
    playing.mark_greeting(cid, g2, "completed")
    got = {x["id"]: x for x in playing.available_greetings(cid)}
    assert got[g1]["available"] is False and "already played" in got[g1]["reasons"]
    assert got[g2]["available"] is False and "already played" in got[g2]["reasons"]


def test_replaying_a_played_greeting_raises(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    playing.start_from_greeting(cid, sid, g)
    sid2 = scenes.create_scene(cid, "Second")
    with pytest.raises(playing.PlayError):
        playing.start_from_greeting(cid, sid2, g)


def test_mark_played_runs_after_the_body_is_appended(monkeypatch, tmp_path):
    """A failure before append_reply must leave the greeting startable: that is
    the whole reason the mark moved."""
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    boom = RuntimeError("expansion blew up")

    def _explode(*a, **k):
        raise boom
    monkeypatch.setattr(playing.context_macros, "expand_macros", _explode)
    with pytest.raises(RuntimeError):
        playing.start_from_greeting(cid, sid, g)
    assert g not in playing.read_played(cid)
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is True


def test_stamping_scene_finds_the_scene_that_played_a_greeting(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    assert playing.stamping_scene(cid, g) is None
    sid = playing.start_from_greeting(cid, sid, g)
    assert playing.stamping_scene(cid, g) == sid


def test_orphaned_played_mark_can_be_cleared(monkeypatch, tmp_path):
    """The scene that justified the mark is gone (an interrupted start, cleaned
    up by the chooser), so the mark is orphaned and must be recoverable."""
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    sid = playing.start_from_greeting(cid, sid, g)
    scenes.delete_scene(cid, sid)
    playing.mark_greeting(cid, g, "none")
    assert g not in playing.read_played(cid)
    assert {x["id"]: x["available"] for x in playing.available_greetings(cid)}[g] is True


def test_played_mark_still_refuses_while_a_scene_stamps_it(monkeypatch, tmp_path):
    wid = _world(monkeypatch, tmp_path)
    wroot = worlds.world_root(wid)
    characters.create_character(wroot, "S", "default", characters.blank_card("S"))
    g = greetings.create_greeting(wroot, "A", "s", "default", body="A.")
    cid, sid = _campaign_after_seed(wid)
    playing.start_from_greeting(cid, sid, g)
    with pytest.raises(playing.PlayError):
        playing.mark_greeting(cid, g, "none")
```

Append to `backend/tests/test_greetings_store.py`:

```python
def test_availability_excludes_played_greetings():
    items = [{"id": "a", "name": "A", "predecessor_join": "all",
              "requires_tags": [], "pcless": False}]
    out = {x["id"]: x for x in greetings.availability(items, {}, {"a"}, set())}
    assert out["a"]["available"] is False
    assert "already played" in out["a"]["reasons"]
```

- [ ] **Step 2: Run the tests to verify they fail**

`make check-py` takes no test selector (it runs `pytest backend -q`), so run pytest directly for a single file. `PYTHONPATH=src` is load-bearing: `backend/.venv` holds an editable install whose `.pth` points at whichever checkout created it, so a bare `pytest` inside a worktree silently tests the *other* tree's sources.

```
cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_playing_store.py tests/test_greetings_store.py -v
```

Expected: `test_availability_excludes_played_greetings` fails (`available is True`), `test_played_greeting_is_not_available` fails, `test_replaying_a_played_greeting_raises` fails (no raise), `test_stamping_scene_*` fails (`AttributeError: module has no attribute 'stamping_scene'`), `test_orphaned_played_mark_can_be_cleared` fails (`PlayError`), `test_mark_played_runs_after_...` fails (greeting already marked).

- [ ] **Step 3: Add self-exclusion to `availability()`**

In `backend/src/grimoire/store/greetings.py`, inside the `for g in items:` loop of `availability()`, add the check as the **first** reason, immediately after `reasons: list[str] = []`:

```python
        reasons: list[str] = []
        if gid in played:
            # Self-exclusion, distinct from the predecessor use of `played`
            # below: an opening that has already opened a scene is not an
            # opening this campaign can still use.
            reasons.append("already played")
        p = preds[gid]
```

- [ ] **Step 4: Reorder `_mark_played` and add the recovery helpers**

In `backend/src/grimoire/store/playing.py`, move the `_mark_played(cid, gid)` call in `start_from_greeting` from before `stamp_greeting` to after `append_reply`:

```python
    if g["pcless"] and not scene_pcless:
        scenes_write.set_pcless(cid, sid)  # before substitution: {{user}} needs the pcless fallback
    scenes_write.stamp_greeting(cid, sid, gid)
    text = context_macros.expand_macros(overlay.read_greeting(cid, gid)["body"],
                                        context_macros.scene_substitutions(cid, sid), cid, sid)
    # append_reply, not append_message: [keep the existing comment block verbatim]
    scenes_write.append_reply(cid, sid, scenes_write.split_reply(
        text, frozenset(appearances_cast.player_names(cid, sid))))
    # Marked only once the body is actually on the scene. Marking earlier meant
    # a failed expansion or append consumed the greeting anyway, and — since a
    # played greeting is now unavailable — consumed it permanently.
    _mark_played(cid, gid)
    # retitle last: any earlier failure leaves the caller's sid valid for cleanup
    return scenes_lifecycle.rename_scene(cid, sid, g["name"])
```

Add `stamping_scene` above `mark_greeting`:

```python
def stamping_scene(cid: str, gid: str) -> str | None:
    """The scene recording that `gid` was played, or None if no scene does.

    Walks every scene's frontmatter head — `read_scene_meta` exists for exactly
    this kind of bulk scan and never parses a transcript. Not free, and
    deliberately called only from the unmark path below, never from the
    picker's."""
    for meta in scenes_read.list_scenes(cid):
        if scenes_read.read_scene_meta(cid, meta["id"]).get("greeting", "") == gid:
            return meta["id"]
    return None
```

Replace `mark_greeting`'s played-mark refusal:

```python
def mark_greeting(cid: str, gid: str, status: str) -> None:
    """Set a greeting's off-screen mark: completed / skipped / none (clear).

    A played mark is normally immutable — a scene records the play. But the
    scene can be gone: the new-scene chooser deletes a half-seeded scene on
    failure, and versions before the cleanup rule could strand the mark behind
    it. An orphaned mark is now clearable, because a played greeting is
    unavailable and an orphan would otherwise be unstartable forever."""
    overlay.read_greeting(cid, gid)  # raises GreetingNotFound
    if status not in ("completed", "skipped", "none"):
        raise PlayError(f"unknown mark status: {status}")
    marks = read_marks(cid)
    if gid in marks["played"]:
        if status != "none" or stamping_scene(cid, gid) is not None:
            raise PlayError("greeting was played in a scene; its mark cannot be changed")
    marks["completed"].discard(gid)
    marks["skipped"].discard(gid)
    marks["played"].discard(gid)
    if status != "none":
        marks[status].add(gid)
    _write_marks(cid, marks)
```

- [ ] **Step 5: Run the tests to verify they pass**

```
cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_playing_store.py tests/test_greetings_store.py -v
```

Expected: PASS.

- [ ] **Step 6: Run the whole backend suite and reconcile existing tests**

```
make check-py
```

Two existing tests read availability after playing a greeting and must be re-read against the new semantics rather than blindly edited:
- `test_available_greetings_after_param` (`backend/tests/test_routes.py:3237`) — plays `g1`, then asserts `avail[0]["id"] == g2`. Still passes: played greetings stay *in the list* (only skipped ones are dropped) and the unlocked sort puts `g2` first. If it fails, the sort or drop behavior changed and that is a real regression, not a test to update.
- `test_available_greetings_end_to_end` (`backend/tests/test_playing_store.py:48`) — never plays anything; unaffected.

Fix any other failure by understanding it first. Do not weaken an assertion to make it green.

- [ ] **Step 7: Commit**

```bash
git add backend/src/grimoire/store/greetings.py backend/src/grimoire/store/playing.py backend/tests/test_playing_store.py backend/tests/test_greetings_store.py
git commit -m "fix(greetings): a played greeting is no longer available (#315)

availability() reported played greetings as startable -- the played set was
only ever consulted for predecessor logic -- so the picker offered them as
cards and suggest.greeting_candidates offered them to the LLM ranker too.

Making them unavailable creates a trap on its own: start_from_greeting marked
played before appending the body, and mark_greeting refused to clear a played
mark, so an interrupted start burned the greeting permanently. So the mark now
happens after the append, and an orphaned mark -- one no scene stamps -- can
be cleared."
```

---

## Task 2: An offscreen scene never casts the player

Pre-existing, and fixed here because #316 and #317 aim user intent straight at this filter: a direction like "what they do while she sleeps" must not cast her.

**Files:**
- Modify: `backend/src/grimoire/store/suggest.py` (`build_snapshot` ~line 120, `parse_output` ~line 237)
- Test: `backend/tests/test_suggest.py`

**Interfaces:**
- Consumes: nothing from Task 1.
- Produces: `suggest._token_ok(tok: str, char_ids: set[str], player_tokens: set[str], offscreen: bool) -> bool`, the single validity predicate `parse_output` and (Task 4) `parse_intent` both call.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_suggest.py` (create it if absent, mirroring the imports of the nearest existing store test):

```python
def test_offscreen_rejects_a_player_seated_as_a_character(monkeypatch, tmp_path):
    """CastPanel's role selector lets a `characters` actor be a player. An
    offscreen scene is defined by the player's absence, whatever kind seats
    them."""
    cid = _campaign_with_player_character(monkeypatch, tmp_path)   # seats characters:mara as role=player
    reply = '{"suggestions": [{"title": "T", "premise": "P", "cast": ["characters:mara"], "location": ""}]}'
    assert suggest.parse_output(reply, cid, offscreen=True)[0]["cast"] == []
    snap = suggest.build_snapshot(cid, offscreen=True)
    assert "characters:mara" not in {c["token"] for c in snap["cast"]}


def test_pc_scene_still_accepts_that_same_player(monkeypatch, tmp_path):
    """The offscreen clause must stay guarded: without the guard it would
    reject players from ordinary PC scenes too."""
    cid = _campaign_with_player_character(monkeypatch, tmp_path)
    reply = '{"suggestions": [{"title": "T", "premise": "P", "cast": ["characters:mara"], "location": ""}]}'
    assert suggest.parse_output(reply, cid, offscreen=False)[0]["cast"] == ["characters:mara"]
    snap = suggest.build_snapshot(cid, offscreen=False)
    assert "characters:mara" in {c["token"] for c in snap["cast"]}
```

Write `_campaign_with_player_character` as a module-level helper in that test file: create a world, create character `Mara`, create a campaign, create a scene, and `appearances.transitions.appear(cid, sid, "characters", "mara", version, "player")`. Follow the `_world` / `_campaign_after_seed` helpers in `backend/tests/test_playing_store.py` for the exact construction calls.

- [ ] **Step 2: Run the tests to verify they fail**

```
cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_suggest.py -v
```

Expected: `test_offscreen_rejects_...` FAILS on both assertions (cast is `["characters:mara"]`, token is present). `test_pc_scene_still_accepts...` PASSES already — it is the regression guard for step 3.

- [ ] **Step 3: Extract the shared predicate and fix the offscreen case**

In `backend/src/grimoire/store/suggest.py`, add above `parse_output`:

```python
def _token_ok(tok: str, char_ids: set[str], player_tokens: set[str], offscreen: bool) -> bool:
    """A cast token this campaign actually has.

    The offscreen clause is FIRST and guarded, both deliberately. A PC seated as
    a `characters` actor (CastPanel's role selector allows exactly that) would
    otherwise pass on the `char_ids` check below, and an offscreen scene is
    defined by the player's absence. Guarded, because dropping the `offscreen`
    condition would reject players from ordinary PC scenes."""
    kind, _, aid = tok.partition(":")
    if offscreen and tok in player_tokens:
        return False
    if kind == "characters" and aid in char_ids:
        return True
    return not offscreen and tok in player_tokens
```

In `parse_output`, delete the local `_valid_token` and call the shared one:

```python
    out: list[dict] = []
    for e in suggestions:
        if not isinstance(e, dict):
            continue
        title, premise = str(e.get("title", "")).strip(), str(e.get("premise", "")).strip()
        if not title or not premise:
            continue
        raw_cast = e.get("cast", [])
        cast = ([t for t in (str(x).strip() for x in raw_cast)
                 if _token_ok(t, char_ids, player_tokens, offscreen)]
                if isinstance(raw_cast, list) else [])
```

In `build_snapshot`, skip roster players in the character loop when offscreen:

```python
    cast, seen = [], set()
    for c in overlay.list_characters(cid):
        tok = f"characters:{c['id']}"
        if offscreen and tok in player_tokens:
            continue   # don't offer the model a token the parser will discard
        seen.add(tok)
```

- [ ] **Step 4: Run the tests to verify they pass**

```
cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_suggest.py -v
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/store/suggest.py backend/tests/test_suggest.py
git commit -m "fix(suggest): an offscreen scene never casts the player

_valid_token accepted any characters:<id> in the campaign before consulting
player_tokens, so a PC seated as a characters-kind actor -- which CastPanel's
role selector allows -- passed the offscreen filter, and build_snapshot
offered it to the model in the first place.

Extracts the predicate as _token_ok so the incoming scene-intent parser shares
one definition rather than growing a second."
```

---

## Task 3: Steer suggestions with a direction, and refresh without re-ranking

**Files:**
- Modify: `backend/src/grimoire/store/suggest.py` (`build_prompt`)
- Create: `templates/scene_suggestions/instruction/direction_addendum.j2`
- Modify: `templates/scene_suggestions/system.j2`, `templates/scene_suggestions/user.j2`
- Modify: `backend/src/grimoire/routes/scenes.py` (`post_scene_suggestions`)
- Modify: `scripts/verify_templates.py`
- Modify: `frontend/src/api/client.ts` (`sceneSuggestions`)
- Test: `backend/tests/test_suggest.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `_token_ok` from Task 2 (unchanged here).
- Produces: `suggest.build_prompt(snapshot, greeting_candidates=None, offscreen=False, direction="") -> list[dict]`; `suggest.DIRECTION_LIMIT = 500`; route params `direction: str = ""` and `rank: bool = True`; client `api.sceneSuggestions(cid, after?, offscreen?, direction?, rank?)`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_suggest.py`:

```python
def test_direction_reaches_the_prompt():
    snap = {"now": "", "friendly": "", "holidays_today": [], "upcoming": None,
            "birthdays": [], "story_so_far": [], "open_threads": [], "cast": [],
            "available_locations": []}
    msgs = suggest.build_prompt(snap, None, direction="something at sea")
    assert "something at sea" in msgs[1]["content"]
    assert "Direction" in msgs[0]["content"]      # the instruction addendum


def test_no_direction_leaves_the_prompt_as_it_was():
    snap = {"now": "", "friendly": "", "holidays_today": [], "upcoming": None,
            "birthdays": [], "story_so_far": [], "open_threads": [], "cast": [],
            "available_locations": []}
    assert suggest.build_prompt(snap, None) == suggest.build_prompt(snap, None, direction="")
```

Add to `backend/tests/test_routes.py` (near the other scene-suggestion route tests):

```python
def test_scene_suggestions_rank_false_skips_greeting_picks(client, monkeypatch):
    wid, cid = _campaign(client)
    # three startable greetings is what normally triggers ranking
    client.post(f"/api/worlds/{wid}/characters", json={"name": "Seraphine"})
    for name in ("Alpha", "Beta", "Gamma"):
        client.post(f"/api/worlds/{wid}/greetings",
                    json={"name": name, "character": "seraphine",
                          "version": "default", "body": f"{name}."})
    seen = {}
    import grimoire.store.suggest as suggest_mod
    real = suggest_mod.greeting_candidates
    def _spy(*a, **k):
        seen["called"] = True
        return real(*a, **k)
    monkeypatch.setattr(suggest_mod, "greeting_candidates", _spy)
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions?rank=false")
    assert r.status_code == 200
    assert r.json()["greeting_picks"] == []
    assert "called" not in seen


def test_scene_suggestions_truncates_an_over_long_direction(client):
    wid, cid = _campaign(client)
    r = client.post(f"/api/campaigns/{cid}/scene-suggestions",
                    params={"direction": "x" * 900})
    assert r.status_code == 200
```

Wire the LLM through `app.dependency_overrides[routes.get_llm]` with a fake from `backend/tests/llm_fakes.py` exactly as the existing scene-suggestion route tests do — never write a new inline fake. For the truncation assertion, use a fake that captures the outgoing messages and assert the rendered prompt contains exactly 500 `x`s.

- [ ] **Step 2: Run the tests to verify they fail**

```
cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_suggest.py tests/test_routes.py -k "direction or rank_false" -v
```

Expected: FAIL — `build_prompt() got an unexpected keyword argument 'direction'`, and the route ignores both query params.

- [ ] **Step 3: Create the instruction addendum**

`templates/scene_suggestions/instruction/direction_addendum.j2` — one line, no trailing newline, leading space to match `date_addendum.j2`'s concatenation style:

```
 The game master has asked for a particular kind of scene; their instruction appears under "Direction" below. Every suggestion must satisfy it while still respecting the campaign situation, the available cast tokens, and the location ids.
```

- [ ] **Step 4: Include it, and render the direction itself**

`templates/scene_suggestions/system.j2` — add the include and document the new selector:

```jinja
{#- The scene-suggestion instruction. Selector: `offscreen` (bool) picks the
    instruction/ variant; `s.now` (a current date exists) appends the date
    addendum; `greeting_candidates` (non-empty list) appends the ranking
    addendum; `direction` (non-empty string) appends the direction addendum.
    Vars: offscreen, s (the snapshot — same as user.j2), greeting_candidates,
    direction. -#}
{%- include "scene_suggestions/instruction/" ~ ("offscreen" if offscreen else "standard") ~ ".j2" -%}
{%- if s.now -%}{%- include "scene_suggestions/instruction/date_addendum.j2" -%}{%- endif -%}
{%- if greeting_candidates -%}{%- include "scene_suggestions/instruction/rank_addendum.j2" -%}{%- endif -%}
{%- if direction -%}{%- include "scene_suggestions/instruction/direction_addendum.j2" -%}{%- endif -%}
```

`templates/scene_suggestions/user.j2` — add `direction` to the header comment's var list, and append the labelled block immediately before the final `{{ content }}` line:

```jinja
{%- if direction -%}
{%- set content = content ~ "\n\nDirection (the game master's instruction for this batch):\n" ~ direction -%}
{%- endif -%}
{{ content }}
```

Labelled as a distinct block so the model reads it as an instruction from the GM rather than as another piece of campaign data.

- [ ] **Step 5: Thread `direction` through `build_prompt`**

In `backend/src/grimoire/store/suggest.py`:

```python
DIRECTION_LIMIT = 500


def build_prompt(snapshot: dict, greeting_candidates: list[dict] | None = None,
                 offscreen: bool = False, direction: str = "") -> list[dict]:
    # the templates pick the instruction variant and addenda from the same vars
    vars = {"s": snapshot, "offscreen": offscreen,
            "greeting_candidates": greeting_candidates,
            "direction": direction.strip()[:DIRECTION_LIMIT]}
    return [{"role": "system", "content": prompts.render("scene_suggestions/system.j2", **vars)},
            {"role": "user", "content": prompts.render("scene_suggestions/user.j2", **vars)}]
```

Truncation lives here rather than at the route so every caller gets it, and it truncates rather than rejecting — an over-long direction is a user typing, not an error.

- [ ] **Step 6: Add the route parameters**

In `backend/src/grimoire/routes/scenes.py`, `post_scene_suggestions`:

```python
async def post_scene_suggestions(cid: str, after: str | None = None, offscreen: bool = False,
                                 direction: str = "", rank: bool = True,
                                 client: LLMClient = Depends(get_llm)):
    ...
    conn = _require_connection()
    # A refresh passes rank=false: re-ranking would reshuffle the greeting cards
    # under the user's cursor, and the ranking is the expensive half of the prompt.
    candidates = store.suggest.greeting_candidates(cid, after, pcless=offscreen) if rank else []
    messages = store.suggest.build_prompt(store.suggest.build_snapshot(cid, offscreen=offscreen),
                                          candidates, offscreen=offscreen, direction=direction)
```

The rest of the function is unchanged: `picks` is already `[]` when `candidates` is empty.

- [ ] **Step 7: Register the new prompt variants**

In `scripts/verify_templates.py`, extend the suggestions loop (~line 146) with two direction cases and pass the var through both renders:

```python
for label, snap, cands, off, direction in (
        ("empty", EMPTY_SNAP, None, False, ""),
        ("full", FULL_SNAP, None, False, ""),
        ("full+greetings", FULL_SNAP, GREETINGS, False, ""),
        ("offscreen", FULL_SNAP, None, True, ""),
        ("direction", FULL_SNAP, None, False, "something at sea"),
        ("direction+greetings", FULL_SNAP, GREETINGS, False, "something at sea")):
    exp = suggest.build_prompt(snap, cands, offscreen=off, direction=direction)
    check(f"suggestions system ({label})", exp[0]["content"],
          render("scene_suggestions/system.j2", s=snap, offscreen=off,
                 greeting_candidates=cands, direction=direction))
    check(f"suggestions user ({label})", exp[1]["content"],
          render("scene_suggestions/user.j2", s=snap, offscreen=off,
                 greeting_candidates=cands, direction=direction))
```

Also update the store-level check near line 727, which renders with `StrictUndefined` and will now fail without the var:

```python
snap = suggest.build_snapshot(cid)
exp = suggest.build_prompt(snap, None)
check("suggestions system (store)", exp[0]["content"],
      render("scene_suggestions/system.j2", s=snap, offscreen=False,
             greeting_candidates=None, direction=""))
check("suggestions user (store)", exp[1]["content"],
      render("scene_suggestions/user.j2", s=snap, offscreen=False,
             greeting_candidates=None, direction=""))
```

- [ ] **Step 8: Add the client parameters**

In `frontend/src/api/client.ts`:

```ts
  sceneSuggestions: (cid: string, after?: string, offscreen?: boolean,
                     direction?: string, rank = true) => {
    const params = new URLSearchParams();
    if (after) params.set("after", after);
    if (offscreen) params.set("offscreen", "true");
    if (direction) params.set("direction", direction);
    if (!rank) params.set("rank", "false");
    const qs = params.toString();
    return request<{ suggestions: SceneSuggestion[]; greeting_picks?: string[]; next_date?: string }>(
      "POST", `/api/campaigns/${cid}/scene-suggestions${qs ? `?${qs}` : ""}`);
  },
```

- [ ] **Step 9: Run the tests and the template harness**

```
cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_suggest.py tests/test_routes.py -v
cd .. && make check-templates
```

Expected: PASS, and `verify_templates.py` reports its check count with no failures.

- [ ] **Step 10: Commit**

```bash
git add backend/src/grimoire/store/suggest.py backend/src/grimoire/routes/scenes.py templates/scene_suggestions scripts/verify_templates.py frontend/src/api/client.ts backend/tests/
git commit -m "feat(scenes): steer scene suggestions with a direction (#316, #89)

POST /scene-suggestions takes a free-text direction, truncated to 500 chars in
build_prompt so every caller gets the same bound, and rendered as a labelled
block so the model reads it as the GM's instruction rather than as campaign
data.

rank=false skips greeting_candidates entirely. A refresh that re-ranked would
reshuffle the greeting cards under the user's cursor, and ranking is the
expensive half of the prompt."
```

---

## Task 4: Extract scene metadata from typed text

**Files:**
- Create: `templates/scene_intent/system.j2`, `templates/scene_intent/user.j2`
- Modify: `backend/src/grimoire/store/suggest.py` (`build_intent_prompt`, `parse_intent`)
- Modify: `backend/src/grimoire/routes/models.py` (`SceneIntent`)
- Modify: `backend/src/grimoire/routes/scenes.py` (`post_scene_intent`)
- Modify: `scripts/verify_templates.py`
- Modify: `frontend/src/api/client.ts`
- Test: `backend/tests/test_suggest.py`, `backend/tests/test_routes.py`

**Interfaces:**
- Consumes: `suggest._token_ok`, `_valid_ids`, `_date_normalizer`, `_extract_json` (Task 2); `build_snapshot`.
- Produces: `suggest.build_intent_prompt(cid: str, typed: str, offscreen: bool = False) -> list[dict]`; `suggest.parse_intent(reply: str, cid: str, offscreen: bool = False) -> dict` returning `{"title": str, "date": str, "location": str, "cast": list[str]}`; `suggest.INTENT_LIMIT = 2000`; route `POST /campaigns/{cid}/scene-intent`; client `api.sceneIntent(cid, text, offscreen)` returning `{title, date, location: {id, name} | null, cast: {kind, id, name}[]}`.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_suggest.py`:

```python
INTENT_REPLY = ('{"title": "The morning after", "date": "2026-03-04", '
                '"location": "saltmarch", "cast": ["characters:mara"]}')


def test_parse_intent_validates_every_field(monkeypatch, tmp_path):
    cid = _campaign_with_location_and_character(monkeypatch, tmp_path)  # locations/saltmarch, characters/mara
    got = suggest.parse_intent(INTENT_REPLY, cid)
    assert got["title"] == "The morning after"
    assert got["location"] == "saltmarch"
    assert got["cast"] == ["characters:mara"]
    assert got["date"]        # normalized, non-empty


def test_parse_intent_drops_what_the_campaign_does_not_have(monkeypatch, tmp_path):
    cid = _campaign_with_location_and_character(monkeypatch, tmp_path)
    reply = ('{"title": "T", "date": "the fourth of Never", "location": "atlantis", '
             '"cast": ["characters:nobody", "garbage"]}')
    got = suggest.parse_intent(reply, cid)
    assert got == {"title": "T", "date": "", "location": "", "cast": []}


def test_parse_intent_takes_the_first_object_of_a_bare_array(monkeypatch, tmp_path):
    cid = _campaign_with_location_and_character(monkeypatch, tmp_path)
    assert suggest.parse_intent(f"[{INTENT_REPLY}]", cid)["title"] == "The morning after"


def test_parse_intent_survives_garbage(monkeypatch, tmp_path):
    cid = _campaign_with_location_and_character(monkeypatch, tmp_path)
    assert suggest.parse_intent("I'm afraid I can't do that.", cid) == {
        "title": "", "date": "", "location": "", "cast": []}


def test_parse_intent_honors_offscreen(monkeypatch, tmp_path):
    cid = _campaign_with_player_character(monkeypatch, tmp_path)
    reply = '{"title": "T", "date": "", "location": "", "cast": ["characters:mara"]}'
    assert suggest.parse_intent(reply, cid, offscreen=True)["cast"] == []
```

Add to `backend/tests/test_routes.py`:

```python
def test_scene_intent_rejects_empty_text(client):
    _wid, cid = _campaign(client)
    assert client.post(f"/api/campaigns/{cid}/scene-intent",
                       json={"text": "   ", "offscreen": False}).status_code == 400


def test_scene_intent_resolves_names(client):
    """The response mirrors scene-suggestions' shapes so the frontend reuses one
    converter: location is {id, name} or null, cast carries names."""
    wid, cid = _campaign(client)
    # ... create locations/saltmarch and characters/mara, install the fake LLM
    # via app.dependency_overrides[routes.get_llm] returning INTENT_REPLY ...
    r = client.post(f"/api/campaigns/{cid}/scene-intent",
                    json={"text": "the morning after, back at the marsh house",
                          "offscreen": False})
    assert r.status_code == 200
    assert r.json()["location"] == {"id": "saltmarch", "name": "Saltmarch"}
    assert r.json()["cast"][0]["name"] == "Mara"
```

Use `backend/tests/llm_fakes.py` for the fake in both route tests.

- [ ] **Step 2: Run the tests to verify they fail**

```
cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_suggest.py tests/test_routes.py -k intent -v
```

Expected: FAIL — `module 'suggest' has no attribute 'parse_intent'`, and 404 on the route.

- [ ] **Step 3: Write the templates**

`templates/scene_intent/system.j2`:

```
You extract structured metadata from a game master's own description of how the next scene of a role-play campaign starts. Do NOT invent a scene, rewrite their description, or propose an alternative — read what they wrote and report only what it implies. Reply with ONLY a JSON object with keys "title" (a short label for the scene, 2-5 words), "date" (the date the scene opens, in the same notation as the current date shown below, or "" if the text implies no particular date), "location" (one location id from the available locations, or "" if the text implies none), and "cast" (a list of "<kind>:<id>" tokens for the characters the text places in the scene, chosen ONLY from the available cast). Use only the ids given; never invent one. Prefer "" or an empty list over a guess.
```

`templates/scene_intent/user.j2`:

```jinja
{#- Extraction input: the campaign snapshot, shared verbatim with
    scene_suggestions/user.j2 rather than restated, plus the game master's typed
    scene-start description. Vars: s, offscreen, greeting_candidates (always
    None here), typed. -#}
{%- include "scene_suggestions/user.j2" %}

The game master's description of how the next scene starts:
{{ typed }}
```

The `{%-` strips leading whitespace while ` %}` preserves the newlines that follow, so the typed block lands two lines below the snapshot. `greeting_candidates` must be passed (as `None`) because `verify_templates.py` renders with `StrictUndefined`.

- [ ] **Step 4: Add the builder and parser**

In `backend/src/grimoire/store/suggest.py`, update the module docstring's first line to say the module builds and parses *scene-suggestion and scene-intent* prompts, then add:

```python
INTENT_LIMIT = 2000


def build_intent_prompt(cid: str, typed: str, offscreen: bool = False) -> list[dict]:
    """Prompt for extracting metadata from the user's own scene description.

    Over the FULL snapshot, story-so-far included: "the morning after the
    funeral" is exactly the kind of phrase this has to resolve, and only the
    recent chronicle can resolve it."""
    vars = {"s": build_snapshot(cid, offscreen=offscreen), "offscreen": offscreen,
            "greeting_candidates": None, "typed": typed.strip()[:INTENT_LIMIT]}
    return [{"role": "system", "content": prompts.render("scene_intent/system.j2", **vars)},
            {"role": "user", "content": prompts.render("scene_intent/user.j2", **vars)}]


def parse_intent(reply: str, cid: str, offscreen: bool = False) -> dict:
    """Metadata extracted from the user's own description, every field validated
    against the campaign.

    Malformed or semantically invalid model output never raises — extraction is
    a convenience, and a miss must leave the user a blank form rather than an
    error. Store and calendar failures underneath (`_valid_ids` reads entities,
    `_date_normalizer` imports a user-authored provider) are NOT covered by that
    and surface as the route's ordinary 500, exactly as they do for
    `parse_output`."""
    empty = {"title": "", "date": "", "location": "", "cast": []}
    parsed = _extract_json(reply)
    if isinstance(parsed, list):   # a bare array is a common LLM deviation
        parsed = next((e for e in parsed if isinstance(e, dict)), None)
    if not isinstance(parsed, dict):
        return empty
    char_ids, player_tokens, loc_ids = _valid_ids(cid)
    raw_cast = parsed.get("cast", [])
    cast = ([t for t in (str(x).strip() for x in raw_cast)
             if _token_ok(t, char_ids, player_tokens, offscreen)]
            if isinstance(raw_cast, list) else [])
    loc = str(parsed.get("location", "")).strip()
    return {"title": str(parsed.get("title", "")).strip(),
            "date": _date_normalizer(cid)(str(parsed.get("date", "")).strip()),
            "location": loc if loc in loc_ids else "",
            "cast": cast}
```

- [ ] **Step 5: Add the body model and the route**

In `backend/src/grimoire/routes/models.py`, beside `Opener`:

```python
class SceneIntent(BaseModel):
    text: str
    offscreen: bool = False
```

In `backend/src/grimoire/routes/scenes.py`, add `SceneIntent` to the `.models` import and place the route directly after `post_scene_suggestions`:

```python
@router.post("/campaigns/{cid}/scene-intent")
@computes_only
async def post_scene_intent(cid: str, body: SceneIntent,
                            client: LLMClient = Depends(get_llm)):
    """Metadata implied by the user's own scene-start description. Computes and
    returns; the confirm form is what decides whether any of it is written."""
    try:
        store.campaigns.read_campaign(cid)
    except store.campaigns.CampaignNotFound:
        raise HTTPException(status_code=404, detail="campaign not found")
    if not body.text.strip():
        raise HTTPException(status_code=400, detail="empty scene description")
    conn = _require_connection()
    messages = store.suggest.build_intent_prompt(cid, body.text, offscreen=body.offscreen)
    try:
        text = await _bounded_call(client.complete(messages, conn))
    except LLMError as exc:
        raise HTTPException(status_code=502, detail={"detail": exc.detail, "kind": exc.kind})
    got = store.suggest.parse_intent(text, cid, offscreen=body.offscreen)
    loc_names = {e["id"]: e.get("name", e["id"]) for e in store.overlay.list_entities(cid, "locations")}
    loc = ({"id": got["location"], "name": loc_names.get(got["location"], got["location"])}
           if got["location"] else None)
    return {"title": got["title"], "date": got["date"], "location": loc,
            "cast": _resolve_cast(cid, got["cast"])}
```

- [ ] **Step 6: Register the templates**

`build_intent_prompt` calls `build_snapshot` itself, so unlike `build_prompt` it needs a real campaign — the check therefore belongs in the store-level block near line 727, which already has a `cid`, not in the pure-snapshot loop above it. Add there:

```python
TYPED = "the morning after, back at the marsh house"
iexp = suggest.build_intent_prompt(cid, TYPED)
isnap = suggest.build_snapshot(cid)
check("intent system (store)", iexp[0]["content"],
      render("scene_intent/system.j2", s=isnap, offscreen=False,
             greeting_candidates=None, typed=TYPED))
check("intent user (store)", iexp[1]["content"],
      render("scene_intent/user.j2", s=isnap, offscreen=False,
             greeting_candidates=None, typed=TYPED))

ioff_exp = suggest.build_intent_prompt(cid, TYPED, offscreen=True)
ioff_snap = suggest.build_snapshot(cid, offscreen=True)
check("intent user (store, offscreen)", ioff_exp[1]["content"],
      render("scene_intent/user.j2", s=ioff_snap, offscreen=True,
             greeting_candidates=None, typed=TYPED))
```

Do **not** add a check that renders a template and compares it to itself — that passes unconditionally and proves nothing. Each check compares the *builder's* output to a direct render.

- [ ] **Step 7: Add the client call**

In `frontend/src/api/client.ts`, beside `sceneSuggestions`, and export the type:

```ts
export type SceneIntentResult = {
  title: string; date: string;
  location: { id: string; name: string } | null;
  cast: { kind: string; id: string; name: string }[];
};
```

```ts
  sceneIntent: (cid: string, text: string, offscreen: boolean) =>
    request<SceneIntentResult>("POST", `/api/campaigns/${cid}/scene-intent`,
      { text, offscreen }),
```

- [ ] **Step 8: Run the tests and the harness**

```
cd backend && PYTHONPATH=src .venv/Scripts/python.exe -m pytest tests/test_suggest.py tests/test_routes.py -v
cd .. && make check-templates && make check-lint
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add backend/src/grimoire/store/suggest.py backend/src/grimoire/routes templates/scene_intent scripts/verify_templates.py frontend/src/api/client.ts backend/tests/
git commit -m "feat(scenes): extract date, location and cast from typed text (#317)

POST /scene-intent reads the user's own description of how a scene starts and
reports only what it implies -- the typed text is never rewritten, which is why
this is its own prompt rather than a scene-suggestions call with n=1.

Over the full snapshot, story-so-far included: 'the morning after the funeral'
is the class of phrase this exists to resolve. Its user template includes
scene_suggestions/user.j2 rather than restating the snapshot."
```

---

## Task 5: The draft seam

Pure types and constructors, no rendering. Written first so every later frontend task shares one definition.

**Files:**
- Create: `frontend/src/components/errMsg.ts`
- Create: `frontend/src/components/sceneDraft.ts`
- Test: `frontend/src/components/sceneDraft.test.ts`

**Interfaces:**
- Consumes: `Availability`, `SceneSuggestion`, `SceneIntentResult` from `../api/client` (Task 4).
- Produces: `SceneDraft`, `DraftCast`, `BLANK_TITLE`, `greetingDraft(g, nextDate, pcless)`, `suggestionDraft(s, nextDate, pcless)`, `customDraft(typed, intent, nextDate, pcless)`, and `errMsg(err)`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/sceneDraft.test.ts`:

```ts
import { greetingDraft, suggestionDraft, customDraft, BLANK_TITLE } from "./sceneDraft";

const G = { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true };
const S = {
  title: "The creditor", premise: "A debt-collector arrives.", date: "2026-03-04",
  cast: [{ kind: "characters", id: "mara", name: "Mara" }],
  location: { id: "saltmarch", name: "Saltmarch" },
};

test("a greeting draft takes its title from the greeting and its date from nextDate", () => {
  const d = greetingDraft(G, "2026-01-01", false);
  expect(d).toEqual({ source: "greeting", gid: "reck", title: "Reckoning",
                      defaultTitle: "Reckoning", date: "2026-01-01",
                      location: "", pcless: false });
});

test("a suggestion draft prefers its own date and falls back to nextDate", () => {
  expect(suggestionDraft(S, "2026-01-01", false).date).toBe("2026-03-04");
  expect(suggestionDraft({ ...S, date: "" }, "2026-01-01", false).date).toBe("2026-01-01");
});

test("a suggestion draft carries cast, location id, and premise", () => {
  const d = suggestionDraft(S, "", false);
  expect(d).toMatchObject({ source: "generated", location: "saltmarch",
                            premise: "A debt-collector arrives.",
                            cast: [{ kind: "characters", id: "mara", name: "Mara" }] });
});

test("a custom draft keeps the typed text as the premise, never the model's title", () => {
  const d = customDraft("back at the marsh house", 
    { title: "The morning after", date: "2026-03-04",
      location: { id: "saltmarch", name: "Saltmarch" }, cast: [] }, "2026-01-01", false);
  expect(d).toMatchObject({ source: "custom", title: "The morning after",
                            defaultTitle: "The morning after", date: "2026-03-04",
                            location: "saltmarch", premise: "back at the marsh house" });
});

test("a blank draft still seeds nextDate, as every path does today", () => {
  const d = customDraft("", null, "2026-01-01", false);
  expect(d).toMatchObject({ title: BLANK_TITLE, defaultTitle: BLANK_TITLE,
                            date: "2026-01-01", location: "", premise: "", cast: [] });
});

test("an extraction that returned nothing degrades to the blank defaults", () => {
  const d = customDraft("something", { title: "", date: "", location: null, cast: [] },
                        "2026-01-01", false);
  expect(d).toMatchObject({ title: BLANK_TITLE, date: "2026-01-01", location: "",
                            premise: "something" });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```
cd frontend && npx vitest run src/components/sceneDraft.test.ts
```

Expected: FAIL — cannot resolve `./sceneDraft`.

- [ ] **Step 3: Write the two modules**

`frontend/src/components/errMsg.ts`:

```ts
/** LLM-backed endpoints 502 with an object detail; coerce so it renders as text. */
export function errMsg(err: any): string {
  const d = err?.detail;
  return typeof d === "string" ? d : (d?.detail ?? String(err));
}
```

`frontend/src/components/sceneDraft.ts`:

```ts
import type { Availability, SceneIntentResult, SceneSuggestion } from "../api/client";

export type DraftCast = { kind: string; id: string; name: string };

type DraftBase = {
  /** editable in the confirm form */
  title: string;
  /** immutable: what an emptied title falls back to, so the fallback is
   *  executable from the draft alone rather than from whatever produced it */
  defaultTitle: string;
  /** native notation as typed or proposed — set_datetime canonicalizes it */
  date: string;
  /** a location id, "" for none */
  location: string;
  pcless: boolean;
};

/** Greeting drafts carry no premise and no cast BY CONSTRUCTION: the greeting
 *  body is the first post, and start_from_greeting seats the greeting's cast
 *  under locked-version rules a form must not re-implement. */
export type SceneDraft =
  | (DraftBase & { source: "greeting"; gid: string })
  | (DraftBase & { source: "generated" | "custom"; premise: string; cast: DraftCast[] });

export const BLANK_TITLE = "New scene";

export function greetingDraft(g: Availability, nextDate: string, pcless: boolean): SceneDraft {
  return { source: "greeting", gid: g.id, title: g.name, defaultTitle: g.name,
           date: nextDate, location: "", pcless };
}

export function suggestionDraft(s: SceneSuggestion, nextDate: string,
                                pcless: boolean): SceneDraft {
  return { source: "generated", title: s.title, defaultTitle: s.title,
           date: s.date || nextDate, location: s.location?.id ?? "",
           pcless, premise: s.premise, cast: s.cast };
}

/** `typed` is always the premise. The extraction's job is metadata only — it
 *  never replaces what the user wrote. `intent` is null when there was no LLM
 *  connection, the call failed, or nothing was typed. */
export function customDraft(typed: string, intent: SceneIntentResult | null,
                            nextDate: string, pcless: boolean): SceneDraft {
  const title = intent?.title || BLANK_TITLE;
  return { source: "custom", title, defaultTitle: title,
           date: intent?.date || nextDate, location: intent?.location?.id ?? "",
           pcless, premise: typed, cast: intent?.cast ?? [] };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```
cd frontend && npx vitest run src/components/sceneDraft.test.ts && npx tsc -b
```

Expected: PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/sceneDraft.ts frontend/src/components/sceneDraft.test.ts frontend/src/components/errMsg.ts
git commit -m "feat(scenes): the SceneDraft seam between picking and confirming

A discriminated union so no path can construct a state the create sequence
cannot execute: greeting drafts carry a required gid and neither premise nor
cast, and every draft carries pcless and an immutable defaultTitle.

The constructors pin the per-source date fallback, which is the part 'an empty
draft' would otherwise get wrong -- every path today seeds nextDate, blank
creation included."
```

---

## Task 6: The suggestions hook

**Files:**
- Create: `frontend/src/components/useSceneSuggestions.ts`
- Test: `frontend/src/components/useSceneSuggestions.test.ts`

**Interfaces:**
- Consumes: `api.sceneSuggestions` (Task 3), `errMsg` (Task 5).
- Produces: `useSceneSuggestions(cid, afterSid, ready, offscreen)` returning `{ suggestions: SceneSuggestion[] | null, picks: string[] | null, nextDate: string, busy: boolean, error: string | null, refresh: (direction: string) => void }`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/useSceneSuggestions.test.ts`:

```ts
import { renderHook, waitFor, act } from "@testing-library/react";
import { useSceneSuggestions } from "./useSceneSuggestions";

vi.mock("../api/client", () => ({ api: { sceneSuggestions: vi.fn() } }));
import { api } from "../api/client";

const R = (suggestions: any[], picks: string[] = [], next_date = "") =>
  ({ suggestions, greeting_picks: picks, next_date });

beforeEach(() => vi.clearAllMocks());

test("the first fetch ranks; a refresh does not, and keeps the picks", async () => {
  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "A" }], ["g1"], "2026-01-01"));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.picks).toEqual(["g1"]));
  expect(api.sceneSuggestions).toHaveBeenCalledWith("c", "s1", false, "", true);

  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "B" }], [], ""));
  act(() => result.current.refresh("something at sea"));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "B" }]));
  expect(api.sceneSuggestions).toHaveBeenLastCalledWith("c", "s1", false, "something at sea", false);
  expect(result.current.picks).toEqual(["g1"]);      // not clobbered by the empty list
  expect(result.current.nextDate).toBe("2026-01-01"); // not cleared by an empty one
});

test("a stale response that resolves after a newer one is discarded", async () => {
  let releaseFirst: (v: any) => void = () => {};
  (api.sceneSuggestions as any).mockReturnValueOnce(new Promise((r) => { releaseFirst = r; }));
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));

  (api.sceneSuggestions as any).mockResolvedValue(R([{ title: "newest" }]));
  act(() => result.current.refresh("x"));
  await waitFor(() => expect(result.current.suggestions).toEqual([{ title: "newest" }]));

  await act(async () => { releaseFirst(R([{ title: "stale" }], ["g9"])); });
  expect(result.current.suggestions).toEqual([{ title: "newest" }]);
  expect(result.current.picks).toBeNull();     // the stale ranked reply wrote nothing
});

test("without a connection nothing is fetched and the lists are empty, not pending", () => {
  renderHook(() => useSceneSuggestions("c", "s1", false, false));
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
});

test("a failure empties the suggestions and reports the error", async () => {
  (api.sceneSuggestions as any).mockRejectedValue({ detail: "no key" });
  const { result } = renderHook(() => useSceneSuggestions("c", "s1", true, false));
  await waitFor(() => expect(result.current.error).toBe("no key"));
  expect(result.current.suggestions).toEqual([]);
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```
cd frontend && npx vitest run src/components/useSceneSuggestions.test.ts
```

Expected: FAIL — cannot resolve `./useSceneSuggestions`.

- [ ] **Step 3: Write the hook**

`frontend/src/components/useSceneSuggestions.ts`:

```ts
import { useCallback, useEffect, useRef, useState } from "react";
import { api, type SceneSuggestion } from "../api/client";
import { errMsg } from "./errMsg";

/** The generated half of the picker. `null` on suggestions/picks means "still
 *  generating"; `[]` means "nothing to offer" (no key, empty, or failed) — the
 *  picker renders those two states differently, so they must stay distinct. */
export function useSceneSuggestions(cid: string, afterSid: string | null,
                                    ready: boolean, offscreen: boolean) {
  const [suggestions, setSuggestions] = useState<SceneSuggestion[] | null>(ready ? null : []);
  const [picks, setPicks] = useState<string[] | null>(ready ? null : []);
  const [nextDate, setNextDate] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Only the NEWEST request may write state. The initial ranked fetch and a
  // regenerate race freely, and without this a slow first reply lands after the
  // directed one and silently replaces it with undirected cards.
  const seq = useRef(0);

  const run = useCallback((direction: string, rank: boolean) => {
    if (!ready) return;
    const mine = ++seq.current;
    setBusy(true);
    setError(null);
    api.sceneSuggestions(cid, afterSid ?? undefined, offscreen, direction, rank)
      .then((r) => {
        if (mine !== seq.current) return;
        setSuggestions(r.suggestions);
        // A rank=false reply carries no picks; writing its empty list would
        // wipe the ranking the greeting cards are ordered by.
        if (rank) setPicks(r.greeting_picks ?? []);
        // Likewise: a refresh that estimates no date must not clear a good one.
        if (r.next_date) setNextDate(r.next_date);
      })
      .catch((err) => {
        if (mine !== seq.current) return;
        setSuggestions([]);
        if (rank) setPicks([]);
        setError(errMsg(err));
      })
      .finally(() => { if (mine === seq.current) setBusy(false); });
  }, [cid, afterSid, ready, offscreen]);

  useEffect(() => { run("", true); }, [run]);

  const refresh = useCallback((direction: string) => run(direction, false), [run]);
  return { suggestions, picks, nextDate, busy, error, refresh };
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```
cd frontend && npx vitest run src/components/useSceneSuggestions.test.ts && npx tsc -b
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/useSceneSuggestions.ts frontend/src/components/useSceneSuggestions.test.ts
git commit -m "feat(scenes): the suggestions fetch/refresh hook, with sequencing

Only the newest request writes state -- the same discipline CampaignView's
sceneListSeq uses. Without it a slow initial ranked fetch lands after a
regenerate and replaces the directed cards with undirected ones.

A rank=false reply carries no picks and often no next_date; neither empty is
written, so a refresh cannot wipe the greeting ordering or a good date."
```

---

## Task 7: The pick pane

**Files:**
- Create: `frontend/src/components/SceneIdeaPicker.tsx`
- Test: `frontend/src/components/SceneIdeaPicker.test.tsx`

**Interfaces:**
- Consumes: `useSceneSuggestions` (Task 6), the draft constructors (Task 5), `api.availableGreetings`, `api.sceneIntent` (Task 4).
- Produces: `<SceneIdeaPicker cid afterSid ready pcless onPicked onCancel />` where `onPicked: (d: SceneDraft) => void`.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/SceneIdeaPicker.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SceneIdeaPicker } from "./SceneIdeaPicker";

vi.mock("../api/client", () => ({
  api: { availableGreetings: vi.fn(), sceneSuggestions: vi.fn(), sceneIntent: vi.fn() },
}));
import { api } from "../api/client";

const GREETINGS = [
  { id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true },
  { id: "open", name: "Open", available: true, reasons: [], unlocked: false },
];
const SUGGESTION = {
  title: "The creditor", premise: "A debt-collector arrives.", date: "2026-03-04",
  cast: [{ kind: "characters", id: "mara", name: "Mara" }],
  location: { id: "saltmarch", name: "Saltmarch" },
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.availableGreetings as any).mockResolvedValue(GREETINGS);
  (api.sceneSuggestions as any).mockResolvedValue({
    suggestions: [SUGGESTION], greeting_picks: [], next_date: "2026-01-01" });
  (api.sceneIntent as any).mockResolvedValue({
    title: "The morning after", date: "2026-03-04",
    location: { id: "saltmarch", name: "Saltmarch" }, cast: [] });
});

function renderPicker(onPicked = vi.fn(), ready = true) {
  render(<SceneIdeaPicker cid="c" afterSid="s1" ready={ready} pcless={false}
                          onPicked={onPicked} onCancel={() => {}} />);
  return onPicked;
}

test("picking a greeting emits a greeting draft", async () => {
  const onPicked = renderPicker();
  fireEvent.click(await screen.findByText("Reckoning"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "greeting", gid: "reck", title: "Reckoning", date: "2026-01-01" }));
});

test("picking a generated card emits its resolved metadata", async () => {
  const onPicked = renderPicker();
  fireEvent.click(await screen.findByText("The creditor"));
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "generated", location: "saltmarch", date: "2026-03-04",
    premise: "A debt-collector arrives." }));
});

test("Regenerate re-fetches with the direction and does not refetch greetings", async () => {
  renderPicker();
  await screen.findByText("The creditor");
  expect(api.availableGreetings).toHaveBeenCalledTimes(1);
  fireEvent.change(screen.getByLabelText("Direction"), { target: { value: "something at sea" } });
  fireEvent.click(screen.getByRole("button", { name: /regenerate/i }));
  await waitFor(() => expect(api.sceneSuggestions).toHaveBeenLastCalledWith(
    "c", "s1", false, "something at sea", false));
  expect(api.availableGreetings).toHaveBeenCalledTimes(1);
});

test("typed text runs the extraction and emits a custom draft", async () => {
  const onPicked = renderPicker();
  await screen.findByText("The creditor");
  fireEvent.change(screen.getByLabelText("Your own scene"),
                   { target: { value: "back at the marsh house" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "custom", title: "The morning after", location: "saltmarch",
    premise: "back at the marsh house" })));
});

test("empty text creates a blank draft with no LLM call", async () => {
  const onPicked = renderPicker();
  await screen.findByText("The creditor");
  fireEvent.click(screen.getByRole("button", { name: /create blank scene/i }));
  expect(api.sceneIntent).not.toHaveBeenCalled();
  expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "custom", title: "New scene", date: "2026-01-01", premise: "" }));
});

test("a failed extraction still opens with the typed text as the premise", async () => {
  (api.sceneIntent as any).mockRejectedValue({ detail: "no key" });
  const onPicked = renderPicker();
  await screen.findByText("The creditor");
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    source: "custom", title: "New scene", premise: "a storm" })));
});

test("without a connection the direction row is disabled but typing still works", async () => {
  const onPicked = renderPicker(vi.fn(), false);
  await screen.findByText("Reckoning");
  expect(api.sceneSuggestions).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /regenerate/i })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Your own scene"), { target: { value: "a storm" } });
  fireEvent.click(screen.getByRole("button", { name: /use this/i }));
  await waitFor(() => expect(onPicked).toHaveBeenCalledWith(expect.objectContaining({
    premise: "a storm", title: "New scene" })));
  expect(api.sceneIntent).not.toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```
cd frontend && npx vitest run src/components/SceneIdeaPicker.test.tsx
```

Expected: FAIL — cannot resolve `./SceneIdeaPicker`.

- [ ] **Step 3: Write the component**

`frontend/src/components/SceneIdeaPicker.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api, type Availability } from "../api/client";
import { errMsg } from "./errMsg";
import { customDraft, greetingDraft, suggestionDraft, type SceneDraft } from "./sceneDraft";
import { useSceneSuggestions } from "./useSceneSuggestions";

export function SceneIdeaPicker({ cid, afterSid, ready, pcless, onPicked, onCancel }: {
  cid: string;
  afterSid: string | null;
  ready: boolean;
  pcless: boolean;
  onPicked: (draft: SceneDraft) => void;
  onCancel: () => void;
}) {
  const [greetings, setGreetings] = useState<Availability[]>([]);
  const [direction, setDirection] = useState("");
  const [typed, setTyped] = useState("");
  const [inferring, setInferring] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { suggestions, picks, nextDate, busy, error: genError, refresh } =
    useSceneSuggestions(cid, afterSid, ready, pcless);

  useEffect(() => {
    api.availableGreetings(cid, afterSid ?? undefined)
      .then((all) => setGreetings(all.filter((g) => g.available && !!g.pcless === pcless)))
      .catch((err) => { setGreetings([]); setError(errMsg(err)); });
  }, [cid, afterSid, pcless]);

  // 4 slots: 2 greetings + 2 generated; greetings grow to 4 when nothing will generate
  const wantGenerated = ready && (suggestions === null || suggestions.length > 0);
  // with >2 available the LLM chooses; until it answers, show nothing rather than
  // cards that would shuffle. Empty/failed picks fall back to today's order.
  const rankPending = ready && greetings.length > 2 && picks === null;
  const picked = (picks ?? [])
    .map((id) => greetings.find((g) => g.id === id))
    .filter((g): g is Availability => g !== undefined);
  const orderedGreetings = picked.length ? picked : greetings;
  const greetingCards = rankPending ? [] : orderedGreetings.slice(0, wantGenerated ? 2 : 4);
  const generatedCards = (suggestions ?? []).slice(0, 4 - (rankPending ? 2 : greetingCards.length));

  async function useTyped() {
    const text = typed.trim();
    if (!text) {           // the blank path: no call, today's "Create manually"
      onPicked(customDraft("", null, nextDate, pcless));
      return;
    }
    if (!ready) {          // inference is an enhancement of this path, not a requirement
      onPicked(customDraft(text, null, nextDate, pcless));
      return;
    }
    setInferring(true);
    setError(null);
    try {
      const intent = await api.sceneIntent(cid, text, pcless);
      onPicked(customDraft(text, intent, nextDate, pcless));
    } catch (err: any) {
      // A miss must leave a usable form, never a dead end.
      setError(`${errMsg(err)} — continuing without inferred details.`);
      onPicked(customDraft(text, null, nextDate, pcless));
    } finally {
      setInferring(false);
    }
  }

  const shown = error ?? genError;
  return (
    <>
      {shown && <div className="banner">{shown}</div>}

      <div className="picker">
        <input type="text" aria-label="Direction" className="grow"
               placeholder="Steer the generated ideas — e.g. something at sea"
               value={direction} onChange={(e) => setDirection(e.target.value)} />
        <button className="subtle" disabled={!ready || busy}
                onClick={() => refresh(direction)}>↻ Regenerate</button>
      </div>

      <div className="role">From a greeting</div>
      {rankPending && <div className="field-hint">Choosing…</div>}
      {!rankPending && greetingCards.length === 0 && <div className="field-hint">No available greetings.</div>}
      {greetingCards.map((g) => (
        <button className="chooser-card" key={g.id}
                onClick={() => onPicked(greetingDraft(g, nextDate, pcless))}>
          <span className="chooser-card-title">{g.name}</span>
          {g.unlocked && <span className="chip on">unlocked</span>}
        </button>
      ))}

      <div className="role">Generated</div>
      {!ready && <div className="field-hint">Set up an LLM connection in Config to generate.</div>}
      {ready && suggestions === null && <div className="field-hint">Generating…</div>}
      {generatedCards.map((s, i) => (
        <button className="chooser-card" key={i}
                onClick={() => onPicked(suggestionDraft(s, nextDate, pcless))}>
          <span className="chooser-card-title">{s.title}</span>
          <span className="chooser-card-premise">{s.premise}</span>
          <span className="field-hint">
            {s.cast.map((c) => c.name).join(", ")}{s.location ? ` · ${s.location.name}` : ""}
          </span>
        </button>
      ))}

      <div className="role">Your own</div>
      <textarea aria-label="Your own scene" rows={3} value={typed}
                placeholder="Describe how the scene starts — the date and place are read from this."
                onChange={(e) => setTyped(e.target.value)} />

      <div className="form-actions">
        <button className="subtle" onClick={onCancel}>Cancel</button>
        <button className="primary" disabled={inferring} onClick={useTyped}>
          {inferring ? "…" : typed.trim() ? "Use this →" : "Create blank scene"}
        </button>
      </div>
    </>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```
cd frontend && npx vitest run src/components/SceneIdeaPicker.test.tsx && npx tsc -b
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SceneIdeaPicker.tsx frontend/src/components/SceneIdeaPicker.test.tsx
git commit -m "feat(scenes): the pick pane — direction, refresh, and a custom card (#316, #317, #89)

Writes nothing: every path emits a SceneDraft for the confirm form. The 'Your
own' textarea replaces the Create-manually button, running the extraction when
there is text and a connection, and falling straight through to a blank draft
when there is neither -- typing still works without a key."
```

---

## Task 8: The confirm pane and the create sequence

**Files:**
- Create: `frontend/src/components/SceneConfirmForm.tsx`
- Test: `frontend/src/components/SceneConfirmForm.test.tsx`

**Interfaces:**
- Consumes: `SceneDraft`, `BLANK_TITLE` (Task 5); `api.createScene`, `addCastBatch`, `setSceneLocation`, `setSceneDatetime`, `startFromGreeting`, `renameScene`, `deleteScene`, `listEntities`, `listCharacters`, `listCampaignPCs`.
- Produces: `<SceneConfirmForm cid draft onBack onCreated />` where `onCreated: (sid: string, initialPrompt?: string) => void` — the same signature `NewSceneChooser` reports today.

- [ ] **Step 1: Write the failing tests**

`frontend/src/components/SceneConfirmForm.test.tsx`:

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SceneConfirmForm } from "./SceneConfirmForm";
import type { SceneDraft } from "./sceneDraft";

vi.mock("../api/client", () => ({
  api: {
    createScene: vi.fn(), addCastBatch: vi.fn(), setSceneLocation: vi.fn(),
    setSceneDatetime: vi.fn(), startFromGreeting: vi.fn(), renameScene: vi.fn(),
    deleteScene: vi.fn(), listEntities: vi.fn(), listCharacters: vi.fn(),
    listCampaignPCs: vi.fn(),
  },
}));
vi.mock("./CalendarDatePicker", () => ({
  CalendarDatePicker: ({ value, onChange, ariaLabel }: any) =>
    <input aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} />,
}));
import { api } from "../api/client";

const GEN: SceneDraft = {
  source: "generated", title: "The creditor", defaultTitle: "The creditor",
  date: "2026-03-04", location: "saltmarch", pcless: false,
  premise: "A debt-collector arrives.",
  cast: [{ kind: "characters", id: "mara", name: "Mara" }],
};
const GRT: SceneDraft = {
  source: "greeting", gid: "reck", title: "Reckoning", defaultTitle: "Reckoning",
  date: "2026-03-04", location: "saltmarch", pcless: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  (api.createScene as any).mockResolvedValue({ id: "s9" });
  (api.addCastBatch as any).mockResolvedValue({ ok: true, added: 1, skipped: [] });
  (api.setSceneLocation as any).mockResolvedValue({ ok: true, moved: false, name: "" });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, id: "s9-dated" });
  (api.startFromGreeting as any).mockResolvedValue({ ok: true, id: "s9-greet" });
  (api.renameScene as any).mockResolvedValue({ id: "s9-titled", title: "T" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
  (api.listEntities as any).mockResolvedValue([{ id: "saltmarch", name: "Saltmarch" }]);
  (api.listCharacters as any).mockResolvedValue([{ id: "mara", name: "Mara" }]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
});

function renderForm(draft: SceneDraft, onCreated = vi.fn()) {
  render(<SceneConfirmForm cid="c" draft={draft} onBack={() => {}} onCreated={onCreated} />);
  return onCreated;
}

test("nothing is written until Create", async () => {
  renderForm(GEN);
  await screen.findByDisplayValue("The creditor");
  expect(api.createScene).not.toHaveBeenCalled();
});

test("Back writes nothing", async () => {
  const onBack = vi.fn();
  render(<SceneConfirmForm cid="c" draft={GEN} onBack={onBack} onCreated={vi.fn()} />);
  fireEvent.click(await screen.findByRole("button", { name: /back/i }));
  expect(onBack).toHaveBeenCalled();
  expect(api.createScene).not.toHaveBeenCalled();
});

test("a generated draft creates, casts, locates, dates, and hands off the premise", async () => {
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9-dated", "A debt-collector arrives."));
  expect(api.createScene).toHaveBeenCalledWith("c", "The creditor", "2026-03-04", false);
  expect(api.addCastBatch).toHaveBeenCalledWith("c", "s9", [{ kind: "characters", id: "mara" }]);
  expect(api.setSceneLocation).toHaveBeenCalledWith("c", "s9", "saltmarch");
  expect(api.setSceneDatetime).toHaveBeenCalledWith("c", "s9", "2026-03-04");
});

test("a greeting draft applies location and date BEFORE seeding", async () => {
  const onCreated = renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalled());
  const order = (api.setSceneDatetime as any).mock.invocationCallOrder[0];
  expect(order).toBeLessThan((api.startFromGreeting as any).mock.invocationCallOrder[0]);
  // seeded against the dated scene, and the confirmed title lands after the rename
  expect(api.startFromGreeting).toHaveBeenCalledWith("c", "s9-dated", "reck");
  expect(api.renameScene).toHaveBeenCalledWith("c", "s9-greet", "Reckoning");
  expect(onCreated).toHaveBeenCalledWith("s9-titled", undefined);
});

test("a greeting draft never hands a premise to the panel", async () => {
  const onCreated = renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith(expect.any(String), undefined));
});

test("the greeting pane offers no premise or cast control", async () => {
  renderForm(GRT);
  await screen.findByDisplayValue("Reckoning");
  expect(screen.queryByLabelText("Premise")).toBeNull();
  expect(screen.queryByLabelText("Add to cast")).toBeNull();
  expect(screen.getByLabelText("Location")).toBeInTheDocument();
});

test("an emptied title falls back to defaultTitle, not to the backend default", async () => {
  renderForm(GEN);
  fireEvent.change(await screen.findByLabelText("Title"), { target: { value: "  " } });
  fireEvent.click(screen.getByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", "The creditor", expect.any(String), false));
});

test("a cast failure deletes the scene", async () => {
  (api.addCastBatch as any).mockRejectedValue({ detail: "boom" });
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalledWith("c", "s9"));
  expect(onCreated).not.toHaveBeenCalled();
  expect(screen.getByText(/boom/)).toBeInTheDocument();
});

test("a date failure keeps the scene and offers Continue", async () => {
  (api.setSceneDatetime as any).mockRejectedValue({ detail: "bad date" });
  const onCreated = renderForm(GEN);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/bad date/);
  expect(api.deleteScene).not.toHaveBeenCalled();
  expect(onCreated).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /continue to scene/i }));
  expect(onCreated).toHaveBeenCalledWith("s9", "A debt-collector arrives.");
});

test("a startFromGreeting failure deletes the scene", async () => {
  (api.startFromGreeting as any).mockRejectedValue({ detail: "not available" });
  renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.deleteScene).toHaveBeenCalled());
});

test("a final rename failure keeps the scene", async () => {
  (api.renameScene as any).mockRejectedValue({ detail: "locked" });
  renderForm(GRT);
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await screen.findByText(/locked/);
  expect(api.deleteScene).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: /continue to scene/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the tests to verify they fail**

```
cd frontend && npx vitest run src/components/SceneConfirmForm.test.tsx
```

Expected: FAIL — cannot resolve `./SceneConfirmForm`.

- [ ] **Step 3: Write the component**

`frontend/src/components/SceneConfirmForm.tsx`:

```tsx
import { useEffect, useState } from "react";
import { api, type CharacterSummary, type EntitySummary, type PCSummary } from "../api/client";
import { CalendarDatePicker } from "./CalendarDatePicker";
import { errMsg } from "./errMsg";
import type { DraftCast, SceneDraft } from "./sceneDraft";

export function SceneConfirmForm({ cid, draft, onBack, onCreated }: {
  cid: string;
  draft: SceneDraft;
  onBack: () => void;
  onCreated: (sid: string, initialPrompt?: string) => void;
}) {
  const [title, setTitle] = useState(draft.title);
  const [date, setDate] = useState(draft.date);
  const [location, setLocation] = useState(draft.location);
  const [cast, setCast] = useState<DraftCast[]>(draft.source === "greeting" ? [] : draft.cast);
  const [premise, setPremise] = useState(draft.source === "greeting" ? "" : draft.premise);
  const [locations, setLocations] = useState<EntitySummary[]>([]);
  const [chars, setChars] = useState<CharacterSummary[]>([]);
  const [pcs, setPCs] = useState<PCSummary[]>([]);
  const [addId, setAddId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // set when the scene exists but a later, non-fatal step failed: the user
  // reads what went wrong, then goes to the scene anyway
  const [salvaged, setSalvaged] = useState<string | null>(null);

  useEffect(() => {
    api.listEntities({ kind: "campaign", id: cid }, "locations").then(setLocations).catch(() => setLocations([]));
    api.listCharacters({ kind: "campaign", id: cid }).then(setChars).catch(() => setChars([]));
    api.listCampaignPCs(cid).then(setPCs).catch(() => setPCs([]));
  }, [cid]);

  // pcless scenes never seat players, matching start_from_greeting's guards
  const addable: DraftCast[] = [
    ...chars.map((c) => ({ kind: "characters", id: c.id, name: c.name })),
    ...(draft.pcless ? [] : pcs.map((p) => ({ kind: "pcs", id: p.id, name: p.name }))),
  ].filter((o) => !cast.some((c) => c.kind === o.kind && c.id === o.id));

  async function create() {
    setBusy(true);
    setError(null);
    const finalTitle = title.trim() || draft.defaultTitle;
    let sid: string;
    try {
      // 1. the date also goes in as suggested_date, so a later failure still
      //    leaves CastPanel's date box pre-filled
      ({ id: sid } = await api.createScene(cid, finalTitle, date || undefined, draft.pcless));
    } catch (err: any) {
      setError(errMsg(err));
      setBusy(false);
      return;
    }
    // 2. cast — the last step for which deleting the scene is still clean
    if (draft.source !== "greeting" && cast.length) {
      try {
        await api.addCastBatch(cid, sid, cast.map((c) => ({ kind: c.kind, id: c.id })));
      } catch (err: any) {
        await api.deleteScene(cid, sid).catch(() => {});
        setError(errMsg(err));
        setBusy(false);
        return;
      }
    }
    // 3-4. location and date BEFORE seeding: start_from_greeting expands the
    //      greeting body through expand_macros, which resolves {{date}} from
    //      the scene's CURRENT moment. Seeding first dates it against nothing.
    //      Neither failure deletes: each is one independent piece of metadata.
    const soft: string[] = [];
    if (location) {
      try { await api.setSceneLocation(cid, sid, location); }
      catch (err: any) { soft.push(errMsg(err)); }
    }
    if (date) {
      try {
        const r = await api.setSceneDatetime(cid, sid, date);
        sid = r.id;
      } catch (err: any) { soft.push(errMsg(err)); }
    }
    // 5. seed. A failure here has written nothing outside the scene, so the
    //    scene goes; anything after has, so nothing does.
    if (draft.source === "greeting") {
      try {
        const r = await api.startFromGreeting(cid, sid, draft.gid);
        sid = r.id;
      } catch (err: any) {
        await api.deleteScene(cid, sid).catch(() => {});
        setError(errMsg(err));
        setBusy(false);
        return;
      }
      // The title field is what the user was looking at when they pressed
      // Create, so it is their intent whether or not they typed in it — and
      // start_from_greeting has just overwritten it with the greeting's name.
      try {
        const r = await api.renameScene(cid, sid, finalTitle);
        sid = r.id;
      } catch (err: any) { soft.push(errMsg(err)); }
    }
    setBusy(false);
    const prompt = draft.source === "greeting" ? undefined : (premise || undefined);
    if (soft.length) { setSalvaged(sid); setError(soft.join(" · ")); return; }
    onCreated(sid, prompt);
  }

  return (
    <>
      {error && <div className="banner">{error}</div>}

      <label className="role" htmlFor="confirm-title">Title</label>
      <input id="confirm-title" aria-label="Title" type="text" value={title}
             onChange={(e) => setTitle(e.target.value)} />

      <div className="role">When</div>
      <CalendarDatePicker scope={{ kind: "campaign", id: cid }} value={date}
                          onChange={setDate} ariaLabel="Scene date" />

      <div className="role">Where</div>
      <select aria-label="Location" value={location} onChange={(e) => setLocation(e.target.value)}>
        <option value="">— no location —</option>
        {locations.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>

      {draft.source === "greeting" ? (
        <div className="field-hint">
          The greeting supplies the opening post and seats its own cast.
        </div>
      ) : (
        <>
          <div className="role">In this scene</div>
          {cast.length === 0 && <div className="field-hint">No one cast yet.</div>}
          {cast.map((c) => (
            <span className="chip on" key={`${c.kind}/${c.id}`}>
              {c.name}
              <button className="subtle" aria-label={`Remove ${c.name}`}
                      onClick={() => setCast(cast.filter((x) => !(x.kind === c.kind && x.id === c.id)))}>×</button>
            </span>
          ))}
          <div className="picker">
            <select aria-label="Add to cast" value={addId}
                    onChange={(e) => setAddId(e.target.value)}>
              <option value="">— pick —</option>
              {addable.map((o) => (
                <option key={`${o.kind}/${o.id}`} value={`${o.kind}/${o.id}`}>{o.name}</option>
              ))}
            </select>
            <button className="primary" disabled={!addId} onClick={() => {
              const found = addable.find((o) => `${o.kind}/${o.id}` === addId);
              if (found) setCast([...cast, found]);
              setAddId("");
            }}>Add</button>
          </div>

          <label className="role" htmlFor="confirm-premise">Premise</label>
          <textarea id="confirm-premise" aria-label="Premise" rows={3} value={premise}
                    onChange={(e) => setPremise(e.target.value)} />
          <div className="field-hint">Seeds the opener box once the scene exists.</div>
        </>
      )}

      <div className="form-actions">
        {salvaged ? (
          <button className="primary" onClick={() =>
            onCreated(salvaged, draft.source === "greeting" ? undefined : (premise || undefined))}>
            Continue to scene
          </button>
        ) : (
          <>
            <button className="subtle" disabled={busy} onClick={onBack}>← Back</button>
            <button className="primary" disabled={busy} onClick={create}>
              {busy ? "…" : "Create scene"}
            </button>
          </>
        )}
      </div>
    </>
  );
}
```

- [ ] **Step 4: Run the tests to verify they pass**

```
cd frontend && npx vitest run src/components/SceneConfirmForm.test.tsx && npx tsc -b
```

Expected: PASS. If `EntitySummary`, `CharacterSummary`, or `PCSummary` lack a `name`, read the real types in `frontend/src/api/client.ts` and adjust — do not add `any`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SceneConfirmForm.tsx frontend/src/components/SceneConfirmForm.test.tsx
git commit -m "feat(scenes): the confirm pane and the create sequence (#90, #23)

Nothing is written until Create. The date is APPLIED through set_datetime
rather than left as a hint, which is #23 -- and it is applied before seeding,
because start_from_greeting expands the greeting body through expand_macros,
which resolves {{date}} from the scene's current moment.

The cleanup rule: delete the scene only while nothing irreversible has
happened to it. Deleting a scene that already holds a played greeting's body
is what stranded the greeting's played mark; those steps now surface the error
and offer Continue to scene instead."
```

---

## Task 9: Rewire the orchestrator

**Files:**
- Modify: `frontend/src/components/NewSceneChooser.tsx` (replace its body)
- Modify: `frontend/src/components/NewSceneChooser.test.tsx`
- Modify: `frontend/src/index.css`
- Test: the full suite

**Interfaces:**
- Consumes: `SceneIdeaPicker` (Task 7), `SceneConfirmForm` (Task 8).
- Produces: `<NewSceneChooser cid afterSid ready onClose onCreated />` — unchanged props, so `CampaignView.tsx` needs no edit.

- [ ] **Step 1: Rewrite the chooser test around the new flow**

Replace `frontend/src/components/NewSceneChooser.test.tsx`'s body with flow-level tests; the card and form details are covered by Tasks 7 and 8.

```tsx
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { NewSceneChooser } from "./NewSceneChooser";

vi.mock("../api/client", () => ({
  api: {
    availableGreetings: vi.fn(), sceneSuggestions: vi.fn(), sceneIntent: vi.fn(),
    createScene: vi.fn(), startFromGreeting: vi.fn(), addCastBatch: vi.fn(),
    setSceneLocation: vi.fn(), setSceneDatetime: vi.fn(), renameScene: vi.fn(),
    deleteScene: vi.fn(), listEntities: vi.fn(), listCharacters: vi.fn(),
    listCampaignPCs: vi.fn(),
  },
}));
vi.mock("./CalendarDatePicker", () => ({
  CalendarDatePicker: ({ value, onChange, ariaLabel }: any) =>
    <input aria-label={ariaLabel} value={value} onChange={(e) => onChange(e.target.value)} />,
}));
import { api } from "../api/client";

beforeEach(() => {
  vi.clearAllMocks();
  (api.availableGreetings as any).mockResolvedValue(
    [{ id: "reck", name: "Reckoning", available: true, reasons: [], unlocked: true }]);
  (api.sceneSuggestions as any).mockResolvedValue(
    { suggestions: [], greeting_picks: [], next_date: "2026-01-01" });
  (api.createScene as any).mockResolvedValue({ id: "s9" });
  (api.startFromGreeting as any).mockResolvedValue({ ok: true, id: "s9" });
  (api.renameScene as any).mockResolvedValue({ id: "s9", title: "Reckoning" });
  (api.setSceneDatetime as any).mockResolvedValue({ ok: true, id: "s9" });
  (api.deleteScene as any).mockResolvedValue({ ok: true });
  (api.listEntities as any).mockResolvedValue([]);
  (api.listCharacters as any).mockResolvedValue([]);
  (api.listCampaignPCs as any).mockResolvedValue([]);
});

test("mode is chosen first and nothing is fetched before it", () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  expect(screen.getByText("With your PC")).toBeInTheDocument();
  expect(api.availableGreetings).not.toHaveBeenCalled();
});

test("picking a card opens the confirm form and creates nothing yet", async () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  await screen.findByRole("button", { name: /create scene/i });
  expect(api.createScene).not.toHaveBeenCalled();
});

test("Back returns to the picker without writing", async () => {
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  fireEvent.click(await screen.findByRole("button", { name: /back/i }));
  await screen.findByText("Reckoning");
  expect(api.createScene).not.toHaveBeenCalled();
});

test("offscreen mode asks for pcless greetings and pcless scenes", async () => {
  (api.availableGreetings as any).mockResolvedValue(
    [{ id: "cabal", name: "Cabal", available: true, reasons: [], unlocked: false, pcless: true }]);
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={() => {}} onCreated={() => {}} />);
  fireEvent.click(screen.getByText("Offscreen (NPCs only)"));
  fireEvent.click(await screen.findByText("Cabal"));
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(api.createScene).toHaveBeenCalledWith("c", "Cabal", expect.anything(), true));
});

test("creating reports the scene and Escape closes while idle", async () => {
  const onCreated = vi.fn();
  const onClose = vi.fn();
  render(<NewSceneChooser cid="c" afterSid="s1" ready onClose={onClose} onCreated={onCreated} />);
  fireEvent.keyDown(window, { key: "Escape" });
  expect(onClose).toHaveBeenCalled();
  fireEvent.click(screen.getByText("With your PC"));
  fireEvent.click(await screen.findByText("Reckoning"));
  fireEvent.click(await screen.findByRole("button", { name: /create scene/i }));
  await waitFor(() => expect(onCreated).toHaveBeenCalledWith("s9", undefined));
});
```

- [ ] **Step 2: Run to verify it fails**

```
cd frontend && npx vitest run src/components/NewSceneChooser.test.tsx
```

Expected: FAIL — the old chooser has no confirm step, so "Create scene" is never found.

- [ ] **Step 3: Replace the chooser with the orchestrator**

`frontend/src/components/NewSceneChooser.tsx`, in full:

```tsx
import { useEffect, useState } from "react";
import { SceneConfirmForm } from "./SceneConfirmForm";
import { SceneIdeaPicker } from "./SceneIdeaPicker";
import type { SceneDraft } from "./sceneDraft";

/** Mode → pick → confirm → create. Props are unchanged from the
 *  commit-on-click version, so CampaignView's usage is untouched. */
export function NewSceneChooser({ cid, afterSid, ready, onClose, onCreated }: {
  cid: string;
  afterSid: string | null;          // ranking reference: the selected (or latest) scene
  ready: boolean;
  onClose: () => void;
  onCreated: (sid: string, initialPrompt?: string) => void;
}) {
  // scene mode is picked first; nothing is fetched until then
  const [mode, setMode] = useState<"pc" | "offscreen" | null>(null);
  const [draft, setDraft] = useState<SceneDraft | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="chooser-backdrop" role="dialog" aria-label="New scene"
         onClick={onClose}>
      <div className="chooser" onClick={(e) => e.stopPropagation()}>
        <h3>New scene</h3>

        {mode === null ? (
          <>
            <div className="role">What kind of scene?</div>
            <button className="chooser-card" onClick={() => setMode("pc")}>
              <span className="chooser-card-title">With your PC</span>
              <span className="chooser-card-premise">Your player character takes part.</span>
            </button>
            <button className="chooser-card" onClick={() => setMode("offscreen")}>
              <span className="chooser-card-title">Offscreen (NPCs only)</span>
              <span className="chooser-card-premise">
                What happens away from your PC — NPC plans, motivations, and events you don't witness.
              </span>
            </button>
            <div className="form-actions">
              <button className="subtle" onClick={onClose}>Cancel</button>
            </div>
          </>
        ) : draft === null ? (
          <SceneIdeaPicker cid={cid} afterSid={afterSid} ready={ready}
                           pcless={mode === "offscreen"}
                           onPicked={setDraft} onCancel={onClose} />
        ) : (
          <SceneConfirmForm cid={cid} draft={draft}
                            onBack={() => setDraft(null)} onCreated={onCreated} />
        )}
      </div>
    </div>
  );
}
```

The backdrop and Escape no longer gate on a `busy` flag: the confirm form disables its own controls while creating, and the picker writes nothing, so dismissing at any point is safe.

- [ ] **Step 4: Add the styles**

In `frontend/src/index.css`, beside the existing `.chooser` rules (~line 1082):

```css
.chooser .picker input.grow { flex: 1; }
.chooser textarea { width: 100%; background: transparent; border: 1px solid var(--muted); color: inherit; padding: 8px; font: inherit; }
.chooser select { width: 100%; }
.chooser label.role { display: block; }
```

- [ ] **Step 5: Run the full frontend suite**

```
cd frontend && npm run test:coverage && npx tsc -b
```

Expected: PASS. `CampaignView.test.tsx` exercises the chooser through the campaign view — if it drove the old immediate-create flow, update it to click through the confirm step; do not stub the confirm form away.

- [ ] **Step 6: Run the whole gate**

```
make check
```

Expected: `check-py`, `check-web`, `check-lint`, `check-templates`, and `check-pydantic1` all pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/NewSceneChooser.tsx frontend/src/components/NewSceneChooser.test.tsx frontend/src/index.css frontend/src/routes/
git commit -m "feat(scenes): the picker becomes mode -> pick -> confirm -> create (#89, #90)

NewSceneChooser keeps its props and drops to an orchestrator; the card grid,
the create sequence, and the suggestions state each moved to their own file.
CampaignView is untouched.

Closes #315, #316, #317, #89, #90, #23."
```

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task: #315 self-exclusion and the recovery path → Task 1; the offscreen adjacent fix → Task 2; `direction`/`rank` and their templates → Task 3; `/scene-intent`, its templates and parser → Task 4; the `SceneDraft` union, the construction table and `defaultTitle` → Task 5; request sequencing → Task 6; the pick pane's four regions → Task 7; the confirm pane, the create sequence, the cleanup rule and #23's `set_datetime` application → Task 8; the orchestrator and styles → Task 9. The spec's Accepted risks are deliberately not implemented — that is what makes them accepted.

**Type consistency.** `SceneDraft`, `DraftCast`, `BLANK_TITLE` and the three constructors are defined in Task 5 and used under those exact names in Tasks 7, 8 and 9. `_token_ok`'s four-parameter signature is fixed in Task 2 and called unchanged in Task 4. `api.sceneSuggestions(cid, after?, offscreen?, direction?, rank?)` is defined in Task 3 and called with all five arguments in Task 6's assertions. `api.sceneIntent(cid, text, offscreen)` returns `SceneIntentResult`, which is exactly what `customDraft` accepts. `onCreated(sid, initialPrompt?)` is unchanged from today's chooser throughout.

**Known follow-ups**, deliberately out of scope: `store.playing` remains in `locks.UNREVIEWED`; the ledger (#88) and adapted greetings (#91) are untouched.
