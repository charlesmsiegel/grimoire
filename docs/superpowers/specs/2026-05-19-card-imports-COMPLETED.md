# Character Card Imports — COMPLETED

> Shipped: 2026-05-19. Branch: `2026-05-19-card-imports`. Plan: `docs/superpowers/plans/2026-05-19-card-imports.md` (now superseded). Design: `2026-05-19-card-imports-design.md` (now deleted).

## What landed

All five branches (A — macros; B — `LoreEntry` schema; C — `lore_for_post` rewrite + tier routing; D — ingest pipeline writes greetings + lore; E — REST + frontend) were squashed into the single development branch and shipped together.

- `grimoire.characters.macros.expand_macros` — closed-set macros (`{{char}}`, `{{user}}`, `{{random}}`, `{{pick}}`, `{{roll:NdM}}`, `{{newline}}`, `{{trim}}`, `{{// comment}}`). Deterministic via `SHA-256(card_asset_id::field::index)` seeding.
- Late-stage `{{user}}` substitution at the end of `ContextBuilderService._assemble` against the active PC's name (`"the player"` fallback). Helper `_resolve_runtime_macros` is a pure list-of-Message → list-of-Message pass.
- `LoreEntry` extended with `secondary_keys`, `selective_logic`, `constant`, `enabled`, `case_sensitive`, `match_whole_words`, `priority`, `probability`, `position`, `at_depth`, `scan_depth`, `comment`, `import_source`. New enums `LorePosition` and `SelectiveLogic`. New `ImportSource` model. All new fields default to safe values so existing lore frontmatter parses unchanged.
- `WorldService.lore_for_post` rewritten with primary-keyword + selective-logic + probability + scan_depth + priority sort. Signature kept as `(text, campaign_id, ...)` for backwards compat; gained an optional `turn_id` keyword for deterministic probability seeding.
- `ContextBuilderService._lore_triggers` now returns three tier-segregated lists. New module-level `_route_lore_to_tier` maps `position` to (spotlight / background / archive).
- `ingest_character_card_v2` runs the macro pass over every text field and parses `character_book.entries[]` and greetings into structured `IngestedLoreEntry` / `IngestedGreeting` shapes on the returned `IngestedCharacterCard`. The character body no longer contains `## Alternate greetings` / `## System prompt` / `## Post-history instructions` sections (greetings are first-class entities; the other two are surfaced in the import report).
- `CharactersService._finalize_import` extended to (1) atomically write the character, (2) materialize greetings (`<char>--default.md`, `<char>--alt-NN.md`), (3) materialize lore (`<char>--<entry_slug>.md`) with macro-expanded body, (4) suffix on collision up to `-99`, (5) write a per-import markdown report to `data/library/imports/<timestamp>-<char_slug>.md`.
- `strip_avatar_metadata` keeps `IHDR`/`IDAT`/`IEND`/`PLTE`/`tRNS` and the `chara` / `ccv3` tEXt chunks, drops everything else. Applied unconditionally when persisting the embedded avatar.
- REST routes under `/api/library/worlds/<wid>/imports/sillytavern/{preview,commit}` plus `/api/library/imports` and `/api/library/imports/<id>`. The preview path stashes the parsed ingest in an in-memory TTL cache (15 min); commit reads from the cache and runs `_finalize_import`.
- `frontend/src/routes/library/ImportDialog.tsx` + `frontend/src/api/imports.ts` give a file-picker → preview → toggles → commit flow, with details panels for greetings / lore / warnings / errors. Mounted via the "Import character card" button in `WorldDetailView`.

## Breaking changes / migration notes

- **`LoreEntry.position` defaults to `after_cast`** → existing hand-written lore now lands in the **Background** tier rather than always the Archive tier. To restore the old behavior for a specific entry, set `position: archive` in its frontmatter.
- **Character body no longer renders `## Alternate greetings`, `## System prompt`, `## Post-history instructions`**. Greetings are first-class library entities; the system prompt is recorded in the per-import report so the user can route it deliberately. Already-imported character markdown is unaffected (only fresh imports change shape).

## Adaptations from the design spec

The plan / design spec described the algorithm in pre-refactor language. The implementation adapts as follows:

- "setting_id" / "library/settings/" in the spec → `world_id` / `library/worlds/`. The codebase migrated from "setting" to "world" in migration `014_rename_setting_to_world.sql`; the plan was authored before that. No semantic change.
- `lore_for_post(setting_id, scene, …)` in the spec → `lore_for_post(text, campaign_id, *, turn_id=…, …)` in the implementation. The current `Scene` model has only `post_count`; posts are separate state-store records. Builders already assemble the haystack string before calling — preserving the existing signature meant zero caller churn while still enabling the new algorithm. `scan_depth` operates over haystack lines.
- `at_depth` lore is routed to BACKGROUND (with a `lore-depth-N` section label) rather than the spec's "LOCK_IN at depth N" because the existing builder pipeline has no positional injection point inside recent-posts.
- System prompt routing is "log to import report" (v1) rather than "write to campaign-scoped `system_addendum.yaml`". The campaign-addendum file is reserved for a future cross-spec hook.
- Character-scoped lore is **out of scope** for v1 — all imported lore lands at world scope. The dialog notes this.

## Out of scope (deferred)

Everything in the design spec's "Out of scope" section (vectorized lore, recursion / sticky / cooldown, group scoring, scan-scope flags, slash commands, connection profiles, multi-character formats, auto-pruning, bidirectional sync, SillyTavern export).

## Tests added

- `backend/tests/characters/test_macros.py` — 21 cases covering every macro type, determinism, warnings, edge cases.
- `backend/tests/context/test_runtime_macros.py` — `{{user}}` substitution, idempotence, no-PC fallback.
- `backend/tests/types/test_lore_entry.py` — backwards-compat defaults + extended-field round-trip.
- `backend/tests/world/test_lore_for_post.py` — enabled/constant, keyword matching, selective_logic, probability, scan_depth, priority sort.
- `backend/tests/context/test_lore_routing.py` — `_route_lore_to_tier` per position value.
- `backend/tests/characters/test_import_card_writes.py` — greeting + lore writes, macro pass, collision suffix, report file.
- `backend/tests/characters/test_avatar_metadata.py` — PNG chunk stripping.
- `backend/tests/api/test_imports_routes.py` — preview / commit / list-reports / get-report.

Full suite: 2234 passed, 16 skipped.
