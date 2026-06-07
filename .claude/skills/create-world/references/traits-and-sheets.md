# Mechanics traits & sheets

## Where sheets live (and why this skill doesn't author them)

Mechanics are **campaign-scoped**, not world-scoped. The active module is chosen
in `campaign.yaml`; mechanical **sheets** live at
`campaigns/<id>/sheets/<kind>/<id>.<mechanics-id>.yaml` and are validated by the
module's `sheet_schema(entity_kind)`. A **library world has no mechanics
binding**, so it cannot carry sheets.

The module contract (`MechanicsModule` Protocol) provides `sheet_schema`,
`initialize_sheet(entity_kind, entity_id)`, `validate_sheet`, and
`character_creation_steps()`. Sheets are created at campaign time — by the
character-creation wizard, or surfaced as `missing_sheets` when a campaign
switches modules. None of that is a library-authoring action, so this skill does
not write sheets or touch `data/mechanics/`.

## What the skill CAN do: pre-author traits in `extras`

`extras` is the narrative-traits tier on every library entity (frontmatter is
SSOT). Its stated purpose includes **cross-mechanics consistency — "extras
travel when mechanics changes"** — and richer profiles for `mechanics: null`
campaigns. That makes `extras` the right home for the traits a sheet will later
draw from.

When the intended system is **obvious** (the user names it, or the genre makes a
system family clear), pre-author the trait data in `extras` so later sheet
creation has a ready source to map from. These extras are a **reference for the
creation wizard / author — they are not auto-applied** into a module sheet.

## Authoring `extras`

- **Keys:** snake_case, 1–40 chars, `[a-z0-9_]`. **Reserved prefixes — do NOT
  use:** `_internal_`, `mechanics_`, `system_`.
- **Values:** scalar (str / int / float / bool / null), a list of scalars, or a
  **flat** dict of scalars (one level only). Keep them concise (soft caps:
  ~200 chars/string, ~20 keys/entity).
- Bare scalars are accepted — no provenance wrapper needed when hand-authoring:

```yaml
extras:
  archetype: "duelist"
  attributes: { strength: 3, dexterity: 4, wits: 3 }
  notable_skills: ["fencing", "streetwise"]
  health_levels: 7
```

## Picking trait keys when the system is obvious

Prefer the target system's own vocabulary so mapping to a sheet is 1:1, and use
the **same key names across the whole cast** so the mapping is uniform.

- **D20 / fantasy:** `attributes: {strength, dexterity, constitution,
  intelligence, wisdom, charisma}`, `level`, `class`, `hit_points`.
- **World of Darkness / Storyteller:** `attributes: {strength, dexterity,
  stamina, charisma, manipulation, appearance, perception, intelligence,
  wits}`, a flat `disciplines` / `spheres` dict, `willpower`, `health_levels`.
- **PbtA:** `stats: {cool, hard, hot, sharp, weird}`, `harm`, `playbook`.

If the system is **not** obvious, author only system-agnostic narrative traits
(`archetype`, `notable_skills`, descriptive cues) and leave numeric stats out —
**don't guess a system**.

## "Other entities" too

The same applies beyond characters: items, locations, and factions all have an
`extras` field. Author trait data a sheet/content instance would consume (e.g. a
weapon's `damage`, a location's `defense_rating`) only when the system is
obvious; otherwise keep them narrative.

## When mechanics ARE installed

Authoring or validating actual module sheets is a campaign-scope, dev-time
action (the Library → Mechanics UI / `MechanicsAuthor` for the declarative parts;
the creation wizard for instances). This skill stops at library `extras` traits.
