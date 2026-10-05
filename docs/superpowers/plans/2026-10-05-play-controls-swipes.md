# Play controls I — swipes on the live response path: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record what made every response-ledger variant, and give the last response SillyTavern swipes (arrows, touch swipe, ←/→ keys).

**Architecture:** Backend: response settings are captured once in `_assemble` and stored on the response record; each variant gets a `made_by` built from the meter's usage holder; a lock-free swipe read serves the client. Frontend: a `useResponseSwipe` hook owns the fetch and the step/generate logic, a `useSwipe` hook owns the touch gesture, and `TranscriptPost` renders the arrows from both.

**Tech Stack:** FastAPI + pytest (`backend/`), React + vitest (`frontend/`).

**Spec:** `docs/superpowers/specs/2026-10-05-play-controls-swipes-design.md`

## Global Constraints

- Unknown is absent, never zero: a `made_by` key the usage holder lacks is omitted, never filled from `effective_model(conn)`.
- Building `made_by` never fails a turn (catch, `logging.getLogger("grimoire.character_turns")` log, return `None`).
- Guidance is clipped to `store.alternates.MAX_GUIDANCE_CHARS` (500) before it is stored anywhere new.
- pydantic stays v1/v2-agnostic; responses go out as plain dicts.
- Imports at module scope; inside `store/`, bind submodules (`test_import_guard.py`).
- Keys only through `useHotkeys`; chords `arrowleft` / `arrowright`; group `IN THIS SCENE`; never `whileTyping`.
- `SWIPE_MIN_PX = 56`; horizontal travel ≥ 2× vertical.
- Placeholder names only in fixtures (Mara, Winifred, Seraphine, Realm, Saltmarch).
- Ratchet gates: if a change resolves or adds a lint/mypy/eslint finding, run `make baseline` and commit the new `lint-baselines/*.json` with that change.
- Backend tests: `cd backend && PYTHONPATH=src .venv/bin/python -m pytest -q <files>`. Frontend tests: `cd frontend && npx vitest run <files>`.

## Review Focus

1. A swipe while an automatic round is still handing off (B paused on a roll after A finished) must not supersede B's proposal — the client disables on `round_open` / live proposal (Task 8 test).
2. Rapid double-click on › at the newest must start one reroll, not two — `rerollResponse` already bails while `busy`; Task 8 pins it.
3. A vertical scroll that drifts sideways on a phone must not swipe — 2× angle rule and `pointercancel` (Task 7 tests).
4. A scene opened on a migrated legacy reply (one variant, no snapshot) must show no dead `1/1` control (Task 8 test).
5. A provider that reports no model (holder without `model`) must still save the variant, with `model` absent (Task 2 test).

---

### Task 1: Response settings on the response record

**Files:**
- Modify: `backend/src/grimoire/store/context/assemble.py` (`_assemble` return dict; `_prepare`)
- Modify: `backend/src/grimoire/store/responses.py` (`prepare`, `save_resume_prompt`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`_prepare`)
- Test: `backend/tests/test_response_provenance.py` (new)

**Interfaces:**
- Produces: `_assemble(...)["response_settings"] -> {"style_id": str, "phase": "opening"|"continuation", "words": int, "paragraphs": int}` (the phase dict is `targets[phase]` merged with `style_id` and `phase`).
- Produces: `PreparedMessages.settings: dict | None` — set by `assemble._prepare` from `a.get("response_settings")` (attribute assigned after construction).
- Produces: `responses.prepare(cid, sid, round_id, actor_ref, speaker, snapshot, settings: dict | None = None) -> dict` storing `record["settings"]` when given.
- Produces: `responses.save_resume_prompt(cid, sid, rid, snapshot, settings: dict | None = None) -> None` storing `record["resume_settings"]` when given.
- Produces: `character_turns._prepare(...) -> tuple[dict, PreparedMessages, str]` — third element `composed`: `"primary"` for a new composition or a reused primary snapshot, `"resume"` for a resume composition or a reused `resume_snapshot`.

- [ ] **Step 1: Write the failing tests** in `test_response_provenance.py` (reuse the `seed` helper by copying its body from `test_character_turns.py` — it is a module function there, not a fixture):

```python
def test_first_take_records_the_settings_the_prompt_rendered(client):
    cid, sid = seed(client)
    store.scenes.set_meta(cid, sid, {"response_continuation_words": "300"})  # use the scene meta setter the response-targets routes use
    fake = FakeLLM([['Hi.\n```handoff\n{"next":null}\n```']])
    client.app.dependency_overrides[routes.get_llm] = lambda: fake
    base = f"/api/campaigns/{cid}/scenes/{sid}"
    client.post(base + "/chat", json={"content": "Hello", "speaker_ref": "characters:mara"})
    rid = store.scenes.read_scene(cid, sid)["messages"][-1]["response_id"]
    record = client.get(base + f"/responses/{rid}").json()
    assert record["settings"]["phase"] == "continuation"
    assert record["settings"]["words"] == 300
    assert "provenance" not in record["settings"]

def test_one_shot_override_is_in_the_recorded_settings(client): ...
    # POST /chat with "response": {"response_continuation_words": "90"}; record["settings"]["words"] == 90

def test_reroll_does_not_re_resolve_settings(client): ...
    # first take at 300, change scene meta to 120, reroll; record["settings"]["words"] == 300

def test_roll_continuation_records_resume_settings(client): ...
    # drive a roll fence then decline (pattern: test_roll_decline_continues_same_actor_then_handoff);
    # change scene words between fence and decline; record["resume_settings"]["words"] is the new value,
    # record["settings"]["words"] the old one
```

Find the scene-meta setter the response-targets route uses (`grep -n "response_continuation_words" backend/src/grimoire/routes/*.py`) and call that store function rather than inventing one.

- [ ] **Step 2: Run, verify FAIL** — `KeyError: 'settings'`.
- [ ] **Step 3: Implement.** In `_assemble`, after `targets = response_targets.resolve(...)`, compute `phase = "opening" if opening_narrator else "continuation"` once (reuse it for `data["budget"]`) and add `"response_settings": {"style_id": budget["style_id"], "phase": phase, **targets[phase]}` to the returned dict. In `assemble._prepare`, set `prepared.settings = deepcopy(a.get("response_settings"))` before returning. Thread `settings` through `responses.prepare` / `save_resume_prompt`, and make `character_turns._prepare` pass `getattr(messages, "settings", None)` and return `composed`; update its two callers in `_frames` to unpack three values.
- [ ] **Step 4: Run** the new file plus `test_character_turns.py test_responses.py test_context.py` — PASS.
- [ ] **Step 5: Commit** `feat(responses): record the response settings a prompt rendered`.

### Task 2: `made_by` on every saved variant

**Files:**
- Modify: `backend/src/grimoire/store/responses.py` (`save_variant`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`_made_by`, `_frames`, `_save`, `_pause`, `_rescue`, `_reroll_frames`, `_accept_reroll`, `regenerate_response`)
- Modify: `frontend/src/api/types.ts` (`ResponseRecord`)
- Test: `backend/tests/test_response_provenance.py`

**Interfaces:**
- Consumes: Task 1's `composed` from `_prepare`.
- Produces: `responses.save_variant(..., made_by: dict | None = None)` — stored as `variant["made_by"]` only when not `None`.
- Produces: `character_turns._made_by(meter, record: dict, task: str, composed: str, guidance: str = "") -> dict | None`. Reads `meter.usage` keys `model`, `connection`, `provider`, and `meter.usage.get(llm.ATTEMPTED, {}).get("id")` as `connection_id`, omitting falsy ones; `note` from `store.responses` round lookup by `record["round_id"]` (add `responses.round_note(cid, sid, round_id) -> str`, lock-free read, `""` when absent); `guidance[:alternates.MAX_GUIDANCE_CHARS]`. Wrapped in `try/except Exception` → log + `None`.
- Produces: `_save(..., made_by=None)` and `_pause(..., made_by=None)` pass it through to `save_variant`; `_accept_reroll(..., made_by=None)` likewise.
- Produces (TS): `ResponseVariant.made_by?: { task?: string; connection_id?: string; connection?: string; model?: string; provider?: string; composed?: "primary"|"resume"; guidance?: string; note?: string }`; `ResponseRecord.settings?` / `resume_settings?: { style_id: string; phase: string; words: number; paragraphs: number }`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_turn_variant_says_what_made_it(client):
    # one chat turn; variant = record["variants"][-1]["made_by"]
    assert made_by["task"] == "chat" and made_by["composed"] == "primary"
    assert made_by["model"] and made_by["provider"] == "openrouter"
    assert made_by["guidance"] == "" and made_by["note"] == ""

def test_director_turn_and_its_reroll_carry_the_note(client): ...
    # empty-composer director send with a note (POST /chat {"content": "", "direct": ...} — use the
    # director body the existing director tests use); reroll it; both variants' note == that note

def test_reroll_variant_carries_clipped_guidance(client): ...
    # guidance = "x" * 600 -> made_by["guidance"] == "x" * 500, task == "regenerate"

def test_fallback_records_the_served_connection(client): ...
    # pattern: test_model_guidance_routes.test_fallback_captures_matching_profile_only_when_attempted
    # (llm.LLMClient with a failing ScriptedProvider primary and a serving fallback);
    # made_by["model"] == the fallback model, made_by["connection_id"] == the fallback's id

def test_holder_without_a_model_leaves_model_absent(client, monkeypatch): ...
    # monkeypatch character_turns' meter so usage lacks "model"; "model" not in made_by; variant saved

def test_ledger_append_failure_still_records_made_by(client, monkeypatch): ...
    # monkeypatch store.usage.record to raise OSError inside (or make its path unwritable); made_by present

def test_prose_before_a_roll_fence_and_a_rescue_carry_made_by(client): ...
    # roll-fence turn: the incomplete variant has made_by; a FailingOpenRouter mid-stream: incomplete variant has made_by

def test_continuation_variant_is_composed_resume(client): ...

def test_old_variant_without_made_by_round_trips(client): ...
    # write a ledger variant without made_by directly, activate another and back; no made_by key appears
```

- [ ] **Step 2: Run, verify FAIL** — `KeyError: 'made_by'`.
- [ ] **Step 3: Implement.** In `_frames`, build `made_by = _made_by(meter, record, meter.task, composed)` immediately after `meter.done()` and **before** `meter = None`; pass it to `_pause` and `_save`. In `_rescue`, build it after its own `meter.done(...)` (meter may be `None` → `made_by=None`). In `_reroll_frames`, build it after `meter.done()` with `task="regenerate"`, `composed="primary"`, and `guidance` (pass `body.guidance or ""` from `regenerate_response` into `_reroll_frames` as a new keyword argument).
- [ ] **Step 4: Run** `test_response_provenance.py test_character_turns.py test_responses.py test_response_controls_routes.py test_response_mechanics.py` — PASS.
- [ ] **Step 5: Commit** `feat(responses): record what made each variant`.

### Task 3: Steering on ledger rerolls

**Files:**
- Modify: `backend/src/grimoire/routes/character_turns.py` (`regenerate_response`)
- Test: `backend/tests/test_response_provenance.py`

- [ ] **Step 1: Write the failing tests:**

```python
def test_ledger_reroll_records_its_steer(client):
    # reroll with guidance "Colder." -> store.steering.texts(cid, sid) == ["Colder."]
def test_empty_guidance_records_no_steer(client): ...           # texts == []
def test_refused_reroll_records_no_steer(client): ...
    # a record with snapshot removed (historical_context_unavailable, 409) -> texts == []
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** — `store.steering.record(cid, sid, body.guidance)` inside the campaign-lock hold, after the `snapshot` check and before `streaming._claim_turn`, only when `body and body.guidance`.
- [ ] **Step 4: Run** the file plus `test_steering_store.py` — PASS.
- [ ] **Step 5: Commit** `fix(responses): a ledger reroll's steer reaches absorb`.

### Task 4: A swipe keeps a rolling summary that never covered it

**Files:**
- Modify: `backend/src/grimoire/store/responses.py` (`_invalidate`, `activate`, `delete`)
- Test: `backend/tests/test_responses.py`

**Interfaces:**
- Produces: `responses._invalidate(cid, sid, changed_at: int) -> None` — resets the rolling summary only when `changed_at < read.read_scene(cid, sid)["meta"]` rolling `at` (use the same reader `store/scenes/read.py:231` uses — `rolling_at` parsed by `_count`; expose it if needed as `read.rolling_at(meta) -> int`). Rounds, proposals and the scene break are reset as today.

- [ ] **Step 1: Write the failing tests:**

```python
def test_activating_the_trailing_response_keeps_an_earlier_fold(client):
    # two turns; set_rolling_summary(cid, sid, "Earlier.", at=2, digest=<digest of first 2>);
    # reroll the trailing response and activate the old variant; summary still "Earlier."
def test_activating_a_folded_response_resets_the_summary(client):
    # summary at=len(messages); activate a variant of a response inside it; summary == ""
```

- [ ] **Step 2: Run, verify FAIL** — summary reset to `""`.
- [ ] **Step 3: Implement** — `activate` passes `index` (from `editable`), `delete` passes `index`.
- [ ] **Step 4: Run** `test_responses.py test_character_turns.py test_rolling_summary*.py` (whatever exists: `ls backend/tests | grep -i rolling`) — PASS.
- [ ] **Step 5: Commit** `fix(responses): keep a rolling summary a swipe did not touch`.

### Task 5: The lock-free swipe read

**Files:**
- Modify: `backend/src/grimoire/store/responses.py` (`swipe_state`, `_editable_reason`)
- Modify: `backend/src/grimoire/routes/character_turns.py` (`GET .../responses/{rid}/swipe`)
- Modify: `frontend/src/api/client.ts`, `frontend/src/api/types.ts`
- Test: `backend/tests/test_responses.py`, `backend/tests/test_response_controls_routes.py`

**Interfaces:**
- Produces: `responses._editable_reason(record: dict, messages: list[dict], scene_rolls: bool, rid: str) -> str | None` — the three checks `editable` makes, returning the refusal kind or `None`; `editable` is rewritten to call it (behaviour unchanged).
- Produces: `responses.swipe_state(cid, sid, rid) -> dict` raising `ResponseNotFound`: `{"active": int|None, "variants": [{"id", "status", "made_by"?}], "settings": dict|None, "resume_settings": dict|None, "can_reroll": bool, "editable": bool, "round_open": bool}`. `active` is the index in `variants` of `active_variant`. Uses `identity.scene_identity` (never `ensure_identity`), no lock, no snapshot reads, no variant `content`/`reasoning`.
- Produces: route `GET /campaigns/{cid}/scenes/{sid}/responses/{rid}/swipe` → `swipe_state`, 404 on `ResponseNotFound`.
- Produces (TS): `api.getResponseSwipe(cid, sid, rid): Promise<ResponseSwipe>`; `type ResponseSwipe` mirroring the dict.

- [ ] **Step 1: Write the failing tests:**

```python
def test_swipe_state_takes_no_lock_and_writes_nothing(client):
    # hold store.locks.campaign_lock(cid) in another thread; swipe_state returns within 1s;
    # scene file and responses.json mtimes unchanged
def test_swipe_state_omits_contents(client): ...   # no "content"/"reasoning" in any variant
def test_swipe_state_not_editable_after_a_roll(client): ...  # POST .../roll after the response -> editable False
def test_swipe_state_not_editable_at_the_audit_boundary(client): ...
    # a rolls.json entry for this scene and no roll line in the transcript -> editable False
def test_swipe_state_round_open_while_paused(client): ...    # roll fence pending -> round_open True
def test_swipe_route_404_for_unknown_response(client): ...
```

- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run** `test_responses.py test_response_controls_routes.py test_import_guard.py test_lock_domain_guard.py` — PASS.
- [ ] **Step 5: Commit** `feat(responses): a lock-free swipe read`.

### Task 6: `useResponseSwipe` — fetch, step, generate

**Files:**
- Create: `frontend/src/components/play/useResponseSwipe.ts`
- Create: `frontend/src/components/play/swipeTitle.ts`
- Test: `frontend/src/components/play/swipeTitle.test.ts`

**Interfaces:**
- Consumes: `api.getResponseSwipe` (Task 5).
- Produces: `swipeTitle(swipe: ResponseSwipe): string | undefined` — lines in order `Guided: …`, `Note: …`, `Model: …`, `Via: <connection>`, `Style: <style_id>`, `Length: ~<words> words, <paragraphs> paragraphs`, absent keys skipped; settings from `resume_settings` when the active variant's `made_by.composed === "resume"`, else `settings`.
- Produces: `useResponseSwipe(args: { cid: string; sid: string | null; rid: string | null; content: string | null; window: unknown; tick: number }) : { swipe: ResponseSwipe | null; complete: string[]; position: number | null; refresh(): void }` — refetches when `cid/sid/rid/content/window/tick` change; drops a response whose request no longer matches (the `altsReq` pattern in `CampaignView.tsx`); `swipe` is `null` on error. `complete` = ids of complete variants in order; `position` = index of the active id in `complete`.

- [ ] **Step 1: Write the failing tests** for `swipeTitle`: all keys present → six lines in order; `model` absent → no `Model:` line; `composed: "resume"` reads `resume_settings`; no `made_by` → `undefined`.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** both modules.
- [ ] **Step 4: Run** `npx vitest run src/components/play/swipeTitle.test.ts` — PASS.
- [ ] **Step 5: Commit** `feat(play): swipe state hook and provenance tooltip`.

### Task 7: `useSwipe` — the touch gesture

**Files:**
- Create: `frontend/src/components/play/useSwipe.ts`
- Test: `frontend/src/components/play/useSwipe.test.tsx`

**Interfaces:**
- Produces: `export const SWIPE_MIN_PX = 56;` and `useSwipe(opts: { enabled: boolean; onNext(): void; onPrevious(): void }): { onPointerDown, onPointerUp, onPointerCancel }` — React handlers for the `.msg` element. Left swipe (dx ≤ −56) → `onNext`; right swipe → `onPrevious`.

- [ ] **Step 1: Write the failing tests** (render a `<div {...handlers}>` with a child `<button>` and a child `<div style="overflow-x:auto">`):
  - touch drag dx −80, dy 10 → `onNext` once; dx +80 → `onPrevious` once;
  - dx −50 → nothing; dx −80, dy 50 → nothing;
  - `pointerType: "mouse"` → nothing; `pointercancel` between down and up → nothing; a second `pointerdown` mid-gesture → nothing;
  - started on the button / inside the scrollable div → nothing;
  - `window.getSelection()` non-empty at `pointerup` → nothing; `enabled: false` → nothing.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** — store start `{id, x, y}` in a ref on a touch `pointerdown` whose target is not inside `button, a, input, textarea, select, [contenteditable], form` and has no ancestor (up to the handler's element) with `scrollWidth > clientWidth`; decide on `pointerup` for the same `pointerId`.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(play): touch swipe gesture`.

### Task 8: Swipes on the last response in the play view

**Files:**
- Modify: `frontend/src/routes/CampaignView.tsx` (swipe target, `useResponseSwipe`, step/generate, `useHotkeys`, `transcriptReroll`)
- Modify: `frontend/src/components/play/TranscriptPost.tsx` (`TranscriptSwipe` gains `previous`/`next` disabled flags and the gesture; render on ledger posts)
- Modify: `frontend/src/index.css` (`touch-action: pan-y` on the swipe target)
- Test: `frontend/src/routes/CampaignView.swipes.test.tsx` (new; header and mocks copied from `CampaignView.shortcuts.test.tsx`)

**Interfaces:**
- Consumes: Tasks 6 and 7.
- Produces: `TranscriptSwipe = { active: number | null; count: number; title?: string; previousDisabled: boolean; nextDisabled: boolean; ledger: boolean }`; `TranscriptActions.stepAlternate(delta)` unchanged for legacy, new `TranscriptActions.stepVariant(delta: -1 | 1)` for ledger.

Behaviour (spec §6): target = `messages[rerollIndex]` when it has `response_id` and is `lastOfResponse`. Arrows when `complete.length >= 2 || (complete.length === 1 && swipe.can_reroll)`. `blocked = responseDisabled || absorb || proposal live || !swipe.editable || swipe.round_open`. ‹ disabled when `blocked || position === 0`. › at the newest generates via `rerollResponse(rid, "", NO_REROLL_ROUTE, { keepPending: true })` — add that option so the call sends `response: undefined` and skips `setPendingResponse(null)`; disabled when `blocked || !canReroll || !swipe.can_reroll`. Stepping calls the existing `mutateResponse(rid, vid)`; on its failure path also call `refresh()`. Keys: `arrowleft` "Previous reply variant", `arrowright` "Next reply variant" — at the newest it does `setRerollPrompt(""); setRerollRoute(NO_REROLL_ROUTE)` (what `r` does), never generates; `enabled` = the arrows' condition.

- [ ] **Step 1: Write the failing tests:**
  - arrows on the last response only (`2/3` from a mocked `getResponseSwipe`), none on an earlier response;
  - ‹ calls `activateResponseVariant` with the previous complete id, skipping an incomplete one; the counter follows after the mocked scene returns new content for the same rid;
  - › at the newest calls `regenerateResponse` with `guidance: ""` and no `response`, and a pending length chip survives; a second click while busy calls it once;
  - all arrows disabled with `round_open: true`, with `editable: false`, with a live proposal, and after a landed review;
  - one complete variant and `can_reroll: false` → no arrows;
  - `arrowleft` steps; `arrowright` at the newest opens the reroll box and calls nothing; both listed in the `?` sheet; neither fires with the caret in the composer;
  - a trailing reply with no `response_id` (mocked scene) still shows the legacy arrows from `getAlternates`.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the new file plus `CampaignView.test.tsx CampaignView.shortcuts.test.tsx CampaignView.render.test.tsx` and `npm run typecheck` — PASS.
- [ ] **Step 5: Commit** `feat(play): swipe the last response`.

### Task 9: Provenance in the variants disclosure

**Files:**
- Modify: `frontend/src/components/ResponseControls.tsx`
- Test: `frontend/src/components/ResponseControls.test.tsx`

- [ ] **Step 1: Write the failing test** — a variant with `made_by: {model: "vendor/m", guidance: "Colder."}` renders `Model: vendor/m` and `Guided: Colder.` under its text; a variant without `made_by` renders neither.
- [ ] **Step 2: Run, verify FAIL.**
- [ ] **Step 3: Implement** — reuse `swipeTitle`'s line builder (export `madeByLines(madeBy, settings)` from `swipeTitle.ts` and have `swipeTitle` use it), one `<p className="subtle">` per line.
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Commit** `feat(play): show what made each variant`.

### Task 10: Gate and finish

- [ ] **Step 1:** `make check PY=$(pwd)/backend/.venv/bin/python` — all targets pass. If a ratchet reports an improvement or regression, fix or `make baseline` and commit the baseline with its cause.
- [ ] **Step 2:** Implementation → done gate: adversarial review of the branch diff (stand-in for `/codex:review`), resolve findings.
- [ ] **Step 3:** Done → actually done gate: adversarial review of the diff against the spec (stand-in for the final `/codex:adversarial-review`), resolve findings.
- [ ] **Step 4:** Update the PR description: what shipped, gate findings and resolutions.
