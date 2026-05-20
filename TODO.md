# TODO

Last updated 2026-05-19. Updated after the first execution pass landed (a) a Windows path-format bug fix and (b) frontend wiring for orphaned components.

---

## ✅ Completed this pass

- **Windows path-format bug** — `relative_to_root` now uses forward slashes (commit `8c914f0`). 4 previously-failing tests now pass on Windows; no effect on Linux/CI.
- **expression-sprites** — `ExpressionPicker` wired into composer; emotion plumbed through `usePlayState.submit` and PATCHed on next `pc_post_appended`. Spec renamed to `-COMPLETED.md`, plan deleted.
- **transient-state** — Spec renamed to `-COMPLETED.md`, plan deleted (backend-only spec; no frontend was owed).
- **fork** — Lineage UI verified in `CampaignsView.tsx`. Spec renamed to `-COMPLETED.md`, plan deleted.
- **context-inspector** — Toggle in `play-top-bar` switches `SidePanel` ↔ `InspectorPanel`; draft text lifted in PlayView. Spec renamed to `-COMPLETED.md`, plan deleted.
- **extraction-modes** — Orchestrator now resolves the route, calls `select_mode(...)`, and threads `extractor_mode` to `context_builder.build` + `extractor.extract` at both canonical (`_continue_turn_after_pre_roll`) and regenerate (`_regenerate_post_core`) callsites; rewrite_post pins SEPARATE. Streaming tool_calls are still a gateway gap, gated via `_NullAutoDisable.tool_use_disabled=True`. Spec renamed to `-COMPLETED.md`, plan deleted.

---

## 1. Partially shipped — still owed before rename

### narrative-extras
Backend ✅. `ExtrasTable` wired into `EntityEditorView` ✅. **Pin chip rendering blocked on scene-hud Branch G** — narrative-extras spec writes `pinned_extras` to `hud.yaml` and expects the HUD to read it; the HUD frontend doesn't exist yet.
- [ ] Build scene-hud Branch G (see below) — that unblocks `PresentCastChip` which renders pinned chips
- [ ] Smoke test the full flow (add extras, pin one, see chip, promote to fact)
- [ ] Then rename + delete plan

### auxiliary-tasks
Backend ✅. All 7 task-kind UI surfaces shipped: **Brainstorm** (in SidePanel), **Rewrite**, **Continue as...**, **Translate...**, **What would they say...** (per-post in PostItem), **Suggest a post** (impersonate_pc) and **Polish** (edit_prose) in InputArea. Still missing:
- [ ] **SideHud in-flight indicator** — blocked on scene-hud Branch G
- [ ] Manual end-to-end smoke per `Integration check` (accept one of each TaskKind, verify state mutates only on accept)
- [ ] Then rename + delete plan

### scene-hud
Backend ✅. **Entire Branch G frontend missing** — no `SideHud/` directory exists. Blocks: narrative-extras pin chips, auxiliary-tasks in-flight indicator.
- [ ] Create `frontend/src/routes/campaign/SideHud/SideHud.tsx` (top-level layout)
- [ ] Create widgets: `RowWidget.tsx`, `BlockWidget.tsx`, `ChipListWidget.tsx`, `BannerWidget.tsx`, `CompositeWidget.tsx`
- [ ] Create `PresentCastChip.tsx`
- [ ] Create `useHud.ts` + `frontend/src/api/hud.ts`
- [ ] Modify `ScenePane.tsx` to add HUD column (or wire into PlayView's right pane as a third option)
- [ ] Per-widget render-hint tests
- [ ] Latency: `pytest backend/tests/perf/test_hud_aggregate.py` < 100ms p50
- [ ] Then rename + delete plan

---

## 2. Stale "remaining" plans

Reconciled this pass. Both plans verified branch-by-branch against shipped code; everything was already in the tree. Plans deleted.

- ✅ `plans/2026-05-17-imagegen-remaining.md` — all 6 branches shipped (migrations 018/019, full `imagegen/` module, 12+ REST routes, lifecycle hooks, persistent queue). Deleted.
- ✅ `plans/2026-05-17-world-remaining.md` — all 8 branches shipped (`WorldConfig`, lore filtering + FTS, weather extractor, atmosphere generation, spatial composition entity refs, greeting handoff, emergent location, location state). Deleted.

---

## 3. Net-new work

### lore-reclassification
Design exists; **no plan**; zero matching code.

- [ ] Write implementation plan at `docs/superpowers/plans/2026-05-19-lore-reclassification.md` covering the design's six areas (conversion service, field-mapping, heuristic classifier, import dialog integration, library UI, audit trail)
- [ ] Implementation
- [ ] Note hard dep: depends on card-imports Task E2 being extended for `lore_overrides`

### card-imports
Spec + plan exist; **zero matching code** (`grep -r card_import backend/src/grimoire` returns nothing).

- [ ] Branch A: macros
- [ ] Branch B: lore schema
- [ ] Branch C: lore algorithm
- [ ] Branch D: ingest pipeline
- [ ] Branch E: REST + frontend
  - [ ] In Task E2, add the `lore_overrides` extension required by lore-reclassification

---

## Suggested order

1. **scene-hud Branch G** is the highest-impact unblock — building it unblocks narrative-extras (chips) and auxiliary-tasks (in-flight indicator). Worth doing before the other partial items.
2. **auxiliary-tasks** residual UI (continue-as / what-would-x-say / edit-prose / translate) — 4 small surfaces; can be done in parallel with scene-hud once design is clear.
3. **Section 2** reconciliation — likely just deletions of stale plans after confirming the work shipped.
4. **Section 3**: card-imports first (has the dep), then lore-reclassification (write plan, then implement).
