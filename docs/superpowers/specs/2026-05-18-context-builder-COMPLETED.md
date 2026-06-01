# Context Builder — Remaining Work (Completed)

> Everything from the original `specs/02-context-builder.md` that did not land in the original shipped builder (`2026-05-12-context-builder-design.md`) is now implemented. This document is the record of what was added in the 2026-05-18 pass.

**Companion (shipped):** `2026-05-12-context-builder-design.md`
**Module:** `backend/src/grimoire/context/`
**Tests:** `backend/tests/context/test_builder.py`

## What landed

### §1 + §2 — Richer tier promotion via `CharactersService.recommend_tiers`

`_resolve_cast` now consults `characters.recommend_tiers(scene, campaign_id, recent_posts=..., commitments_targeting_pcs=...)` for tier assignments and falls back to the legacy body-token scan when the collaborator does not expose the method. Tier pins (§2) live on the Characters service and are already honoured inside `recommend_tiers`, so the builder gets pin enforcement for free. Open commitments are fetched once per turn and reused for both the lock-in commitments block and the `commitments_targeting_pcs` hint.

The new `_pc_refs` / `_commitments_targeting_pcs` helpers translate "open commitments authored by an NPC targeting a PC" into the set the Characters service expects.

### §3 — Faction state in background tier

`_resolve_factions` enumerates factions declared in the active composition's worlds (via `world.list_factions`) and pulls each through `world.faction_state`. Each rendered as one compact line capturing current focus, public perception, goals, and top resources. Cap controlled by `ContextBuilderConfig.faction_state_limit` (default 4).

### §4 — Calendar / world-time

`_resolve_calendar` plumbs an optional `time_engine` collaborator into the builder. Emits one background "Calendar" item with the current in-game time (from `time_engine.current`), season (via `world.season_for`), holiday (via `world.holiday_at`), and the next few scheduled events (via `time_engine.upcoming_events`). Falls back to the scene's `in_game_start` when the time engine is unavailable.

### §5 — Recent facts in compact form

`_continuity_background` now uses `facts_about(limit=recent_facts_limit)` (default 50) and renders the facts as a single compact "Recent facts" block with one-line entries, capped at `recent_facts_char_cap` characters (default 4 000) to keep background tight.

### §6 — Relationship deltas since last scene

`_relationship_deltas` uses `characters.get_relationship_history(active_pc_ref, other, campaign_id, branch_id=...)` for each present cast member and renders the most recent event as `- pc ↔ other: trust +2 — orchard promise`. Emitted as a single background block.

### §7 — Explicit past-scene references in retrieval

`_scene_refs_from_input` scans the player input for `scene:<id>` tokens, looks up each scene through `scenes.get_scene`, and emits one archive item per match (capped by `scene_ref_limit`, default 5). Priority 20 means they outrank vector/keyword hits.

### §8 — Cross-asset duplicate-name handling

`_with_cast_header` prepends a `[world:<world_id>]` line on every library-sourced cast card so two `library:worlds/A/characters/margaret` and `library:worlds/B/characters/margaret` render distinctly. Campaign-local refs are unchanged.

### §9 — Voice anchor surfacing

When `ContextBuilderConfig.enable_voice_anchor` is True (default), each spotlighted character also emits a separate spotlight item under a `# Voice anchor — <ref>` heading, sourced from `characters.get_voice_only(ref, campaign_id)`. Distinct from the full card.

### §10 — Recent direct dialogue per spotlighted speaker

`_recent_dialogue_for` filters the scene's `recent_posts` by `author_pc_ref` / `author_npc_ref` for each spotlighted character and emits the last `recent_dialogue_per_speaker` (default 3) lines as a spotlight item.

### §11 — Regenerate cache

> **Removed (#512).** The assembled-prompt cache (`context/cache.py`,
> `ContextBuilderCache`, `make_cache_key`, the `_composition_hash` helper, and
> the `context_cache` constructor argument) was deleted in the reroll
> consolidation — it existed solely for the now-removed `regenerate_last`
> path. The original description is kept below for historical context.

New module `backend/src/grimoire/context/cache.py` provides:

- `make_cache_key(campaign_id, player_input, composition_hash, scene_id, branch_id, pc_ref)` — deterministic SHA-256 over the regenerate-stable inputs.
- `ContextBuilderCache` — bounded in-memory `key -> AssembledPrompt` store with FIFO eviction.

`OrchestratorService` constructs a `ContextBuilderCache` and a new `_composition_hash(campaign_id)` helper. The cache is populated on every successful `_run_turn` and consulted on `regenerate_last` (which passes `reuse_prompt_cache=True`). The cache lives at the orchestrator boundary, not in the Context Builder, as the spec required.

### §13 — Library asset retrieval weighting

`_priority_hints(composition)` builds a `{world_id: priority}` dict from the resolved composition and passes it to `state_store.vector_search` / `keyword_search` as the `priority_hints` keyword. `_invoke_store_search` retries without the kwarg when the store rejects it, so the builder degrades gracefully against older stores. Toggleable via `RetrievalConfig.enable_priority_weighting`.

## Deferred / rejected (unchanged)

- **§12 Cost-aware tiering** — explicitly deferred to v2.
- **§14 Multi-shot voice examples** — deferred to v2 unless drift correctives prove insufficient.
- **§15 SillyTavern mining** — rejected as a discrete task. The concrete behaviours (keyword-triggered lore) already shipped.

## Configuration additions (`ContextBuilderConfig`)

```python
recent_facts_limit: int = 50            # §5
recent_facts_char_cap: int = 4_000      # §5
recent_dialogue_per_speaker: int = 3    # §10
enable_voice_anchor: bool = True        # §9
faction_state_limit: int = 4            # §3
promotion_cooldown_turns: int = 3       # §1 (reserved for store-side cooldown)
scene_ref_limit: int = 5                # §7
RetrievalConfig.enable_priority_weighting: bool = True  # §13
```

## Constructor additions

- `ContextBuilderService(..., time_engine=None)` — optional `TimeEngine` collaborator. Required for §4 calendar items; omitted in unit tests that don't need calendar.
- `OrchestratorService(..., library=None)` — `library` enables composition fingerprinting. (The `context_cache` argument and its `ContextBuilderCache` were removed in #512; see §11.)

## Test coverage

`backend/tests/context/test_builder.py` adds dedicated tests for: `recommend_tiers` integration with promotion to background and forced spotlight (pins), commitments-targeting-PCs threading, voice-anchor on/off, recent dialogue filtering, world-prefix cast headers, compact recent-facts rendering, relationship-deltas block, explicit scene-ref injection, priority-hint forwarding (and graceful degradation against older stores), faction-state rendering, calendar item via TimeEngine, and the cache module's round-trip + eviction.

All 1 299 tests + 8 skips pass; `ruff check` and `ruff format --check` both clean.
