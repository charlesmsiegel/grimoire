# Transient per-turn state, with decay and promotion

Issues #120 (transient per-turn field state with provenance + decay) and #121
(promote reinforced transient state to canonical facts).

## Problem

Canonical per-NPC state today is one prose snapshot per character —
`store/playstate.py` writing `characters/<id>/state.md` (`## Current state /
## Knows / ## Suspects`) — rewritten only at absorb, through the
extraction → StagedEdit → user-approval pipeline. Its granularity is the
scene. Nothing in the app knows that Winifred has been *guarded* for the last
four posts, or that she stopped being guarded two posts ago.

So the prompt cannot tell the model what mood/intent/posture an NPC is
currently holding, and the review pipeline has no evidence for whether a
transient cue has hardened into something worth writing down.

## Cost, stated honestly

Live per-turn state needs either a second LLM call after every turn (doubling
per-turn spend and latency) or an inline block the reply already carries.
This takes the second road, #120's recommended Option A: the model ends each
reply with a fenced `state` block, the backend strips it before the reply
enters the transcript, and it costs zero extra calls and a few dozen output
tokens per turn.

The price is prompt contamination risk — one more instruction, and a model
that complies badly leaks a JSON blob into prose. That is why the feature is
**off by default** (`turnstate_depth = "0"`), the same shape `archive_depth`
uses for "0 disables it".

## Scope

In: the ledger store, the strip/record path, in-stream suppression of the
block, the context section with decay, promotion as synthetic StagedEdits at
absorb, two config keys and their Configuration-page fields.

Out: #109's `extraction_mode` setting (this ships its own knob, which that
issue can later subsume), #195's HUD, tool-use extraction, per-post state for
players (playstate tracks `characters` only, and so does this).

## The block

The model ends a reply with:

    ```state
    {"Winifred": {"mood": "guarded", "intent": "get the ledger back", "posture": "between the door and the desk"}}
    ```

Keyed by the **name** the model already uses in its `**Speaker:**` markers,
not by a store id — the scene prompt never shows ids, and asking for one
would mean putting an id list in the prompt for the model to mistype.

Labels resolve through `scenes.match_name`, the same rule the transcript
grammar uses: exact first, else the single cast name the label is a
word-boundary prefix of. That matters — `**Winifred:**` is a valid transcript
label for `Winifred Ash`, so it is a label the instruction invites the model to
reuse, and exact-matching would have persisted the dialogue while silently
dropping all of its state. Injected into `resolve` rather than imported:
`scenes` imports `turnstate` (for `drop_scene`), so reaching back would close
a cycle, and injection keeps the rule in one place. Ambiguous labels resolve
to nobody, which is what `match_name` already says about them. Fields are exactly `mood`, `intent`, `posture`; other keys are
dropped; values are collapsed to one line and dropped above 80 characters,
which is #121's "constrain the tracked fields to short values so streaks are
detectable".

**Only a trailing block is stripped.** A `state` fence in the middle of a
reply is left in the transcript: it is either the model narrating about a
fence (rare) or misbehaving (visible, and it should be), and silently
deleting mid-reply text is the one failure mode that loses narration. A
trailing *unterminated* opener is stripped too — a truncated turn has no
narration after it by construction.

Both fences accept `\r?\n`. A provider returning CRLF otherwise matched
neither boundary, and that failure is silent and total: the block persists into
the transcript as narration and its state is never recorded.

The redactor resolves everything it withheld through `split_block` at end of
stream, held prefixes included. A stream that stops exactly after `` ```state ``
with no newline leaves a *complete* opener held, and `split_block` strips that
as an unterminated block — emitting it would show the player an opener the
transcript does not contain, and on a reroll would show it in place of the
reply the server just restored.

Stripping and recording are unconditional; only the *instruction* and the
*injection* are gated on the config. Turning the feature off must not leave
blocks landing in transcripts while the model still complies from context.

## Storage

`campaigns/<cid>/turnstate.json`:

```json
{"0003-the-ledger": {"12": {"characters:winifred": {"mood": "guarded"}}}}
```

Provenance is the `(sid, post index)` key. The index is that of the **last
post of the generation** the block rode in on, so a fresh entry always sits
at the transcript tail.

Index-keying is what #120 specifies and it inherits that choice's weakness: a
`edit_message` that changes how a message splits shifts later indices by one,
misattributing entries. Bounded and accepted — everything outside the decay
window is dropped anyway.

Two narrower cases are *not* accepted, because the ledger would state something
false rather than merely stale:

- Reads drop entries at or past the current transcript length, so a `trim` or a
  reroll that shortens the transcript cannot leave an entry describing a post
  that no longer exists.
- Every persisted reply first **supersedes** entries from its landing index on.
  The tail filter alone does not cover a reroll: the replacement lands at the
  same index, so the discarded variant's entry points at a post that exists
  again, and a replacement carrying no block of its own would inherit it.
- `remove_trailing_assistant_run` supersedes from the index it cut at — inside
  the store mutator, not its callers, because there are two (the reroll route
  and `alternates.promote`, which swaps a parked take in through the same
  remove/append pair) and they must not diverge. The cut index, not the
  post-removal length: trailing transition lines are preserved and re-appended,
  so the replacement lands *above* where the old generation sat. The dropped
  entries ride back in the removal's token: reroll deletes before it generates,
  and a generation that then fails, is cancelled, or says nothing but a tracker
  block puts the reply back — restoring its narration while its recorded mood
  stayed deleted would leave the reply visibly present and silently absent from
  the next prompt.
- `PUT .../messages/{index}` supersedes from that index too. An edit is the one
  transcript change no index-based filter can see: rewriting a furious exchange
  as a calm one leaves the entry at a perfectly valid index. Everything after
  the edit goes with it, since editing text can add or remove blocks and shift
  every later index onto a post it does not describe.

Promotion measures the **ledger its caller hands it**, and `post_absorb`
captures those entries with the scene under one lock before awaiting the
extraction call. A transcript length alone was not a snapshot: an edit or a
reroll landing mid-absorb rewrites entries *below* the tail, so only a copy
taken at the same instant is immune. Nothing that landed after the review's
transcript can contribute a promoted value to it.

Promotion is gated on `turnstate_depth > 0`, not only on `promote_streak`. The
Configuration page says `0` turns the whole feature off, so a retained ledger —
or blocks a model volunteered while it was off all along — must not keep
proposing canonical state behind that promise. `promote_streak` stays the
narrower switch: promotion off, tracking still on.

Every "did this turn produce anything?" test runs on the **stripped**
narration. A reply that is only a tracker block is non-empty raw and empty in
the transcript, and those tests decide whether to restore a reply reroll
deleted, whether to take a stranded user post back, and whether to persist at
all — so testing the raw text made a tracker-only regenerate look successful
and destroy the only copy of the reply it replaced.

A scene keeps its newest 200 entries. turnstate.json is per-campaign and is
re-read and rewritten on every persisted turn, and both readers only ever want
the tail — `current` a window back from it, `streaks` the final run.

`promote_streak` is clamped to that retention: a threshold above it could
never be met, and a setting that silently cannot fire is worse than one that
saturates at the memory the system has.

Scene ids are **recycled** on delete (`scenes.lifecycle.delete_scene`), so
the ledger is purged for the deleted scene there, exactly as `commits` is
retired — otherwise the next scene to take the id inherits a dead one's
moods. Renames go through the `scene_refs.repoint` fan-out.

## Decay

At context build, only entries whose index is within `turnstate_depth` posts
of the tail are considered, newest value per (actor, field) wins, and older
values simply fall out. `0` disables the whole feature. Rendered as a
`Transient state` section at `SPOTLIGHT` tier, directly after `Character
state` — same tier, because it is the same kind of claim at a shorter
half-life — so it appears in the inspector's token breakdown like every other
section.

The instruction to emit the block is its own `LOCK_IN` section after
`Response format`. It renders empty on the opener: the opener is streamed
ephemerally into an editable box the user reads before adopting, and a JSON
blob in that box is a defect the user has to delete by hand.

## In-stream suppression

`_persist_reply` strips the block from what is *stored*, but the deltas have
already reached the browser. A small `StreamRedactor` composed **outside**
`fence.FenceWatcher` (not inside it — that class is load-bearing for roll
proposals and does not need a second concern) buffers from any backtick and
releases the buffer the moment it cannot become a ` ```state ` opener.

Once one opens it withholds the rest and resolves at end of stream with
`split_block` itself — not by dropping it. "Is this block trailing?" is
undecidable until there is no more text, and a redactor that guessed would
silently end the streamed reply early on exactly the input the transcript
keeps whole. `watcher.narration` is untouched throughout, so the persistence
path is unchanged and the grammar has one definition.

## Promotion (#121)

At absorb, inside `absorb.materialize` — so promotion rides the existing
review checklist, `apply_edits` and `changes.json` deltas unchanged, and
nothing writes canonical state silently.

An `(actor, field)` whose **final** run of recorded values is the same
normalized value across ≥ `promote_streak` entries yields a `character_state`
StagedEdit folding `Field: value` into `current_state`.

Two judgment calls the issue leaves open:

- **Final run, not any run.** `current_state` is standing state; a mood held
  for four posts in the middle of a scene and abandoned before the end is
  precisely not what the character is now. Promoting it would write the
  scene's discarded middle into its conclusion.
- **Entries where the actor is absent are skipped, not streak-breaking.** An
  NPC who did not act this turn has not changed her posture, and requiring
  literal index adjacency would make promotion unreachable in any scene with
  more than one NPC.

Folding is idempotent: a `Mood:` line already in `current_state` is replaced,
not appended, so the second absorb over the same ledger proposes nothing
(`materialize` already drops an edit whose `before == after`). Collision with
the model's own `character_state` edit for that character merges into the one
StagedEdit id (`character_state:<char_id>`) rather than emitting two.

## Config

| key | default | meaning |
| --- | --- | --- |
| `turnstate_depth` | `"0"` | posts of tail the ledger is injected over; `0` disables the feature |
| `promote_streak` | `"3"` | consecutive recorded values that promote |

Both on the Configuration page beside the existing Context knobs.

## Tests

Backend (`GRIMOIRE_HOME` → `tmp_path`): block parsing (absent, malformed
JSON, mid-reply, unterminated, unknown fields, over-long values, unknown
names); ledger record/read/decay/repoint/drop; the redactor (split across
deltas, released non-fence backticks, suppression after an opener);
`_persist_reply` end-to-end (block never reaches the transcript, entry
lands at the right index); promotion (streak met → staged with correct
before/after, streak broken → nothing, merge with an LLM-proposed edit,
idempotent second absorb); the two new context sections and their gating.
Frontend: the two Configuration fields round-trip.
`scripts/verify_templates.py` covers the new templates' data contract.
