---
name: create-world
description: Use when building a new grimoire World from a concept rather than from imported cards — a genre, a premise, a cast idea. Interviews for the setting's shape, writes a JSON plan (tags, cast, player characters, locations, lore, items, groups, creatures, greetings, plot map, calendar, module), applies it through backend/scripts/create_world.py, and checks the result is playable.
---

# Creating a World from a concept

A World is a directory of plain records under `<GRIMOIRE_HOME>/worlds/<wid>/`: a
character card per NPC, a persona per player character, one markdown file per
location/lore/item/group/creature entry, one per greeting, and `tags.md`,
`plotmap.json` and `calendar.json` beside them. Every kind already has a store
constructor, and this skill never writes any of those files itself. It writes a
**plan**, which is a JSON description of the World that names records by name and
refers to other records by name. `backend/scripts/create_world.py` turns the plan into
records through the same functions the app's routes call, so ids come from
`slugify`/`uniquify`, `{{char}}` gets baked, and frontmatter gets quoted the way
the app expects.

Where this sits among its siblings:

- **Cards already exist** (a roster of imported SillyTavern cards) → use
  `populate-world-content` for bulk extraction, then `world-card-integration` for the
  editorial pass. This skill is for the opposite direction: there are no cards yet,
  only an idea.
- **One record at a time** → the app's own world editor does that better. This skill
  is for authoring a whole World, or a coherent batch of it, in one go.

## Privacy

A World is the user's private content and lives outside this repo by design
(`CLAUDE.md`, "Privacy"). So:

- **The plan file is private content too.** Write it to the session scratchpad, or
  somewhere the user names outside the repo. Never put it in the repo, and never
  commit it.
- **Never carry the World's names back into the repo**: not into a commit message,
  a doc, a test fixture, or an edit to this skill. The examples below use the
  codebase's placeholder names (Saltmarch, Seraphine, Mara, Winifred). Keep it that
  way.

Talking about the World in the conversation is fine, since that's the work.

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
World's id. Every later command needs that id.

## Workflow

### 1. Interview

Ask until you could write the plan's first draft without guessing. A World authored
on a wrong guess means renaming records later, which is the one thing ids make
expensive (step 2). Cover:

- **Premise, genre, tone, era.** What kind of story is played here, and what does a
  typical scene feel like?
- **Who the player is.** One kind of player character, or several (outsider vs.
  insider, human vs. not)? Each kind that changes *which scenes make sense* becomes
  a **tag**. Tags are a player-character vocabulary, not a keyword index.
- **The cast.** Who the player meets first, who recurs, who is behind things. For
  each one: what they want, who they are to the others, and how they talk.
- **The furniture.** Places where things happen, organisations with members,
  possessions that matter, the setting's own concepts (its magic, its politics, its
  history), and creatures if the genre has them.
- **What's secret.** What the narrator may know but uninvolved characters may not,
  and what is for the author's eyes only.
- **Openings.** The first scenes a campaign could start with, and whether any of
  them only make sense after another.
- **Time and mechanics.** Real-world dates or a fictional calendar? A dice system,
  or freeform?

Use **AskUserQuestion** for the decisions that fork the plan (one PC kind or several,
which calendar, whether to bind a module). Ask the open-ended parts in prose.

### 2. Sketch the names, and confirm them

List every record by kind and name, with a line each, and get the user's agreement
**before** applying anything. An id is derived from the name at creation and is
**stable for life**: world→campaign sync keys on it (`store/entities.py`). Renaming a
record in the app changes only its `name`. Renaming it in the plan makes a *new*
record, because `apply` finds records by name. Names that are right the first time
save a cleanup later.

### 3. Write the plan

A complete example, with every key shown. Omit anything you don't need.

```json
{
  "world": "Saltmarch",
  "calendar": {"provider": "gregorian", "region": "US"},
  "module": "pool-basic",
  "tags": ["Outsider", "Guild Member"],
  "characters": [
    {"name": "Seraphine",
     "description": "The harbourmaster of Saltmarch, ...",
     "personality": "dry, exacting, tired",
     "scenario": "",
     "voice_anchor": "Short declaratives; never answers a question with a question.\n\"The tide doesn't wait. Neither do I.\" - the clipped close."},
    {"name": "Mara", "description": "A tide-reader for the guild, ..."}
  ],
  "pcs": [
    {"name": "Winifred", "tags": ["Outsider"], "pronouns": "she/her",
     "summary": "A stranger off the packet boat.", "description": "..."}
  ],
  "locations": [
    {"name": "The Tide Hall", "body": "Where the guild meets ...",
     "keys": ["Tide Hall"], "fields": {"climate": "temperate-coastal"}}
  ],
  "lore": [
    {"name": "The Drowned Bell", "body": "...", "keys": ["Drowned Bell", "bell"]},
    {"name": "Seraphine's Debt", "body": "...", "owners": ["characters:Seraphine"],
     "secrecy": "secret"}
  ],
  "groups": [
    {"name": "The Tide Guild", "body": "...", "keys": ["Tide Guild", "guild"],
     "fields": {"leader": "characters:Seraphine", "headquarters": "locations:The Tide Hall"}}
  ],
  "items": [
    {"name": "Seraphine's Lantern", "body": "...", "owners": ["characters:Seraphine"],
     "fields": {"holder": "characters:Seraphine"}}
  ],
  "creatures": [],
  "greetings": [
    {"name": "Off the Packet Boat", "character": "Seraphine", "location": "The Tide Hall",
     "body": "{{char}} looks {{user}} over at the quay ...",
     "leads_to": ["The Guild's Offer"]},
    {"name": "The Guild's Offer", "character": "Mara", "present": ["Mara", "Seraphine"],
     "requires_tags": ["Outsider"], "predecessor_join": "all",
     "body": "..."}
  ]
}
```

**References.** Anywhere a record names another one, write its **name**: a greeting's
`character`, `present`, `location`, `requires_tags`, `leads_to` and `excludes`, a PC's
`tags`, and an entry's `owners` and ref `fields`. `owners` and ref fields are spelled
`<kind>:<name>`, where kind is `characters`, `pcs`, `locations`, `lore`, `items`,
`groups` or `creatures`. An existing record's id also works. A name the plan creates
works anywhere in the same plan, in any order.

**Per kind:**

| kind | keys a plan may set |
|---|---|
| `characters` | `description`, `personality`, `scenario`, `first_mes`, `mes_example`, `alternate_greetings`, `tags`, `creator_notes`, `system_prompt`, `post_history_instructions`, `nickname` (the V3 card's `data`), plus `voice_anchor` |
| `pcs` | `tags`, `pronouns`, `summary`, `birthdate`, `description` |
| `locations` `lore` `items` `groups` `creatures` | `body`, `keys` (list), `owners` (list), `secrecy` (`public`/`secret`/`gm-only`), `fields` |
| `greetings` | `body`, `character`, `present`, `location`, `requires_tags`, `predecessor_join` (`all`/`any`), `pcless`, `phase`, `sequence`, `optional`, `leads_to`, `excludes` |

`fields` are the per-kind typed fields in `store/entity_schema.py`: a location's
`climate`/`persistence`/`weather_zone`, an item's `item_type`/`rarity`/`holder`, a
group's `group_type`/`leader`/`headquarters`, and a creature's
`creature_type`/`threat`/`habitat`. Lore has none. Game stats are not fields. They
are sheets, owned by the bound module (`create-mechanics-module`).

Put the NPCs' opening scenes in **greetings**, not in a card's `first_mes`. A world
greeting carries its cast, location, tags and plot-map edges, and a card's
`first_mes` carries none of them.

For the *prose* of each record, `world-card-integration/references/house-formats.md`
shows the entry shapes the author's existing Worlds settled into: a concept entry,
a five-line cast entry, a group with its roster, and a voice anchor. A new World
can follow them or set its own, but it should be consistent with itself.

**Patch semantics.** A key the plan gives is written, a key it omits is left as it
is, and a record the plan doesn't mention is never touched. Nothing is ever
deleted. Re-applying the same plan changes nothing (every row reads `unchanged`), so
iterate on the plan freely: fix it, re-apply, re-check.

### 4. Decide activation, per entry

This is where a World most often does the opposite of what its author meant. It is
also the decision the prompt is built from (`context/world_state.py`):

- **Keyed, unowned**: enters the prompt when a key appears, whole-word and
  case-insensitive, in recent scene text. This is the right default for nearly
  every entry. Keys are the names people will actually *say*: the full name, the
  short name, an in-world synonym.
- **Keyless, unowned lore/item/group/creature**: **always on**, in every turn's
  prompt, and every turn pays for it. Reserve it for the few facts that are true of
  every scene (the setting's one-paragraph premise, say).
- **Keyless location**: never always-on. A location with no keys reaches the prompt
  **only as the current setting** of a scene set there, never because someone
  mentions it. Give a location keys if it should come up when talked about.
- **Owned** (`owners`): silent unless an owner is in the scene, and then the keyed
  or keyless rule above applies. This is how a character's private history stays
  out of scenes they aren't in.
- **`secrecy: secret`**: activates as normal but renders under a heading telling the
  model that uninvolved characters don't know it. **`gm-only`**: never reaches a
  prompt at all, so it is author's notes.

`check` lists every keyless location and every always-on entry, so you can confirm
each one was meant.

### 5. Greetings and the plot map

A greeting is a scene opener. Whether a campaign can start it is decided by
`greetings.availability`, and it is startable when **all** of these hold:

- every tag in `requires_tags` is one the player character carries;
- its predecessors (the greetings whose `leads_to` names it) have been played: all
  of them under `predecessor_join: "all"`, at least one under `"any"`;
- no played greeting `excludes` it, and it excludes nothing already played;
- it hasn't been played already.

So the classic authoring bug is a World where **no greeting is startable by a fresh
campaign**: every opener gated on a tag, or every one downstream of another. Make
sure at least one opening needs no tag and has no predecessor, unless every player
character the World offers carries the tag it needs.

Add `leads_to` only where a later scene really depends on an earlier one. A wrong
edge silently forces a story order nobody wanted. `excludes` is for branches: taking
one path closes the other. The plot map must be acyclic (a cycle means none of its
greetings can ever start), and both `apply` and `check` refuse one.

`pcless: true` is an offscreen opener, an NPC-only scene with no player character.
`phase`, `sequence` and `optional` only order the "what next" recommendations
(`greetings.recommendations`) and never change what is startable.

The body keeps `{{user}}` for the player character. `{{char}}` is replaced with the
greeting's own character's name when the greeting is written, so use it or write
the name out, whichever reads better.

### 6. Calendar and module

- **Calendar.** `gregorian` and `hebrew` ship in `store/calendars/`, and a fictional
  calendar is a plugin the user keeps in `<GRIMOIRE_HOME>/calendars/`
  (`store/calendars/plugins.py`). Never author one into the repo. A plan may give
  just the primary block (`{"provider": ..., "region": ...}`) or the whole config
  (`{"primary": {...}, "secondary": {...}}`). An authored calendar is saved as
  `confirmed`, so campaigns made from this World start on it without asking.
- **Module.** Name a mechanics module by id (`pool-basic`, `d20-basic`, or one in the
  user library) to make it the World's default. `apply` refuses to *change* the
  module of a World that already has campaigns, because rebinding those campaigns
  needs the world editor's locking. Change it there instead.

### 7. Dry-run, apply, check

```bash
$PY backend/scripts/create_world.py apply --plan plan.json --dry-run
```

Validates everything that can be checked before writing anything: unknown keys,
names that don't resolve, duplicate names, actor names already used by another
character or PC in this World or its campaigns, field values, secrecy levels,
plot-map cycles, the calendar and the module. A plan that fails the dry-run would
also refuse `apply`, so `apply` never leaves half a World behind. Fix every problem
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
description, a PC who can start nothing, and a greeting gated on a tag no PC
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
- `.claude/skills/world-card-integration/references/house-formats.md`: the entry prose
  shapes.
- `store/context/world_state.py` (`activate`, `_world_info`): the activation rules in
  step 4.
- `store/greetings.py` (`availability`, `recommendations`): the opening rules in
  step 5.
