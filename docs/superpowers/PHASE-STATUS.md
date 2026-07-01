# Scene Lifecycle & Continuity — phase status / handoff

Umbrella design: `specs/2026-06-30-scene-lifecycle-continuity-design.md`.
All merged work is on `main`. Suites: **backend 468, frontend 160, tsc clean.**

## Done (merged to main)

- **Phase 1 — Chronicle + recap spine.** `chronicle.py` (chronicle.json + timeline.md),
  End-scene extraction (summary/timeline), `# Story so far` injection, End-scene review
  panel. Spec/plan: `plans/2026-06-30-scene-chronicle-recap.md`.
- **Phase 2 — State write-back.** `absorb.py` (one extraction → `materialize` → `apply_edits`),
  `playstate.py` (per-character `state.md` `current_state` snapshot), `# Character state`
  injection, diff-review checklist (character_state/lore/authored StagedEdits).
  Spec `specs/2026-07-01-scene-state-writeback-design.md`, plan `plans/2026-07-01-scene-state-writeback.md`.
- **Phase 3 — Relationships.** `relationships.json` (directed feelings + canonical bonds),
  extraction `relationship_deltas`/`bond_changes`, `materialize` relationship/bond StagedEdits
  (structured `payload`, approve-only rows), `apply_edits`, `# Relationships` injection.
  Spec `specs/2026-07-01-scene-relationships-design.md`, plan `plans/2026-07-01-scene-relationships.md`.
- **Phase 4 — Knowledge (who-knows-what).** Per-NPC `knows`/`suspects` prose as optional
  `## `-headed sections on `state.md` (back-compatible: a headerless body is `current_state`;
  a body is only parsed as structured when its first non-empty line is a recognized header).
  Rides the existing `character_state` StagedEdit (one editable blob, no new kind/`payload`)
  and the `# Character state` injection. **Keep-on-omit**: an omitted `knows`/`suspects`
  preserves the stored value, an explicit `""` clears it — so a current_state-only absorb
  never erases accreted knowledge. NPC-only. Spec `specs/2026-07-01-scene-knowledge-design.md`,
  plan `plans/2026-07-01-scene-knowledge.md`.
- **Phase 5a — Plot threads.** `plot.json` (open/advanced/closed threads, each with dated
  `beats` + `last_scene`), extraction `plot_movements`, `materialize` a new `plot`
  StagedEdit (editable beat `after` + structured `{id,title,status,scene}` payload),
  `apply_edits` via `plot.set_movement`, and a `# Plot threads` injection (open/advanced
  only). New-thread ids `slugify(title)`; pid resolved-then-looked-up so colliding titles
  merge honestly; movements deduped; tolerant reads. `plot.render_open` is shared by the
  prompt snapshot and the context block. Spec `specs/2026-07-01-scene-plot-threads-design.md`,
  plan `plans/2026-07-01-scene-plot-threads.md`.
- **Phase 5b — Suggested next scenes.** Ephemeral read-forward helper at scene creation:
  `store/suggest.py` assembles a deterministic snapshot (open threads, "now" = latest
  chronicled scene's date + calendar holidays/upcoming/roster **birthdays**, long-absent
  cast, seedable ids = world characters + roster players + campaign locations), builds a
  one-shot prompt, and parses id-validated openings (tolerant of a bare-array reply).
  `POST /campaigns/{cid}/scene-suggestions` (campaign-level, key-gated, non-streaming,
  world-first name resolution). The empty-scene `CastPanel` gains a **Suggest scenes**
  button; picking a card auto-seeds cast + location via existing endpoints and prefills the
  opener prompt. Read-only (persists nothing). Spec
  `specs/2026-07-01-scene-suggestions-design.md`, plan `plans/2026-07-01-scene-suggestions.md`.

## The established pipeline (every continuity axis follows this)

extraction (`absorb.build_prompt` fed deterministic snapshots + `parse_output`) →
`absorb.materialize` (JSON → `StagedEdit`s with before/after, optional `payload`) →
`POST /absorb` returns `edits` (writes nothing) → review checklist in `CampaignView` →
`PUT /chronicle` `absorb.apply_edits` (best-effort per edit) → `context._assemble`
injects a labeled, always-on, **tolerant** (omit-never-crash) section.

`StagedEdit` shape (backend↔TS, fixed): `{id, kind, target:{kind,id}, label, field,
before, after, authored, payload?}`.

## Next: Phase 6 — Campaign-vs-base world view (final umbrella phase)

Browse the campaign's copies of world records (characters/lore/locations) with divergence
from the base world highlighted, via the `sync.md` hashes already maintained by `sync.py`
(the write-back phases mutate campaign copies; this surfaces *what* diverged). **Open design
decisions for the brainstorm:** read model (a per-record diff endpoint vs. a campaign-wide
divergence list), what "divergence" shows (hash-mismatch flag only, or a field/body diff),
and the UI surface (a new page vs. badges on the existing campaign record lists). This is
read-only reporting — no new write path.

## Working cadence (used for Phases 1–5)

spec (`specs/…-design.md`) → plan (`plans/…md`) → **inline TDD** per task (red→green→commit;
subagents can't Edit/Write here, so implement inline) → one **whole-branch review on the
strongest model** as the backstop → apply findings → **rebase-merge to main** (ff-only,
per user preference; no merge commits) → delete branch. Feature work on a branch (not a
worktree — the editable `backend/.venv` is pinned to this checkout). Progress ledger at
`.superpowers/sdd/progress.md` (git-ignored scratch).

## Known deferrals / small debts

- PC-persona evolution and `voice_drift` (not modeled).
- Bond `since_scene` is stored but never populated (schema-ready; wire when useful).
- Phase-1 Minor: re-absorbing a scene re-appends `timeline.md` lines (no timeline reader
  yet; fix when one lands).
- Relationship metrics are approve/reject only in review (no inline number editing yet).
- Phase-4 knowledge is edited as one prose blob in the existing `character_state` textarea
  (no per-field/structured editing). Residual parse edge: a `current_state` whose *first*
  non-empty line is literally a recognized header (`## Knows` etc.) would be misread as
  structured — vanishingly unlikely for standing-condition prose; not guarded.

## Commands

- Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q`
- Frontend (from `frontend/`): `npx vitest run` and `npx tsc -b`
