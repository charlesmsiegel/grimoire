# New Scene Workflow

## Overview

A player-facing workflow for starting new scenes between play sessions or after ending a scene. Combines a persistent **Scene Ledger** of curated scene ideas with fresh LLM-generated suggestions, a preview/confirm step, and first-post generation.

**Entry points:**
- Auto-prompted after clicking "End Scene" (the suggestion picker replaces the play area)
- Manual "New Scene" button in the side panel (available when no open scene is active)

## Scene Ledger

### Data Model

Per-campaign SQLite table storing scene ideas from all sources.

```sql
CREATE TABLE scene_ledger (
    id          TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL REFERENCES campaigns(id),
    summary     TEXT NOT NULL,        -- one-sentence scene hook
    greeting_id TEXT,                  -- set if this idea maps to a greeting
    source      TEXT NOT NULL,         -- 'greeting' | 'llm' | 'user'
    status      TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'used' | 'dismissed'
    created_at  TEXT NOT NULL,
    used_in_scene_id TEXT,            -- set when the idea becomes a real scene
    FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);
CREATE INDEX idx_scene_ledger_campaign ON scene_ledger(campaign_id, status);
```

### Lifecycle

- **Pre-populated on campaign creation** with all greetings from the campaign's worlds. Each greeting becomes a ledger item with `source=greeting` and `greeting_id` set. The greeting used for the initial scene seed is immediately marked `status=used`.
- **LLM suggestions** are added when generated during the suggestion flow. Suggestions the player doesn't pick are saved to the ledger (`source=llm`) for future use.
- **Player-curated** via a ledger management UI. Players can dismiss items that are no longer relevant or possible, and restore previously dismissed items.
- **Marked used** when a ledger item is selected and confirmed as a real scene. `used_in_scene_id` is set to the created scene's ID.

### Ledger Management UI

Accessible from the side panel as a "Scene Ledger" button/dialog. Shows all ledger items grouped by status (active first, then used, then dismissed). Each item shows its summary, source badge (Greeting/Generated/Custom), and controls to dismiss or restore.

## Suggestion Flow

### Step 1: Scene Suggestions

The suggestion picker replaces the play area. It shows:

- **Up to 3 items from the ledger** (status=`active`, ordered by creation — greetings first since they're pre-populated)
- **At least 2 freshly LLM-generated suggestions** (not in the ledger yet)
- A **free-text input** for the player to describe a custom scene
- A **Refresh button** to regenerate the LLM slots

Greeting-sourced items are visually tagged with a greeting badge.

### Step 2: Player Chooses

Three paths depending on what the player clicks:

1. **Clicks a ledger item** — proceeds to preview. The ledger item will be marked `used` on confirm.
2. **Clicks a generated suggestion** — proceeds to preview. The other generated suggestion(s) are saved to the ledger as `source=llm`. The chosen one becomes a scene directly (not saved to ledger).
3. **Types custom text** — proceeds to preview. Not saved to the ledger.

### Step 3: Preview & Confirm

The preview panel shows resolved scene metadata:
- **Title** (LLM-generated from the suggestion/description)
- **Location** (resolved from context)
- **In-game time** (advanced from last scene's end time)
- **Cast** (present characters, including PCs)
- **First post source** — indicates which generation path will be used (see below)

All fields are editable so the player can tweak before confirming. "Back" returns to the picker; "Start Scene" creates the scene.

### Step 4: Scene Creation

On confirm:
1. Create the real Scene via `SceneManager.start_scene()` with the previewed metadata
2. Generate the first post (see First Post Generation below)
3. Mark the ledger item as `used` (if applicable)
4. Transition the play view to normal scene mode with the first post displayed

## First Post Generation

Three paths depending on how the scene was initiated:

### Path A: Verbatim Greeting
The player selected a greeting-backed ledger item. The greeting's `body` is used as-is with standard placeholder substitution (`{{user}}`, `{{char}}`, `[PC]`). No LLM call. Uses the existing `_seed_greeting_first_post` logic.

### Path B: Adapted Greeting
The player selected a greeting-backed ledger item but campaign context has advanced significantly beyond the greeting's original premise. The LLM receives the greeting body plus recent context (scene summaries, continuity facts) and produces an adapted opening that preserves the greeting's tone and core setup while accounting for plot developments.

### Path C: Fully Generated
No greeting involved. The LLM generates the opening narrator post from scratch using the scene setup (title, location, cast) and campaign context. Follows the same narrator voice conventions the orchestrator uses for regular turns.

The `preview` endpoint determines the path and surfaces it in the preview panel so the player knows what to expect (e.g., "Opening from greeting: Camp Confrontation" vs. "Opening will be generated").

## Suggestion Generation (LLM Prompt)

### Context Assembly

The suggestion endpoint gathers:
- **Recent scene summaries** — final summaries from the last 2-3 closed scenes
- **Open continuity threads** — unresolved commitments, active character facts
- **Unused greetings** — names and one-line summaries of active greeting-sourced ledger items
- **Campaign state** — active PCs, last known location, in-game time

### Prompt Structure

The LLM is asked to produce scene suggestions as structured JSON. Each suggestion includes:
- `summary` — one sentence describing the scene
- `proposed_location` — where the scene takes place
- `proposed_cast` — character refs likely involved

The prompt specifies the minimum count (2) and that suggestions should be diverse: some advancing the main plot, some exploring character relationships, some introducing new complications.

### Ledger Interaction

- If the ledger already has 3+ active items, the endpoint returns those plus generates 2 fresh ones
- If fewer than 3, it returns what's available and still generates at least 2
- The "Refresh" button regenerates only the LLM slots; ledger items stay

## API Endpoints

### `POST /campaigns/{campaign_id}/scenes/suggest`

Returns scene suggestions for the picker.

**Response:**
```json
{
  "ledger_picks": [
    { "ledger_id": "...", "summary": "...", "greeting_id": "...", "source": "greeting" }
  ],
  "generated": [
    { "summary": "...", "proposed_location": "...", "proposed_cast": ["..."] }
  ]
}
```

### `POST /campaigns/{campaign_id}/scenes/preview`

Resolves a player's choice into concrete scene metadata.

**Request** (one of three shapes):
```json
{ "ledger_id": "led-123" }
{ "generated_index": 0, "generated_suggestions": [...] }
{ "custom_text": "The party sneaks into the castle under cover of darkness" }
```

**Response:**
```json
{
  "title": "...",
  "location_ref": "...",
  "in_game_start": "...",
  "present_character_refs": ["..."],
  "present_pc_refs": ["..."],
  "greeting_id": null,
  "first_post_source": "generated",
  "ledger_id": null
}
```

### `POST /campaigns/{campaign_id}/scenes/start`

Creates the scene and first post.

**Request:** The preview response payload, optionally with player edits, plus `confirm: true`.

**Response:** `{ scene: ApiScene, first_post: ApiPost }`

**Side effects:**
- Scene and first post created on disk
- Ledger item marked `used` (if `ledger_id` present)
- Unchosen generated suggestions saved to ledger as `source=llm`
- `SCENE_STARTED` event emitted

### `GET /campaigns/{campaign_id}/scene-ledger`

Returns all ledger items. Supports `?status=active` query param filter.

### `PATCH /campaigns/{campaign_id}/scene-ledger/{item_id}`

Updates a ledger item's status. Request: `{ "status": "dismissed" | "active" }`.

## Frontend Architecture

### New Components

- **`SceneSuggestionView`** — Replaces play area during scene selection. Contains suggestion cards, free-text input, and refresh button.
- **`ScenePreviewPanel`** — Shows resolved scene metadata with editable fields, Back and "Start Scene" buttons.
- **`SceneLedgerDialog`** — Side panel dialog for managing ledger items (dismiss/restore).

### Play State Machine

New `mode` field in `PlayState`:

```
play → suggesting → picking → previewing → creating → play
```

| Transition | Trigger |
|---|---|
| `play → suggesting` | End scene completes, or player clicks "New Scene" |
| `suggesting → picking` | Suggestions loaded from API |
| `picking → previewing` | Player clicks suggestion / types custom |
| `previewing → picking` | Player clicks Back |
| `previewing → creating` | Player clicks "Start Scene" |
| `creating → play` | Scene + first post created, view transitions to normal scene |

### Side Panel Changes

- "New Scene" button in Quick Actions, enabled when no open scene or current scene is closed
- "Scene Ledger" button opens the `SceneLedgerDialog`

## Migration

One new migration adding the `scene_ledger` table. No existing tables are modified.

## Out of Scope

- Branch-awareness (ledger is per-campaign only; see #461 for branch removal)
- Automatic scene suggestions without player interaction (the orchestrator's boundary detection remains separate)
- Multi-scene planning (suggesting a sequence of scenes rather than one at a time)
