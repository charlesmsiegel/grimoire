# Mechanics Phase 8 — module authoring UI (draft)

**Status: draft.** Scope sketch updated for the multi-sheet-type contract in
`2026-07-12-mechanics-phase1-modules-design.md`; gets its own brainstorm →
spec → plan cycle when picked up. Issue #829. Last phase — needs every
format (manifest, sheets, checks, rules, layout) stable first.

## Scope

- In-app scaffold/edit of a user-library module: manifest, field groups,
  sheet types (kind, group membership, own fields, derived expressions),
  checks, rules docs + activation flags, statted content, layout.
- The Modules library page (read-only since Phase 1) grows an Edit mode for
  user-library modules following the list/detail pattern; built-ins stay
  read-only (copy-to-library to customize).
- Live validation surfacing `store/modules.py` errors inline (bad group
  ref, expression parse error with the offending construct named).
- Expression editing with immediate feedback — evaluate against a sample
  sheet.

## Changes vs the 2026-07-05 roadmap sketch

- The editor is structured around **groups + sheet types** (the composition
  model), not a single sheet schema: group editor, sheet-type editor with a
  group-membership picker, per-kind organization.
- Interim authoring path already exists: the `create-mechanics-module` skill
  (Phase 1). This phase supersedes it for in-app use; the skill remains for
  conversational authoring.

## Open questions (settle at pick-up)

- Editing a group used by many sheet types: impact preview (which types /
  existing sheets are affected)?
- Schema migration for existing campaign sheets when a user edits a module
  mid-campaign (renamed field keys, removed groups) — versioning vs flag-
  invalid (Phase 1 chose flag-invalid for module *switches*; edits are
  gentler and may deserve rename support).
- Module export/import (zip?) for sharing.
- Layout editing: raw JSON with preview, or a visual arranger.
