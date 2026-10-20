# Validation and app checks

### 8. Validate after every step

Don't wait until the pack is "done" — run this after each step above, not just at the end, so an
error points at the change you just made rather than an accumulated pile:

```
$PY -c "from grimoire.store import modules; print(modules.load_pack('<mid>')['errors'])"
```

Run it from the repo root. An empty list (`[]`) means the pack is fully valid — manifest,
`sheets.json` cross-references (group refs exist, no key collisions, every `derived` expression
parses and resolves), `checks.json` (placeholders parse, names reachable via `requires`), rules
frontmatter, and any content stat blocks. A non-empty list names every problem found, so fix them
all before moving on rather than re-running per fix.

If you're working from a git worktree rather than the primary checkout, the worktree has no
editable install / venv of its own — either run this from a checkout that does, or point
`PYTHONPATH` at the worktree's `backend/src` while invoking the primary checkout's interpreter,
e.g. (Git Bash) `PYTHONPATH=backend/src /path/to/primary/$PY -c
"..."`. Also worth knowing: `load_pack` resolves `<GRIMOIRE_HOME>/modules/` via `store.home()`
(`AGENTS.md`), so if you scaffolded into a non-default `GRIMOIRE_HOME` (tests, an alternate
library) the validation command needs that same env var set to find it.

### 9. Confirm it's visible in the app

Once the pack's `errors` list is empty, open the Modules page. User-library packs
can be edited there; built-ins offer Duplicate first. Inspect groups, sheet types,
checks and rules, and keep `display_errors` separate from load validity. See
[the launcher instructions](../../../../README.md) or the [isolated verification skill](../../verify/SKILL.md). Then bind it to a world or campaign (world editor's default-module setting, or
a campaign's Mechanics config panel) to see it actually take effect.

## Common mistakes

- Writing sheet types before groups — a sheet type's `groups` list references groups that must
  already exist in the same `sheets.json`; there's no forward-declaration.
- Referencing a `text` or `list` field inside a `derived` or `roll` expression — both are
  non-addressable and fail validation with an unknown-name error.
- Forgetting the `_max` suffix when a `derived`/`roll` expression needs a `resource` field's
  ceiling rather than its current value (`essence_max`, not `max_essence` or `essence.max`).
- Treating `requires` in `checks.json` as decorative — it's enforced: a check referencing a group
  name not listed in `requires` fails validation even if that group exists elsewhere in the pack.
- Placing the same field in two layout nodes — each field/derived name may
  appear once per sheet type; the second placement is a display error.
- Setting `colors.bg` without `colors.ink` (or vice versa) — they only
  apply as a pair, so a lone one is dropped.
- Hand-editing a built-in pack under `backend/src/grimoire/store/builtin_modules/` instead of
  copying its shape into a new module under `<GRIMOIRE_HOME>/modules/` — built-ins are the
  read-only reference models, not a starting point to mutate in place.
- Skipping validation until the whole pack is written — a single typo'd expression early on is
  much easier to place when you validated right after writing it.
