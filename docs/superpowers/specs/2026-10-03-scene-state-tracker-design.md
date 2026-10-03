# Scene state tracker

A per-scene, per-post record of each present character's state — what they
wear, where they stand, what they hold, how they look and how they actually
feel — with each value tagged by who is aware of it. Every character's
generation call receives only what that character could know. Every post
carries its own full snapshot, so rerolls, swipes and edits need no cleanup.

This design replaces the transient turn-state ledger (`store/turnstate.py`,
`docs/superpowers/specs/2026-08-06-transient-turn-state-design.md`) and
narrows the perception rider (ST-11/ST-13 in `templates/scene/response_actor.j2`).
It came out of a brainstorming session over an earlier draft spec. Where this
document and the draft disagree, this document is the decision; the
"Decisions" appendix records what changed and why.

## Problem

Inside a scene, the only record of what someone is wearing, how hurt they look
or how they feel is the transcript. In long scenes those details fall out of
context or get contradicted. Absorb updates `state.md` at scene end, which is
too late for the scene itself.

Grimoire already generates one call per character (`routes/character_turns.py`),
which makes strict per-character visibility possible: each call can be given
exactly the state its character could perceive.

The existing turn-state ledger was a first step and is too narrow to grow:
three hardcoded fields, non-player characters only, set-only, keyed by
transcript index (so an edit that re-splits a reply misattributes every later
entry), emitted inline by the model in a fenced block that has to be stripped
from the stream, and off by default.

## Goals

- Track a configurable set of typed fields for every character present in a
  scene, player characters included, updated after every post.
- Tag every value with who is aware of it, and filter every generation call by
  that awareness.
- Store a full snapshot per post, keyed the way rerolls are keyed, so a reroll,
  swipe or edit carries its own state with it.
- Show the state of each post in an expandable **Tracker** disclosure, the way
  **Thinking** is shown, editable by the user.
- Hand the final state to absorb at scene end.
- Never block play on the tracker.

## Non-goals

- Scene-wide fields (location, time, weather). The scene already tracks these
  (`location_history`, `time_history`, the weather store).
- Numeric fields such as health. Those belong to mechanics sheets.
- Carrying typed state between scenes. A scene starts fresh; only absorb's
  `state.md` crosses the boundary.
- Feelings toward other characters as their own field. They go in
  `true_mood`'s text; across scenes `relationships.json` carries them.
- A self-report fence in the actor's own call.
- Mood portraits, HUD widgets, reacting to field changes, inventory, and a
  bridge to sheets (all Phase 2, below).

## Concepts

| Term | Meaning |
| --- | --- |
| Field | One tracked attribute: key, label, type, default awareness, hint |
| Field set | The effective fields for a scene, built from the layers below |
| Snapshot | The full state of every present character as of one post |
| Record | One post's snapshot plus its status, change list and flags |
| Awareness | Per value: `present` (anyone in the scene can perceive it) or a list of the other actors who know it. The owner always knows their own values. |
| User-set marker | `set_by: "user"` on a value the user edited. A soft marker: the update call is told to change such a value only if the post explicitly changes it. |

## Fields

### Types

- `text`: a short string.
- `list`: a list of short strings.
- `enum`: a `text` field with a closed `options` list.

### Definition

```json
{ "key": "true_mood", "label": "True mood", "type": "text",
  "aware": "self",
  "hint": "What they actually feel, including toward others in the scene." }
```

`aware` is the default awareness a *new* value starts with: `"present"` or
`"self"` (an empty awareness list). `hint` is what the update call reads to
understand the field. `options` is required for `enum` and forbidden otherwise.

### Built-in defaults

Every character, player characters included:

| Key | Type | Default awareness | Hint (abridged) |
| --- | --- | --- | --- |
| `clothing` | text | present | What they are wearing now, briefly |
| `position` | text | present | Where they are in the space, relative to people and things |
| `pose` | text | present | Body posture |
| `holding` | text | present | What is in their hands |
| `condition` | list | present | Visible physical state: soaked, bleeding, limping, flushed, exhausted |
| `visible_mood` | enum | present | The demeanour others can read from face and voice |
| `true_mood` | text | self | What they actually feel, including toward others |
| `intent` | text | self | What they are trying to get out of this scene right now |
| `concealed` | text | self | Something on them others do not know about |
| `attention` | text | present | Who or what they are focused on |

`visible_mood` uses the 28 SillyTavern Character Expressions labels, so that
expression sprite packs map onto it directly in Phase 2:

> admiration, amusement, anger, annoyance, approval, caring, confusion,
> curiosity, desire, disappointment, disapproval, disgust, embarrassment,
> excitement, fear, gratitude, grief, joy, love, nervousness, neutral,
> optimism, pride, realization, relief, remorse, sadness, surprise

Several of these are momentary reactions rather than states (realization,
surprise). That is deliberate, both for sprite compatibility and because
`visible_mood` is what a face shows *now*. `true_mood` is free text so it can
carry nuance ("furious with Winifred, hiding it behind courtesy").

### Layers

The effective field set is built in this order:

1. **Built-in defaults** — shipped in code; not editable.
2. **World** — `<world>/tracker.json`.
3. **Campaign** — `<campaign>/tracker.json`.
4. **Scene** — stored in the scene's tracker directory (below).

Layers 2 and 3 may each **add** a field, **change** any property of an
inherited field, or **switch a field off** by key. Layer 4 may only switch an
inherited field off or add a scene-only field. It may not redefine an inherited
field, because changing an enum's options or a field's type mid-scene would
invalidate snapshots already stored.

```json
{ "version": 1,
  "fields": [ { "key": "blindfolded", "label": "Blindfolded", "type": "text",
                "aware": "present", "hint": "…" } ],
  "change": { "visible_mood": { "hint": "…" } },
  "off": ["attention"] }
```

`<world>/tracker.json` and `<campaign>/tracker.json` are **not** overlay-inherited
files. The campaign layer is an override *on top of* the world layer, resolved
at read time, so a world edit reaches every campaign that has not overridden
that field. World fork copies the world file with the rest of the tree
(`fork_world` copies the whole tree already).

A field switched off stays in stored snapshots but is not rendered in prompts
and is not sent to the update call.

## Storage

### Keys

- **A character post** is keyed `r:<response_id>:<variant_id>`. Every
  model-written post already has a `response_id`: new ones from
  `responses.prepare`, and legacy, opener and greeting posts from the lazy
  `responses.migrate`. The ledger knows each response's active variant. A
  response split around a roll (`response_part`) is still one response and gets
  one record.
- **A user post** is keyed `p:<post_id>`. `post_id` is a new opaque id carried
  in the transcript's existing per-post metadata comment (`<!-- grimoire-response
  {…} -->`, `serialize.RESPONSE_METADATA`). It is minted when a user post is
  appended from now on. Older user posts get no id and are not tracked.
- **Synthetic lines** (rolls, transitions, director notes) are never tracked.

### Layout

```
<campaign>/tracker/<scene identity>/
  fields.json            # the scene layer, if any
  index.json             # per-key status, change list and flags
  r-<rid>-<vid>.json     # one snapshot per tracked character-post variant
  p-<post_id>.json       # one snapshot per tracked user post
```

Response, variant and post ids are hex, so these filenames need no escaping.

The directory is keyed by the scene's **identity** (`store/scenes/identity.py`),
not its `sid`. A `sid` moves on rename and is reused after a delete. The
identity is minted once and never reused, which is why detached runs already key
on it. So there is no rename fan-out, and a recycled `sid` cannot inherit a dead
scene's records. `scenes.lifecycle.delete_scene` removes the directory.

One file per snapshot keeps each update's write cost flat however long the
scene gets. Rerolls add files. A snapshot file is rewritten only when its own
post is edited or re-run.

What changes often and in bulk lives in `index.json` instead: status, the
change list and the two flags for every key. An edit that flags fifty later
posts rewrites one small file, not fifty snapshots. The index holds no
snapshot values. The ordering of writes is snapshot file first, then index,
so a crash between them leaves a snapshot the index does not mention. The
index is treated as rebuildable: a missing or unreadable index is rebuilt
from the snapshot files, with each one counted `ok` and unflagged.

### Snapshot file

```json
{
  "version": 1,
  "snapshot": {
    "characters:mara": {
      "clothing":  { "value": "travel leathers, grey cloak", "aware": "present" },
      "condition": { "value": ["soaked"], "aware": "present" },
      "true_mood": { "value": "afraid of Winifred, hiding it", "aware": ["characters:winifred"], "set_by": "user" },
      "concealed": { "value": "a letter in her boot", "aware": [] }
    },
    "characters:winifred": { "…": "…", "_present": true },
    "pcs:seraphine": { "…": "…" }
  },
  "fields_digest": "…",
  "model": "…",
  "at": "2026-10-03T12:00:00Z"
}
```

- `_present: false` marks a character who has left. Their last values are kept
  so they are still there if they return.
- `fields_digest` records the field set the snapshot was made under.

### Index entry

```json
{ "r-<rid>-<vid>": { "status": "ok",
                     "changed": [["characters:mara", "condition"]],
                     "flags": { "upstream_changed": false, "text_changed": false } } }
```

- `status` is `pending`, `ok` or `failed`.
- `changed` drives the disclosure's collapsed summary, so the summaries for a
  whole scene come from one read.
- "A record" below means a key's snapshot file together with its index entry.

### State at a point

The state as of a post is the record of the nearest earlier-or-equal tracked
post, walking the transcript backwards and using each response's active
variant. "Current state" is that walk from the tail. Only `ok` records count.

### Rules the store follows

- Every write goes through `store.atomic` under `locks.campaign_lock(cid)`.
- The new store module is classified in `store/locks.py` `DOMAIN_MODULES`.
- Paths resolve through `store.paths` / the campaign path resolvers.

## Update pipeline

### Triggers

One function, `tracker.schedule(cid, sid, key)`, is called:

- after a user post is appended (`scenes._chat_run`);
- after a character response settles in `character_turns._save`, including a
  resumed roll part (which re-runs the update for that variant over the whole
  response);
- after a reroll or regenerate variant lands (`_accept_reroll` and the
  regenerate path);
- after an opener is adopted (`greetings._adopt_first_post`) and after a scene
  starts from a greeting (`playing.start_from_greeting`);
- from the user actions **Retry** and **Re-run tracker from here**.

These are per-post hooks. They are not the once-per-round `after_turn`
follow-up, which regenerate and replay never call.

Drafts never schedule. The tracker writer is added to the forbidden-writer list
in `backend/tests/test_draft_suppression.py`.

When the campaign's tracker setting resolves to off, `schedule` does nothing.

### Run

- An update is a `background`-class detached run (`runs.reserve_background`)
  with subject `("scene", cid, identity)`. It has no exclusion key, so it never
  refuses or freezes a turn.
- **Updates in one scene run one at a time, in transcript order.** An update
  whose predecessor is `pending` waits for it. If the predecessor `failed`, the
  update starts from the last `ok` record.
- Each reroll variant runs and keeps its own record. Nothing is cancelled.
- The record is written `pending` when scheduled, and `ok` or `failed` when the
  run ends.

### The call

- **Route:** a new `tracker` route in `store/routing.py`. It inherits by
  default, so it uses whatever model play uses until the user points it at a
  cheaper one.
- **Task:** `tracker-update`, resolved with `_require_connection` and metered
  under `usage.Meter`, attributed to the post.
- **Templates:** `templates/tracker/update_system.j2` and
  `templates/tracker/update_user.j2`.
- **Inputs:**
  - the effective field set: keys, labels, types, options, hints and default
    awareness;
  - each present character's previous values, with awareness and user-set
    markers;
  - newcomers (present characters with no previous values), each with their
    card's appearance description and the "Current state" section of their
    `state.md`;
  - the two preceding non-synthetic posts, marked as context only;
  - the new post, with its speaker.
- **Output:** only changes, plus awareness additions. A list field is returned
  whole.

```json
{ "changes":   { "Mara": { "visible_mood": "fear",
                           "condition": ["soaked", "cut on left forearm"] } },
  "awareness": { "Mara.concealed": ["Winifred"] } }
```

- Character names are resolved through `scenes.match_name`, the rule the
  transcript format already uses. That function covers exact names, a unique
  word-boundary prefix, and a sub-speaker parenthetical removed by
  `scenes.speaker_base`.

### Validation and merge

No model is involved.

- Unknown characters and fields are dropped.
- An enum value outside its options is dropped.
- A `list` value must be a list of strings.
- Text values, and each list item, are collapsed to one line and truncated at
  200 characters.
- A value that changes loses its `set_by` marker. Its awareness resets to the
  field's default unless the reply supplied awareness for it.
- Awareness additions must name actors present at that post; others are
  dropped.
- The result is merged onto the previous snapshot. Present-ness comes from the
  scene's presence intervals (`store/appearances`) at that post.
- The merged result is written as the post's full snapshot.

### Failure

On a provider error, an unparseable reply or a timeout, the record is written
`failed` and nothing else changes. Play is unaffected. The disclosure offers
**Retry**.

### Edits and re-runs

- **Editing a record's values** (any post, not only the latest) writes that
  record. Changed values are marked `set_by: "user"`, and every later record
  gets `upstream_changed`. Nothing re-runs automatically.
- **Editing a post's text** (`PUT …/messages/{index}`) sets that post's record
  `text_changed` and every later record `upstream_changed`.
- **Retry** on a failed record, when it succeeds, sets every later record
  `upstream_changed`.
- **Re-run tracker from here** re-runs that post's update and then every later
  one in order. Each re-run clears its record's flags.
- **Cut, retcon and replay** delete the records of posts that no longer exist.
  They are found by walking the directory against the transcript's current keys.

## Prompt assembly

### The section

- A new `Scene state` section (`templates/scene/sections/tracker_state.j2`)
  replaces `transient_state.j2` in the `SECTIONS` catalog, at the same
  `SPOTLIGHT` tier. It is added to the packer's `_CAST_SECTIONS`, so context
  pins protect it.
- It reads the state as of the post being answered. A reroll reads the state
  before the post it replaces.
- It renders plain labelled lines, the speaker's own block first:

```
# Scene state
You (Mara): wearing travel leathers, grey cloak; crouched by the fire; holding
  a knife; looks nervous; actually afraid of Winifred and hiding it; wants to
  get the letter back unseen; concealed: the letter, in her boot
Winifred (as you see her): wearing a riding coat; standing by the door; looks
  annoyed; holding nothing
```

The exact wording is fixed in the template and covered by `verify_templates.py`
and the offline eval.

### Who sees what

| Call | Own values | Other characters' values |
| --- | --- | --- |
| A character's turn (actor-scoped) | all | `present` values, plus private values whose awareness lists this character |
| Narrator (`grimoire`) | n/a | all; private values labelled *private: never state or imply in narration* |
| Next-speaker selector, opener drafts, other side calls | — | none |

A player character is filtered like any other character: their private values
reach a character's prompt only once that character is on the value's list.

The frozen per-response prompt (`response-prompts/<rid>/…`) already captures
the rendered section, so the turn history shows exactly what each reply
received with no further work.

### The perception rider

- Its state half is removed from `response_actor.j2`. It keeps only "what did
  this character hear or see happen", and points at the Scene state block as
  the source for what the character can see about others.
- It gets its own global on/off setting (`perception_rider`, default on),
  independent of the tracker, so the ST evals can keep comparing with and
  without it.
- The gating to non-narrator actors is unchanged.

### Removed sections

`transient_state.j2` and `transient_tracker.j2` are deleted.
`context/layout.py`'s `_RENAMED` map gains `transient_state → tracker_state`,
so a saved layout keeps the new section where the user had placed the old one,
enabled or not. The instruction section `transient_tracker` has no successor.
`_ordered` already skips ids not in the catalog, so a saved layout that names
it simply loses it.

## Scene boundaries

- **Scene start.** The first post's update (opener or greeting) sees every
  present character as a newcomer and fills their values from their card's
  appearance and their `state.md`. The opener's Tracker disclosure is the
  starting-state card: open it, fix anything, and the fix carries forward as a
  user-set value.
- **Arrival.** After `appear`, the next post's update sees the arrival as a
  newcomer and fills them the same way. No record attaches to the synthetic
  transition line.
- **Departure.** After `leave`, the character's values stay in later snapshots
  with `_present: false`, left out of prompts and of the update call's input.
- **Existing campaigns.** These start tracking from their next post. Earlier
  posts have no record and show no disclosure. The first update finds no
  previous record, so it treats everyone present as a newcomer.

## Absorb

- `_absorb_snapshot` (`routes/scenes.py`) captures the scene's final state
  under the same lock hold that captures the transcript and watermark. That is
  the latest `ok` record at the transcript absorb is reviewing.
- Absorb's prompt gets a **Final tracked state** section listing every
  character's values, private ones included, since absorb writes canonical
  state rather than speaking for a character. Templates: `templates/absorb/`.
- The existing `character_state` edit decides what still holds after the scene,
  and goes through the review checklist unchanged. Nothing is written
  automatically.
- **Player characters gain `state.md`.** `playstate` is extended to
  `pcs/<id>/play/state.md`, with the "Current state" section only; Knows and
  Suspects stay non-player-only. (Not `pcs/<id>/state.md`: a PC's directory
  root is where its versions live, and a file there would be read as one.) Absorb's `character_state_edits` contract accepts PC
  ids, and `apply_edits` writes them. The next scene's first update reads that
  file like any other.

## Retiring turn state

- `store/turnstate.py` is deleted, together with:
  - its `supersede` calls in `scenes/write`, `responses._invalidate`, `retcon`,
    `cascade` and `replay`;
  - `_record_turnstate` in `streaming._persist_reply` and the recording in
    `character_turns._save`;
  - `_promote` and `streaks_from` in `absorb/materializer`;
  - the read in `context/assemble`;
  - `drop_scene` / `repoint_scenes` in `scenes/lifecycle` and the `scene_refs`
    fan-out;
  - its `store/locks.py` classification.
- Stripping a trailing ```` ```state ```` block (`split_block` and
  `StreamRedactor`) moves to `store/response_protocol.py` and stays forever, so
  an old context that still elicits one can never leak it into a transcript.
- `turnstate.json` files are left on disk and never read again.
- `turnstate_depth` and `promote_streak` are removed from `config.py` and the
  Configuration page. Stored values are ignored.
- The frozen campaign's `snapshot.json` is regenerated once, deliberately,
  because the prompt sections changed. `home/` is untouched.

## Interface

### Tracker disclosure

`components/TrackerDisclosure.tsx`, rendered in `TranscriptPost` beside
`SavedThinking`:

- **Placement:** once per response (`lastOfResponse`), and on user posts that
  carry a `post_id`.
- **Collapsed summary:**
  - *Tracker · Mara: visible mood → fear; condition + "cut on left forearm"*
  - *Tracker · no change*
  - *Tracker · updating…*
  - *Tracker · untracked · Retry*
  - plus a marker for *earlier state changed* / *text changed since tracked*.
- **Loading:** the record is fetched only on expand, keyed by `(response_id,
  variant_id)` or `post_id`, as `SavedThinking` does. Summary lines come from
  the scene's `index.json` in one read, so collapsed posts transfer no
  snapshots.
- **Expanded:**
  - each character with values at that post (present ones first);
  - their fields, with the ones this post changed highlighted;
  - an awareness tag on each value: nothing for `present`, *private*, or the
    names of those who know;
  - **Edit** and **Re-run tracker from here**.
- **Edit:** read-only by default with an explicit Edit step, per the project's
  editor rules. Inputs fit the type (text input, list chips, enum select), each
  with an awareness picker (public, or choose who knows). Save writes the
  record and returns to view.

### Cast column

- Each cast tile shows the character's current `visible_mood` as a small label
  under the name, and nothing until the character has state. On a phone the
  label is hidden, as `.cast-state` already is.
- `DossierColumn` gets a **Now** section with the current snapshot and the same
  Edit flow, which edits the latest `ok` record.

### Field editors

- **World:** a new **Tracker** section in `WorldView`, a two-pane list/detail
  editor in the `EntityEditor` pattern, one row per field. Built-in fields are
  shown as inherited, with "override" and "switch off".
- **Campaign:** the same editor in the campaign. Inherited fields are labelled
  by source (built-in or world), with "revert" per field.
- **Scene:** a **Tracker** entry in the Scene ⋯ menu, with per-field on/off
  toggles and "+ Scene-only field".

### Settings

- **Global:** `tracker` (on/off, default on) and `perception_rider` (on/off,
  default on).
- **Campaign:** `tracker` as inherit/on/off in `campaign.md`, resolved like the
  module binding's tri-state.
- The `tracker` model route appears with the other routes automatically.
- The "Transient state" settings group is removed.

### Routes (backend)

- Under `/api/campaigns/{cid}/scenes/{sid}/tracker`:
  - `GET` the per-post summaries;
  - `GET` one record by key;
  - `PUT` one record's values (the user edit);
  - `POST` retry and re-run-from.
- World and campaign field-layer `GET`/`PUT`.
- Scene field-layer `GET`/`PUT`.

All writes take the campaign lock, and are stamped by the activity middleware
like any other campaign write.

## Phases

**Phase 1** (this spec) is everything above.

**Phase 2** gets its own spec:
- mood portraits: a per-character mapping from each `visible_mood` label to a
  gallery image, SillyTavern sprite-pack import, and the tile and transcript
  avatar following `visible_mood` with fallback to the avatar;
- HUD widgets;
- reacting to field changes;
- inventory as structured list fields;
- a bridge to mechanics sheets.

## Acceptance criteria

1. A reroll's Tracker shows its own snapshot. Swiping back to an earlier
   variant shows that variant's snapshot with no new call.
2. No character's prompt contains another character's private value unless that
   character is on the value's awareness list. This is checked by a leak test
   over generated prompts for a scene with private values on every character,
   player character included.
3. The frozen prompt for any reply (`response-prompts/<rid>/…`, the durable
   record the reroll path replays) contains the exact Scene state block its call
   received.
4. A `set_by: "user"` value survives updates over posts that do not change it.
   This is tested with the fake LLM returning no change for it.
5. A tracker failure never blocks play. The post persists, its record is
   `failed`, and Retry works.
6. Editing an earlier record or a post's text flags every later record and
   re-runs nothing on its own.
7. A draft never writes a tracker record (`test_draft_suppression.py`).
8. Nothing reads or writes turn state, and a trailing `state` block in a reply
   is still stripped from the stream and the transcript.

## Testing

**Backend** (store isolated with `GRIMOIRE_HOME`):
- field layering, including scene off/add and the refusal to redefine at scene
  level;
- validation and merge rules;
- awareness filtering for each call type in the table;
- the leak test;
- the ordered per-scene chain, including a failed predecessor;
- scheduling from every trigger point;
- record removal on cut, retcon and replay;
- delete removes the directory, and a recycled `sid` starts clean;
- edit and re-run flags;
- the absorb input and PC `state.md`;
- `post_id` minted on new user posts, and old posts unaffected.

**LLM fakes:** the update call is driven through `backend/tests/llm_fakes.py`.
A cassette entry for `tracker-update` goes in `backend/tests/fixtures/llm/`, so
`test_llm_fakes.py` renders the real template against it.

**Guards:** routing, lock domain, atomic writes, paths, imports, overlay and
pydantic guards pass. No new exemption markers are expected.

**Templates:** `verify_templates.py` covers the new section and the tracker
templates, `templates/README.md` documents their variables, and the offline
eval requires the Scene state section verbatim.

**Frontend:**
- the disclosure fetches on expand, renders each summary state, and Edit
  reveals the form;
- the field editor's list/detail tests (row → read-only view, Edit → form,
  + New → form);
- the cast tile label and the dossier's Now section.

**Live eval** (follow-up, opt-in): measure false changes and missed changes per
model on the `tracker` route, to choose that route's model on data.

## Appendix: decisions against the earlier draft

| Draft position | Decision | Why |
| --- | --- | --- |
| Deltas of typed operations folded in order | Full snapshot per post | Simpler to display, edit and reason about; rerolls carry their own state |
| `dual` visibility type | Two fields, `visible_mood` and `true_mood` | Same effect with no special type |
| Visibility per field | Awareness per value, with a per-field default | A particular secret becomes known; the field stays private |
| Evidence span per operation | Dropped | Snapshot model with user review in the disclosure; revisit if the live eval shows drift |
| Pins | Soft user-set marker | Persistence falls out of the snapshot model; no hold machinery |
| Separate starting-state card | The opener's Tracker disclosure | Same function, no extra UI |
| Typed carry-over and persistence rules | Per-scene state; absorb carries via `state.md` | Most state does not carry; absorb already decides what does |
| Inline extraction as a future mode | Turn state retired; inline stripping kept | One writer, no prose contamination |
| Self-reported internal state | Not in this phase | The separate call covers user posts and other characters, which self-report cannot |
| Scene-scope fields | None | Already tracked at the scene level |
| Number fields | None | Sheets own numbers |
| Anchors in `routes.py`, `absorb.py` | `character_turns._save`, `scenes._chat_run`, `store/absorb/` | Verified against the code |
