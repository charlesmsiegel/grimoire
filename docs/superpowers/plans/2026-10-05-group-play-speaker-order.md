# Group Play Speaker Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Per-scene speaker order modes (directed / manual / list / natural), talkativeness, sit-out, one-tap reply-as chips, and opt-in server-side auto-continue rounds with Stop.

**Architecture:** A pure `store/group_play.py` owns settings parsing/validation and round planning. The settings live as one JSON frontmatter field on the scene file. `routes/character_turns.py` asks the planner at round start, stores the plan on the round record, follows it instead of handoffs in list/natural, and chains follow-on rounds inside the same detached run. The frontend replaces the Respond-as dropdown with chips and adds a Group panel.

**Tech Stack:** FastAPI + pydantic (v1/v2-agnostic), pytest with `tests/llm_fakes.FakeLLM`; React + vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-group-play-speaker-order-design.md`

## Global Constraints

- Pydantic: plain `BaseModel` fields only, dumped via `routes.common._dump` (no `Field`, validators, `model_dump()`).
- Every store write goes through `store.atomic`; scene-file writes are `@locking._serialized` in `store/scenes/write.py`.
- Imports at module scope; inside `store/`, cross-package imports bind a submodule (`from .context import speaker`, then `speaker.mentioned(...)`). `store/scenes/*` must NOT import `group_play` (it would close a cycle through `store.context`); routes compose the two.
- Fake the LLM only with `tests/llm_fakes.py` (`FakeLLM` answers by call order; `fake.requests[i]["messages"]`).
- Frontend: no `keydown` listeners outside `src/shortcuts/`; shared mocks in `src/testkit/`; run vitest from `frontend/`.
- Lint gates are ratcheted: a new finding fails `check-lint`/`check-mypy`/`check-eslint`; a removed one requires `make baseline` committed with the fix.
- Invented names only in fixtures and docs (Mara, Winifred, Seraphine, Realm, Saltmarch).
- Defaults: `order="directed"`, talkativeness `50`, `auto_rounds` `0..5`, default `0`. A scene with no `group_play` field behaves exactly as today.

## Review Focus

1. A character who leaves and rejoins, or is renamed in the cast, keeps settings keyed by ref, and a stale ref in `order_list` / `sitting_out` for an absent actor never crashes planning. Covered: Task 2 `test_unknown_refs_are_ignored_by_planning`.
2. Retry after a Stop in the middle of an auto chain must finish only the interrupted reply, never start new rounds. Covered: Task 5 `test_stop_zeroes_remaining_rounds_and_retry_starts_none`.
3. A Directed follow-on led by a character whom the talkativeness roll would filter out must still be named correctly (not stamped "Grimoire"). Covered: Task 5 `test_repeat_handoff_leads_next_round` (asserts the speaker label), via `force=` in Task 2.
4. Editing settings while a chain runs must not be refused with `scene_busy`. Covered: Task 3 `test_put_group_accepted_while_a_run_holds_the_scene`.
5. Hand-edited or corrupt `group_play` frontmatter must degrade to defaults, not 500 the play view. Covered: Task 2 `test_parse_is_lenient_per_key`, Task 3 `test_get_group_survives_corrupt_frontmatter`.

---

### Task 1: Name and silence helpers in `speaker.py`

**Files:**
- Modify: `backend/src/grimoire/store/context/speaker.py`
- Test: `backend/tests/test_context_speaker.py`

**Interfaces:**
- Produces: `speaker.mentioned(text: str, names: list[str]) -> list[str]`, the present names a text names, in order of first mention, each once (whole word, using `_name_labels`, so an ambiguous label names nobody). `speaker.quietest(names: list[str], history: list[dict]) -> list[str]`, all names, longest silent first (the existing ranking: silence desc, blocks said asc, cast order). `nominate` is refactored to use `quietest` with unchanged behaviour.

- [ ] **Step 1: Write failing tests**

```python
def test_mentioned_lists_every_named_npc_in_mention_order():
    names = ["Seraphine Vale", "Mara", "Winifred"]
    assert speaker.mentioned("Winifred, ask Mara. Winifred again.", names) == ["Winifred", "Mara"]
    assert speaker.mentioned("the maraud was loud", names) == []
    assert speaker.mentioned("Seraphine?", names) == ["Seraphine Vale"]

def test_quietest_ranks_never_spoken_first_then_longest_silence():
    history = [_npc("Mara", "a"), _npc("Winifred", "b")]
    assert speaker.quietest(["Mara", "Winifred", "Seraphine Vale"], history) == [
        "Seraphine Vale", "Mara", "Winifred"]
```

(Use the file's existing `_player` helper; add `_npc(name, text)` returning `{"role": "assistant", "speaker": name, "content": text}` if no equivalent exists.)

- [ ] **Step 2: Run** `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_context_speaker.py -q`. Expected: the 2 new tests FAIL (AttributeError).
- [ ] **Step 3: Implement** both functions. Order mentions by the earliest `re.search(...).start()` over all labels that resolve to each name. Move the ranking body out of `nominate` into `quietest`.
- [ ] **Step 4: Run** the same command. Expected: all pass, including every existing `nominate` test.
- [ ] **Step 5: Commit** `feat(speaker): expose mention order and silence ranking`

---

### Task 2: Pure settings and planner, `store/group_play.py`

**Files:**
- Create: `backend/src/grimoire/store/group_play.py`
- Modify: `backend/src/grimoire/store/__init__.py` (import + `__all__`, alphabetical); update `tests/test_store_api_baseline.py`'s baseline if it enumerates modules
- Test: `backend/tests/test_group_play.py`

**Interfaces:**
- Consumes: `speaker.mentioned`, `speaker.quietest` (Task 1).
- Produces (a roster entry is `{"ref": "kind:id", "name": str}`, the shape of `character_turns.roster`; `"grimoire"` is never in the roster and is always available):
  - `ORDERS = ("directed", "manual", "list", "natural")`, `DEFAULT_TALKATIVENESS = 50`, `MAX_AUTO_ROUNDS = 5`
  - `parse(raw: str) -> dict`: lenient; always returns all five keys. Malformed JSON → defaults; each bad key → that key's default; talkativeness entries outside 0..100 or non-int are dropped.
  - `settings_of(meta: dict) -> dict`, which is `parse(meta.get("group_play", ""))`.
  - `validate(body: dict) -> dict`: strict; raises `ValueError(msg)` on unknown order, non-string/empty ref, talkativeness not an int in 0..100, `auto_rounds` not in 0..5, or `"grimoire"` in `sitting_out`. Dedupes list refs, keeping first occurrence.
  - `dump(settings: dict) -> str`: `json.dumps(..., sort_keys=True, separators=(",", ":"))`.
  - `available(settings, roster) -> list[dict]`: roster minus `sitting_out`, roster order.
  - `plan_post(settings, roster, *, trigger: str, history: list[dict], rng: random.Random, force: tuple[str, ...] = ()) -> dict` returns `{"mode", "eligible": list[dict], "actor_ref": str | None, "plan": list[str]}`:
    - directed: `eligible` = available entries that are named in `trigger` or in `force`, or pass `rng.random() * 100 < talkativeness`. If that is empty but available is not, keep the single highest-talkativeness entry (ties broken by `quietest`). `actor_ref=None`, `plan=[]`.
    - manual: `eligible=roster`, `actor_ref=None`, `plan=[]`.
    - list / natural: `eligible=roster`; `order` = the mode's sequence (below); `actor_ref=order[0]`, `plan=order[1:]`. `force` refs are removed from `order` (the caller supplies the lead).
    - list sequence: `order_list` refs that are available or `"grimoire"`, then available refs missing from it, in roster order; `["grimoire"]` if empty.
    - natural sequence: refs named in `trigger` (mention order, available only); then the other available refs after `rng.shuffle`, each kept if `rng.random() * 100 < talkativeness`; if empty, `[quietest(available)[0]]`; `["grimoire"]` if nothing is available.
  - `plan_continue(settings, roster, *, last: dict | None, history: list[dict], rng) -> str | None`. `last` is `{"ref": str | None, "text": str}` for the newest non-synthetic contribution. Directed / manual → `None` (the caller uses the selector). List → the next available ref in the list sequence after `last["ref"]`, wrapping, or the head when there is no `last`. Natural → the first ref of the natural sequence over `last["text"]`, excluding `last["ref"]`.
  - `next_planned(settings, roster, plan: list[str]) -> tuple[str | None, list[str]]`: pops the first ref still available (present and not sitting out, or `"grimoire"`) and returns it with the remainder after it.

- [ ] **Step 1: Write failing tests** (seeded `random.Random(0)` where randomness matters; talkativeness 0/100 for determinism):

```python
R = [{"ref": "characters:mara", "name": "Mara"}, {"ref": "characters:winifred", "name": "Winifred"},
     {"ref": "characters:seraphine", "name": "Seraphine Vale"}]

def test_parse_is_lenient_per_key():
    s = group_play.parse('{"order":"bogus","auto_rounds":9,"talkativeness":{"characters:mara":150,"characters:winifred":20}}')
    assert s == {"order": "directed", "order_list": [], "sitting_out": [], "auto_rounds": 0,
                 "talkativeness": {"characters:winifred": 20}}
    assert group_play.parse("{not json") == group_play.parse("")

def test_validate_refuses_out_of_range():
    for bad in ({"auto_rounds": 6}, {"order": "chaos"}, {"talkativeness": {"characters:mara": 101}},
                {"sitting_out": ["grimoire"]}):
        with pytest.raises(ValueError):
            group_play.validate({**group_play.parse(""), **bad})

def test_list_order_then_newcomers_and_sit_out():
    s = {**group_play.parse(""), "order": "list", "order_list": ["characters:winifred"],
         "sitting_out": ["characters:seraphine"]}
    p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))
    assert (p["actor_ref"], p["plan"]) == ("characters:winifred", ["characters:mara"])

def test_natural_named_first_then_talkative_only():
    s = {**group_play.parse(""), "order": "natural",
         "talkativeness": {"characters:mara": 0, "characters:winifred": 100, "characters:seraphine": 0}}
    p = group_play.plan_post(s, R, trigger="Seraphine, look.", history=[], rng=random.Random(0))
    assert [p["actor_ref"], *p["plan"]] == ["characters:seraphine", "characters:winifred"]

def test_natural_nobody_joins_falls_back_to_quietest():
    s = {**group_play.parse(""), "order": "natural", "talkativeness": {r["ref"]: 0 for r in R}}
    history = [{"role": "assistant", "speaker": "Mara", "content": "x"},
               {"role": "assistant", "speaker": "Winifred", "content": "y"}]
    p = group_play.plan_post(s, R, trigger="", history=history, rng=random.Random(0))
    assert (p["actor_ref"], p["plan"]) == ("characters:seraphine", [])

def test_directed_keeps_named_and_force_and_never_empties():
    silent = {**group_play.parse(""), "talkativeness": {r["ref"]: 0 for r in R}}
    p = group_play.plan_post(silent, R, trigger="Mara?", history=[], rng=random.Random(0), force=("characters:seraphine",))
    assert {e["ref"] for e in p["eligible"]} == {"characters:mara", "characters:seraphine"}
    s = {**silent, "talkativeness": {**silent["talkativeness"], "characters:winifred": 10}}
    p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))  # kept by roll or by fallback
    assert [e["ref"] for e in p["eligible"]] == ["characters:winifred"]

def test_everyone_sitting_out_means_grimoire():
    s = {**group_play.parse(""), "order": "list", "sitting_out": [r["ref"] for r in R]}
    assert group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))["actor_ref"] == "grimoire"

def test_plan_continue_rules():
    base = group_play.parse("")
    assert group_play.plan_continue(base, R, last=None, history=[], rng=random.Random(0)) is None
    lst = {**base, "order": "list"}
    assert group_play.plan_continue(lst, R, last={"ref": "characters:winifred", "text": ""},
                                    history=[], rng=random.Random(0)) == "characters:seraphine"
    assert group_play.plan_continue(lst, R, last={"ref": "characters:seraphine", "text": ""},
                                    history=[], rng=random.Random(0)) == "characters:mara"  # wraps
    nat = {**base, "order": "natural", "talkativeness": {r["ref"]: 0 for r in R}}
    assert group_play.plan_continue(nat, R, last={"ref": "characters:mara", "text": "Mara asks Winifred."},
                                    history=[], rng=random.Random(0)) == "characters:winifred"

def test_unknown_refs_are_ignored_by_planning():
    s = {**group_play.parse(""), "order": "list", "order_list": ["characters:ghost", "characters:mara"],
         "sitting_out": ["characters:ghost"]}
    p = group_play.plan_post(s, R, trigger="", history=[], rng=random.Random(0))
    assert "characters:ghost" not in [p["actor_ref"], *p["plan"]]

def test_next_planned_skips_newly_sitting_out():
    s = {**group_play.parse(""), "sitting_out": ["characters:winifred"]}
    assert group_play.next_planned(s, R, ["characters:winifred", "grimoire", "characters:mara"]) == (
        "grimoire", ["characters:mara"])
```

- [ ] **Step 2: Run** `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_group_play.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `group_play.py` per the Interfaces. Map names to refs through each roster entry's `name` when calling `speaker.mentioned` / `quietest`. Add a module docstring stating it is pure (no store reads or writes), so `test_lock_domain_guard` needs no entry.
- [ ] **Step 4: Run** the same tests, then `tests/test_import_guard.py tests/test_store_api_baseline.py tests/test_lock_domain_guard.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(group-play): pure settings and speaker planner`

**Amendments (review, fix round on Task 2's module — `store/group_play.py`):**
- `plan_post(..., author: str | None = None)`: a ref equal to `author` is never counted as named (a character naming themselves does not count). Test `test_self_mention_is_not_named`: natural, all talkativeness 0, `author="characters:mara"`, trigger "Mara thinks." → Mara is not first (falls to the quietest-other fallback).
- Directed `eligible` keeps every `force` ref that is present in the roster even when it is in `sitting_out` (explicit pick wins). Test `test_forced_sitting_out_actor_stays_eligible`.
- `plan_continue` list mode locates `last["ref"]` in the FULL configured sequence (order_list refs then roster refs, before filtering availability), then scans forward, wrapping, to the next available ref. Test `test_list_continue_anchor_survives_last_speaker_sitting_out`: order_list `[mara, winifred, seraphine]`, Winifred sits out, last=Winifred → Seraphine.

---

### Task 3: Storage and `/group` routes

**Files:**
- Modify: `backend/src/grimoire/store/scenes/write.py`, plus re-export from `store/scenes/__init__.py` beside `set_response`
- Modify: `backend/src/grimoire/routes/models.py` (add `GroupSettings`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (two routes on its `router`)
- Test: `backend/tests/test_group_play_routes.py`

**Interfaces:**
- Consumes: `group_play.validate / dump / settings_of` (Task 2).
- Produces: `store.scenes.set_group(cid: str, sid: str, raw: str) -> None` (`@locking._serialized`, writes `meta["group_play"] = raw`, `SceneNotFound` like `set_response`). `GroupSettings(BaseModel)`: `order: str = "directed"`, `order_list: list[str] = []`, `talkativeness: dict[str, int] = {}`, `sitting_out: list[str] = []`, `auto_rounds: int = 0`. `GET /api/campaigns/{cid}/scenes/{sid}/group` → settings dict. `PUT` same path, body `GroupSettings` → `{"ok": True, "settings": <validated>}`; 400 `{"kind": "invalid_group", "detail": msg}` on `ValueError`; 404 via `_require_scene`. No `scene_held_free`.

- [ ] **Step 1: Write failing tests** (seed like `test_character_turns.seed`):
  - `test_get_group_defaults`: GET → `{"order": "directed", "order_list": [], "talkativeness": {}, "sitting_out": [], "auto_rounds": 0}`.
  - `test_put_group_round_trips_and_survives_rename`: PUT natural + auto_rounds 2 → GET equals; `store.scenes.read_scene(...)["meta"]["group_play"]` is the compact JSON. Rename the scene through its existing rename route; GET at the new sid returns the same settings.
  - `test_put_group_rejects_invalid`: `auto_rounds: 9` → 400, kind `invalid_group`; stored settings unchanged.
  - `test_put_group_accepted_while_a_run_holds_the_scene`: with the `held_scene` fixture pattern from `test_scene_freeze.py`, PUT → 200.
  - `test_get_group_survives_corrupt_frontmatter`: write `group_play: "{oops"` via `set_group(cid, sid, "{oops")` → GET 200 with defaults.
- [ ] **Step 2: Run** `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_group_play_routes.py -q`. Expected: FAIL (404 route).
- [ ] **Step 3: Implement.** GET reads `_require_scene(cid, sid)["meta"]` through `settings_of`. PUT runs `validate(_dump(body))`, then `set_group(cid, sid, dump(validated))`.
- [ ] **Step 4: Run** the tests plus `tests/test_scene_freeze.py tests/test_pydantic_guard.py tests/test_atomic_guard.py tests/test_lock_domain_guard.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(group-play): per-scene settings routes`

---

### Task 4: Engine follows the order modes

**Files:**
- Modify: `backend/src/grimoire/store/responses.py` (`new_round`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`start`, `_compose`, `_first_actor`, `_frames` successor block, `_recover_completed`)
- Modify: `backend/src/grimoire/routes/scenes.py:794-799` (pass `kind` / `trigger`)
- Test: `backend/tests/test_group_play_turns.py`

**Interfaces:**
- Consumes: Task 2 planner, Task 3 settings in scene meta.
- Produces:
  - `new_round(..., mode="directed", plan=(), auto_remaining=0, round_index=1, auto_total=0)` stores those keys. Readers use `.get(key, default)` so legacy rounds act as directed.
  - `start(..., kind: str = "note", trigger: str = "")`, where kind ∈ `post | continue | note`. In `scenes.py`: `kind="post"` when not ephemeral; `"continue"` when ephemeral and `content` is empty; else `"note"`. `trigger=content`.
  - `character_turns._rng: Callable[[], random.Random] = random.Random`, the seam tests monkeypatch.
  - `_successor(cid, sid, round_record, handoff, cancelled: bool) -> dict` returns `{"next": str | None, "issue": str | None, "plan": list[str], "handed_back": bool}`. It is used by both `_frames` and `_recover_completed`, replacing their duplicated `validate_handoff` blocks.

Planning in `start` when `round_record is None` (under the existing lock, reading `settings_of(read_scene(...)["meta"])`, `roster`, scene messages):
- `actor_ref` given (explicit / Respond as / replay): no planning for the lead. If `kind == "post"`, call `plan_post(..., force=(actor_ref,))` and keep its `mode`, `eligible` and `plan`. In manual mode use `plan=[]`.
- `kind == "post"`, no actor: `plan_post`. If mode is list/natural, `actor_ref = result["actor_ref"]`.
- `kind == "continue"` or `"note"` with no actor: the round's `eligible` is `available(settings, roster)` (sitting-out characters never reach the selector).
- `kind == "continue"`: `last` = newest assistant message whose speaker is not synthetic, with ref from `store.responses.actor_refs(cid, sid).get(m.get("response_id"))`; `actor_ref = plan_continue(...)` (None → selector, as today). `mode` recorded, `plan=[]`.
- `kind == "note"`: otherwise unchanged (selector).

Engine rules:
- `_compose`: offer handoff candidates only when `automatic` and `mode == "directed"`.
- `_first_actor`: when `actor_ref` is None and `mode == "manual"` and `automatic`, return `(None, round with status "complete")` without calling `_select`.
- `_successor`, list/natural: `next_planned(current settings, current roster, round_record["plan"])`; the handoff is ignored; `handed_back=False`.
- `_successor`, directed: today's `validate_handoff` against round `eligible` minus refs now in `sitting_out` (+ grimoire); `handed_back = next is None and issue is None`.
- Either way, `next` is forced to None unless `automatic and not cancelled`. Persist `plan` on the round with the other `_round_state` fields.
- Before each speaker, in addition to the existing left-the-scene check, if the planned actor is now sitting out and the round is automatic and was not explicitly targeted (i.e. `mode` in list/natural), advance with `next_planned` instead of generating.

- [ ] **Step 1: Write failing tests** (seed with Mara + Winifred via a copy of `test_character_turns.seed`; set settings with `client.put(base + "/group", json=...)`):
  - `test_list_mode_speaks_in_order_without_selector`: list `order_list` `[winifred, mara]`; FakeLLM two replies each ending with a handoff naming the other. Assert `fake.calls == 2`, speakers `["Winifred", "Mara"]`, and that no request contains `"Choose at most one initial speaker"`.
  - `test_list_mode_prompt_offers_no_handoff_candidates`: the `Eligible next speakers:` section of request 0 contains no `characters:` and no `grimoire`.
  - `test_natural_ignores_handoff_and_puts_named_first`: natural, both talkativeness 0, post `"Winifred?"`; one reply whose handoff names Mara. Assert `fake.calls == 1` and speaker Winifred.
  - `test_manual_post_generates_nothing`: manual; POST chat `"Hello"` → 200, `fake.calls == 0`, the transcript ends with the user post, and the round status is `complete`.
  - `test_manual_reply_as_generates_one`: manual; POST `{"speaker_ref": "characters:mara"}` → one call.
  - `test_directed_talkativeness_zero_filters_selector_roster`: directed, Winifred talkativeness 0, Mara 100, post `"Hello"`. A single eligible means no selector: assert `fake.calls == 1` and speaker Mara.
  - `test_continue_in_list_mode_picks_next_after_last_speaker`: list; seed one Mara response by POSTing `{"speaker_ref": "characters:mara"}`, then POST empty → speaker Winifred, no selector call.
  - `test_sitting_out_mid_round_is_skipped`: list `[mara, winifred]`. Drive `_frames` directly as in `test_single_npc_skips_selector_and_stop_blocks_successor`; on Mara's `response_end` frame call `store.scenes.set_group(cid, sid, group_play.dump({... sitting_out: [winifred]}))`. Assert `fake.calls == 1`.
  - `test_continue_selector_never_sees_sitting_out`: directed, Winifred sits out, empty POST → one call (single eligible means no selector) and the speaker is Mara.
  - `test_retry_resumes_plan_without_replanning`: list mode, FakeLLM with the 2nd call raising an `LLMError` (use the failure helper in `llm_fakes`). Then POST retry and assert Winifred speaks. Monkeypatch `character_turns._rng` to raise if called during retry.
- [ ] **Step 2: Run** `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_group_play_turns.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** per the rules above.
- [ ] **Step 4: Run** the new file plus `tests/test_character_turns.py tests/test_response_controls_routes.py tests/test_response_mechanics.py tests/test_scene_freeze.py tests/test_turn_follow_ups.py`. Expected: all PASS (default directed behaviour unchanged).
- [ ] **Step 5: Commit** `feat(group-play): rounds follow the scene's speaker order`

**Amendments (review):**
- `_prepare` resolves the speaker's display name from the round's `eligible` and, failing that, from the current full `roster(cid, sid)` before falling back to "Grimoire" — so an explicitly targeted actor is never stamped "Grimoire". Test `test_reply_as_sitting_out_npc_in_directed_keeps_its_name`.
- A Manual-mode player post with no `speaker_ref` generates nothing, so `post_chat` must not refuse it for a missing connection: when the scene's mode is manual and the turn is a non-director post without `speaker_ref`, a `missing_key`-style failure from `_require_connection` is tolerated and the post is appended with the round completing immediately (no LLM, no tracker start that needs a client beyond what `_chat_run` already does without a connection — if the tracker path requires a connection, skip starting it). Test `test_manual_post_needs_no_connection` (delete the openrouter connection, POST content → 200, post appended). If this cannot be done without touching unrelated paths, report DONE_WITH_CONCERNS describing why.

---

### Task 5: Auto-continue rounds

**Files:**
- Modify: `backend/src/grimoire/store/response_protocol.py` (`validate_handoff`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`start`, `_compose`, `_successor`, `_frames`, `_rescue`)
- Modify: `docs/superpowers/specs/2026-10-05-group-play-speaker-order-design.md` (clarification below)
- Test: `backend/tests/test_group_play_turns.py`

**Interfaces:**
- Consumes: Task 4 round keys and `_successor`.
- Produces:
  - `validate_handoff` returns `(None, "repeated speaker")` when the ref is eligible but in `used`. It keeps `"ineligible or repeated speaker"` only for an ineligible ref.
  - `_successor` gains a `"lead": str | None` key: in directed mode with `auto_remaining > 0` and a `"repeated speaker"` issue, `lead = ref` and `issue = None`.
  - `_follow_on(cid, sid, run, token, round_record, lead: str | None) -> dict | None`: under the campaign lock with `_fence`, returns a new round or None.
  - SSE frame `{"round_start": {"index": n, "of": total}}`.

Rules:
- `start`: when `kind == "post"` and mode ≠ manual, `auto_remaining = auto_total = settings["auto_rounds"]` and `round_index = 1`.
- `_compose`: in directed mode with `auto_remaining > 0`, candidates are eligible + grimoire minus the current actor only (used actors included). This is what makes a repeat handoff reachable. Put that sentence into the spec's Auto-continue section in this task's commit.
- In `_frames`, when a round ends with `next is None`, call `_follow_on` unless any of these holds: `run.cancel_requested`; the last contribution had an issue or was not complete; `round_record["used"]` is empty; mode is manual; `auto_remaining <= 0`; or `handed_back` is true without a `lead`.
- `_follow_on` plans with `plan_post(settings, roster, trigger=<last contribution text>, history, rng, force=(lead,) if lead else ())`, then calls `new_round(automatic=True, post=<original post>, note=prompts.render("scene/director_note.j2"), turn=<original turn>, mode=..., plan=..., actor_ref=lead or planned lead, auto_remaining=prev - 1, round_index=prev + 1, auto_total=prev total)`. Usage keeps the original `post`.
- `_frames` yields `round_start` with `index=round_index`, `of=auto_total + 1`, then runs `_first_actor` (a directed round with no lead goes through the selector; `null` there ends the chain) and continues the same loop. Restructure the existing `while actor` into an outer loop over rounds. Settle once: `outcome.land()` and `done` only after the last round.
- Stop: wherever `run.cancel_requested` blocks a successor, and in `_rescue` when `run.cancel_requested`, write `auto_remaining=0` on the round.
- Roll pause keeps `auto_remaining`. `resume_roll` → `start(round_record=...)` continues the chain with no change.

- [ ] **Step 1: Write failing tests**
  - `test_list_auto_rounds_stop_at_cap`: list, `auto_rounds=1`; FakeLLM 4 replies. Assert `fake.calls == 4`, speakers `[M, W, M, W]` (order_list mara, winifred), and the response body contains `"round_start": {"index": 2, "of": 2}` exactly once.
  - `test_directed_null_handoff_ends_chain`: directed, `auto_rounds=3`, `speaker_ref` mara + text; Mara hands off `null` → `fake.calls == 1`.
  - `test_repeat_handoff_leads_next_round`: directed, `auto_rounds=1`, `speaker_ref` mara + text. Replies: Mara → winifred, Winifred → mara (repeat), Mara → null. Assert `fake.calls == 3` and the last message's `speaker == "Mara"`. Assert request 1's candidate section includes `characters:mara`.
  - `test_repeat_handoff_without_rounds_is_rejected`: `auto_rounds=0`, same first two replies → `fake.calls == 2`. The ledger round's issue is `"repeated speaker"`.
  - `test_stop_zeroes_remaining_rounds_and_retry_starts_none`: list, `auto_rounds=2`. Drive `_frames` and set `run.cancel_requested` on the first `delta` of round 1's 2nd speaker. Then read `store.responses.unfinished(cid, sid)["auto_remaining"] == 0`. POST retry with one more FakeLLM reply → `fake.calls` grows by exactly 1.
  - `test_roll_pause_continues_chain_after_resolution`: list, `auto_rounds=1`. Mara's reply requests a roll (as in `test_roll_decline_continues_same_actor_then_handoff`); decline it. Assert the remaining replies (Mara's continuation, Winifred, then round 2's Mara and Winifred) all generate.
  - `test_manual_never_auto_continues`: manual, `auto_rounds=3`, `speaker_ref` mara + text → 1 call.
  - `test_follow_ups_fire_once_per_chain`: list, `auto_rounds=1`; monkeypatch `streaming._fire_follow_up` to count → 1.
- [ ] **Step 2: Run** `cd backend && PYTHONPATH=src .venv/bin/python -m pytest tests/test_group_play_turns.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** per the rules.
- [ ] **Step 4: Run** the full backend suite: `make check-py`. Expected: PASS. Then `make check-lint check-mypy`, and fix any new findings.
- [ ] **Step 5: Commit** `feat(group-play): opt-in automatic rounds with cap and stop`

**Amendments (review):**
- Follow-on rounds pass `author=<ref of the contribution used as trigger>` to `plan_post`, so a speaker naming themselves does not lead the next round.
- Stop clears the round's remaining `plan` (`plan=[]`), sets `auto_remaining=0` and sets `stopped=True` on the round. `_successor` returns `next=None` for a `stopped` round in every mode, so Retry finishes only the interrupted contribution and generates nothing after it. Test `test_stop_mid_round_retry_generates_only_the_interrupted_reply`: seed THREE NPCs (add Seraphine), list mode, `auto_rounds=0`; stop on the first `delta` of the first speaker; Retry → exactly one more call; transcript has one response from the first speaker only.

---

### Task 6: Reply-as chips

**Files:**
- Modify: `frontend/src/api/client.ts` (`GroupSettings` type, `getSceneGroup`, `setSceneGroup`, next to `getSceneResponse`)
- Modify: `frontend/src/api/stream.ts` (`ChatEvent.round_start?: { index: number; of: number }`)
- Modify: `frontend/src/testkit/campaignMocks.tsx` (two `vi.fn()`s) and `testkit/campaignHarness.tsx` (default `getSceneGroup` resolves `DEFAULT_GROUP`, exported)
- Create: `frontend/src/components/play/ReplyChips.tsx` + `ReplyChips.test.tsx`
- Modify: `frontend/src/routes/CampaignView.tsx` (remove the `responseActor` state and the select+button at ~5202-5213; `respondAs(ref: string)`; `send(speakerRef?: string)` threading `speakerRef` as `api.chat`'s 10th argument in both of its branches; load `group` via `api.getSceneGroup` wherever `getSceneResponse` is loaded for the active scene)
- Test: `frontend/src/routes/CampaignView.test.tsx` (replace the two Respond-as tests at ~8258 and ~8281)

**Interfaces:**
- Produces: `type GroupSettings = { order: "directed" | "manual" | "list" | "natural"; order_list: string[]; talkativeness: Record<string, number>; sitting_out: string[]; auto_rounds: number }`. `api.getSceneGroup(cid, sid): Promise<GroupSettings>` and `api.setSceneGroup(cid, sid, s: GroupSettings): Promise<{ ok: boolean; settings: GroupSettings }>`. `ReplyChips` props: `{ cast: { kind: string; id: string; name: string; role: string }[]; sittingOut: string[]; disabled: boolean; onReply: (ref: string) => void }`. It renders `role="group"` `aria-label="Reply as"` containing a `Grimoire` button and one button per `role === "npc"` member. A sitting-out button gets class `sitting-out` and title `"Sitting out — tap to have them reply anyway"`. Chip click in CampaignView: `input.trim() ? send(ref) : respondAs(ref)`.

- [ ] **Step 1: Write failing tests**
  - `ReplyChips.test.tsx`: renders Grimoire + NPC chips but not the player; clicking `Mara` calls `onReply("characters:mara")`; a sitting-out chip has class `sitting-out` and is still enabled; `disabled` disables all.
  - `CampaignView.test.tsx`: `"a reply chip with an empty composer sends one targeted request"` asserts `api.chat` call `[2] === ""` and `[9] === "characters:mara"`. `"a reply chip with text posts it with that speaker leading"` types "Hello", clicks Mara, and asserts `[2] === "Hello"` and `[9] === "characters:mara"`. `"Grimoire chip works with no NPC in the scene"`.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/components/play/ReplyChips.test.tsx src/routes/CampaignView.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.** Chips sit inside `.form-actions` where the select was, disabled when `busy || rolling || sceneLocked || renamesInFlight > 0`. Style with existing `.chip` classes plus a `.reply-chip.sitting-out { opacity: .55 }` rule in the stylesheet holding `.composer-run-actions`.
- [ ] **Step 4: Run** the same tests, then `make check-web check-eslint`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(play): one-tap reply-as chips`

---

### Task 7: Group panel and round indicator

**Files:**
- Create: `frontend/src/components/play/GroupPanel.tsx` + `GroupPanel.test.tsx`
- Modify: `frontend/src/routes/CampaignView.tsx` (toggle button in `.composer-meta` after the response hint; `roundProgress` state; indicator text)
- Test: `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Consumes: Task 6 `GroupSettings` and API.
- Produces: `GroupPanel` props `{ cid: string; sid: string; cast: CastMember[]; settings: GroupSettings; onChange: (s: GroupSettings) => void; onClose: () => void }`. Each control builds the next settings object, calls `api.setSceneGroup`, and on success calls `onChange(result.settings)`. On failure it shows `role="alert"` text with the error detail and keeps the prior settings. Controls and accessible names:
  - mode `<select aria-label="Speaker order">` with options Directed / Manual / List / Natural;
  - list mode only: one row per NPC in effective list order with buttons `Move {name} up` / `Move {name} down`, which write the full displayed ref order to `order_list`;
  - directed/natural only: `<input type="range" min=0 max=100 step=5 aria-label="{name} talkativeness">`;
  - every mode: `<input type="checkbox" aria-label="{name} sits out">`;
  - `<input type="number" min=0 max=5 aria-label="Automatic rounds">`, disabled in manual;
  - a `Close` button.
- The toggle button reads `Order: {Mode}` and has `aria-expanded`. The panel renders inline below `.composer-meta` (not modal; no key bindings). The toggle stays enabled while a run is live.
- `roundProgress: { index: number; of: number } | null`: set from `e.round_start` in both `runStream` event handlers (~2430, ~2793) and cleared when the stream settles. When set, the responding line reads `Round {index}/{of} · {speaker} is responding…`.

- [ ] **Step 1: Write failing tests**
  - `GroupPanel.test.tsx`: changing `Speaker order` to List calls `setSceneGroup` with `order: "list"`; the talkativeness slider is absent in list/manual and present in directed; checking `Mara sits out` sends `sitting_out: ["characters:mara"]`; `Move Winifred up` sends a reordered `order_list`; a rejected save shows an alert and leaves the controls on the prior value.
  - `CampaignView.test.tsx`: `"group toggle opens the panel with the scene's settings"`; `"round_start frames show Round n/total while responding"` (mock `api.chat` to emit `round_start {index: 2, of: 3}` then `response_start`, and assert the status text contains `Round 2/3`).
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/components/play/GroupPanel.test.tsx src/routes/CampaignView.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** per the Interfaces.
- [ ] **Step 4: Run** the same, then `make check-web check-eslint`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(play): group order panel and round indicator`

**Amendments (review):**
- Reordering in List mode merges the visible permutation into the existing `order_list`: refs not currently displayed (characters who left) keep their positions; the visible refs are written into the slots visible refs occupied, in the new order, and visible refs not yet in the list are appended. Test `"reordering keeps an absent character's stored position"`.
- Saves are serialized and optimistic: the panel keeps a local `latest` settings value updated immediately on each change, sends PUTs one at a time in order (each built from `latest` at send time), and on a failed PUT reverts `latest` to the last server-confirmed settings and shows the alert. Test `"two quick edits both reach the server"` (slider then sit-out before the first PUT resolves; the second PUT body contains both changes).

---

### Task 8: Documentation and full gate

**Files:**
- Modify: `docs/character-responses.md` (a "Speaker order" section: the four modes, talkativeness, sit out, auto rounds and what stops them, where settings live)
- Modify: `frontend/src/routes/ConfigView.tsx:877` copy, if it still names "Respond as": use "Continue, or a reply chip with an empty composer, requests one additional contribution; a reply chip with text posts it with that character leading the round."

- [ ] **Step 1:** Write the docs section from the spec. Use invented names only, and no store-content measurements.
- [ ] **Step 2: Run** `make check`. Expected: every target passes. If a ratchet reports an *improvement*, run `make baseline` and include the baseline files.
- [ ] **Step 3: Commit** `docs(group-play): describe speaker order controls`
