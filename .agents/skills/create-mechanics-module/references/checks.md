# Checks, defaults and outcomes

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
  value. Two names are always in scope for `roll` regardless of `requires` — `{difficulty}` (the
  target number in play, from the check's own `difficulty`, `_defaults.difficulty`, or a caller
  override) and `{modifier}` (a per-roll situational adjustment supplied at resolve time,
  0 when omitted) — reference them the same way as any sheet-derived name, e.g. `{vigor + brawl +
  modifier}d10 t{difficulty}`.
- `requires` — group names the formula needs; a check is only offered to actors whose sheet type
  composes all of them. Every name used in `roll` must be reachable given `requires` (plus
  sheet-type-level names, checked when the check actually fires).
- `rules` — rules-doc slugs (filename stems from `rules/*.md`) pulled into context when this
  check resolves.
- `difficulty` (optional) — an integer target number specific to this check, overriding
  `_defaults.difficulty` (see below). Resolution can still override it per-roll; this is just the
  check's own default.
- `outcomes` (optional) — an explicit tier ladder grading the resolved roll, overriding
  `_defaults.outcomes` (see below) for this check only. A list of `{"label", "when"}` objects,
  evaluated top to bottom — **first match wins**. `when` is an expression over roll-scope names,
  not sheet fields: `total`, `natural` (the first die), `margin` (`total - difficulty`),
  `successes`, `ones` (count of 1s rolled), `dice` (dice count). A `when` that references a name
  the roll shape doesn't produce (e.g. `natural` on a multi-die pool roll) is skipped quietly for
  that roll rather than erroring; a name outside the roll-scope vocabulary fails validation.

#### `_defaults` — module-wide fallback difficulty/outcomes

`checks.json` may include a top-level `_defaults` key (a sibling of the named checks, not a check
itself) carrying a module-wide `difficulty` and `outcomes` ladder that every check falls back to
when it doesn't define its own. A check-level `outcomes` list, if present, is used *exclusively*
— `_defaults.outcomes` is only consulted when the check defines none of its own (no merging
between the two ladders). `pool-basic` uses this to give every check the same target-number and
success-grading convention without repeating it per check:

```json
{
  "_defaults": {
    "difficulty": 6,
    "outcomes": [
      {"label": "botch", "when": "successes == 0 and ones > 0"},
      {"label": "exceptional success", "when": "successes >= 5"},
      {"label": "success", "when": "successes >= 1"},
      {"label": "failure", "when": "successes == 0"}
    ]
  },
  "brawl": {
    "label": "Vigor + Brawl",
    "roll": "{vigor + brawl + modifier}d10 t{difficulty}",
    "requires": ["attributes", "abilities"],
    "rules": ["combat"]
  }
}
```
