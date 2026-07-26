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

#### Length is a tagged union

The length half of a record is **exactly one of** two forms, and this is validated,
not merely documented:

- **Named form** — `length_preset: <id>`.
- **Explicit form** — the five knob keys.

Rules, stated exhaustively so two implementers cannot diverge:

- **Write path** (`POST` / `PUT`) rejects a record carrying both forms with 400.
  There is no normalization, no silent precedence.
- **Read path**, if `length_preset` is present, ignores the explicit keys
  *unconditionally* — including when `length_preset` names something unknown.
- A `length_preset` naming an unknown length preset makes the record **invalid**.
  An invalid record is never partially used: a scope naming it resolves as though
  it named nothing, and the management view flags it. Explicit values can therefore
  never spring to life because a name was mistyped or a preset was renamed.
- **Explicit form with missing or malformed knobs** completes those knobs from
  `standard` and stays valid. Hand-editing files under `<GRIMOIRE_HOME>` is a
  supported workflow, so a partially-written record degrades rather than breaking;
  the view flags the completed fields.

#### Style is optional and tri-state

`style_id` in a preset has three meanings, and the distinction matters — without it,
selecting a length-only preset would silently wipe an inherited style:

| value | meaning |
|---|---|
| absent / empty | **the preset has no opinion on style** — style keeps resolving up the chain |
| a style id | the preset supplies that style |
| `none` | the preset explicitly clears the style |

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

1. **Base.** Walk turn → scene → campaign → global for the first scope naming a
   **resolvable** response preset. A scope naming a missing or invalid preset is
   treated as naming nothing and the walk continues. If no scope names a resolvable
   preset, the base is the `standard` length preset supplying no style.

2. **A preset supplies only the fields it specifies.**
   - A valid preset always supplies all five length knobs (via either union form).
   - It supplies a style only when `style_id` is a style id or the `none` sentinel.
     Absent or empty means it supplies nothing for style.

3. **Overrides.** For each field independently, the narrowest scope-level override
   wins, subject to one restriction: a field **supplied by the base preset** accepts
   overrides only from the base preset's own scope or narrower. A field the base
   preset did **not** supply accepts overrides from every scope.

Rule 3's restriction is the important one and deserves the heaviest tests. Naming a
preset at a scope means *start fresh from these numbers here*, so a stale global
`length_reply_words: 90` cannot quietly haunt a scene the user just set to
Cinematic. Scoping it **per field** rather than wholesale is what keeps that
freshness from spilling into fields the preset never spoke to — the concrete
failure being: a campaign set to Gothic Horror, a scene set to the built-in
Cinematic preset, and the scene silently losing Gothic Horror because a
*length* choice reset a *style*. Under these rules it doesn't: `cinematic` supplies
no style, so the campaign's style override still applies.

`resolve` always returns a **complete** dict — `style_id` (possibly empty) plus all
five knobs as valid values. This is mandatory, not stylistic: the Jinja environment
runs with `StrictUndefined`, so a missing template var is a hard render failure in
the middle of a scene turn.

### Migration

Existing installs have `default_style_id` in `config.md` and `style_id` on
campaigns and scenes, and no `response_preset` anywhere. Under the rules above that
reads as "no scope names a preset, so the base is `standard` supplying no style;
since the base supplies no style, style overrides from every scope apply" — which
yields exactly the style each scope already had, plus a `standard` length budget.

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

Total-words violation:

```
Your recent replies have run long: the last 3 turns averaged 1,640 words
against a budget of 300 — 5.5× over. Cut hard; this reply must land near
300 words total. Trim description first, then dialogue tags.
```

Structural violations each render their own line:

```
Recent replies have exceeded 4 blocks; keep this one to at most 4, narration included.
Recent replies have exceeded 3 speaking characters; keep this one to at most 3.
A character has taken more than 1 block in a reply; give each character at most 1.
A block has run past 2 paragraphs; keep every block to at most 2.
```

**Word-budget tiers**, so mild drift is not shouted at:

| drift ratio | rendered |
|---|---|
| < 1.25× | nothing |
| 1.25× – 1.75× | "Trim toward the budget." |
| ≥ 1.75× | "Cut hard." |

Because the budget is on the *total*, splitting the same prose across more blocks
does not reduce the ratio — the loophole is closed structurally rather than by
asking the model not to exploit it. The `blocks` cap backs this up.

The four structural rules are **not** tiered — they are caps, not targets, so they
are either met or not. Each renders when **any single turn in the window** violates
it. Evaluating a cap against the window *mean* would be wrong: with a cap of 3,
turns of 5, 2, and 2 speakers average exactly 3 and would produce no correction
despite one turn plainly breaking the rule.

**Clearing** is automatic and needs no hysteresis: the window is the last 3 turns,
so every signal disappears once 3 consecutive compliant turns have rolled through.
A single compliant turn does not clear a violation, which is what prevents
oscillation between corrected and uncorrected states.

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

**Turn segmentation** reuses the existing notion: one turn's output is a maximal
run of assistant-side messages — the same run `scenes.remove_trailing_assistant_run`
operates on. Manual dice-roll lines (`scenes.ROLL_SPEAKER`) are skipped entirely;
they are interleaved into runs but are not model prose.

**Per turn:**

- **total words** — whitespace-split token count across every block in the run,
  narration included, after stripping ` ```roll ` fenced bodies. `store/fence.py`
  already owns that grammar; measurement reuses its opener regex rather than
  restating it.
- **blocks** — assistant messages in the run, roll lines excluded.
- **max paragraphs** — the largest paragraph count of any single block.
- **distinct speakers** — count of distinct non-`None` speakers. Narrator segments
  store `speaker: None`, so "narration does not count against the speaker cap"
  falls out of the existing data model for free.
- **max blocks per speaker** — the largest number of blocks held by any one speaker.

**Window**: the last **3** completed assistant turns. A constant, deliberately not
a setting.

**Signals:**

- **Word drift ratio** = mean *total words per turn* across the window ÷
  `reply_words`. This is the tiered signal.
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

Deleting a preset that scopes reference is permitted, but never silent. The delete
confirmation states the blast radius — *"Used by 2 campaigns and 5 scenes, and as
the global default. They will fall back to Standard."* — sourced from
`GET /api/response-presets/{id}/usage`, which scans campaign and scene frontmatter.
The scan runs only on delete, so its cost is irrelevant.

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
- `GET /api/response-presets/{id}/usage` — scopes referencing this preset
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
- An **unknown `style_id`** in a preset or override renders no style section,
  matching today's behavior when a style is deleted out from under a campaign.
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

- `resolve`: nearest-preset search across all four scopes; the per-field override
  restriction (a preset-supplied field rejecting broader overrides, a
  non-supplied field accepting them); **the specific case of a scene-level
  built-in length preset over a campaign-level style, asserting the style
  survives**; the `none` sentinel clearing a style; fallthrough on a missing or
  invalid preset; a complete dict returned in every case.
- Migration: a store with only legacy `style_id` / `default_style_id` resolves to
  the same style it does today, plus the `standard` budget.
- `response_presets`: read/write/list, built-in immutability, both-forms rejection
  on write, unknown `length_preset` invalidating a record *without* activating its
  explicit keys, explicit form completing missing knobs from `standard`.
- `length_drift`: turn segmentation, roll-line exclusion, fence stripping,
  narrator-is-not-a-speaker, per-turn cap evaluation (explicitly: a 5/2/2 speaker
  window with a cap of 3 **does** trigger), fewer-than-three turns, zero turns,
  clearing after 3 compliant turns.
- **Closed-loop check**: a synthetic transcript of budget-sized blocks whose *count*
  grows each turn must trigger the total-words corrective — the regression test for
  the split-into-more-blocks loophole.
- Templates: budget section text across `blocks_per_speaker` values; the three word
  tiers; each structural line appearing only when its own rule is violated.
- `context`: new section registered in `_SECTIONS`; post-history message carrying
  the corrective when card instructions are empty.
- Routes: CRUD including 400 on built-in edit/delete and on both-forms bodies;
  `/usage`; scope GET/PUT round-trip; a send carrying a one-shot override.

Store isolation via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)` as usual.

**Frontend.**

- `ResponsePresetPicker`: preset select, overrides disclosure, effective-values
  readout with provenance, inherited values shown as placeholders, **Save as
  preset…**.
- `ResponsePresetsView`: the CLAUDE.md list/detail checks — clicking a row shows
  the read-only view with no `textarea`, **Edit** reveals the form, **+ New** opens
  the form directly. Plus the delete confirmation showing usage counts.
- Composer chip: shows the resolved name, takes a one-shot pick, reverts after send.

## Build order

One coherent feature, but it stages cleanly, and each stage is independently
useful and independently verifiable:

1. **Budget plumbing end to end, no UI.** `store/lengths.py`, `response_presets.py`
   with the four built-ins, `resolve`, the `response_budget.j2` section, and
   `_SECTIONS` registration. At this point every scene renders a `standard` budget
   and existing styles still resolve identically — verifiable purely by tests and
   the token breakdown.
2. **The counterweight.** `length_drift.py`, `length_correction.j2`, post-history
   plumbing. This is the piece that solves the stated problem, and it works before
   any picker exists.
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
