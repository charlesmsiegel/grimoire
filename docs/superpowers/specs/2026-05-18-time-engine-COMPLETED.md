# Time Engine — Remaining Work (COMPLETED 2026-05-18)

> Everything from the original `specs/07-time-engine.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-time-engine-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-time-engine-design.md`
**Module:** `backend/src/grimoire/time_engine/`

## Status

All actionable items implemented on `claude/implement-time-engine-spec-Q1vHK`:

- §1 — `TimeEngineSubscriber` + orchestrator `turn_complete` wiring (with `time_advances` payload).
- §2 — Shared inter-NPC events pre-pass with injectable `shared_events_fn` and `SharedEvent` type.
- §3 — `subscribe_calendar` thin wrapper around `event_bus.subscribe("time_advance", …)`.
- §4 — Household-based significance via `Character.household_id`.
- §5 — Faction tick depth: resource decay, library-side leader actions, inter-faction conflict pass; surfaced via `FactionConflict` and the existing `FactionTickSummary` fields.
- §6 — `scheduled_event_pre_notice` emits `scheduled_event_imminent`; `pre_notice_emitted_at` column on `scheduled_events` (migration `020_time_engine_extensions.sql`) keeps it once-per-event.
- §7 — `advance(..., activity_ref=...)` threads through to `mechanics.time_tick` via `TickContext.extras["activity_ref"]`.
- §8 — `propose_advance` issues a `CheckpointSuggestion` + token, emits `time_advance_checkpoint_suggested` over the configured threshold; `advance(..., checkpoint_token=...)` consumes the token.
- §9 — Optional drift check callable; warnings on `TimeAdvanceResult.drift_warnings`; `npc_drift_detected` event per warning.
- §10 — `TimeEngineConfig.precision` quantizes both ends of every advance to the configured granularity (`minute` | `hour` | `day` | `season`).

Deferred / dropped per the original spec ordering:

- §11 — Frontend digest display path (engine side already done).
- §12, §13 — v2 deferred.
- §14 — Rejected; orchestrator continues to own the apply path.

## 1. Orchestrator wiring — advance on `turn_complete`

Spec 07 §Triggers for time advancement: "The Orchestrator's turn loop checks for time advancement after extraction. If the Extractor reports a `time_advances` delta, the Time Engine processes it."

Today the Time Engine has no orchestrator subscriber. `TimeEngineService` is constructed in `backend/src/grimoire/main.py:187` and reachable from the HTTP layer (`POST /campaigns/{campaign_id}/time/advance` in `api/campaigns.py:843`), but no code path turns "the extractor noticed a week passed" into an `advance(...)` call. This is the same gap orchestrator-remaining §1 calls out from its side — the engine half is already there, only the subscriber is missing.

Shape: at startup wiring, subscribe a small adapter to the orchestrator's `turn_complete` event. The adapter pulls the `time_advances` info off the extractor result (delta shape needs nailing down — currently the extractor doesn't have a typed time-delta), maps it to a `Duration` + `TimeAdvanceReason.SCENE_NARRATION`, and calls `TimeEngineService.advance(...)` with `scene_id` from the event payload. Coordinate with orchestrator §1 to land both subscribers in the same plan.

## 2. Shared inter-NPC events pre-pass

Spec 07 §Resolution coherence: "When two NPCs interact during a tick (e.g., 'winifred and vivienne spent the week planning a party'), the engine produces a single shared interaction record visible to both ticks… before running individual ticks, run a 'shared events' pass that produces inter-NPC events for the period. Then individual ticks reference these."

Today `_run_npc_ticks` (`service.py:645`) runs every tick independently behind an `asyncio.Semaphore` and the shipped design notes "there is no 'shared events' pre-pass". This is the main known coherence risk for multi-NPC skips.

Needed:
- A `_shared_events(present, from_time, to_time) -> list[SharedEvent]` step before `_run_npc_ticks` (one LLM call seeded with the full ticked-NPC list)
- Augment `NpcTickInput` with `shared_events: list[SharedEvent]` filtered to the events involving that NPC
- A `SharedEvent` shape on `types/time.py` (participants, summary, in_game_at)
- Surface shared events on `TimeAdvanceResult` so the digest can mention them

The "ticks for related NPCs run sequentially with prior results in context" half of the spec is a different (weaker) mitigation — pick one. The shared-events pass is the better answer and supersedes it.

## 3. `subscribe_calendar` API

Spec 07 §Interface includes `def subscribe_calendar(self, handler: Callable) -> SubscriptionId: ...`. The protocol definition is still in `types/protocols.py:907` but `TimeEngineService` does not implement it.

The current `event_bus` injection already covers most subscriber use cases — `time_advance` is emitted on the bus — so this may be **redundant**. Decision needed: drop from the spec, or build a thin convenience wrapper around `event_bus.subscribe("time_advance", handler)` that returns a subscription id. Lean toward dropping.

## 4. Household-based significance

`SignificanceConfig.tick_in_household: bool = True` is a config field with no consumer (`service.py:614` only checks role / commitment / recent-post).

Needs: a notion of "household" on the character or world data. Today neither `CharacterData` nor `ResolvedCharacter` carries a household-membership field. Either:
- Add a `household_id` to character cards + a "PC's household" lookup, and extend `_significant_npcs` to include NPCs whose `household_id` matches any PC's
- Or reinterpret "household" as "same location as the PC" and tick anyone whose `location_ref` matches the PC's current location

The second is cheaper and uses fields that already exist (`ResolvedCharacter.current_state.location_ref`). Decide before building.

## 5. Faction tick depth

Spec 07 §Faction ticks lists four concerns: goal progress, resource changes, leader actions, conflicts with other factions. The shipped `_run_faction_ticks` only does goal progress, and only as a hardcoded `+0.01 * months` bump per goal (`service.py:725`). `FactionTickSummary.resource_changes` / `.notable_actions` / inter-faction conflict are always empty.

A real implementation would:
- Read faction resources from `faction_state.state["resources"]` and update them based on a slow-decay / leader-action model
- Treat each faction's `leader_ref` as eligible for a tick that produces "this is what the leader did" entries on `notable_actions`
- Add an inter-faction pass (analogous to §2's shared-events pass but at the faction layer) that resolves conflicts when two factions have intersecting goals

This is mostly content-design work, not engineering — keep the engineering surface minimal (add resource/leader state to `faction_state.state` and surface the existing summary fields).

## 6. Wire `scheduled_event_pre_notice`

`TimeEngineConfig.scheduled_event_pre_notice: timedelta = 7d` exists with no consumer. Spec 07 §Configuration calls it out as "warn user 1 week before scheduled events".

Wire: after `_run_pipeline` (or as part of it), check `upcoming_events(within=scheduled_event_pre_notice)` and emit a `scheduled_event_imminent` event per upcoming event whose time falls in `(to_time, to_time + pre_notice]` and that hasn't already been warned about. Persist a `pre_notice_emitted_at` column on `scheduled_events` (small migration) so we don't re-warn on every subsequent advance.

## 7. Activity-based advancement UX

Spec 07 §Activity-based advancement defines the player-facing flow: "I want to train sword for the next two months" → UI offers a "Advance 2 months and resolve training?" button → Time Engine runs.

The engine already supports this via `advance(reason=TimeAdvanceReason.ACTIVITY_DURATION)`, but there is no UI surface that proposes it and no Mechanics ↔ Time Engine handshake that turns "this is a long activity" into a "skip to its end" proposal. Likely lives partly in Mechanics (which knows activity durations) and partly in the Frontend (which renders the prompt). The engine-side gap is whether `advance` should accept activity metadata that mechanics can use to "resolve training" cleanly — today `mechanics.time_tick` is called per character with just the duration, with no link back to the originating activity.

Suggested addition: an optional `activity_ref: str | None` kwarg on `advance` that gets threaded through to `mechanics.time_tick`'s context, so a mechanic can resolve a specific outstanding activity rather than re-deriving it from sheet state.

## 8. Reversibility checkpointing

Spec 07 §Open questions: "Advancing time creates a lot of deltas. Undo is supported but expensive. Should there be a 'checkpoint before advance' prompt for big skips? Probably yes."

Needs:
- A configurable threshold (`config.checkpoint_threshold: timedelta`, default e.g. 7 days) above which the engine emits `time_advance_checkpoint_suggested` before running, with the projected duration in the payload
- Frontend round-trip to confirm or branch via `state_store.fork_branch` first
- Either a synchronous "wait for confirmation" path on `advance` (probably not — keeps the API pure) or a separate `propose_advance(...)` method that returns a token the UI exchanges for a real `advance(...)` call

## 9. NPC consistency drift check

Spec 07 §Open questions: "NPC consistency over many ticks. Drift accumulates. A drift check post-tick that flags wild deviations would help."

Post-tick, for each NPC summary, run a cheap LLM/heuristic check that compares the summary's `state_at_end`, `activities`, `relationships_changed` against the NPC's card and prior facts. Flag wild deviations onto a new field (`TimeAdvanceResult.drift_warnings: list[DriftWarning]`) and emit a `npc_drift_detected` event. This is closely related to the orchestrator's per-post drift check (orchestrator-remaining §1) and should probably share an implementation.

## 10. Configurable time precision per campaign

Spec 07 §Open questions: "Time precision. Do we track to the hour? The day? Configurable per campaign — some sagas care about minutes (heists), some care about seasons (Ars Magica)."

Today the engine uses raw `datetime` everywhere and quantizes nothing. A `TimeEngineConfig.precision: Literal["minute", "hour", "day", "season"]` (or campaign-level) would let `advance(...)` round both `from_time` and `to_time`, and let the digest render at the appropriate granularity. Likely also informs `faction_tick_resolution` defaults (a "season" campaign probably wants quarterly faction ticks, not monthly).

## 11. Narrative-digest display path

Spec 07 §Digest generation: "The narrative digest is shown when the player returns to the campaign post-advancement, before the next scene starts."

The engine produces `TimeAdvanceResult.digest` and the HTTP endpoint serializes it, but no Frontend surface displays it. Out of scope for the engine itself but worth recording so the work doesn't get rebuilt as an engine concern — when the UI plan picks this up, the engine side is already done.

## 12. In-fiction time vs. real time (v2; deferred)

Spec 07 §Open questions: "Some scenes happen in seconds of in-game time but take thousands of words to narrate. Do we track narration-time-budgets separately? Probably not for v1." Not in scope; record here so it doesn't get re-litigated.

## 13. Cross-character / multi-POV time skips (v2; deferred)

Spec 07 §Open questions: "If the PC is unconscious and the campaign continues from an NPC POV, who is the Time Engine following? Multi-POV is out of scope for v1." Treat as **deferred to v2**; the per-PC active-scene model in `SceneManager` is the prerequisite work and is not yet in shape for this.

## 14. Mechanics-deltas auto-apply (rejected)

The Time Engine returns `mechanics_deltas` on `TimeAdvanceResult` but does not apply them to the store; callers do. There was a tempting design alternative where the engine applies them directly via `state_store.apply_delta`. Rejected because (a) callers need to attribute deltas to the right `source` (turn id vs. time-advance id vs. activity id) and the engine doesn't know which, and (b) the Orchestrator already owns the apply path with rollback semantics (orchestrator-remaining §7). Treat as **rejected** unless a clear ownership argument emerges otherwise.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §1 — orchestrator subscriber + extractor `time_advances` delta shape. Unblocks the engine actually firing from gameplay. Coordinate with orchestrator-remaining §1.
2. §2 — shared inter-NPC events pre-pass. Biggest correctness win once §1 makes the engine load-bearing.
3. §7 + §4 — activity-based advancement plumbing and household significance; both are small extensions of the existing pipeline.
4. §6 — `scheduled_event_pre_notice` wiring. Small, standalone, user-visible.
5. §8 + §9 — checkpointing and drift check. Share a "before/after advance" extension surface.
6. §5 — faction tick depth. Mostly content/data; do it once the engine surface stabilizes.
7. §10 — configurable precision. Cross-cuts a lot of the above; doing it earlier means re-touching everything.
8. §3 — decide whether to drop `subscribe_calendar` or wrap the event bus. Cheap.
9. §11 — coordinate with the UI plan; engine side is already done.
