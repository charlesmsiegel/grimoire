# PC Profile Improvements

## Problem

PCs are created as blank slates when added to a campaign. The `campaign_pcs` table tracks only `character_ref`, `display_name`, `owner`, and `active` — no description, goals, or narrative context. This means:

- **New inline PCs** have nothing for the LLM to work with beyond a name.
- **Imported SillyTavern cards** carry a description and voice anchor but lack campaign-specific context (goals, motivations, player guidance) and have no connection to the mechanical capabilities system.

The narrator LLM needs core identity, goals/motivations, and (when mechanics are enabled) capability awareness to produce rich, character-appropriate narration.

## Solution

Introduce a **PCProfile** — a campaign-scoped markdown file that serves as a narrative overlay for a PC. It holds campaign-specific description, goals, and player notes. The card renderer merges this profile with the library character and mechanical capabilities into a single enriched PC card for the LLM context.

## Data Model

### PCProfile

Stored as a markdown file at:

```
data/campaigns/{campaign_id}/characters/{character_id}/profile.md
```

YAML frontmatter for structured fields, markdown body for the description:

```yaml
---
character_ref: library:worlds/sakura-high/characters/aoi-shirakawa
goals:
  - Uncover the truth about her father's disappearance
  - Earn the trust of the student council
player_notes: >
  Lean into dramatic irony — she's oblivious to how
  obvious her investigation is to the people around her.
updated_at: 2026-05-26T14:30:00Z
---

A quiet, observant second-year student who hides her sharp
intellect behind a mask of polite disinterest...
```

| Field | Type | Purpose |
|-------|------|---------|
| `character_ref` | `str` | Reference to the library character |
| `goals` | `list[str]` | Current goals/motivations — what the character wants, fears, is working toward |
| `player_notes` | `str` | Meta-level guidance for the narrator — tone, themes, things to avoid. Not in-world. |
| `updated_at` | `datetime` | Last modification timestamp |
| *(body)* | `str` | Campaign-specific description — appearance, personality, backstory context |

All fields are optional. A profile can be entirely empty and the system degrades gracefully.

### Revision History

Revisions are stored as timestamped copies in a sibling directory:

```
data/campaigns/{campaign_id}/characters/{character_id}/revisions/2026-05-26T14-30-00Z.md
```

Each edit copies the current `profile.md` to `revisions/` before overwriting. The narrator does not consume revision history — it exists for the player to review character evolution.

### What This Does NOT Include

Mechanical stats and capabilities already live in the state store keyed by `(campaign_id, entity_kind, entity_id, mechanics_id)` and are retrieved via `MechanicsService.capabilities_of()`. This design surfaces them in card rendering but does not duplicate them into the profile.

The `campaign_pcs` DB table is unchanged — it remains a lightweight index for fast queries. Profile content lives entirely in the filesystem.

## Card Rendering

When the context builder calls `get_full_card()` for a PC, the rendering pipeline merges three sources:

1. **Library character** — the existing `render_full()` output (name, aliases, age, tags, description, body, voice anchor)
2. **PC profile overlay** — the campaign-scoped `profile.md`
3. **Mechanical capabilities** — retrieved via `capabilities_of()` from the state store

### Merge Rules

- **Description:** If the library character has a non-empty `description` (not empty string or whitespace-only), it takes precedence as the primary identity text. The profile body is appended under a `## Campaign Context` heading. If the library character's description is empty or whitespace-only (brand-new PC), the profile body becomes the primary description in place of the missing library description.
- **Goals:** Rendered as a `## Goals` section with a bulleted list, inserted after the identity/description block and before the voice anchor.
- **Player notes:** Rendered as a `## Player Notes` section. Framed explicitly as meta-guidance so the LLM understands these are narrator instructions, not in-world facts.
- **Capabilities:** When a mechanics module is active and the PC has a sheet, render a `## Capabilities` section listing the PC's current capabilities (name, kind, brief description). When no mechanics module is bound or the sheet is empty, this section is omitted.

### Rendered Card Structure

```
# {Name}
{aliases, age, tags}          ← existing library content
{description / body}          ← existing library content
## Campaign Context            ← from profile.md body (only if library desc exists)
## Goals                       ← from profile.md frontmatter
## Capabilities                ← from mechanics state store
## Voice                       ← existing library content
## Player Notes                ← from profile.md frontmatter (last, meta-level)
```

Only `render_full()` (used for the `LOCK_IN` PC card) gets the merged output. The compressed, voice-only, and capsule renderers are unchanged — they serve NPC tiers.

## API

### Service Layer — `CharactersService`

New methods:

- `get_pc_profile(campaign_id, character_ref) -> PCProfile | None` — reads and parses the profile markdown file.
- `save_pc_profile(campaign_id, character_ref, profile: PCProfile)` — writes the profile markdown file, archiving the previous version to `revisions/` if one existed.
- `list_pc_profile_revisions(campaign_id, character_ref) -> list[PCProfileRevision]` — lists available revision timestamps.
- `get_pc_profile_revision(campaign_id, character_ref, timestamp) -> PCProfile | None` — reads a specific historical revision.

The existing `get_full_card()` method is updated to call `get_pc_profile()` and merge it into the rendered card. It also calls `capabilities_of()` when a mechanics module is active.

### HTTP Routes

Added to the campaign PCs router (`/campaigns/{campaign_id}/pcs`):

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/{character_ref}/profile` | Fetch current profile |
| `PUT` | `/{character_ref}/profile` | Create or update profile (full replace) |
| `GET` | `/{character_ref}/profile/revisions` | List revision timestamps |
| `GET` | `/{character_ref}/profile/revisions/{timestamp}` | Fetch a specific historical revision |

PUT payload:

```json
{
  "description": "A quiet, observant second-year student...",
  "goals": ["Uncover the truth about her father's disappearance"],
  "player_notes": "Lean into dramatic irony..."
}
```

The route writes the markdown file via the service, which handles revision snapshotting. No new DB migrations.

## Frontend

### PC Addition Flow (Campaign Wizard)

When adding a PC to a campaign, the wizard gains an expandable **"PC Profile"** section below the existing name/character-ref fields:

- **Description** — textarea. Pre-populated from the library character's `description` if one exists.
- **Goals** — dynamic list input. Each goal is a short text field with add/remove controls. Starts with one empty row.
- **Player Notes** — textarea with placeholder text: "Guidance for the narrator — tone, themes, things to avoid..."

All fields are optional. The section is collapsed by default when the library character already has a rich description, expanded by default for blank-slate characters.

### Mid-Campaign Editing

The PC management panel gets an **"Edit Profile"** action that opens the same form fields, pre-populated with the current profile. On save, the frontend PUTs to the profile endpoint.

### Revision History

A **"History"** link on the edit form opens a list of revision timestamps. Selecting one shows a read-only view of that snapshot. No diffing or restore functionality in v1.

### Mechanical Sheet

Not edited here. The mechanics sheet has its own dedicated UI flow (character sheet editor). The capabilities section in the rendered card is automatic based on what's in the sheet.
