# Retcon

Deliberate edit of an *earlier* post. The orchestrator already has
`retcon_post` for the "leave-as-is" variant. This spec adds the replay
variant, the dedicated UX, and the contradiction surfacing.

## Two follow-ups after a retcon

1. **Leave subsequent posts as-is.** The user accepts that subsequent
   posts may now be inconsistent. State Store rewinds the original post's
   deltas and applies the new ones; subsequent posts' deltas remain.
   Continuity's contradiction detection will surface issues; the user
   addresses them. (This is the path the existing
   `orchestrator.retcon_post` implements.)
2. **Replay following turns.** The system re-runs each subsequent
   canonical turn from the retcon point, using the retconned post and
   any earlier turns as context. Each replayed turn produces a new
   alternate (per `swipes-alternates.md`); the original alternate is
   preserved. The user reviews each one with Accept / Try again / Cancel.

## Flow

1. User selects a post and chooses "Retcon...".
2. Inline editor — edit prose directly, or invoke `auxiliary-tasks.md`'s
   `rewrite_post` to produce a new version.
3. User accepts the retconned version.
4. System prompts: leave following turns, or replay?
5. **Leave path**: rewind old deltas, apply new deltas, surface
   contradictions via Continuity.
6. **Replay path**: enter "retcon replay mode" — Orchestrator generates a
   new alternate for each following turn; user reviews each.

## Replay UX

Dedicated full-screen modal (retcons touch multiple turns, so they
deserve their own surface):

```
Retcon replay — by-night-london, from scene 47 post p_4710

[1/4] p_4710 (retconned)            ✓ accepted
[2/4] p_4711                        Reviewing... [Accept] [Try again] [Cancel]
[3/4] p_4712                        Pending
[4/4] p_4713                        Pending
```

Cancellation policy: partial retcon leaves the campaign in the "leave-as-is"
state up to the cancel point — rewound and reapplied deltas up to the cancel
point; rest of the timeline retained. No mid-retcon "undo" — the user
either cancels (partial commit) or completes.

## Auto-fork nudge

When the user retcons with replay ≥ 5 posts, prompt:

> This is a substantial change. Would you like to fork the campaign
> first, retcon on the fork, and then decide whether to promote the
> fork?

Cheap nudge; lets users explore retcons non-destructively. The dialog
defers to `fork.md`.

## Backend surface

```
POST   /campaigns/{id}/turns/{turn-id}/retcon
```

```python
async def retcon_post(
    campaign_id: str,
    post_id: str,
    new_text: str,
    replay_subsequent: bool,
) -> RetconResult: ...

@dataclass
class RetconResult:
    edited_post_id: str
    replayed_post_ids: list[str]
    cancelled_at_post_id: Optional[str]
    contradictions_detected: list[Contradiction]
```

WebSocket events:

```json
{ "type": "retcon_started", "post_id": "...", "replay_subsequent": true }
{ "type": "retcon_post_replayed", "post_id": "...", "new_alternate_id": "..." }
{ "type": "retcon_complete", "post_id": "..." }
```

## Interactions

- `swipes-alternates.md`: each replayed turn produces a new alternate
  (the original is preserved, the new one becomes primary on accept).
- `11-continuity.md`: contradiction detection runs after rewind / apply
  on "leave-as-is"; surfaces issues from divergent state.
- `fork.md`: heavy retcons can nudge toward forking first.
- `16-observability.md`: time-travel queries respect retcons as
  first-class events.

## Audit log

```
[retcon] campaign=... post=p_4708 replay_subsequent=true posts_replayed=3
```

## Performance

- "Leave-as-is" retcon: rewind + apply one delta set → < 100ms.
- "Replay" retcon: one canonical turn cost per replayed post (linear in
  number of replayed posts).
