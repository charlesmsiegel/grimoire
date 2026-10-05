# Play controls II — hide a post from context

Step 2 of the SillyTavern-parity play controls (the programme table is in
`2026-10-05-play-controls-swipes-design.md`). The owner asked for the whole
programme to run without stopping for approvals; decisions a brainstorm would
have put to them are recorded here as decisions, with the reason.

## What it is

A player marks a post **excluded**. It stays in the transcript, in the play
view (visibly marked) and in every export, but it never reaches a prompt:
not the turn, not the speaker selector, not the rolling summary, the scene-break
check, absorb, the audit, dossiers, voice drift, the tracker or the
passage-character draft. Uses: OOC chatter, a failed experiment kept for the
record, a tangent.

## Decisions

- **What can be excluded.** Player posts and model posts. **Not** a dice-roll
  line, a scene-transition line or a director note:
  - a roll line is in lockstep with an immutable `rolls.json` entry that the
    audit reads directly (`audit.roll_lines`), so hiding the line would hide
    half of a fact;
  - a transition line is the transcript half of `location_history` /
    `time_history`, which the context builder reads as scene state;
  - a director note already never reaches a story prompt.
  The route refuses those with 400 `not_excludable`.
- **Exclusion belongs to the post slot, not the variant.** Swiping, rerolling
  or activating another variant of an excluded response keeps it excluded; the
  player excluded "that reply", not "that take". `responses._message` rebuilds
  the message dict on `save_variant`, `activate` and `publish_saved`, so those
  writers carry the flag across.
- **Safe by default.** `chronicle.transcript_text` filters excluded posts
  unless told otherwise; the two exports that call it pass
  `include_excluded=True`. A future LLM caller that forgets the parameter
  therefore leaks nothing; a future export that forgets it drops OOC posts, which
  is visible and harmless.
- **Excluding is not deleting.** Structural counts are unchanged: `turn_sizes`,
  reroll targeting, the response ledger, attempts, pin lifetimes and usage
  attribution by post index all see the post. A turn answering an excluded
  player post is still charged to that post's index.
- **Legacy alternates lose it.** A reply that predates the response ledger and
  is swapped through the legacy alternates sidecar (`alternates.promote`) comes
  back included: the sidecar stores speaker and content only. Every reply since
  character turns is a ledger reply, so this touches old scenes only; stated
  rather than fixed.

## Design

### 1. Storage

`serialize.RESPONSE_METADATA` gains `"excluded"`. The per-message
`<!-- grimoire-response {json} -->` comment already round-trips for every block
regardless of role or speaker and never enters `content`. The value stored is
the boolean `true`; an included post simply has no key (no `false` written).

Helper: `serialize.is_excluded(message) -> bool`, and
`serialize.in_context(messages) -> list[dict]` returning the messages that are
neither excluded nor director notes, in order — the one filter every LLM input
uses so the rule lives in one place.

### 2. Toggling

`PUT /campaigns/{cid}/scenes/{sid}/messages/{index}/excluded` with body
`{"excluded": bool}` (pydantic model `ExcludeMessage`).

- Under `runs.scene_held_free` (refused `scene_busy` while a turn or review
  holds the scene); a row in `test_scene_freeze.py`.
- 404 for an unknown scene, 400 for an index out of range, 400
  `not_excludable` for a roll, transition or director-note line.
- Store mutator `scenes.write.set_excluded(cid, sid, index, excluded)`,
  `@locking._serialized`, parse → set/remove the key → serialize, one atomic
  write. Idempotent.
- Exclusion changes what later prompts saw, so like an edit it flags every
  later ledger response `context_changed`, and the tracker is told via the same
  hook an edit uses (`tracker_routes.after_text_edit`).
- Returns the scene (as the edit route does). The revision middleware stamps it.

### 3. Every LLM input skips excluded posts

| Input | Change |
|---|---|
| Turn history — `context/story._project_history` | skip `is_excluded` (beside the existing director-note skip) |
| `_assemble` content inputs: voice examples (`history[-4:]`), `recent_text` (world info, mechanics, art catalogue, recall), `birthday_text`, `speaker.nominate`, `length_drift.measure_contributions` | computed from `in_context(history)`; the index-based steps (`pins.active`, `actor.observed_history`) still run on the full list first |
| Speaker selector — `character_turns._selector_messages` | last 12 *in-context* posts |
| `chronicle.transcript_text` (audit, absorb, rolling summary, scene-break, dossiers) | filters by default; `include_excluded=True` for the markdown and plain-text exports |
| Absorb evidence — `store.absorb.materialize(..., messages)` and `absorb/routing.speaker_index` | given the same filtered list the model was shown, so a quote from an excluded post does not check out as evidence |
| Tracker — `tracker/walk._tracked`, `routes/tracker._context_posts` | an excluded post is not tracked and is not context for a neighbour |
| Passage character draft — `passage_characters._observable_neighbors` | skips excluded neighbours |

The prompt inspector's history row is built from the packed projected history,
so it follows automatically.

### 4. Staleness

Toggling exclusion changes what a fold or review was built from, so the
flag joins the digests that decide staleness: `rolling_summary.covered_digest`
(also used by the scene-break check), `pending_reviews.watermark`, and
`responses.transcript_hash`. Each hashes role, speaker and content today; each
adds `excluded` when present, so an unflagged transcript hashes exactly as it
did before (no spurious invalidation on upgrade). After a toggle the client asks
for the follow-ups as it does after an edit (`askAfterPost`).

### 5. Exports keep everything

`export.collect` and the plain-text export pass `include_excluded=True`.
The JSON export writes messages verbatim, metadata included, so the flag
travels. The markdown export marks an excluded post with a trailing
` *(not in context)*` after its speaker label, so a reader of the export can
tell what the model never saw.

### 6. Play view

- A gutter toggle on every excludable post, beside ✎: `⊘`, `aria-pressed`
  reflecting the state, title "Hide from context" / "Return to context",
  `disabled={rolling}` like its neighbours, shown when the scene is active.
- An excluded post renders with class `excluded` on its `.msg` row: dimmed
  body, dashed left rule, and a small "not in context" tag beside the speaker.
  Per post, not per run, because an excluded post can sit inside a run of
  included ones.
- After a toggle: reload the scene and `askAfterPost`, as `saveEdit` does.
- `Message.excluded?: boolean` in `api/types.ts`; `api.setExcluded(cid, sid,
  index, excluded)`.

## Testing

Backend:
- the flag round-trips through serialize/parse on player, model and response
  posts, and `excluded: false` writes no key;
- the route excludes and re-includes; refuses a roll line, a transition line
  and a director note (400 `not_excludable`), an out-of-range index (400), and
  is refused `scene_busy` while a turn holds the scene (freeze-table row);
- a later ledger response is flagged `context_changed`;
- the composed turn prompt omits an excluded post's text, the inspector
  breakdown omits it, and the speaker selector's conversation omits it;
- `transcript_text` omits it by default and keeps it with
  `include_excluded=True`; the markdown export keeps it with the marker;
- swiping, rerolling and activating a variant of an excluded response keeps
  the flag;
- the rolling-summary digest, the review watermark and the transcript hash
  change when a post is excluded and are unchanged for a transcript with no
  flags (matching the pre-change value);
- absorb evidence matching does not accept a quote that only appears in an
  excluded post.

Frontend:
- the toggle appears on player and model posts, not on roll, transition or
  note lines; clicking calls `setExcluded` and reloads; `aria-pressed` follows;
- an excluded post carries the `excluded` class and the "not in context" tag.
