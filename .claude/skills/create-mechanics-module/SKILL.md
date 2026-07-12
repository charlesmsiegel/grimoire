---
name: create-mechanics-module
description: Use when authoring a new game-mechanics module (data pack) for grimoire — interviews for the system's shape, scaffolds the pack, and validates each step.
---

# Authoring a mechanics module

A **module** is a data pack that teaches grimoire a tabletop system's stats, checks, and rules
text — dice pools, d20 + modifiers, whatever the table actually plays. It is pure data (JSON +
markdown), never code: sharing a module never runs untrusted logic. This skill hand-authors a
pack file by file, validating after every step. The in-app authoring UI (Phase 8 of the Mechanics
& Dice milestone) doesn't exist yet — until it does, this skill is the way modules get made.

Full format spec, including validation rules and rationale for every decision below:
`docs/superpowers/specs/2026-07-12-mechanics-phase1-modules-design.md` (sections "Module data
contract" and "Expression language"). Read it if anything here is ambiguous — this skill
summarizes the contract, the spec is the source of truth.

**Reference pack** — `backend/src/grimoire/store/builtin_modules/pool-basic/` is a complete,
validated example (d10 dice pools vs. a target number) covering every format below: shared
groups, two character sheet types, an item and a location sheet type, group- and type-level
derived fields, every rules-activation flag, and one pre-statted content entry. Keep it open
alongside this skill and copy its shapes rather than inventing your own.

## Privacy

Module content is generic rules data — dice math, stat names, rules text — and is fine to share
and commit. But per the repo-wide privacy rule (see `CLAUDE.md`), **never embed a real world,
campaign, or character name** in a worked example while authoring, even a throwaway one. Use
invented names (the reference packs use generic fantasy/occult placeholders like "Medium",
"Shifter", "Moonwell Talisman" — reuse that flavor or invent your own, never reuse something from
an actual campaign).

## Workflow

### 1. Interview

Before scaffolding anything, ask enough to know the pack's shape. Don't guess — a module authored
on a wrong guess means redoing `sheets.json` later. Cover:

- **Dice habit** — the system's default roll notation in `store/dice.py` syntax (`1d20`, `5d10
  t6`, `3d6`, …). This becomes `module.md`'s `dice:` field, the fallback when a check doesn't
  specify its own `roll`.
- **Attribute/ability structure** — what stat blocks does every character type share? (e.g.
  "three attributes, a list of trained skills.") This becomes one or more shared `groups`.
- **Character types** (splats, classes, archetypes) — do different kinds of character have
  *different* stats on top of the shared ones (a spellcaster's mana vs. a fighter's stamina)?
  Each becomes its own sheet type composed from the shared groups plus its own fields.
- **Object families** — do items, locations, creatures, or lore carry stats too (a weapon's
  damage, a haven's wards)? Each family that does gets its own sheet type(s) targeting that kind.
- **Mutable resources vs. static ratings** — which numbers change during play (HP, mana, a damage
  track) versus which are fixed until the character improves (an attribute rating, a skill dot)?
  This is the `resource`/`track` vs. `dots`/`number` split in the field-type table below, and it
  matters later: Phase 5's absorb validation only proposes deltas against `resource`/`track`
  fields.

### 2. Scaffold the module directory

Either call the scaffolding helper (creates `module.md` with `name`/`description`/`version`
frontmatter plus an empty `sheets.json`, and returns the module id — a slug of the name, deduped
against existing user and built-in ids):

```
backend/.venv/Scripts/python.exe -c "from grimoire.store import modules; print(modules.create_module('<Display Name>'))"
```

or `mkdir` the pack yourself and hand-write `module.md`. Either way it lands at
`<GRIMOIRE_HOME>/modules/<mid>/` — the **user library** (see `store.home()` in `CLAUDE.md` for how
`GRIMOIRE_HOME` resolves). Built-in modules live in-repo at
`backend/src/grimoire/store/builtin_modules/<mid>/` and are **read-only models** to study, not
edit in place — never hand-edit a built-in pack; copy its shape into a new user-library module
instead.

`module.md` frontmatter: `name` (display name), `description` (one-liner), `version` (freeform
string), `dice` (the habitual roll from the interview). Body is freeform authoring notes, never
fed to the LLM.

### 3. Author `sheets.json` — groups first

Groups are shared field sets referenced by multiple sheet types — DRY without inheritance's
override semantics. Write every group the interview surfaced before touching sheet types:

```json
{
  "groups": {
    "attributes": {
      "label": "Attributes",
      "fields": [
        {"key": "vigor", "label": "Vigor", "type": "dots", "max": 5, "default": 1}
      ],
      "derived": {}
    }
  },
  "sheet_types": {}
}
```

A group's `derived` (optional) computes group-local math (e.g. an ability modifier) visible only
to that group's own fields — see Expression language below.

### 4. Author `sheets.json` — sheet types per kind

Add one sheet type per character-type/object-family the interview named. Each has: `label`,
`kind` (one of `characters`, `items`, `locations`, `creatures`, `groups`, `lore` —
`characters` covers PCs too), `groups` (ordered list of group names to compose in), `fields` (its
own fields on top of the groups), and optional `derived` (sees the full assembled field set plus
every composed group's derived names).

Field keys must be unique across a sheet type's composed groups + its own fields; derived names
must not collide with field keys. Two more reserved-key rules (Phase 3): a field key may not be
an expression function name (`min`, `max`, `floor`, `ceil`, `abs`), and may not equal another
field's implicit resource-max name (`<key>_max` of a `resource` field in the same assembled
set). `store/modules.py`'s validator catches all of these at load time.

**Field-type table** (from the spec's "Module data contract" section):

| type | meaning | extras |
|---|---|---|
| `number` | integer scalar | `default`, optional `min`/`max` |
| `dots` | small rated scalar (rendered as dots later) | `max` (required), `default` |
| `track` | boxes that check off (a damage track) | `max` (required) |
| `resource` | mutable current/max pair (HP, willpower, essence) | `max` (required — the *default* maximum; each sheet stores its own current/max pair, since maxima vary per character) |
| `text` | freeform string | — |
| `list` | list of strings (merits, gear) | — |

`resource` and `track` are the fields play can mutate — flag them for anything that goes up or
down during a session; everything else is closer to a fixed rating.

Sheet *instances* exist as of Phase 3: campaigns store per-entity sheets (created/edited from
each entity's detail view via the Sheet section), and worlds can hold per-module starting sheets
that seed into new campaigns bound to that module — so a module you author here is immediately
usable end-to-end.

**Expression-addressability** (matters for `derived` and for `checks.json` below, per the spec's
"Expression language" section): a `resource` field named `essence` contributes **two** names to
expressions — `essence` (current value) and `essence_max` (its maximum); a `track` field
contributes just its checked-box count under its own name. `dots` and `number` fields contribute
their scalar directly. **`text` and `list` fields are not addressable** in any expression — don't
reference them in `derived` or `roll`. Unknown names are a load-time validation error, never a
roll-time surprise; the whitelist is numeric literals, names, `+ - * /` (true division), `//`,
unary minus, comparisons, `and`/`or`/`not`, ternary `x if c else y`, and calls to `min`, `max`,
`floor`, `ceil`, `abs` only — no attributes, subscripts, strings, or arbitrary calls.

### 5. Author `checks.json` — named checks

Each check is a named, addressable roll — the LLM asks for the check by name at roll time
(Phase 4), it never composes dice notation itself:

```json
{
  "vigor_brawl": {
    "label": "Vigor + Brawl",
    "roll": "{vigor + brawl}d10 t6",
    "requires": ["attributes", "abilities"],
    "rules": ["combat"]
  }
}
```

- `roll` — `store/dice.py` notation with `{expression}` placeholders (e.g. `1d20 + {str_mod}`,
  `{dexterity + melee}d10 t6`). Each placeholder evaluates against the acting entity's sheet and
  substitutes as an integer at roll time; the substituted string must parse as plain dice
  notation. Validation parses every placeholder and test-renders the template with a sample
  value.
- `requires` — group names the formula needs; a check is only offered to actors whose sheet type
  composes all of them. Every name used in `roll` must be reachable given `requires` (plus
  sheet-type-level names, checked when the check actually fires).
- `rules` — rules-doc slugs (filename stems from `rules/*.md`) pulled into context when this
  check resolves.
- `outcomes` (optional) — an explicit tier list overriding the engine's default margin/successes
  interpretation. Phase 1 validates it only as an opaque optional list; the schema settles later.

### 6. Author `rules/*.md` — LLM rules text

One markdown file per rules doc; the slug is the filename stem (`combat.md` → `combat`).
Frontmatter carries **activation flags**, combinable:

| flag | when it loads | typical use |
|---|---|---|
| `always: true` | whenever the module is bound to the campaign | the core digest — how the dice work, at a glance |
| `on_roll: true` | injected into the continuation whenever a roll resolves | dice interpretation — botches, crits, counting successes |
| `keys: [...]` | keyword-activated against recent scene text (lorebook mechanism) | situational rules triggered by in-fiction words ("fight", "shift") |
| `sheet_types: [...]` | loaded when an actor with that sheet type is in the scene | splat powers / class features specific to that character type |

A doc with **no** flags loads only when a check's `checks.json` `rules:` list names it — use that
for check-specific procedure text that doesn't need standing activation. Body is the markdown fed
to the LLM; keep it short and procedural, not prose-y lore.

### 7. Optional: statted content

If the module ships ready-made entities (a monster, a magic item), add
`content/<kind>/<id>.md` — same markdown + frontmatter shape as a world entity — and, if it
carries stats, a sidecar `content/<kind>/<id>.sheet.json`:

```json
{"sheet_type": "talisman", "fields": {"power": 3, "charges": {"current": 10, "max": 10}}}
```

Note the `resource` field's current/max pair shape (`{"current": ..., "max": ...}`) versus a
plain scalar for `dots`/`number`/`text`/`list` fields. The sidecar validates against the resolved
sheet type exactly like a campaign sheet file.

### 8. Validate after every step

Don't wait until the pack is "done" — run this after each step above, not just at the end, so an
error points at the change you just made rather than an accumulated pile:

```
backend/.venv/Scripts/python.exe -c "from grimoire.store import modules; print(modules.load_pack('<mid>')['errors'])"
```

Run it from the repo root. An empty list (`[]`) means the pack is fully valid — manifest,
`sheets.json` cross-references (group refs exist, no key collisions, every `derived` expression
parses and resolves), `checks.json` (placeholders parse, names reachable via `requires`), rules
frontmatter, and any content stat blocks. A non-empty list names every problem found, so fix them
all before moving on rather than re-running per fix.

If you're working from a git worktree rather than the primary checkout, the worktree has no
editable install / venv of its own — either run this from a checkout that does, or point
`PYTHONPATH` at the worktree's `backend/src` while invoking the primary checkout's interpreter,
e.g. (Git Bash) `PYTHONPATH=backend/src /path/to/primary/backend/.venv/Scripts/python.exe -c
"..."`. Also worth knowing: `load_pack` resolves `<GRIMOIRE_HOME>/modules/` via `store.home()`
(`CLAUDE.md`), so if you scaffolded into a non-default `GRIMOIRE_HOME` (tests, an alternate
library) the validation command needs that same env var set to find it.

### 9. Confirm it's visible in the app

Once `load_pack` returns `[]`, start grimoire (see the `run` skill if you need to launch it) and
open the Modules page — the new module should appear in the library list with its groups, sheet
types, checks, and rules readable (the library page is read-only in this phase; there's no
in-app editor yet). Then bind it to a world or campaign (world editor's default-module setting, or
a campaign's Mechanics config panel) to see it actually take effect.

## Minimal complete example

A tiny but complete worked example — one group, one sheet type composing it, one check. Modeled
directly on `pool-basic`; scale this pattern up per the interview's answers.

`sheets.json`:

```json
{
  "groups": {
    "attributes": {
      "label": "Attributes",
      "fields": [
        {"key": "grit", "label": "Grit", "type": "dots", "max": 5, "default": 1},
        {"key": "wits", "label": "Wits", "type": "dots", "max": 5, "default": 1}
      ]
    }
  },
  "sheet_types": {
    "wanderer": {
      "label": "Wanderer",
      "kind": "characters",
      "groups": ["attributes"],
      "fields": [
        {"key": "resolve", "label": "Resolve", "type": "resource", "max": 8}
      ],
      "derived": {"instinct_pool": "grit + wits"}
    }
  }
}
```

`checks.json`:

```json
{
  "instinct": {
    "label": "Instinct",
    "roll": "{grit + wits}d10 t6",
    "requires": ["attributes"]
  }
}
```

Note this check's `roll` spells out `grit + wits` directly rather than referencing the sheet
type's `instinct_pool` derived field — a check's placeholders are validated against its
`requires` groups at pack-load time, before any sheet type is in the picture, so only
group-level names (and group-level `derived`) are load-time-reachable there. A sheet-type-level
`derived` name like `instinct_pool` is real and usable elsewhere (e.g. displayed on the sheet),
but only becomes reachable in a `roll` once an actual actor/sheet type is bound at fire time —
don't rely on it validating at load time the way group names do.

That's a load-valid pack on its own (plus `module.md`); `rules/*.md` and `content/` are additive
from there. Verified against the real validator while writing this skill.

## Common mistakes

- Writing sheet types before groups — a sheet type's `groups` list references groups that must
  already exist in the same `sheets.json`; there's no forward-declaration.
- Referencing a `text` or `list` field inside a `derived` or `roll` expression — both are
  non-addressable and fail validation with an unknown-name error.
- Forgetting the `_max` suffix when a `derived`/`roll` expression needs a `resource` field's
  ceiling rather than its current value (`essence_max`, not `max_essence` or `essence.max`).
- Treating `requires` in `checks.json` as decorative — it's enforced: a check referencing a group
  name not listed in `requires` fails validation even if that group exists elsewhere in the pack.
- Hand-editing a built-in pack under `backend/src/grimoire/store/builtin_modules/` instead of
  copying its shape into a new module under `<GRIMOIRE_HOME>/modules/` — built-ins are the
  read-only reference models, not a starting point to mutate in place.
- Skipping validation until the whole pack is written — a single typo'd expression early on is
  much easier to place when you validated right after writing it.
