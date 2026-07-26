# Response presets: controlling reply length, breadth, and drift

**Status**: design
**Date**: 2026-07-26

## Problem

Scene replies have no length control of any kind. `templates/scene/sections/response_format.j2`
tells the model to write a script of `**Name:**` blocks and stops there — nothing
constrains how many blocks a reply has, how long each one runs, or whether a
character speaks twice. There is no `max_tokens` anywhere in the LLM layer either.

The acute symptom is **drift**: as a scene goes on, replies grow — more paragraphs
per block and longer paragraphs. This is an anchoring effect. The model's own prior
turns sit in the history as concrete, recent evidence of "how long a reply is here",
and they outweigh any rule stated once in a system prompt tens of thousands of
tokens earlier. A static instruction is not a counterweight to a moving target;
it loses the same way every turn.

Three related controls are wanted, and they interact:

1. how long each block runs,
2. how many characters may act or speak in one reply,
3. whether one character may take more than one block in a reply.

They interact because the obvious way to satisfy a per-block word budget is to
write more blocks. Any design that constrains one without the others just moves
the bloat — which is why the primary budget below is **total words per reply**,
not words per block.

## Solution overview

Two layers of data plus two prompt surfaces.

**Length presets** are the built-in numeric vocabulary — four named bundles of the
five knobs below. **Response presets** are saveable named records that pair a prose
style with a length (either a named length preset or explicit numbers). Response
presets resolve across four scopes — turn, scene, campaign, global — with per-field
overrides available at any scope.

The resolved budget reaches the model twice: as a **static budget section** in the
system prompt, and — when measurement shows replies have actually drifted over
budget — as an **adaptive corrective appended to the post-history system message**,
the last message before generation and therefore the closest counterweight to the
anchor. The corrective is computed deterministically in Python from the stored
transcript. No extra LLM call.

## Data model

### Length presets

Built-in constants in `store/lengths.py`. Not files, not user-editable, no CRUD
surface — tuning happens by overriding individual knobs at a scope.

| knob | meaning | Terse | Brisk | Standard | Cinematic |
|---|---|---|---|---|---|
| `reply_words` | target **total** words in one reply, narration included | 150 | 300 | 550 | 900 |
| `blocks` | max blocks in one reply, narration included | 3 | 4 | 5 | 7 |
| `paragraphs` | max paragraphs in any single block | 1 | 2 | 2 | 3 |
| `speakers` | max distinct speaking characters in one reply | 2 | 3 | 4 | 5 |
| `blocks_per_speaker` | max blocks any one character may take | 1 | 1 | 2 | 2 |

`reply_words` is the primary quantity — it is what the user actually experiences as
"replies got longer", and budgeting it is what closes the split-into-more-blocks
loophole that a per-block budget alone leaves open. Per-block length is *derived*
for prompt guidance (`reply_words / blocks` — 50, 75, 110, 129 words respectively)
rather than stored, so the two can never contradict each other.

`blocks` counts narration. `speakers` does not: `**Grimoire:**` narration is a
block and consumes budget, but it is not a character. `blocks` is set to
`speakers + 1` in every preset precisely so narration always has room.

`blocks_per_speaker: 1` is the "no repeats" case — a numeric limit rather than a
boolean, so the cap is measurable and statable in one form.

These numbers are starting values, expected to be tuned in use; that is what the
per-knob overrides exist for.

`standard` is the system-wide floor: what resolution falls back to when no scope
names anything.

### Response presets

Saveable named records in `store/response_presets.py`, following the built-in /
user split established by `store/styles.py`:

- built-ins under `templates/response_presets/` (resolved via
  `prompts.templates_dir()`, so the `GRIMOIRE_TEMPLATES` indirection the Android
  build depends on works unchanged),
- user-authored under `<GRIMOIRE_HOME>/response_presets/`,
- built-ins are immutable — edit and delete raise `BuiltInPresetImmutable`,
  mirroring `styles.BuiltInStyleImmutable`.

Markdown with frontmatter, like styles, but frontmatter-only — a response preset
has no body:

```
---
name: Slow Burn
description: Gothic dread, long unhurried blocks.
style_id: gothic-horror
length_preset: cinematic
---
```

or with explicit values instead of a named length:

```
---
name: Saltmarch Interrogations
description: Two voices, clipped, no narration sprawl.
style_id: noir-detective
reply_words: 220
blocks: 3
paragraphs: 1
speakers: 2
blocks_per_speaker: 1
---
```

#### A preset supplies exactly the fields it specifies

This one rule governs both halves of a record, and every other rule below is a
consequence of it. A field a preset does not specify is not "defaulted" — the
preset simply has no opinion, and resolution walks past it to the next scope.

**Style** — `style_id` is tri-state:

| value | meaning |
|---|---|
| absent / empty | **no opinion** — style keeps resolving outward |
| a style id | supplies that style |
| `none` | explicitly clears the style |

**Length** is a tagged union of two forms, validated rather than merely documented:

- **Named form** — `length_preset: <id>`. Supplies all five knobs.
- **Explicit form** — one or more of the five knob keys. Supplies **exactly the
  knobs present**; the rest are no-opinion and keep resolving outward.
- **Neither form** — a valid, useful record: a style-only preset with no length
  opinion at all. All five knobs keep resolving outward.

Rules, stated exhaustively so two implementers cannot diverge:

- **Write path** (`POST` / `PUT`) rejects a record carrying **both** forms with 400.
  No normalization, no silent precedence. A record carrying neither is accepted —
  it is style-only, not malformed.
- **Read path**, when `length_preset` is present, ignores the explicit keys
  *unconditionally*, including when `length_preset` names something unknown.
- A `length_preset` naming an unknown length preset makes the record **invalid**.
  An invalid record is never partially used: a scope naming it resolves as though
  it named nothing at all — not even its style — and the management view flags it.
  Explicit values can therefore never spring to life because a name was mistyped or
  a preset was renamed.
- **Malformed individual knob values** in explicit form (non-integer, negative,
  zero) are treated as absent for that knob, which keeps resolving outward. The
  view flags them. There is no completion from `standard`: completing a knob would
  make a preset silently supply a field it never specified, which is exactly the
  behaviour that caused the earlier style-wiping bug.

The uniform rule is what makes a style-only preset unambiguous. Under an earlier
formulation it could have been read either as invalid (falling through entirely) or
as an explicit form with five missing knobs (silently supplying `standard` lengths
and clobbering a broader Cinematic). Now it is neither: it supplies a style, and
nothing else.

**Shipped built-ins**: `terse`, `brisk`, `standard`, `cinematic` — each naming the
matching length preset with `style_id` absent. Out of the box the preset list
therefore reads as a plain length picker *that does not disturb styles*; combined
presets like the two examples above are things the user saves.

### Scope storage

Each of the three persistent scopes stores seven flat frontmatter keys. Empty
string means *inherit*.

| key | meaning |
|---|---|
| `response_preset` | id of a response preset, or empty |
| `style_id` | loose style override — **spelled `default_style_id` at global scope** |
| `length_reply_words` | loose knob override |
| `length_blocks` | loose knob override |
| `length_paragraphs` | loose knob override |
| `length_speakers` | loose knob override |
| `length_blocks_per_speaker` | loose knob override |

- **global** — `config.md` frontmatter, via `store/config.py`. `default_style_id`
  is already in `_CONFIG_KEYS`; the other six keys are added to it.
- **campaign** — campaign frontmatter.
- **scene** — scene frontmatter.
- **turn** — not stored. Rides the send request, unpersisted, exactly as the
  offscreen director note does.

The style override key already exists at all three scopes today — `style_id` on
campaigns and scenes, `default_style_id` in `config.md`. Both keep their current
spelling and semantics; the field simply becomes one member of the bundle rather
than a standalone setting. Renaming `default_style_id` for symmetry would break
every existing install's global style for no functional gain. `resolve` normalizes
the two spellings internally so the rest of the system sees one field. See
Migration.

## Resolution

`response_presets.resolve(turn, scene_meta, campaign_meta, config) -> dict`,
mirroring the shape of `styles.resolve_style`.

**There is no single "base preset".** Resolution is a **per-field cascade**: each
of the six fields (`style_id` plus the five length knobs) resolves independently.

For one field, walk the scopes **turn → scene → campaign → global** and take the
first value found. Within each scope, look in this order:

1. that scope's **loose override** for the field, if set;
2. that scope's **named preset's** value for the field, if the scope names a
   resolvable preset *and that preset supplies this field*.

If the walk finds nothing, fall back to `standard`'s value for a length knob, or to
no style for `style_id`.

One refinement for `style_id` only: a value naming a **style that does not exist**
counts as no opinion, and the walk continues outward. See Error handling.

**What "supplies" means** is defined once, above: a preset supplies exactly the
fields it specifies — all five knobs under the named form, only the knobs present
under the explicit form, none under neither form, and `style_id` only when it holds
a style id or the `none` sentinel. A scope naming a missing or invalid preset
supplies nothing at all, and the walk continues.

This is the whole algorithm. Two properties fall out of it that earlier, more
elaborate formulations got wrong:

- **A length choice never disturbs a style.** Global preset *Slow Burn*
  (`style_id: gothic-horror`, `length_preset: cinematic`), scene preset `terse`.
  `reply_words` resolves at the scene (its preset supplies it) → 150. `style_id`
  finds nothing at scene — `terse` has no opinion — and continues to global →
  `gothic-horror`. The scene gets terse lengths and keeps its style. This holds for
  *every* cascade ordering, whether the broader style came from a preset or a loose
  override, because the walk does not care which of the two supplied it.
- **Stale broad settings cannot haunt a narrow choice.** A global
  `length_reply_words: 90` with a scene set to Cinematic resolves `reply_words` at
  the scene, since the scene's preset supplies it. The global value is never
  consulted. No "start fresh" restriction is needed to get this; the walk order
  already produces it.

Within a scope, a loose override beating that scope's own preset is what makes the
"preset plus a tweak" UI work: setting `length_speakers: 3` on a campaign whose
preset is Cinematic yields Cinematic everywhere except speakers.

`resolve` always returns a **complete** dict — `style_id` (possibly empty) plus all
five knobs as valid values. This is mandatory, not stylistic: the Jinja environment
runs with `StrictUndefined`, so a missing template var is a hard render failure in
the middle of a scene turn.

`resolve` also returns, per field, **which scope and which source** produced the
value. The UI's provenance readout needs it, and it makes the cascade directly
testable rather than inferable from effective values.

### Migration

Existing installs have `default_style_id` in `config.md` and `style_id` on
campaigns and scenes, and no `response_preset` anywhere. Under the per-field
cascade that reads as: `style_id` resolves to the first loose override found
walking scene → campaign → global (no presets exist to supply anything), which is
precisely what `styles.resolve_style` does today; and every length knob falls
through to `standard`.

So the migration is a no-op on disk. No rewrite pass, no version bump. Existing
libraries keep resolving their current style with zero user action; the only
behavioral change is that a `standard` budget now applies where none did before.

## Prompt surfaces

### Static budget section

New `templates/scene/sections/response_budget.j2`, included by
`templates/scene/system.j2` immediately after `response_format.j2` — it is a
reply-shape rule and belongs with the other one — and registered in
`context._SECTIONS` so it appears in the scene inspector's token breakdown.

Approximate rendered text at Brisk:

```
# Response budget
This whole reply runs about 300 words across at most 4 blocks, narration
(**Grimoire:**) included — roughly 75 words per block. No block exceeds 2
paragraphs.
At most 3 characters act or speak. Give each exactly one block — do not
return to a character you have already written.
```

The final line reflects `blocks_per_speaker`; above 1 it becomes *"No character
takes more than 2 blocks."*

Word counts are phrased as targets (*"about 300"*), never as hard caps. A hard cap
makes models truncate mid-thought; a target makes them compose shorter. The
structural limits (`blocks`, `paragraphs`, `speakers`, `blocks_per_speaker`) are
phrased as caps, because they are.

### Adaptive corrective

New `templates/scene/length_correction.j2`, appended to the **post-history system
message** — the last message before generation, and thus the closest available
counterweight to the transcript anchor.

The block renders when **any** rule is measurably violated; each rule contributes
its own lines, and a rule within tolerance contributes nothing. A reply whose total
length is fine but which crowds in six speakers gets a corrective containing only
the speaker line.

Total-words violation, quoting the actual per-turn totals rather than a summary
statistic — concrete numbers are better prompt material, and they stay honest
about a signal that is driven by the worst turn, not the average:

```
Your recent replies have run long: the last 3 turns ran 900, 1,510, and
1,640 words against a budget of 300 — up to 5.5× over. Cut hard; this
reply must land near 300 words total. Trim description first, then
dialogue tags.
```

Structural violations each render their own line:

```
Recent replies have exceeded 4 blocks; keep this one to at most 4, narration included.
Recent replies have exceeded 3 speaking characters; keep this one to at most 3.
A character has taken more than 1 block in a reply; give each character at most 1.
A block has run past 2 paragraphs; keep every block to at most 2.
```

**Word-budget tiers**, driven by the **worst** turn in the window — the largest
per-turn ratio, not the window mean:

| max per-turn ratio | rendered |
|---|---|
| < 1.25× | nothing |
| 1.25× – 1.75× | "Trim toward the budget." |
| ≥ 1.75× | "Cut hard." |

**Max, not mean, is load-bearing.** A mean-driven signal oscillates near the
threshold: with a 100-word budget, turns of 130/130/130 sit at 1.30× and render a
correction; one compliant 100-word turn drops the window to 1.20× and clears it,
and the next 150-word turn re-triggers at 1.27×. The corrective would flicker on
and off while behaviour had not meaningfully changed. Using the window maximum
makes the rule "a correction shows iff some turn in the last 3 broke the budget",
which is monotone in the window's contents and cannot flicker.

It also makes **clearing** true rather than merely claimed: since every signal is
"any turn in the window violated", all of them disappear only after 3 consecutive
compliant turns have rolled through. One good turn is never enough. No hysteresis
latch and no persisted state — the signal is a pure function of the last 3 turns.

The cost is that one deliberately long set-piece reply keeps a gentle corrective on
for three turns. That is the right trade: at 1.25×–1.75× the corrective is a nudge,
and predictable behaviour matters more here than optimal sensitivity.

Because the budget is on the *total*, splitting the same prose across more blocks
does not reduce the ratio — the loophole is closed structurally rather than by
asking the model not to exploit it. The `blocks` cap backs this up.

The four structural rules are **not** tiered — they are caps, not targets, so they
are either met or not. Each renders when **any single turn in the window** violates
it, the same any-violation rule the word signal uses. Evaluating a cap against the
window *mean* would be wrong for the same reason: with a cap of 3, turns of 5, 2,
and 2 speakers average exactly 3 and would produce no correction despite one turn
plainly breaking the rule.

### Plumbing

- `templates/scene/post_history.j2` takes a new `length_correction` var and joins
  it after the NPC cards' post-history instructions.
- `context._assemble` resolves the budget, runs measurement, renders the
  correction, and passes it through.
- `context.build_messages` already emits the post-history message whenever the
  string is non-empty, so a scene whose cards carry no post-history instructions
  still receives the corrective. No change needed there.
- `build_director_messages` calls `_assemble`, so offscreen director turns are
  covered by the same path with no extra work.

## Measurement

New pure module `store/length_drift.py` — no I/O. Takes a message list and a
resolved budget, returns metrics. Called once from `_assemble`, which has already
read the scene.

The transcript is **already stored per speaker**: `scenes.split_reply` splits each
model reply on the `**Name:**` marker grammar into separate messages carrying a
`speaker` field. Measurement needs no new parsing.

### Assistant role is not the same thing as a model turn

Measurement cannot infer turns from message role, because grimoire appends
**synthetic assistant messages** that no model wrote. There are four:

| site | text |
|---|---|
| `appearances.appear` | `*{name} joins the scene.*` |
| `appearances.leave`  | `*{name} leaves the scene.*` |
| `scenes.set_location` | `*The scene moves to {name}.*` |
| `scenes.set_datetime` | `*Time passes. It is now {friendly}.*` |

These are speakerless, so they read as narrator blocks. Left alone they would break
measurement two ways: they inflate a turn's block and word counts, and — worse —
one sitting between two model replies **merges them into a single run**. A Brisk
reply of four blocks followed by a location change would measure as a five-block
turn and fire a false `blocks` correction against a reply that obeyed the budget.

**Fix**: a new reserved speaker, `scenes.TRANSITION_SPEAKER`, applied at all four
sites — following `ROLL_SPEAKER` exactly, including the U+2063 prefix that makes
the marker uncollidable with a real character name. This is a small backend change
that belongs to this work, not a pre-existing bug to route around.

### Turn boundaries must be persisted, not inferred

Message roles cannot delimit turns either, because **ephemeral turns are never
stored**. `post_chat` builds a director note (pcless scene) or an empty send
("next NPC round") as a user message that steers one generation and is then
discarded; only the generated assistant segments persist. Consecutive director
generations — the normal way an offscreen scene is played — therefore leave *no*
user message between them. Any role-based segmentation merges an entire offscreen
scene into one enormous "turn", measures it as one cumulative reply, and fires
false corrections that can never clear.

So each generation records its own boundary. `scenes.append_reply(cid, sid,
segments)` becomes the single entry point for persisting a model reply — it already
happens as one loop over `split_reply` output — and appends that generation's block
count to a `turn_sizes` list in the scene's frontmatter, comma-joined, exactly as
`location_history` and `time_history` already are. `remove_trailing_assistant_run`
pops the last entry.

Counts rather than indices, deliberately: message *edits* leave counts untouched,
where indices would need rewriting on every edit.

**Turn segmentation** is then:

> A **model turn** is one entry in `turn_sizes` — the run of messages that one
> generation persisted. Reserved-speaker messages (`ROLL_SPEAKER`,
> `TRANSITION_SPEAKER`) are never part of a generation's segments, so they fall
> outside every turn and are excluded from all metrics automatically.

The greeting is appended through the same entry point and so records a turn of its
own.

Scenes played before this change have no `turn_sizes`. Rather than guess, the
fallback is explicit: **a scene with no `turn_sizes` is not measured**, and no
corrective renders until it has accumulated three recorded generations. Silence for
the first few turns of a pre-existing scene is a far better failure than confident
wrong numbers.

The earlier draft of this design proposed detecting legacy transition lines by
matching their prose shape. That is dropped: a model can legitimately write
`*Mara leaves the scene.*`, and a content match would then delete a real narration
block from the metrics and split a genuine reply in two. Identity is not
recoverable from prose, so it is not inferred from prose.

This spec makes no claim that its turn notion is shared with
`scenes.remove_trailing_assistant_run`; that function *stops at* roll lines rather
than skipping them, because it serves a different purpose (never deleting a roll
whose entry still lives in `rolls.json`).

**Greetings are counted as model turns.** `playing.py` appends the greeting body as
an assistant message, and it is authored rather than generated — but it is the
single strongest length anchor the model has at the start of a scene, and the model
will match it. Measuring it is correct: a 900-word greeting under a 300-word budget
*should* produce a corrective on turn one, because that is exactly where drift
starts.

**Per turn:**

- **total words** — whitespace-split token count across every block in the run,
  narration included, after stripping ` ```roll ` fenced bodies. `store/fence.py`
  already owns that grammar; measurement reuses its opener regex rather than
  restating it.
- **blocks** — messages in the run (separators are already excluded by
  segmentation).
- **max paragraphs** — the largest paragraph count of any single block.
- **distinct speakers** — count of distinct non-`None` speakers, **canonicalized
  against the scene cast first**. Narrator segments store `speaker: None`, so
  "narration does not count against the speaker cap" falls out of the existing data
  model for free.
- **max blocks per speaker** — the largest number of blocks held by any one
  canonicalized speaker.

**Speaker labels are not identities.** `split_reply` preserves whatever label the
model wrote, so one character can appear as `Winifred` in one block and
`Winifred Vance` in the next. Counting raw strings would read that as two speakers
— inflating the speaker count into a false violation while simultaneously letting
the character slip under `blocks_per_speaker`, breaking both structural signals at
once under perfectly ordinary label variation.

Measurement therefore takes the scene's cast names and canonicalizes each label
through `scenes.match_name`, which already resolves exact matches and unambiguous
word-boundary prefixes and is the same helper the rest of the system uses. Labels
it cannot resolve are kept verbatim and counted as themselves — an unrecognized
name is most likely a genuinely new character.

**Window**: the last **3** completed assistant turns. A constant, deliberately not
a setting.

**Signals** — every one of them is "any turn in the window violated", so they all
activate and clear on the same rule:

- **Word drift ratio** = **max** over the window of (turn total words ÷
  `reply_words`). This is the tiered signal. The per-turn totals are returned
  alongside it, since the corrective quotes them.
- **Cap violations** — `blocks`, `paragraphs`, `speakers`, `blocks_per_speaker` are
  each violated if **any single turn** in the window exceeded them.

**Edge cases:**

- Fewer than 3 turns in the scene — measure what exists.
- Zero assistant turns (scene opener, fresh scene) — no corrective, and no
  measurement work at all.
- **Regenerate** measures the turns *before* the one being replaced, because the
  trailing assistant run is dropped first. Re-rolling a bloated reply therefore
  correctly sees the bloat that preceded it.
- Hand-edited messages count. They are what the model sees, which is the premise.
- A mid-scene budget change still measures the older, longer replies. This is
  correct: those replies are still the anchor, and that is exactly when the
  corrective is most needed.

## UI

### `ResponsePresetPicker`

One component used at all three persistent scopes. Contains:

- a preset select,
- an expandable **Overrides** disclosure holding the style picker and the five
  knob fields,
- an **effective values** readout showing what actually resolves and which scope
  each value came from.

Unset override fields render their inherited value as a placeholder, so an empty
field visibly shows what it is inheriting rather than looking blank. The readout is
the antidote to a four-level cascade being hard to reason about: it always answers
"why is this value what it is".

A **Save as preset…** button inside the disclosure mints a
`<GRIMOIRE_HOME>/response_presets/` record from the currently-resolved values.
This is the primary creation flow — presets accumulate from real use rather than
from a blank form.

Placement:

- **ConfigView** — global scope, replacing the current global style default control.
- **CampaignView** campaign settings — campaign scope, replacing `StyleConfig`.
- **SceneInspector** — scene scope, replacing the existing scene style override.

`components/StyleConfig.tsx` is absorbed and deleted; `StyleGuidesView` (which
manages style *records*, a different job) is untouched.

### Composer chip

In `CampaignView`, beside the Send button: a chip showing the resolved preset name.
Clicking it opens the preset list; picking one applies to **the next turn only**,
rendered with a one-shot badge, reverting to inherited once the reply lands.

### `ResponsePresetsView`

A management view alongside `StyleGuidesView`, built on the list/detail page
pattern from CLAUDE.md: `.editor-list` rail of presets plus `+ New`, `.editor-body`
showing a read-only `.detail-view` by default with an explicit **Edit** step.
Built-in presets show no Edit button and offer **Duplicate** instead, matching how
built-in styles behave. Invalid records (unknown `length_preset`) and
completed-from-`standard` records are flagged in the detail view.

### Deleting a preset in use

Deleting a preset that scopes reference is permitted, but never silent. The
confirmation must describe what will **actually** happen, and two things make that
harder than counting references:

1. **Deletion re-resolves; it does not reset to `standard`.** A scene that loses
   its preset re-runs the per-field cascade and may pick up a campaign or global
   preset's style and lengths. `standard` is reached only where nothing else
   supplies a field.
2. **Scopes that never named the preset still change.** If a campaign names it and
   twenty scenes inherit, deleting changes all twenty. If it is the *global*
   default, potentially every campaign and scene changes. A scan for direct
   references would report one campaign and understate the blast radius twentyfold.

So `GET /api/response-presets/{id}/usage` does not scan for references. It
**diffs resolutions**: for the global scope, every campaign, and every scene, it
computes the effective bundle now and again with the preset treated as absent, and
returns every scope whose bundle changes, with both values. Finding direct
references already requires reading every campaign and scene's frontmatter, so the
extra work is an in-memory `resolve` per scope — the I/O was already being paid.

The dialog shows the changed scopes, capped with a count so a global-default
deletion does not render five hundred lines:

> **Slow Burn** — deleting changes 1 global default, 2 campaigns, and 23 scenes.
> • Global → no style, Standard lengths
> • Campaign *Saltmarch* → Gothic Horror (from global), Standard lengths
> • Scene *The Long Dark* → Gothic Horror, Cinematic lengths (from campaign)
> • …and 23 more

Stating "they will fall back to Standard" would be false for most of these, and a
false impact preview immediately before an irreversible delete is worse than no
preview — it is the whole justification for not keeping tombstones.

Snapshots or tombstones that preserve a deleted preset's last resolved values were
considered and rejected: they add a second, invisible source of truth to a store
whose whole premise is hand-inspectable markdown, to protect a single-user library
where re-creating a preset is a minute's work.

## API

Mirrors the styles routes, including their conventions — built-in immutability
returns **400** with an explanatory detail, as `PUT /styles/{sid}` does today, not
409.

**Preset CRUD:**

- `GET /api/response-presets` — list
- `POST /api/response-presets` — create; 400 if the body carries both length forms
- `GET /api/response-presets/{id}`
- `PUT /api/response-presets/{id}` — 400 on built-in; 400 on both length forms
- `DELETE /api/response-presets/{id}` — 400 on built-in
- `GET /api/response-presets/{id}/usage` — every scope whose effective bundle would
  change if this preset were deleted, with before and after values
- `POST /api/response-presets/{id}/duplicate`
- `GET /api/length-presets` — the four constants, so the picker can show the
  numbers behind a named length

**Scope settings** — the existing per-scope `/style` sub-resources are generalized
rather than supplemented, so there is exactly one write path per field:

- `GET/PUT /api/campaigns/{cid}/response`
- `GET/PUT /api/campaigns/{cid}/scenes/{sid}/response`
- global scope: the new keys join the existing config GET/PUT payload

`GET/PUT /api/campaigns/{cid}/style` and
`GET/PUT /api/campaigns/{cid}/scenes/{sid}/style` are **removed**, along with their
frontend callers. `style_id` is now one field of a bundle; two endpoints writing
one field invites divergence. See Review notes for why a deprecation window is not
warranted here.

**Turn override** — the chat send endpoint gains an optional `response` payload
(preset id and/or knob overrides). Unpersisted, like the director note.

Route models stay plain `BaseModel` with plain fields, dumped via `routes._dump`,
per the pydantic v1/v2-agnostic rule.

## Error handling

Nothing here may break play. Every failure degrades to a working budget.

- A scope naming a **deleted, missing, or invalid preset** resolves as if that
  scope named nothing, falling through to the next scope.
- **Malformed knob values** at a scope (non-integer, negative, zero) are treated as
  unset and fall through the resolution chain.
- An **unknown `style_id`** in a preset or override is treated as **no opinion**:
  style resolution continues outward to the next scope. This matches
  `styles.resolve_style`, which today skips an id that doesn't resolve and falls
  back up the chain precisely so a stale reference "never breaks generation". Only
  the explicit `none` sentinel clears an inherited style. Taking "first `style_id`
  found, valid or not" would let one stale scene-level reference suppress a
  perfectly good campaign style after upgrade — and would falsify the no-op
  migration claim.
- Measurement never raises. A transcript it cannot segment yields no metrics and
  therefore no corrective.

## Android

Per `docs/android-architecture.md` and CLAUDE.md:

- Built-in presets live under `templates/response_presets/` and resolve through
  `prompts.templates_dir()`, inheriting the `GRIMOIRE_TEMPLATES` indirection that
  `templates/styles/` already relies on.
- User presets live under `<GRIMOIRE_HOME>/response_presets/` via `store.paths`.
- No new dependencies — measurement is stdlib `re` and `str.split`.
- Route models stay pydantic v1/v2-agnostic.

## Testing

**Backend.** The cascade is where the bugs will be, so it carries the most weight:

- `resolve`: the per-field cascade across all four scopes; loose override beating
  its own scope's preset; fallthrough on a missing or invalid preset; the `none`
  sentinel clearing a style; a complete dict plus per-field provenance returned in
  every case.
- **Style survival, exhaustively.** A narrower length-only preset must never wipe a
  broader style, whichever way the broader style arrived. Cover the cross-product:
  a scene-level built-in length preset over (a) a campaign *loose* `style_id`,
  (b) a campaign *preset* supplying a style, (c) a global `default_style_id`,
  (d) a global preset supplying a style. Case (d) is the one an earlier draft of
  this design got wrong.
- Migration: a store with only legacy `style_id` / `default_style_id` resolves to
  the same style it does today, plus the `standard` budget — **including** a scene
  carrying a stale reference to a deleted style over a valid campaign style, which
  must still resolve to the campaign's.
- `response_presets`: read/write/list, built-in immutability, both-forms rejection
  on write, unknown `length_preset` invalidating a record *without* activating its
  explicit keys, partial explicit form supplying only the knobs present, malformed
  knobs treated as absent rather than completed.
- **Style-only (neither-form) preset**: accepted on write, and over a broader named
  length preset it must supply its style while leaving all five knobs to resolve
  outward — the case an earlier draft left implementation-dependent.
- **Turn boundaries**: several consecutive director / empty-send generations, which
  persist no user message between them, must measure as separate turns — the case
  that breaks any role-based segmentation. Plus: a budget-compliant reply followed
  by each of the four transition messages must **not** trigger a `blocks`
  correction; `turn_sizes` survives message edits and is popped by
  `remove_trailing_assistant_run`; a scene with no `turn_sizes` renders no
  corrective at all.
- **Speaker canonicalization**: a character writing blocks as `Winifred` and
  `Winifred Vance` counts as one speaker for both `speakers` and
  `blocks_per_speaker`; an unresolvable label counts as itself.
- `length_drift`: turn segmentation, roll-line exclusion, fence stripping,
  narrator-is-not-a-speaker, per-turn cap evaluation (explicitly: a 5/2/2 speaker
  window with a cap of 3 **does** trigger), fewer-than-three turns, zero turns,
  clearing only after 3 compliant turns.
- **No-oscillation regression**: with a 100-word budget, the sequence
  130/130/130 → 130/130/100 → 130/100/150 must keep the corrective **on**
  throughout. Under a mean-driven signal the middle window clears at 1.20× and the
  third re-triggers at 1.27×; under the specified max-driven signal it stays on.
- **Closed-loop check**: a synthetic transcript of budget-sized blocks whose *count*
  grows each turn must trigger the total-words corrective — the regression test for
  the split-into-more-blocks loophole.
- Templates: budget section text across `blocks_per_speaker` values; the three word
  tiers; each structural line appearing only when its own rule is violated.
- `context`: new section registered in `_SECTIONS`; post-history message carrying
  the corrective when card instructions are empty.
- Routes: CRUD including 400 on built-in edit/delete and on both-forms bodies;
  scope GET/PUT round-trip; a send carrying a one-shot override.
- `/usage`: reports **indirectly** affected scopes, not just direct references —
  a campaign-level preset with N inheriting scenes reports all N, and a
  global-default preset reports affected campaigns and scenes. Post-deletion values
  are the *re-resolved* ones (a scene inheriting a campaign preset's style), not a
  blanket `standard`.

Store isolation via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)` as usual.

**Frontend.**

- `ResponsePresetPicker`: preset select, overrides disclosure, effective-values
  readout with provenance, inherited values shown as placeholders, **Save as
  preset…**.
- `ResponsePresetsView`: the CLAUDE.md list/detail checks — clicking a row shows
  the read-only view with no `textarea`, **Edit** reveals the form, **+ New** opens
  the form directly. Plus the delete confirmation listing affected scopes with
  their post-deletion effective values.
- Composer chip: shows the resolved name, takes a one-shot pick, reverts after send.

## Build order

One coherent feature, but it stages cleanly, and each stage is independently
useful and independently verifiable:

1. **Budget plumbing end to end, no UI.** `store/lengths.py`, `response_presets.py`
   with the four built-ins, `resolve`, the `response_budget.j2` section, and
   `_SECTIONS` registration. At this point every scene renders a `standard` budget
   and existing styles still resolve identically — verifiable purely by tests and
   the token breakdown.
2. **The counterweight.** Two transcript-integrity changes first — `scenes.
   TRANSITION_SPEAKER` at the four synthetic message sites, and
   `scenes.append_reply` recording `turn_sizes` — then `length_drift.py`,
   `length_correction.j2`, and post-history plumbing on top. This is the piece that
   solves the stated problem, and it works before any picker exists. The two
   transcript changes are worth landing and testing on their own: measurement is
   worthless if turn boundaries are wrong, and every false correction the design
   could produce traces back to them.
3. **Scope settings and API.** The `/response` endpoints, retirement of the
   `/style` endpoints, `ResponsePresetPicker` at all three scopes.
4. **Saving and managing presets.** `ResponsePresetsView`, **Save as preset…**,
   duplicate, delete-with-usage.
5. **Turn override.** The composer chip and the send-request payload.

Stages 1 and 2 together deliver the actual fix; 3–5 deliver the control surface.

## Review notes

Points raised in adversarial review and deliberately **not** adopted, recorded so
they are not re-litigated:

- **A deprecation window for the retired `/style` endpoints.** The standard concern
  is version-skewed clients receiving 404s. It does not apply: `main.py` mounts the
  built frontend from `dist_dir()` via `StaticFiles`, and the Android APK packages
  backend and frontend verbatim into one artifact. Backend and client are always
  the same version by construction, and there are no external API consumers — a
  repo-wide search finds no non-frontend callers. Keeping deprecated adapters would
  preserve exactly the dual write path the change exists to remove.
- **Tombstones or snapshots for deleted presets.** Rejected in favour of a usage
  count in the delete confirmation; rationale under *Deleting a preset in use*.

## Out of scope

Recorded as follow-ups, not built:

- An under-budget corrective (pushing replies longer when they run thin).
- Per-character length multipliers — a terse NPC and a verbose one. Considered and
  set aside; per-character voice is arguably a character-card concern.
- A separate narration budget distinct from character blocks.
- History anchor-trimming — compressing over-budget past turns in the projected
  history. Attacks the root cause hardest but degrades what the model sees of its
  own transcript, with knock-on risk to continuity, absorb, and export.
- Post-generation retry when a reply lands over budget.
- A configurable drift window.
- A composer drift readout (`1640w↗/300`), which would make the feedback loop
  visible but is not needed to make it work.

## Extensibility

The design is additive along both axes. New response-preset fields — POV,
narration voice, a natural-prose toggle — drop into the preset frontmatter, and
new loose-override keys drop into the scope frontmatter. The per-field "a preset
supplies only what it specifies" rule means a new field slots into the existing
resolution algorithm without changing it, and without disturbing existing preset
files or scope settings.
