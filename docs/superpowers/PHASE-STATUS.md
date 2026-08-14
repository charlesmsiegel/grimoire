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
before, after, authored, payload?, review?}`.

`review?` is the confidence routing added by #110/#112 (`store/absorb/routing.py`):
`{certainty, quote, speaker, authority, score, band}`, present on the rows
`materialize` staged from the extraction and absent on the ones the later phases
(dossier, voice, sheet) stage, which rest on no transcript citation. It is
**display and default-checkbox state only** — `apply_edits` never reads it, and a
`low` row a reviewer ticks applies like any other. The review-everything
invariant above therefore still holds: routing only ever *withholds* a default
approval (a `low` row starts unchecked, behind a "show N low-confidence" toggle);
it never grants one.

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
- #110's Option A asked the panel to **sort** by confidence; it partitions instead
  (server order kept, `low` split off). Sorting numerically would scramble the
  kind grouping the labels rely on, and the row's index in `editRows` is what the
  #111 conflict verdicts are keyed on, so reordering is the one change with a
  correctness cost. Revisit if the banding alone proves too coarse to scan.
- #110's Option A also pre-checked only the HIGH rows; `medium` pre-checks too,
  so the only behaviour change is that `low` rows stop being pre-approved.
  Deliberate: withholding an approval is safe, granting one is not.
- #112's Option A put the evidence in the StagedEdit `payload`; it rides in
  `review` instead. `payload` is what `apply_edits` reads, and `new_character`
  payloads already carry an `evidence` key meaning something else entirely (the
  thin/sketched/established provenance sentence).
- Phase-1 Minor: re-absorbing a scene re-appends `timeline.md` lines (no timeline reader
  yet; fix when one lands). **Still open after #198.** The timeline *view* landed, but it
  derives its cards from `scenes` + `chronicle.json` + `plot.json` (`store/timeline.py`)
  and never opens `timeline.md` — those lines carry no delimiters and no scene ids, so
  reading them back needs a format decision and a migration first. Nothing depends on
  them, so the duplicate append is still cosmetic.
- Relationship metrics are approve/reject only in review (no inline number editing yet).
- Phase-4 knowledge is edited as one prose blob in the existing `character_state` textarea
  (no per-field/structured editing). Residual parse edge: a `current_state` whose *first*
  non-empty line is literally a recognized header (`## Knows` etc.) would be misread as
  structured — vanishingly unlikely for standing-condition prose; not guarded.
- Phase-6 changes still shows only the **last** write-back delta per record (rolling) on its
  **Records** tab. The history behind it is no longer missing — see the change journal below
  — but the two remaining gaps are unchanged: a cumulative fork-point diff and a campaign-vs-
  current-world compare are both deliberately out of scope.
- **Change journal + undo (#31).** `store/journal.py` is an append-only per-campaign history
  at `journal.json` (`{seq, entries}`; ids `j<n>`, never reused, newest `RETENTION` kept),
  and `store/undo.py` is what makes an entry reversible. `absorb.apply_edits` appends one row
  per applied edit of **every** kind — plot, commitment, fact and the relationship pair
  included, which `changes.json` never covered — carrying the reversal snapshotted just
  before the write. Manual coverage is the two campaign routes whose write is one text body:
  `PUT /campaigns/{cid}/{kind}/{eid}` and `PUT /campaigns/{cid}/groups/{gid}/state`.
  `GET /campaigns/{cid}/journal` + `POST /campaigns/{cid}/journal/{jid}/undo`, behind a
  **History** tab in `ChangesPanel`; undoing appends its own entry, so undoing that is redo.
  Undo is a compare-and-swap on what the write produced — a record that moved since is a 409,
  never a silent overwrite.
  - **Not reversible, by decision and with the reason in the entry**: `fact` (the ledger
    models supersession and must not lose a row), `weather` and `sheet` (each owns its own
    conflict contract), and the `new_*` creations (undo there is a cascading delete — #75).
    A `commitment` whose id the write reallocated is journalled without a reversal too.
  - **Not covered**: manual edits through `PUT /campaigns/{cid}/characters/{char}/versions/
    {vid}` (whole-card replace, so a field-precise row would describe only part of it) and
    the world-scope routes (the journal is per campaign).

## Commands

- Backend: `backend/.venv/Scripts/python.exe -m pytest backend -q`
- Frontend (from `frontend/`): `npx vitest run` and `npx tsc -b`
