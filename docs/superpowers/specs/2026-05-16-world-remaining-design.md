# World — Remaining Work

> Everything from the original `specs/09-world.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-world-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-world-design.md`
**Module:** `backend/src/grimoire/world/`

## 1. `WorldConfig` and the `world:` config block

Spec §Configuration calls for:

```yaml
world:
  weather:
    enabled: true
    seed_per_campaign: true
    model: rule_based
  lore:
    keyword_match: true
    keyword_min_length: 4
    max_lore_in_archive: 5
  atmosphere_auto_generate: true
  composition:
    multiple_calendars_policy: pick     # pick | merge_warn | error
```

None of this is wired today:

- No `WorldConfig` dataclass exists (`backend/src/grimoire/world/` has no `config.py`)
- Weather is always on, always seeded per-campaign, always `rule_based`
- `lore_by_keyword` / `lore_for_post` hard-code `min_length=4` and `max_results=5` as method-level defaults
- `atmosphere_auto_generate` has nothing to wire to (see §3)
- `composition.multiple_calendars_policy` is implicitly `pick` only (see §6)

Likely shape: a `world/config.py` mirroring `orchestrator/config.py` with nested dataclasses for `WeatherConfig`, `LoreConfig`, `CompositionPolicyConfig`; pass it into `WorldService.__init__` and thread the lore knobs into `lore_for_post` / `lore_by_keyword`. Spec also pairs with the global config-loader story; today the orchestrator's `OrchestratorConfig` is constructed at wiring time in `main.py` so follow the same pattern.

## 2. Lore secrecy filtering for player-facing views

`LoreEntry.secrecy` is stored (`types/world.py:103-113`) and the `SecrecyLevel` enum is defined, but nothing in the World module — or anywhere else searched — filters lore by secrecy when returning to a player view. Spec §Lore schema: "the `secrecy` field controls visibility — secret lore is available to the model but hidden from player-facing views by default."

Needs:

- An `audience` parameter (or a separate `for_player` method) on `search_lore` / `lore_by_keyword` / `lore_for_post` that drops `restricted` and `secret` entries
- A clear convention for who flags themselves as the "model" caller (Context Builder) vs the "player" caller (Frontend list views)
- A test matrix covering the four `SecrecyLevel` values

## 3. Atmosphere auto-generation

`WorldMeta.atmosphere` is round-tripped verbatim as `Json`. Spec §World metadata shows a `default_register` / `default_palette` shape, and §Configuration has `atmosphere_auto_generate: true` — implying an LLM-driven generator that fills atmosphere when creating a world with empty values.

No generator exists today. Design needed:

- Where to call it (probably `create_world` when `atmosphere` is empty *and* the config flag is on)
- Which LLM task name to use (likely a new entry in `llm_gateway` task config)
- What input to feed it (world id, name, tags, genre, description)
- A test that mocks the gateway and asserts the `atmosphere` field gets populated

## 4. FTS-backed lore search

`search_lore` is a linear substring scan with hand-rolled scoring (`service.py:844-859`). Spec §Lore schema notes "Lore `keywords` trigger archive-tier inclusion ... The Context Builder calls `World.lore_by_keyword(...)` to find triggered entries" — the substring approach is fine for now, but the `StateStore` already maintains FTS indexes (`StateStore.keyword_search`) and the shipped doc explicitly defers to it.

Switch `search_lore` to drive FTS through `StateStore.keyword_search`, filter the results by composition (so excluded worlds don't leak), and drop the hand-rolled scoring. Keep `lore_by_keyword` and `lore_for_post` as exact-match scans — they're cheap and the keyword list is small.

## 5. Player weather override via the Extractor

Spec §Weather: "Player can override (the Extractor catches 'and it began to rain' and writes a campaign-local weather override)."

`WorldService.override_weather(...)` exists and is callable. Missing wiring:

- An extractor rule (or LLM prompt path) that emits a `weather_override` delta
- A `route_deltas` entry routing that delta kind to `WorldService.override_weather(...)`
- A test that runs a fake extracted delta through the orchestrator's `_apply_routing` and observes the override taking effect on the next `weather_for(...)` call

## 6. Multi-world calendar conflict policy

`calendar_for_campaign(...)` picks the highest-priority world's calendar unconditionally. The spec's `composition.multiple_calendars_policy = pick | merge_warn | error` is not enforced. After §1 lands:

- `pick` — current behavior (no change)
- `merge_warn` — pick highest, but log/emit a warning when other refs declare conflicting `calendar` blocks
- `error` — raise `CompositionError` when more than one ref declares a non-empty calendar that differs from the picked one

## 7. Composition-aware spatial queries

The spec interface shows `adjacent_locations(ref, campaign_id)`, `path_between(a, b, campaign_id)`, `locations_within(parent_ref, campaign_id, depth=1)` — all take entity refs and a campaign id, implying they should resolve against the composition cascade (so campaign-local emergent locations participate, and so cross-world references in a multi-world campaign work).

Today's implementation (`service.py:288-374`) takes `world_id + location_id` and walks the per-world location set only. Campaign-local emergent locations are invisible to spatial queries; multi-world campaigns can't trace connections across world refs (which would only matter if a campaign explicitly imported overlapping geographies — a rare case, possibly intentional).

Decision needed: (a) accept the world-scoped behavior as the v1 design and update the spec, or (b) widen the queries to resolve refs through the composition cascade. (b) is more work — would need a `LocationConnection.to` field that can be a ref instead of an `asset_id` within a world.

## 8. Greeting handoff to Orchestrator

Spec §Greetings: "When a campaign is created with a greeting selected, World hands the greeting to the Orchestrator, which seeds scene 1: time set, location set, present cast in place, opening narration appended as the first post."

`list_greetings` / `get_greeting` are wired (delegate to `LibraryService`). The handoff path — campaign creation → greeting selection → Orchestrator scene-1 seeding — is not visible from World. Verify whether this lives in the campaign-creation flow already and, if not, add the wiring there (probably in whatever owns `create_campaign`, with World contributing only the `get_greeting(...)` read).

## 9. Procedural location generation

Spec §Open questions: "Procedural location generation. 'I enter a tavern' — auto-generate one? Yes via LLM, campaign-local emergent, user review."

Marked as a positive open question; not implemented. When picked up:

- New extractor / orchestrator rule that detects an unresolved location reference in a post
- LLM call to generate a `Location` frontmatter + body
- Write via `StateStore.write_emergent` so the row lives campaign-local until the user promotes it
- A review queue entry (the `state_store.queue_for_review` plumbing already exists for the orchestrator)

## 10. Full `LocationState` API

`LocationStateData` (`types/world.py:190-201`) and the `location_state` SQLite table model the full per-campaign location snapshot (`weather`, `time_of_day`, `occupants`, `condition`, `transient_features`, `updated_at_turn`). World today only reads/writes the `weather` column directly.

If/when callers need the full state (Context Builder describing a location's current condition, Time Engine writing `updated_at_turn` after a tick), add:

- `get_location_state(location_ref, campaign_id, branch_id=None) -> LocationStateData`
- `update_location_state(location_ref, campaign_id, patch, *, branch_id, source, turn_id) -> LocationStateData`
- These should go through `apply_delta` so the delta log records the change, unlike `override_weather` which writes direct

Note this is a v1 nice-to-have, not a hard gap — the spec interface doesn't list either method.

## 11. Faction state delta logging

`update_faction_state` writes direct to SQLite (`service.py:626-671`) and accepts a `source` argument that it currently ignores. The spec doesn't explicitly require delta logging here, but every other long-lived state column in the store goes through `apply_delta`. When the Time Engine starts mutating faction state on ticks, route the write through `apply_delta` so undo / fork / retcon work as expected.

## 12. Cross-world lore sharing / lore families (v2; deferred)

Spec §Open questions: "Some lore appears across variants (a religion in two worlds). v1: duplicate; v2: lore families if patterns emerge." `cross_world_lookup` already surfaces variants by shared `asset_id`; nothing further is needed for v1.

## 13. Map UI (v2; deferred)

Spec §Open questions: "Map UI. Worth having? v2 candidate; schema supports coordinates and connections." `Coords` is on the `Location` model; no UI work is in scope.

## 14. Travel mechanics (rejected for World)

Spec §Open questions: "Travel mechanics. World handles description; mechanics handles mechanical effects." World's job here ends with `path_between` returning the route description (`LocationConnection` carries `via` + `duration_min`); fatigue / encounter rolls live in the active mechanics module. **No work owed by World.**

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. **§1 + §6** — land the config block first; calendar policy is a one-line consumer of it. This unblocks §3/§5/§9 by giving them feature flags.
2. **§2** — secrecy filtering is small, well-bounded, and unblocks anything player-facing that touches lore.
3. **§4** — FTS swap is internal-only and benefits Context Builder immediately.
4. **§5** — wire the extractor → `override_weather` path; depends on the Orchestrator's `_apply_routing` (already shipped).
5. **§8** — greeting handoff; coordinate with whoever owns `create_campaign` (probably the API / library wiring, not World itself).
6. **§3** — atmosphere generation; needs an LLM task + prompt and is the heaviest piece of work in this set.
7. **§7** — composition-aware spatial queries; decide-then-implement.
8. **§9** — procedural location generation; the largest and depends on §1 (feature flag) + the extractor work in §5.
9. **§10 + §11** — full `LocationState` surface + faction-state delta logging; pull in when Time Engine starts mutating world state, not before.
