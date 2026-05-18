# Orchestrator — Remaining Work

> Everything from the original `specs/01-orchestrator.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-orchestrator-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-orchestrator-design.md`
**Module:** `backend/src/grimoire/orchestrator/`

## 1. Background-work fan-out after `turn_complete`

Spec 01 §Canonical turn flow step 15 calls for fire-and-forget side effects after `turn_complete` is emitted:

- `ImageGen`: check `should_illustrate`; queue if yes
- `Time Engine`: advance the clock if the turn indicates time passage
- `Continuity`: update the ledger (facts, commitments, foreshadowing)
- `Characters`: drift check on present characters, sampled at `config.background_work.drift_check_sampling` (default 0.25)
- `Characters` / world: NPC tick if `config.background_work.npc_tick_after_each_turn`

Today `_run_turn` ends at step 12 (response post + `turn_complete`) and never schedules these. The `BackgroundWorkConfig` dataclass already exists; only the wiring is missing.

Likely shape: subscribe each downstream module to `turn_complete` on the event bus rather than have the orchestrator call them directly — the bus is already in place and modules already exist (`time_engine/`, `imagegen/`, `continuity/`, `characters/`). The orchestrator's job here is to confirm those modules subscribe at startup wiring time (currently in `backend/src/grimoire/main.py` and the `_create_app` factory).

## 2. Mid-stream cancellation

Spec 01 §Open questions notes "Mid-stream cancellation. User clicks cancel during streaming — abort the LLM, don't apply any deltas. Supported in the protocol; UI details in v1."

Today there is no cancel path:
- `_run_turn` holds the lock end-to-end with no cancellation surface
- `_stream_main_response` iterates the gateway stream with no cooperative checkpoint
- The WebSocket protocol does not define a cancel message

Design needed: a `cancel_turn(campaign_id, turn_id)` orchestrator method, a WebSocket inbound `{"type": "cancel_turn", "turn_id"}`, cooperative cancellation in `_stream_main_response` (likely via an `asyncio.Event` on `_ActiveTurn`), and an explicit "do not extract / do not apply deltas / do not append narrator post" path when cancelled. Emit `turn_cancelled` instead of `turn_complete`.

## 3. Turn-timeout enforcement

`OrchestratorConfig.turn_timeout_seconds` (default 180.0) exists but is not enforced anywhere. `_run_turn` should wrap its body in `asyncio.wait_for(..., timeout=config.turn_timeout_seconds)` (or a similar pattern that respects cooperative cancellation from §2 above) and emit `turn_timed_out` on the bus before re-raising or returning.

## 4. Long-running turn heartbeats

Spec 01 §Open questions: "Some turns take 30s+ on local models. UX: keep alive heartbeats, allow background."

While streaming, push periodic `{"type": "heartbeat", "turn_id"}` frames at a configurable interval (default ~10s) so the Frontend can distinguish "still working" from "connection died". Pair with a `last_chunk_at` timestamp on `_ActiveTurn` for diagnostic purposes.

## 5. Scene-break medium-confidence interactive prompt

Spec 01 §Scene break decisions defines three bands:
- High (`>= scene_break.auto_threshold`, default 0.8) — auto-close, auto-open. **Shipped.**
- Medium (`scene_break.prompt_threshold .. auto_threshold`, default 0.5..0.8) — "prompt the user — continue here or start a new scene?". **Today emits `scene_break_suggested` with no return path.**
- Low (`< prompt_threshold`) — continue silently. **Shipped (implicit).**

Needs: a return channel from the Frontend (`Orchestrator.resolve_scene_break(campaign_id, turn_id, choice)` plus a UI prompt), and a way to pause the turn between scene-break detection and context build while waiting for the user's choice. Today the orchestrator never waits — it just continues in the current scene.

## 6. Transactional post / response semantics

Spec 01 §Error handling: "The turn is transactional at the post level: either the player's post + the model's response both end up in the scene, or neither does. The State Store provides the transaction primitive."

Today `submit_post` appends the player post **before** running the turn. If the turn fails partway (LLM error, extractor crash, delta apply error), the player post stays on disk and the narrator response never appears. Need either:

- (a) Defer the player-post append until after the turn succeeds, buffering it in memory while running
- (b) Use a State Store transaction that brackets the whole turn and rolls back the post on failure
- (c) Mark the orphan post with a `pending_response: true` flag and surface a retry affordance

Pick one, then update `_run_turn`'s `finally:` accordingly.

## 7. State Store apply-failure rollback

Tied to §6. Spec 01: "State Store apply fails: roll back the post append; require user retry." Today per-delta exceptions in `_apply_routing` are logged and skipped; there is no rollback. Define what "atomic delta batch" means at the State Store interface and have `_apply_routing` either commit-all or revert.

## 8. LLM gateway error surfacing

Spec 01 §Error handling: "LLM Gateway fails after retries and fallback: surface error with the partial response if streaming." Today gateway exceptions inside `_stream_main_response` propagate uncaught — they unwind through `_run_turn`'s `finally:` (which releases the lock and clears `_ActiveTurn`) and bubble back to the caller without emitting a terminal event or pushing anything user-visible.

Catch in `_run_turn` after streaming, emit `turn_failed(reason="llm_gateway", partial_response=...)`, and decide whether to still call the extractor on the partial (`errors.surface_partial_response_on_llm_error` is the relevant config knob).

## 9. Extractor retry on parse failure

`ErrorConfig.retry_extractor_on_parse_failure: 1` is configured but `_do_extract` does not retry — one exception, log, return `None`. Add a single retry on parse-shaped exceptions (need the extractor to expose a parse-failure subclass).

## 10. Missing event emissions the orchestrator owns

Spec 01 §Event bus lists events the orchestrator should originate that today are silent:

- `pc_post_appended` — emit from `submit_post` right after `scene_manager.append_post(...)`
- `advance_requested` — emit from `advance(...)` before running the turn
- `advance_disabled` / `advance_enabled` — likely Scene-Manager-owned; confirm where these belong
- `scene_started` / `scene_ended` — Scene Manager owns the data, but the orchestrator triggers them via `_maybe_break_scene`; either Scene Manager emits or the orchestrator does after a successful close/start

The others in the spec event list (`time_advanced`, `npc_tick_complete`, `fact_recorded`, `commitment_*`, `contradiction_detected`, `drift_detected`, `library_*`, `entity_promoted`, `plugin_*`, `image_ready`, `imagegen_job_queued`) all originate in other modules — confirm during §1 wiring that they actually fire.

## 11. `retcon_post` downstream flagging

`RetconResult.downstream_flagged_turns` is currently always `[]`. Spec 01 §Retcon: "flags downstream turns for review." Walk the turns after the retconned post's turn and surface any whose facts/commitments depended on text we just rewrote. Likely needs a query against the delta log filtered by source post id (which the log already records).

## 12. Multi-campaign coordination (v2; explicitly deferred)

Spec 01 §Open questions notes a v2 concern: global LLM rate-limit pacing when a user has many campaigns open. Not in scope; record here so it doesn't get re-litigated during the next pass.

## 13. Speculative execution (rejected)

Spec 01 §Open questions: pre-fetch the next turn while the user types. Marked "probably not worth the complexity" in the original. Treat as **rejected** unless evidence emerges otherwise; do not add to a plan without re-brainstorming.

---

## Suggested plan ordering

If picking this up, a reasonable order:
1. §6 + §7 + §8 + §9 together — finishes the error/rollback story end-to-end
2. §1 — turn the existing modules into actual subscribers; this is mostly wiring + tests
3. §10 — fill the event gaps once everything is wired
4. §2 + §3 + §4 — cancellation, timeout, heartbeats; share an `_ActiveTurn` cancellation/heartbeat surface
5. §5 — scene-break interactive prompt (needs a Frontend round-trip; coordinate with the UI plan)
6. §11 — retcon downstream flagging (needs Continuity cooperation)
