---
name: create-world
description: >-
  Create or update a full Grimoire world in the live library (~/.grimoire or
  $GRIMOIRE_DATA_ROOT) by authoring world.yaml and entity markdown files
  directly, and by ingesting SillyTavern character cards / lorebooks. Use when
  the user wants to build, extend, or import content into a Grimoire world.
disable-model-invocation: true
---

# create-world — author or extend a Grimoire world

Grimoire worlds live as a directory under `<data-root>/library/worlds/<id>/`:
a `world.yaml` plus one markdown file per entity in `characters/`, `locations/`,
`items/`, `factions/`, `monsters/`, `lore/`, and `greetings/`. Files are the
source of truth; the running app's watcher indexes them. This skill authors
those files directly — no running backend required.

`$ARGUMENTS` is a world id (to update) or a concept (to create). If absent, ask
which world to build or extend.

## Data root

Resolve once: `GRIMOIRE_DATA_ROOT` if set, else `~/.grimoire`. Worlds live at
`<data-root>/library/worlds/<id>/`. Never author into the repo seed dir
(`backend/src/grimoire/seed/...`).

## Reference material (read on demand, not all up front)

- `references/entity-fields.md` — exact frontmatter for every kind + `world.yaml`.
  Read before writing any file of a kind you haven't written this run.
- `references/card-lorebook-formats.md` — SillyTavern V2/V3, `charx`, PNG `tEXt`,
  and the field→Grimoire mapping. Read only when ingesting a card/lorebook.
- `references/quality-bar.md` — what "sakura-high quality" means, concretely.
  Read before generating prose/voice.
- `references/traits-and-sheets.md` — how mechanics sheets relate to library
  worlds, and how to pre-author the traits a sheet will use in `extras`. Read
  when the user wants mechanics-aware characters or a specific game system.

## Workflow

1. **Resolve target.** Compute the data root. Decide create vs update by whether
   `library/worlds/<id>/` exists.
   - On **update**: read the existing `world.yaml` and list existing entity ids
     (`ls` each kind dir) before writing, so new content matches the established
     calendar/tone and never collides on an id.

2. **Gather context.** Collect the concept/pitch, supplied notes, and any
   cards/lorebooks to ingest. If the concept is thin and nothing rich was
   supplied, ask a few targeted questions (genre, tone, PC role, scope).

3. **Plan → confirm (required gate — do not skip).** Present a short plan:
   - premise, genre, tone, `pc_role_tags`
   - calendar / seasons / holidays (inline `calendar:` block by default)
   - cast list with one-line hooks — **propose counts; ask the user to adjust**
   - key locations, factions, lore — **propose counts; ask the user to adjust**
   - starting greeting(s) and `defaults` (starting_location, style-guide id,
     image-preset id)
   Get explicit sign-off before writing any files.

4. **Generate.** Write `world.yaml` first, then entity files. Wire refs as you
   go: `parent_id`, `connections`, `typical_occupants`, faction membership,
   `structural_relationships`, greeting `present_characters`. Use ids that are
   slugified-kebab-case of the name. Follow `references/quality-bar.md`.

5. **Ingest (only when a card/lorebook was supplied).** Parse per
   `references/card-lorebook-formats.md` and merge into the world, deduping
   against existing ids. On update, link imported characters to existing
   factions/characters where the source implies it.

6. **Validate (required gate).** Run, from `backend/`:
   ```
   uv run python ../.claude/skills/create-world/scripts/validate_world.py <id>
   ```
   (Pass a full path instead of `<id>` to validate a world outside the data
   root.) Fix every **ERROR** and re-run until the report is error-free.
   Warnings are advisory (e.g. a connection to an unmodeled corridor) — review
   them but they don't block.

7. **Report.** Summarize created/updated files. Note the running app's watcher
   will index them automatically; if the app isn't running it will index on next
   start.

## Character variants (multiple versions of a character)

Grimoire's only built-in "variant" mechanism is **cross-world**: the same
character `id` (filename stem) authored in more than one world is treated as a
variant of that character (`GET /library/variants/...`, cross-world lookup). So
to make alternate versions today, author the character with the **same id in
different worlds** (e.g. `alistair` in `wod-london` and in `wod-chicago`), each a
different portrayal.

There is **no in-world way to hold multiple coexisting versions of one
character** — within a single world an `id` is unique (one id, one file), and
`version` is just a content-revision counter. If the user wants in-world
alternates, explain this limitation rather than faking it with near-duplicate
ids. The concept is tracked for clarification in app issue
[#579](https://github.com/charlesmsiegel/grimoire/issues/579).

## Mechanics traits & sheets

Mechanical sheets are **campaign + mechanics scoped**, so a library world can't
carry them. But you can pre-author the **traits a sheet will draw from** in each
entity's `extras` when the intended game system is obvious — see
`references/traits-and-sheets.md`. Use the system's own vocabulary, keep keys
consistent across the cast, and never use the reserved `mechanics_` /
`system_` / `_internal_` key prefixes.

## Conventions to honor

- One entity per file; filename is `<id>.md`. Prose goes in the markdown body;
  structured data in the YAML frontmatter.
- Lore files use `title:` (not `name:`); every other kind uses `name:`.
- Use `voice_register:` (not `register:`) inside a character's `voice:` block.
- Ids are kebab-case slugs of the name; keep them stable once referenced.
- Don't invent frontmatter keys — if a field isn't in `entity-fields.md`, it's
  dropped on load. Use `extras:` / `tags:` for anything genuinely custom.
- `defaults.starting_location` and every greeting's `starting_location` /
  `present_characters` must reference entities that exist in the world.
