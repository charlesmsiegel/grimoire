# Play controls IV — keep writing (continue the last reply): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A **Keep writing ▸** control that extends the trailing response, saving `old + joiner + continuation` as a new, active variant, by prefill on connections the user marked prefill-capable and by an instruction everywhere else.

**Architecture:** A connection gains `prefill: bool`. The extend prompt is the response's frozen snapshot plus two alternative *tails* (prefill / instruction) carried on the `PreparedMessages`; `llm._dispatch` asks it for the attempt's messages given the attempt's connection (`for_connection`), so a fallback picks its own tail. The route `POST .../responses/{rid}/extend` is a detached `turn` run of kind `extend` that reuses the reroll stream (`_reroll_frames`, generalized) and lands through the reroll's persist tail. The client streams into a bubble seeded with the old text.

**Tech Stack:** FastAPI + pytest (`backend/`), React + vitest (`frontend/`), Jinja templates (`templates/`).

**Spec:** `docs/superpowers/specs/2026-10-05-play-controls-continue-design.md` — its **Gate resolutions** section is binding and overrides the earlier text.

**Depends on (implemented before this plan; verify the final names in code before using them):**
- Step 2, hide-from-context (`docs/superpowers/specs/2026-10-05-play-controls-hide-from-context-design.md`): the frozen-prompt check that refuses a reroll with 409 `context_excluded`. Find it with `grep -n context_excluded backend/src/grimoire/routes/character_turns.py` and call the **same** helper `regenerate_response` calls. This plan writes it as `store.responses.excluded_since(cid, sid, rid)`; substitute the real name.
- Step 3, branching (`...-branching-design.md`, gate 2): `runs.require_scene_open` (409 `branch_closed`) is called by `runs.reserve_turn`, so `extend` inherits it by reserving a turn. Verify with `grep -n require_scene_open backend/src/grimoire/routes/runs.py`.

## Global Constraints

- Names: **Keep writing** in the UI; **`extend`** in code — route, routing task, run kind, meter task, prompt-log task, `made_by.task`.
- **Instruction mode is the default; prefill is an explicit opt-in per connection.** A connection record gains `prefill: bool` (default false, editable in the connection form, any kind); `llm.prefill_capable(conn)` reads it. There is no `PREFILL_KINDS`.
- **The mode is chosen per attempt**: `_dispatch` calls `for_connection(conn)` on a `PreparedMessages` (falling back to `for_model` for ordinary prompts). No route filtering. `made_by.mode` records the mode the **served** attempt used.
- **The two tails** both start with the partial reply as an assistant message, projected the way history is (`export.drop_images`; no speaker label). Prefill: that message is last. Instruction: followed by a **user** message rendered from `scene/extend_instruction.j2`. A steer goes into the instruction text in instruction mode, and as the step-1 steer message (`scene/response_steer.j2`, system role) **before** the partial reply in prefill mode.
- **Continue what is shown**: `old` is the response's transcript prose (the join `responses.get` uses), not the active variant's stored text.
- **Multi-part responses** continue from the latest resume snapshot (`composed: "resume"`, its settings).
- **Joining.** Prefill: blank line → `\n\n`, newline → `\n`, spaces → ` `, none → `""`. Instruction: `\n\n`, unless the continuation's first character is lowercase or closing punctuation (`. , ; : ! ? ) ] ” ’ ' * … —`), then ` `. The perception fence is stripped before joining; in prefill mode the watcher runs with perception off.
- **Refusal kinds, exactly**: `not_last_response`, `applied_mechanics`, `historical_context_unavailable`, `context_excluded`, `round_open`, `proposal_pending`, `review_pending`, `run_in_flight` (from `reserve_turn`), plus `variant_incomplete` (existing kind, active variant not complete) and `branch_closed` (inherited). A roll fence in the continuation: `extend_roll_refused`, detail `"A continuation cannot propose a roll — reroll the reply instead"`. An empty continuation: `replacement_incomplete`. In every refusal the active variant stays.
- **Length**: the instruction asks for the rest of the beat, at most the record settings' `words` (omitted when the settings are absent).
- **Metering**: task `extend`, carrying `post` and `response_id`; **not** in `store.usage.REROLL_TASKS` or the reroll rate.
- `made_by`: `task: "extend"`, `composed`, the round's typed note, the clipped guidance, `extends` (id of the variant that was extended), `mode` (`"prefill"` | `"instruction"`).
- **Live bubble**: the old text is a render-only `seed` (never in the stream accumulator), the target message is hidden while streaming, and the bubble renders `seed + " " + stream`. The `response_start` frame carries `extend: {seed}`.
- **No bare key** for Keep writing (it spends money). Prompt-log label **"Extend"**.
- Project rules: every store write through `store.atomic`; `store/` imports bind submodules and stay at module scope (`test_import_guard.py`); `llm.py` and `model_guidance.py` stay store-free; pydantic plain `BaseModel` fields only, dumped via `routes.common._dump`; keys only through `useHotkeys`; placeholder names only (Mara, Winifred, Seraphine, Realm, Saltmarch); a route that reserves is `def`, not `async def`.
- Ratchet gates: if a change resolves or adds a ruff/mypy/eslint finding, run `make baseline` and commit the new `lint-baselines/*.json` with that change.
- Backend tests: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q <files>`. Frontend tests: `cd frontend && npx vitest run <files>`, then `npm run typecheck`.

## Review Focus

1. A prefill prompt reaching a fallback that is not prefill-capable would glue a second reply onto the first — the fallback must get the instruction tail, and `made_by.mode` must name the served attempt's mode (Task 2 dispatch test; Task 4 `_served_mode` test).
2. Whitespace lost or doubled at the join (a prefill finishing a word, an instruction continuation starting lowercase) — Task 3 parametrized joiner test and the Task 4 route tests.
3. A hand-trimmed reply must be continued from the trim, not from the stored variant (Task 4 `test_extend_continues_the_trimmed_transcript_prose`).
4. A continuation that proposes a roll or comes back empty must leave the active variant and the variant count untouched (Task 4 tests, both modes).
5. Extending under a stored review would silently invalidate the longest generation in the app — refused `review_pending`; and trailing transition lines after the response must **not** count as "not last" (Task 5 refusal table).

---

### Task 1: The connection's `prefill` flag

**Files:**
- Modify: `backend/src/grimoire/store/llm_connections.py` (`_FIELDS`, `_write_raw`, `_read`)
- Modify: `backend/src/grimoire/routes/models.py` (`ConnectionCreate`, `ConnectionUpdate`)
- Modify: `backend/src/grimoire/llm.py` (new `prefill_capable`)
- Test: `backend/tests/test_llm_connections_store.py`, `backend/tests/test_llm.py`

**Interfaces:**
- Produces: stored frontmatter key `prefill` (`"true"` or `""`); `_read` returns `prefill: bool` (`meta.get("prefill") == "true"`), so an old file reads `False`. `_write_raw` writes `"true"` when `fields.get("prefill") in (True, "true")`, else `""`.
- Produces: `ConnectionCreate.prefill: bool = False`; `ConnectionUpdate.prefill: bool | None = None` (None = keep, as the other fields).
- Produces: `llm.prefill_capable(conn: dict) -> bool` — `conn.get("prefill") is True`. Store-free, no kind check.

- [ ] **Step 1: Write the failing tests**

```python
# test_llm_connections_store.py
def test_prefill_defaults_off_and_round_trips(monkeypatch, tmp_path):
    monkeypatch.setenv("GRIMOIRE_HOME", str(tmp_path))
    cid = llm_connections.create_connection("openrouter", "Realm OR", api_key="k", model="m")
    assert llm_connections.read_connection_raw(cid)["prefill"] is False
    llm_connections.update_connection(cid, prefill=True)
    assert llm_connections.read_connection_raw(cid)["prefill"] is True
    llm_connections.update_connection(cid, name="Renamed")          # None-filtered: kept
    assert llm_connections.read_connection_raw(cid)["prefill"] is True

# test_llm.py
@pytest.mark.parametrize("conn,expected", [
    ({"kind": "openrouter"}, False), ({"kind": "openrouter", "prefill": True}, True),
    ({"kind": "claude", "prefill": True}, True), ({"kind": "openrouter", "prefill": "true"}, False)])
def test_prefill_capable_reads_only_the_flag(conn, expected):
    assert llm.prefill_capable(conn) is expected
```

Also a route round trip in `test_llm_connections_store.py` or `test_routes.py` style: `client.put("/api/llm-connections/openrouter", json={"prefill": True})` then `client.get(...).json()["prefill"] is True`.

- [ ] **Step 2: Run, verify FAIL** — `KeyError: 'prefill'` / `AttributeError: prefill_capable`.
- [ ] **Step 3: Implement** as in Interfaces. Check `ensure_migrated`'s `_write_raw` calls (~line 270) still work with the default.
- [ ] **Step 4: Run** `test_llm_connections_store.py test_llm.py test_routes.py -k connection` — PASS.
- [ ] **Step 5: Commit** `feat(connections): a per-connection prefill opt-in`.

### Task 2: Per-attempt tails on `PreparedMessages`

**Files:**
- Modify: `backend/src/grimoire/model_guidance.py` (`PreparedMessages.__init__`, new `with_tails`, `for_connection`, `mode_for`)
- Modify: `backend/src/grimoire/llm.py` (`LLMClient._dispatch`)
- Test: `backend/tests/test_model_guidance.py`, `backend/tests/test_model_guidance_dispatch.py`

**Interfaces:**
- Produces: `PreparedMessages.with_tails(self, tails: dict[str, list[dict]], choose: Callable[[dict], str], primary: dict) -> PreparedMessages` — a copy restored from `self.snapshot()` (as `with_appended` does) carrying `_tails` and `_choose`; its list body is the primary model's messages plus `tails[choose(primary)]` (so the prompt log and a fake LLM see what the primary attempt sends); `breakdown` is `None`.
- Produces: `PreparedMessages.for_connection(self, conn: dict, model: str) -> list[dict]` — `self.for_model(model)`, plus `deepcopy(self._tails[self._choose(conn)])` when tails are set.
- Produces: `PreparedMessages.mode_for(self, conn: dict) -> str | None` — `self._choose(conn)` when tails are set, else `None`.
- Declare `self._tails: dict[str, list[dict]] | None = None` and `self._choose: Callable[[dict], str] | None = None` in `__init__` (an undeclared attribute is a new mypy finding the ratchet fails).
- Changes: `_dispatch` replaces `messages.for_model(effective_model(conn))` with `messages.for_connection(conn, effective_model(conn))`. Nothing else in `llm.py` changes — no route filtering.

- [ ] **Step 1: Write the failing tests**

```python
# test_model_guidance.py
def test_for_connection_appends_the_tail_the_connection_chooses():
    base = PreparedMessages("m", lambda model: ([{"role": "system", "content": "S"}], None))
    tails = {"prefill": [{"role": "assistant", "content": "Mara"}],
             "instruction": [{"role": "assistant", "content": "Mara"},
                             {"role": "user", "content": "Continue."}]}
    choose = lambda c: "prefill" if c.get("prefill") is True else "instruction"
    tailed = base.with_tails(tails, choose, {"prefill": True})
    assert list(tailed)[-1] == {"role": "assistant", "content": "Mara"}
    assert tailed.for_connection({"prefill": False}, "m")[-1]["role"] == "user"
    assert tailed.mode_for({}) == "instruction" and base.mode_for({}) is None
    assert base.for_connection({"prefill": True}, "m") == base.for_model("m")

# test_model_guidance_dispatch.py (reuses `_prepared` and `ScriptedProvider`)
async def test_a_prefill_prompt_falls_back_with_the_instruction_tail():
    primary = ScriptedProvider(chunks=(), error=LLMError("auth", "refused"))
    fallback = ScriptedProvider(chunks=(" and left.",))
    facade = llm.LLMClient(openrouter=primary, openai_compatible=fallback, retries=0,
                           fallback={"kind": "openai_compatible", "model": "vendor/unknown"})
    conn = {"model": "vendor/unknown", "prefill": True}
    messages = _prepared("vendor/unknown").with_tails(
        {"prefill": [{"role": "assistant", "content": "Mara paused"}],
         "instruction": [{"role": "assistant", "content": "Mara paused"},
                         {"role": "user", "content": "Continue exactly where your last message stops."}]},
        lambda c: "prefill" if llm.prefill_capable(c) else "instruction", conn)
    assert await facade.complete(messages, conn) == " and left."
    assert primary.requests[0]["messages"][-1]["role"] == "assistant"
    assert fallback.requests[0]["messages"][-1]["role"] == "user"
```

- [ ] **Step 2: Run, verify FAIL** — `AttributeError: with_tails`.
- [ ] **Step 3: Implement** in `model_guidance.py` (no store or `llm` import — the chooser is passed in) and the one-line `_dispatch` change.
- [ ] **Step 4: Run** `test_model_guidance.py test_model_guidance_dispatch.py test_llm.py` — PASS.
- [ ] **Step 5: Commit** `feat(llm): choose a prompt tail per dispatched attempt`.

### Task 3: The extend prompt, the joiner and the trailing check (pure helpers)

**Files:**
- Create: `templates/scene/extend_instruction.j2`
- Modify: `templates/README.md` (one entry under `scene/`: vars `words`, `guidance`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (new `_extend_messages`, `_extend_joiner`)
- Modify: `backend/src/grimoire/store/response_protocol.py` (new `strip_preparation`)
- Modify: `backend/src/grimoire/store/responses.py` (new `is_trailing`)
- Test: `backend/tests/test_response_extend.py` (new)

**Interfaces:**
- Produces: `extend_instruction.j2`, rendered with `words: int | None` and `guidance: str` (always both — templates render under `StrictUndefined`). Text: continue exactly where your last message stops; do not repeat, restate or summarise it; same voice, same speaker; the rest of the beat, `{% if words %}at most {{ words }} words{% endif %}`; and, when `guidance`, a `Direction for this continuation: {{ guidance }}` line.
- Produces: `character_turns._extend_messages(snapshot: dict, conn: dict, old: str, guidance: str, words: int | None) -> PreparedMessages` — `PreparedMessages.from_snapshot(snapshot, effective_model(conn)).with_tails(tails, _mode_of, conn)` where `partial = {"role": "assistant", "content": store.export.drop_images(old)}`, `tails["prefill"] = [steer?, partial]` (steer = `{"role": "system", "content": prompts.render("scene/response_steer.j2", guidance=guidance)}` only when `guidance`), `tails["instruction"] = [partial, {"role": "user", "content": prompts.render("scene/extend_instruction.j2", words=words, guidance=guidance)}]`, and `_mode_of = lambda c: "prefill" if llm.prefill_capable(c) else "instruction"` (import `prefill_capable` beside `ATTEMPTED`).
- Produces: `character_turns._EXTEND_CLOSERS = frozenset(".,;:!?)]”’'*…—")` and `_extend_joiner(lead: str, text: str, mode: str) -> str`. Prefill: `lead.count("\n") >= 2` → `"\n\n"`, `== 1` → `"\n"`, other non-empty `lead` → `" "`, empty → `""`. Instruction: `" "` if `text[:1].islower() or text[:1] in _EXTEND_CLOSERS`, else `"\n\n"`.
- Produces: `response_protocol.strip_preparation(text: str) -> str` — runs `_PreparationPrefix(True)` over `text` (`feed` then `finish`) and returns the prose; a no-op for text without a leading perception fence. Leading whitespace of the prose is preserved.
- Produces: `responses.is_trailing(messages: list[dict], rid: str) -> bool` — among messages whose speaker is not in `serialize.SYNTHETIC_SPEAKERS`, the ones carrying `response_id == rid` are a non-empty suffix. Pure, no reads.

- [ ] **Step 1: Write the failing tests** in `test_response_extend.py`:

```python
@pytest.mark.parametrize("lead,text,mode,joiner", [
    ("\n\n", "Then rain.", "prefill", "\n\n"), ("\n", "Then rain.", "prefill", "\n"),
    (" ", "then rain.", "prefill", " "), ("", "ing.", "prefill", ""),
    ("", "Then rain.", "instruction", "\n\n"), ("", "and left.", "instruction", " "),
    ("", "— or not.", "instruction", " "), ("", "”", "instruction", " "),
    ("", "…", "instruction", " "), ("\n\n", "and left.", "instruction", " ")])
def test_extend_joiner(lead, text, mode, joiner):
    assert character_turns._extend_joiner(lead, text, mode) == joiner

def test_extend_tails_carry_the_partial_reply_and_the_steer():
    snap = {"version": 1, "primary_model": "m", "unprofiled": [[{"role": "user", "content": "Go."}], None], "profiles": {}}
    m = character_turns._extend_messages(snap, {"model": "m", "prefill": True},
                                         "Mara ![a lamp](/api/x.png) waits", "colder", 150)
    assert list(m)[-2]["role"] == "system" and "colder" in list(m)[-2]["content"]
    assert list(m)[-1] == {"role": "assistant", "content": "Mara a lamp waits"}
    instr = m.for_connection({"model": "m"}, "m")
    assert instr[-2] == {"role": "assistant", "content": "Mara a lamp waits"}
    assert instr[-1]["role"] == "user"
    assert "150" in instr[-1]["content"] and "colder" in instr[-1]["content"]
    assert not any(x["role"] == "system" and "colder" in x["content"] for x in instr)

def test_strip_preparation_removes_only_a_leading_fence():
    assert response_protocol.strip_preparation("```perception\nnotes\n```\nThen.") == "Then."
    assert response_protocol.strip_preparation(" and left.") == " and left."

def test_is_trailing_skips_synthetic_lines():
    T = store.scenes.serialize.TRANSITION_SPEAKER
    msgs = [{"role": "assistant", "speaker": "Mara", "content": "A", "response_id": "r1"},
            {"role": "assistant", "speaker": T, "content": "→ dock"}]
    assert store.responses.is_trailing(msgs, "r1")
    assert not store.responses.is_trailing(msgs + [{"role": "user", "speaker": "You", "content": "Hi"}], "r1")
```

- [ ] **Step 2: Run, verify FAIL** — `AttributeError: _extend_joiner`.
- [ ] **Step 3: Implement** the template, the four helpers and the README line.
- [ ] **Step 4: Run** `test_response_extend.py`, then `python scripts/verify_templates.py` (with `PYTHONPATH=backend/src`) — PASS.
- [ ] **Step 5: Commit** `feat(extend): the continuation prompt, joiner and trailing check`.

### Task 4: The extend route — stream, land, provenance

**Files:**
- Modify: `backend/src/grimoire/routes/character_turns.py` (new `extend_response` route, `ExtendPlan`, `_served_mode`, `_accept_extend`; generalize `_reroll_frames`; extract `_land_variant` from `_accept_reroll`; `_made_by` gains `extra`)
- Modify: `backend/src/grimoire/store/routing.py` (the `scene` route's task tuple gains `"extend"`, line ~59)
- Test: `backend/tests/test_response_extend.py`

**Interfaces:**
- Consumes: Tasks 1–3; `store.responses.round_typed_note`, `store.steering.record`, `streaming._claim_turn`, `runs.replay_attempt` / `reserve_turn` / `reservation` / `start_detached` / `tail_response` / `lead_frame`, `_override_connection(body, "extend", cid)`, `_turn_override(body)`.
- Produces: `@router.post("/campaigns/{cid}/scenes/{sid}/responses/{rid}/extend") def extend_response(cid, sid, rid, request, body: RegenerateBody | None = None, client = Depends(get_llm), x_grimoire_attempt = Header(default=None))` — the shape of `regenerate_response`: replay check, `_turn_override`, `_require_scene`, `_override_connection(body, "extend", cid)`, `runs.reserve_turn(app, cid, sid, "extend", attempt)`, then inside `runs.reservation` and **one** `store.locks.campaign_lock(cid)` hold: `plan = _extend_target(cid, sid, rid)` (Task 5 adds its refusals; here it returns the plan for a valid target), `token = streaming._claim_turn(cid, sid)`, `note = store.responses.round_typed_note(...)`, `messages = _extend_messages(plan.snapshot, conn, plan.old, guidance, plan.words)`, then `store.steering.record(cid, sid, guidance)` last when guidance is non-empty.
- Produces: `@dataclass(frozen=True) class ExtendPlan: record: dict; old: str; snapshot: dict; composed: str; settings: dict | None; extends: str; words: int | None` — `old` is `record["content"]` from `store.responses.get(cid, sid, rid, private=True)` (the transcript join, gate 3); `snapshot`/`composed`/`settings` are `record["resume_snapshot"]`/`"resume"`/`record.get("resume_settings")` when a resume snapshot exists, else `record["snapshot"]`/`"primary"`/`record.get("settings")`; `extends = record["active_variant"]`; `words = (settings or {}).get("words")`.
- Produces: `_served_mode(messages, meter, conn) -> str` — `messages.mode_for((meter.usage.get(ATTEMPTED) if meter else None) or conn)`.
- Produces: `_made_by(..., extra: dict | None = None)` — merged after the existing keys (`{"extends": plan.extends, "mode": served}`).
- Changes: `_reroll_frames(..., *, note="", guidance="", extend: ExtendPlan | None = None)`. With `extend`: meter task and `_capture` task `"extend"` (meter still carries `post=record.get("post")`, `round_id`, `response_id=rid`); watcher `perception=record["actor_ref"] != "grimoire" and not prefill_capable(conn)`; the `response_start` frame adds `"extend": {"seed": extend.old}`; after `meter.done()`, `made_by = _made_by(meter, "extend", extend.composed, note, guidance, settings=extend.settings if extend.composed == "resume" else None, extra={"extends": extend.extends, "mode": served})`; accept via `_accept_extend`; a returned refusal kind becomes `outcome.fail(kind, detail)` plus an `error` frame. Without `extend` the reroll path is byte-for-byte what it was.
- Produces: `_land_variant(cid, sid, rid, record, watcher, text, tracked, made_by) -> None` — the tail of `_accept_reroll` from `save_variant(..., activate=False, ...)` through `activate`, `tracker_routes.after_swipe`, `streaming._turn_settled`, `tracker_routes.mark(...)`; `_accept_reroll` calls it unchanged in behaviour.
- Produces: `_accept_extend(cid, sid, rid, run, token, record, watcher, plan: ExtendPlan, mode: str, tracked=None, made_by=None) -> tuple[str, str] | None` — under the campaign lock with `_fence`: `run.cancel_requested` → `("replacement_incomplete", "The previous response was retained.")`; `watcher.roll.complete or watcher.roll.truncated` → `("extend_roll_refused", "A continuation cannot propose a roll — reroll the reply instead")`; `raw = watcher.narration`, and `strip_preparation(raw)` in instruction mode; `lead = raw[: len(raw) - len(raw.lstrip())]`; `text, issue = _normalise(cid, sid, record, raw)`; empty `text` → `replacement_incomplete`; else `_land_variant(..., plan.old + _extend_joiner(lead, text, mode) + text, ...)` and `None`.

- [ ] **Step 1: Write the failing tests** (import `_events` from `tests.test_runs_routes`, copy `_answer` from `test_response_controls_routes.py`; use `seed` from `test_character_turns.py` and `FakeLLM`):

```python
def _extend(client, base, rid, reply, body=None):
    fake = FakeLLM([[reply + '\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    return client.post(base + f"/responses/{rid}/extend", json=body or {}), fake

def test_prefill_extend_appends_the_partial_reply_and_saves_a_joined_variant(client):
    cid, sid = seed(client)
    client.put("/api/llm-connections/openrouter", json={"prefill": True})
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    rid = _answer(client, base, "Mara paused")
    before = store.responses.get(cid, sid, rid)
    result, fake = _extend(client, base, rid, ", then left.")
    assert result.status_code == 200
    start = next(f["response_start"] for f in _events(result.text) if "response_start" in f)
    assert start["extend"] == {"seed": "Mara paused"}
    assert fake.messages[-1] == {"role": "assistant", "content": "Mara paused"}
    after = store.responses.get(cid, sid, rid)
    assert after["content"] == "Mara paused, then left."
    assert len(after["variants"]) == len(before["variants"]) + 1
    assert after["active_variant"] == after["variants"][-1]["id"]
    made_by = after["variants"][-1]["made_by"]
    assert made_by["task"] == "extend" and made_by["mode"] == "prefill"
    assert made_by["extends"] == before["active_variant"] and made_by["composed"] == "primary"

@pytest.mark.parametrize("kind", ["claude", "openai_compatible"])
def test_instruction_extend_on_a_non_prefill_connection(client, kind): ...
    # create the connection (openai_compatible with post_process "strict"), extend with
    # {"connection_id": <id>} and reply "Then the door opened.";
    # fake.messages[-1]["role"] == "user" and "Continue exactly where" in it;
    # fake.messages[-2] == {"role": "assistant", "content": "Original."};
    # no trailing assistant message; content == "Original.\n\nThen the door opened.";
    # made_by["mode"] == "instruction"

def test_instruction_extend_joins_a_lowercase_continuation_with_a_space(client): ...
    # "Original." + "and she left." -> "Original. and she left."

def test_instruction_extend_strips_a_leading_perception_fence(client): ...
    # reply "```perception\nShe notes the door.\n```\nThen she left." -> "Original.\n\nThen she left."

def test_extend_continues_the_trimmed_transcript_prose(client): ...
    # prefill on; _answer "Original."; store.scenes.edit_message(cid, sid, index, "Orig");
    # extend reply "inal, she said." -> content "Original, she said."

@pytest.mark.parametrize("prefill", [True, False])
def test_empty_continuation_keeps_the_active_variant(client, prefill): ...
    # reply "" (handoff only) -> "replacement_incomplete" in result.text;
    # variants and active_variant unchanged

def test_a_roll_fence_in_the_continuation_is_refused(client): ...
    # FakeLLM reply 'She reaches.\n```roll\n{"check":"notice"}\n```' ->
    # "extend_roll_refused" in result.text; variants unchanged; store.proposals.get(cid, sid) is None

def test_a_multi_part_response_extends_from_its_resume_snapshot(client): ...
    # roll fence, then decline with a null handoff (pattern:
    # test_roll_decline_continues_same_actor_then_handoff); record = get(private=True);
    # the extend request minus its tail equals
    # list(PreparedMessages.from_snapshot(record["resume_snapshot"], <model>));
    # made_by["composed"] == "resume" and made_by["settings"] == record["resume_settings"];
    # the response is one message afterwards (activate collapses parts)

def test_steer_rides_the_instruction_and_reaches_the_steering_log(client): ...
    # body {"guidance": "colder"}: in the user message, not a system message;
    # store.steering read for the scene gains "colder"; made_by["guidance"] == "colder"

def test_served_mode_follows_the_attempt_that_answered():
    # _served_mode(tailed, meter_with_usage({ATTEMPTED: {"kind": "openai_compatible"}}),
    #              {"prefill": True}) == "instruction"; with meter None -> mode of conn

def test_extend_is_metered_as_extend_and_not_counted_as_a_reroll(client): ...
    # the newest usage row has task "extend", post and response_id;
    # store.usage.scene_usage(cid, sid) reports no reroll for that post
```

Also in `test_routing_guard.py` nothing new is needed — it runs over the code and fails until `routing.py` claims `extend`.

- [ ] **Step 2: Run, verify FAIL** — 404/405 on `/extend`.
- [ ] **Step 3: Implement** per Interfaces. `_extend_target` in this task only resolves the plan (`editable` + `get(private=True)`); Task 5 adds the rest of its refusals.
- [ ] **Step 4: Run** `test_response_extend.py test_response_controls_routes.py test_response_provenance.py test_character_turns.py test_routing_guard.py test_routing.py` — PASS.
- [ ] **Step 5: Commit** `feat(extend): keep writing the trailing response`.

### Task 5: Refusals, and the docs that name the handler

**Files:**
- Modify: `backend/src/grimoire/routes/character_turns.py` (`_extend_target`)
- Modify: `CLAUDE.md` (detached runs: "Twenty-one handlers" → "Twenty-two handlers"; the `turn` class list gains `extend_response`)
- Test: `backend/tests/test_response_extend.py`

**Interfaces:**
- Produces: `_extend_target(cid: str, sid: str, rid: str) -> ExtendPlan`, called inside the route's single campaign-lock hold, raising `HTTPException(409, detail={"kind": ..., "detail": ...})` (404 for `ResponseNotFound`, via `_public_error`) in this order:
  1. `store.responses.editable(cid, sid, rid)` → `applied_mechanics`;
  2. `not store.responses.is_trailing(messages, rid)` → `not_last_response` ("Only the last reply can be continued.");
  3. `not record["snapshot"]` → `historical_context_unavailable` (same detail as `regenerate_response`);
  4. step 2's frozen-prompt check (written `store.responses.excluded_since(cid, sid, rid)` — use the real helper) → `context_excluded`;
  5. `store.responses.unfinished(cid, sid)` → `round_open`;
  6. `(store.proposals.get(cid, sid) or {}).get("status") in store.proposals.NON_TERMINAL` → `proposal_pending`;
  7. `store.pending_reviews.read(cid, sid) is not None` → `review_pending`;
  8. `record["status"] != "complete"` → `variant_incomplete`.
  None of these spends: every refusal happens before the LLM call and before `store.steering.record`.

- [ ] **Step 1: Write the failing tests** — one row per refusal, each asserting the status, the kind, `fake.calls == 0`, and an unchanged steering log:

```python
def test_extend_refusals(client, monkeypatch): ...
# not_last_response: two _answer calls, extend the first
# applied_mechanics: store.scenes.append_message(cid, sid, "assistant", "🎲 1d20 = 12",
#                    speaker=store.scenes.serialize.ROLL_SPEAKER) after the response
# historical_context_unavailable: store.scenes.append_message(cid, sid, "assistant", "Old narration.")
# context_excluded: exclude the player post before the response through step 2's route
#                   (PUT .../messages/{i}/excluded {"excluded": true}), then extend
# round_open: a reply cut short (FakeLLM([["Partial"]], error=LLMError("rate_limit", "Wait")))
# variant_incomplete: the same, then store.responses.update_round(cid, sid, round_id, status="superseded")
# proposal_pending: after a landed answer, store.proposals.new(cid, sid, {"check": "notice"})
# review_pending: monkeypatch.setattr(store.pending_reviews, "read", lambda c, s: {"review": {}})
# run_in_flight: runs.reserve_turn(client.app, cid, sid, "chat", "a-1") first
#                (pattern: test_scene_freeze.py), then extend -> 409 run_in_flight

def test_trailing_transition_lines_do_not_make_a_reply_not_last(client): ...
    # append a TRANSITION_SPEAKER line after the response; extend succeeds

def test_extend_on_a_closed_branch_is_refused(client): ...
    # only if step 3's helper is present: close the scene as its spec's tests do;
    # extend -> 409 branch_closed (inherited from reserve_turn)
```

- [ ] **Step 2: Run, verify FAIL** — refusals answer 200.
- [ ] **Step 3: Implement** `_extend_target`'s checks and the CLAUDE.md edit.
- [ ] **Step 4: Run** `test_response_extend.py test_docs_guard.py test_scene_freeze.py` — PASS.
- [ ] **Step 5: Commit** `feat(extend): refuse what a continuation would break`.

### Task 6: Frontend plumbing — types, api, labels, connection form

**Files:**
- Modify: `frontend/src/api/types.ts` (`LLMConnection.prefill?: boolean`, `LLMConnectionDraft.prefill?: boolean`, `PromptEntry.task` gains `"extend"`)
- Modify: `frontend/src/api/stream.ts` (`response_start?: { id; speaker; actor_ref; extend?: { seed: string } }`)
- Modify: `frontend/src/api/client.ts` (new `extendResponse`, beside `regenerateResponse`)
- Modify: `frontend/src/components/turnLabels.ts` (`extend: "Extend"`)
- Modify: `frontend/src/components/ConnectionForm.tsx` (`ConnectionFormValue.prefill?: boolean`, `BLANK_CONNECTION.prefill: false`, a checkbox), `frontend/src/components/ConnectionEditor.tsx` (`setForm` reads `d.prefill ?? false`; `save` sends `prefill`)
- Modify: `frontend/src/testkit/campaignMocks.tsx` (`extendResponse: vi.fn()`), `frontend/src/testkit/campaignHarness.tsx` (default in `installCampaignMocks`: `mockResolvedValue(undefined)`)
- Test: `frontend/src/components/ConnectionEditor.test.tsx`, `frontend/src/api/client.test.ts`

**Interfaces:**
- Produces: `api.extendResponse(cid: string, sid: string, rid: string, onEvent: (e: ChatEvent) => void, body?: RegenerateOverrides, signal?: AbortSignal, attempt?: string, onIndex?: (i: number) => void)` → `streamPost(\`/api/campaigns/${cid}/scenes/${sid}/responses/${rid}/extend\`, body ?? {}, onEvent, signal, attempt, onIndex)`.
- Produces: a checkbox labelled **"Continue replies by prefill"** for every kind, hint: "Send a cut-short reply back as the start of the model's own turn. Only for models that continue a trailing assistant message; most chat models and current Claude models do not, and get an instruction instead."

- [ ] **Step 1: Write the failing tests** — `ConnectionEditor.test.tsx`: opening a connection with `prefill: true` shows the box checked; unchecking and saving calls `api.updateConnection(id, expect.objectContaining({ prefill: false }))`. `client.test.ts`: `extendResponse` posts to `/responses/r1/extend` with the body and forwards the attempt header (pattern: the `TURN_PRODUCERS` table — add a row).
- [ ] **Step 2: Run, verify FAIL**.
- [ ] **Step 3: Implement**.
- [ ] **Step 4: Run** `cd frontend && npx vitest run src/components/ConnectionEditor.test.tsx src/api/client.test.ts && npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(play): extend api and the connection's prefill switch`.

### Task 7: Keep writing ▸ in the play view

**Files:**
- Modify: `frontend/src/components/ResponseControls.tsx` (props `onExtend?`, `extendDisabled?`)
- Modify: `frontend/src/components/play/TranscriptPost.tsx` (`TranscriptReroll.extend`, `TranscriptActions.extendResponse`, `TranscriptContext.hiddenResponse`; skip the hidden response's rows)
- Modify: `frontend/src/routes/CampaignView.tsx` (`extendResponse`, the disabled flag, the `seed` on stream boundaries in `runStream` **and** `attachToRun`, the bubble render, `hiddenResponse`)
- Test: `frontend/src/components/ResponseControls.test.tsx`, `frontend/src/routes/CampaignView.test.tsx`

**Interfaces:**
- Produces: `ResponseControls` renders `<button disabled={disabled || extendDisabled} onClick={() => onExtend(responseId, guidance.trim(), route)}>Keep writing ▸</button>` beside **Reroll response**, inside the `canReroll` branch, only when `onExtend` is given — the steer box and route picker are shared.
- Produces: `TranscriptReroll.extend: { disabled: boolean } | null`; `TranscriptPost` passes `onExtend={actions.extendResponse}` and `extendDisabled` only on the swipe-target row (`rerollRow && m.response_id && ctx.lastOfResponse.has(index)`) when `reroll?.extend` is set.
- Produces: in `CampaignView`, `extendDisabled = swipeBlocked || !canReroll || !ledgerSwipe?.can_reroll` (the conditions the swipe's generating › already uses: busy/rolling/locked/editing/renames, a landed review, a live proposal, a pending swipe read, `!editable`, `round_open`; `edited` does **not** block — a trimmed reply is continued from the trim). No `useHotkeys` binding.
- Produces: `async function extendResponse(id: string, guidance: string, route: RerollRoute = NO_REROLL_ROUTE)` — the guards and the `runStream(sid, …api.extendResponse(cid, sid, id, onEvent, { guidance, ...route }, signal, attempt, onIndex), undefined, true, "", true)` call of `rerollResponse`; never sends or clears `pendingResponse`.
- Produces: the stream boundary type gains `seed?: string`, set from `e.response_start.extend?.seed` in both handlers; the bubble renders `part.seed !== undefined ? part.seed + " " + slice : slice` for that part. `streaming` (the accumulator) never contains the seed.
- Produces: `TranscriptContext.hiddenResponse: string | null` — `busy && streamingId === activeId ? streamingSpeakers.find((p) => p.seed !== undefined)?.id ?? null : null`; rows with that `response_id` are not rendered. After `done` the reload shows the server-joined variant.

- [ ] **Step 1: Write the failing tests**

```tsx
// ResponseControls.test.tsx
it("keeps writing with the shared steer and route, and only when offered", () => {
  const onExtend = vi.fn();
  show({ onExtend });
  fireEvent.change(screen.getByLabelText("Response steer"), { target: { value: "Colder" } });
  fireEvent.click(screen.getByRole("button", { name: "Keep writing ▸" }));
  expect(onExtend).toHaveBeenCalledWith("response-a", "Colder", { connection_id: "", model: "" });
});
// + without onExtend there is no "Keep writing ▸"; with extendDisabled it is disabled

// CampaignView.test.tsx (shared testkit harness; mock getResponseSwipe with
// { active: 0, variants: [{ id: "v1", status: "complete" }], settings: null, resume_settings: null,
//   can_reroll: true, editable: true, round_open: false, edited: false })
test("keep writing is offered on the trailing response only and calls the extend route", ...)
  // two responses (response-a, response-b); open both disclosures: exactly one
  // "Keep writing ▸"; clicking it calls api.extendResponse with ("run", "s1", "response-b", fn,
  // { guidance: "", connection_id: "", model: "" }, ...) and never api.regenerateResponse
test("the extend bubble grows from the old text and hides the target post", ...)
  // extendResponse emits { response_start: { id: "response-b", speaker: "Mara",
  //   actor_ref: "characters:mara", extend: { seed: "The accepted response." } } }
  // then { delta: "Then more." }: the bubble reads "The accepted response. Then more."
  // and the transcript row for response-b is not rendered while busy
test("keep writing is disabled like the generating swipe", ...)
  // round_open: true -> disabled; editable: false -> disabled; a failed swipe read
  // (the harness default) -> disabled; edited: true alone -> enabled
```

- [ ] **Step 2: Run, verify FAIL**.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** `cd frontend && npx vitest run src/components/ResponseControls.test.tsx src/routes/CampaignView.test.tsx src/components/play && npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(play): keep writing the last reply`.

### Task 8: The gate

- [ ] **Step 1:** `make check PY=$(pwd)/backend/.venv/bin/python`.
- [ ] **Step 2:** Fix what it reports — nothing else. A ratchet failure from a count that moved is `make baseline`, committed with the fix.
- [ ] **Step 3: Commit** `chore: make check green for keep writing` (only if Step 2 changed anything).

## Controller ruling (binding)

Multi-part responses (gate resolution 4): the resume snapshot already carries
the reply's earlier part(s) as history, so the tail's partial-reply message is
the **latest part's text only** (`variant["part_content"]` of the active
variant, falling back to the transcript text of the last message carrying the
response id). The saved variant is still the **whole** transcript prose +
joiner + continuation. Test: a two-part response's extend prompt contains part
one exactly once.

## Plan-gate rulings (binding; override the tasks and the controller ruling above where they differ)

Plan → implementation gate: independent adversarial review (stand-in for
`/codex:adversarial-review`; owner-approved).

1. **Which snapshot, which partial.** Use the resume snapshot **only while the
   transcript still holds the response as separate parts** (some message with
   this `response_id` has a non-empty `response_part`); then the partial is the
   transcript content of the **last** such message (which also honours a hand
   trim of that part). Otherwise use the primary snapshot with partial = `old`
   (the whole transcript prose). `ExtendPlan` gains `partial`, passed to
   `_extend_messages`. Tests: extend twice on a two-part reply; reroll a
   two-part reply then extend — part one appears exactly once in each prompt.
2. **Routing guard.** `_reroll_frames` takes `task: str = "regenerate"` as a
   keyword and `extend_response` calls it with the literal `task="extend"`, so
   the guard's literal scan sees `extend`.
3. **`with_tails`** builds the copy as `PreparedMessages(self._primary_model,
   self._factory, profiles=self._frozen_profiles)` plus the tails — never via
   `snapshot()` (which raises on unfrozen prompts the tests build).
4. **Metering test** seeds with `{"content": "Hello", "speaker_ref":
   "characters:mara"}` so the response has a post.
5. **Fallback capture**: `for_connection` fires `on_variant` with the tailed
   list (and `_capture` points at it), so a fallback's actual prompt is logged.
6. **Handoff/issue**: the extended variant keeps the previous variant's
   `handoff`; `issue` is set only from `_normalise` (speaker authority), as
   `_accept_reroll` does.
7. **Hiding the target while streaming** happens where speaker groups are built
   (`TranscriptRun` / CampaignView), so an emptied group draws no plate;
   `hiddenResponse` joins the `transcriptCtx` memo deps.
8. Minor: CLAUDE.md's detached-runs count is recounted from the real handler
   list (add `regenerate_response` and `extend_response`), not hard-coded; the
   perception-fence-in-live-bubble on a prefill→instruction fallback is
   accepted (saved text is clean); prefill-mode steer uses a continuation
   wording (`response_steer.j2` gains a `continuation` flag); tests build a
   meter as `SimpleNamespace(usage={ATTEMPTED: {...}})`; the `branch_closed`
   test is unconditional (step 3 lands first); Keep writing is also disabled
   when the swipe read shows a non-complete active variant.
