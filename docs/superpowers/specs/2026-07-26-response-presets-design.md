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
the bloat.

## Solution overview

Two layers of data plus two prompt surfaces.

**Length presets** are the built-in numeric vocabulary — four named bundles of the
three knobs above. **Response presets** are saveable named records that pair a
prose style with a length (either a named length preset or explicit numbers).
Response presets resolve across four scopes — turn, scene, campaign, global — with
per-field overrides available at any scope.

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

| | Terse | Brisk | Standard | Cinematic |
|---|---|---|---|---|
| `words` — target words per block | 80 | 160 | 280 | 450 |
| `paragraphs` — max paragraphs per block | 1 | 2 | 3 | 5 |
| `speakers` — max distinct speaking characters per reply | 2 | 3 | 4 | 5 |
| `repeats` — a character may take multiple blocks | no | no | yes | yes |

`**Grimoire:**` narration is a block and obeys the `words` and `paragraphs`
budget, but does **not** count against `speakers` — it is not a character.

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
words: 110
paragraphs: 1
speakers: 2
repeats: no
---
```

Field rules:

- `length_preset` and the explicit quartet (`words`, `paragraphs`, `speakers`,
  `repeats`) are **mutually exclusive**. If `length_preset` is present and names a
  known preset, the explicit fields are ignored. If it is absent or unknown, the
  explicit fields are used, each falling back to `standard`'s value when missing
  or malformed.
- `style_id` may be empty — a length-only preset.

**Shipped built-ins**: `terse`, `brisk`, `standard`, `cinematic` — each naming the
matching length preset with `style_id` empty. Out of the box the preset list
therefore reads as a plain length picker; combined presets like the two examples
above are things the user saves.

### Scope storage

Each of the three persistent scopes stores six flat frontmatter keys. Empty string
means *inherit*.

| key | meaning |
|---|---|
| `response_preset` | id of a response preset, or empty |
| `style_id` | loose style override — **spelled `default_style_id` at global scope** |
| `length_words` | loose knob override |
| `length_paragraphs` | loose knob override |
| `length_speakers` | loose knob override |
| `length_repeats` | loose knob override (`yes` / `no`) |

- **global** — `config.md` frontmatter, via `store/config.py`. `default_style_id`
  is already in `_CONFIG_KEYS`; the other five keys are added to it.
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
mirroring the shape of `styles.resolve_style`. Two steps:

1. **Base.** Walk turn → scene → campaign → global for the first scope with a
   non-empty `response_preset` that names a preset which exists. Its `style_id`
   and length values are the base. If no scope names a resolvable preset, the base
   is the `standard` length preset with no style.
2. **Overrides.** Apply loose overrides from **the scope the base came from and
   narrower only**, narrowest winning. When the base came from the fallback rather
   than a named preset, all four scopes' overrides apply.

Step 2's scoping is the important rule and the one worth testing hardest. Naming a
preset at a scope means *start fresh from these numbers here*, so a stale global
`length_words: 90` cannot quietly haunt a scene the user just set to Cinematic.
Without it, "why is my Cinematic scene writing 90-word blocks" becomes an
unanswerable support question.

`resolve` always returns a **complete** dict — `style_id` (possibly empty) plus all
four knobs as valid values. This is mandatory, not stylistic: the Jinja environment
runs with `StrictUndefined`, so a missing template var is a hard render failure in
the middle of a scene turn.

### Migration

Existing installs have `default_style_id` in `config.md` and `style_id` on
campaigns and scenes, and no `response_preset` anywhere. Under the resolution rules
above that reads as "no scope names a preset, so base = `standard` + no style, then
all scopes' loose overrides apply" — which yields exactly the style each scope
already had, plus a `standard` length budget.

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
Each block runs about 160 words and at most 2 paragraphs. Narration
(**Grimoire:**) follows the same budget.
At most 3 characters act or speak in this reply. Give each exactly one
block — do not return to a character you have already written.
```

The final line flips when `repeats` is on: *"A character may take a second block
when a genuine back-and-forth calls for it."*

Word counts are phrased as targets (*"about 160"*), never as hard caps. A hard cap
makes models truncate mid-thought; a target makes them compose shorter.

### Adaptive corrective

New `templates/scene/length_correction.j2`, appended to the **post-history system
message** — the last message before generation, and thus the closest available
counterweight to the transcript anchor.

The block renders when **any** of the three rules is measurably violated; each
rule contributes its own lines, and a rule within tolerance contributes nothing.
A reply whose blocks are the right length but which crowds in six speakers gets a
corrective containing only the speaker line.

Approximate text for a word-budget violation:

```
Your recent replies have run long: the last 3 turns averaged 510 words
per block against a budget of 160 — 3.2× over. Cut hard; this reply must
land near 160 words per block. Trim description first, then dialogue
tags. Do not split the same volume of prose across more blocks.
```

The final sentence is deliberate anti-gaming: the obvious cheat against a per-block
budget is more blocks.

Speaker-cap and repeat-block overshoots render their own lines in the same block:

```
The last 3 turns averaged 5.0 speaking characters against a cap of 3.
Characters took repeat blocks in 2 of the last 3 turns; the cap is one each.
```

**Word-budget tiers**, so mild drift is not shouted at:

| drift ratio | rendered |
|---|---|
| < 1.25× | nothing |
| 1.25× – 1.75× | "Trim toward the budget." |
| ≥ 1.75× | "Cut hard." |

The other two rules are not tiered — they are caps, not targets, so they are
either met or not:

- the **speaker line** renders when the mean distinct-speaker count across the
  window exceeds `speakers`;
- the **repeat line** renders when `repeats` is off and at least one turn in the
  window contained a repeated speaker.

All three evaluate independently of each other.

**Not doing**: an under-budget corrective. The problem is growth, and nudging a
model to write *more* is what the static budget section already does well.

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

- **blocks** — assistant messages in the run, roll lines excluded.
- **words per block** — whitespace-split token count of each block's content, after
  stripping ` ```roll ` fenced bodies. `store/fence.py` already owns that grammar;
  measurement reuses its opener regex rather than restating it.
- **distinct speakers** — count of distinct non-`None` speakers. Narrator segments
  store `speaker: None`, so "narration does not count against the speaker cap"
  falls out of the existing data model for free.
- **repeats** — whether any single speaker holds more than one block.

**Window**: the last **3** completed assistant turns. A constant, deliberately not
a setting.

**Drift ratio** = mean words-per-block across every block in the window ÷ the
resolved `words` budget. Narration blocks are included in the mean, since they are
under the same budget.

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
- an expandable **Overrides** disclosure holding the style picker and the four
  knob fields,
- an **effective values** readout showing what actually resolves and which scope
  each value came from.

Unset override fields render their inherited value as a placeholder, so an empty
field visibly shows what it is inheriting rather than looking blank.

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
built-in styles behave.

## API

Mirrors the styles routes, including their conventions — built-in immutability
returns **400** with an explanatory detail, as `PUT /styles/{sid}` does today, not
409.

**Preset CRUD:**

- `GET /api/response-presets` — list
- `POST /api/response-presets` — create
- `GET /api/response-presets/{id}`
- `PUT /api/response-presets/{id}` — 400 on built-in
- `DELETE /api/response-presets/{id}` — 400 on built-in
- `POST /api/response-presets/{id}/duplicate`
- `GET /api/length-presets` — the four constants, so the picker can show the
  numbers behind a named length

**Scope settings** — the existing per-scope `/style` sub-resources are generalized
rather than supplemented, so there is exactly one write path per field:

- `GET/PUT /api/campaigns/{cid}/response`
- `GET/PUT /api/campaigns/{cid}/scenes/{sid}/response`
- global scope: the six keys join the existing config GET/PUT payload

`GET/PUT /api/campaigns/{cid}/style` and
`GET/PUT /api/campaigns/{cid}/scenes/{sid}/style` are **removed**, along with their
frontend callers. `style_id` is now one field of a bundle; two endpoints writing
one field invites divergence.

**Turn override** — the chat send endpoint gains an optional `response` payload
(preset id and/or knob overrides). Unpersisted, like the director note.

Route models stay plain `BaseModel` with plain fields, dumped via `routes._dump`,
per the pydantic v1/v2-agnostic rule.

## Error handling

Nothing here may break play. Every failure degrades to a working budget.

- A scope naming a **deleted or unknown preset** resolves as if that scope named
  nothing, falling through to the next scope. Deletion is therefore always
  permitted; the picker surfaces a dangling reference as *missing*. The
  alternative — blocking deletion of an in-use preset — would require scanning
  every campaign and scene on delete for a worse outcome.
- **Malformed knob values** (non-integer, negative, zero, unparseable boolean) are
  treated as unset and fall through the resolution chain.
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

- `resolve`: nearest-preset search across all four scopes; broader overrides
  correctly *dropped* when a narrower scope names a preset; all scopes' overrides
  applying when nothing names a preset; fallthrough on a missing or deleted preset;
  a complete dict returned in every case.
- Migration: a store with only legacy `style_id` / `default_style_id` resolves to
  the same style it does today, plus the `standard` budget.
- `response_presets`: read/write/list, built-in immutability, `length_preset` vs
  explicit-values precedence, malformed values falling back.
- `length_drift`: turn segmentation, roll-line exclusion, fence stripping,
  narrator-is-not-a-speaker, repeat detection, fewer-than-three turns, zero turns.
- Templates: budget section text with `repeats` on and off; the three corrective
  tiers; speaker and repeat violation lines appearing only when violated.
- `context`: new section registered in `_SECTIONS`; post-history message carrying
  the corrective when card instructions are empty.
- Routes: CRUD including 400 on built-in edit and delete; scope GET/PUT round-trip;
  a send carrying a one-shot override.

Store isolation via `monkeypatch.setenv("GRIMOIRE_HOME", tmp_path)` as usual.

**Frontend.**

- `ResponsePresetPicker`: preset select, overrides disclosure, effective-values
  readout, inherited values shown as placeholders, **Save as preset…**.
- `ResponsePresetsView`: the CLAUDE.md list/detail checks — clicking a row shows
  the read-only view with no `textarea`, **Edit** reveals the form, **+ New** opens
  the form directly.
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
   duplicate.
5. **Turn override.** The composer chip and the send-request payload.

Stages 1 and 2 together deliver the actual fix; 3–5 deliver the control surface.

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
- A composer drift readout (`510w↗/220`), which would make the feedback loop
  visible but is not needed to make it work.

## Extensibility

The design is additive along both axes. New response-preset fields — POV,
narration voice, a natural-prose toggle — drop into the preset frontmatter, and
new loose-override keys drop into the scope frontmatter. Neither disturbs the
resolution algorithm, existing preset files, or existing scope settings.
