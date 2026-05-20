# TODO

Last updated 2026-05-20. Audit confirmed scene-hud Branch G frontend, narrative-extras pin chips, and the auxiliary-tasks SideHud in-flight indicator all shipped — only one net-new item remains.

---

## card-imports + lore-reclassification — section 4 follow-up

The one remaining piece is **lore-reclassification spec section 4** — wiring the per-row category dropdown into the card-imports `ImportDialog.tsx` so users can promote a `character_book` entry to a Character/Location/Faction/Item at import time instead of after the fact.

Verification (2026-05-20):
- ✅ Shared transform `apply_mapping` exists at `backend/src/grimoire/library/reclassify.py`.
- ✅ Heuristic `suggest_kind` exists at `backend/src/grimoire/library/classify.py`.
- ✅ Matching-metadata fields (`priority`, `probability`, `at_depth`, `scan_depth`, `case_sensitive`, `selective_logic`, etc.) are on `LoreEntry` in `types/world.py:162-171`.
- ❌ `lore_overrides` not yet accepted by the card-imports commit payload (`backend/src/grimoire/api/imports.py`).
- ❌ No per-row category dropdown in `frontend/src/routes/library/ImportDialog.tsx`.

Owed:

- [ ] **Backend:** extend the card-imports commit payload to accept `lore_overrides: [{source_index, kind, overrides}]`; in `_finalize_import`, when `kind != "lore"`, call `apply_mapping(lore, target_kind, overrides)` from `grimoire.library.reclassify` and write the target-kind entity via `LibraryService.create_entity` instead of as lore.
- [ ] **Frontend:** in `ImportDialog.tsx`, add a per-row category dropdown defaulting to `suggest_kind(entry)` (call `previewReclassify` or a new lightweight `suggest` endpoint), seeded above the configured `suggestion_threshold` (default 0.6 from `LibraryConfig.reclassification.suggestion_threshold`). For non-default targets, collect required overrides inline (e.g. `Location.kind`).
- [ ] Sanity-check the existing `_DROPPED_MATCHING_FIELDS` rollup warning in `reclassify.py` only fires when matching-metadata fields are set to non-defaults (sentinels already in `_DEFAULT_VALUES`).
