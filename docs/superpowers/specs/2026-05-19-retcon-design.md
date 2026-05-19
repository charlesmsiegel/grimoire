## Retcon — Design

> **Status:** Design ready for implementation plan. Depends on `swipes-alternates-design.md` (replayed turns produce alternates via `delta_set_id`) and the existing `orchestrator.retcon_post` (leave-as-is variant). Soft dep: `auxiliary-tasks-design.md` for the inline `rewrite_post` editor.

**Source idea:** `specs/new/retcon.md`
**Module:** `backend/src/grimoire/orchestrator/`, `backend/src/grimoire/continuity/`

## Purpose

A retcon is a deliberate edit of an *earlier* post. The current `orchestrator.retcon_post` (`backend/src/grimoire/orchestrator/service.py:376–472`) implements the "leave-as-is" variant: rewind the post's deltas, apply new deltas from re-extracting the new text, let Continuity surface contradictions in downstream turns. This spec adds the **replay** variant (re-run each subsequent canonical turn with the retconned post as context), the dedicated full-screen UX, the auto-fork nudge, and the surfaced contradiction list.

## Two flows

### Leave-as-is (already shipped)

User accepts inconsistencies; State Store rewinds original deltas + applies new ones (`_reverse_turn_deltas` + `_apply_routing`). Continuity flags downstream turns that touch the same targets via `downstream_flagged_turns`. The existing path stays — this spec only formalizes its API surface and audit.

### Replay (new)

System re-runs each subsequent canonical turn from the retcon point, using the retconned post and any earlier turns as context. Each replayed turn produces a **new alternate** on its post (per `swipes-alternates`). The original alternate is preserved. User reviews each one with Accept / Try again / Cancel.

## Flow

1. User selects a post and chooses "Retcon...".
2. Inline editor — edit prose directly, or invoke `auxiliary-tasks.rewrite_post`.
3. User accepts the retconned text.
4. Prompt: **Leave following turns** or **Replay**?
5. Leave path: rewind old deltas, apply new deltas, surface contradictions (existing).
6. Replay path: enter "retcon replay mode" — Orchestrator generates a new alternate for each following turn; user reviews each with Accept / Try again / Cancel.

## Replay-mode state machine

```
              ┌──────────────────────────────────────────┐
              │ Retcon initiated                         │
              └─────┬────────────────────────────────────┘
                    ▼
            ┌──────────────────┐
            │ Edit text & save │
            └─────┬────────────┘
                  ▼
        ┌───────────────────────┐
        │ Decision: Leave/Replay│
        └─────┬─────────────────┘
        Leave │            Replay
              ▼                  ▼
         ┌────────┐    ┌─────────────────────────┐
         │ Done   │    │ Start replay batch      │
         └────────┘    │ batch_id = rb_<uuid>    │
                       └─────┬───────────────────┘
                             ▼
              ┌──────────────────────────────────┐
              │ For each subsequent post i:      │
              │   1. Generate alternate (regen)  │
              │   2. Stream to user              │
              │   3. Wait for Accept/Try/Cancel  │
              │   4a. Accept → switch_primary    │
              │   4b. Try    → regenerate again  │
              │   4c. Cancel → finalize at i     │
              └──────────────────────────────────┘
```

The orchestration of replay lives in a new `orchestrator/retcon_replay.py` module (kept separate from the canonical turn loop). It reuses the canonical-turn helpers — context builder, LLM call, extractor — but injects the retconned post as the "anchor" before context assembly (see below).

## Replay context injection

This is the central new piece: how do we get the Context Builder to see the **retconned** version of post N when generating alternate N+1?

The cleanest answer is **post identity preservation + primary alternate switch**:

1. The retconned post keeps its `post_id`. The new text becomes a new `Alternate` on that post, promoted to primary. (This uses the swipes-alternates machinery — `state_store.swap_delta_set` rewinds the original deltas and applies the new deltas atomically.)
2. The Context Builder reads from the scene's primaries. By the time replay starts on post N+1, post N's primary IS the retconned version. No special context-builder mode needed.
3. For each replayed post i (i = N+1 .. end): `regenerate_post(post_id=i)` (per swipes spec) does the right thing — uses the now-current scene context (including retconned post N) and generates a new alternate. The new alternate is **not** auto-primary; user reviews via the replay UI; on Accept, `switch_primary_alternate(post_id=i, alternate_id=new)` promotes it.

The replay batch is tracked via `replay_batch_id` stored in the alternate's `extra` sidecar field. Each alternate in the same replay points at the same batch id so audit + the UI can group them. The batch id also enables cancel-by-batch (see partial commit).

This avoids needing a "context injection" parameter on the Context Builder at all — the existing assembly logic naturally produces the right context once the primary on post N points at the retconned text. **No context-builder protocol change** is required.

## Partial commit on cancel

Spec: cancellation at post i leaves "leave-as-is up to post i — rewound and reapplied deltas up to the cancel point, rest of the timeline retained."

Implementation:

- Posts N..i-1 have had their primary switched to the new alternates (user accepted them); their state is committed.
- Post i is mid-generation when user cancels: the in-flight alternate (whose deltas may have been pre-applied per the swipes design) is rolled back via `state_store.rewind_delta_set(in_flight.delta_set_id)`. The alternate row in the sidecar is removed.
- Posts i+1..end retain their **original** alternates as primary. Continuity's contradiction detection will surface inconsistencies the user can address later.

The state is exactly the "leave-as-is" outcome from post i forward. No mid-retcon undo; cancel is a clean finalization.

## Contradiction gating (when does it run?)

Per the open question: **after each replayed post's Accept**, not in a batch at end. Rationale: the user is reviewing each turn anyway; surfacing the contradictions specific to that turn alongside the Accept/Try buttons lets them course-correct mid-replay (e.g., choose "Try again" if the new alternate creates a contradiction).

The replay UI shows per-post contradiction badges; the user can expand to see Continuity's full report. The existing `ContinuityService.check_contradictions` path (`backend/src/grimoire/continuity/service.py:337–375`) is called as part of the normal extractor-routing pipeline already, so no new contradiction code is needed — just surfacing.

For leave-as-is, contradictions are surfaced after the single rewind+apply step (same flow as today).

## Auto-fork nudge

When the user retcons with replay and the count of following turns ≥ 5, show:

> This is a substantial change (will replay 7 turns). Would you like to fork the campaign first, retcon on the fork, and then decide whether to promote the fork?
>
> [Fork & retcon there]  [Retcon here]  [Cancel]

The nudge appears at step 4 (the Leave/Replay decision), inserted as a third option when the count ≥ 5. Defers to `fork-design.md` for the fork flow. If user picks "Fork & retcon there," the system runs `fork_campaign(...)` first, switches to the fork, then re-opens the retcon dialog on the equivalent post in the fork.

## Backend surface

```python
async def retcon_post(
    campaign_id: str,
    post_id: str,
    new_text: str,
    *,
    replay_subsequent: bool = False,
) -> RetconResult: ...
```

`RetconResult` (extended from current shape):

```python
@dataclass
class RetconResult:
    edited_post_id: str
    reversed_delta_ids: list[str]      # current field
    new_delta_ids: list[str]           # current field
    downstream_flagged_turns: list[str] # current field
    # New for replay:
    replay_batch_id: Optional[str]
    replayed_post_ids: list[str]       # those whose alternates were committed
    cancelled_at_post_id: Optional[str]
    contradictions_detected: list[ContradictionReportRef]  # ids; client fetches full reports
```

Backwards compat: old fields kept exactly. New fields default to `None` / `[]` for the leave-as-is path.

REST routes:
```
POST   /campaigns/{id}/turns/{turn_id}/retcon          # existing (extended payload)
POST   /campaigns/{id}/retcon/replay/{batch_id}/accept      # accept alternate for the current post
POST   /campaigns/{id}/retcon/replay/{batch_id}/try-again   # regenerate alternate for the current post
POST   /campaigns/{id}/retcon/replay/{batch_id}/cancel      # finalize the batch at the current post
GET    /campaigns/{id}/retcon/replay/{batch_id}             # poll current state (post index, contradictions)
```

WebSocket events:
```json
{ "type": "retcon_started",       "post_id": "...", "replay_subsequent": true, "batch_id": "rb_..." }
{ "type": "retcon_post_replayed", "post_id": "...", "new_alternate_id": "...", "batch_id": "rb_..." }
{ "type": "retcon_post_accepted", "post_id": "...", "alternate_id": "...", "batch_id": "rb_..." }
{ "type": "retcon_cancelled",     "batch_id": "rb_...", "cancelled_at_post_id": "..." }
{ "type": "retcon_complete",      "batch_id": "rb_...", "post_id": "..." }
```

`EventType` enum in `backend/src/grimoire/types/orchestrator.py` gains `RETCON_STARTED`, `RETCON_POST_REPLAYED`, `RETCON_POST_ACCEPTED`, `RETCON_CANCELLED`, `RETCON_COMPLETE`.

## Idempotency

Two retcons on the same post in sequence: the **second** retcon operates on the current primary alternate of that post (which was set by the first retcon). Each retcon creates a new alternate and the swap rewinds whatever the current primary's delta set is. No special idempotency layer; the alternate model gives us natural history (every retcon adds a new alternate).

Concurrent retcons on the same post: rejected at the API layer with `409 RETCON_INFLIGHT` (one active batch per campaign at a time — enforced via `orchestrator._active_retcon_batches[campaign_id]`).

## "Try again" semantics

In replay mode, "Try again" on post i regenerates a fresh alternate (same prompt, different sample). The replaced alternate is dropped (not pinned, not in the audit kept beyond a brief retention) — otherwise five "Try agains" on every post would balloon the alternates list. **Counter-decision:** keep them; reuse the existing `auto_purge_older_than_days` retention. Users may want to compare. The frontend just shows the most recent until accepted.

The orchestrator behavior is the same as `regenerate_post` (per swipes spec); the only retcon-specific addition is tagging with `replay_batch_id`.

## Audit log

```
[retcon-start]  campaign=... post=p_4708 replay=true batch=rb_a1b2
[retcon-edit]   campaign=... post=p_4708 reversed_deltas=14 new_deltas=11
[retcon-replay] campaign=... post=p_4709 new_alt=a_9101 batch=rb_a1b2 contradictions=0
[retcon-accept] campaign=... post=p_4709 alt=a_9101 batch=rb_a1b2
[retcon-replay] campaign=... post=p_4710 new_alt=a_9102 batch=rb_a1b2 contradictions=1
[retcon-cancel] campaign=... batch=rb_a1b2 stopped_at=p_4710
```

## Cross-spec hooks

- **`swipes-alternates`** — every replayed turn produces a new alternate via the `regenerate_post` primitive; accepting on the replay UI calls `switch_primary_alternate`. The `replay_batch_id` lives in `Alternate.extra`. **Hard dependency: this spec lands after swipes-alternates.**
- **`auxiliary-tasks.rewrite_post`** — the inline retcon editor offers "Use AI to rewrite" which invokes the `rewrite_post` auxiliary task; the accepted output is the `new_text` argument to `retcon_post`. **Soft dependency** — retcon ships without it (manual edit only) and the auxiliary task hooks in later.
- **`fork`** — auto-fork nudge defers to fork's API. Order doesn't matter; both can ship independently as long as the nudge can detect whether fork is available.
- **Continuity** — contradiction detection runs on each replayed accept (existing call path).

## Performance

- Leave-as-is retcon: rewind + apply one delta set → < 100 ms.
- Replay per-post: one canonical-turn cost. A 5-post replay is roughly 5× a turn, with user-paced reviews between each.
- Replay contradiction check: existing path, < 500 ms per post (LLM judge on candidates).

## Failure handling

| Failure | Behavior |
|---|---|
| Retcon edit causes extractor error | Roll back the rewind via `state_store.rewind_delta_set` of the (incomplete) new set; restore original primary; surface error |
| Replay-mode generation fails mid-stream | Drop in-flight alternate; surface error; user can Try again |
| User closes browser mid-replay | Batch remains "open" server-side; on reconnect, frontend re-syncs via `GET /retcon/replay/{batch_id}` and resumes at the right post |
| Contradiction-check timeout | Render replay accept buttons with a "contradiction check pending" badge; non-blocking |
| `switch_primary_alternate` fails on accept | Atomic rollback inside swipes' `swap_delta_set`; show error; user re-accepts |

## Test wiring

`backend/tests/orchestrator/test_retcon.py` (extend existing):
- Leave-as-is: existing tests retained; add coverage for `replayed_post_ids=[]` and `cancelled_at_post_id=None` in `RetconResult`.
- Replay: 3-post replay, user accepts all → all primaries switched, contradictions surfaced as expected.
- Replay: cancel at post 2 → posts 1 and 2 accepted, posts 3-5 retain originals, in-flight rolled back cleanly.
- Replay: Try again twice then Accept on the same post → multiple alternates created, accepted one is primary.
- Idempotency: retcon post P, then retcon P again → two retcon edits visible in alternate history.
- Concurrent: second retcon on active batch → 409.

`backend/tests/orchestrator/test_retcon_replay.py` (new):
- Context injection sanity: alternate N+1 generated after retconning N sees N's new text in `AssembledPrompt` (assert message contents include the edit).

## Wiring touchpoints

- `orchestrator/service.py:376–472`: `retcon_post` accepts `replay_subsequent`; leave-as-is path retained; replay path delegates to `retcon_replay.RetconReplaySession`.
- `orchestrator/retcon_replay.py` (new): batch state machine, per-post regenerate + accept/try/cancel handlers, WS event emission, contradiction surfacing.
- `types/orchestrator.py`: extended `RetconResult`; new `EventType` entries.
- `api/campaigns.py`: extended payload on existing route + 4 new routes; `EventType` plumbing for WS.
- `frontend/src/routes/campaign/RetconReplay.tsx` (new): full-screen modal, post-by-post review UI, contradiction badges, fork nudge.
- `frontend/src/api/campaign.ts`: new client methods for the replay endpoints.
