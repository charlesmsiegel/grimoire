# Characters — Remaining Work

> Everything from the original `specs/08-characters.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-characters-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-characters-design.md`
**Module:** `backend/src/grimoire/characters/`

## 1. Tier-recommendation rules beyond "present → spotlight"

`recommend_tiers` (`service.py:353`) currently honors only two of the four spec rules:

- **Shipped:** present in scene → spotlight; user `tier_pin` overrides everything.
- **Missing:** mentioned in recent posts → background (needs a post-scan over the current scene's recent posts and an alias/name match against the campaign's resolved characters).
- **Missing:** open commitments to PC → at least background (needs a Continuity hook; the commitments ledger lives in `continuity/` per spec 11).
- **Missing:** inactivity → demotion over time (needs `last_screen_time_turn` on `CharacterState` to be checked against the current turn id; the field is populated by `mark_screen_time` but never consulted).

Design needed: extend the signature to accept `recent_posts` (or fetch via the optional `post_fetcher`) and a `commitments` source (likely an injected `ContinuityService`-shaped protocol). Define the demotion curve in config (e.g. spotlight → background after N quiet turns, background → archive after M).

## 2. Voice-anchor sample rotation in compressed views

Spec 08 §Voice anchors and §Configuration: `voice_anchor.sample_dialogue_rotation: true` / `max_samples: 5`. The renderer `views.rotate_samples(voice, *, seed)` exists and is exported but **no caller invokes it** — `render_full` / `render_compressed` / `render_voice_only` use the raw `voice.samples` order.

Needs: thread a turn-count / scene-post-count seed into the renderers (probably via a `seed: int | None` arg on the four `get_*_card` service methods, defaulting to `None` = no rotation) and call `rotate_samples` inside `_render_voice` before the `[:max_samples]` slice.

## 3. Drift check sampling + cadence

Spec 08 §Drift detection + §Configuration: `drift.check_every_n_appearances: 5`. Today `check_drift` runs only when explicitly called; there's no automatic cadence.

Needs: a hook from the Orchestrator's post-turn fan-out (see the orchestrator-remaining doc §1 — `background_work.drift_check_sampling`) that calls `check_drift` for present characters with appearance-counter throttling. The counter probably belongs on `CharacterState` as `appearances_since_last_drift_check` and gets bumped by `mark_screen_time`.

## 4. Drift UI surfacing + auto-corrective injection

Spec 08 §Drift detection: "Surface a UI badge", "Inject corrective voice anchors in the next prompt featuring this character", "Optionally offer to regenerate the last response with stronger voice guidance".

Needs:
- An event emission (e.g. `drift_detected`) on the bus when `check_drift` returns a score ≥ threshold so the Frontend can render the badge. See orchestrator-remaining doc §10.
- A Context Builder integration: when a character is present and `drift_corrective_context(...)` returns a non-empty snippet, the Builder should inject it into the next prompt. Today `drift_corrective_context` exists but no caller invokes it.
- A regenerate hook from the Orchestrator that re-runs the last response with the corrective snippet bolted on (rerolls now go through `regenerate_post`; the snippet would need to be threaded through). Note: `regenerate_last` was removed in #512.

## 5. Compressed-view caching

Spec 08 §Compressed card views: "Cached; cache invalidates on any source change." Today `get_full_card` / `get_compressed_card` / `get_voice_only` / `get_capsule` re-resolve and re-render on every call. With drift snippets layered on top this becomes more expensive.

Needs: an in-process LRU keyed by `(ref, campaign_id, view)` plus an invalidation hook on `update`, `delete`, `upsert_override`, `update_state`, `pin_tier`. The Library already exposes mtime; reuse it for staleness checks.

## 6. CharactersConfig dataclass

Spec 08 §Configuration lists a config block with knobs. Today most knobs are constructor kwargs (`drift_threshold`, `drift_checker`, `ingest_llm`) and per-call options (`IngestOptions`). Missing as a config block:

- `drift.check_every_n_appearances`
- `drift.check_model` (probably implicit in the LLM-backed `DriftChecker`'s gateway task name; document)
- `voice_anchor.sample_dialogue_rotation` + `max_samples`
- `capsules.auto_generate` (see §10)
- `promotion.require_confirmation` (see §9)
- `cross_world_lookup.case_sensitive`
- `multi_pc.auto_advance_with_single_pc` / `require_advance_with_multiple_pcs` (the second is already enforced via `should_auto_respond`; the first is implicit in the same call)

Define a `CharactersConfig` dataclass parallel to `OrchestratorConfig`, accept it in `__init__`, and replace ad-hoc kwargs.

## 7. Cross-world lookup case-insensitivity

Spec 08 §Configuration: `cross_world_lookup.case_sensitive: false`. Today `cross_world_lookup` (`service.py:321`) passes the raw `character_id` to `library.variants_of(...)` — match behavior is whatever the Library does (currently exact-match by asset id). Needs: slug-normalize the id (lower-case + slugify) before lookup so `Alistair-Hyde-Smythe` finds `alistair-hyde-smythe`.

## 8. State Store delta-log integration for `_save_state`

The shipped `_save_state` (`service.py:992`) bypasses `apply_delta` deliberately ("we don't yet have a turn-level audit pipeline; … reversal stays possible"). The TODO is real: state writes from `update_state` / `mark_screen_time` / `pin_tier` / `set_current_scene_for_pc` / `check_drift` are not reversible by the Orchestrator's `undo_turn` because no delta row is recorded. The `_ = source` line at `service.py:1072` documents the intent.

Needs: route each `_save_state` call through `state_store.apply_delta(...)` (or a new dedicated path) with `entity_kind="character_state"` and a `turn_id` so reversal works. Audit which callers actually need reversal — `pin_tier` arguably should not be reversed by undo, while `mark_screen_time` definitely should.

## 9. Promotion confirmation flow

Spec 08 §Configuration + §Promotion: `promotion.require_confirmation: true`. Today `promote_to_library` is a single async call that writes immediately. The spec implies a two-step UI flow: "propose mapping" → "user confirms" → "commit".

Needs: a `propose_promotion(campaign_id, character_id, target_world_id) -> PromotionProposal` method returning the rendered frontmatter, the target library path, and any validation warnings (id collision, missing voice anchor); then `promote_to_library(...)` becomes the commit step. The single-shot path can stay for tests / programmatic use behind a `confirm: bool = False` flag.

Spec also mentions "Migrate any campaign-local sheet to library-level sheet if applicable" — this requires a hop into Mechanics and is not yet wired.

## 10. Auto-capsule generation for emergent characters

Spec 08 §Configuration: `capsules.auto_generate: true`. Today the `render_capsule` view exists but capsules are computed on demand from the character's current data. For emergent NPCs that don't yet have a fleshed-out card, the capsule is essentially empty.

Needs: when `create_emergent` is called with a sparse `CharacterData` (no description, no tags), kick off a background LLM call to draft a one-line capsule + tag list and write the result back. Pairs with §11.

## 11. Auto-draft voice anchors for emergent characters

Spec 08 §Open questions: "When a new NPC appears, auto-draft an anchor from their first scene? Yes, with user review." Treat as **planned**, not deferred.

Needs: a `draft_voice_anchor(character_ref, campaign_id, sample_window=10) -> VoiceAnchor` method that pulls the character's recent dialogue + scene context and asks an LLM to compose a summary + samples + register + dos/don'ts. The result lands as a proposal the user can accept (writes through `update_emergent` or `upsert_override`) or discard.

## 12. Relationship `history[]` log

Spec 08 §Relationships: each `Relationship` has `history: list[RelationshipEvent]`. The `RelationshipEvent` type is defined (`types/characters.py:161`) but never written. `update_relationship` only persists the rolling `state` — there's no record of which post/turn drove which delta.

Needs: extend the `relationships` table schema with a `history JSON` column (or a sibling `relationship_events` table keyed by relationship id), and have `update_relationship` append a `RelationshipEvent(in_post=..., summary=..., delta=...)` when callers supply enough context. Drives the "relationship timeline" UI panel.

## 13. Promote-with-sheet-migration

Spec 08 §Promotion: "Migrate any campaign-local sheet to library-level sheet if applicable." Today `promote_to_library` only moves the markdown; if the emergent character has a Mechanics sheet stored campaign-locally, that sheet is left behind and the promoted library character has no mechanics.

Needs: a `mechanics.migrate_sheet(campaign_id, character_ref, target_library_id)` hook that's called from `promote_to_library` after the markdown write. Coordinate with the Mechanics module owner — that API does not exist yet.

## 14. PCEntry `active` semantics + multiplayer owner — RESOLVED

**Resolution:** `list_pcs` and `active_pc` both delegate to `_seed_active_pc_from_rows`, which hydrates the in-process `_active_pc` cache from the DB rows (picking the first row with `active=1`, or the earliest-added row if none carries the bit) and returns a single ref. PCEntry rows then derive `active` from equality against that one ref, so at most one PC is ever reported active per campaign — even on a cold worker with legacy data where multiple rows persist `active=1`. The store-side `add_pc` defaults new rows to `active=0` whenever the campaign already has a PC, and `set_active_pc` flips the bit atomically inside a transaction. Regression coverage in `backend/tests/characters/test_service.py` pins the cold-cache, multi-active, and all-inactive paths. The `owner` half of the spec section was already shipped — `add_pc` accepts and persists it.

## 15. Search ranking + advanced filters — RESOLVED (YAGNI)

**Resolution:** Confirmed YAGNI. The shipped substring-match `search(...)` is sufficient for the API contract today; ranking, role/world/tag filters, and fuzzy-match all add real complexity (BM25 or trigram-style indexing, query DSL, typo budget) but no consumer currently asks for any of them. Revisit when a frontend search affordance explicitly needs one of these — at that point the relevant requirement will pin down which gap to close and how.

## 16. Cross-world rename (v2; deferred)

Spec 08 §Open questions: "Renaming `alistair-hyde-smythe` to `hyde-smythe` in one world breaks the variant link. A `rename` operation that updates references is a v2 idea." Treat as **(v2; deferred)** — do not plan against this now.

## 17. Sheet versioning (v2; deferred)

Spec 08 §Open questions: "Mechanical sheets change (XP spent) — are old versions kept? Yes via delta log; snapshot-per-session is nice-to-have." This is a Mechanics concern, not Characters. Treat as **(v2; deferred)**.

## 18. Relationship visualization (v2; deferred)

Spec 08 §Open questions: "Relationship graph, variant lineage. UI consideration; data model supports it." Frontend-only; the data model is already in place. Treat as **(v2; deferred)**.

## 19. PC scene-placement default (v2; deferred)

Spec 08 §Open questions: "When a PC has no current scene … what's the default? Probably 'show the campaign overview'; UI decision." Frontend concern. Treat as **(v2; deferred)**.

## 20. Canonical body headings (rejected)

Spec 08 §Open questions: "The markdown body is unstructured; canonical headings (Appearance, Personality, Background) are encouraged via templates but not enforced." The ingestor already composes `## Description` / `## Personality` / `## Scenario` etc. when parsing SillyTavern cards (`ingest._compose_body`), which is good enough. Treat as **(rejected)** — do not add a body-headings validator without re-brainstorming.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §1 — tier-recommendation rules (self-contained, needed by Context Builder; pairs with §5 for caching).
2. §8 — wire `_save_state` through the delta log (foundational for §3, §4, §11, §12; unblocks Orchestrator `undo_turn` over character state).
3. §3 + §4 — drift cadence + UI surfacing + corrective injection (these go together; the orchestrator-remaining doc §1 has to land too).
4. §2 — sample rotation in views (small; ships with §5 if you tackle caching anyway).
5. §5 — compressed-view caching (do after §1+§2+§4 settle the data the cache key has to depend on).
6. §6 — `CharactersConfig` dataclass (after §3 because that's the largest new knob); covers §7 (case-insensitive lookup) cleanly along the way.
7. §11 + §10 — auto-draft voice anchor + auto-capsule for emergents (LLM-heavy; can land independently).
8. §12 — relationship history log (touches DB schema; do alone).
9. §9 + §13 — promotion confirmation + sheet migration (Mechanics coordination required).
10. §14 — PCEntry `active` consistency cleanup (small but easy to miss; tag onto a related PR).
11. §15 — search ranking / advanced filters (YAGNI until a UI consumer asks).

§16–§19 are **(v2; deferred)** and §20 is **(rejected)** — leave them alone unless re-brainstormed.
