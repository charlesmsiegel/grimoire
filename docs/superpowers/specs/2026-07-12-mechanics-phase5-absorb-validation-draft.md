# Mechanics Phase 5 — narrated-event validation (draft)

**Status: draft.** Scope sketch updated for the multi-sheet-type contract in
`2026-07-12-mechanics-phase1-modules-design.md`; gets its own brainstorm →
spec → plan cycle when picked up. Issue #826. Depends on Phase 4.

## Scope

When the campaign resolves to a module, the end-scene absorb pass also
checks the transcript against sheets and the roll log:

- flag narration contradicting mechanics — claimed outcomes with no roll-log
  entry, spent resources that weren't tracked, damage that never landed on a
  sheet;
- propose **sheet deltas** (damage, resource spend, XP, new list entries)
  through the existing staged-review flow (`absorb.py` → `changes.py` →
  StagedEdit review), mirroring how `group_state_edits` write-back works;
- deltas target only mutable field types (`resource`, `track`, `list`) —
  static stats (`number`, `dots`) change through the editor, not absorb.

## Changes vs the 2026-07-05 roadmap sketch

- Deltas are validated against the entity's **own sheet type** (field must
  exist in that type's assembled field set), not one module-wide schema.
- Any sheeted entity can receive deltas — a treasure losing a charge, a
  location's ward weakening — not just actor sheets.
- The mutable/static split is now structural (field types), giving absorb a
  crisp rule for what it may propose.

## Open questions (settle at pick-up)

- Prompt design: does the absorb model see full sheets or only mutable
  fields + roll log?
- Delta representation in StagedEdit (per-field old→new like group state?);
  conflict when two scenes in one batch touch the same field.
- Whether XP/advancement is a plain `number` absorb may touch (exception to
  the mutable-only rule) or module-defined.
- Severity split between "contradiction warning" (informational) and
  "proposed delta" (actionable).
