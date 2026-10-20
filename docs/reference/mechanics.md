# Mechanics and extensions

[Documentation](../README.md) · [Worldbuilding](../guides/worldbuilding.md)

## Dice notation

The manual dice control uses this grammar, in this clause order:

```text
[N]dM [khN|klN|dhN|dlN] [!] [+K|-K] [tN | vs N]
```

| Expression | Meaning |
|---|---|
| `d20`, `2d6` | One d20 or two d6, summed |
| `4d6kh3` | Keep the highest three of four dice |
| `4d6kl2` | Keep the lowest two |
| `4d6dh1`, `4d6dl1` | Drop the highest or lowest die |
| `5d6!` | Roll again on a maximum face, subject to the explosion bound |
| `2d6+3` | Add a flat modifier to the sum |
| `7d10t6` | Count kept dice at least 6 as successes |
| `1d20+5 vs 15` | Grade the summed total against 15 |

`tN` and `vs N` are mutually exclusive. Pool rolls do not accept a nonzero flat
modifier. Keep/drop counts must leave a valid selection. The current parser
accepts 1–100 dice with 2–1000 sides and bounds explosions. Spaces between clauses
and letter case do not change the notation.

The engine is deterministic for a notation and stored integer seed. Sum-mode
explosions add to the original die; pool-mode explosions append another die.
See [dice.py](../../backend/src/grimoire/store/dice.py) and the persistent
[roll ledger](../../backend/src/grimoire/store/rolls.py).

## Mechanics modules and sheets

A mechanics module is a data pack. Bind a world/campaign to a module to make its
checks and sheet definitions available. The **Modules** library supports pack
authoring, and a campaign's **Sheets** page manages its cast's sheet coverage.
An invalid pack reports validation errors and cannot simply be treated as a
valid binding.

The shipped [d20-basic](../../backend/src/grimoire/store/builtin_modules/d20-basic/)
and [pool-basic](../../backend/src/grimoire/store/builtin_modules/pool-basic/)
packs are concrete examples of the implemented schema. They demonstrate basic
mechanics rather than claiming complete licensed game-system coverage.

| Pack file | Responsibility |
|---|---|
| `module.md` | Manifest, description and optional default dice expression |
| `sheets.json` | Sheet types, groups, fields and derived values |
| `checks.json` | Named checks, dice expressions, requirements and outcome rules |
| `rules/*.md` | Rule text and activation metadata |
| `content/` | Reusable records and optional sheet values |
| `layout.json`, `theme.json` | Supported sheet display and theme customization |

User packs live under the library's `modules/`; a user pack can shadow a built-in
ID. Built-ins resolve from the package, with `GRIMOIRE_MODULES` as the deployment
override. Validate against the loader before binding or publishing a pack.

The expression language is deliberately constrained. It is not arbitrary Python.
Use supported fields/functions and check validation errors before a scene reaches
the roll flow. A roll proposal, accepted result and reviewed sheet change have
different persistence steps; module changes must not invalidate a proposal while
it is being committed.

## Author a module

Start from a built-in pack close to the mechanics you need. Define the sheet
shape, add one check, validate it, then grow the pack. The
[module-authoring skill](../../.agents/skills/create-mechanics-module/SKILL.md)
walks the supported authoring procedure. Keep homebrew content in the user's
library instead of adding it as an example in the public repository.

The loader and validator are the schema authority:
[pack.py](../../backend/src/grimoire/store/modules/pack.py),
[validate.py](../../backend/src/grimoire/store/modules/validate.py),
[fields.py](../../backend/src/grimoire/store/modules/fields.py) and
[expressions.py](../../backend/src/grimoire/store/expressions.py).

## Calendars

Gregorian and Hebrew providers ship with the application. Calendar dates map to
a fixed-day integer so arithmetic can operate across supported notations.
World/campaign configuration chooses the provider and relevant calendar options.

Custom providers live as Python files under the library's `calendars/` directory.
They implement `CalendarProvider` and call `register` when imported. Required
methods parse and format dates, describe a day, list holidays and enumerate months.
The base class supplies some age and anniversary helpers.

Use [base.py](../../backend/src/grimoire/store/calendars/base.py) for signatures,
the [Gregorian](../../backend/src/grimoire/store/calendars/gregorian.py) and
[Hebrew](../../backend/src/grimoire/store/calendars/hebrew.py) implementations
for examples, and [plugins.py](../../backend/src/grimoire/store/calendars/plugins.py)
for loading behavior. These plugins execute Python code; install only providers
you intend to run. Personal calendars and fictional holiday sets stay outside
the repository.
