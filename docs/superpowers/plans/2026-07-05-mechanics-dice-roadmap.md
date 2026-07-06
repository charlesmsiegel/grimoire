# Mechanics & Dice — roadmap plan

Companion plan to
`docs/superpowers/specs/2026-07-05-mechanics-dice-roadmap-design.md`, which
holds the architecture spine, the settled decisions, and the phase
definitions. This plan records how the roadmap turns into work.

## What this plan is

The Mechanics & Dice milestone (#822–829, plus #693 as Phase 0) is too large
for a single spec. The roadmap fixes the shared architecture so the phases
compose, then each phase is picked up independently with its own
brainstorm → spec → implementation-plan → implementation cycle.

## Phase order and dependencies

| Phase | Issue | Depends on |
|---|---|---|
| 0 — Item/Faction/Monster entity kinds | #693 | — |
| 1 — Module registry + binding + data contract | #822 | — |
| 2 — Dice engine | #824 | — |
| 3 — Character sheets | #823 | 1 |
| 4 — Play integration (roll tag, proposals) | #825 | 2, 3 |
| 5 — Narrated-event validation | #826 | 4 |
| 6 — Sheet widget library + themes | #828 | 3 |
| 7 — Content browsers + creation wizard | #827 | 0, 3 |
| 8 — Module authoring UI | #829 | 1–6 stable |

Phases 0, 1, and 2 are mutually independent and can be taken in any order or
in parallel; 0 is the smallest and a good warm-up.

## Standing constraints for every phase

- **Null fall-through everywhere:** a campaign with no `module:` key must
  behave exactly as today — no mechanics sections in context, no mechanics UI.
- **Reference modules are fixtures:** the 5e-flavored and
  Storyteller-flavored modules live in the test suite from Phase 1 onward;
  any engine change must keep both passing.
- **Store conventions:** markdown+frontmatter for prose, JSON for structured
  mutable state, generic CRUD in the style of `entities.py`; backend tests
  isolate the store via `GRIMOIRE_HOME`.
- **LLM calls in routes, prompts in templates, parsing in the store layer**
  — the absorb pattern (`absorb.py`) is the model for all new extraction or
  refereeing calls.

## Follow-ups (not started here)

- Rewrite the GitHub issue bodies for #822–829 with the phase scopes and
  dependency links from the spec, and comment on #693 linking it as Phase 0.
- When Phase 1 starts: brainstorm the module data contract (sheet.json,
  checks.json, expression grammar) — that spec is the keystone the rest of
  the milestone builds on.
