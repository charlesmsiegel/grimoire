# Scene Lifecycle & Continuity — phase status / handoff

Umbrella design: `specs/2026-06-30-scene-lifecycle-continuity-design.md`.
All merged work is on `main`. Suites: **backend 426, frontend 157, tsc clean.**

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

## The established pipeline (every continuity axis follows this)

extraction (`absorb.build_prompt` fed deterministic snapshots + `parse_output`) →
`absorb.materialize` (JSON → `StagedEdit`s with before/after, optional `payload`) →
`POST /absorb` returns `edits` (writes nothing) → review checklist in `CampaignView` →
`PUT /chronicle` `absorb.apply_edits` (best-effort per edit) → `context._assemble`
injects a labeled, always-on, **tolerant** (omit-never-crash) section.

`StagedEdit` shape (backend↔TS, fixed): `{id, kind, target:{kind,id}, label, field,
before, after, authored, payload?}`.

## Next: Phase 4 — Knowledge (who-knows-what)

Umbrella bundled knowledge with relationships; it was split out. Build it as the next
continuity axis on the pipeline above. **Open design decision to resolve in brainstorm:**
how to store knowledge — the leading options are (a) extend `state.md`/`playstate` to
carry structured `knows`/`suspects` prose fields alongside `current_state` (state.md
currently holds only `current_state` as its body, so this needs a small structure
change), (b) a separate per-character/campaign knowledge store, or (c) fold knowledge
into the `current_state` prose (simplest, least explicit). Snapshot semantics (fed
current, rewritten, fed into the prompt) should match Phase 2/3. Then: extraction field(s),
materialize (likely new kind(s) or extra character_state rows), apply, and a
`# Knowledge` (or merged into `# Character state`) injection, plus review rows.

## Then (umbrella phases 4–5)

- **Plot threads + suggested next scenes** — `plot.json` (open/advanced/closed threads);
  an ephemeral one-call scene-suggestion helper at scene creation (reads open threads +
  long-absent cast + upcoming calendar).
- **Campaign-vs-base world view** — browse the campaign's copies of world records with
  divergence from the base highlighted (via `sync.md` hashes).

## Working cadence (used for Phases 1–3)

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

## Commands

- Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q`
- Frontend (from `frontend/`): `npx vitest run` and `npx tsc -b`
