# Spine action icons — design

**Date:** 2026-07-02
**Revises:** `2026-07-02-guided-reroll-design.md` (action placement only)

## Goal

Move the transcript message actions (Reroll / Edit) out of the bottom-right
overlay and into the left spine column, as icon buttons stacked under the
vertical speaker label. Hovertext identifies them.

## Design

- Each `.msg`'s left column becomes `.spine-col` (flex column): the existing
  vertical `.spine` label on top, then the action icons beneath it:
  - **↻** — only on the last assistant post (same condition as today),
    `title="Reroll"`, opens the guidance popover.
  - **✎** — on every message, `title="Edit message"`, enters edit mode.
- Icons are revealed on message hover / focus-within, exactly like the old
  actions block. Colors: `--page-muted`, accent on hover (existing
  `.msg-edit` skin).
- The **guidance popover is unchanged in behavior** (input, `Reroll ▸`,
  Enter fires, empty = plain reroll, Esc closes) but re-anchors: absolutely
  positioned just right of the spine column, bottom-aligned with the icons
  (`.spine-col { position: relative }`, popover `left: 100%; bottom: 0`).
- The `.msg-actions` bottom-right overlay is deleted; `.msg`'s extra
  `padding-bottom: 6px` reverts to none.

## Testing (vitest, `CampaignView.test.tsx`)

- Existing reroll tests re-target `getByTitle("Reroll")` (or role+name via
  aria-label) and keep their popover-flow assertions.
- Edit test re-targets the ✎ button by its accessible name; assert
  `title="Edit message"` present.
- No backend changes.
