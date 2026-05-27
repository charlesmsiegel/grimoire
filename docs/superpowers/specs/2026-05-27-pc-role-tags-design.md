# PC Role Tags — Design Spec

**Issue:** #470
**Date:** 2026-05-27
**Status:** Active

## Problem

Worlds with multiple entry points (e.g., ashgrove Regency has greetings for vivienne, winifred's husband, giselle's husband) need a way to match PCs to the greetings designed for them. Currently there is no mechanism — the wizard shows all greetings in a flat dropdown regardless of which PC is selected.

## Solution

Add `role_tags` to Characters, Greetings, and Worlds. A greeting's role_tags declare which PC roles it serves; a PC's role_tags declare which roles it fills. The wizard filters greetings to those matching the selected PCs.

## Data Model

### §1 WorldMeta

Add `pc_role_tags: list[str]` to `WorldMeta` and `world.yaml`.

This is the world author's canonical list of roles a PC can fill in this world (e.g., `["vivienne", "winifred-husband", "giselle-husband", "debutante"]`). The wizard reads this to present a pick-list when assigning role tags to PCs during campaign creation.

### §2 Character

Add `role_tags: list[str]` to `Character` and `CharacterData` in `types/characters.py`.

Library characters (especially those with `role: pc`) declare what roles they fill by default. These are functional tags for matching, distinct from the existing narrative `tags` field.

Frontmatter example:
```yaml
role_tags: [transfer-student]
```

### §3 Greeting

Add `role_tags: list[str]` to `Greeting` in `types/composition.py`.

A greeting with `role_tags: ["vivienne"]` is designed for PCs whose role_tags include `"vivienne"`. A greeting with empty `role_tags` is **universal** — it matches any PC.

Frontmatter example:
```yaml
role_tags: [transfer-student]
```

### §4 campaign_pcs (DB)

Add `role_tags TEXT` column to `campaign_pcs` table. Stored as a JSON-encoded array (e.g., `'["vivienne","debutante"]'`). This is the campaign-scoped override — a PC might fill different roles in different campaigns.

New migration file required.

## Matching Logic

Matching runs in the **frontend** (both greeting list and PC list are already in wizard state). No new backend endpoint needed.

Rules:
- Greeting has role_tags AND PC has role_tags → **match** if set intersection is non-empty
- Greeting has no role_tags (empty list) → **universal**, always shown
- PC has no role_tags → matches **only universal** greetings
- Multiple PCs → union their role_tags, then filter greetings against the union

## Backend Changes

### §5 Models

- `types/characters.py`: Add `role_tags: list[str] = Field(default_factory=list)` to `Character` and `CharacterData`
- `types/composition.py`: Add `role_tags: list[str] = Field(default_factory=list)` to `Greeting`; add `pc_role_tags: list[str] = Field(default_factory=list)` to `WorldMeta`

### §6 Library Service

- `_greeting_from_row`: parse `role_tags` from frontmatter
- `_world_meta_from_row`: parse `pc_role_tags` from frontmatter

### §7 State Store — campaign_pcs

- Add `role_tags` parameter to `add_pc` and include in INSERT
- Return `role_tags` in `list_pcs` query results
- Parse JSON on read, serialize on write

### §8 Characters Service

- `add_pc` accepts optional `role_tags: list[str]`
- `list_pcs` returns `PCEntry` with `role_tags`

### §9 PC API

- `AddPCPayload` gains `role_tags: list[str] = Field(default_factory=list)`
- Pass through to `CharactersService.add_pc`

### §10 DB Migration

New migration adding `role_tags TEXT DEFAULT '[]'` to `campaign_pcs`.

## Frontend Changes

### §11 API Types

- `api/library/worlds.ts` `Greeting` interface: add `role_tags: string[]`
- `api/wizard.ts` `GreetingSummary`: add `role_tags: string[]`; `fromGreeting` pipes it through
- `api/wizard.ts` `CharacterSummary`: add `role_tags: string[]`; `fromCharacterEntity` extracts from frontmatter
- `api/wizard.ts` `WorldSummary`: add `pc_role_tags: string[]`

### §12 Wizard Types

- `DraftPC`: add `role_tags: string[]`
- `addCampaignPC` call includes `role_tags` in payload

### §13 StepPCs

- Union `pc_role_tags` from all composed worlds into available tags
- Per PC: show available role tags as a checkbox/chip picker
- Library PCs with existing `role_tags` in frontmatter → pre-select those
- New/emergent PCs → start with empty role_tags

### §14 StepStartingScene

- Compute union of all draft PCs' role_tags
- Filter greetings: show greeting if `greeting.role_tags` is empty (universal) OR intersects with the PC role_tags union
- When no PCs have role_tags, show only universal greetings

### §15 Greeting Form (Library Editor)

- `GreetingFormValue`: add `roleTagsText: string`
- `GreetingFormFields`: add role_tags input (comma-separated, like existing tags)
- `greetingFormToPayload`: emit `role_tags` array in frontmatter
- `emptyGreetingForm`: include `roleTagsText: ""`

## Seed Data

### §16 sakura-high

**world.yaml**: add `pc_role_tags: ["transfer-student", "new-girl"]`

**Characters**:
- `haruto-takeda.md`: add `role_tags: [transfer-student]`
- `mei-tachibana.md`: add `role_tags: [new-girl]`

**Greetings**:
- `transfer-student-arrival.md`: add `role_tags: [transfer-student]` (this greeting is written from the transfer student's POV)
- `rooftop-lunch.md`: no role_tags (universal)
- `festival-prep.md`: no role_tags (universal)

## Testing

### §17 Backend Tests

- Unit test: `role_tags` round-trips through `Character` / `Greeting` / `WorldMeta` model construction
- Unit test: `_greeting_from_row` parses `role_tags` from frontmatter
- Unit test: `_world_meta_from_row` parses `pc_role_tags` from frontmatter
- Integration test: `add_pc` with `role_tags`, `list_pcs` returns them
- Migration test: column exists after migration runs

### §18 Frontend Tests

- Unit test: matching logic (greeting role_tags × PC role_tags → show/hide)
- Unit test: `fromGreeting` includes `role_tags`
- Unit test: `fromCharacterEntity` includes `role_tags`
