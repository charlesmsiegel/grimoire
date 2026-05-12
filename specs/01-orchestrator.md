# 01 — Orchestrator

## Purpose

The Orchestrator drives the turn loop for each campaign. It receives player input, decides whether to call the LLM now or wait (multi-PC advance), composes a deterministic prompt via the Context Builder, calls the LLM through the Gateway, parses the output via the Extractor, applies state deltas through the State Store, schedules side effects (image gen, time advancement, drift checks), and pushes streaming updates to the Frontend.

The Orchestrator owns the in-process event bus. It is the only module that calls the LLM directly during a turn.

## Responsibilities

- Receive post submissions from the Frontend
- Append posts to the active scene via Scene Manager
- Decide whether to auto-respond or wait (multi-PC advance trigger)
- Run the canonical turn sequence (scene break check → context build → mechanics pre-roll → LLM call → extract → apply deltas)
- Stream model output to the Frontend
- Schedule downstream work via the event bus (ImageGen, Continuity ticks, drift checks)
- Manage per-campaign concurrency (one turn at a time per campaign; multiple campaigns can be active)
- Handle errors and retries during the turn loop
- Coordinate undo, fork, retcon at the turn level

## Non-responsibilities

- Does not assemble prompts (Context Builder does)
- Does not parse model output structurally (Extractor does)
- Does not store state (State Store does)
- Does not own scenes or posts at the data level (Scene Manager does)
- Does not implement mechanics (Mechanics does)
- Does not own provider configuration (LLM Gateway does)

## Submit-post API

The Frontend calls:

```python
async def submit_post(
    self,
    campaign_id: str,
    pc_ref: str,                          # which PC is posting
    text: str,
    metadata: dict = {},
) -> SubmitResult:
    ...
```

The Orchestrator:

```
1. Validate: campaign exists, pc_ref is a registered PC for this campaign
2. Determine the active scene for this PC (from Characters module)
3. Build the post object (author_kind='pc', author_pc_ref=pc_ref, ...)
4. Append to scene via Scene Manager (writes the scene markdown file, updates SQLite)
5. Emit pc_post_appended event
6. Ask Scene Manager: should we auto-respond now?
   - If yes (1 PC in scene), continue with the turn (steps below)
   - If no (2+ PCs), return immediately; UI shows "waiting for advance"
```

## Advance API

When a multi-PC scene needs the system to respond, the Frontend calls:

```python
async def advance(self, campaign_id: str, scene_id: str) -> AdvanceResult:
    ...
```

The Orchestrator:

```
1. Validate: scene has pending PC posts since last advance
2. Mark the advance point (Scene Manager records last_advance_at_post)
3. Continue with the turn (steps below)
```

The single-PC auto-flow and the multi-PC advance flow converge at the same point: ready to run a turn.

## Canonical turn flow

Given a campaign, an active scene, and a triggering condition (auto-respond after single-PC post, or advance after multi-PC):

```
1. Acquire per-campaign turn lock.
2. Emit turn_started(turn_id, campaign_id, scene_id).
3. Ask Scene Manager: is_scene_break(player_input)?
   - If yes, close the current scene (or open a new one as configured),
     update scene refs for the next steps.
4. Ask Mechanics: evaluate_pre_roll(player_input, scene_context).
   - If proposed rolls: resolve them (or surface to user for confirmation depending on config).
   - Roll results are included in the prompt as authoritative.
5. Ask Context Builder: build(campaign_id, scene_id, mechanics_results).
   - Returns assembled prompt, source map, token budget.
6. Emit context_built(turn_id, prompt_metadata).
7. Call LLM Gateway: stream(task='main', request, campaign_id).
   - For each chunk: forward to Frontend via WebSocket, also accumulate.
8. On completion: emit model_response_received(turn_id, response).
9. Ask Extractor: extract(response, scene_context).
   - Returns list of state deltas with confidence scores.
   - Asks Mechanics: validate_narrated_event for each mechanical claim.
   - Asks Continuity: check for contradictions.
10. Emit deltas_extracted(turn_id, deltas).
11. For each delta:
    - High confidence: apply via State Store.
    - Low confidence: queue for review.
    - Mechanically invalid: surface warning.
12. Append model response as a new post via Scene Manager.
13. Emit turn_complete(turn_id, summary).
14. Release per-campaign turn lock.
15. Background work (fire-and-forget after the turn):
    - ImageGen: check should_illustrate; queue if yes.
    - Time Engine: advance clock if scene indicates time passage.
    - Continuity: update ledger.
    - Characters: drift check on present characters (sampled, not every turn).
```

## Concurrency model

One turn per campaign at a time. Multiple campaigns can be active simultaneously (separate locks). Within a single campaign:

- `submit_post` and `advance` both acquire the per-campaign turn lock
- If a turn is in progress, additional submits queue until the lock releases
- The Frontend shows "thinking..." with the queue position

Background work (image generation, drift checks, NPC ticks) does not hold the turn lock; they run after `turn_complete` is emitted.

## Multi-PC coordination

The Orchestrator delegates the auto-vs-wait decision to Scene Manager (which knows `present_pc_refs`). The Orchestrator itself doesn't decide based on PC count; it just acts on what Scene Manager returns.

The flow:

- PC A posts in scene X (1 PC present) → Scene Manager says auto-respond → Orchestrator runs the turn
- PC A posts in scene Y (2 PCs present, PC A and PC B) → Scene Manager says wait → Orchestrator returns, marks scene as "pending advance"
- PC B posts in scene Y → Scene Manager says wait (still 2 PCs, no advance yet) → Orchestrator returns
- User clicks Advance for scene Y → Orchestrator runs the turn, response addresses both PC inputs

The LLM prompt for a multi-PC advance includes both PC inputs in order, with author attribution; the model is instructed to address both.

## Streaming

The Orchestrator forwards model output as it streams. Per turn:

```python
async for chunk in self.llm_gateway.stream(task='main', request, campaign_id):
    await self.ws_push(campaign_id, {
        "type": "token",
        "turn_id": turn_id,
        "delta": chunk.delta,
    })
    accumulated += chunk.delta
```

Frontend renders tokens as they arrive. On stream end, the full text is processed by the Extractor.

## Scene break decisions

The Orchestrator calls `Scene Manager.is_scene_break(player_input)` before the LLM. Behavior depends on confidence:

- High confidence (≥0.8): close the current scene, open a new one with the suggested init, run the turn in the new scene
- Medium confidence (0.5-0.8): prompt the user — "this looks like a scene break, continue here or start a new scene?"
- Low confidence (<0.5): continue in the current scene

Scene break detection runs only on player inputs, not on Advance triggers (the multi-PC scene is staying coherent).

## Error handling

Per-step error handling:

- **Scene Manager fails (e.g., file write error)**: surface to user, retry once, then fail with rollback
- **Mechanics pre-roll throws**: log, skip pre-roll, continue (mechanics is optional context)
- **Context Builder throws**: this is fatal for the turn; surface error, do not proceed
- **LLM Gateway fails after retries and fallback**: surface error with the partial response if streaming
- **Extractor fails**: surface error, append the response without deltas, queue for manual review
- **State Store apply fails**: roll back the post append; require user retry

The turn is transactional at the post level: either the player's post + the model's response both end up in the scene, or neither does. The State Store provides the transaction primitive.

## Event bus

The Orchestrator owns an in-process event bus. Other modules subscribe.

```python
class EventBus(Protocol):
    def subscribe(self, event_type: str, handler: Callable) -> Subscription: ...
    async def emit(self, event: Event) -> None: ...
```

Core events:
- `turn_started`, `context_built`, `model_response_received`, `deltas_extracted`, `turn_complete`
- `scene_started`, `scene_ended`
- `pc_post_appended`, `advance_requested`, `advance_disabled`, `advance_enabled`
- `time_advanced`, `npc_tick_complete`
- `fact_recorded`, `commitment_created`, `commitment_paid_off`, `contradiction_detected`
- `drift_detected`
- `library_file_changed`, `library_indexed`, `entity_promoted`, `library_ref_upgraded`
- `image_ready`, `imagegen_job_queued`, `imagegen_job_failed`
- `plugin_loaded`, `plugin_health_changed`
- `review_item_added`, `review_item_resolved`

Subscribers:
- Frontend (WebSocket relay)
- Continuity
- Time Engine
- ImageGen
- Characters (drift check scheduling)
- Observability (everything is audited)

## Undo

```python
async def undo_turn(self, campaign_id: str, count: int = 1) -> UndoResult:
    ...
```

Pops the last N turns; reverses deltas in reverse order; updates the scene files; emits `turn_undone` events. Frontend rerenders. The delta log retains undone deltas marked as reversed.

## Retcon

```python
async def retcon_post(self, post_id: str, new_text: str) -> RetconResult:
    ...
```

Replaces a past post; reverses deltas sourced from it; re-runs Extractor on the new text; applies new deltas; flags downstream turns for review.

## Fork

```python
async def fork(self, campaign_id: str, from_turn_id: str, label: str) -> ForkResult:
    ...
```

Creates a new branch with the State Store's copy-on-write semantics. New branch is active; the user can switch back.

## Interface

```python
class Orchestrator(Protocol):
    # Turn flow
    async def submit_post(
        self,
        campaign_id: str,
        pc_ref: str,
        text: str,
        metadata: dict = {},
    ) -> SubmitResult: ...

    async def advance(self, campaign_id: str, scene_id: str) -> AdvanceResult: ...

    async def regenerate_last(self, campaign_id: str) -> RegenerateResult: ...

    # Editing past turns
    async def undo_turn(self, campaign_id: str, count: int = 1) -> UndoResult: ...
    async def retcon_post(self, post_id: str, new_text: str) -> RetconResult: ...
    async def fork(self, campaign_id: str, from_turn_id: str, label: str) -> ForkResult: ...

    # Status
    async def turn_in_progress(self, campaign_id: str) -> Optional[TurnStatus]: ...
    async def queue_length(self, campaign_id: str) -> int: ...

    # Event bus
    def event_bus(self) -> EventBus: ...
```

## Configuration

```yaml
orchestrator:
  per_campaign_concurrency: 1
  turn_timeout_seconds: 180
  stream_response: true

  scene_break:
    auto_threshold: 0.8
    prompt_threshold: 0.5

  pre_roll:
    confirm_before_executing: configurable    # 'always' | 'never' | 'high_stakes_only'

  multi_pc:
    advance_required: true

  background_work:
    drift_check_sampling: 0.25            # check 25% of turns
    npc_tick_after_each_turn: true

  errors:
    retry_extractor_on_parse_failure: 1
    surface_partial_response_on_llm_error: true
```

## Open questions (deferred)

- **Multi-campaign coordination.** A user has 3 campaigns active in different tabs; LLM provider rate-limited. Should the Orchestrator coordinate global pacing? v2.
- **Speculative execution.** Pre-fetch the next likely turn while the user is typing? Probably not worth the complexity.
- **Mid-stream cancellation.** User clicks cancel during streaming — abort the LLM, don't apply any deltas. Supported in the protocol; UI details in v1.
- **Long-running turns.** Some turns take 30s+ on local models. UX: keep alive heartbeats, allow background.
- **Concurrent advances.** Two scenes both pending advance in the same campaign — process sequentially via the turn lock. No parallel turns per campaign.
