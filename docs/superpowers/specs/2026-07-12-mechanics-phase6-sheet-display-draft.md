# Mechanics Phase 6 — sheet display (draft)

**Status: draft.** Scope sketch updated for the multi-sheet-type contract in
`2026-07-12-mechanics-phase1-modules-design.md`; gets its own brainstorm →
spec → plan cycle when picked up. Issue #828. Depends on Phase 3.

## Scope

- Widget library: dot track, checkbox track, resource bar, stat grid,
  derived badge, list — one widget per field type plus layout containers.
- `layout.json` in the module pack: **per sheet type** (the sketch had one
  module-wide layout), arranging that type's groups/fields into
  rows/columns/panels; shared groups can share a layout fragment so
  attributes render identically on every sheet type that includes them.
- Per-module `theme.css`, scoped to the sheet container (fonts, colors,
  borders) — a d20 sheet looks like a d20 sheet, a pool sheet looks gothic.
- Replaces the Phase-3 generic viewer wherever a layout exists; generic
  rendering stays as the fallback for types without one.

## Changes vs the 2026-07-05 roadmap sketch

- `layout.json` keyed by sheet type, with reusable group-layout fragments.
- Layouts must cover non-character kinds — an item card and a location
  dossier are smaller/different shapes than a character sheet.

## Open questions (settle at pick-up)

- `layout.json` schema: grid-based? nested rows/columns? how much CSS power
  does `theme.css` get before it can break the app shell (sanitize/scope
  strategy)?
- Print/export view?
- Whether the Phase-4 in-context sheet *summary* format also becomes
  module-customizable here.
- Android/WebView rendering constraints on custom CSS.
