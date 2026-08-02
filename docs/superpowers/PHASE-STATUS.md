# Scene Lifecycle & Continuity — phase status / handoff

Umbrella design: `specs/2026-06-30-scene-lifecycle-continuity-design.md`.
All merged work is on `main`. Suites: **backend 486, frontend 164, tsc clean.**

**All six umbrella phases are complete.**

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
- **Phase 6 — Campaign record changes.** `store/changes.py` (rolling `changes.json`,
  keyed `"<kind>/<id>"` → the latest write-back delta `{scene, fields:[{field,label,
  before,after}]}`) plus `line_diff` (stdlib `difflib` → tagged `{op,text}` lines).
  `absorb.apply_edits(cid, edits, sid=None)` captures the before/after of each **applied
  browsable** edit (`character_state`/`authored` → `characters/{id}`, `lore` →
  `{lore|locations}/{id}`; relationship/bond/plot never recorded; `sid=None` skips).
  `GET /campaigns/{cid}/changes` resolves record name + scene label and returns a
  per-field server-side line diff (tolerant; deleted records dropped). A read-only
  **ChangesPanel** (records grouped Characters/Lore/Locations, each field a highlighted
  add/remove line diff) revealed by a **Changes** toggle in `CampaignView`. The
  "previous version → current version" delta (last write-back), not a base-world compare.
  Spec `specs/2026-07-01-campaign-record-changes-design.md`,
  plan `plans/2026-07-01-campaign-record-changes.md`.

## The established pipeline (every continuity axis follows this)

extraction (`absorb.build_prompt` fed deterministic snapshots + `parse_output`) →
`absorb.materialize` (JSON → `StagedEdit`s with before/after, optional `payload`) →
`POST /absorb` returns `edits` (writes nothing) → review checklist in `CampaignView` →
`PUT /chronicle` `absorb.apply_edits` (best-effort per edit) → `context._assemble`
injects a labeled, always-on, **tolerant** (omit-never-crash) section.

`StagedEdit` shape (backend↔TS, fixed): `{id, kind, target:{kind,id}, label, field,
before, after, authored, payload?}`.

## Next: none — umbrella complete

Phase 6 shipped as **campaign record changes** (a last-write-back "previous → current"
diff per record), not the originally-sketched campaign-vs-base-**world** compare: the
fork-point content is not recoverable (`sync.md` stores only hashes) and, per the user, the
useful view is how the campaign's own copy evolved through play, not how it differs from the
world. See the deferrals below for follow-on ideas.

## Working cadence (used for Phases 1–5)

spec (`specs/…-design.md`) → plan (`plans/…md`) → **inline TDD** per task (red→green→commit;
subagents can't Edit/Write here, so implement inline) → one **whole-branch review on the
strongest model** as the backstop → apply findings → **rebase-merge to main** (ff-only,
per user preference; no merge commits) → delete branch. Feature work on a branch (not a
worktree — the editable `backend/.venv` is pinned to this checkout). Progress ledger at
`.superpowers/sdd/progress.md` (git-ignored scratch).

## Known deferrals / small debts

- PC-persona evolution (not modeled). `voice_drift` landed with #59: world-level
  anchors (`store/voice_anchors.py`), a per-anchored-NPC judge at absorb, and a
  post-history corrective (`store/voice_drift.py`,
  `templates/scene/voice_correction.j2`).
- Bond `since_scene` is stored but never populated (schema-ready; wire when useful).
- Phase-1 Minor: re-absorbing a scene re-appends `timeline.md` lines (no timeline reader
  yet; fix when one lands).
- Relationship metrics are approve/reject only in review (no inline number editing yet).
- Phase-4 knowledge is edited as one prose blob in the existing `character_state` textarea
  (no per-field/structured editing). Residual parse edge: a `current_state` whose *first*
  non-empty line is literally a recognized header (`## Knows` etc.) would be misread as
  structured — vanishingly unlikely for standing-condition prose; not guarded.
- Phase-6 changes shows only the **last** write-back delta per record (rolling). Not modeled:
  a cumulative fork-point diff, full per-scene history/timeline stepping, or a campaign-vs-
  current-world compare (all deliberately out of scope). ChangesPanel is read-only.

## Commands

- Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q`
- Frontend (from `frontend/`): `npx vitest run` and `npx tsc -b`
