# Extractor — Remaining Work

> Everything from the original `specs/04-extractor.md` (now superseded) that did **not** land in the shipped design (`2026-05-12-extractor-design.md`). Use this as the input to a writing-plans pass when picking up the work.

**Companion (already shipped):** `2026-05-12-extractor-design.md`
**Module:** `backend/src/grimoire/extractor/`

## 1. Library-targeted change detection

Spec 04 §Library-targeted changes calls for the Extractor to detect when prose modifies an entity that resolves through a library asset (e.g. vivienne's appearance described differently from her library card) and propose a **campaign-local override**, never a library edit. The user gets three choices in the review UI: "Add as campaign-local override", "Edit library card", "Treat as transient".

Today none of the strategies look at the resolved scope of referenced entities. The `EntityCandidate.suggested_card` always stamps `scope: "campaign-local"` for *new* names; there is no path for *existing* library entities to have their narrated state diverge and surface as overrides.

Likely shape:

- A new dependency protocol (e.g. `EntityResolver`) that, given a character/location ref, reports `(scope, current_card)` from the library or campaign DB
- An LLM-strategy post-processing step (or a fourth strategy) that compares `character_updates` / `scene_changes` against the resolved card and, on divergence, retypes the delta as a new `DeltaKind.CAMPAIGN_OVERRIDE` (or annotates `extra["override_of_library"] = True` and routes to review)
- A new flag code (e.g. `library_drift`) so the UI prompt can render the three-option choice rather than the normal review actions

## 2. Contradiction-and-resolution review workflow

Spec 04 §Contradictions and the contradiction log says contradictions don't silently apply or drop: the new delta is queued *with the contradiction flagged*, the existing fact is included in the review item, and the user resolves "keep old / replace with new / merge".

Today `_check_contradictions` (service.py:339-377) downgrades the new delta's confidence by `contradiction_confidence_penalty` (default 0.25) and stuffs the conflict list into `delta.extra["contradictions"]` + a `CONTRADICTION` flag. That's enough to push most contradicting facts into the REVIEW bucket but:

- There is no guarantee a contradicting fact lands in REVIEW (a fact starting at 0.99 still routes to AUTO_APPLY after the 0.25 penalty)
- The existing-fact payload is just a list of strings — no fact ids, no full fact records the UI can render
- No "merge" affordance exists on either side

Needs: force-route contradicting deltas to REVIEW regardless of confidence; expand `ContradictionChecker.check` to return structured conflicts (id + text + source turn); design a review-queue resolution choice with merge semantics.

## 3. Speaker-authority confidence adjustment

Spec 04 §Confidence scoring lists "Speaker authority" as one of four scoring factors: the GM-voice narrator is more authoritative than a character's claim ("she lied that she had..." is a low-confidence fact).

The config field `testimony_confidence_penalty: 0.1` exists on `ExtractorConfig` for this, and the schema includes `speaker_id` on fact items, but no strategy reads `speaker_id` and no penalty is applied. The LLM builder passes `speaker_id` through into `delta.after["speaker_id"]` (llm_strategy.py:161) and stops there.

Needs: in `_run`, after merging, walk `FACT_ADD` deltas, and if `after["speaker_id"]` is set and is not the GM narrator marker, subtract `testimony_confidence_penalty`. Decide what the "GM narrator" marker is (likely a sentinel `speaker_id=None` for prose narration, vs a character ref for in-character speech).

## 4. Auto-apply / queue / drop side effects

Spec 04 §Confidence scoring describes auto-apply at >=0.85, queue for review at 0.6-0.85, drop with log entry at <0.6. The pure-function `route_deltas` partitioning is shipped; the **drop log entry** is not. Today `Routing.dropped` is just a list — dropping a delta produces no record, so the calibration feedback loop (spec §Open questions item 4) has nothing to learn from.

Needs: emit a debug log line per dropped delta (kind, target_id, confidence, evidence) so operators can see what's being silently discarded; optionally surface a counter to the Orchestrator's `deltas_extracted` event.

## 5. Extractor retry on parse failure

Spec 04 §Configuration implicitly assumes the orchestrator's `ErrorConfig.retry_extractor_on_parse_failure: 1` is meaningful: today the structured-LLM strategy raises an `llm_json_unparseable` flag on the first parse failure and gives up. Add a single retry inside `extract_with_llm` when `_extract_json_payload` returns `None`, gated on a config flag (matches the orchestrator-side gap in `2026-05-16-orchestrator-remaining-design.md` §9).

The retry should reuse the same `CompletionRequest` with an appended user message instructing the model to return valid JSON only — or alternatively re-render the prompt with an explicit "your last response was unparseable" preamble.

## 6. Streaming-friendly start — RESOLVED (option b)

**Resolution:** option (b) — extraction runs once after the full response streams. The deviation is documented in `2026-05-12-extractor-design.md` under "Timing — runs after streaming, not during". Option (a) (`extract_partial` API + per-chunk orchestrator calls) is deferred until profiling shows the structured-LLM strategy is the user-perceived bottleneck; the rule-based and heuristic strategies are fast enough on a complete buffer that the coordination cost of splitting isn't worth it speculatively.

## 7. Commitment-id resolution for `commitment_resolutions`

The LLM schema includes a `commitment_resolutions` category referencing an existing `commitment_id`. `_make_commitment_resolution_delta` (llm_strategy.py:309-326) trusts the model's id verbatim — if the model hallucinates `c_4521` and there's no such commitment, the delta will fail at apply time inside the State Store.

Needs: resolve the proposed commitment id against `snapshot.open_commitments` either inside the builder or as a post-merge validation step. Unmatched ids should produce a `CONTRADICTION` (or new `UNRESOLVED_REFERENCE`) flag and route to review.

## 8. New-entity proposals beyond characters

Spec 04 §Entity candidates: "The same pattern applies to locations introduced in prose, factions named in dialogue, lore mentioned in passing — all start campaign-local, can be promoted later."

Today `_make_entity_candidate` hardcodes `kind="character"` and `find_proper_noun_candidates` hardcodes `kind=EntityKind.CHARACTER`. The schema's `new_characters` array also doesn't have peer arrays for `new_locations` / `new_factions` / `new_lore`.

Needs: schema additions (`new_locations`, `new_factions`, `new_items`), corresponding builders, and a heuristic-side classifier (e.g. "the Florentine Society" → faction, "the orchard" → location) — though candidate-kind classification is genuinely hard; a v1 may want to leave classification to the LLM strategy and have the heuristic only emit character candidates.

## 9. Confidence calibration feedback loop (v2; deferred)

Spec 04 §Open questions: "Confidence calibration. The threshold values are guesses. We need a feedback loop: when users reject auto-applied deltas, lower the threshold; when they always approve queued ones, raise it."

Defer until there's actual user data. Recording the inputs (auto-applied delta ids, user accept/reject decisions on review queue items) is a prerequisite — that data plumbing belongs with the review-queue UI work, not here.

## 10. Schema versioning + migration (v2; deferred)

Spec 04 §Open questions: "The output schema will grow. Versioning the schema and migrating stored deltas is needed long-term."

Not urgent — the State Store stores the typed `StateDelta` objects, not the raw LLM payload. When the schema actually changes in a backward-incompatible way we'll cross that bridge. Worth noting so it doesn't get re-litigated.

## 11. Self-consistency / double-extraction (rejected)

Spec 04 §Open questions: "Should the Extractor be run twice with different models and the results merged? Probably overkill for v1."

Treat as **rejected** unless evidence emerges otherwise; do not add to a plan without re-brainstorming.

## 12. Player-authority heuristic tuning (v2; deferred)

Spec 04 §Open questions: "How much should we trust player declarations vs. GM ratification? The above heuristic is a starting point; needs tuning." Shipped behavior is a single `player_other_subject_confidence_cap` knob (default 0.7). Tuning waits on real play data — same gating as §9.

---

## Suggested plan ordering

If picking this up, a reasonable order:

1. §5 (extractor retry on parse failure) — small, self-contained, pairs with the orchestrator-side §9 in `2026-05-16-orchestrator-remaining-design.md`
2. §3 (speaker-authority penalty) + §4 (drop logging) — both touch only `_run` and the existing strategies; small wins on calibration quality
3. §7 (commitment-id resolution) — schema-shaped fix, needed before commitment resolution is usable end-to-end
4. §2 (contradiction review workflow) — needs `ContradictionChecker` protocol expansion + review-queue UI cooperation; coordinate with State Store / review-queue plan
5. §1 (library-targeted overrides) — biggest design surface; depends on an entity-resolver protocol that probably doesn't exist yet and on the review-queue choices from §2
6. §8 (multi-kind entity candidates) — schema growth; do after §1 so the library-resolution path is in place
7. §6 (streaming-friendly start) — only worth doing if a profile shows the structured-LLM strategy is the bottleneck and partial-extraction would actually save user-perceived latency
