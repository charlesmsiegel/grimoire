# `create-world` skill — design

**Date:** 2026-06-06
**Status:** Design (approved for planning)
**Type:** Claude Code project skill (`.claude/skills/create-world/`)

## Summary

A user-invoked Claude Code skill that **creates or updates a full Grimoire
world** in the user's live library by authoring the on-disk files directly —
`world.yaml` plus the entity Markdown files (characters, locations, items,
factions, lore, monsters, greetings). It works from a supplied concept and/or
by **ingesting character cards and lorebooks** (SillyTavern V2/V3, `charx`, PNG
`tEXt`, plaintext) into the world. Generated content is held to the
`sakura-high` seed-world quality bar and validated by round-tripping every file
through the real backend Pydantic models before the skill reports done.

The skill is a Claude-driven authoring path. It is distinct from — and does not
call — the app's existing REST import pipeline
(`/library/worlds/{id}/imports/sillytavern/...`); it parses formats itself so it
needs no running backend.

## Goals

- Create a new, fully playable world from a concept, with a `plan → confirm →
  generate` workflow.
- Update an existing world: add characters/locations/factions/lore/etc., or
  ingest a card/lorebook into it, without id collisions or tone drift.
- Ingest character cards and lorebooks by parsing the formats directly.
- Produce content at the `sakura-high` quality bar (concrete voice anchors,
  specific sensory prose, real ref wiring).
- Validate output offline against the real models + ref integrity.

## Non-goals

- Replacing or wrapping the app's REST import pipeline.
- Authoring into the repo seed dir (`backend/src/grimoire/seed/...`). Target is
  the **live library** only.
- Mechanics sheets, campaigns, style-guides, or image-presets (world content
  only; the skill may *reference* an existing style-guide/image-preset id in
  `world.yaml` defaults but does not author them).
- Generating images (it may write `image.base_prompt` / `images[]` metadata,
  but does not call ImageGen).

## Key decisions

| Decision | Choice |
|----------|--------|
| Skill name | `create-world` (also updates; SKILL.md states this) |
| Write target | Live library: `<data-root>/library/worlds/<id>/`, where data root is `GRIMOIRE_DATA_ROOT` else `~/.grimoire` |
| Ingestion | Claude parses cards/lorebooks and authors files directly; no running app |
| Workflow | `plan → confirm → generate` |
| Starter scope | No fixed default; the plan step proposes counts and the user adjusts |
| Validation | Round-trip every file through the real Pydantic models + ref-integrity check (offline script) |
| Structure | Lean `SKILL.md` + `references/` + one `scripts/validate_world.py` (progressive disclosure, matches repo house style) |
| Invocation | User-invoked (`disable-model-invocation: true`, like `new-module`) |

## Layout

```
.claude/skills/create-world/
  SKILL.md                      # workflow: create OR update a world
  references/
    entity-fields.md            # exact frontmatter per kind (from the models)
    card-lorebook-formats.md    # SillyTavern V2/V3 + character_book + charx/PNG mapping
    quality-bar.md              # what "sakura-high quality" means, concretely
  scripts/
    validate_world.py           # round-trips every file through real models + ref check
```

## Authoritative models (the skill documents and validates against these)

- `WorldMeta` — `backend/src/grimoire/types/composition.py` (`world.yaml`)
- `Greeting` — same file (`greetings/*.md`)
- `Character`, `VoiceAnchor`, `ImagePromptTemplate`, `CharacterImage`,
  `StructuralRelationship`, `CharacterRole` —
  `backend/src/grimoire/types/characters.py`
- `Location`, `LocationConnection`, `LocationKind`, `Item`, `Faction`,
  `Monster`, `MonsterCategory`, `LoreEntry` (+ `SecrecyLevel`, `LorePosition`,
  `SelectiveLogic`), `Season`, `Month`, `Holiday`, `WorldCalendar` —
  `backend/src/grimoire/types/world.py`
- Ingest mapping reference — `backend/src/grimoire/characters/ingest.py`,
  `backend/src/grimoire/characters/imports.py`, and the
  `Ingested*` shapes in `types/characters.py`
- File I/O helpers the script reuses — `grimoire.files` (`read_markdown`,
  `load_yaml`)

`world.yaml` may use either the legacy inline `calendar:` block (as `sakura-high`
does) or first-class `calendar_ids` / `holiday_set_ids` / `display_calendar_id`.
The skill defaults to the inline calendar block for self-containment and
documents the first-class option.

## Workflow (`SKILL.md`)

`$ARGUMENTS` = a world id (to update) or a concept (to create). If absent, ask.

1. **Resolve target.** Compute the data root (`GRIMOIRE_DATA_ROOT` else
   `~/.grimoire`). Determine create vs update by whether
   `library/worlds/<id>/` exists. On **update**, read the existing `world.yaml`
   and enumerate existing entity ids first, so new content matches the
   established calendar/tone and never collides on id.
2. **Gather context.** Collect the concept/pitch, any supplied notes, and any
   cards/lorebooks to ingest. If the concept is thin and nothing rich was
   supplied, ask a few targeted questions (genre, tone, PC role, scope).
3. **Plan → confirm.** Draft a short world plan:
   - premise + genre + tone, `pc_role_tags`
   - calendar / seasons / holidays (or which calendar ids to attach)
   - cast list with one-line hooks (proposed count — user adjusts)
   - key locations, factions, lore (proposed counts — user adjusts)
   - starting greeting(s) + `defaults` (starting_location, style-guide id,
     image-preset id)
   Present it; get explicit sign-off before writing any files.
4. **Generate.** Write `world.yaml` and the entity `.md` files (YAML
   frontmatter + prose body) to the quality bar. Wire refs as you go
   (`parent_id`, `connections`, `typical_occupants`, faction membership,
   relationships, greeting cast).
5. **Ingest (when cards/lorebooks supplied).** Parse per the format reference
   and merge into the world (see below).
6. **Validate.** Run `validate_world.py <id>`; fix every error before
   proceeding. Re-run until clean.
7. **Report.** Summarize created/updated files and note the running app's
   watcher will index them (or that the user should restart/refresh if the app
   isn't running).

## Card / lorebook ingestion sub-flow

Claude parses directly — no app required. Mapping mirrors
`characters/ingest.py` so it stays faithful:

- **Character** ← card `name` / `description` / `personality` / `scenario` /
  `mes_example`. `mes_example` and quoted lines seed `voice.samples`;
  description/personality seed `voice.summary` + the prose body. Role defaults
  to `major_npc` unless specified.
- **Greetings** ← `first_mes` (primary, `source_index: 0`) and
  `alternate_greetings[i]` (`source_index: 1..`) → `greetings/*.md` with
  `present_characters: [<card character id>]`.
- **Lore** ← `character_book.entries[]` → `lore/*.md`: `keys` → `keywords`,
  carry `secondary_keys`, `selective_logic`, `constant`, `enabled`,
  `case_sensitive`, `match_whole_words`, `priority`, `probability`, `position`,
  `at_depth`, `scan_depth`, `comment`. Reclassify entries that are clearly a
  location / faction / item / character into the right entity kind instead of
  lore (mirrors the app's reclassify step).
- **Formats:** JSON envelope (V2/V3), PNG with embedded `chara` (base64 JSON)
  or `ccv3` `tEXt` chunk, `charx` (zip) bundle, and plaintext. Expand
  SillyTavern macros (`{{char}}`, `{{user}}`, etc.) in text fields.
- **Merge:** dedupe against existing world ids; on update, link new characters
  to existing factions/characters where the card implies it.
- **Avatars:** if a card embeds an avatar and the user wants it kept, write it
  under the world's images area and record it in `images[]` (`source:
  embedded_avatar`). Otherwise record only `image.base_prompt`.

## References (bundled with the skill)

- **`entity-fields.md`** — one compact table per kind (field · type · required? ·
  notes), transcribed from the models, with a minimal and a full example each.
  Covers `world.yaml` (`WorldMeta` + inline calendar) and every entity kind.
- **`card-lorebook-formats.md`** — the V2/V3 JSON envelope, the PNG
  `chara`/`ccv3` chunk layout, the `charx` zip layout, and the field→Grimoire
  mapping table. Explicitly cross-references `characters/ingest.py` as the
  source of truth.
- **`quality-bar.md`** — distilled from `sakura-high`: concrete voice anchors
  (summary + register + samples + speech_patterns + address_terms + dos/donts),
  specific sensory prose over generic description, fully-wired refs, and
  `defaults` that point at a real starting location + greeting.

## Validation script (`scripts/validate_world.py`)

Run from `backend/`: `uv run python
../.claude/skills/create-world/scripts/validate_world.py <world-id>` (resolves
the same data root). Behavior:

1. Load `world.yaml` → `WorldMeta`.
2. For each entity dir, load each `.md` via `grimoire.files.read_markdown`,
   merge `world_id`, and validate against the kind's model
   (`Character`/`Location`/`Item`/`Faction`/`Monster`/`LoreEntry`/`Greeting`).
3. Report unknown/misspelled frontmatter keys (model is strict where it can be;
   otherwise diff keys against model fields and warn).
4. Ref-integrity pass across the loaded set:
   - `Location.parent_id`, `connections[].to`, `typical_occupants[]`
   - `Character.structural_relationships[].to_ref` (character or faction ref)
   - `Faction.base_location`, `leaders[]`, `members[]`, `allies[]`, `rivals[]`
   - `Item.current_holder`
   - `LoreEntry.related_locations/factions/characters[]`
   - `Greeting.starting_location`, `present_characters[]`, `pov_character`
   - `world.yaml` `defaults.starting_location`
5. Print a pass/fail report grouped by file; exit non-zero on any error so the
   skill loop can gate on it.

The script imports the real models so it never drifts from the schema; it is the
single source of validation truth for the skill.

## Testing / verification

- `validate_world.py` is exercised by running it against the `sakura-high` seed
  world (must pass clean) and against a deliberately-broken fixture (must report
  the planted errors and exit non-zero).
- Skill behavior is verified manually: a create run and an update/ingest run,
  each ending in a clean `validate_world.py`.

## Open questions

None blocking. Calendar authoring defaults to the inline block; first-class
calendar entities are documented but optional.
