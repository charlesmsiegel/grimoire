# Interview and scaffold

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
$PY -c "from grimoire.store import modules; print(modules.create_module('<Display Name>'))"
```

or `mkdir` the pack yourself and hand-write `module.md`. Either way it lands at
`<GRIMOIRE_HOME>/modules/<mid>/` — the **user library** (see `store.home()` in `AGENTS.md` for how
`GRIMOIRE_HOME` resolves). Built-in modules live in-repo at
`backend/src/grimoire/store/builtin_modules/<mid>/` and are **read-only models** to study, not
edit in place — never hand-edit a built-in pack; copy its shape into a new user-library module
instead.

`module.md` frontmatter: `name` (display name), `description` (one-liner), `version` (freeform
string), `dice` (the habitual roll from the interview). Body is freeform authoring notes, never
fed to the LLM.
