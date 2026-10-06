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
- **Exclusion belongs to the post slot, not the variant — and to the whole
  response.** Swiping, rerolling or activating another variant of an excluded
  response keeps it excluded; the player excluded "that reply", not "that
  take". A response split into parts around a roll is one reply: toggling any
  part sets every part with that `response_id`, a newly appended part inherits
  the flag from its siblings, and `activate` (which collapses parts) keeps it if
  any part had it. `responses._message` rebuilds the message dict on
  `save_variant`, `activate` and `publish_saved`, so those writers carry the
  flag across.
- **A frozen prompt that predates an exclusion is not replayed.** Rerolls,
  Retry and roll continuations replay a response's frozen prompt snapshot
  rather than recomposing, so a snapshot taken before a post was excluded still
  contains it. The flag's stored value is the time it was set; a reroll (and
  step 4's Keep writing) of a response whose record was created before any
  currently-excluded post that precedes it is refused with 409
  `context_excluded` — "A post this reply was written from is now hidden.
  Replay from here to regenerate without it." A response composed after the
  exclusion rerolls normally.
- **Not on an absorbed scene, not while a round is open.** An absorbed scene
  has no future prompts to protect, and what absorb already took from the post
  stays in the chronicle and facts (retcon or re-absorb is how to correct
  those); the toggle is refused 409 `scene_absorbed` and not shown. Toggling
  while a round is paused or incomplete would change the transcript hash and
  strand its Retry or roll resolution, so it is refused 409 `round_open`.
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
the ISO time it was excluded (truthy, and what the frozen-prompt rule above
compares against); an included post simply has no key (no `false` written).

Helpers in `serialize`: `is_excluded(message) -> bool`;
`without_excluded(messages)` — drops excluded posts only, for inputs that today
include director notes (voice examples, `recent_text`, `birthday_text`), so
their behaviour for notes does not change; and `in_context(messages)` — drops
excluded posts **and** director notes, for inputs that already drop notes
(history projection, transcript text, the selector). The rules live in these
two functions.

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
| `_assemble` content inputs: voice examples (`history[-4:]`), `recent_text` (world info, mechanics, art catalogue, recall), `birthday_text`, `speaker.nominate`, `length_drift.measure_contributions` | computed from `without_excluded(history)`; the index-based steps (`pins.active`, `actor.observed_history`) still run on the full list first |
| Speaker selector — `character_turns._selector_messages` | last 12 *in-context* posts |
| Speaker-order planner — `character_turns._plan`, `_follow_on` | plans over in-context posts only, so a hidden post neither names a speaker nor counts as one having spoken |
| World-info activation — the `posts` `_assemble` hands `world_state._world_info` | a hidden post is not scanned, so its words wake no entry (transcript indices are kept for timed entries) |
| Image-slot picker — `context/story._chosen_images` | a hidden post takes no image slot; the slots go to the most recent in-context posts |
| `chronicle.transcript_text` (audit, absorb, rolling summary, scene-break, dossiers) | filters by default; `include_excluded=True` for the markdown and plain-text exports |
| Absorb evidence — `store.absorb.materialize(..., messages)` and `absorb/routing.speaker_index` | given the same filtered list the model was shown, so a quote from an excluded post does not check out as evidence |
| Tracker — `routes/tracker._context_posts` and the post-landing mark | an excluded post is not context for a neighbour, and no tracker update is marked (paid) for an excluded post. `tracker/walk._tracked` is **unchanged**: it also drives `prune` and later-record flagging, and an excluded post's records must survive so re-including it finds them. The tracker's running state is a ledger of what happened; excluding a post later does not unwind effects already folded into later records (Re-run from here rebuilds them) |
| Passage character draft — `passage_characters` | refused 409 `excluded_source` when the source passage is excluded; excluded neighbours are skipped |
| Empty-transcript guards — absorb, dossiers, `_rolling_due` | count `in_context` posts, so an all-excluded scene is "empty" (no invented dossier, no paid fold restating the summary) |
| Replay (`store/replay.py`) | a generation step whose posts are excluded is replayed verbatim (kept excluded) instead of regenerated — regenerating text that never reaches a prompt buys nothing |

The prompt inspector's history row is built from the packed projected history,
so it follows automatically.

### 4. Staleness

Toggling exclusion changes what a fold or review was built from, so the
flag joins the digests that decide staleness: `rolling_summary.covered_digest`
(also used by the scene-break check), `pending_reviews.watermark`, and
`responses.transcript_hash`. Each hashes role, speaker and content today, and
an unflagged transcript hashes exactly as it did before (no spurious
invalidation on upgrade). The review watermark and the transcript hash add
`excluded` when present. `covered_digest` hashes the in-context projection
instead -- a hidden post is left out -- because the summary and the scene-break
question are folded from that render: hiding or returning a post moves it, while
swiping or rerolling an already-hidden one does not, and activating a variant of
a hidden response inside the fold keeps the summary rather than resetting it. After a toggle the client asks
for the follow-ups as it does after an edit (`askAfterPost`).

### 5. Exports keep everything, marked

`export._chapter` rebuilds messages as role/speaker/content today; it carries
`excluded` through, and each format marks an excluded post:

- markdown and plain text: the post's content is prefixed with the line
  `*(not in context)*`;
- HTML and EPUB: the post gets an `excluded` class and a small "not in context"
  tag;
- JSON: messages verbatim, metadata included.

The absorb and dossier prompts render through `snippets/transcript.j2`, which
is untouched: those prompts never see excluded posts at all.

**Round trip.** `scene_import` recognises a leading `*(not in context)*` line on
an imported post (markdown, text) and sets the flag (stripping the line), and
`ImportedMessage` / `_expanded` carry `excluded` from a JSON bundle — so an
export re-imported does not bring hidden posts back into context.

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
  and a director note (400 `not_excludable`), an out-of-range index (400), an
  absorbed scene (409 `scene_absorbed`), an open round (409 `round_open`), and
  is refused `scene_busy` while a turn holds the scene (freeze-table row);
- toggling one part of a multi-part response sets every part; a new part
  inherits; activate keeps it;
- rerolling a response composed before a preceding post was excluded is refused
  `context_excluded`; one composed after rerolls;
- absorb, dossiers and the rolling-summary trigger treat an all-excluded
  scene as empty; replay keeps an excluded model step verbatim; the passage
  draft refuses an excluded source; no tracker mark is made for an excluded
  post and its records survive a prune;
- every export marks an excluded post, and importing the markdown or JSON
  export restores the flag;
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

## Gate record

Spec → planning gate: independent adversarial review (stand-in for
`/codex:adversarial-review`, Codex CLI unavailable; owner-approved). Resolved:
frozen-snapshot rerolls (refuse `context_excluded`), exports and the import
round trip, tracker prune/marking, multi-part responses, empty-transcript
guards, open rounds, absorbed scenes, director-note behaviour preserved
(`without_excluded`), excluded passage sources, and replay of excluded steps.
