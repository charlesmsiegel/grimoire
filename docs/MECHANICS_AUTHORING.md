# Mechanics module authoring

Sprint 086's first slice lets an author write a small mechanics module (sheet types and named checks) as a
JSON document they own, see every problem with it in plain words, and publish it as an immutable revision
only once it has none. A sheet filled under a revision and a check graded under it pin that revision, so a
later edit changes new checks and never one already recorded. Its editor is the Rules page (the Wave 9 UI
exit, 116, [RULES_PAGE](RULES_PAGE.md)); there is still no campaign selection of a module and no durable roll
journal (075).

- `grimoire.mechanics` is pure: the expression language, validation, sheets, recorded-dice grading and the
  templates. No files, clocks or randomness.
- `grimoire.mechanicsstore` is its file adapter: drafts and published revisions in a library.

## A module

```json
{
  "format": "grimoire.mechanics/1",
  "id": "duel", "name": "Duel", "version": "1", "family": "generic",
  "sheets": {
    "fencer": {
      "label": "Fencer",
      "fields": [
        {"key": "blade", "label": "Blade", "type": "number", "min": 0, "max": 5, "default": 1},
        {"key": "motto", "label": "Motto", "type": "text"}
      ],
      "derived": {"edge": "blade * 2"}
    }
  },
  "checks": {
    "thrust": {
      "label": "Thrust", "sheet": "fencer", "difficulty": 10,
      "dice": {"count": "1", "sides": 12},
      "total": "natural + edge + modifier",
      "outcomes": [{"label": "hit", "when": "margin >= 0"}, {"label": "miss", "when": "margin < 0"}]
    }
  }
}
```

- **Fields** are `number` (`min`, `max`, `default`), `dots` (0 to `max` ≤ 10), `resource` and `track`
  (0 to a required `max`; a resource starts full, a track empty), `text` (`max_length`) and `list` (of
  short texts, `max_items`). Only the first four hold numbers a formula may use. `ref` fields are refused
  for now: a reference must name a source-qualified record, which this slice cannot check.
- **Derived values** are formulas over the sheet's numeric fields and other derived values. They are ordered
  so each uses only what comes before it; a loop is a problem, and so is a derived value that is not a whole
  number (or divides by zero) with every field at its default.
- **Formulas** use plain spaces, tabs or line breaks between parts; any other character is a problem named
  in the reason.
- **A check** belongs to one sheet type. `dice.count` is a formula; `sides` is 2 to 1000; with a
  `dice.target` formula, each face at or above it is a success. `total` is a formula; without one the total
  is the successes when there is a target, and the dice sum otherwise. `outcomes` are conditions tried in
  order; the first that holds is the result, and a roll none matches is refused, never guessed.
- **Names in scope.** Every check formula sees the sheet's numbers, `modifier` and `difficulty` (the
  check's own, or one supplied for the roll). `total` also sees `dice`, `dice_sum`, `ones`, `highest`,
  `lowest`, `natural` (only when `dice.count` is exactly `1`) and `successes` (only with a target).
  Outcomes also see `total` and `margin` (`total - difficulty`). A field or derived value cannot take one of
  these names.
- **Families.** `family` is `generic` (no further rule), `d20` (each check rolls exactly one d20 and has no
  target) or `pool` (each check has a target and no total). They are authoring constraints that catch a
  check of the wrong shape; they are not 062's rules-family profiles (`grimoire.rulesets`), which this
  slice does not change.

### The expression language

Integers, `true`/`false`, names, `+ - * /`, parentheses, comparisons (`== != < <= > >=`, not chained),
`and`, `or`, `not`, and the functions `floor`, `ceil`, `abs`, `min`, `max`. Arithmetic is exact: `7 / 2` is
seven halves, and a sheet value, dice count or total that is not whole is a problem asking for `floor()` or
`ceil()`, never rounded silently. Types are checked before anything runs (a number is not a condition).
Formulas are parsed by the package's own parser: there is no `eval`, attribute, item or string, and an
expression is at most 256 characters, 32 levels deep, with literals up to 1,000,000 and no step beyond
10^12.

### Rules never run code

A key asking for executable behaviour (`hooks`, `script`, `code`, `python`, `javascript`, `lua`, `exec`)
is refused as a key of the module, a sheet type, a field, a check, its dice or an outcome; names the author
chooses (sheet types, checks, derived values) are data, not behaviour. This keeps Sprint 007's obligation: authored rules
compute with formulas; untrusted rule scripts are not run in-process with library authority.

### Problems

`validate_module` returns every problem it finds, each a dotted path and a reason, such as
`checks.stealth.total: unknown name 'stelth' (did you mean 'stealth'?)` or
`checks.stealth.total: unknown name 'gear': 'gear' is a list field and holds no number`. Unknown keys are
problems too. A check of a sheet type that has problems is checked once that sheet is valid, so one mistake
is not reported many times, and neither is a formula that only uses a broken field or derived value.
`parse_module` returns the validated `MechanicsModule` or raises `ModuleInvalid` with the same list: a
module with any problem has no value at all, so it cannot be published, loaded or
bound.

### Revisions

A valid module's `revision` is `sha256:` and the digest of its document's meaning: key order and whitespace
do not change it, every value does (`"1"` is not `1`). Bounds (16 sheet types, 64 fields, 32 derived values,
64 checks, 16 outcomes, 100 dice) are this prototype's input limits, not game limits.

## Sheets and checks

`fill_sheet(module, sheet_type, values)` checks one actor's values (unknown keys and out-of-range or wrongly
typed values are each a problem; absent fields take their defaults) and computes the derived values. The
`Sheet` names the module and revision it is valid under.

`run_check(module, sheet, check, dice=(...), modifier=0, difficulty=None)` grades a check from dice the
caller recorded: this package never rolls. The dice must be exactly as many as the count formula gives,
each face 1 to `sides`. A sheet filled under another revision is refused ("fill it again"), and a `Sheet`
value whose numbers are not what its module computes is not trusted. Nor is a `MechanicsModule` value that is
not what its own document validates to (say, an edited module stamped with an older revision): `fill_sheet`,
`run_check` and `confirm` refuse it. The `CheckRecord` carries the module,
revision, sheet values, modifier, effective difficulty, dice, total, successes and outcome.

`confirm(module, record)` re-derives a record under the revision it names and refuses any other revision:
a record is never re-graded under an edited module. A record is an unaccepted proposal: it commits nothing,
spends nothing and does not decide that the check applies to the fiction (062's authority rules hold;
Sprint 075 owns durable roll recording and retries).

## Templates

`template("d20_basic")` and `template("pool_basic")` return fresh copies of V0.2.1's two reference modules
in this format, as starting points. They keep those examples' sheets and checks; this format checks every
formula name statically, offers `natural` and `successes` only to checks of the right shape, type-checks
outcomes and orders derived values. V0.2.1's `ref` fields, creation pools and advancement costs are not
carried.

## Drafts and published revisions on disk

```
config/mechanics/drafts/<id>.json            the author's working copy, exactly as saved
config/mechanics/modules/<id>/<digest>.json  one published revision, named by its content digest
```

- `save_draft(id, data, expected)` keeps any bytes (up to 256 KiB), even unfinished JSON, so work is never
  refused or lost. The published form is indented; one that would be over 512 KiB is a problem of the draft,
  so nothing publishes that `load` would refuse. `check_draft(id)` lists its problems (a JSON error is a
  problem of `$`; an `id` that is not the one it is saved under is one too) or gives the module it would
  publish as.
- `publish(id, expected)` writes the revision file only when the draft read at `expected` has no problem.
  Publishing the same content again writes nothing (a revision file laid out differently but holding the same
  module counts as the same). An edited draft publishes a new file beside the old one; a revision file is
  never rewritten, and one found with other content is refused (`altered`) and left alone.
- `load(id, revision)` returns exactly that revision. It is refused when it was never published
  (`unpublished`), no longer validates (`invalid`) or no longer has its digest (`altered`); there is no
  fallback to another revision or to the draft.
- Writes are a temp file renamed over the target, and name the etag of the draft they read; a draft that
  moved on meanwhile is refused `StaleWrite`. Links are never followed. `recover()` sweeps temp files only.
  Ids are 1-64 lowercase letters, digits and `_`, and revisions exact digests, so neither can leave its
  folder.

Nothing here reads a campaign, a world or a provider, and removing these files changes no other record.

## Not yet

Compatibility and migration previews against existing characters (milestone 2), interactive rule tests and
authoring extension policies (3), source-grounded rule compilation (4), selecting a published revision for a
campaign or chat, and durable roll journals (075). The editor page is 116's Rules page
([RULES_PAGE](RULES_PAGE.md)).
