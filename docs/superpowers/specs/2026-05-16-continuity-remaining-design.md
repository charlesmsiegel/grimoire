# Continuity — Remaining Work

> Everything from the original `specs/11-continuity.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-continuity-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-continuity-design.md`
**Module:** `backend/src/grimoire/continuity/`

## 1. Per-campaign service scoping

Today `main.py:139-143` constructs a single shared `ContinuityService()` for the whole app with the in-memory store. The API routes in `backend/src/grimoire/api/campaigns.py:787-835` work around this by over-fetching and filtering rows by `campaign_id` client-side:

```python
all_facts = await continuity.facts_about(limit=max(limit * 8, 200))
scoped = [f for f in all_facts if getattr(f, "campaign_id", None) == campaign_id]
```

That filter only works because `SqliteContinuityStore` rows have a `campaign_id` column — but the in-memory store doesn't, so today's default returns facts from every campaign mixed together (no one notices because nothing writes facts yet — see §5).

Design needed:
- A `ContinuityRegistry` (or extend the existing container pattern) that hands out one `ContinuityService` per (campaign_id, branch_id), constructing a `SqliteContinuityStore(db, campaign_id=..., branch_id=...)` and a matching `HybridFactSearchIndex` lazily on first use.
- API routes resolve the per-campaign service from the path id rather than depending on `ContinuityDep` (or the dependency itself takes the campaign id).
- The over-fetch/filter hacks in `api/campaigns.py` go away.

## 2. Wire SqliteContinuityStore as the default

`main.py:139-143` currently has a TODO comment ("Swap in SqliteContinuityStore when persistence matters"). Once §1 lands, the per-campaign factory should construct `SqliteContinuityStore(container.state_store.db, campaign_id=..., branch_id=...)` so facts and commitments survive restart. Migration 005 already provides the schema.

## 3. Wire HybridFactSearchIndex and LLMContradictionJudge

The default `ContinuityService()` uses `KeywordFactSearchIndex` and `StubContradictionJudge`. To get actual contradiction detection working end to end:

- Construct `HybridFactSearchIndex(store, db, campaign_id=..., branch_id=..., embedder=<LLMGateway-backed QueryEmbedder>)`. The `QueryEmbedder` protocol takes a `(task, list[str]) -> list[list[float]]` shape; `LLMGatewayService.embed` already has that signature.
- Construct `LLMContradictionJudge(gateway=container.extras["llm_gateway"], request_factory=<builds a CompletionRequest>, task=config.contradiction_check.model_route)`. The `model_route` config knob already exists but is unused (`config.py:17`).
- Inject both into the per-campaign `ContinuityService`.

Without this, `check_contradictions` always returns an empty report (keyword search may find similar facts but `StubContradictionJudge` always returns UNCERTAIN with confidence 0, which is filtered out at `service.py:240`).

## 4. Continuity event emission

The original spec calls for the bus events `fact_recorded`, `commitment_paid_off`, `commitment_broken`, `commitment_overdue`, `commitment_stale`, `contradiction_detected`. The event bus header in `event_bus.py:1-9` even names Continuity as a subscriber/emitter. Today **no continuity code emits anything**. The relevant emission sites:

- `add_fact` after `store.put_fact` → `fact_recorded {fact_id, campaign_id, source}`
- `resolve_commitment(status=PAID)` → `commitment_paid_off {commitment_id, in_post}`
- `resolve_commitment(status=BROKEN)` → `commitment_broken {commitment_id, in_post}`
- `age` — one event per item transitioned: `commitment_overdue` for each in `became_overdue`, `commitment_stale` for each in `became_stale`
- `check_contradictions` after persisting a report with non-empty `conflicts` → `contradiction_detected {report_id, conflict_count}`

Needs: `ContinuityService` takes an optional `event_bus: EventBus | None` (mirrors how `SceneManager` does it in `main.py:138`); emit calls swallow exceptions like the orchestrator's `_emit_turn_event` does. Add subscription tests in `tests/continuity/test_events.py`.

## 5. Extractor → Continuity write path

`grimoire.extractor` produces deltas from prose but **does not call `add_fact` or `add_commitment`** today (`grep` for `add_fact`/`add_commitment` in `extractor/` returns nothing). The original spec assumes "Facts are produced by the Extractor and written to Continuity."

Needs:
- A delta kind for `fact` and `commitment` in whatever the Extractor's delta union currently supports.
- A router (likely in the Orchestrator's `_apply_routing`, since deltas already flow through there) that maps `delta.kind == 'fact' | 'commitment'` to `continuity.add_fact` / `add_commitment` instead of `state_store.apply_delta`.
- Run `check_contradictions(candidate)` before writing; route reports with non-empty `conflicts` to the State Store review queue (`state_store.queue_for_review`) so the user can resolve via `resolve_contradiction` (already implemented).

Until this lands, the only way a fact gets into Continuity is the `POST /campaigns/{id}/facts` endpoint (user-declared).

## 6. Knowledge-state filtering in Context Builder

Spec §Knowledge state per character: "Used for filtering Context Builder output by POV, preventing model from making characters react to things they don't know."

Today `context/builder.py:_continuity_background` calls `continuity.facts_about(limit=8)` with no character filter. To honour POV:
- Pass the active PC ref (`active_pc_ref`, already in scope) through to a new filter parameter on `facts_about` or call `continuity.knows(active_pc_ref, fact.id)` per candidate fact and drop those the PC doesn't know.
- Probably wants a new bulk query like `facts_known_by(character_id, limit)` on the Continuity protocol so we don't N+1 the knowledge table.

Also needs an opt-out for the narrator/omniscient view — when there's no active PC, surface everything (current behaviour).

## 7. Keyword retrieval (prose-driven)

Spec §Retrieval describes a second path: "When proper nouns or topic keywords appear in recent posts, Continuity looks up tagged facts and surfaces them." The `KeywordRetrievalConfig` dataclass exists (`min_keyword_length`, `case_insensitive`) but nothing reads it beyond `min_keyword_length` (the `KeywordFactSearchIndex` constructor consumes it as a token-length floor).

Needs a method on `Continuity` like `facts_for_terms(terms: list[str], limit=10)` plus a tokenizer/proper-noun extractor over the most recent posts (Context Builder side). The Context Builder calls it during background-tier assembly. `keyword_retrieval.case_insensitive` finally has a consumer.

## 8. Configuration knobs not yet enforced

These exist in `ContinuityConfig` but no code path reads them:

- `contradiction_check.model_route` — needs §3 wiring to flow into `LLMContradictionJudge(task=...)`.
- `keyword_retrieval.case_insensitive` — used implicitly by the token regex today; should be honoured per-config in any new `facts_for_terms` (§7).
- `surface_overdue_in_context` (default `True`) — Context Builder should call `overdue_commitments(as_of)` and inject those into the lock-in tier when this is set. Today `_render_commitments` already pulls open + overdue via `open_commitments` but doesn't separate them or check the flag.
- `surface_stale_in_context` (default `False`) — when True, also surface stale commitments. Today never queried by the builder.

## 9. STALE → REOPENED transition

Spec §Commitment lifecycle lists `STALE -> REOPENED (becomes relevant again)`. The `CommitmentStatus` enum in the dataclass module has no `REOPENED` member (`continuity/types.py:123-128`); the parallel pydantic enum in `types/continuity.py:71-77` does. `resolve_commitment` refuses `OPEN` but accepts any other status, so a caller can already write `STALE → BROKEN` etc. — but there's no clean "this got relevant again" path.

Needs: add `REOPENED` to `CommitmentStatus` in `continuity/types.py`, decide whether REOPENED is terminal-for-aging-purposes (probably it ages like OPEN), and add a `reopen_commitment(cid, in_post)` helper or document that callers use `resolve_commitment(cid, REOPENED, in_post)`. Surface REOPENED items in `AgingReport` as a new field if `age` should be the one promoting STALE back to active.

## 10. UI surfaces

Spec §Surfacing to the user lists five surfaces. Status today:

| Surface | State |
| --- | --- |
| Context Builder injection | Partially shipped (commitments + 8 recent facts, no POV filter, no overdue/stale flag handling) |
| UI panel: "campaign ledger" | **Not shipped.** Frontend has no ledger view; only the `GET /campaigns/{id}/{facts,commitments}` endpoints exist. |
| Pre-scene briefing | **Not shipped.** Scene Manager doesn't call Continuity on scene open. |
| End-of-session digest | **Not shipped.** No "session" concept end to end yet. |
| EPUB export appendix | Shipped (`export/epub.py:_render_continuity`) |

The UI panel is the most actionable: a route like `GET /campaigns/{id}/continuity/ledger` that returns `{open_commitments, overdue, stale, recent_facts, unresolved_contradictions}` in one round-trip, plus a Frontend page that surfaces it.

The pre-scene briefing wants a `Continuity.brief_for_scene(scene_id, pc_refs)` helper that returns "active threads involving these PCs"; needs Scene Manager to call it during `start_scene`.

## 11. `contradiction_reports` listing API

`SqliteContinuityStore.get_contradiction_report` exists but there is no `list_contradiction_reports(resolved: bool | None)` on either the store or the service. To build the "Conflict detected — pick a resolution" UI we need a way to enumerate unresolved reports per campaign. Add `list_contradiction_reports` to the `ContinuityStore` protocol and a `pending_contradictions(limit=20)` convenience to the service.

## 12. Inferred facts (v2; deferred)

Spec §Open questions: "Should the system propose inferred facts ("if A and B, then C")? Possibly, with extra-low confidence and explicit `inference` source."

The `FactSource.INFERRED` enum value exists today but nothing produces inferred facts. Treat as v2 until the Extractor's primary write path (§5) is solid.

## 13. Fact graph visualization (v2; deferred)

Spec §Open questions calls this out as "future feature." No work needed here beyond noting it doesn't get scoped into v1.

## 14. Commitment prioritization heuristic (rejected as a config knob)

Spec §Open questions proposes `weight × recency × due-proximity` as a tunable. Today `open_commitments` sorts by `(−weight, due_by or +∞, in_game_created_at)` and that hard-coded order has been adequate for the Context Builder. Don't add a tunable until we have evidence the current order misranks. Treat as **rejected** unless re-brainstormed.

## 15. Auto-resolve stale commitments (rejected)

Spec §Open questions: "Should commitments auto-resolve to 'abandoned' after N years? User preference; default to surface, not auto-resolve." The shipped behaviour already matches the spec's default — STALE is a surface signal, not a terminal status. Treat as **rejected** until a user requests it.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §1 + §2 — per-campaign scoping plus SqliteContinuityStore wiring. Everything else assumes facts survive restart and routes are clean.
2. §3 — wire HybridFactSearchIndex and LLMContradictionJudge. Cheap once §1 is done, and unlocks real contradiction detection.
3. §5 — extractor → continuity write path. This is the moment Continuity stops being a manual-only ledger.
4. §4 — event emission. Easier to add tests once §3 + §5 are producing real reports and writes.
5. §11 — `list_contradiction_reports` + `pending_contradictions`. Needed before the UI panel.
6. §10 — UI panel + pre-scene briefing + end-of-session digest. The Frontend work that turns the ledger into something the user touches.
7. §6 + §7 + §8 — Context Builder polish: POV filtering, prose-driven retrieval, config knobs. Cleanest after §10 surfaces what's missing.
8. §9 — REOPENED. Small, isolated; pick up whenever a real use case appears.
