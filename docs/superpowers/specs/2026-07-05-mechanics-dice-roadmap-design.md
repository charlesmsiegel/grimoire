# Mechanics & Dice — roadmap design

> **Amended 2026-07-12**: the Phase-1 data contract is now fully specified in
> `2026-07-12-mechanics-phase1-modules-design.md`, which supersedes this
> document where they disagree — notably: modules define **multiple sheet
> types** per entity kind (`sheets.json` with shared field groups, replacing
> the single `sheet.json`); the module binds as a **world default with a
> campaign tri-state override** (not campaign-only); world starting sheets
> are keyed by module id; all entity kinds (including lore) are sheetable;
> rules docs gain `always`/`on_roll`/`sheet_types` activation. Draft specs
> for Phases 3–8 (same date prefix) update each phase sketch accordingly.

Roadmap for milestone 18 (Mechanics & Dice, issues #822–829) plus #693
(Item/Faction/Monster entity kinds). This document fixes the architecture
spine and sequences the milestone into phases; each phase gets its own
brainstorm → spec → plan → implementation cycle when picked up.

## Goals

1. **Modular mechanics** — Forgotten Realms runs on a D&D-ish system, WoD:
   London on a Storyteller-ish system, future worlds on game systems that
   don't exist yet.
2. The **LLM accurately makes most rules calls**.
3. **Real randomness** — dice at least as random as Python's `random` module
   (the LLM never invents roll results).
4. **Character sheets** that impact play, stored in the store.
5. Sheets **displayed properly** in the UI, themed per system.

## Decisions (settled 2026-07-05)

| Decision | Choice | Why |
|---|---|---|
| Module format | Declarative data pack + safe expression language; no code plugins | New systems are authored as data (matches the markdown/JSON store philosophy); makes the #829 authoring UI feasible; sharing a module never means running untrusted code. The LLM covers exotic subsystem logic via rules text. |
| Rules adjudication | LLM referees, engine computes | The LLM decides *when* a check is warranted and *which* check it is (guided by module rules text in context); the engine does all arithmetic and dice, so LLM math errors cannot leak into outcomes. |
| Roll request mechanism | In-band roll tag in the narration stream | The OpenRouter client is plain streaming text and users pick arbitrary (often RP-tuned) models without tool-calling support. A structured fence works with any text model and costs nothing on roll-free turns. |
| Sheet scope | All actors, campaign-level documents | Stats mutate during play (HP, XP, conditions), so sheets are mutable campaign state like `playstate.py`, not versioned authored content. World-level starting sheets seed on copy-on-create. Actors without sheets are fine (null fall-through). |
| Proving modules | Both minimal, early | A d20+modifier-vs-DC system and a dice-pool+successes system stress the abstraction in opposite ways; two structurally different systems is the only real proof the module format generalizes. |

## Architecture spine

New store concepts follow existing conventions: markdown+frontmatter for
prose, JSON for structured mutable state, generic CRUD in the style of
`entities.py`.

### Mechanics module

A folder in a global library `~/.grimoire/modules/<mid>/`:

- `module.md` — manifest (name, description, dice defaults).
- `sheet.json` — sheet schema: field groups, field types (number, dots,
  track, text, list, resource), **derived fields as expressions** (e.g.
  `floor((str - 10) / 2)`, `dex + firearms`).
- `checks.json` — named check/roll definitions: dice notation, which sheet
  fields feed the formula, outcome tiers (crit/success/partial/fail, or
  successes-counted for pools).
- `rules/*.md` — rules text for the LLM, keyed/owned like lore so the context
  builder can activate relevant sections.
- `content/<kind>/*.md` — module content (spells, items, monsters) in the
  same shape as world entities.
- `layout.json` + `theme.css` — sheet display (Phase 6).

### Expression language

A tiny safe evaluator over a whitelisted Python AST subset — arithmetic,
comparison, min/max/floor/ceil, names resolving to sheet fields. Never
`eval`. This is the load-bearing new primitive; it is specified fully in
Phase 1 and reused by derived fields, check formulas, and (later) the
creation wizard's budgets.

### Campaign binding

`campaign.md` gains `module: <mid>`. Absent/null ⇒ every mechanics feature
disappears — the `null` fall-through of #822. Existing campaigns are
unchanged.

### Dice engine

Notation parser (`NdM+k`, keep/drop, pools with target number, exploding),
seeded `random.Random` per roll, append-only roll log `<campaign>/rolls.json`
storing seed + notation + result so any roll is **replayable** (#824).

### Sheets

`<campaign>/sheets/<kind>--<id>.json` for any actor (PC, NPC, later monster),
validated against the module schema; derived fields computed on read, never
stored. Optional world-level starting sheets sync like other content.

### Context integration

New sections in `templates/scene/system.j2` + `context.py::_SECTIONS`, all
empty when the module is null: a rules digest (activated `rules/*.md`),
present-actor sheet summaries (derived values precomputed), and a
response-format section teaching the roll tag.

### Turn loop: two-phase generation

The model emits a fenced ` ```roll {...}``` ` block and stops when a check is
warranted. The backend detects the fence in the stream, cuts generation, and
surfaces a proposal chip (**accept / modify / decline**, #825). On accept the
engine resolves (sheet fields → expressions → seeded roll → outcome tier) and
a continuation call narrates the outcome. Decline ⇒ the continuation is told
to proceed without a check.

### Validation (#826)

When a module is bound, the absorb pass also checks the transcript against
sheets + the roll log (claims of unrolled outcomes, spent resources, HP
changes) and emits **proposed sheet deltas** into the existing staged-review
flow (`absorb.py` → `changes.py`).

## Phases

Each phase is independently shippable.

- **Phase 0 — Entity kinds: items, factions, monsters (#693).** Extend
  `ENTITY_KINDS`/`SYNCED_KINDS` in `entities.py`, routes, EntityEditor tabs,
  sync. Independent of mechanics; unblocks monster sheets and content
  browsers. Small — first.
- **Phase 1 — Module registry + campaign binding + data contract (#822).**
  Module library CRUD, manifest format, `module:` key on campaigns (wizard +
  campaign config UI), and the expression evaluator with the sheet/checks
  schema formats fully specified. Ships with the two skeletal reference
  modules (5e-ish, Storyteller-ish) as fixtures — the contract's test bed.
- **Phase 2 — Dice engine (#824).** Notation parser, seeded rolls, outcome
  tiers, roll log + replay API. UI: a manual "Roll" affordance in the play
  view writing results into the scene — useful before LLM refereeing exists,
  and it exercises the log.
- **Phase 3 — Character sheets (#823).** Sheet storage/validation/read-update
  API, derived-field computation, world starting sheets + campaign seeding,
  and a functional generic sheet editor/viewer (plain field groups — pretty
  rendering is Phase 6). Flesh both reference modules out to real (still
  small) sheet schemas.
- **Phase 4 — Play integration (#825) — the payoff.** Context sections (rules
  digest, sheet summaries, roll-tag instructions), stream fence detection +
  generation cut, proposal chip with accept / modify / decline, engine
  resolution, continuation turn. After this phase a campaign plays with
  LLM-refereed, engine-resolved, truly random checks.
- **Phase 5 — Narrated-event validation (#826).** Extend absorb with
  mechanics awareness: flag narration contradicting sheets or the roll log;
  propose sheet deltas (damage, resource spend, XP) through the existing
  review-then-commit flow.
- **Phase 6 — Sheet display (#828).** Widget library (dot track, checkbox
  track, resource bar, stat grid, derived badge, list) + module `layout.json`
  + per-module `theme.css` rendering. Replaces the Phase-3 generic viewer;
  D&D sheets look D&D, WoD sheets look WoD.
- **Phase 7 — Content browsers + creation wizard (#827).** Browse module
  `content/` merged with world items/monsters (Phase 0); attach content to
  sheets (known spells, inventory). Character-creation wizard driven by
  module creation rules (point buys, dot budgets).
- **Phase 8 — Module authoring UI (#829).** In-app scaffold/edit of a module:
  manifest, sheet fields, checks, rules text, layout. Last — it needs every
  format stable first.

**Dependencies:** 0 and 1‖2 can proceed in parallel; 3 needs 1; 4 needs 2+3;
5 needs 4; 6 needs 3; 7 needs 0+3; 8 needs 1–6 stable.

## Milestone end-state verification

Create a campaign bound to the 5e-ish module, sheet a PC, play a scene under
the `verify` skill's mocked OpenRouter emitting a roll fence, accept the
proposal, confirm the roll-log entry + continuation narration; repeat a pool
roll under the Storyteller-ish module; confirm a module-less campaign shows
zero mechanics UI.
