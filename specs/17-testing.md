# 17 — Testing

## Purpose

The Testing spec describes how the system is tested — not a module the app ships, but a design constraint on every other module. The architecture's emphasis on isolated modules with typed interfaces only pays off if those interfaces are tested. This spec defines the test strategy per module type, the integration patterns, the plugin conformance suite, and the LLM record/replay machinery that makes deterministic tests possible despite a non-deterministic dependency.

## Goals

1. **Every module independently testable** with its dependencies mocked
2. **End-to-end turn loop testable** without hitting a real LLM
3. **Every plugin conformance-checked** against its host module's protocol before being registered
4. **Long-running campaigns regression-protected** against silent state-model breakage
5. **Performance regression detected** before users feel it

## Testing layers

```
   ┌─────────────────────────────────────────────────┐
   │  L5: User scenario tests                         │
   │  (full app, real-ish LLM via record/replay)      │
   ├─────────────────────────────────────────────────┤
   │  L4: Frozen campaign tests                       │
   │  (snapshot campaigns, run forward, compare)      │
   ├─────────────────────────────────────────────────┤
   │  L3: Integration tests                           │
   │  (multi-module flows; LLM mocked or replayed)    │
   ├─────────────────────────────────────────────────┤
   │  L2: Plugin conformance tests                    │
   │  (every plugin passes its kind's contract)       │
   ├─────────────────────────────────────────────────┤
   │  L1: Unit tests                                  │
   │  (per-module; all deps mocked)                   │
   └─────────────────────────────────────────────────┘
```

Each layer is owned by a different audience: unit and conformance tests by individual module authors; integration tests by feature work; frozen-campaign tests as part of the CI pipeline; scenario tests as exploratory and pre-release.

## L1 — Unit tests per module

Every module's interface is the unit boundary. Tests live alongside the module and exercise the protocol with all dependencies mocked.

| Module | What gets tested |
|---|---|
| Library | Asset CRUD, member add/remove, composition resolution (priority cascading), version pinning, promotion from campaign-local, character family operations, scope filter application, dependent-campaign lookup |
| Orchestrator | Turn lifecycle correctness, event emission, hook execution order, error recovery per step, retry behavior |
| Context Builder | Tier promotion logic, budget allocation, retrieval result merging, voice anchor inclusion, mechanics injection, cross-scope source attribution |
| State Store | Schema migrations, delta application, delta reversal, fork copy-on-write semantics, contradiction detection, vector search correctness, **read cascade across library and campaign scopes**, **library asset versioning** |
| Extractor | Each strategy in isolation (rule-based, structured-LLM with mocked output, heuristic flagging), confidence scoring, contradiction handling, **scope assignment for new entities (always campaign-local)** |
| LLM Gateway | Provider adapter contract, retry policy, tokenizer cache, streaming normalization, cost computation |
| Mechanics | (Per module) pre-roll evaluation, roll resolution determinism with fixed seed, character creation step validation, time tick effects, **sheet lookup across (character, mechanics, scope) keys** |
| Time Engine | Significance filter, NPC tick coherence, faction tick, scheduled event triggering, commitment aging |
| Characters | Tier recommendation, drift detection logic, voice anchor versioning, cross-setting variant resolution by shared id, import parsing, **scope-aware CRUD**, **library/campaign override semantics**, **promote-to-library**, **multi-PC coordination** |
| Setting | Spatial queries, lore keyword matching, weather determinism (same seed = same output), calendar arithmetic, **scope filters in composition** |
| Scene Manager | Boundary detection signals, summary generation triggering, scene-seed construction |
| Continuity | Fact retirement, commitment lifecycle transitions, knowledge state filtering, contradiction reports |
| ImageGen | Prompt composition, queue scheduling, seed reproducibility, **preset resolution from library** |
| Export | Each adapter's pipeline, filter application, asset bundling, **cross-scope composition resolution** |
| Plugins | Manifest validation, dependency resolution (topological sort, cycle detection), lifecycle ordering |
| Observability | Audit record completeness, cost rollups, log query correctness, **composition snapshot capture** |

Each module's directory contains a `tests/` subdirectory with unit tests. Naming convention: `test_<concern>.py` (or `.test.ts`). Test framework: pytest (Python) or vitest (TypeScript).

## L2 — Plugin conformance tests

Every plugin kind has a standard conformance suite. Plugins pass the suite before being registered.

```python
class ConformanceSuite(Protocol):
    kind: str                          # "mechanics", "llm_provider", etc.
    async def run(self, adapter: Any) -> ConformanceReport: ...

@dataclass
class ConformanceReport:
    passed: list[str]                  # test names
    failed: list[tuple[str, str]]      # (test name, failure message)
    skipped: list[tuple[str, str]]     # (test name, reason)
    duration_ms: int
```

### Mechanics conformance suite

- `test_sheet_schema_valid_json_schema`
- `test_validate_sheet_accepts_valid`
- `test_validate_sheet_rejects_invalid`
- `test_evaluate_pre_roll_returns_list`
- `test_resolve_roll_deterministic_with_seed`
- `test_resolve_roll_within_legal_outcome_space`
- `test_character_creation_steps_resolvable_in_order`
- `test_time_tick_returns_valid_deltas`
- `test_combat_optional_or_complete` (if `combat_supported: true`, full combat suite runs)

### LLM Provider conformance suite

- `test_complete_returns_completion_result`
- `test_complete_handles_empty_response`
- `test_complete_streaming_callback_invoked`
- `test_complete_structured_validates_against_schema`
- `test_embed_returns_correct_vector_dimensions`
- `test_tokenize_consistent_across_calls`
- `test_retry_on_5xx`
- `test_no_retry_on_4xx_other_than_429`
- `test_429_respects_retry_after`
- `test_cost_estimate_nonzero_for_paid_models`

### ImageGen Backend conformance suite

- `test_generate_returns_image_bytes`
- `test_generate_preserves_seed`
- `test_generate_same_seed_same_image` (where backend guarantees this)
- `test_upscale_returns_larger_image` (if capability declared)
- `test_inpaint_respects_mask` (if capability declared)

### Export Adapter conformance suite

- `test_export_produces_nonempty_output`
- `test_export_respects_scene_selection`
- `test_export_respects_appendix_selection`
- `test_export_validates_against_format_spec` (e.g., EPUBCheck for EPUB)

Conformance is checked at plugin load time (in dev mode) or in CI (always). Failing conformance excludes a plugin from the registry with a clear error.

## L3 — Integration tests

Integration tests cross module boundaries with controlled inputs. Patterns:

### Turn loop end-to-end (mock LLM)

```python
async def test_turn_full_loop():
    app = TestApp.with_fixtures("simple_campaign")
    app.llm_gateway.set_mock_response("She nods. 'I'll meet you at Camden Market.'")
    result = await app.orchestrator.submit_turn(
        player_input="I tell winifred I want to talk privately."
    )
    assert result.scene_appended
    assert len(result.extracted_deltas) > 0
    # Expect a commitment fact about meeting at Camden Market
    facts = await app.continuity.recent_facts(since=app.calendar.current())
    assert any("camden" in f.text for f in facts)
```

### Scene boundary detection with auto-break

```python
async def test_scene_break_on_location_change():
    app = TestApp.with_fixtures("library_scene_active")
    result = await app.orchestrator.submit_turn(
        player_input="The next morning, I head down to the kitchen."
    )
    assert result.scene_broke
    assert result.scene_id != app.fixtures["library_scene_id"]
```

### Time advancement with NPC ticks

```python
async def test_npc_tick_runs_for_significant_offscreen_npcs():
    app = TestApp.with_fixtures("estate_with_household")
    app.llm_gateway.set_mock_response_for_role("npc_tick", deterministic_tick_response)
    result = await app.time_engine.advance(Duration(weeks=1), reason=EXPLICIT_USER)
    assert "alistair-hyde-smythe" in result.npc_summaries
    assert "winifred" in result.npc_summaries
    # the gardener (minor NPC, no PC commitment) should not tick
    assert "gardener" not in result.npc_summaries
```

### Extractor produces expected deltas

```python
async def test_extractor_identifies_commitment():
    app = TestApp.with_fixtures("conversation_scene")
    result = await app.extractor.extract(
        response_text='winifred smiled. "I will teach you to ride. Tomorrow morning."',
        scene=app.current_scene(),
        prior_state_snapshot=app.snapshot(),
    )
    assert any(
        d.kind == "commitment_added" and "ride" in d.after["text"]
        for d in result.deltas
    )
```

### Library composition: characters resolve through cascade

```python
async def test_resolve_character_uses_library_with_campaign_override():
    app = TestApp.with_fixtures("library_with_two_campaigns")
    # wod-london contains alistair-hyde-smythe; campaign A has an override on her mood
    resolved_a = await app.characters.resolve("alistair-hyde-smythe", campaign_id="campaign-a")
    resolved_b = await app.characters.resolve("alistair-hyde-smythe", campaign_id="campaign-b")
    assert resolved_a.current_state.emotional_state == "stormy"   # override in A
    assert resolved_b.current_state.emotional_state == "calm"     # library default
    assert resolved_a.appearance == resolved_b.appearance          # library card unchanged
```

### Crossover composition: characters from setting A, locations from setting B

```python
async def test_crossover_campaign_resolves_from_multiple_assets():
    app = TestApp.with_fixtures("crossover_setup")
    # campaign-crossover: settings=[wod-london], settings=[wod-london]
    cast = await app.characters.list_for_campaign("campaign-crossover")
    world = await app.setting.list_locations_for_campaign("campaign-crossover")
    assert any(c.name == "Alistair" for c in cast)                # from wod-london characters
    assert any(l.name == "Camden Market" for l in world)           # from wod setting
    assert not any(l.name == "Brixton" for l in world)             # wod-nyc not referenced
```

### Promotion: campaign-local NPC becomes library

```python
async def test_promote_campaign_local_character_to_library():
    app = TestApp.with_fixtures("emergent_npc_in_campaign")
    # The Bartender was spawned mid-play in campaign-a; she is campaign-local
    new_id = await app.characters.promote_to_library(
        character_id="bartender-campaign-local",
        target_setting_id="wod-london",
    )
    # In campaign-a she should now resolve to the library version
    resolved = await app.characters.resolve(new_id, campaign_id="campaign-a")
    assert resolved.scope_chain[0].scope == "library"
    # A new campaign created with wod-london should also see her
    app.create_campaign("new-campaign", settings=["wod-london"])
    cast = await app.characters.list_for_campaign("new-campaign")
    assert any(c.id == new_id for c in cast)
```

### Version pinning protects active campaign from library edit

```python
async def test_pinned_campaign_sees_old_version_after_library_edit():
    app = TestApp.with_fixtures("campaign_with_pinned_ref")
    # campaign-pinned references wod-london v3
    original = await app.characters.resolve("alistair-hyde-smythe", "campaign-pinned")
    # Edit the library version
    await app.library.update_character("alistair-hyde-smythe", patch={"appearance": "new"})
    # Pinned campaign still sees v3
    still_original = await app.characters.resolve("alistair-hyde-smythe", "campaign-pinned")
    assert still_original.appearance == original.appearance
    # Upgrade the ref
    await app.library.upgrade_ref("campaign-pinned", "wod-london")
    # Now it sees the new version
    updated = await app.characters.resolve("alistair-hyde-smythe", "campaign-pinned")
    assert updated.appearance == "new"
```

### Mechanics swap: same characters, different system

```python
async def test_same_setting_two_campaigns_different_mechanics():
    app = TestApp.with_fixtures("wod_with_two_mechanics")
    # Both campaigns use wod-london; one uses wod, one uses another-campaign
    wod_sheet = await app.mechanics.get_sheet("alistair-hyde-smythe", campaign_id="campaign-wod")
    ars_sheet = await app.mechanics.get_sheet("alistair-hyde-smythe", campaign_id="campaign-ars")
    assert wod_sheet.system == "wod-mechanics"
    assert ars_sheet.system == "another-campaign-mechanics"
    # Both resolved through library; both campaigns share the same character card
    wod_char = await app.characters.resolve("alistair-hyde-smythe", "campaign-wod")
    ars_char = await app.characters.resolve("alistair-hyde-smythe", "campaign-ars")
    assert wod_char.appearance == ars_char.appearance
```

Integration tests live in a top-level `tests/integration/` directory. Each test sets up a controlled `TestApp` instance with explicit fixtures.

## Library + campaign fixtures

A new fixture type combines library state with one or more campaign states:

```python
@dataclass
class LibraryCampaignFixture:
    library_assets: list[LibraryAsset]              # settings, presets
    library_entities: dict[str, list]               # characters, locations, lore per asset
    character_families: list[CharacterFamily]
    campaigns: list[CampaignFixture]                # each with composition + state
```

Frozen-campaign tests now snapshot both library and campaign state. Replay-forward tests can mutate either independently to verify cascading behavior.

## LLM record/replay

The hardest part of testing this system is the LLM dependency. Record/replay solves it:

```python
class RecordReplayLLM(LLMGateway):
    mode: Literal["record", "replay", "passthrough"]

    async def complete(self, messages, params, on_token=None):
        if self.mode == "record":
            result = await self._real_complete(messages, params)
            self._save_fixture(messages, params, result)
            return result
        elif self.mode == "replay":
            return self._load_fixture(messages, params)
        else:
            return await self._real_complete(messages, params)
```

Fixtures are stored in `tests/fixtures/llm/` as JSON files, named by hash of (messages + params + model). When test runs in replay mode, hitting a missing fixture is an error (fast-fails rather than calling the real API silently).

Recording is run periodically against the real API to update fixtures. CI runs in replay mode only.

### Fixture format

```json
{
  "request": {
    "model": "claude-opus-4-7",
    "messages_hash": "sha256:...",
    "messages": [...],
    "params": {...}
  },
  "response": {
    "text": "...",
    "finish_reason": "stop",
    "usage": {...},
    "latency_ms": 2341,
    "recorded_at": "2026-05-11T..."
  }
}
```

### Mocked LLM (simpler alternative)

For most unit and integration tests, a fully mocked LLM is preferable to recorded fixtures:

```python
mock_llm = MockLLMGateway()
mock_llm.queue_response("primary", "She nods.")
mock_llm.queue_response("extraction", {"facts": [...]})
mock_llm.queue_response("drift_check", {"score": 0.2})
```

Each `complete` call pops the next queued response for that task. Tests fail loudly if the queue is exhausted (catches missed expectations).

## L4 — Frozen campaign tests

The system's hardest failure mode is silent state-model breakage in long-running campaigns. A schema change or extractor behavior change can make the next turn subtly wrong without any error.

Frozen campaign tests guard against this:

1. Pick representative campaigns at known good states (snapshots)
2. Store the snapshot as a fixture
3. In CI, load the snapshot, run one or more turns with deterministic LLM responses, verify outputs

```python
@pytest.mark.frozen_campaign("wod_london_session_47")
async def test_advance_one_turn_preserves_invariants(app):
    pre_state = app.snapshot_invariants()
    await app.orchestrator.submit_turn(
        player_input="I look at Alistair and ask if she's well.",
        options=TurnOptions(model_override="mock"),
    )
    post_state = app.snapshot_invariants()
    assert post_state.character_count >= pre_state.character_count
    assert post_state.scene_count == pre_state.scene_count + (1 if app.scene_broke else 0)
    assert post_state.unresolved_commitments == pre_state.unresolved_commitments  # or differs by exactly the deltas
```

Invariants to check:
- Character count never decreases unexpectedly
- Open commitments aren't silently closed
- Voice anchors aren't lost
- Facts aren't dropped (retirement is explicit, not silent)
- Embeddings exist for every post, scene summary, fact
- Delta log is contiguous (no missing turn IDs)

Snapshots are stored as anonymized SQLite dumps in `tests/fixtures/campaigns/`. Real campaign data, names redacted via configurable filters.

## L5 — User scenario tests

End-to-end scenarios driven from the Frontend API surface. Slower, run pre-release rather than in tight CI loops. Examples:

- "Create a campaign, import a SillyTavern character, run 5 turns, export to EPUB, verify EPUB validates"
- "Open campaign with 100 characters, navigate timeline, jump to scene 23, run a turn"
- "Fork at turn 47, run 3 turns on the fork, switch back to main, run 3 turns, verify isolation"
- "Advance time 1 week with a household of 8 NPCs, verify all significant NPCs ticked and digest is coherent"

Driven via the HTTP API; LLM is record/replay.

## Determinism guarantees

To make tests reproducible:

| Component | Determinism |
|---|---|
| Mechanics rolls | Seeded RNG; same seed = same result |
| State Store | Deterministic by inputs |
| Context Builder | Deterministic given inputs (tier promotion, retrieval results) |
| Extractor (rule-based) | Deterministic |
| Extractor (LLM-based) | Non-deterministic; tested via mock or replay |
| LLM Gateway | Non-deterministic in real mode; deterministic in replay mode |
| ImageGen | Backend-dependent; record/replay where needed |
| Time Engine | Deterministic except for LLM-driven NPC ticks |
| Setting weather | Deterministic given seed + location + time |

Tests requiring non-deterministic components mock them.

## Test fixtures

Fixtures organized by what they're testing:

```
tests/fixtures/
  campaigns/                  # frozen campaign snapshots
    wod_london_session_47.sqlite
    opt_cohen_day_52.sqlite
    minimal_test_campaign.sqlite
  llm/                        # recorded LLM responses
    by_hash/
      sha256_abc...json
  characters/                 # standalone character cards
    alistair-hyde-smythe.json
    winifred.json
  sillytavern_cards/          # for import tests
    sample_v2.png
    sample_v3.charx
  plugins/                    # toy plugins for conformance tests
    test-mechanics/
    test-llm-provider/
  scenes/                     # standalone scenes
    library_dialogue.json
```

Fixtures are version-controlled. Updates to fixtures (e.g., when re-recording LLM fixtures against a new model version) are explicit commits.

## Performance regression tests

A small suite of perf-critical operations is benchmarked in CI:

- Turn submission latency (mock LLM): expect < 50ms overhead
- Context Builder build duration for a 100-character campaign: expect < 200ms
- State Store vector search over 10k embeddings: expect < 100ms
- Frozen-campaign load + 1 turn: expect < 2s
- Plugin discovery + load for 10 plugins: expect < 500ms

Regressions beyond a configurable threshold (default: 20%) fail CI.

## CI pipeline

```
1. lint                      → fast fail on style issues
2. unit tests                → all modules in parallel
3. plugin conformance        → every bundled plugin
4. integration tests         → with mocked LLM
5. frozen campaign tests     → all fixtures, with mocked LLM
6. performance regression    → benchmark suite
7. (manual) scenario tests   → pre-release, record-mode against real LLM
```

Test runtime budget: full pipeline (steps 1–6) under 5 minutes.

## Configuration

```yaml
testing:
  llm_mode: replay              # replay, mock, real (real for fixture recording only)
  fixture_directory: ./tests/fixtures
  fail_on_missing_fixture: true
  frozen_campaign_invariants:
    enforce_strict: true
  performance:
    regression_threshold_percent: 20
    benchmark_iterations: 5
  conformance:
    run_on_plugin_load: true
    run_in_ci: true
```

## Open questions

- **Real-LLM smoke tests.** A small set of tests that hit the real API (with budget caps) to catch fixture rot. Daily or weekly, not per-commit.
- **Mutation testing.** Worth running against the state-store and extractor modules — high-value, well-tested code. Out of v1 default; opt-in.
- **Property-based testing.** For State Store delta application, contradiction detection, fork semantics. Hypothesis (Python) or fast-check (TS). High-value; recommended for those specific modules.
- **Snapshot test maintenance.** Frozen campaigns will drift from the working schema over time. Strategy: maintain migration scripts for fixtures, run migrations as part of fixture loading.
- **LLM fixture privacy.** Recorded fixtures contain campaign prose. Anonymize before committing? Default: yes, with a small per-test override for fixtures that need specific names.
- **Plugin conformance for user plugins.** Should conformance run on every load, or only at install? Performance vs. safety trade-off. Default: install only, with a dev-mode flag to re-check on every load.
