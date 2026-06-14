# Orchestrator — Design (Shipped)

> Captures the Orchestrator design as actually built. The matching "remaining" spec at `2026-05-16-orchestrator-remaining-design.md` covers everything from the original `specs/01-orchestrator.md` that did **not** land in this work.

**Commit:** `86b035f` — "Build Orchestrator + turn loop (task 22)" (followed by `d56bc81`, `1a77719`, `e8cb59f`, `4b454aa`)
**Module:** `backend/src/grimoire/orchestrator/`
**Tests:** `backend/tests/orchestrator/test_service.py`

## Purpose

The Orchestrator drives the per-campaign turn loop. It receives player posts from the Frontend, decides whether to call the LLM now or wait for a multi-PC advance, runs the canonical turn (context build → optional mechanics pre-roll → LLM stream → extraction → delta application → response post), and owns the in-process event bus.

## Module surface

`OrchestratorService` (`orchestrator/service.py`) is constructed with duck-typed collaborators so tests can wire in fakes:

- `event_bus: EventBus` (in-process async fan-out; spec 01 §Event bus)
- `scene_manager: SceneManager`
- `llm_gateway`, `context_builder`, `extractor`, `state_store`
- `mechanics` (optional — `mechanics: null` campaigns just skip pre-roll)
- `ws_push` (optional `(campaign_id, msg) -> awaitable` for streaming chunks and lifecycle events)
- `extractor_config`, `config: OrchestratorConfig`
- `clock`, `rng` (injected for deterministic tests)

## Public API

```python
class OrchestratorService:
    # Turn flow
    async def submit_post(campaign_id, pc_ref, text, metadata=None) -> SubmitResult
    async def advance(campaign_id, scene_id) -> AdvanceResult

    # Editing past turns
    async def undo_turn(campaign_id, count=1) -> UndoResult
    async def retcon_post(post_id, new_text) -> RetconResult
    async def fork_campaign(...) -> ForkCampaignResult  # creates a sibling campaign

    # Status
    async def turn_in_progress(campaign_id) -> TurnStatus | None
    async def queue_length(campaign_id) -> int

    # Event bus
    def event_bus() -> EventBus
```

## Submit-post flow

1. Validate campaign exists (`UnknownCampaignError`) and the PC is registered (`UnknownPCError`)
2. Resolve the active scene for the PC via `scene_manager.active_scene_for_pc(...)`
3. Build a `Post(author_kind=PC, is_player=True, author_pc_ref=...)` and append via `scene_manager.append_post(...)`
4. Ask `scene_manager.on_post_submitted(...)`; if it returns `auto_respond=False` (multi-PC), return immediately with the decision reason
5. Otherwise run a turn and return `SubmitResult(accepted=True, turn_id=..., auto_responding=True, reason=...)`

## Advance flow

`scene_manager.on_advance_requested(scene_id)` returns the scene and the list of pending PC posts. The orchestrator concatenates them as `"[pc_ref] body\n\n[pc_ref] body"` to form a combined player input, then runs the turn. Returns `AdvanceResult` with the pending posts and turn id.

## Canonical turn (`_run_turn`)

Per-campaign state lives in a `_CampaignTurnState` (one `asyncio.Lock`, a `queued` counter, the current `_ActiveTurn`, and the `last_turn_id`).

1. Bump `queued`, acquire the per-campaign lock (`BaseException`-safe so `asyncio.CancelledError` during acquire still decrements the counter), decrement
2. Generate `turn_id = "t_" + uuid4()[:16]`, set `_ActiveTurn(stage="starting")`
3. Emit `turn_started`
4. **Scene break check** (`_maybe_break_scene`): only on player posts (skipped for advance triggers). If `is_scene_break.is_break` and `confidence >= scene_break.auto_threshold`: close the current scene, start a new one via `scene_manager.start_scene(...)`, continue in the new scene id. If below the auto threshold but still a break, emit `scene_break_suggested` and stay in the current scene.
5. `stage = "mechanics_pre_roll"` → `_do_pre_roll`. If no mechanics, returns `[]`. Otherwise calls `evaluate_pre_roll(campaign_id, player_input, ctx)`; if `pre_roll.confirm_before_executing == "always"` returns proposed-only with no resolution; otherwise resolves each via `mechanics.resolve_roll`. Mechanics errors are logged and produce an empty result list (mechanics is optional context).
6. `stage = "context_build"` → `context_builder.build(player_input, campaign_id, mechanics_results=..., pc_ref=...)` → emit `context_built` with budget usage
7. `stage = "streaming"` → `_stream_main_response`: build `CompletionRequest`, iterate `llm_gateway.stream(config.main_llm_task, request, campaign_id=...)`; for each chunk forward `{"type": "token", "turn_id", "delta"}` over `ws_push` and accumulate
8. Emit `model_response_received` with response length
9. `stage = "extracting"` → `_do_extract`: `extractor.extract(response_text, scene, campaign_id, snapshot)`; extractor exceptions are logged and produce `None` (turn still completes with no deltas). Emit `deltas_extracted` with the count
10. `stage = "applying"` → `_apply_routing`: `route_deltas(...)` from `extractor.routing` partitions into auto-apply, review, drop. For each:
    - `AUTO_APPLY` → `state_store.apply_delta(delta, source, turn_id, branch_id, campaign_id)`
    - `REVIEW` → `state_store.queue_for_review(...)` then emit `review_item_added` with the review id
    - `DROP` → ignored
    Per-delta exceptions are logged and skipped (turn does not abort)
11. Build the narrator response post (`author_kind=NARRATOR, is_player=False, turn_id=turn_id`) and append via `scene_manager.append_post`
12. Emit `turn_complete`, record `last_turn_id`
13. `finally:` clear `_ActiveTurn`, release the lock

## Concurrency

One turn per campaign at a time via `asyncio.Lock`. Multiple campaigns run independently. `queue_length(campaign_id)` reports waiters. `turn_in_progress(campaign_id)` returns the active `_ActiveTurn`'s `(turn_id, campaign_id, started_at, stage)`.

The event bus (`event_bus.py`) is a synchronous fan-out — `emit` awaits each subscriber in registration order. The previously held `asyncio.Lock` was removed in `e8cb59f` after documenting that callers rely on emit returning only after all subscribers have run.

## Undo / regenerate / retcon / fork-campaign

- `undo_turn(campaign_id, count)` walks the delta log for the most-recent `count` turns, reverses each turn's deltas LIFO via `state_store.reverse_delta`, emits a `turn_undone` event per turn with the reversed delta ids, then resets `last_turn_id` to whatever turn now sits on top. Returns `UndoResult(turns_undone, reversed_delta_ids, warnings)`. Empty campaigns raise `NoTurnsToUndoError`.
- `regenerate_last` was removed in the reroll consolidation (#512). Rerolls now go exclusively through the non-destructive per-post swipe/alternate path (`regenerate_post` → `RegeneratePostResult`); there is no destructive whole-turn re-run, and `RegenerateResult` / `_strip_response_for_turn` no longer exist.
- `retcon_post(post_id, new_text)` locates the post + scene, reverses any deltas attributed to the post's turn, calls `scene_manager.edit_post(..., source="retcon")` to overwrite the body on disk, re-runs the extractor on the new text (using the original turn id as the source), routes the new deltas through auto-apply / review, and returns the before/after texts plus delta ids. Downstream-turn flagging is reserved for a follow-up.
- `fork_campaign(...)` creates a sibling campaign rooted at a chosen turn. Within-campaign branching was removed in #494; `state_store.fork_branch` / `scene_manager.fork_scenes_for_branch` no longer exist.

## Streaming

`_stream_main_response` builds a `CompletionRequest(model="", messages=prompt.messages, max_tokens=prompt.params.max_tokens or 4096, temperature=prompt.params.temperature or 1.0)`. The empty `model` string lets gateway routing pick the model for the configured task (`config.main_llm_task`, default `"main"`). For each `chunk` from `gateway.stream`, non-empty `delta`s are accumulated and pushed over WebSocket as `{"type": "token", "turn_id", "delta"}`; `chunk.is_final` breaks the loop. `_push_to_ws` swallows push exceptions at `DEBUG` so a dead client never aborts a turn.

## Event emission

All lifecycle events go through `_emit_turn_event(type, turn_id, campaign_id, scene_id, **payload)` which emits on the bus **and** pushes the same shape to the WebSocket. Events the orchestrator emits today:

- `turn_started`, `context_built`, `model_response_received`, `deltas_extracted`, `turn_complete`
- `turn_undone` (one per undone turn, includes the reversed delta ids)
- `scene_break_suggested` (medium-confidence breaks; payload includes `confidence` and `reason`)
- `review_item_added` (per delta routed to review, includes `review_id` and `turn_id`)

Other event types in spec 01 are owned by other modules (`imagegen_job_failed` in ImageGen, `library_file_changed` / `library_indexed` in the watcher, etc.) — the orchestrator does not synthesize them.

## Configuration (`OrchestratorConfig`)

`orchestrator/config.py` provides dataclass configs with the spec 01 §Configuration shape:

```python
OrchestratorConfig(
    turn_timeout_seconds=180.0,         # config exists; not yet enforced in _run_turn
    main_llm_task="main",
    scene_break=SceneBreakConfig(auto_threshold=0.8, prompt_threshold=0.5),
    pre_roll=PreRollConfig(confirm_before_executing="never"),
    background_work=BackgroundWorkConfig(drift_check_sampling=0.25),
    errors=ErrorConfig(retry_extractor_on_parse_failure=1,
                       surface_partial_response_on_llm_error=True),
)
```

`errors` fields and `background_work.drift_check_sampling` are live; the
remaining behaviors they configure are deferred to the remaining-design spec.
(The never-read `per_campaign_concurrency`, `stream_response`, `multi_pc`, and
`background_work.npc_tick_after_each_turn` settings were removed in #593 — they
were exposed but had no consumer.)

## Error handling (as implemented)

- Mechanics pre-roll: any `Exception` from `evaluate_pre_roll` or `resolve_roll` is logged at WARNING and produces an empty result list (turn proceeds)
- Scene break: `is_scene_break` exceptions are swallowed (assume "not a break")
- Scene close on auto-break: `WARNING`-logged on failure, turn continues in the proposed new scene
- Extractor: `extract` exceptions are logged at WARNING and produce `None`; the turn still appends the narrator response and emits `turn_complete`
- Delta apply: per-delta exceptions are logged and skipped
- WebSocket push: exceptions are logged at DEBUG and swallowed

## Test wiring

`OrchestratorService` accepts `Any` for most collaborators specifically so `backend/tests/orchestrator/test_service.py` can wire in fakes for any subset. Scene-manager interop uses two adapter helpers (`_pydantic_scene`, `_pydantic_post`) at the bottom of `service.py` to bridge between the scene-manager dataclasses and the pydantic models the Extractor and Mechanics expect.
