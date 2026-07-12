# Mechanics Phase 3 — character sheets (draft)

**Status: draft.** Scope sketch updated for the multi-sheet-type contract in
`2026-07-12-mechanics-phase1-modules-design.md`; gets its own brainstorm →
spec → plan cycle when picked up. Issue #823. Depends on Phase 1.

## Scope

- `store/sheets.py`: campaign sheet CRUD at `<campaign>/sheets/<kind>--<id>.json`
  and world starting sheets at `<world>/sheets/<mid>/<kind>--<id>.json`;
  validation against the resolved module; derived-field computation on read
  (via `store/expressions.py`); campaign-create seeding from the world's
  matching module directory.
- Generic sheet editor/viewer in the frontend (plain field groups — pretty
  rendering is Phase 6), reachable from any sheetable entity's detail view
  in campaign context, and a world-level starting-sheet editor.
- The **unsheeted indicator**: campaign level on entity rows/detail; world
  level as a per-module coverage view (defaulting to the world's default
  module).
- Flesh both reference modules out to real (still small) sheet schemas.

## Changes vs the 2026-07-05 roadmap sketch

- Sheet creation starts with a **sheet-type picker**, filtered to the
  resolved module's types for that entity's kind; the file records
  `sheet_type`.
- Sheets extend beyond actors to **all sheetable kinds** (characters/PCs,
  items, locations, creatures, groups, lore) — the editor is kind-agnostic.
- World starting sheets are **keyed by module id**, so seeding matches the
  campaign's resolved module, not a single world schema.
- One sheet per entity per campaign; replacing the sheet type replaces the
  sheet.

## Open questions (settle at pick-up)

- Editor UX for `resource`/`track` fields (paired current/max inputs; box
  count) in the generic widget set.
- Where the campaign-level indicator lives visually (rail badge vs detail
  chip vs Overview-tab counts — the world Overview tab now exists).
- Whether changing a sheet's type preserves values for fields shared via
  common groups (probably yes — same keys carry over, orphaned keys drop
  with a confirm).
- Bulk actions: "sheet all unsheeted characters with type X"?
- How invalid sheets (module changed after creation) surface in the editor.
