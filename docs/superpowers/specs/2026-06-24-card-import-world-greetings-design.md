# Card Import → World Parts, and World-Level Greetings — Design

> A V3 card is raw material, not necessarily a single character. This spec lets you **decompose
> any card into a world's parts** — premise/lore, locations, and scene-opener greetings — on one
> sectioned review screen, routing each piece, into a **new or existing world**. It makes
> **greetings optional-character** so a scenario card's openers can belong to the world itself, and
> adds a **convert lore → bare character** action. All cards are structurally identical V3 cards;
> their "kind" is semantic, so the target is always **chosen by the user**, never auto-detected.

**Status:** Design — not yet implemented
**Date:** 2026-06-24
**Branch:** `card-import-world-greetings` (off `main`)
**Builds on:**
[`2026-06-21-character-cards-design.md`](2026-06-21-character-cards-design.md) (the `cards` parser,
character containers),
[`2026-06-22-lorebook-import-design.md`](2026-06-22-lorebook-import-design.md) (parse→review→commit,
`lorebook._normalize`, `create_entity`), and
[`2026-06-22-greetings-plotmaps-design.md`](2026-06-22-greetings-plotmaps-design.md) (world
greetings, `start_from_greeting`, availability).

## Purpose

Today importing a card always creates a **character**, and greetings must name a character. But
real cards are used as scenarios, locations, and whole world-bibles (e.g. a "location" card whose
`description` is actually a character, whose `creator_notes` is the place's backstory, and whose
`character_book` holds keyed lore; or a "scenario" card with 48 lore entries and 97 opener
greetings). This spec turns a card into the **set of world parts it really contains**, reviewed and
routed by the user, and lets a scenario card's openers live at the **world** level (no character),
so worlds like "characters created per campaign" work.

## Non-goals (this iteration)

- **No auto-detection of card kind.** All cards are V3; the user picks targets. (We *suggest*
  defaults, never decide.)
- **No LLM multi-character splitting.** The Characters section is a deterministic **seam**: today
  it points you to the existing character import; a later, key-gated "Suggest characters" can
  populate reviewable character rows into the same screen with no architectural change.
- **No character creation inside the unified commit.** Making a character from a card stays in the
  existing Characters-tab import (which preserves PNG-bytes avatars). The unified importer writes
  **lore/locations + greetings** only — avoiding card/avatar byte round-tripping.
- **No dedup / merge-by-identity.** Importing several cards into one world is additive; each import
  creates new uniquified entities/greetings (consistent with `create_entity`).
- **No new entity kind.** "Scenario" is *not* a stored type — a scenario card becomes a **world**
  plus ordinary lore/locations/greetings. "Permanent" context is just grimoire's existing
  keyless = always-on rule.

## Part 1 — World-level greetings (the enabling change)

A greeting may now omit its character: an **empty `character`** ⇒ a **world greeting**.

- `greetings.create_greeting(root, name, character="", version="", body=…, …)` already stores
  `character`/`version` as strings; this spec makes `""` a first-class, supported value (no schema
  change). `_meta_dict` already defaults them to `""`.
- `playing.start_from_greeting(cid, sid, gid)` — **skip the auto-cast when `character` is empty**:

  ```python
  g = greetings.read_greeting(wroot, gid)["meta"]
  ...
  if g["character"]:
      appearances.appear(cid, sid, "characters", g["character"], g["version"], "npc")
  # then seed the scene body as today (substitution unchanged)
  ```

  A world greeting therefore **seeds the scene's opening text without forcing a character** — you
  cast or create characters afterward (the Oyakodon "per-campaign characters" case).
- **Availability / gating unchanged.** `requires_tags` gating, the played-set, and plot-map nodes
  all still apply; a world greeting is simply a character-less node. (`greetings.availability`
  already ignores `character`/`version` entirely — it works off the played set, player tags, and
  plot-map edges — so it needs no change.)
- **`GreetingEditor`** gains a **"— World (no character) —"** choice in the character picker; when
  selected, the version select is hidden and `character`/`version` commit as `""`.

## Part 2 — Flatten (card → markdown body)

`cardimport.flatten(card_data) -> str` composes the card's **static-setting** fields into a markdown
body, non-empty only, in this order, each under a `##` header:

| header | source field |
|--------|--------------|
| Description | `description` |
| Personality | `personality` |
| Scenario | `scenario` |
| Notes | `creator_notes` |

**Excluded** from the body: `first_mes` and `alternate_greetings` (these are *openers* → greetings,
Part 3); `mes_example`, `system_prompt`, `post_history_instructions` (dialogue examples / LLM-control,
not world setting). Nothing is lost: a user who also wants the full card as a character imports it via
the Characters tab, which keeps every field.

## Part 3 — Unified import: parse → sectioned review → commit

A two-call flow (like the lorebook importer), world-agnostic so it can **create** a world.

### `POST /api/cards/preview` (multipart: `file`, `format=lorebook|json|png|charx`) → preview

`cardimport.preview(data, fmt) -> dict`:

```json
{
  "suggested_world": "Manor Vows",
  "entries": [
    { "section": "premise", "name": "Manor Vows", "keys": "", "body": "## Description…", "category": "lore" },
    { "section": "lore", "name": "Althenian Religion", "keys": "Goddess,Althena,religion", "body": "…", "category": "lore" }
  ],
  "greetings": [
    { "name": "Manor Vows — opener 1", "body": "*{{user}} was left in the grand foyer…*" }
  ]
}
```

- **`entries`** — the **premise** row first (`section:"premise"`, `keys:""` so it defaults to
  permanent/always-on, body = `flatten(data)`, name = card name, `category:"lore"`), followed by
  the **`character_book`** rows from `lorebook._normalize(data.character_book)` (`section:"lore"`,
  their keys, each routable `lore`↔`locations`). For `format=="lorebook"` (a bare world-info file)
  there is **no premise row and no greetings** — just the normalized lore rows.
- **`greetings`** — `first_mes` (if non-empty) then each `alternate_greetings[i]`, named
  `"<card name> — opener <n>"` (1-based), body = the opener text. Empty bodies skipped.
- Pure; writes nothing. `cards.CardParseError` / bad input → `400`.

### `POST /api/cards/commit` (JSON payload) → `{world, created}`

```json
{
  "world": { "mode": "new", "name": "Manor Vows" },      // or {"mode":"existing","id":"<wid>"}
  "entries":  [ { "name": "...", "keys": "...", "body": "...", "category": "lore|locations" } ],
  "greetings":[ { "name": "...", "body": "...", "character": "", "version": "" } ]
}
```

`cardimport.commit(payload) -> dict`:
1. **Resolve world.** `mode:"new"` → `worlds.create_world(name)` → `wid`; `mode:"existing"` →
   validate `id` exists. Get `wroot`.
2. **Entries** → `entities.create_entity(wroot, category, name, body, keys)` each (the same path
   `lorebook.commit` uses; `category ∉ {lore, locations}` → `400`). `keys==""` ⇒ keyless/always-on.
3. **Greetings** → `greetings.create_greeting(wroot, name, character, version, body)` each
   (`character` defaults `""` ⇒ world greeting). A non-empty `character` that isn't a real
   character/version → `400`.
4. Return `{ "world": wid, "created": { "lore": n, "locations": n, "greetings": n } }`.

Only **included** rows are sent — the frontend omits anything the user unchecked. Commit is
deterministic and stateless; nothing but the edited payload crosses the wire (no card/avatar bytes).

### The review screen (frontend `CardImport` component)

One screen, fed by `preview`, with collapsible sections; every row has a **keep** checkbox and
inline edits:

- **World target** (top) — radio: **New world** (text field prefilled with `suggested_world`) **or**
  **Existing world** (a world picker). When launched from inside a world, defaults to that world.
- **Premise** — the single premise entry: editable name, **category** `lore`↔`locations`, a
  **Permanent** toggle (on ⇒ `keys` cleared/always-on; off ⇒ `keys` editable, prefilled with the
  name).
- **Lore** — the `character_book` rows in the existing table (name / keys / category `lore`↔
  `locations` / body preview / keep).
- **Greetings** — rows with editable name, body preview, **keep** toggle, and an optional
  **character** binding (default "— World —"; a select of the target world's characters when an
  existing world is chosen).
- **Characters** — a **seam**: an explanatory note ("To add characters, use the Characters tab's
  Import, or create them per campaign. LLM-assisted suggestions will appear here later."). No
  control wired this iteration.
- **Commit** → posts the included rows; on success shows counts and (if a new world) links to it.

This `CardImport` **replaces the Lore-tab's `LorebookImport`** (which only pulled `character_book`)
and is also mounted on the **Worlds list** page for world-creating imports.

## Part 4 — Convert lore → bare character

`POST /api/worlds/{wid}/lore/{eid}/convert-to-character` →
`characters.from_entity(wroot, eid) -> {character, version}`:

- Read the lore entry; `create_character(wroot, name=entry.name, card=blank_card(name))` then set
  the card's `description = entry.body` and `tags = entry.keys.split(",")`; **delete** the lore
  entry (a move). Returns the new character id + default version id.
- Route maps `EntityNotFound` → `404`. Frontend: a **"Convert to character"** action on each lore
  row in `EntityEditor` (kind `lore`), with a confirm; on success refreshes both lists.

## Backend — modules & routes

```
backend/src/grimoire/store/
  cardimport.py   # NEW — flatten(); preview(data, fmt); commit(payload). Depends on cards,
                  #       lorebook (_normalize), entities, greetings, worlds. One responsibility:
                  #       translate a card into routed world parts.
  greetings.py    # start_from_greeting/availability tolerate empty character (Part 1)
  characters.py   # from_entity() (Part 4)
  worlds.py       # create_world(name) -> wid reused (exists)
```

```
# routes.py (new)
POST /api/cards/preview                                   (multipart file, format)  → preview
POST /api/cards/commit                                    (JSON payload)             → {world, created}
POST /api/worlds/{wid}/lore/{eid}/convert-to-character                               → {character, version}
```

`/api/cards/*` are **top-level** (world-agnostic — they can create a world). The convert route is
world-scoped and lore-only.

## Frontend — deltas

- **`CardImport`** (new) — the sectioned review above; mounted in the **Lore tab** (replacing
  `LorebookImport`, defaulting world-target to the current world) and on the **Worlds list**
  (defaulting to "New world").
- **`GreetingEditor`** — "— World (no character) —" option; hide the version select and commit
  `""`/`""` when chosen; render world greetings (no character) in the list.
- **`EntityEditor`** (lore) — "Convert to character" row action.
- **`api/client.ts`** — `cardsPreview(file, format)`, `cardsCommit(payload)`,
  `convertLoreToCharacter(wid, eid)`; types for the preview/commit payloads.
- All components use **theme tokens only**.

## Error handling

- Unparseable card / bad lorebook → `400` (readable). Unknown `format` → `400`.
- `commit` with `mode:"existing"` and a missing world id → `404`; an entry `category` outside
  `{lore, locations}` → `400`; a greeting naming a non-existent character/version → `400`.
- A card with no `character_book` and no openers → a preview with just the premise row (and no
  greetings); a bare `lorebook` → lore rows only.
- World/entity/greeting ids uniquify on collision (never an error); re-importing duplicates.
- Convert on a non-lore kind or missing entity → `404`.
- Empty-body greetings/entries are dropped at preview; unchecked rows are dropped at commit.

## Testing

**Backend (pytest, temp `GRIMOIRE_HOME`):**
- `flatten` — composes only the four static fields, under headers, skipping empties; excludes
  `first_mes`/`mes_example`/`system_prompt`/`post_history`.
- `preview` — a real-shaped card yields a premise row (keys `""`), `character_book` → lore rows,
  `first_mes`+`alternate_greetings` → named greeting rows; a bare `lorebook` → lore rows only, no
  premise/greetings; garbage → `400`.
- `commit` — `mode:"new"` creates the world and writes entries (lore + a routed location) and world
  greetings; `mode:"existing"` merges into it; counts returned; unchecked/omitted rows not written;
  bad category / bad world id / bad greeting character → `400`/`404`. An imported keyless premise
  **activates always-on** through the existing builder (end-to-end sanity).
- **World greetings** — `create_greeting` with `character=""` round-trips; `start_from_greeting`
  on a world greeting seeds the body and **casts no character**; on a character greeting still
  casts; availability lists world greetings and gates them by `requires_tags`.
- **convert** — `from_entity` creates a character (description=body, tags=keys) and removes the lore
  entry; missing entity → error.

**Frontend (light):**
- `CardImport` renders sections from a mocked preview; unchecking a row omits it from the commit
  payload; the world radio toggles new-name vs existing-picker; premise "Permanent" clears keys.
- `GreetingEditor` can save a world greeting (no character) and shows it in the list.
- `EntityEditor` lore row "Convert to character" calls the route and refreshes.

## Phasing (for the implementation plan)

1. **World-level greetings** — `start_from_greeting`/availability tolerate empty character; tests.
2. **`cardimport.py`** — `flatten` + `preview` + `commit`; routes; backend tests.
3. **Convert lore → character** — `characters.from_entity` + route + tests.
4. **Frontend** — `CardImport` sectioned review (Lore tab + Worlds list); `GreetingEditor` world
   option; `EntityEditor` convert action; client types; light tests.

## What grows later (not built now)

- **LLM "Suggest characters"** — populates proposed character rows into the same review screen
  (key-gated); the Characters section is the seam.
- **Character creation inside the unified commit** (carrying PNG-bytes avatars) if wanted.
- **Greeting↔character rebinding** and richer plot-map editing for world greetings.
- **Identity-aware merge/dedup** when importing overlapping cards into one world.
