# Retcon — Completion Notes

Tracks how the retcon-replay implementation diverges from the design
(`2026-05-19-retcon-design.md`) and plan (`../plans/2026-05-19-retcon.md`).
Read alongside those, not in place of them.

## Branches landed

| Branch | Plan section | Status | Landed in |
|--------|--------------|--------|-----------|
| A — extended `RetconResult` + retcon `EventType` entries | A1, A2 | This PR | combined |
| B — `RetconReplaySession` state machine | B1 | This PR | combined |
| C — REST routes + WS event forwarding | C1 | This PR | combined |
| D — frontend chevron-strip "Retcon..." action + replay modal + fork nudge | D1 | This PR | combined |

## Deltas vs the design

### `RetconResult` stays a Pydantic `BaseModel`

The plan's A1 sketch used a `@dataclass`; the existing `RetconResult` is a
Pydantic `BaseModel` and the rest of `types/orchestrator.py` is too, so the
extension keeps the BaseModel shape. Behaviour is the same — new fields
default to `None` / `[]`, the leave-as-is path doesn't touch them.

### Latest-post enforcement refactor

`regenerate_post` and `switch_primary_alternate` both enforce
"latest-model-post-only" (per the swipes design). The replay path needs to
work on *non-latest* posts by definition, so both methods are split into
a public wrapper that enforces the check + a private `_..._core` that does
the actual work. `RetconReplaySession` calls the cores directly. The
public surface (and its REST routes) still enforces the check, so this
isn't a hole in the swipes contract.

### `replay_batch_id` on `Alternate`

Plan suggested stuffing the batch id into a generic `extra` bag. The
existing `Alternate` dataclass doesn't have one, and adding one for one
optional field felt like overkill — instead `Alternate.replay_batch_id:
str | None` joins the existing typed fields, and the sidecar
reader/writer round-trips it. No schema migration needed (sidecar is YAML;
the field is just another optional key).

### Contradiction surfacing

The design says "after each replayed post's Accept, surface
contradictions specific to that turn." The implementation pulls them from
`ContinuityService.pending_contradictions(limit=50)` filtered by
`candidate_fact.established_in_post == post_id`. This works against the
existing routing pipeline (which calls `check_contradictions` as part of
delta routing during the regenerate) and doesn't need any new continuity
code. Reports are exposed by id only; the frontend follows up via the
existing `/continuity/ledger` route to fetch full details.

### Replay batch lifecycle

The plan implies one canonical session keyed by `campaign_id`. The
implementation keeps that — one open batch per campaign at a time;
concurrent `replay_subsequent=True` calls raise `RetconInFlightError`
(409). Once a batch completes or cancels it moves to a `_closed` map so a
client can still poll its terminal state until the next batch on the
campaign evicts it.

### Subsequent-post collection

Walk in scene-ordinal order. Within the edited scene, include posts with
`order_in_scene > edited.order_in_scene`; in later scenes, include all
posts. Filter to model-authored posts only (`is_player=False` and
`author_kind != PC`). No optional "alternates only" filter — every model
post should re-sample as a regular regenerate would. The plan's note
about restricting to posts that "have alternates" is misleading: by the
time replay starts, the regenerate path lazily synthesizes an implicit
alternate for any post that doesn't have one (the existing swipes-B
behaviour), so the filter would be a no-op in practice.

## Backend surface

- `OrchestratorService.retcon_post(post_id, new_text, *, campaign_id=None, replay_subsequent=False)`
  — keyword-extends the existing positional signature without breaking
  current callers.
- `OrchestratorService.accept_replay / try_again_replay / cancel_replay
  / get_replay_state(campaign_id[, batch_id])` — public API surface.
- Errors: `RetconInFlightError` (409), `RetconBatchNotFoundError` (404),
  `RetconBatchClosedError` (409). Mapped in `api/campaigns.py:_map_retcon_error`.

REST routes:

```
POST   /api/campaigns/{id}/turns/{turn_id}/retcon          # existing — accepts replay_subsequent
GET    /api/campaigns/{id}/retcon/replay/{batch_id}
POST   /api/campaigns/{id}/retcon/replay/{batch_id}/accept
POST   /api/campaigns/{id}/retcon/replay/{batch_id}/try-again
POST   /api/campaigns/{id}/retcon/replay/{batch_id}/cancel
```

WS events forwarded by `api/stream.py:_FORWARDED_EVENTS`:
`retcon_started`, `retcon_post_replayed`, `retcon_post_accepted`,
`retcon_cancelled`, `retcon_complete`.

## Frontend surface

- `campaignApi`: `retconPost`, `getRetconReplay`, `acceptRetconReplay`,
  `tryAgainRetconReplay`, `cancelRetconReplay`, `forkCampaign`.
- `PostItem`: gains a `Retcon...` button on every model-authored post
  (not gated by latest-post). Clicking opens `RetconLauncher`.
- `RetconLauncher.tsx`: stepwise UI (edit → leave/replay decision → fork
  nudge when count ≥ 5 → replay modal).
- `RetconReplay.tsx`: full-screen modal showing the per-post checklist
  with the current row prominent + Accept / Try again / Cancel buttons +
  contradiction-id list.

## Deferred follow-ups

- **Fork-and-redirect routing.** The fork nudge currently forks the
  source campaign but stays on it — it doesn't navigate to the fork. The
  design called for "switch to the fork, re-open retcon dialog there";
  that requires router work outside this PR's scope. Surface for now: the
  user sees the fork created and can switch to it manually.
- **WebSocket-driven progress.** The replay modal polls / reacts to API
  responses; it does not yet listen on the WS stream for the
  `retcon_post_replayed` events. The events are wired backend-side and
  the modal already accepts an `initialState` prop, so adding WS push is
  additive.
- **Rewrite-with-AI button** in the inline editor — owned by the
  `auxiliary-tasks` plan; the textarea is the v1 path.
- **Backend tests:** `test_retcon_replay.py` covers happy-path,
  try-again, cancel, idempotent close, no-subsequent, event ordering, and
  unknown-batch errors. Contradiction-surfacing is exercised
  end-to-end-light (no continuity fake plumbed); deeper coverage would
  need a fake `pending_contradictions`-shaped continuity service in the
  orchestrator fixtures.

## Integration

Full backend unit suite (`pytest -m "not conformance and not integration
and not frozen_campaign and not perf and not golden"`) passes on this
branch. Frontend `npm test` (vitest) passes for the new
`RetconReplay.test.tsx` (6 tests) alongside the existing smoke +
`PostItem.test.tsx` (7 tests). `tsc -b --noEmit` and `eslint .` clean
on touched files.
