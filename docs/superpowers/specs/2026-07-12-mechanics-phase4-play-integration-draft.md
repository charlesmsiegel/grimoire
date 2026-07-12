# Mechanics Phase 4 — play integration (draft)

**Status: draft.** Scope sketch updated for the multi-sheet-type contract in
`2026-07-12-mechanics-phase1-modules-design.md`; gets its own brainstorm →
spec → plan cycle when picked up. Issue #825. Depends on Phases 2 + 3.
This is the payoff phase: after it, a campaign plays with LLM-refereed,
engine-resolved, truly random checks.

## Scope

- **Context sections** (`templates/scene/system.j2` + `context.py::_SECTIONS`,
  all empty when the resolved module is `None`):
  - rules digest — `always: true` docs, plus `keys:`-activated docs (reusing
    the lorebook scan), plus `sheet_types:`-activated docs for sheet types
    present among in-scene actors;
  - sheet summaries for present actors — compact, derived values
    precomputed, labeled by sheet type ("Seraphine — Warden");
  - response-format section teaching the roll tag and listing the checks
    *available to present actors* (a check is offered when an actor's sheet
    type includes all `requires` groups).
- **Two-phase generation**: the model emits a fenced ```` ```roll {...} ````
  block naming a check id and stops; backend detects the fence mid-stream,
  cuts generation, surfaces a proposal chip (**accept / modify / decline**).
  Accept ⇒ engine resolves (sheet fields → expressions → seeded roll →
  outcome tier) and a continuation call narrates, with `on_roll: true` docs
  plus the check's `rules:` docs injected. Decline ⇒ continuation proceeds
  without a check.
- Settle the `outcomes` tier schema left opaque in Phase 1, and the exact
  roll-tag JSON shape (check id, acting entity, optional
  difficulty/DC/modifier supplied by the LLM).

## Changes vs the 2026-07-05 roadmap sketch

- Rules activation now has four triggers (`keys`, `always`, `on_roll`,
  `sheet_types`) plus check-linked `rules:` — the sketch only had
  keyed/owned lore-style activation.
- Check availability is computed per actor from sheet-type group membership,
  not module-global.
- Sheet summaries cover any sheeted in-scene entity (a statted location or
  treasure can appear), not only actors — exact inclusion rules to settle.

## Open questions (settle at pick-up)

- Roll-tag grammar details and how tolerant parsing is of malformed fences
  from RP-tuned models.
- The `outcomes` tier schema (labels, expression-based conditions?).
- **modify** UX on the proposal chip: swap check? adjust difficulty? edit
  the resolved pool before rolling?
- Whether the player can invoke a manual check from the play view against a
  sheeted actor (extending the Phase-2 manual Roll affordance with check
  lookup).
- Context budget: cap on activated rules docs / sheet summaries per turn.
