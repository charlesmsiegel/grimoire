# Frontend — Status & Deferred Scope

> **Status: status/scope note — NOT a design.** Records what frontend exists today and the
> backend capabilities that currently have **no UI**, so the deferred work isn't lost. Each item
> below becomes (part of) its own brainstorm → spec → plan cycle when picked up.

**Date:** 2026-06-22

## Why this exists

Every backend spec since worlds/campaigns deferred its frontend ("depends on the unbuilt
worlds/campaigns frontend shell"). Those deferrals were scattered across specs and conversation
context; this note consolidates them. It is also the practical blocker for **playtesting** the
context builder: exercising injection needs a way to cast actors into a scene, which today means
manual API calls.

## What's built (Vite + React + react-router)

`frontend/src/` — confirmed present:

- **Shell & nav:** `App.tsx` (top nav: Campaigns · Worlds · Config), theme system
  (`theme/`, tokens only), `api/client.ts` (typed: worlds, campaigns, scenes, chat/retry, config),
  `api/stream.ts` (SSE).
- **Routes:** `CampaignsView` (list + create-from-world), `CampaignView` (the play space — scenes
  sidebar, transcript, streaming input, retry, markdown), `WorldsView` (**list only**),
  `ConfigView` (model/theme/key, `ModelCombobox`).
- **Components:** `EditableRow` (rename/delete row used by the scenes sidebar).

This is the worlds/campaigns "phase 2 core loop." It predates everything below.

## What has backend + API but NO UI

Mapped to the spec that shipped the backend:

**worlds/campaigns "phase 3" (never built) — the shared scaffolding everything else needs**
- A single-world **`WorldView`** route (there is only the list).
- Shared **`EntityList` / `EntityEditor`** for locations/lore CRUD.
- **`IncomingReview`** — per-object accept/reject sync UI with the world-vs-mine conflict diff.
- World **push panel** (`GET /worlds/{wid}/campaigns` pending counts).

**character-cards**
- Character **container/version editor** (V3 card form: description/personality/scenario/
  first_mes/alternate_greetings/mes_example, set-default-version).
- **Import/export** UI (`.json` / PNG / `.charx`).
- **Suggested-cast** strip (name-mention suggestions) + dismiss.
- Character rows in `IncomingReview` (card-field conflict rendering).

**pcs-tags-actors**
- **PC editor** (persona: name/pronouns/summary/description + version management).
- World **tag vocabulary** editor; PC **tag assignment** (from the vocabulary).
- **Cast UI**: add a character or PC to a scene with **kind + role** (player/npc), the version
  picker, "use a character as a PC," and a **roster/cast panel** (from `appearances`).

**context-builder (2a)**
- Editing **`keys`** on lore/location entries (the world-info triggers) — needs the entity editor.
- A **`context_scan_depth`** setting in Config (functional via file/API today; no UI).
  *(The builder itself needs no UI to run.)*

**not-yet-built backends (their UI compounds the above)**
- **Greetings (2b):** greeting editor, the **plot-map graph editor**, scene-start-from-greeting,
  generate-opener-from-prompt.
- **Lorebook import (2c):** import + **per-entry category routing** UI.

## Dependency order (rough)

`WorldView` route + `EntityEditor` + `IncomingReview` are prerequisites; the character / PC /
tags / cast / greetings UIs build on that shell. A minimal **playtest slice** that unblocks tuning
the context builder is narrower than the full list: `WorldView` + character & PC editors + the
**cast panel** (add actor with kind/role) — the scene chat that consumes the assembled context
already exists.

## Note

The earlier `docs/superpowers/plans/2026-06-20-worlds-campaigns-frontend-core.md` is the **phase-2**
plan that was already implemented — not the deferred work above.
