# Mechanics Phase 7 — content browsers + creation wizard (draft)

**Status: draft.** Scope sketch updated for the multi-sheet-type contract in
`2026-07-12-mechanics-phase1-modules-design.md`; gets its own brainstorm →
spec → plan cycle when picked up. Issue #827. Depends on Phases 0 + 3.

## Scope

- **Content browsers**: browse the resolved module's `content/<kind>/`
  merged with world entities of the same kind; module entries are read-only
  templates that can be *instantiated* into the world/campaign (copy, then
  it's yours). Statted content entries carry their `sheet_type` + `fields`
  into the instantiated sheet.
- **Attach content to sheets**: known spells, inventory, disciplines — list
  fields (or a richer ref field, to settle) pointing at content/world
  entries.
- **Creation wizard**: module creation rules drive point buys / dot budgets,
  with budgets as expressions over the sheet (reusing
  `store/expressions.py`). Per **sheet type** — creating a Warden offers
  warden budgets and warden-legal picks.

## Changes vs the 2026-07-05 roadmap sketch

- The wizard is per sheet type, not per module; creation rules live with
  the sheet type (schema addition to `sheets.json`, or a `creation` block —
  to settle).
- Statted module content (already validated by Phase 1) becomes the
  template pool for instantiation.

## Open questions (settle at pick-up)

- Creation-rules format: budgets, group minima/maxima, freebie-point style
  conversions — how much is expressible declaratively vs left to rules text
  and the LLM.
- Ref-valued list fields (link a sheet's "known spells" to entities) — this
  intersects deferred issue #221 (ref-valued typed fields).
- Whether module content can be *world-overridden* (same id shadows module
  entry) like the overlay pattern, or instantiation-only.
- Advancement (XP spends) — here or a later follow-up.
