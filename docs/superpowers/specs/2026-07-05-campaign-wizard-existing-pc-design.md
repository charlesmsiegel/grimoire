# Campaign wizard: play an existing world PC

## Problem

The wizard's Character step (step 2) always creates a brand-new campaign PC,
even when the chosen world already has PCs. `create_campaign` copies every
world PC into the new campaign, so the copy is already there — the wizard just
never offers it.

## Design (approved 2026-07-05)

Frontend-only change to `CampaignWizard.tsx`.

- When the world selected in step 1 has PCs (`api.listPCs({kind: "world", id})`),
  step 2 shows a **"Play an existing character"** section above the current
  new-character form: one selectable card per PC (name, pronouns, summary,
  tag chips), sourced from each PC's default persona summary fields available
  on `PCSummary` (name + tags; pronouns/summary come from `versions` if cheap,
  else name + tags only).
- Selecting a card highlights it and collapses the new-character form behind an
  "— or create someone new —" divider; clicking the selected card again (or the
  divider) deselects and restores the form.
- **Next** enables when a PC is picked *or* the new-persona name is filled.
- On commit with a picked PC: skip `createCampaignPC`; seat the pick in the
  opening scene with `addToCast(cid, sid, {kind: "pcs", id})` — the version is
  resolved server-side from the campaign's copied default.
- Changing the world in step 1 clears the pick and reloads the PC list.
- Worlds with no PCs render step 2 exactly as today.

## Not doing

- No backend changes; no editing of the picked PC in the wizard (edit via the
  world's PC page).
- No multi-PC seating at creation (the cast panel already handles that later).

## Tests

`CampaignWizard.test.tsx`: world with PCs shows the section; picking one
enables Next, commit seats it via `addToCast` and never calls
`createCampaignPC`; deselecting restores the form path; world without PCs
shows no section.
