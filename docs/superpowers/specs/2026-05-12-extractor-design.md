# Extractor — Design (Shipped)

> Captures the Extractor design as actually built. The matching "remaining" spec at `2026-05-16-extractor-remaining-design.md` covers everything from the original `specs/04-extractor.md` that did **not** land in this work.

**Commit:** `2ded8e3` — "Build Extractor (task 19)" (templates moved to `templates/` in follow-up `e13de4a`)
**Module:** `backend/src/grimoire/extractor/`
**Tests:** `backend/tests/extractor/` (service, rule-based, heuristics, LLM strategy, merge, routing)

## Purpose

The Extractor turns a freetext model response (or player post) into a list of typed `StateDelta` proposals plus `EntityCandidate`s and `ExtractionFlag`s. Three strategies run in parallel; their outputs are merged, deduped, optionally cross-checked against mechanics and the fact ledger, then handed back for the Orchestrator to route into auto-apply / review / drop via `route_deltas`.

Every proposal targets **campaign-local** scope. The Extractor never produces a library-scoped delta — `_make_entity_candidate` and `find_proper_noun_candidates` both stamp `suggested_card={"scope": "campaign-local"}`.

## Module surface

`ExtractorService` (`extractor/service.py`) is constructed with keyword dependencies; everything is optional except that the structured-LLM strategy degrades cleanly when no gateway is supplied:

- `gateway: LLMGatewayLike | None` — the LLM gateway (matches `gateway.complete(task, request, campaign_id=...)`)
- `mechanics: MechanicsValidator | None` — narrow protocol exposing `validate_narrated_event(campaign_id, event, scene)`
- `contradictions: ContradictionChecker | None` — narrow protocol exposing `check(campaign_id, fact_text, about) -> list[str]`
- `config: ExtractorConfig | None` — tunables (thresholds, strategies, caps); defaults match spec 04 §Configuration
- `source: str = "extractor"` — attribution string written into every `StateDelta.source`

The protocols (`extractor/protocols.py`) are deliberately tiny so the service can be wired up and tested without dragging in the full Mechanics or Continuity modules.

## Public API

```python
class ExtractorService:
    async def extract(
        response_text: str,
        scene: Scene,
        campaign_id: CampaignId,
        prior_state_snapshot: StateSnapshot,
        *,
        pre_roll_resolved: bool = False,
    ) -> ExtractionResult

    async def extract_from_user_text(
        user_text: str,
        scene: Scene,
        campaign_id: CampaignId,
        *,
        snapshot: StateSnapshot | None = None,
        player_pc_ref: str | None = None,
    ) -> ExtractionResult
```

`ExtractionResult` (`types/extraction.py`) carries `deltas`, `candidates`, `flags`, `confidence_overall`, plus diagnostics `extraction_strategies_run` and `duration_ms`. Both entrypoints funnel into `_run`.

`route_deltas(deltas, *, config)` lives at `extractor/routing.py` and is what the Orchestrator imports; it returns `Routing(auto_apply, review, dropped)` with `Decision` per delta based on `auto_apply_threshold` / `review_threshold`.

## Timing — runs after streaming, not during

Spec 04 §Performance imagined extraction running in parallel with the response stream ("extraction starts immediately"). The shipped Orchestrator calls `extract(response_text, ...)` once, after the full response has finished streaming (see `_run_turn` in `2026-05-12-orchestrator-design.md`). This is a deliberate deviation: the structured-LLM strategy needs the full text, and the rule-based + heuristic strategies are fast enough on a complete buffer that splitting into `extract_partial` / `extract_full` would add coordination cost without saving user-perceived latency until profiling proves otherwise. Documented in `2026-05-16-extractor-remaining-design.md` §6, resolved as option (b).

## Extraction flow (`_run`)

1. Start a monotonic clock for the `duration_ms` report
2. For each strategy in `config.parallel_strategies`, build a coroutine — strategies not in the list are replaced with `_noop_*` so `gather` indices stay stable. `pre_roll_resolved` is forwarded into the heuristic strategy
3. `asyncio.wait_for(asyncio.gather(*coros, return_exceptions=True), timeout=config.timeout_seconds)`
   - On `TimeoutError`, return early with a single `WARNING` flag (`code="extraction_timeout"`) and no deltas — the Orchestrator's contract is that a missing extraction must not block the turn
4. Unwrap each strategy's result (`_unwrap_list`, `_unwrap_llm`, `_unwrap_heuristic`); per-strategy exceptions become `<strategy>_failed` warning flags and an empty payload
5. Merge `rule_based + llm` deltas via `merge_deltas` (the heuristic strategy emits only flags and candidates)
6. If `from_player and player_pc_ref`, apply `_clamp_player_authority` to each delta: deltas about the player's own PC keep their confidence; deltas about other subjects clamp to `config.player_other_subject_confidence_cap` (spec 04 §Handling player text)
7. If `mechanics` is wired up, `_validate_mechanical_events` walks `DeltaKind.MECHANICAL_EVENT` deltas, constructs a `NarratedEvent`, calls `validate_narrated_event`, and on a `not result.valid` response demotes the delta's confidence by `contradiction_confidence_penalty` and adds a `MISSING_MECHANIC` flag. Validator exceptions are logged + flagged but the delta passes through unchanged
8. If `contradictions` is wired up, `_check_contradictions` walks `DeltaKind.FACT_ADD` deltas, calls `check(campaign_id, fact_text, about)`, and on conflicts demotes confidence and adds a `CONTRADICTION` flag with `payload={"conflicts": [...]}`. The conflicts list is also written into `delta.extra["contradictions"]`
9. Append LLM-strategy flags then heuristic flags
10. `merge_candidates(llm_out.candidates, heur.candidates)` dedupes by `(kind, proposed_id, normalized_name)`; the top `max_new_entities_per_turn` survive
11. `confidence_overall = mean(d.confidence for d in deltas)` or `0.0` if empty
12. Return `ExtractionResult` populated with `extraction_strategies_run` (only strategies that were actually scheduled, in declaration order) and `duration_ms`

## Strategy 1 — rule-based (`extractor/rule_based.py`)

Pure-regex pass yielding high-confidence deltas. Lives in `extract_rule_based(text, *, campaign_id, config, source)` as a generator. Confidence floors come from `config.rule_based_base_confidence` (default 0.95):

- **Time advances** via `_TIME_PHRASE` (count + unit + "passed/later/elapsed/...") render to coarse ISO-8601 via `_iso8601_duration` and emit `TIME_ADVANCE` at base
- **Next-named-time-slot** via `_NEXT_MORNING` ("next morning/day/evening/...") renders to P1D or PT12H, confidence `base * 0.95`
- **Inventory verbs** via `_INVENTORY_VERBS` ("X picked up the Y", "X handed over Z") emit `INVENTORY_CHANGE` clamped to `min(base, 0.8)`. `handed | gave | offered` are mapped to `loss`; everything else to `gain`
- **Inventory drops** via `_DROPPED_VERBS` ("dropped / discarded / left behind") emit `INVENTORY_CHANGE direction=loss` at the same clamp
- **Roll echoes** via `_MECH_ROLL_ECHO` ("rolled N successes") emit `MECHANICAL_EVENT kind=roll_echo` at base
- **Damage / wounds** via `_MECH_DAMAGE` ("X took/suffered N damage") emit `MECHANICAL_EVENT kind=wound` clamped to `min(base, 0.85)`

Every delta carries `extra={"strategy": "rule_based", ...}` so `merge_deltas` can track which strategies converged.

## Strategy 2 — structured LLM (`extractor/llm_strategy.py`)

The workhorse. `extract_with_llm(...)`:

1. `_build_request` renders two Jinja templates — `extractor_system` (with the full JSON schema injected) and `extractor_user` (with the response text plus a compact state view from `_compact_snapshot`). Both live under `backend/src/grimoire/templates/` after `e13de4a`
2. The user message also gets a `_compact_snapshot(snapshot, scene)` view (scene id/title, location, present characters, up to 8 known characters, 5 open commitments, 8 recent facts) so the model can ground proposals
3. `CompletionRequest(model="", system=..., messages=[user], max_tokens=config.llm_max_output_tokens, temperature=config.llm_temperature)` is sent to `gateway.complete(config.task_name, request, campaign_id=...)` — empty `model` lets the gateway route by task (default task `"extractor"`)
4. `_extract_json_payload` recovers JSON from raw output, a ` ```json` fence, or a bare `{...}` substring. Unparseable output becomes an `llm_json_unparseable` warning flag
5. `parse_llm_payload` walks the schema-shaped dict; for each known category it invokes a typed builder (`_make_fact_delta`, `_make_character_update_delta`, ...). `new_characters` are diverted through `_make_entity_candidate` (capped at `max_new_entities_per_turn`)

Gateway exceptions are caught and surfaced as an `llm_call_failed` warning; the rest of the pipeline still runs.

### Builder map

`_BUILDER_MAP` covers exactly the spec 04 §Output schema categories: `facts → FACT_ADD`, `character_updates → CHARACTER_STATE_UPDATE`, `scene_changes → SCENE_CHANGE`, `time_advances → TIME_ADVANCE`, `commitments → COMMITMENT_ADD`, `inventory_changes → INVENTORY_CHANGE`, `mechanical_events → MECHANICAL_EVENT`, `relationship_changes → RELATIONSHIP_UPDATE`, `commitment_resolutions → COMMITMENT_RESOLVE`. The schema itself is generated by `extractor/schema.py::output_schema()` with `additionalProperties: False` on inner objects so the model can't smuggle untyped fields.

Fact `target_id`s use a 10-char SHA-1 of the fact text so distinct proposed facts don't collide on the merge key.

## Strategy 3 — heuristic flags (`extractor/heuristics.py`)

Drift-signal detector. `run_heuristics` invokes three sub-checks:

- `find_proper_noun_candidates` — capitalized phrases not in the scene's `present_character_refs` / snapshot's `character_states`, filtered through `_NAME_STOPLIST` (sentence-starts, days of week, months). Each becomes an `EntityCandidate(kind=CHARACTER, confidence=0.55, suggested_card={"scope": "campaign-local"})` with the surrounding sentence as evidence
- `detect_missing_mechanics` — wound/damage prose without a `_ROLL_HINT` and with `pre_roll_resolved=False` raises a `MISSING_MECHANIC` flag per match
- `detect_missing_context_names` — proper nouns repeated 2+ times that aren't in the scene context raise a `MISSING_CONTEXT` flag

This strategy emits no deltas — only candidates and flags.

## Merging and deduplication (`extractor/merge.py`)

- `merge_deltas(*delta_lists)` keys on `(kind, target_id, target_table)`. When two strategies converge on the same key the higher-confidence delta wins; the kept copy inherits the longer evidence string and a union `extra["strategies"]` list for traceability. Output is sorted by `(kind, target_id, -confidence)`
- `merge_candidates(*candidate_lists)` keys on `(kind, proposed_id, normalized_name)` and keeps the higher-confidence candidate. Output is sorted `(-confidence, name)`
- `split_facts_from_others` is a helper for callers that need to partition facts off — currently only used by tests

## Confidence routing (`extractor/routing.py`)

`route_deltas(deltas, *, config) -> Routing` partitions per `decide`:

```python
if delta.confidence >= config.auto_apply_threshold: AUTO_APPLY  # default 0.85
elif delta.confidence >= config.review_threshold:  REVIEW      # default 0.60
else:                                              DROP
```

The routing primitive is a pure function — the State Store / review queue side effects live in the Orchestrator's `_apply_routing`.

## Player-text authority clamp

`extract_from_user_text` resolves `player_pc_ref` from the explicit kwarg or via `_author_pc(scene)` (POV ref, falling back to the first present PC). After merging, every delta runs through `_clamp_player_authority`:

- `_delta_is_about(delta, pc_ref)` looks at `after["character_id" | "actor_ref" | "from" | "subject"]` (string equality or suffix match) and `after["about"]["character_ids"]`. Match → keep
- Otherwise the delta's confidence is capped at `config.player_other_subject_confidence_cap` (default 0.7)

## Error handling

The contract throughout is "flag and continue" — extraction never raises out of `_run`. Specific contract points:

- **Timeout**: extraction-level `asyncio.wait_for` → empty result + `extraction_timeout` warning
- **Per-strategy exception**: caught by `return_exceptions=True` on `asyncio.gather`, unwrapped into a `<strategy>_failed` warning + empty payload
- **LLM gateway exception**: caught inside `extract_with_llm`, becomes an `llm_call_failed` warning
- **LLM unparseable**: `_extract_json_payload` returns `None` → `llm_json_unparseable` warning
- **Mechanics validator exception**: caught in `_validate_mechanical_events`, becomes a `mechanics_validation_failed` warning, original delta passes through
- **Mechanics rejection** (`result.valid == False`): delta confidence demoted by `contradiction_confidence_penalty`, `mechanics_rejected` flag at `MISSING_MECHANIC` level with `payload={"errors", "warnings"}`
- **Contradiction-check exception**: caught in `_check_contradictions`, logged at WARNING, delta passes through
- **Contradiction found**: confidence demoted, `fact_contradiction` flag at `CONTRADICTION` level, conflicts stored on `delta.extra["contradictions"]`

## Configuration (`ExtractorConfig`)

```python
ExtractorConfig(
    task_name="extractor",                       # gateway task route
    parallel_strategies=("rule_based",
                         "structured_llm",
                         "heuristic_flags"),
    auto_apply_threshold=0.85,
    review_threshold=0.60,
    timeout_seconds=30.0,
    max_new_entities_per_turn=5,
    llm_max_output_tokens=2048,
    llm_temperature=0.0,
    player_other_subject_confidence_cap=0.7,
    contradiction_confidence_penalty=0.25,
    testimony_confidence_penalty=0.1,            # field exists; not yet applied
    rule_based_base_confidence=0.95,
    strategy_tags={"rule_based": "extractor:rule", ...},
)
```

`testimony_confidence_penalty` is declared on the dataclass for API stability but no code path consumes it yet — it's tracked in the remaining-design doc.

## Templates

Prompts live at `backend/src/grimoire/templates/extractor_system.j2` and `extractor_user.j2` (after `e13de4a` consolidated all prompts there). The system template injects the JSON schema as compact JSON; the user template gets `response_text` and the compact `context` snapshot.

## Test wiring

`backend/tests/extractor/conftest.py` provides `FakeGateway`, `FakeMechanics`, `FakeContradictionChecker` plus reusable `scene` and `snapshot` fixtures. Test files cover each module surface:

- `test_service.py` — strategy fan-out, merging, player-authority clamp, mechanics/contradiction integration, timeout, gateway absence
- `test_rule_based.py`, `test_heuristics.py`, `test_llm_strategy.py`, `test_merge.py`, `test_routing.py` — module-local coverage

The fakes give the service its public-API contract: anything implementing `LLMGatewayLike`, `MechanicsValidator`, or `ContradictionChecker` (all `Protocol`s) plugs in without modification.
