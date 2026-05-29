# Guided world hub

> Sub-project 4 of 4 for issue #441. Depends conceptually on SP1–SP3 but touches a
> different surface (the world detail page). Can ship independently once the
> create/edit improvements land.

## Problem

The world detail page (`WorldDetailView.tsx`) is a header (name, id, version, a
row of action buttons) plus a flat strip of tabs (Characters, Monsters, Items,
Locations, Lore, Factions, Greetings, Meta, Dependent campaigns). It reads as
disconnected CRUD: no sense of how complete a world is, how much is in it, or what
to do next. For "people with worlds to make," the landing page should orient and
guide, not just list tabs.

## Goal (SP4)

Turn the world detail landing view into a **hub** that:
1. Shows **setup progress** — a short, honest checklist of what a usable world
   has and what's missing.
2. Shows **per-kind counts** at a glance.
3. Offers **suggested next actions**, especially contextual prompts for empty
   kinds ("+ Add a starting location").
4. Cleans up the header/tab layout so actions and navigation are less cramped.

## Non-goals (SP4)

- A world-level token total (the brainstorm scoped token counts to the editor
  header (SP1) and list cards (SP2), not the hub).
- Changing what the entity tabs themselves do (still `EntityListView`).
- Onboarding tours / coach-marks.

## Design

### 1. Hub as the world index route

Today `WorldDetailView` renders header + tabs + `<Outlet>`, and the index route
redirects/shows the first tab's content. SP4 adds a real **index element**: when
no entity tab is selected, render `<WorldHub>` (the landing content) above/in
place of an empty outlet. Selecting a tab shows that tab as today. The hub is the
"home" of a world.

### 2. Entity counts — backend summary endpoint

Add `GET /library/worlds/{world_id}/summary` returning counts per kind plus a few
booleans the progress checklist needs:

```json
{
  "counts": { "characters": 3, "locations": 0, "items": 1, "lore": 2,
              "factions": 0, "monsters": 0, "greetings": 1 },
  "has_description": true,
  "has_genre": false
}
```

Counts come from the existing library index (no full-entity reads). This avoids 6
parallel `listEntities` round-trips on every hub view. (Fallback if the endpoint
is deferred: the hub fetches `listEntities` per kind in parallel and uses
`data.length` — correct but heavier; the endpoint is preferred.)

### 3. `<WorldHub>` content

```
Ravenmark
A kingdom rotting from within…                     [Fork] [Import] [Delete]
id: ravenmark · v3 · Grimdark fantasy

World setup ███████░░░  70%
  ✓ Description   ✓ Genre   ✓ 3 characters
  ▢ Add a location   ▢ Add a greeting to start play

Contents
 ┌────────────┬───────────┬──────────┬──────────┐
 │ Characters │ Locations │  Lore    │ Factions │
 │     3      │  0 — add  │    2     │ 0 — add  │
 └────────────┴───────────┴──────────┴──────────┘
 Items 1 · Monsters 0 · Greetings 1

Suggested next
 [+ Add a starting location]  [+ Write an opening greeting]
```

- **Progress**: a small, transparent heuristic — checklist items = has
  description, has genre, ≥1 character, ≥1 location, ≥1 greeting. Percent =
  satisfied / total. Each item links to the relevant tab/create form. The
  heuristic is deliberately simple and documented in-code so it reads as guidance,
  not a gate.
- **Contents grid**: per-kind count cards linking to each tab. Empty kinds show a
  muted "0 — add" affordance.
- **Suggested next**: derived from unmet checklist items (e.g. no location → "Add
  a starting location"; no greeting → "Write an opening greeting"). Buttons deep-
  link to the kind's tab with its create form opened (a `?create=1` query the tab
  reads, or navigation to the tab — see §4).

### 4. Layout cleanup

- Move the action buttons (Fork / Import / Delete / Refresh) into a compact action
  menu or a right-aligned group so the heading isn't a long button row
  (`WorldDetailView.tsx:113-142`).
- Group the tabs into "Contents" (the entity kinds) vs "Settings" (Meta, Dependent
  campaigns) for less visual noise. Tabs themselves unchanged.
- The "suggested next" deep-link opens the target tab; the tab's create form auto-
  opens when navigated to with a `create` intent (small addition to
  `EntityListView`, reusing the SP3 create form).

## Components / files

New:
- `frontend/src/routes/library/WorldHub.tsx`.
- Backend: `GET /library/worlds/{world_id}/summary` in `api/library.py` + a
  `worldSummary(worldId)` client in `api/library/worlds.ts`.

Changed:
- `WorldDetailView.tsx` — render `<WorldHub>` as the index; regroup tabs; tidy the
  action row.
- `EntityListView.tsx` — open the create form when navigated with a `create`
  intent.

## Testing

- **Backend**: `summary` returns correct counts for a world with mixed entities
  and `has_description`/`has_genre` reflecting `world.yaml`; empty world → all
  zeros/false.
- **Component**: `<WorldHub>` renders counts and progress from a summary fixture;
  an empty kind shows the "add" affordance; "suggested next" lists exactly the
  unmet checklist items; clicking a suggestion navigates to the right tab with the
  create intent.
- **Scenario**: load a world's hub, confirm progress reflects its contents; add a
  location and confirm the suggestion disappears and the count updates.

## Risks / trade-offs

- **Progress heuristic is opinionated.** Kept simple and clearly "guidance"; not a
  blocker, no enforcement. Documented in-code so its intent is obvious.
- **Summary endpoint vs client fetch.** The endpoint is the clean path; the
  client-side fallback exists if backend work must be deferred, at the cost of
  extra round-trips on hub load.
- **Tab regrouping** changes a familiar layout; keep labels identical so muscle
  memory survives, only the grouping changes.
