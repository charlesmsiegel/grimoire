# Apply, check and iterate

## Running the CLI

Run from the repo root with the backend venv's interpreter:

```bash
PY=backend/.venv/bin/python               # macOS/Linux
PY=backend/.venv/Scripts/python.exe       # Windows (Git Bash)

$PY backend/scripts/create_world.py apply --plan <plan.json> --dry-run   # validate only
$PY backend/scripts/create_world.py apply --plan <plan.json>             # write
$PY backend/scripts/create_world.py check --world <wid>                  # is it playable?
$PY backend/scripts/create_world.py summary --world <wid>                # what's there, with ids
```

The store root resolves the way the app's does (`GRIMOIRE_HOME`, then the bootstrap
pointer, then `~/.grimoire`). To build somewhere disposable first, set
`GRIMOIRE_HOME` to a scratch directory for every command. From a worktree, use the
primary checkout's interpreter, which reads the worktree's sources because the
script puts its own `backend/src` first on `sys.path`.

`apply` prints one row per record with `created`, `updated` or `unchanged`, plus the
World's id. Every later command needs that id, **including every later `apply`**: a
plan whose `world` names a World that already exists is refused unless
`--world-id <wid>` says to add to it. That keeps a new concept from being merged
into a library World that happens to share its name.

### 7. Dry-run, apply, check

```bash
$PY backend/scripts/create_world.py apply --plan plan.json --dry-run
```

Validates everything that can be checked before writing anything: unknown keys,
wrong types, names that don't resolve or are ambiguous, duplicate names, actor names already used by another
character or PC in this World or its campaigns, field values, secrecy levels,
plot-map cycles, the calendar and the module. A plan that fails the dry-run would
also refuse `apply`, so a plan problem never leaves half a World behind. Fix every problem
it lists, then apply:

```bash
$PY backend/scripts/create_world.py apply --plan plan.json
$PY backend/scripts/create_world.py check --world <wid>
```

`check` reads what is actually on disk, so it also catches anything edited by hand
or in the app since. **Errors** make it exit nonzero: a reference that doesn't
resolve, a plot-map cycle, an unknown tag, a calendar or module that won't load,
and no greeting that any player can start. **Warnings** are judgment calls: always-on
entries, keyless locations, empty bodies, characters with no voice anchor or no
description, a PC who can start no onscreen greeting, and a greeting gated on a tag no PC
carries. Fix the errors. For each warning, either fix it or say why it stays.

### 8. See it in the app

Open the World in the app and start a campaign from it: the new-scene pane should
offer the openings `check` called startable. For a dry run that can't touch the
user's library, build the World under a scratch `GRIMOIRE_HOME` and drive it with
the `verify` skill. When the World is ready to share, the Worlds page exports it as
a bundle (`store/world_bundle.py`).

## Common mistakes

- **Renaming in the plan to fix a typo.** It creates a second record under the new
  name. Rename the existing one in the app (its id is unchanged), then fix the
  plan's spelling to match.
- **Writing ids instead of names.** They work, but a predicted id is a guess:
  `"The Western Road"` slugifies to `the-western-road`, and a collision gets a
  suffix. Names are what the plan is for.
- **A keyless location that should come up in conversation.** It won't. Give it
  keys.
- **Keyless lore everywhere.** Every always-on entry is in every prompt and crowds
  out the entries that matter for this scene.
- **Every opener gated on a tag or a predecessor.** The World has nothing to start
  with, which `check` reports as an error.
- **The plan file in the repo.** It is the user's World. Keep it in the scratchpad.

## Reference

- `backend/scripts/create_world.py`: the CLI. Its module docstring and
  `validate_plan` are the source of truth for the plan format.
- `backend/tests/test_create_world.py`: a complete worked plan, with placeholder
  names.
- `.agents/skills/world-card-integration/references/house-formats.md`: the entry prose
  shapes.
- `store/context/world_state.py` (`activate`, `_world_info`): the activation rules in
  step 4.
- `backend/src/grimoire/store/greetings.py` (`availability`, `recommendations`): the opening rules in
  step 5.

The apply script already syncs its writes; a new cold World has no hot work to rebuild. For edits outside that script, finish with [store-editing sync](../../grimoire-store-editing/references/sync/batch.md#sync-a-coherent-batch).
