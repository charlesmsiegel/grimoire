# Context Builder — Remaining Work

> Everything from the original `specs/02-context-builder.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-context-builder-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-context-builder-design.md`
**Module:** `backend/src/grimoire/context/`

## 1. Tier promotion rules beyond "present + mentioned"

Spec 02 §Tier promotion logic defines a richer matrix than the shipped builder enforces. Today's cast resolution (`_resolve_cast` in `context/builder.py`) promotes characters that are in `scene.present_character_refs` to spotlight and characters whose ref appears as a `library:` / `campaign:` token in the last few post bodies to background. The rules left to implement:

**Promote to spotlight (additional):**
- Character is named in last 3 posts (vs the body-token scan currently used)
- Character has an active commitment with the active PC (cross-reference Continuity)

**Promote to background (additional):**
- Character is in the same household / location as scene characters (cross-reference World)
- Character was in the last 10 posts (today only looks at `recent_posts_n`, default 8)
- Character has any active plot thread with the PC (Continuity)
- Character was promoted in the last 3 sessions (cooldown to avoid churn — requires per-campaign promotion-history state)

**Demote rules (entirely missing):**
- Character has not appeared in N posts (configurable, default 20)
- Character's plot threads are all resolved or stale
- Character is from a different era / branch

The promotion-history cooldown and the "in same household" check imply new helpers on `CharactersService` / `WorldService`; sketch those signatures as part of the plan.

## 2. User tier pins

Spec 02: "User can pin a character to a tier (e.g., 'always keep Alistair at spotlight') to override automatic promotion." Nothing implements this today. Needs:

- Storage for `(campaign_id, character_ref) -> tier` pins (probably on `CharactersService` or a small new `state_store` table)
- A read in `_resolve_cast` that loads pins for the campaign and forces those characters into the pinned tier, bypassing automatic promotion/demotion
- A campaign-settings UI surface to set/unset pins (out of scope for the backend plan)

## 3. Faction state in background tier

Spec 02 §Background tier calls for "Faction state if politically relevant to current scene." `_resolve_world` already collects location, weather, adjacent locations, and running summary — faction state is the obvious next item. Needs a `world.faction_state_for_scene(scene, campaign_id, branch_id=...)` (or similar) that returns the compact faction summaries to render. Builder-side change is a single new entry in the background list.

## 4. Calendar / world-time context

Spec 02 §Background tier: "Calendar / world-time context (season, weather, ongoing events)." Weather is shipped; season and ongoing-events are not. Plumb `time_engine` (or `world`) into the builder and emit a small "Calendar" background item with the current in-game date, season, and any active world events.

## 5. Recent facts in compact form

Spec 02 §Continuity step 4 says "recent facts (last 50 facts in compact form)." `_continuity_background` currently calls `continuity.facts_about(limit=8)` and renders each fact verbatim with a `Fact:` prefix. To honour the spec: increase the limit to 50 and add a compaction render — likely a one-line-per-fact format with a budget cap so background does not balloon.

## 6. Relationship deltas since last scene

Spec 02 §Continuity step 4: "relationship deltas since last scene." No `relationship_delta` plumbing exists in the builder. Needs a `continuity.relationship_deltas_since(scene_id, ...)` (or equivalent) producing compact lines like "winifred ↑ trust (+2) after orchard promise" for the lock-in or spotlight tier.

## 7. Explicit past-scene references in retrieval

Spec 02 §Archive step 5: "explicit scene refs (if player input references past scenes)." `_retrieve_archive` runs vector + keyword only; no scan of `player_input` for scene references. Needs a small parser that recognises `scene:<id>` (or slug references like `"first meeting with NPC X"` via the scene tag index) and forces those scenes into archive without consuming retrieval budget.

## 8. Cross-asset duplicate-name handling

Spec 02 §Open questions: "If two referenced worlds both contain a character named 'Margaret,' both surface in resolved cast." Today characters are deduped by ref, so two `library:worlds/A/characters/margaret` and `library:worlds/B/characters/margaret` already render as two cards. The remaining design choice is the **display label** — surface the world-id prefix in the rendered card header so the model can tell them apart. Likely a one-line change in the cast renderer plus a test.

## 9. Voice-anchor depth and "dialogue don't" lists

Spec 02 §Voice anchors describes the spotlighted character card as carrying:
- Voice anchor (50–200 word canonical-voice prose sample)
- Explicit do/don't dialogue rules
- Recent emotional state + current desire

The builder treats `get_full_card` as opaque text and just inlines whatever the Characters module returns. Confirming with `08-characters.md`: if the full card already embeds anchors/rules then nothing to do here; if not, the Characters module is the right place to extend, but the Context Builder should grow a separate `voice_anchor_for(ref)` call that surfaces them under a clear `# Voice anchor` heading distinct from the card itself.

## 10. Recent direct dialogue per spotlighted speaker

Spec 02 §Drift mitigation: "Surfacing recent direct dialogue from each spotlighted character (last 3 posts where they spoke)." `_resolve_cast` collects voice correctives but does not pull recent in-character dialogue. Needs a `scenes.recent_dialogue_by(character_ref, n=3)` (or post-filter in the builder) and a small spotlight item per spotlighted speaker.

## 11. Cache surface

Spec 02 §Open questions, marked "probably yes": cache the assembled prompt on regenerate. The shipped builder already emits `AssembledPrompt.messages_hash` and `composition_snapshot`, so the inputs to a cache key exist. Remaining: a thin `ContextBuilderCache` (probably keyed by `(campaign_id, player_input_hash, composition_snapshot_hash, scene_id, branch_id)`) used by the orchestrator's `regenerate_last` path. The Context Builder itself should not own the cache — wire it at the orchestrator boundary so cache invalidation lives next to the regenerate logic.

## 12. Cost-aware tiering (v2; deferred)

Spec 02 §Open questions: "When using expensive models, automatically reduce spotlight depth. When using cheap models, expand it." Out of scope for v1. Record here so it does not get re-litigated.

## 13. Library asset retrieval weighting

Spec 02 §Open questions: "Reflect priority order in retrieval weights" so a campaign can mark world A as primary and world B as supplementary. `Composition.worlds[].priority` already exists (the builder sorts the worlds-in-play line by it). Needed:

- Pass per-asset priority to `store.vector_search` / `keyword_search` as a weighting hint
- Let the store re-rank hits accordingly (store-side work, not builder-side)

Trivial on the builder side once the store accepts the parameter.

## 14. Multi-shot voice examples (v2; deferred)

Spec 02 §Open questions: "For very voice-sensitive characters, would few-shot dialogue examples help? Experimentally, yes. Should be a per-character toggle." Defer to v2 unless evidence emerges that drift correctives alone are insufficient.

## 15. SillyTavern feature mining (rejected as a discrete task)

Spec 02 §Open questions lists "lorebook entry triggers, world info recursive scanning, character V2/V3 card structure, prompt manager presets." Keyword-triggered lore already ships (`_lore_triggers`). The remaining items are research notes, not concrete features — treat as **rejected** unless a specific behaviour gets brainstormed into its own item.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §1 + §2 together — finish the cast-resolution story (promotion rules + user pins); they touch the same code path and share new state needs.
2. §3 + §4 — fill out the background-tier world/time picture; small additive changes in `_resolve_world` plus one or two new collaborator calls.
3. §5 + §6 — round out Continuity contribution (compact recent facts + relationship deltas).
4. §7 + §10 — retrieval polish (explicit scene refs and per-speaker recent dialogue); both are small parsing/lookup additions.
5. §8 + §9 — display refinements for duplicate names and voice-anchor surfacing.
6. §11 — regenerate cache at the orchestrator boundary; needs §1–§5 stable to make cache keys meaningful.
7. §13 — retrieval weighting, gated on the State Store accepting per-asset priority hints.
