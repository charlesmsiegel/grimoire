# Output Processing (Regex Pipeline) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An ordered, toggleable find-and-replace rule pipeline at four levels (global, world, campaign, connection) that is applied server-side to transcript text for display, for every LLM prompt, and optionally to stored text as it lands. It comes with a test pane and a SillyTavern importer.

**Architecture:** A new leaf package `store/regex/` holds the rule schema, the engine (`run`/`trace`), level files and layering, the message `view`, the store-phase rewrite record, the streaming display helper and the SillyTavern translator. Routes in `routes/regex_rules.py` expose CRUD, test and import. Existing seams call `regex.view.view(...)` (prompt and display phases) or `regex.view.store_phase(...)` (opt-in rewrites). The frontend gets one `RegexRulesEditor`, mounted at all four levels. It also renders `m.shown ?? m.content` and applies `display` stream frames.

**Tech Stack:** Python 3.11 `re`, FastAPI, pydantic (v1/v2-agnostic), React + vitest.

**Spec:** `docs/superpowers/specs/2026-10-05-regex-output-processing-design.md`

## Global Constraints

- Python `re` only, with no new dependency. `pyproject.toml` base deps stay unchanged (Android).
- Pydantic: plain `BaseModel` fields only, dumped via `routes.common._dump`.
- Every store write goes through `store.atomic`, and every path through a resolver (`store.paths.home()`, `campaigns.paths.campaign_root`, `worlds.paths.world_root`, `llm_connections`).
- Imports at module scope. Inside `store/`, bind submodules (`from ..campaigns import read as campaigns_read`), never functions off a sibling package.
- `store/regex/` imports nothing that imports it back. `llm_connections` may import `regex`, but `regex` must not import `llm_connections` at module level for paths. Instead, `llm_connections` exposes `regex_path(id)` and `regex.layers` imports `llm_connections`, so the edge goes one way only: `regex.layers → llm_connections`. `llm_connections.delete_connection` unlinks `regex_path(id)` itself, the way it unlinks `_sidecar_path`.
- Rule ids: `"r-" + secrets.token_hex(4)`.
- Flags: a subset of `g i m s a`. Targets: a non-empty subset of `model`, `user`. Applies: a subset of `display`, `prompt`, which may be empty only when `rewrite_stored` is true.
- Run order: connection → global → world → campaign. `off` lives at world (may name global and connection ids) and at campaign (may name global, world and connection ids). The global and connection files have no `off`.
- Synthetic lines are never touched (`store.scenes.SYNTHETIC_SPEAKERS`, director notes, transitions, roll lines).
- Expansion cap: an apply whose output exceeds `4 * len(input) + 4096` characters is a runtime failure for that rule. It is skipped and logged once per (rule id, level file hash).
- Display throttle: at most one display frame per 100 ms per contribution, plus one at the end.
- Privacy: test fixtures use only placeholder names (Seraphine, Mara, Winifred, Realm, Saltmarch).
- **Deviations from spec, decided here:**
  1. `rewritten` is **not** written into the per-message metadata comment. `GET` scene derives `rewritten: true` from the rewrite record (§5.1's record is the source of truth), which avoids threading a second new metadata key through four writers. `connection` *is* stored in metadata, as the spec says.
  2. A replacement reference to a group the pattern lacks (`$5`, `$<x>`) is **not** a save error. It is left literal, which is JavaScript's behaviour (so `"costs $5"` imports faithfully), and the editor shows a warning.

## Review Focus

1. **A windowed scene read** (`GET ...?limit=40&before=…`): `shown` depth must be computed from `total`, not the window. Otherwise a `max_depth: 3` rule hides different messages depending on scroll position. Test in Task 6.
2. **Multi-part streaming turn:** a display frame for the second speaker must never alter the first speaker's already-streamed text, and the client's part offsets must stay valid. Test in Tasks 7 and 12.
3. **A hand-edited or synced level file** with one malformed rule, or duplicate ids: the other rules still load, and the play view does not 500. Test in Task 2.
4. **Editing a message that was already rewritten:** the record is replaced by the newly submitted text, and Restore writes back exactly what the player typed on that edit, without being re-rewritten. Test in Task 8.
5. **Zero-width and Unicode matches:** `^` with `gm` (prefix every line) and `\w` on `café` with and without `a` behave the same in `run`, `trace` and the stream. Test in Task 1.

---

### Task 1: Rule schema and engine

**Files:**
- Create: `backend/src/grimoire/store/regex/__init__.py` (re-exports submodules: `rules`, `apply`; later tasks add theirs)
- Create: `backend/src/grimoire/store/regex/rules.py`
- Create: `backend/src/grimoire/store/regex/apply.py`
- Test: `backend/tests/test_regex_engine.py`

**Interfaces:**
- Produces (`rules.py`):
  - `class RuleError(ValueError)` with attributes `index: int | None` and `field: str | None`.
  - `TARGETS = ("model", "user")`, `PHASES = ("display", "prompt")`, `FLAGS = "gimsa"`.
  - `mint_id() -> str`.
  - `normalise(raw: dict, *, index: int | None = None) -> dict`: fills defaults, validates types and enums, mints `id` when absent, and compiles. A non-compiling pattern is allowed only with `enabled: False`. Raises `RuleError`.
  - `compile_pattern(rule: dict) -> re.Pattern` maps flags `i→IGNORECASE`, `m→MULTILINE`, `s→DOTALL`, `a→ASCII`. `g` is not a compile flag.
  - `expand(replacement: str, match: re.Match, trim: list[str]) -> str` expands `$1`…`$99`, `$<name>`, `$&`, `{{match}}` and `$$`. An unknown group stays literal. `trim` strings are removed from the matched text used for `$&` / `{{match}}`.
  - `warnings(rule: dict) -> list[str]` covers unknown group refs.
- Produces (`apply.py`):
  - `Entry = dict` with keys `level: str` (`"connection" | "global" | "world" | "campaign"`), `rule: dict`, `off: bool`, `source: str` (a connection id for connection entries, else `""`).
  - `role_of(m: dict) -> str | None` returns `"user"`, `"model"`, or `None` for synthetic or director lines.
  - `skip_reason(entry, *, role, phase, depth) -> str | None` returns one of `"disabled"`, `"switched off"`, `"not for this role"`, `"not for this phase"`, `"outside depth"`. `phase` is `"display" | "prompt" | "store"`. For `"store"` a rule qualifies iff `rewrite_stored` is true, and depth is ignored.
  - `run(text, entries, *, role, phase, depth) -> str`.
  - `trace(text, entries, *, role, phase, depth) -> list[dict]`. Each step has `{rule_id, level, name, applied, reason, matches, text_after}`, and the error reason is `"error: <msg>"`.

- [ ] **Step 1: Write failing tests** in `test_regex_engine.py`:
  - `test_g_replaces_all_else_first`: pattern `a`, replacement `b`, on `"aaa"` gives `"bbb"` with `g` and `"baa"` without.
  - `test_replacement_syntax`: pattern `(?P<w>\w+) (\w+)` with replacement `"$2 $1|$<w>|$&|{{match}}|$$"` on `"hi there"` gives `"there hi|hi|hi there|hi there|$"`.
  - `test_unknown_group_left_literal`: pattern `x`, replacement `"$5.00"` on `"x"` gives `"$5.00"`, and `warnings()` is non-empty.
  - `test_trim_applies_to_match_text`: pattern `<b>.*?</b>`, trim `["<b>", "</b>"]`, replacement `"*{{match}}*"` on `"<b>hi</b>"` gives `"*hi*"`.
  - `test_run_order_and_off`: the entries list is applied in the given order, and an entry with `off=True` is skipped.
  - `test_role_phase_depth_filtering`: a parametrised table over `skip_reason`.
  - `test_store_phase_only_rewrite_stored`: a rule without `rewrite_stored` is skipped with `"not for this phase"` when the phase is `"store"`.
  - `test_role_of_synthetic_is_none`: a director-note message, a transition message and a roll line all give `None`. Build them with `store.scenes.DIRECTOR_SPEAKER` and the other synthetic constants.
  - `test_runtime_error_skipped`: a rule whose expansion raises (monkeypatch `rules.expand` to raise) is skipped, `trace` reports `"error: …"`, and the text passes through.
  - `test_expansion_cap`: pattern `.` with `gs` and a 5000-character replacement on a 100-character input is skipped with an error reason.
  - `test_zero_width_gm`: pattern `^` with flags `gm`, replacement `"> "`, on `"a\nb"` gives `"> a\n> b"`.
  - `test_ascii_flag`: pattern `\w+` on `"café"` matches `"café"` without `a` and `"caf"` with `a`.
  - `test_normalise_rejects`: an empty targets list; `applies=[]` without `rewrite_stored`; an unknown flag `y`; a bad pattern with `enabled: True` (raises, and `field == "pattern"`); the same bad pattern with `enabled: False` is accepted.
- [ ] **Step 2: Run** `cd backend && PYTHONPATH=src python -m pytest tests/test_regex_engine.py -q`. Expected: FAIL (module missing).
- [ ] **Step 3: Implement** `rules.py` and `apply.py`. `run` uses `pattern.sub(lambda m: rules.expand(...), text, count=0 if "g" in flags else 1)`. Compiled patterns are memoised in a `functools.lru_cache(maxsize=512)` keyed by `(pattern, flags)`.
- [ ] **Step 4: Run the tests** (same command). Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): rule schema and engine`.

### Task 2: Level files and layering

**Files:**
- Create: `backend/src/grimoire/store/regex/layers.py`
- Modify: `backend/src/grimoire/store/llm_connections.py`: add `regex_path(id) -> Path` (`_dir() / f"{id}.regex.json"`), and in `delete_connection` call `regex_path(id).unlink(missing_ok=True)` after `_sidecar_path`.
- Modify: `backend/src/grimoire/store/__init__.py`: import `regex` and add it to `__all__`. Then regenerate `backend/tests/store_api_baseline.json` deliberately in this commit (its docstring explains the regeneration).
- Modify: `backend/src/grimoire/store/locks.py`: add `"store.regex.layers"` to `DOMAIN_MODULES`, with a comment (`write_campaign` read-validates-writes the campaign file against the inherited ids).
- Test: `backend/tests/test_regex_layers.py`

**Interfaces:**
- Consumes: Task 1 (`rules.normalise`, `apply.Entry`).
- Produces:
  - `LEVELS = ("global", "world", "campaign", "connection")`.
  - `path(level: str, key: str = "") -> Path`. Global → `paths.home() / "regex.json"`; world → `worlds_paths.world_root(key) / "regex.json"`; campaign → `campaigns_paths.campaign_root(key) / "regex.json"`; connection → `llm_connections.regex_path(key)`.
  - `read_level(level, key="") -> dict` returns `{"rules": [...], "off": [...]}` and never raises. An unparseable file reads empty and logs one `log.error`. A single invalid rule, or a duplicate id within the file, is dropped and logged, and the rest loads.
  - `validate_doc(doc: dict, *, level: str, inherited_ids: set[str]) -> dict` normalises every rule (`RuleError.index` is the rule's position). It rejects duplicate ids, ids that collide with `inherited_ids`, an `off` on `global`/`connection`, and `off` ids not in `inherited_ids`.
  - `write_level(level, key, doc) -> dict` validates, then writes with `atomic.write_text(json.dumps(doc, indent=2))`. For `campaign` it validates and writes inside `locks.campaign_lock(key)`. It returns the normalised doc.
  - `world_of(cid) -> str` returns `campaigns_read.read_campaign(cid)["meta"].get("world", "")`, and `""` on `CampaignNotFound`.
  - `inherited(level, key="") -> list[Entry]`:
    - global → `[]`; connection → `[]`.
    - world → every connection's rules (from `llm_connections.list_connections()`, tagged `source=<id>`), then global.
    - campaign → every connection's rules, then global, then its world's rules.
    - `off` is set from the requesting level's own `off` list.
  - `effective(*, cid: str | None, connection: str = "") -> list[Entry]` gives the given connection's rules, then global, then world (via `world_of(cid)`), then campaign. Entries whose id is in the world's or campaign's `off` list get `off=True`. A missing or deleted connection contributes nothing.

- [ ] **Step 1: Write failing tests:**
  - `test_round_trip_each_level`: write and read for all four levels. Create a world, a campaign and a connection with the existing store helpers, as `test_tracker_fields.py` does.
  - `test_unparseable_reads_empty` and `test_one_bad_rule_dropped_rest_load` (a file holding `[valid, {"pattern": 5}, valid]` reads two rules).
  - `test_duplicate_id_in_file_drops_second`.
  - `test_effective_order`: rules named `C`, `G`, `W` and `K` at the connection, global, world and campaign levels come out in the order `C, G, W, K`.
  - `test_campaign_off_switches_global_and_connection`.
  - `test_world_off_inherited_by_campaign`.
  - `test_validate_rejects_off_on_global`, `test_validate_rejects_unknown_off_id`, `test_validate_rejects_id_collision_with_inherited`.
  - `test_deleted_connection_contributes_nothing`, and `test_delete_connection_unlinks_rules` (through `llm_connections.delete_connection`).
- [ ] **Step 2: Run** `PYTHONPATH=src python -m pytest tests/test_regex_layers.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** `layers.py` and the `llm_connections` / facade / locks edits. Cache parsed and normalised docs keyed by `(path, st_mtime_ns, st_size)` in a dict capped at 256 entries (cleared when full).
- [ ] **Step 4: Run** the new tests plus the guards: `PYTHONPATH=src python -m pytest tests/test_regex_layers.py tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_paths_guard.py tests/test_import_guard.py tests/test_store_api_baseline.py -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): level files, layering and switch-off`.

### Task 3: Rule routes and the test endpoint

**Files:**
- Create: `backend/src/grimoire/routes/regex_rules.py` (own `router = APIRouter()`)
- Modify: `backend/src/grimoire/routes/__init__.py`: import `regex_rules` and include its router like the others.
- Modify: `backend/src/grimoire/routes/models.py`: `class RegexLayer(BaseModel): rules: list[dict] = []; off: list[str] = []` and `class RegexTest(BaseModel): scope: dict; text: str; role: str = "model"; phase: str = "display"; depth: int = 0; draft: dict | None = None; connection: str = ""`.
- Test: `backend/tests/test_regex_routes.py`

**Interfaces:**
- Consumes: Task 2 (`read_level`, `write_level`, `inherited`, `effective`), Task 1 (`trace`, `warnings`).
- Produces the routes:
  - `GET/PUT /api/regex`, `/api/worlds/{wid}/regex`, `/api/campaigns/{cid}/regex`, `/api/llm-connections/{id}/regex`.
  - GET returns `{"layer": {"rules", "off"}, "inherited": [Entry...], "warnings": {rule_id: [str]}}`.
  - PUT body `RegexLayer` returns the same shape. A `RuleError` becomes 400 `{"kind": "invalid_rule", "index": i, "field": f, "detail": msg}`. An unknown world, campaign or connection is 404.
  - `POST /api/regex/test` takes body `RegexTest` and returns `{"steps": [...], "result": str}`.
    - `scope` is one of `{"kind": "global"}`, `{"kind": "world", "wid"}`, `{"kind": "campaign", "cid"}` or `{"kind": "connection", "id"}`.
    - The effective list is the scope level's inherited entries plus its own rules. For a campaign scope it is `effective(cid=cid, connection=body.connection)`.
    - `draft` replaces the entry with the same id, or is appended at the scope level when the id is new or absent.

- [ ] **Step 1: Write failing tests** using the `client` fixture:
  - `test_put_get_each_level`.
  - `test_put_invalid_rule_reports_index`: the second of three rules has a bad pattern with `enabled: true`, giving 400 with `index == 1`.
  - `test_campaign_get_lists_inherited_with_levels`.
  - `test_test_endpoint_trace_order_and_reasons`: a disabled rule appears with `reason == "disabled"`.
  - `test_test_endpoint_draft_overrides_saved`.
  - `test_unknown_world_404`.
- [ ] **Step 2: Run** `PYTHONPATH=src python -m pytest tests/test_regex_routes.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement** the routes. They are `def` handlers; none reserves a run.
- [ ] **Step 4: Run** the new tests and `tests/test_routing_guard.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): rule CRUD and test-pane routes`.

### Task 4: Connection provenance on messages

**Files:**
- Modify: `backend/src/grimoire/store/scenes/serialize.py`: add `"connection"` to `RESPONSE_METADATA`.
- Modify: `backend/src/grimoire/store/responses.py`:
  - `save_variant(..., connection: str = "")` stores `variant["connection"] = connection`.
  - `_message(record, content, status, variant=None)` adds `"connection": variant["connection"]` when it is non-empty, so `activate` (swipe) carries the swiped variant's connection.
- Modify: `backend/src/grimoire/routes/character_turns.py`:
  - add a `connection` parameter to `_save`, passed into `save_variant`;
  - in `_frames`, compute `served = (meter.usage or {}).get(llm.ATTEMPTED) or conn` before `meter.done()` nulls the meter;
  - pass `served.get("id", "")` through `_save` and `_pause`.
- Modify: `backend/src/grimoire/routes/streaming.py`:
  - `_persist_reply(cid, sid, text, connection: str = "")` adds `"connection": connection` to each segment when it is non-empty;
  - `_fence_stream` callers that persist a model reply pass the served connection id the same way (`meter.usage[llm.ATTEMPTED]`, falling back to `conn`);
  - `greetings.py`'s first-post call passes nothing.
- Test: `backend/tests/test_regex_provenance.py`

**Interfaces:**
- Produces: a transcript message from a model reply carries `m["connection"]` (the connection id), and so does the active response variant.

- [ ] **Step 1: Write failing tests:**
  - `test_chat_turn_records_connection` (FakeOpenRouter through `client`): after a send, `read_scene` gives the last message `connection == <active connection id>`, and `responses.json`'s active variant matches.
  - `test_connection_survives_edit_and_cut`: an edit of an earlier message, then a cut of the last, still leaves `connection` on the surviving reply.
  - `test_fallback_records_serving_connection`: configure `fallback_connection_id` and make the first connection fail, using the existing fallback test harness in `test_llm_fallback*.py`; the recorded connection is the fallback's id.
  - `test_swipe_carries_variant_connection`.
- [ ] **Step 2: Run** `PYTHONPATH=src python -m pytest tests/test_regex_provenance.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests, then `PYTHONPATH=src python -m pytest tests -q -k "responses or character_turn or streaming or serialize"`. Expected: PASS. If the frozen campaign sweep (`tests/fixtures/frozen_campaign`) changes, the change is a bug: old files have no `connection` key.
- [ ] **Step 5: Commit** `feat(regex): record which connection produced each reply`.

### Task 5: The view, and the prompt phase everywhere

**Files:**
- Create: `backend/src/grimoire/store/regex/view.py`
- Modify: `backend/src/grimoire/store/context/assemble.py:122`: `history = regex_view.view(scene["messages"], cid=cid, phase="prompt")` in place of `[dict(m) for m in scene["messages"]]`. The `recent_text` and `birthday_text` built from `history` therefore read the prompt view.
- Modify: `backend/src/grimoire/routes/scenes.py`:
  - absorb (around :2549): compute `shown = store.regex.view.view(scene["messages"], cid=cid, phase="prompt")`, pass it to `transcript_text`, store it as a new `_Prepared.shown` field, and pass `prepared.shown` instead of `prepared.scene["messages"]` to every `store.absorb.materialize(...)` call, including the scoped-retry path. The watermark and digest keep using the raw `scene`.
  - audit (:1789), rolling summary (:3353), scene break (:3595), dossiers (:3882) and voice drift (:2137): pass prompt-viewed messages to `transcript_text` or to the judge.
- Modify: `backend/src/grimoire/routes/character_turns.py:131` (response selector's recent posts) and `backend/src/grimoire/routes/tracker.py:399` (tracker update input): apply the prompt view to the message list they read.
- Create: `backend/tests/test_regex_prompt_guard.py`
- Test: `backend/tests/test_regex_prompt.py`

**Interfaces:**
- Consumes: Tasks 1, 2 and 4 (`m["connection"]`).
- Produces (`view.py`):
  - `view(messages: list[dict], *, cid: str | None, phase: str, offset: int = 0, total: int | None = None) -> list[dict]` returns copies with `content` transformed. Depth for `messages[i]` is `(total if total is not None else len(messages)) - 1 - (offset + i)`. Effective lists are built once per distinct `connection` value in the list.
  - `annotate_shown(messages, *, cid, offset=0, total=None) -> list[dict]` copies the messages and adds `shown` only where the display phase changed `content` (used by Task 6).
  - `store_phase(text: str, *, cid: str, role: str, connection: str = "") -> tuple[str, list[str]]` returns the rewritten text and the ids of the rules that changed it (used by Task 8).

- [ ] **Step 1: Write failing tests** (`test_regex_prompt.py`). Each test PUTs rules via the routes, then drives the real path with the `llm_fakes` cassette or scripted fakes, and reads the captured request messages from the fake:
  - `test_connection_rule_strips_think_in_turn_prompt`: a prior reply from connection A contains `<think>x</think>Hello`; A carries a strip rule; the next turn's request history contains `Hello` and not `<think>`.
  - `test_connection_rule_skips_other_connections_posts`: the same reply recorded under connection B keeps its `<think>`.
  - `test_campaign_prompt_only_rule_not_in_display`: a `prompt`-only rule changes the request but not `GET` scene `content`.
  - `test_absorb_prompt_uses_view`: the absorb request's transcript lacks `<think>`.
  - `test_absorb_citation_from_cleaned_text_attributed`: the transcript holds `Seraphine: “Hello” <think>…` and a rule replaces curly quotes with straight ones; the fake absorb reply cites `"Hello"` with its exact cleaned spelling; the staged edit's review band is not `unattributed`.
  - `test_rolling_summary_and_dossier_prompts_use_view`.
  - `test_depth_counts_full_transcript`: `max_depth: 0` touches only the newest message in the prompt.
- [ ] **Step 2: Write the guard** `test_regex_prompt_guard.py`, in the style of `test_routing_guard.py`. It AST-walks `routes/*.py` and `store/context/*.py` and fails any call to `chronicle.transcript_text(` / `store.chronicle.transcript_text(` whose first argument is not a name bound by a call to `view(`. It allows a `# regex-ok: <reason>` marker, which must carry a reason, capped at 3. The export callers in `store/export.py` are excluded; Task 6 routes them through the display view.
- [ ] **Step 3: Run** `PYTHONPATH=src python -m pytest tests/test_regex_prompt.py tests/test_regex_prompt_guard.py -q`. Expected: FAIL.
- [ ] **Step 4: Implement** `view.py` and the call-site changes. Add `view` to `store/regex/__init__.py`.
- [ ] **Step 5: Run** the new tests plus `tests/test_llm_fakes.py`, the absorb suites (`-k absorb`) and `evals` (`PYTHONPATH=src python -m pytest tests -q -k "absorb or eval or prompt"`). Expected: PASS, with no snapshot or cassette changes. No rules means byte-identical prompts.
- [ ] **Step 6: Commit** `feat(regex): apply prompt-phase rules to every LLM transcript reader`.

### Task 6: Display phase: scene read and exports

**Files:**
- Modify: `backend/src/grimoire/routes/scenes.py`, `get_scene` (:494): wrap each return path's messages through `store.regex.view.annotate_shown(...)`. The windowed path passes `offset=window["offset"], total=window["total"]`. Also add `rewritten: true` from Task 8's record (in this task, leave a `rewritten` hook returning an empty set; Task 8 fills it).
- Modify: `backend/src/grimoire/store/export.py`, `_chapter` (:328): immediately after `read_scene`, set `scene["messages"] = regex_view.view(scene["messages"], cid=cid, phase="display")`. `build_json` is untouched.
- Test: `backend/tests/test_regex_display.py`

**Interfaces:**
- Consumes: Task 5 (`annotate_shown`, `view`).
- Produces: scene GET messages may carry `shown: str`.

- [ ] **Step 1: Write failing tests:**
  - `test_shown_only_when_changed`: a display rule touching only messages containing `*` leaves `shown` absent on the others.
  - `test_windowed_read_depth_from_total`: 10 messages and a rule with `max_depth: 2`; `GET ?limit=4&before=10` and `GET ?limit=4&before=4` mark exactly the absolute last three, and none in the earlier window.
  - `test_markdown_export_uses_display`: the `.md.zip` chapter text lacks the stripped span.
  - `test_json_export_raw`.
  - `test_edit_starts_from_content`: `content` remains raw when `shown` exists.
- [ ] **Step 2: Run** `PYTHONPATH=src python -m pytest tests/test_regex_display.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests plus `-k "export or get_scene or window"`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): display-phase text on scene reads and exports`.

### Task 7: Streaming display frames (server)

**Files:**
- Create: `backend/src/grimoire/store/regex/stream.py`
- Modify: `backend/src/grimoire/routes/character_turns.py` `_stream_contribution` (:331) and `backend/src/grimoire/routes/streaming.py` `_fence_stream.event_stream`. Each builds `ds = store.regex.stream.DisplayStream(store.regex.layers.effective(cid=cid, connection=conn.get("id", "")))`. When `ds.active`, every place that would `yield _sse({"delta": visible})` instead calls `ds.feed(visible)` and yields `_sse({"display": frame})` for a returned frame; at the end it yields `ds.finish()` if that is not `None`. When inactive, the code is unchanged.
- Test: `backend/tests/test_regex_stream.py`

**Interfaces:**
- Produces:
  - `class DisplayStream(entries: list[Entry], *, clock=time.monotonic, interval: float = 0.1)`
    - `active: bool` is true iff any non-off, enabled entry targets `model` with phase `display`.
    - `feed(delta: str) -> dict | None` accumulates raw text and, when the interval has elapsed, runs `apply.run(raw, entries, role="model", phase="display", depth=0)`. It diffs the result against the last-sent display text and returns `{"keep": common_prefix_len, "tail": rest}`, or `None` when nothing changed or the interval has not elapsed.
    - `finish() -> dict | None` always computes and returns the final diff, or `None` if identical.
  - The wire frame is `{"display": {"keep": int, "tail": str}}`. `keep` is relative to the **current contribution's** start (the `response_start` boundary, or 0 on the legacy path).

- [ ] **Step 1: Write failing tests:**
  - `test_think_block_retracted`: feeding `"<think>a"`, `"b</think>"` and `"Hi"` with a fake clock advancing 0.2 s per feed produces frames that, applied to a buffer, end at `"Hi"`. An intermediate frame shows `"<think>a"` hidden only once the rule matches. The test asserts the final buffer and that some frame has `keep < len(previous)`.
  - `test_throttle`: feeds 0.01 s apart yield at most one frame per 0.1 s, and `finish` always yields the remainder.
  - `test_inactive_when_no_display_rules`.
  - `test_turn_stream_sends_display_not_delta`, through the `client` with a scripted fake streaming `<think>x</think>Hello`: the SSE body contains `"display"` frames and no `"delta"` frames, and applying them yields `Hello`.
  - `test_turn_stream_unchanged_without_rules`: the SSE body equals the one produced with no rule files (compare frame lists).
  - `test_multi_part_keep_relative_to_part`: a two-speaker scripted round; frames after the second `response_start` have `keep` measured from that part.
- [ ] **Step 2: Run** `PYTHONPATH=src python -m pytest tests/test_regex_stream.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests plus `-k "stream or character_turn or detached"`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): display frames while a reply streams`.

### Task 8: Store phase: opt-in rewrites, their record, and Restore

**Files:**
- Create: `backend/src/grimoire/store/regex/rewrites.py`
- Modify: `backend/src/grimoire/store/locks.py`: add `"store.regex.rewrites"` to `DOMAIN_MODULES`.
- Modify: `backend/src/grimoire/routes/character_turns.py` `_normalise` (:232): after `expand_macros(...).strip()`, `text, fired = store.regex.view.store_phase(text, cid=cid, role="model", connection=<served id>)`. Return `fired` so that `_save` records `rewrites.record(cid, sid, record["id"], original=<pre-phase text>, rules=fired)` inside its existing lock hold, only when `fired` is non-empty.
- Modify: `backend/src/grimoire/routes/streaming.py` `_persist_reply`: run `store_phase` per segment. For each changed segment, mint `post_id = uuid.uuid4().hex`, put it on the segment, and record it inside the existing lock hold.
- Modify: `backend/src/grimoire/routes/scenes.py` `_chat_run` (:754): run `store_phase(content, role="user")` after `expand_macros`, and record by `post_id` in the same hold.
- Modify: `backend/src/grimoire/routes/scenes.py` `put_scene_message` (:4745):
  - Add `restore: bool = False` to `EditMessage` in `routes/models.py`.
  - With `restore` false, run `store_phase` with the message's role (`apply.role_of`; skip synthetic lines) and the message's `connection`, and record or replace by its `response_id` or `post_id`. A message with neither id has no record key, so the store phase is skipped for it, with a comment explaining why.
  - With `restore` true, skip `store_phase` and call `rewrites.forget(...)`.
  - Both inside the existing `scene_held_free` hold.
- Modify: `backend/src/grimoire/store/scenes/lifecycle.py` `delete_scene`: after the tracker drop, fail-soft `regex_rewrites.drop(cid, ident)` with a logged warning.
- Modify: `routes/scenes.py`: `GET /campaigns/{cid}/scenes/{sid}/rewrites` returns `{key: {original, rules, at}}`, and `get_scene` sets `rewritten: True` on messages whose `response_id` or `post_id` is in the record (filling Task 6's hook).
- Test: `backend/tests/test_regex_rewrites.py`

**Interfaces:**
- Produces (`rewrites.py`):
  - `path(cid: str, identity: str) -> Path` is `campaign_root(cid) / "rewrites" / f"{identity}.json"`.
  - `record(cid: str, sid: str, key: str, *, original: str, rules: list[str]) -> None` runs under `campaign_lock(cid)` and uses `scenes.identity.ensure_identity(cid, sid)`.
  - `read_all(cid: str, sid: str) -> dict[str, dict]` never raises.
  - `forget(cid: str, sid: str, key: str) -> None` runs under the lock.
  - `drop(cid: str, identity: str) -> None` runs under the lock.

- [ ] **Step 1: Write failing tests:**
  - `test_model_reply_rewritten_and_recorded`: a rule with `rewrite_stored: true` and `applies: []` replaces `"..."` with `"…"`. After a turn, the stored content has `…`, the record holds the original with `...`, and GET marks the message `rewritten`.
  - `test_user_post_rewritten_by_post_id`.
  - `test_no_rewrite_rules_no_record_file`.
  - `test_edit_rewrites_and_replaces_record`: edit a rewritten message to `"a...b"`; the stored text is `"a…b"` and the record's original is `"a...b"`.
  - `test_restore_writes_original_without_rewriting`: PUT with `restore: true` and the recorded original; the stored text equals the original, the record is gone, and the message is not flagged.
  - `test_restore_refused_while_busy`: with a held run (the `HeldOpenRouter` pattern from `test_scene_freeze.py`), the PUT returns 409 `scene_busy`.
  - `test_record_survives_rename`: rename the scene and read the record via the new sid.
  - `test_delete_scene_drops_record`.
  - Add the restore door to `tests/test_scene_freeze.py` only if that suite enumerates edit-route variants; otherwise `test_restore_refused_while_busy` covers it.
- [ ] **Step 2: Run** `PYTHONPATH=src python -m pytest tests/test_regex_rewrites.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new tests plus `tests/test_lock_domain_guard.py tests/test_atomic_guard.py tests/test_scene_freeze.py`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): opt-in stored rewrites with a recorded original and restore`.

### Task 9: SillyTavern translator and import routes

**Files:**
- Create: `backend/src/grimoire/store/regex/translate.py`
- Create: `backend/src/grimoire/store/regex/st_import.py`
- Modify: `backend/src/grimoire/routes/regex_rules.py`: add `POST /api/regex/import/preview` (body `{"data": <parsed JSON>}`) returning `{"rows": [{"index", "name", "verdict", "notes", "rule" | None, "original"}]}`, and `POST /api/regex/import` (body `{"scope": {...}, "rows": [rule, ...]}`). The import appends the rules to that level via `write_level`; a rule whose id collides is re-minted.
- Test: `backend/tests/test_regex_translate.py`, `backend/tests/test_regex_import.py`

**Interfaces:**
- Produces:
  - `translate.translate(body: str, flags: str) -> Result`, where `Result = {"verdict": "exact" | "approximate" | "untranslatable", "pattern": str | None, "flags": str, "notes": list[str]}`. The lexer walks escapes, classes, groups and quantifiers. Each construct in spec §7 maps as listed there, and an unrecognised token gives `untranslatable`. After translation, `re.compile` is attempted, and an error gives `untranslatable` with the error text as a note.
  - `st_import.scripts(data) -> list[dict]` accepts a single script, a list, or `{"regex_scripts": [...]}` / `{"extensions": {"regex_scripts": [...]}}`.
  - `st_import.preview(data) -> list[dict]` maps the fields per spec §7's table.

- [ ] **Step 1: Write failing tests:**
  - `test_translate_table`: a parametrised list of `(js_body, js_flags, expected_python, expected_flags, verdict)` covering:
    - `(?<n>a)\k<n>` → `(?P<n>a)(?P=n)`, exact;
    - `a\/b` → `a/b`;
    - `[^]` → `[\s\S]`;
    - `\u{1F600}` with `u` → `\U0001F600`, with a note for `u`;
    - `.` without `s` → `[^\n\r  ]`;
    - `a$` without `m` → `a\Z`;
    - `\s` → the explicit JavaScript whitespace class;
    - `\w+` → `\w+` with `a` in flags;
    - `^a` with `m` → approximate;
    - `é` with `i` → approximate;
    - `\p{L}` → untranslatable;
    - `[]` → untranslatable;
    - `(?<=a+)b` → untranslatable (Python compile error);
    - flag `y` → untranslatable.
  - `test_translate_exact_differential`: for each exact case, a list of sample strings whose matches are hand-checked to agree with JavaScript (literal expected match lists in the test).
  - `test_import_mapping` (`test_regex_import.py`):
    - placement `[2]` → targets `["model"]`;
    - `[1, 2]` → both;
    - `[5]` → untranslatable;
    - `markdownOnly` → `["display"]`;
    - neither flag → `["display", "prompt"]` with `rewrite_stored: false` and a note mentioning that SillyTavern rewrote stored text;
    - `substituteRegex: 1` → untranslatable;
    - `disabled: true` → `enabled: false`;
    - `minDepth: -1` → `None`;
    - `trimStrings` verbatim;
    - `runOnEdit` noted;
    - the replacement `` $` `` → untranslatable.
  - `test_import_preview_accepts_settings_export` (nested `extensions.regex_scripts`).
  - `test_import_commit_appends_and_remints_colliding_id`.
  - `test_untranslatable_rows_have_no_rule` (`rule is None`, and the original is kept).
- [ ] **Step 2: Run** `PYTHONPATH=src python -m pytest tests/test_regex_translate.py tests/test_regex_import.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** Rules from `approximate` rows carry `imported = {"from": "sillytavern", "pattern": "/body/flags", "notes": [...]}`, and so do `exact` rows that have notes.
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): SillyTavern regex import with a translating lexer`.

### Task 10: Frontend API and RegexRulesEditor at four levels

**Files:**
- Modify: `frontend/src/api/types.ts`: add these types, and add `shown?: string; rewritten?: boolean; connection?: string` to `Message` (:345):
  - `RegexRule` (mirrors spec §2);
  - `RegexEntry = { level: "connection" | "global" | "world" | "campaign"; rule: RegexRule; off: boolean; source: string }`;
  - `RegexLayer = { rules: RegexRule[]; off: string[] }`;
  - `RegexBundle = { layer: RegexLayer; inherited: RegexEntry[]; warnings: Record<string, string[]> }`;
  - `RegexScope = { kind: "global" } | { kind: "world"; wid: string } | { kind: "campaign"; cid: string } | { kind: "connection"; id: string }`;
  - `RegexStep`;
  - `RegexImportRow`.
- Modify: `frontend/src/api/client.ts`: add a `regexPath(scope)` helper beside `trackerFieldsPath`, and add `getRegex(scope)`, `putRegex(scope, layer)`, `testRegex(body)`, `previewRegexImport(data)` and `importRegex(scope, rows)`. Reads after writes pass `{ fresh: true }`.
- Create: `frontend/src/components/RegexRulesEditor.tsx`
- Modify the mounts:
  - `frontend/src/routes/ConfigView.tsx`: add `"output"` to `SectionId` and a `SECTIONS` entry `{ id: "output", group: "What you see", label: "Output processing", fields: [] }` rendering `<RegexRulesEditor scope={{ kind: "global" }} />`;
  - `frontend/src/routes/WorldView.tsx`: add an `INDEX` row `{ key: "output", label: "Output processing" }` under "Writing", hidden on the campaign copy like `tracker`, rendering `<RegexRulesEditor key={wid} scope={{ kind: "world", wid }} />`;
  - `frontend/src/routes/CampaignHub.tsx`: add a panel `"output"` beside `"tracker"`;
  - `frontend/src/components/ConnectionEditor.tsx`: render the editor under the form for a saved connection (`scope={{ kind: "connection", id }}`). New, unsaved connections show a hint instead.
- Test: `frontend/src/components/RegexRulesEditor.test.tsx`

**Interfaces:**
- Consumes: the Task 3 and Task 9 routes.
- Produces: `RegexRulesEditor({ scope }: { scope: RegexScope })`, following the CLAUDE.md list/detail pattern:
  - **Rail:** an "Inherited" group, with a level tag on each row and a checkbox titled `Use here` that toggles this level's `off` and saves (hidden at global and connection levels). Then a "This level" group, where each row has an enabled checkbox and `↑` / `↓` buttons, followed by `+ New rule` and `Import…` buttons.
  - **Detail:** an `<h3>` name; `<pre>` pattern and replacement; a sidebar with an **Edit** button and `.side-section`s for Flags, Targets, Applies, Depth and Stored text, plus Imported notes when present; warnings shown as `.field-hint`.
  - **Form:** preset buttons `Model output`, `User input`, `Display only` and `Prompt only` that set the targets and applies checkboxes per spec §2's table, followed by name, pattern, replacement, flags checkboxes, trim (one per line), min and max depth, and `Also rewrite stored text`. Save PUTs the whole layer, and a 400 shows its `detail` on the field named by `field`. Cancel returns to view.

- [ ] **Step 1: Write failing tests:**
  - `clicking a rule shows the read-only view with sidebar and no textarea`;
  - `Edit reveals the form`;
  - `+ New rule opens the form directly`;
  - `preset Display only sets both targets and only display`;
  - `toggling an inherited rule's Use here PUTs off`;
  - `↑ reorders and saves`;
  - `a 400 invalid_rule shows the message on the pattern field`.

  Mock `api` as `GreetingEditor.test.tsx` does.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/components/RegexRulesEditor.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement** the types, client, component and mounts.
- [ ] **Step 4: Run** the same command, then `cd frontend && npx tsc --noEmit -p .` and `npx vitest run src/routes/ConfigView.test.tsx src/routes/WorldView.test.tsx src/routes/CampaignHub.test.tsx src/components/ConnectionEditor.test.tsx` (for whichever of these exist). Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): rule editor at global, world, campaign and connection levels`.

### Task 11: Frontend test pane and import dialog

**Files:**
- Create: `frontend/src/components/RegexTestPane.tsx`
- Create: `frontend/src/components/RegexImportDialog.tsx`
- Modify: `frontend/src/components/RegexRulesEditor.tsx`: mount the pane under the rail (collapsible, with the summary `Test rules`), passing the open form's draft rule. `Import…` opens the dialog.
- Test: `frontend/src/components/RegexTestPane.test.tsx`, `frontend/src/components/RegexImportDialog.test.tsx`

**Interfaces:**
- Consumes: `api.testRegex` and `api.previewRegexImport` / `api.importRegex` (Task 10).
- Produces:
  - `RegexTestPane({ scope, draft, initial }: { scope: RegexScope; draft?: RegexRule; initial?: { text: string; role: "model" | "user"; depth: number; connection?: string } })`. It has a textarea, role / phase (display, prompt, store) / depth controls, and a `Run` button; it shows one row per step, with applied steps showing `matches` and the text after them (changes highlighted with `<mark>` from a simple common-prefix/suffix diff), and skipped steps greyed with their `reason`.
  - `RegexImportDialog({ scope, onDone })`. It has a file input that reads the file as text and calls `JSON.parse` client-side (a parse failure shows an error), then shows a preview table with a checkbox per importable row (`untranslatable` rows are unchecked and disabled, with their notes visible), and an `Import selected` button. It is modal, so it registers Escape through `useHotkeys(..., { modal: true })` like other dialogs. It adds no `window` listeners.

- [ ] **Step 1: Write failing tests:**
  - `runs the trace and shows each step with its reason`;
  - `passes the draft rule`;
  - `import preview lists untranslatable rows disabled with notes`;
  - `Import selected sends only checked rows`;
  - `invalid JSON shows an error and calls nothing`.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/components/RegexTestPane.test.tsx src/components/RegexImportDialog.test.tsx`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command, then `npm run lint --prefix .` (eslint, which includes the keydown rule). Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): test pane and SillyTavern import dialog`.

### Task 12: Frontend play view: shown text, stream frames, turn-history rewrites

**Files:**
- Create: `frontend/src/components/play/displayFrames.ts`
- Modify: `frontend/src/api/stream.ts`: add `display?: { keep: number; tail: string }` to the stream event type (:81 area).
- Modify: `frontend/src/routes/CampaignView.tsx` (both consumers, at :2442 and :2804): handle `e.display` with `acc = applyDisplay(acc, partStart, e.display); setStreaming(acc);`. `partStart` is the `offset` of the last boundary recorded on `response_start`, or 0, kept in a local `let partStart = 0` updated alongside the boundary.
- Modify: `frontend/src/components/play/TranscriptPost.tsx:399`: render `<RenderedMarkdown content={m.shown ?? m.content} />`.
- Modify: `frontend/src/components/SceneInspector.tsx` Turn history (:1647): for a turn whose message has `rewritten`, show a `Rewritten` chip. Opening it fetches `GET .../rewrites` (add `api.getSceneRewrites(cid, sid)`), shows the original against the current content, lists the rule names, and offers **Restore original**. That button calls `api.editMessage(cid, sid, index, original, { restore: true })`; extend the existing edit call with an options argument that sends `restore`.
- Modify: `frontend/src/components/RegexRulesEditor.tsx` / `RegexTestPane.tsx`: no change, other than accepting `initial` from the play view. Add a `Test rules on this post` item to the post's existing actions menu, which opens the pane in a modal with `initial` filled from the message.
- Test: `frontend/src/components/play/displayFrames.test.ts`, plus cases added to `frontend/src/routes/CampaignView.test.tsx` and `frontend/src/components/SceneInspector.test.tsx`.

**Interfaces:**
- Produces: `applyDisplay(acc: string, partStart: number, frame: { keep: number; tail: string }): string` returns `acc.slice(0, partStart + frame.keep) + frame.tail`.

- [ ] **Step 1: Write failing tests:**
  - `applyDisplay retracts within the current part only`: `acc = "A said.B<thi"` with `partStart = 7` and frame `{ keep: 1, tail: "" }` gives `"A said.B"`.
  - CampaignView: `a display frame replaces streamed text` (a fake stream emits `response_start` and then display frames; the DOM shows the final text and never the `<think>` text after the retracting frame).
  - CampaignView: `renders shown over content`.
  - SceneInspector: `a rewritten turn shows the original and Restore calls editMessage with restore`.

  Use the `testkit/campaignHarness` helpers.
- [ ] **Step 2: Run** `cd frontend && npx vitest run src/components/play/displayFrames.test.ts src/routes/CampaignView.test.tsx src/components/SceneInspector.test.tsx`. Expected: FAIL on the new cases.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the same command. Expected: PASS.
- [ ] **Step 5: Commit** `feat(regex): render display text, apply stream frames, restore rewrites`.

### Task 13: Docs, baselines and the full gate

**Files:**
- Modify: `CLAUDE.md`: add a short section, **"Output processing: stored text is raw"**, covering:
  - the four levels and their run order;
  - `off` versus editing an inherited rule;
  - that every prompt reader goes through `store.regex.view.view` (and the guard that enforces it);
  - that display runs server-side so one dialect governs screen and prompt;
  - that stored rewrites are opt-in, happen only as text lands, and are recorded per scene identity.
  
  Keep it to about 12 lines.
- Modify: `CONTRIBUTING.md` guard/marker table: add the `# regex-ok: <reason>` marker and `test_regex_prompt_guard.py`.
- Modify: `lint-baselines/*.json`, only if `make check-lint`, `check-mypy` or `check-eslint` report a *changed* count. Fix the new findings rather than baselining them, and commit `make baseline` only for counts this change *reduced*.

- [ ] **Step 1:** Run `PYTHONPATH=backend/src python -m pytest backend/tests/test_docs_guard.py -q` after the doc edits. Expected: PASS. If it holds CLAUDE.md claims to code, make the new paragraph's file paths real.
- [ ] **Step 2:** Run `make check`. Expected: every target passes. On a failure, fix the cause and re-run; never baseline a new finding.
- [ ] **Step 3:** Confirm the frozen campaign snapshot is unchanged: `git status backend/tests/fixtures/frozen_campaign` shows nothing.
- [ ] **Step 4: Commit** `docs: output processing conventions and guard marker`.
