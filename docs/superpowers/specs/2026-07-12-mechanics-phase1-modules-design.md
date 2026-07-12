# Mechanics Phase 1 — module registry, data contract, binding

Full design for Phase 1 of the Mechanics & Dice milestone (#822), superseding
the Phase-1 sketch in `2026-07-05-mechanics-dice-roadmap-design.md`. Phase 0
(entity kinds + typed fields) and Phase 2 (dice engine, `store/dice.py` +
`store/rolls.py`) are shipped. This document specifies the **complete module
data contract** — including formats that later phases implement — and scopes
Phase 1's implementation to the registry, binding, and expression evaluator.

Companion draft specs for Phases 3–8 (same date prefix) capture how this
contract changes each later phase; each still gets its own brainstorm when
picked up.

## Decisions (settled 2026-07-12)

| Decision | Choice | Why |
|---|---|---|
| Sheet types | A module defines **many named sheet types**, each targeting one entity kind | One system needs different sheets per splat/class (character types) and per object family (item types); a single `sheet.json` schema cannot express this. |
| Sheet schema structure | **Composition**: shared named field groups, referenced by sheet types | Sheet types in one system are ~80% identical; composition is DRY without inheritance's override/merge semantics. Checks can require groups instead of naming sheet types. |
| Sheet-type assignment | The **sheet declares its type** (`sheet_type` in the sheet file); chosen at sheet-creation time | Entities stay mechanics-agnostic; the same world character can be sheeted differently in different campaigns. |
| Sheetable kinds | characters (incl. PCs), items, locations, creatures, groups, **and lore** | Anything can carry stats; PCs use the same sheet types as characters. |
| Module binding | **World default + campaign tri-state override**: campaign `module:` absent ⇒ inherit world default; `none` ⇒ mechanics off; `<mid>` ⇒ that module | A mechanics-free world can host one campaign that opts into a system, and a system-default world can host a freeform campaign. |
| World starting sheets | Keyed by module id: `<world>/sheets/<mid>/…` | A world can hold starting sheets for any module regardless of its default; a second campaign on the same module seeds from them. |
| Sheets per entity | Exactly **one sheet per entity per campaign** | Changing a character's sheet type means replacing the sheet, not stacking sheets. |
| Pack file layout | Few big files: `module.md`, `sheets.json`, `checks.json`, `rules/*.md`, `content/<kind>/*.md` | Groups and the sheet types that reference them validate in one read; JSON is machine-validated, not prose; the Phase-8 authoring UI edits structurally anyway. |
| Rules activation | Frontmatter flags: `keys` (lorebook-style), `always`, `on_roll`, `sheet_types`; plus check-linked `rules:` | Reuses the existing lorebook keyword mechanism; `sheet_types` puts splat powers/class features in context exactly when such an actor is on stage. |
| Module sources | Built-ins in-repo at `store/modules/<mid>/`; user modules at `<GRIMOIRE_HOME>/modules/<mid>/` | Same split as calendars — but modules are **data packs, no code plugins**, so sharing a module never runs untrusted code. |
| Module identity | Directory name = module id (slug); manifest carries a freeform `version` string | Same convention as worlds. No compatibility machinery (YAGNI). |
| Authoring path (pre-Phase-8) | A repo skill, `create-mechanics-module`, walks through hand-authoring a pack | The module format is intricate enough that setup shouldn't be re-derived each time. |

## Module data contract

A module is a folder; the directory name is its id (`mid`, a slug).

```
<module root>/
  module.md          # manifest
  sheets.json        # field groups + sheet types
  checks.json        # named check definitions
  rules/*.md         # LLM rules text with activation frontmatter
  content/<kind>/*.md  # module-shipped entities, optionally statted
```

Later phases add `layout.json` + `theme.css` (Phase 6, per sheet type). All
files except `module.md` and `sheets.json` are optional.

### `module.md` — manifest

Frontmatter: `name` (display name), `description` (one-liner), `version`
(freeform string), `dice` (the system's habitual roll in `store/dice.py`
notation, e.g. `1d20` or `5d10 t6` — the fallback when a check doesn't
specify). Body: freeform authoring notes; not fed to the LLM.

### `sheets.json` — groups and sheet types

```json
{
  "groups": {
    "attributes": {
      "label": "Attributes",
      "fields": [
        {"key": "strength", "label": "Strength", "type": "dots", "max": 5, "default": 1}
      ],
      "derived": {"str_mod": "floor((strength - 10) / 2)"}
    }
  },
  "sheet_types": {
    "warden": {
      "label": "Warden",
      "kind": "characters",
      "groups": ["attributes", "abilities"],
      "fields": [{"key": "resolve", "type": "dots", "max": 10}],
      "derived": {"guard_pool": "dexterity + melee"}
    }
  }
}
```

**Field descriptor**: `key` (slug, unique within the sheet type's assembled
field set), `label`, `type`, plus type-specific extras:

| type | meaning | extras |
|---|---|---|
| `number` | integer scalar | `default`, optional `min`/`max` |
| `dots` | small rated scalar (rendered as dots in Phase 6) | `max` (required), `default` |
| `track` | boxes that check off (damage track) | `max` (required) |
| `resource` | mutable current/max pair (HP, willpower) | `max` (required), `default` (initial current) |
| `text` | freeform string | — |
| `list` | list of strings (merits, gear) | — |

`resource` and `track` are the *mutable during play* markers: Phase 5's
absorb validation proposes deltas only against fields of these types.

**Sheet type**: `label`, `kind` (one of `characters`, `items`, `locations`,
`creatures`, `groups`, `lore` — `characters` covers PCs), `groups` (ordered
list of group refs), `fields` (own fields), `derived`.

**`derived`** maps a name to an expression (see Expression language).
Computed on read, never stored. Allowed at group level (shared math like
ability modifiers) and sheet-type level.

**Validation** (module load time): group refs exist; field keys unique across
a sheet type's groups + own fields; derived names don't collide with field
keys; every derived expression parses and references only names reachable in
that scope (a group's derived sees its own fields; a sheet type's derived
sees its full assembled field set plus group-derived names).

### `checks.json` — named checks

```json
{
  "guard_reflexes": {
    "label": "Guard + Reflexes",
    "roll": "pool(guard_pool) t6",
    "requires": ["attributes", "abilities"],
    "rules": ["combat-basics"]
  }
}
```

- `roll` — `store/dice.py` notation with embedded expressions, resolved
  against the acting entity's sheet at roll time.
- `requires` — group refs the formula needs; a check is offered only for
  actors whose sheet type includes all of them. Validation confirms every
  name in `roll` is reachable given `requires` (plus sheet-type-level names
  are checked when the check fires).
- `rules` — rules-doc slugs pulled into the continuation call when this
  check resolves (Phase 4).
- `outcomes` (optional) — explicit tier list overriding the engine's default
  interpretation (margin vs target for flat rolls, successes counted for
  pools). Exact tier schema is settled in the Phase 4 brainstorm; Phase 1
  validates it as an opaque optional list.

Check ids are the addressable names the roll-request protocol (Phase 4)
uses: the LLM asks for `guard_reflexes`, never composes notation itself.

### `rules/*.md` — LLM rules text

Markdown docs, slug = filename stem. Frontmatter flags (combinable):

- `keys: [...]` — keyword-activated against recent scene text, reusing the
  lorebook activation mechanism.
- `always: true` — in context whenever the module is bound (the core digest).
- `on_roll: true` — injected into the continuation whenever a roll resolves
  (dice-interpretation rules: botches, crits, successes).
- `sheet_types: [...]` — loaded when an actor with that sheet type is in the
  scene (splat powers, class features).

Docs with no flags load only when named by a check's `rules:` list.

### `content/<kind>/*.md` — module content

Same shape as world entities (markdown + frontmatter). An entry may
additionally carry `sheet_type: <type>` and `fields: {...}`, validated like
a sheet — module content ships pre-statted (a treasure with its powers, a
monster with its stat block). How content merges into browsing/play is
Phase 7; Phase 1 just validates it.

## Expression language

The load-bearing new primitive, implemented in `store/expressions.py` and
reused by derived fields, check formulas, and (Phase 7) creation budgets.

- Parsed with Python's `ast` module into a whitelisted node set: numeric
  literals, names, `+ - * /` (true division) and `//`, unary minus,
  comparisons, `and`/`or`/`not`, ternary `x if c else y`, and calls to
  exactly `min`, `max`, `floor`, `ceil`, `abs`.
- Names resolve to field values in the evaluation scope. For a `resource`
  field, `name` is the current value and `name_max` the maximum. For
  `track`, `name` is the number of checked boxes. `list` and `text` fields
  are not addressable in expressions.
- Unknown names are a **load-time validation error**, never a roll-time
  surprise.
- Anything outside the whitelist (attributes, subscripts, comprehensions,
  lambdas, strings, f-strings, calls to other names) fails parse with an
  error naming the offending construct. Never `eval`.
- Pure stdlib, pydantic-free, no filesystem access — Android-safe.

## Binding and resolution

- `world.md` gains optional `module: <mid>` — the world default.
- `campaign.md` gains tri-state `module:`: **absent** ⇒ inherit the world
  default; **`none`** ⇒ mechanics explicitly off; **`<mid>`** ⇒ that module.
- `store/modules.py::resolve(campaign_id) -> str | None` is the single
  authority; every mechanics feature keys off it. Resolved `None` ⇒ zero
  mechanics UI, zero context sections, zero new behavior (#822's null
  fall-through). Existing worlds/campaigns have no `module:` key and resolve
  to `None`.
- A binding that names a missing/invalid module resolves to `None` and the
  UI surfaces a warning rather than erroring.

**UI**: the new-campaign wizard offers a module picker (default: inherit);
the campaign configuration page gets a Mechanics setting (editable after
creation); the world editor gets the default-module setting.

## Sheet storage (contract here, implementation Phase 3)

- Campaign sheets (live, mutable): `<campaign>/sheets/<kind>--<id>.json`.
- World starting sheets: `<world>/sheets/<mid>/<kind>--<id>.json`.
- File shape: `{"sheet_type": "warden", "fields": {...}}`. Derived values
  computed on read. Writes validate against the resolved module.
- **Seeding**: on campaign create, if the campaign resolves to module M and
  `<world>/sheets/M/` exists, those files copy in — sheets are campaign-owned
  mutable state (like `playstate.py`), *not* overlay-read world content;
  damage in one campaign must never bleed into another.
- Changing a campaign's module later does **not** re-seed; existing sheets
  that fail the new module's schema are flagged invalid, not deleted.
- **Unsheeted indicator** (Phase 3 UI): an entity is *sheetable* when the
  resolved module has ≥1 sheet type targeting its kind, *unsheeted* when no
  sheet file exists. Campaign level: indicator on entity rows/detail. World
  level: the same coverage view, per selected module (defaulting to the
  world default when set).

## Phase 1 implementation scope

1. **`store/modules.py`** — pack loader + validator (manifest, `sheets.json`
   cross-references, `checks.json`, rules frontmatter, content stat blocks);
   module list/get merged from built-ins (`store/modules/` in-repo, path via
   `store.paths`/dist-safe resolution) and the user library
   (`<GRIMOIRE_HOME>/modules/`); create/delete for user-library modules
   (create = scaffold a minimal valid pack). `resolve()` per Binding above.
2. **`store/expressions.py`** — the evaluator, specified above.
3. **Binding** — `module:` keys, resolver, wizard + campaign-config +
   world-editor UI.
4. **Routes + frontend** — pydantic-v1/v2-agnostic models dumped via
   `routes._dump`; a Modules library page following the list/detail pattern,
   **read-only** (manifest, sheet types with assembled fields, checks, rules
   docs and their activation); module pickers in wizard/config. No authoring
   UI (Phase 8).
5. **Reference modules** — two built-ins proving the contract from opposite
   directions, each with ≥2 character sheet types, ≥1 item sheet type,
   shared groups, group- and type-level derived fields, every rules
   activation flag, and ≥1 statted content entry:
   - `d20-basic` — flat d20 + modifiers vs DC.
   - `pool-basic` — d10 dice pools vs target number, successes counted.
6. **`create-mechanics-module` skill** — repo skill (alongside
   `ingest-campaign-log`) that interviews the user about their system (dice
   habit, stat structure, character types, object families), scaffolds the
   pack step by step (`module.md` → groups → sheet types per kind →
   `checks.json` → `rules/*.md` with activation flags → optional statted
   content), validates via `store/modules.py` after each step, and finishes
   with the module visible in the library UI. Updated as later phases add
   formats (`layout.json`, Phase 6).

**Not in Phase 1**: sheet storage/editor and the unsheeted indicator
(Phase 3); context injection and the roll fence (Phase 4); absorb validation
(Phase 5); pretty sheet rendering (Phase 6); content browsing (Phase 7);
authoring UI (Phase 8).

## Testing

- **Evaluator**: acceptance table (arithmetic, ternary, min/floor, resource
  `_max` names) and a rejection case per forbidden construct; unknown-name
  detection.
- **Loader/validator**: both reference modules validate clean (they are the
  contract's fixtures); one broken-pack fixture per distinct validation
  error (missing group ref, duplicate field key, derived name collision,
  unparseable expression, unknown name in a check roll, bad activation
  frontmatter, invalid content stat block).
- **Resolver**: tri-state matrix (world default × campaign absent/none/mid),
  missing-module fallback to `None`.
- **Routes**: `GRIMOIRE_HOME` isolation (`monkeypatch.setenv`), user-library
  CRUD, merged listing, campaign/world binding round-trips.
- **Frontend (vitest)**: Modules page — row click shows read-only detail
  (no edit affordance); wizard and campaign-config pickers show
  inherit/none/module options and persist.
- **End state**: create a campaign, bind `pool-basic` via the config UI,
  confirm the resolver reports it; confirm a module-less campaign shows no
  mechanics UI anywhere.

## Privacy note

All names in module fixtures, tests, and docs are invented (reference
modules use generic fantasy/occult placeholders); real world, campaign, or
character names never appear, per the repo privacy rule.
