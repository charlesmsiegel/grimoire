# create-world Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a user-invoked Claude Code skill, `create-world`, that creates or updates a full Grimoire world in the live library by authoring on-disk files directly (and ingesting SillyTavern cards/lorebooks), validated by round-tripping every file through the real backend Pydantic models.

**Architecture:** A lean `SKILL.md` workflow (plan → confirm → generate → validate) plus three `references/` docs (exact entity frontmatter, card/lorebook formats, quality bar) and one `scripts/validate_world.py` that imports the real `grimoire` models and runs a ref-integrity pass. Files are authored under `<data-root>/library/worlds/<id>/` where data root is `GRIMOIRE_DATA_ROOT` or `~/.grimoire`. The only executable code (the validator) is built test-first.

**Tech Stack:** Markdown skill docs; Python 3.12 validator importing `grimoire.types.*` and `grimoire.files.*`; pytest (run under `backend/` via `uv`).

**Spec:** `docs/superpowers/specs/2026-06-06-create-world-skill-design.md`

---

## File Structure

```
.claude/skills/create-world/
  SKILL.md                          # Task 1 — workflow
  references/
    entity-fields.md                # Task 2 — frontmatter per kind
    card-lorebook-formats.md        # Task 3 — SillyTavern/charx/PNG mapping
    quality-bar.md                  # Task 4 — sakura-high quality bar
  scripts/
    validate_world.py               # Task 5 — model round-trip + ref check
backend/tests/skills/
  __init__.py                       # Task 5
  test_validate_world.py            # Task 5 — TDD for the validator
```

Authoritative models the validator and docs track (do not re-define them):
- `WorldMeta`, `Greeting` — `backend/src/grimoire/types/composition.py`
- `Character`, `VoiceAnchor`, `ImagePromptTemplate`, `CharacterImage`, `StructuralRelationship`, `CharacterRole` — `backend/src/grimoire/types/characters.py`
- `Location`, `LocationConnection`, `LocationKind`, `Item`, `Faction`, `Monster`, `MonsterCategory`, `LoreEntry`, `SecrecyLevel`, `LorePosition`, `SelectiveLogic` — `backend/src/grimoire/types/world.py`
- `read_markdown` — `backend/src/grimoire/files/frontmatter.py`; `load_yaml` — `backend/src/grimoire/files/yaml_io.py`
- Ingest reference: `backend/src/grimoire/characters/ingest.py`, `imports.py`; `Ingested*` shapes in `types/characters.py`

---

## Task 1: Scaffold skill dir + write SKILL.md

**Files:**
- Create: `.claude/skills/create-world/SKILL.md`

- [ ] **Step 1: Create the directory structure**

```bash
mkdir -p .claude/skills/create-world/references .claude/skills/create-world/scripts
```

- [ ] **Step 2: Write `.claude/skills/create-world/SKILL.md`**

````markdown
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
````

- [ ] **Step 3: Verify the file parses as a skill (frontmatter present)**

Run: `head -n 8 .claude/skills/create-world/SKILL.md`
Expected: shows the YAML frontmatter with `name: create-world` and `disable-model-invocation: true`.

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/create-world/SKILL.md
git commit -m "feat(skill): add create-world SKILL.md workflow"
```

---

## Task 2: Write references/entity-fields.md

**Files:**
- Create: `.claude/skills/create-world/references/entity-fields.md`

- [ ] **Step 1: Write the file**

Write the following verbatim. The field lists are transcribed from the models named in the File Structure section; `*` marks required fields. Fields not listed here are dropped on load.

````markdown
# Entity frontmatter reference

Source of truth: the Pydantic models in `backend/src/grimoire/types/`. `*` =
required. Unlisted keys are ignored on load. Prose goes in the markdown body
below the frontmatter (for every kind that has a body).

## world.yaml (`WorldMeta`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | kebab-case; matches the directory name |
| `name`* | str | display name |
| `description` | str | one-paragraph pitch |
| `tags` | list[str] | genre/setting tags |
| `pc_role_tags` | list[str] | role tags a PC can take (e.g. `transfer-student`) |
| `genre` | str | free text |
| `calendar` | mapping | inline calendar block (below); default style |
| `calendar_ids` | list[str] | first-class calendar entities (alternative to inline) |
| `holiday_set_ids` | list[str] | first-class holiday sets |
| `display_calendar_id` | str | which attached calendar renders by default |
| `atmosphere` | mapping | `default_register`, `default_palette` |
| `defaults` | mapping | `starting_location`, `default_style_guide_id`, `default_image_preset_id` |
| `version` | int | bump on schema-relevant edits |

Inline `calendar:` block:
- `epoch`: ISO date (campaign start)
- `months`: list of `{ name, days }`
- `days_per_week`: int; `week_day_names`: list[str]
- `seasons`: list of `{ name, start_month, start_day, palette, weather_bias: {kind: weight} }`
- `holidays`: list of `{ name, month, day, description, tags }`

## characters/<id>.md (`Character`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `role`* | enum | `pc` \| `major_npc` \| `minor_npc` \| `ensemble` \| `named_flavor` |
| `aliases` | list[str] | |
| `age` | str | string, not int (e.g. `"16"`) |
| `tags` | list[str] | |
| `role_tags` | list[str] | |
| `voice` | mapping | `summary`, `voice_register`, `samples[]`, `speech_patterns[]`, `address_terms{}`, `dos[]`, `donts[]` |
| `image` | mapping | `base_prompt`, `negative_prompt`, `canonical_seed`, `extra{}` |
| `images` | list | `{ path, description, kind, tags[], seed, prompt_used, source, created_at, extra }`; `kind` ∈ portrait/avatar/expression/pose/scene/reference |
| `structural_relationships` | list | `{ to_ref, kind, note }`; `to_ref` is a character id or `worlds/<w>/factions/<id>`; `kind` e.g. `mentor`, `rival`, `faction:member` |
| `household_id` | str | shared key for characters who tick together |
| `privacy` | mapping | `internal_thoughts: { surface_in_hud, surface_inline, surface_in_context }` |
| `extras` | mapping | snake_case custom keys |

Use `voice_register`, **not** `register`. Description/personality prose goes in
the body.

## locations/<id>.md (`Location`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `parent_id` | str | containing location id, or `null` for a top-level place |
| `kind` | enum | `city` \| `building` \| `room` \| `region` \| `outdoor` \| `other` |
| `aliases` | list[str] | |
| `tags` | list[str] | |
| `climate_zone` | str | |
| `indoor` | bool | |
| `coordinates` | mapping | `{ x, y }` (floats) |
| `permanent_features` | list[str] | |
| `connections` | list | `{ to, via, duration_min, notes }`; `to` is a location id |
| `typical_occupants` | list[str] | character ids |

## items/<id>.md (`Item`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `aliases` | list[str] | |
| `tags` | list[str] | |
| `provenance` | str | |
| `current_holder` | str | character id or `null` |

## factions/<id>.md (`Faction`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `kind` | str | free text |
| `base_location` | str | location id |
| `leaders` | list[str] | character ids |
| `members` | list[str] | character ids |
| `allies` | list[str] | faction ids |
| `rivals` | list[str] | faction ids |
| `tags` | list[str] | |

## monsters/<id>.md (`Monster`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `category` | enum | beast/undead/dragon/fey/demon/aberration/humanoid/construct/elemental/other |
| `aliases` | list[str] | |
| `tags` | list[str] | |
| `threat_level` | str | free text (`"deadly"`, `"CR 12"`) |
| `habitat` | list[str] | location ids or biome strings |
| `abilities` | list[str] | |
| `weaknesses` | list[str] | |

## lore/<id>.md (`LoreEntry`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `title`* | str | **lore uses `title`, not `name`** |
| `tags` | list[str] | |
| `keywords` | list[str] | trigger keys |
| `related_locations` | list[str] | location ids |
| `related_factions` | list[str] | faction ids |
| `related_characters` | list[str] | character ids |
| `secrecy` | enum | public/common-knowledge/common-knowledge-among-kindred/restricted/secret |
| `secondary_keys` | list[str] | |
| `selective_logic` | enum | and_any/and_all/not_any/not_all |
| `constant` | bool | always injected if true |
| `enabled` | bool | |
| `case_sensitive` | bool | |
| `match_whole_words` | bool | |
| `priority` | int | default 100 |
| `probability` | int | 0–100, default 100 |
| `position` | enum | before_cast/after_cast/at_depth/archive |
| `at_depth` | int | when `position: at_depth` |
| `scan_depth` | int | |
| `comment` | str | author note |

Lore prose goes in the markdown body.

## greetings/<id>.md (`Greeting`)

| field | type | notes |
|-------|------|-------|
| `id`* | str | |
| `name`* | str | |
| `starting_location` | str | location id (or `null`) |
| `starting_time` | str | ISO-8601 in the world calendar |
| `present_characters` | list[str] | character ids on stage |
| `pov_character` | str | character id or `null` |
| `mood` | str | one-line scene mood |
| `tags` | list[str] | |
| `role_tags` | list[str] | gates which PC roles see this greeting |

Greeting prose (the opening scene shown to the player) goes in the body.

## Minimal valid examples

A location:
```markdown
---
id: town-square
name: Town Square
parent_id: rivermouth
kind: outdoor
tags: [public, hub]
indoor: false
---
The cobbled heart of Rivermouth, ringed by awnings and the smell of fried fish.
```

A character:
```markdown
---
id: mara-vance
name: Mara Vance
role: major_npc
age: "34"
tags: [smuggler]
voice:
  summary: Clipped, wry, allergic to sentiment.
  voice_register: low, casual
  samples:
    - "You want it done, or you want it done quiet? Pick one."
  dos: ["Names a price fast.", "Watches the exits."]
  donts: ["Never apologizes first."]
image:
  base_prompt: "weathered woman, short dark hair, oilskin coat, harbor at dusk"
---

## Appearance
Lean, sun-creased, a knife she never mentions.

## What she wants
Out from under a debt she didn't sign for.
```
````

- [ ] **Step 2: Verify it renders (no broken fences)**

Run: `grep -c '^## ' .claude/skills/create-world/references/entity-fields.md`
Expected: `10` (world.yaml + 7 kinds + "Minimal valid examples" + the trailing examples heading count — confirm at least 9 sections present).

- [ ] **Step 3: Commit**

```bash
git add .claude/skills/create-world/references/entity-fields.md
git commit -m "docs(skill): add create-world entity-fields reference"
```

---

## Task 3: Write references/card-lorebook-formats.md

**Files:**
- Create: `.claude/skills/create-world/references/card-lorebook-formats.md`

- [ ] **Step 1: Write the file**

````markdown
# Card & lorebook ingestion reference

The faithful mapping lives in `backend/src/grimoire/characters/ingest.py`
(`ingest_character_card_v2`) and `imports.py`. This doc summarizes it so you can
author files without a running backend. When in doubt, read those modules.

## Supported inputs

- **SillyTavern Character Card V2/V3** — JSON envelope `{ "spec":
  "chara_card_v2"|"chara_card_v3", "data": { ... } }`.
- **PNG with embedded card** — a `tEXt` chunk keyed `chara` (base64-encoded JSON,
  V2) or `ccv3` (V3). Decode the chunk's text, base64-decode, then parse as the
  JSON envelope above. The image itself is the avatar.
- **`charx`** — a ZIP bundle; the card JSON is an entry inside it (look for
  `card.json` / a `.json` matching the envelope) plus asset files.
- **Plaintext** — first non-empty line = name; quoted lines = voice samples;
  remaining prose = description/body. Role defaults to `minor_npc`.

To read a PNG `tEXt` chunk or a `charx` zip without the backend, use a short
Python one-off (`zipfile`, or walk PNG chunks) — or, if the backend is handy,
prefer the existing parser. Expand SillyTavern macros (`{{char}}`, `{{user}}`,
`{{original}}`, etc.) in every text field before writing.

## Field mapping: card `data` → Grimoire

| card field | Grimoire target |
|------------|-----------------|
| `name` | character `name` + slugified `id` |
| `description`, `personality` | `voice.summary` + character body prose |
| `scenario` | body context; may seed a greeting `mood` |
| `mes_example` | parsed dialogue → `voice.samples[]` |
| `first_mes` | primary greeting (`greetings/<slug>.md`, present_characters=[char]) |
| `alternate_greetings[i]` | one greeting file each |
| `system_prompt`, `post_history_instructions` | keep as `extras` or body notes; not first-class |
| `tags` | character `tags` |
| `creator`, `character_version` | `extras` |
| `character_book.entries[]` | lore files (below) |
| embedded avatar (PNG) | `images[]` `{ source: embedded_avatar, kind: portrait }` if kept; else derive `image.base_prompt` |

Default imported role: `major_npc` (override if the user says otherwise).

## character_book.entries[] → lore/<id>.md (`LoreEntry`)

| book entry field | LoreEntry field |
|------------------|-----------------|
| `keys` | `keywords` |
| `content` | body |
| `secondary_keys` | `secondary_keys` |
| `selectiveLogic` / `selective_logic` | `selective_logic` (and_any/and_all/not_any/not_all) |
| `constant` | `constant` |
| `enabled` | `enabled` |
| `case_sensitive` | `case_sensitive` |
| `match_whole_words` / `extensions.match_whole_words` | `match_whole_words` |
| `insertion_order` / `priority` | `priority` |
| `probability` | `probability` |
| `position` | `position` (before_cast/after_cast/at_depth/archive) |
| `depth` | `at_depth` (with `position: at_depth`) |
| `scan_depth` | `scan_depth` |
| `comment` | `comment` and/or `title` |

## Reclassification

A `character_book` entry that clearly describes a **place**, **organization**,
**item**, or **person** should become that entity kind instead of lore:

- place → `locations/<id>.md` (pick a `kind`)
- organization → `factions/<id>.md`
- item → `items/<id>.md`
- person → `characters/<id>.md` (`role: minor_npc` unless richer)

This mirrors the app's import reclassify step. When unsure, keep it as lore.

## Merge rules

- Dedupe against existing world ids; if an id collides, suffix or merge content
  (ask the user on a real conflict).
- On update, wire imported characters into existing factions/relationships when
  the source text implies membership or ties.
- After ingesting, run the validator (see SKILL.md step 6).
````

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/create-world/references/card-lorebook-formats.md
git commit -m "docs(skill): add create-world card/lorebook format reference"
```

---

## Task 4: Write references/quality-bar.md

**Files:**
- Create: `.claude/skills/create-world/references/quality-bar.md`

- [ ] **Step 1: Write the file**

````markdown
# Quality bar

The target is the shipped seed world `sakura-high`
(`backend/src/grimoire/seed/library/worlds/sakura-high/`). Read a few of its
files before generating. Concretely:

## Voice anchors (characters)

Every speaking character gets a `voice` block that is specific, not generic:
- `summary`: a sentence that captures the *contradiction* in the character
  ("Direct, dryly funny, surprisingly tender.").
- `voice_register`: how they actually talk ("casual; honorifics used correctly
  but without fuss").
- `samples`: 2–4 real lines in their voice that you could drop into a scene.
- `speech_patterns`, `address_terms`, `dos`, `donts`: tells that keep them
  consistent across turns.

Bad: "She is nice and likes her friends." Good: a line she'd actually say.

## Prose

- Specific sensory detail over abstraction: "the metal of the door handle is too
  hot to touch in the afternoon", not "it is warm in summer".
- Bodies are short, structured (e.g. `## Appearance`, `## What they want`), and
  give the model something to *play*, not an encyclopedia entry.
- A location body conveys mood and what *happens* there, not just geography.

## Wiring (the part that's easy to skip)

- Locations have a real `parent_id` and `connections`; the place a scene starts
  in must exist and be reachable.
- Greetings reference characters that exist and a `starting_location` that
  exists; `defaults.starting_location` points at a real room.
- Factions list real members/leaders; characters' `structural_relationships`
  point at real ids.
- The world ships at least one greeting so a campaign can start immediately.

## Calendar & atmosphere

- Give the world a calendar with named months, seasons (with palettes and
  weather bias), and a handful of holidays that create story hooks.
- `atmosphere.default_register` and `default_palette` set the narrative tone.

## Scope

There is no fixed size. Propose counts in the plan step and let the user adjust.
A lean world (≈4–6 characters, ≈5 locations, 1–2 factions, a few lore, 1–2
greetings) is playable; `sakura-high` (≈12 characters, ≈11 locations) is rich.
Quality per file matters more than count.
````

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/create-world/references/quality-bar.md
git commit -m "docs(skill): add create-world quality-bar reference"
```

---

## Task 5: Build the validator (TDD)

The validator is the only executable code. Build it test-first. Tests run under `backend/` (so `grimoire` imports resolve) and load the script by file path.

**Files:**
- Create: `backend/tests/skills/__init__.py`
- Create: `backend/tests/skills/test_validate_world.py`
- Create: `.claude/skills/create-world/scripts/validate_world.py`

- [ ] **Step 1: Create the test package marker**

```bash
touch backend/tests/skills/__init__.py
```

- [ ] **Step 2: Write the failing tests**

Create `backend/tests/skills/test_validate_world.py`:

```python
"""Tests for the create-world skill's validate_world.py.

Loads the script by path (it lives outside the grimoire package) and exercises
its validate_world() against synthetic worlds plus the real sakura-high seed.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".claude" / "skills" / "create-world" / "scripts" / "validate_world.py"
SEED_WORLD = (
    REPO_ROOT
    / "backend"
    / "src"
    / "grimoire"
    / "seed"
    / "library"
    / "worlds"
    / "sakura-high"
)


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("validate_world", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _valid_world(root: Path) -> Path:
    """A minimal world that must validate clean."""
    world = root / "tinytown"
    _write(
        world / "world.yaml",
        'id: tinytown\nname: Tiny Town\ndefaults:\n  starting_location: square\n',
    )
    _write(
        world / "locations" / "square.md",
        "---\nid: square\nname: Square\nkind: outdoor\n---\nA dusty square.\n",
    )
    _write(
        world / "characters" / "mara.md",
        "---\nid: mara\nname: Mara\nrole: major_npc\n---\nA smuggler.\n",
    )
    _write(
        world / "greetings" / "arrival.md",
        "---\nid: arrival\nname: Arrival\nstarting_location: square\n"
        "present_characters: [mara]\n---\nYou arrive.\n",
    )
    return world


def test_valid_world_passes(tmp_path: Path) -> None:
    mod = _load()
    report = mod.validate_world(_valid_world(tmp_path))
    assert report.errors == [], report.errors
    assert report.ok is True


def test_parse_error_is_reported(tmp_path: Path) -> None:
    mod = _load()
    world = _valid_world(tmp_path)
    # Invalid role enum -> model validation error.
    _write(
        world / "characters" / "bad.md",
        "---\nid: bad\nname: Bad\nrole: wizard\n---\nNope.\n",
    )
    report = mod.validate_world(world)
    assert report.ok is False
    assert any("bad.md" in e for e in report.errors)


def test_missing_greeting_location_is_error(tmp_path: Path) -> None:
    mod = _load()
    world = _valid_world(tmp_path)
    _write(
        world / "greetings" / "arrival.md",
        "---\nid: arrival\nname: Arrival\nstarting_location: nowhere\n"
        "present_characters: [mara]\n---\nYou arrive.\n",
    )
    report = mod.validate_world(world)
    assert report.ok is False
    assert any("nowhere" in e for e in report.errors)


def test_unresolved_connection_is_warning_not_error(tmp_path: Path) -> None:
    mod = _load()
    world = _valid_world(tmp_path)
    _write(
        world / "locations" / "square.md",
        "---\nid: square\nname: Square\nkind: outdoor\n"
        "connections:\n  - to: ghost-alley\n    via: street\n---\nA dusty square.\n",
    )
    report = mod.validate_world(world)
    assert report.ok is True, report.errors
    assert any("ghost-alley" in w for w in report.warnings)


def test_seed_world_is_error_free() -> None:
    mod = _load()
    report = mod.validate_world(SEED_WORLD)
    assert report.errors == [], report.errors
```

- [ ] **Step 3: Run the tests to verify they fail**

Run (from `backend/`): `uv run pytest tests/skills/test_validate_world.py -v`
Expected: collection error / FAIL — `validate_world.py` does not exist yet (`spec.loader.exec_module` raises `FileNotFoundError`).

- [ ] **Step 4: Write the validator**

Create `.claude/skills/create-world/scripts/validate_world.py`:

```python
#!/usr/bin/env python
"""Validate a Grimoire world directory against the real backend models.

Round-trips world.yaml and every entity markdown file through its Pydantic
model, then runs a cross-entity ref-integrity pass. Run from backend/ so the
`grimoire` package imports under the uv env:

    uv run python ../.claude/skills/create-world/scripts/validate_world.py <id-or-path>

Pass a world id (resolved under $GRIMOIRE_DATA_ROOT or ~/.grimoire) or a path to
a world directory. Exit code is non-zero when any ERROR is found; warnings never
fail the run.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, ValidationError

from grimoire.files import FrontmatterError, load_yaml, read_markdown
from grimoire.types.characters import Character
from grimoire.types.composition import Greeting, WorldMeta
from grimoire.types.world import Faction, Item, Location, LoreEntry, Monster

# entity subdir -> model
KINDS: dict[str, type[BaseModel]] = {
    "characters": Character,
    "locations": Location,
    "items": Item,
    "factions": Faction,
    "monsters": Monster,
    "lore": LoreEntry,
    "greetings": Greeting,
}


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


@dataclass
class Loaded:
    meta: WorldMeta | None = None
    # kind -> set of ids
    ids: dict[str, set[str]] = field(default_factory=dict)
    # parsed model instances, keyed (kind, id)
    entities: dict[tuple[str, str], BaseModel] = field(default_factory=dict)


def resolve_data_root() -> Path:
    env = os.environ.get("GRIMOIRE_DATA_ROOT")
    return Path(env) if env else Path.home() / ".grimoire"


def resolve_world_dir(arg: str) -> Path:
    candidate = Path(arg)
    if candidate.is_dir():
        return candidate
    return resolve_data_root() / "library" / "worlds" / arg


def _rel(path: Path, world: Path) -> str:
    try:
        return str(path.relative_to(world))
    except ValueError:
        return str(path)


def _load_entities(world: Path, report: Report) -> Loaded:
    loaded = Loaded(ids={k: set() for k in KINDS})

    meta_path = world / "world.yaml"
    if not meta_path.is_file():
        report.errors.append("world.yaml: missing")
    else:
        try:
            raw = load_yaml(meta_path) or {}
            loaded.meta = WorldMeta.model_validate(raw)
        except (ValidationError, Exception) as exc:  # noqa: BLE001
            report.errors.append(f"world.yaml: {exc}")

    world_id = loaded.meta.id if loaded.meta else world.name

    for kind, model in KINDS.items():
        kind_dir = world / kind
        if not kind_dir.is_dir():
            continue
        for md in sorted(kind_dir.glob("*.md")):
            rel = _rel(md, world)
            try:
                doc = read_markdown(md)
            except FrontmatterError as exc:
                report.errors.append(f"{rel}: {exc}")
                continue
            data = dict(doc.frontmatter)
            # Inject derived/contextual fields the file omits.
            if "world_id" in model.model_fields:
                data.setdefault("world_id", world_id)
            if "body" in model.model_fields and not data.get("body"):
                data["body"] = doc.body
            # Unknown-key check (top level only; models ignore extras silently).
            unknown = set(data) - set(model.model_fields)
            for key in sorted(unknown):
                report.warnings.append(f"{rel}: unknown field {key!r} (ignored on load)")
            try:
                inst = model.model_validate(data)
            except ValidationError as exc:
                report.errors.append(f"{rel}: {exc}")
                continue
            ent_id = data.get("id")
            if not ent_id:
                report.errors.append(f"{rel}: missing 'id'")
                continue
            if ent_id in loaded.ids[kind]:
                report.errors.append(f"{rel}: duplicate id {ent_id!r} in {kind}")
            loaded.ids[kind].add(ent_id)
            loaded.entities[(kind, ent_id)] = inst
    return loaded


def _err(report: Report, where: str, ref: str, kind: str) -> None:
    report.errors.append(f"{where}: missing {kind} ref {ref!r}")


def _warn(report: Report, where: str, ref: str, kind: str) -> None:
    report.warnings.append(f"{where}: unresolved {kind} ref {ref!r}")


def _check_refs(loaded: Loaded, report: Report) -> None:
    chars = loaded.ids["characters"]
    locs = loaded.ids["locations"]
    facs = loaded.ids["factions"]

    # ERROR-level: things that break a campaign start.
    if loaded.meta:
        start = (loaded.meta.defaults or {}).get("starting_location")
        if start and start not in locs:
            _err(report, "world.yaml defaults", start, "location")

    for (kind, eid), ent in loaded.entities.items():
        where = f"{kind}/{eid}.md"
        if kind == "greetings":
            g: Greeting = ent  # type: ignore[assignment]
            if g.starting_location and g.starting_location not in locs:
                _err(report, where, g.starting_location, "location")
            for c in g.present_characters:
                if c not in chars:
                    _err(report, where, c, "character")
            if g.pov_character and g.pov_character not in chars:
                _err(report, where, g.pov_character, "character")

        elif kind == "locations":
            loc: Location = ent  # type: ignore[assignment]
            if loc.parent_id and loc.parent_id not in locs:
                _warn(report, where, loc.parent_id, "location")
            for conn in loc.connections:
                if conn.to and conn.to not in locs:
                    _warn(report, where, conn.to, "location")
            for occ in loc.typical_occupants:
                if occ not in chars:
                    _warn(report, where, occ, "character")

        elif kind == "factions":
            fac: Faction = ent  # type: ignore[assignment]
            if fac.base_location and fac.base_location not in locs:
                _warn(report, where, fac.base_location, "location")
            for c in (*fac.leaders, *fac.members):
                if c not in chars:
                    _warn(report, where, c, "character")
            for f in (*fac.allies, *fac.rivals):
                if f not in facs:
                    _warn(report, where, f, "faction")

        elif kind == "items":
            it: Item = ent  # type: ignore[assignment]
            if it.current_holder and it.current_holder not in chars:
                _warn(report, where, it.current_holder, "character")

        elif kind == "lore":
            lore: LoreEntry = ent  # type: ignore[assignment]
            for r in lore.related_locations:
                if r not in locs:
                    _warn(report, where, r, "location")
            for r in lore.related_factions:
                if r not in facs:
                    _warn(report, where, r, "faction")
            for r in lore.related_characters:
                if r not in chars:
                    _warn(report, where, r, "character")

        elif kind == "characters":
            ch: Character = ent  # type: ignore[assignment]
            for rel in ch.structural_relationships:
                ref = rel.to_ref
                if "/" in ref:
                    continue  # path-form ref (e.g. worlds/<w>/factions/<id>)
                if ref not in chars and ref not in facs:
                    _warn(report, where, ref, "character/faction")


def validate_world(world_dir: str | Path) -> Report:
    world = Path(world_dir)
    report = Report()
    if not world.is_dir():
        report.errors.append(f"{world}: not a directory")
        return report
    loaded = _load_entities(world, report)
    _check_refs(loaded, report)
    return report


def _print(report: Report) -> None:
    for w in report.warnings:
        print(f"WARN  {w}")
    for e in report.errors:
        print(f"ERROR {e}")
    print(
        f"\n{len(report.errors)} error(s), {len(report.warnings)} warning(s) — "
        f"{'OK' if report.ok else 'FAILED'}"
    )


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: validate_world.py <world-id-or-path>", file=sys.stderr)
        return 2
    world = resolve_world_dir(argv[0])
    report = validate_world(world)
    print(f"validating {world}")
    _print(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 5: Run the tests to verify they pass**

Run (from `backend/`): `uv run pytest tests/skills/test_validate_world.py -v`
Expected: 5 passed. If `test_seed_world_is_error_free` fails, inspect the reported errors: any cross-entity ref that is legitimately absent from the seed must be a WARNING, not an ERROR — move that check from `_err` to `_warn` and re-run. (The known-absent seed refs `corridor-second-floor` and `stairwell-east` are connection targets and are already warnings.)

- [ ] **Step 6: Lint/format the new Python**

Run (from `backend/`): `uv run ruff check ../.claude/skills/create-world/scripts/validate_world.py tests/skills/ && uv run ruff format --check ../.claude/skills/create-world/scripts/validate_world.py tests/skills/`
Expected: passes. If format check fails, run `uv run ruff format ../.claude/skills/create-world/scripts/validate_world.py tests/skills/` and re-run.

- [ ] **Step 7: Commit**

```bash
git add .claude/skills/create-world/scripts/validate_world.py backend/tests/skills/
git commit -m "feat(skill): add create-world world validator with tests"
```

---

## Task 6: End-to-end smoke + final verification

**Files:** none (verification only)

- [ ] **Step 1: Validate the seed world via the CLI**

Run (from `backend/`): `uv run python ../.claude/skills/create-world/scripts/validate_world.py ../backend/src/grimoire/seed/library/worlds/sakura-high`
Expected: prints warnings (e.g. unresolved `corridor-second-floor`, `stairwell-east`, unknown `register`/`season_constraint`) and `0 error(s), N warning(s) — OK`, exit 0.

- [ ] **Step 2: Run the full skills test subset once more**

Run (from `backend/`): `uv run pytest tests/skills/ -v`
Expected: all pass.

- [ ] **Step 3: Confirm skill files are present and well-formed**

Run: `ls -R .claude/skills/create-world`
Expected: `SKILL.md`, `references/{entity-fields,card-lorebook-formats,quality-bar}.md`, `scripts/validate_world.py`.

- [ ] **Step 4: Final commit (if anything was adjusted)**

```bash
git add -A
git commit -m "chore(skill): finalize create-world skill" || echo "nothing to commit"
```

---

## Self-Review notes

- **Spec coverage:** layout (Tasks 1–5), data-root resolution (Task 1 SKILL.md + Task 5 `resolve_data_root`), create/update workflow (Task 1), plan→confirm gate (Task 1 step 3), ingestion mapping (Task 3), quality bar (Task 4), entity field docs (Task 2), validator round-trip + ref integrity + exit code (Task 5), seed-clean + broken-fixture tests (Task 5), CLI smoke (Task 6). All spec sections map to a task.
- **Type consistency:** the validator uses `Report.ok`/`errors`/`warnings`, `validate_world()`, `resolve_world_dir()`, `KINDS` consistently across script and tests; model imports match the authoritative modules.
- **Known-warning seed refs** (`corridor-second-floor`, `stairwell-east`, `register`, `season_constraint`) are intentionally non-blocking so the seed validates clean.
