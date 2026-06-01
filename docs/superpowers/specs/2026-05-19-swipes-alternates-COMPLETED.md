# Swipes / Alternates — Completion Notes

Tracks how the implementation differs from the design spec
(`2026-05-19-swipes-alternates-design.md`) and plan
(`../plans/2026-05-19-swipes-alternates.md`). Read alongside those, not in
place of them.

## Branches landed

| Branch | Plan section | Status | Landed in |
|--------|--------------|--------|-----------|
| A — `delta_set_id` first-class on StateStore | Tasks A1–A2 | Merged | main (commit 86362e9) |
| B — Sidecar `alternates` + `.md` rebuild | Tasks B1–B2 | Merged | main (commit 1f51aed) |
| C — `Orchestrator.regenerate_post` | Task C1 | Merged | PR #395 |
| D — `switch_primary_alternate` | Task D1 | Merged | main (commit e06d455) |
| E — pin/unpin/delete + vacuum | Tasks E1, E3 | Landed here | this PR |
| F — REST routes, WS forwarding, frontend chevrons | Tasks F1–F3 | Landed here | this PR |

## Deltas vs the design

### Branch C — non-destructive regenerate

The design suggested replacing `regenerate_last` with `regenerate_post`
(plan step F-end2). At the time the two were kept side by side because they
are not semantically equivalent: `regenerate_last` was a destructive replay
(delete the model post, re-run the turn) while `regenerate_post` is
non-destructive (append a non-primary alternate the user can switch to).

> **Resolved (#512).** That follow-up cleanup landed: `regenerate_last` and
> the `/turns/regenerate` route were removed, and `regenerate_post` (the
> per-post swipe) is now the single reroll path.

### Branch E — eviction + vacuum

The plan called for "max alternates eviction in `regenerate_post`" and a
background-coroutine vacuum sweep. Both are present:

- `OrchestratorService._evict_overflow_alternate` runs after
  `append_alternate` inside `regenerate_post`. It removes the oldest
  non-primary, non-pinned alternate when the count exceeds
  `OrchestratorConfig.swipes.max_alternates_per_post` (default 5). The
  just-generated alternate is always the newest, so it is never the
  eviction target.
- `OrchestratorService.purge_stale_alternates(campaign_id, *, older_than_days=None, now=None)`
  walks every scene/post in a campaign and deletes alternates older than
  the threshold (default `swipes.auto_purge_older_than_days` = 30) that are
  not the primary and not pinned. Returns the list of deleted alternate
  ids. There is no in-process scheduler wiring yet; the method is exposed
  so the observability daemon or a future cron can call it explicitly.

Both helpers reuse `delete_alternate`, so they emit `alternate_deleted`
events for observers (UI, telemetry) just like a user-initiated delete.

### `unpin_alternate`

No separate method — clients call `pin_alternate(..., pinned=False)` to
unpin. The REST `…/pin` route takes `{pinned: bool}` and dispatches to the
single orchestrator method.

### Branch F — REST surface

Routes live in `backend/src/grimoire/api/alternates.py` (a dedicated router, not `api/campaigns.py`) and mount under `/api/campaigns/{cid}/scenes/{sid}/posts/{pid}`:

- `POST /regenerate` → `Orchestrator.regenerate_post`
- `GET /alternates` → reads the post's sidecar alternates
- `POST /alternates/{aid}/primary` → `switch_primary_alternate`
- `POST /alternates/{aid}/pin` (body `{pinned: bool}`) → `pin_alternate`
- `DELETE /alternates/{aid}` (204) → `delete_alternate`

All routes verify that `scene_id` matches the post's scene before
dispatching, so callers can't reach into another scene by guessing ids.
`LatestPostOnlyError` maps to 400, `CannotDeletePrimaryError` to 409,
`AlternateNotFoundError` to 404.

### Branch F — WS event forwarding

`alternate_added`, `primary_switched`, `alternate_pinned`,
`alternate_deleted` were already emitted by the orchestrator but were not
in `api/stream.py:_FORWARDED_EVENTS`; they are now, so subscribed WS
clients receive them.

### Branch F — frontend

- `ApiPost` gained optional `alternates` and `primary_alternate_id`.
  `ApiAlternate` mirrors the backend dataclass.
- `campaignApi` gained `regeneratePost`, `listAlternates`,
  `switchPrimaryAlternate`, `pinAlternate`, `deleteAlternate`.
- `PostItem` renders a chevron strip when a post has 2+ alternates:
  prev / count / next / pin / regenerate. `ScenePane` computes the latest
  model post and passes `isLatestModelPost` so the strip only enables
  mutations on that post; older posts show the "use Retcon / Fork" hint.
- No external rewrite-dialog hook yet (that's `auxiliary-tasks`); the
  pencil/edit button from the design mockup is deferred.

## Deferred follow-ups

- F-end2: migrate the legacy `regenerate_last` callers and the
  `/turns/regenerate` route to `regenerate_post` once the frontend's
  regenerate button has fully moved to the per-post route.
- Background scheduler for `purge_stale_alternates` (observability daemon
  or cron). Currently the method exists; nothing calls it on a timer.
- Rewrite dialog (`openRewriteDialog`) — owned by the `auxiliary-tasks`
  plan, not part of this stack.
