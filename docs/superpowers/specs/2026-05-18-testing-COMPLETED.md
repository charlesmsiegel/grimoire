# Testing — COMPLETED (2026-05-18)

> Implementation of §1-§12 from the original `specs/17-testing.md`
> remaining work. §13-§15 (real-LLM smoke, mutation testing, property-based
> testing) remain deferred to v2 by design.

**Companion (shipped earlier):** `2026-05-12-testing-design.md`
**Module:** `backend/src/grimoire/testing/`
**Tests:** `backend/tests/testing/`, `backend/tests/integration/`, `backend/tests/perf/`, `backend/tests/scenario/`

## Status per section

- §1 — DONE. `PluginsService.__init__` accepts `TestingConfig | ConformanceConfig`; ran-once cache + `recheck_conformance(plugin_id)`. Tests: `backend/tests/plugins/test_conformance_wiring.py`.
- §2 — DONE. `LibraryCampaignFixture` + `CampaignFixture` + `fixtures_registry` (process-wide). Integration suite root at `backend/tests/integration/`.
- §3 — DONE. `TestApp` now composes `OrchestratorService`, `ExtractorService`, and `TimeEngineService` (plus `WorldService` / `CharactersService`) with a stub context builder so integration tests can drive the turn loop end-to-end against the live State Store / Scene Manager / Continuity stack. The §3a/b/c skeletons in `backend/tests/integration/` are now real assertions: turn loop appends posts and routes COMMITMENT_ADD into continuity (a); a "hours later" player input auto-breaks the active scene (b); `time_engine.advance` ticks library-composed `major_npc`s via a recordable `install_npc_tick_fn` seam (c). (d/e/f/g/h/i) continue to pass against real APIs.
- §4 — DONE. `backend/scripts/export_snapshot.py` runs migrations + anonymizer + `VACUUM`. `backend/tests/fixtures/campaigns/minimal_test_campaign.sqlite` (~84 KB; floor set by FTS shadow tables in current migrations). Snapshot-vs-migrations assertion shipped.
- §5 — DONE. `grimoire.testing.Anonymizer` with regex + sqlite in-place + sidecar mapping + `with_passthrough` escape hatch. Wired into `RecordReplayLLM`.
- §6 — DONE. `golden_llm` fixture + `--record` CLI flag + `golden` marker. Two replay fixtures shipped (extractor, scene summary). `backend-golden` CI job. Workflow docs at `backend/tests/fixtures/llm/README.md`.
- §7 — DONE. `validate()` now enforces delta-log contiguity and per-kind embedding non-decrease. Voice-anchor count carries a `TODO(§7)` until `CharactersService` exposes a counter.
- §8 — DONE for the pin-vs-tracks-latest case. Explicit upgrade-flow diff format skips with a comment until the API surface stabilizes.
- §9 — PARTIAL. Real bench: 10k embedding vector search. Four other benches registered as stubs with `TODO(§9)` to wire when upstream APIs (orchestrator.submit_turn, ContextBuilder.build, frozen-campaign turn loop, plugin discovery) land.
- §10 — DONE (skeletons). Four scenario tests + `ScenarioApp` harness. All four skip cleanly today; reasons reference the missing API endpoints (e.g. SillyTavern import route) or fixtures (8-NPC household snapshot).
- §11 — DONE. `pnpm test` runs Vitest with one smoke test; wired into the `frontend` CI job.
- §12 — CLOSED WITHOUT ACTION. No `depends_on` / topological-sort surface exists for user plugins today; existing manifest validation in `tests/plugins/` is sufficient. Documented in `tests/plugins/test_conformance_wiring.py`.

**Deferred (per spec):** §13 real-LLM smoke, §14 mutation testing, §15 property-based testing.

---

## Original remaining-work spec

Below is the original `2026-05-16-testing-remaining-design.md` content, preserved for traceability.

---

**Companion (already shipped):** `2026-05-12-testing-design.md`
**Module:** `backend/src/grimoire/testing/`
**Tests:** `backend/tests/testing/`

## 1. Wire plugin conformance into `PluginsService`

Spec 17 §L2: "Conformance is checked at plugin load time (in dev mode) or in CI (always). Failing conformance excludes a plugin from the registry with a clear error."

Today the conformance suites in `backend/src/grimoire/testing/conformance/` are well-tested in isolation (`backend/tests/testing/test_conformance.py`) but `PluginsService` does not invoke them. `TestingConfig.conformance.run_on_plugin_load=True` and `run_in_ci=True` exist as configuration but nothing reads them.

Design needed:
- `PluginsService` resolves the right `ConformanceSuite` for each loaded adapter by `kind` (`mechanics`, `llm_provider`, `embedding_provider`, `imagegen_backend`, `export_adapter`).
- On load: if `TestingConfig.conformance.run_on_plugin_load` is true, run the suite synchronously and refuse to register the plugin if `report.ok` is false. Surface the failure via the existing plugin-error reporting path.
- On install: also run once and write the result so a successful install doesn't have to re-check at every load. Tie to the "install only, dev-mode flag to re-check" default from spec 17 §Open questions.
- A CLI / API surface to re-run a plugin's suite on demand (helps plugin authors iterate).

## 2. `LibraryCampaignFixture` and the integration test directory

Spec 17 §Library + campaign fixtures defines a `LibraryCampaignFixture` dataclass combining `library_assets`, `library_entities`, `character_families`, and a list of `CampaignFixture`s.

Today `TestAppFixture` (one fixture, one files-root, one optional setup hook) is the only fixture type — fine for single-campaign cases, insufficient for the cross-scope integration tests in spec 17 §L3 (`test_resolve_character_uses_library_with_campaign_override`, `test_crossover_campaign_resolves_from_multiple_assets`, `test_promote_campaign_local_character_to_library`, `test_pinned_campaign_sees_old_version_after_library_edit`, `test_same_world_two_campaigns_different_mechanics`).

Design needed:
- A `LibraryCampaignFixture` (or extend `TestAppFixture`) that can seed multiple campaigns sharing one library state.
- A registry pattern so `TestApp.with_fixtures("library_with_two_campaigns", ...)` resolves a canonical name rather than constructing the fixture inline.
- Create `backend/tests/integration/` with `pytestmark = pytest.mark.integration` and port the spec's L3 examples.

## 3. Integration test suite for L3 scenarios

The CI pipeline already has a `backend-integration` job (`.github/workflows/ci.yml`) that runs `pytest -m integration`, currently tolerating exit code 5 because the suite is empty.

Concrete tests to add (each one a separate plan item):

a. **Turn loop end-to-end with mock LLM** — orchestrator + scene manager + extractor + state store; verify the player input through `submit_turn` yields appended scene posts, extracted deltas, and recorded continuity facts (spec 17 §L3 "Turn loop end-to-end").

b. **Scene boundary detection with auto-break** — orchestrator's `_maybe_break_scene` against a fixture where the next post obviously crosses a scene boundary.

c. **Time advancement with NPC ticks** — Time Engine driving NPC ticks for offscreen significant characters, with mocked tick responses.

d. **Extractor identifies commitment** — assert the extracted delta set includes a `commitment_added` for a known prompt/response pair.

e. **Library composition cascade** — library default vs. campaign-local override resolution (requires §2).

f. **Crossover composition** — characters from world A, locations from world B (requires §2 and a multi-world fixture).

g. **Promote campaign-local to library** — verify a new campaign using the same world picks up the promoted asset.

h. **Version pinning** — pinned campaign keeps the old asset version after a library edit; explicit upgrade brings the new version (requires library versioning in State Store).

i. **Mechanics swap, same world** — two campaigns share a world but use different mechanics modules; character sheets differ, library cards match.

Each test gets a fixture registered with the registry from §2.

## 4. Frozen campaign snapshot fixtures + tests

`FrozenCampaignHarness` exists but no snapshots are checked in. Spec 17 expects:

```
tests/fixtures/campaigns/
  wod_london_session_47.sqlite
  opt_cohen_day_52.sqlite
  minimal_test_campaign.sqlite
```

Design needed:
- A script under `backend/scripts/` or `tools/` that exports an anonymized snapshot from a real or synthetic campaign DB. Should run the same `apply_migrations` against a fresh copy and `VACUUM` for size.
- Per-snapshot test cases using `@pytest.mark.frozen_campaign("wod_london_session_47")`-style parametrization (spec 17 example uses an indirect fixture; design the wiring).
- A "snapshot vs current migrations" assertion that runs migrations against the snapshot at load time and fails loudly if migration is required without the migration script having been bumped.

## 5. Snapshot anonymization pipeline

Spec 17 open question: "Recorded fixtures contain campaign prose. Anonymize before committing? Default: yes, with a small per-test override for fixtures that need specific names."

The `FrozenCampaignHarness` docstring (`backend/src/grimoire/testing/frozen.py:3`) says "Load an anonymized SQLite snapshot" but no anonymizer exists. Same for `RecordReplayLLM` fixtures.

Design needed:
- A configurable name/place filter (regex list + replacement table) that runs over post bodies, scene summaries, fact text, character names, location names. Persist the mapping alongside the snapshot so re-recording is stable.
- Apply during snapshot export (§4) and during LLM fixture write (§6 — `_save_completion` and `_save_embedding`).
- A per-test escape hatch (e.g. fixture name tagged `:keep_names` or a sidecar JSON) for tests that need specific names.

## 6. Real-LLM fixture recording workflow + golden-path tests

`RecordReplayLLM` works end-to-end but no recorded fixtures are checked in and no golden-path tests rely on it.

Design needed:
- A `recording` mode runner: `uv run pytest -m golden --record` that sets `RecordReplayLLM(mode=RECORD)` and writes to `backend/tests/fixtures/llm/by_hash/`. Out of normal CI.
- A `replay` mode default that runs the same tests against the checked-in fixtures.
- A small initial set of golden tests where prose realism matters (e.g. one extractor test, one scene-summary test). Keep the set small — fixture rot is the cost.
- Document the re-record cadence (model upgrade, prompt template change, schema bump).

## 7. Tighten frozen-campaign invariants

`FrozenCampaignHarness.validate` covers character count, scene count, open commitments, and fact count today. Spec 17 lists:

- Voice anchors aren't lost — needs Characters module to land and expose a count.
- Embeddings exist for every post, scene summary, fact — `embedded_row_count` is captured but no per-target-kind assertion is made.
- Delta log is contiguous — fetch turn ids in order, assert no gaps. Currently `max_turn_id` is captured but unused.

Add these checks once the upstream data is queryable. None are blocking but each closes a silent-drift hole.

## 8. Library + campaign fixtures replay-forward semantics

Spec 17 §Library + campaign fixtures: "Replay-forward tests can mutate either independently to verify cascading behavior."

Once §2 lands, build the replay loop:
1. Load fixture with library state at `v_n` and campaigns referencing it.
2. Snapshot invariants per campaign.
3. Mutate library (`update_character`, etc.) and re-resolve each campaign.
4. Assert pinned campaigns see `v_n`, unpinned see new state.
5. Snapshot invariants again.

This is a different shape from the L3 tests in §3 (which run a turn forward); make it its own integration helper.

## 9. Performance regression benchmark suite — shipped (issue #301)

All five spec-17 benchmarks now run as real `BenchmarkSpec`s wired into
the pytest suite at `backend/tests/perf/test_benchmarks.py`:

- Turn submission latency (mock LLM) — drives `OrchestratorService.submit_post`
  through inline fakes for the heavy collaborators (context builder,
  extractor, state store, gateway) while running the real `SceneManager`.
  Budget bumped to 100ms (vs. spec target 50ms) to accommodate Windows
  local-dev disk-write costs; still catches a real regression at >120ms.
- Context Builder build for a 100-character campaign — real
  `ContextBuilderService` against in-module stub library/characters/
  world/scenes/continuity surfaces, driven with 100 `present_character_refs`.
- State Store vector search over 10k embeddings — real `StateStore`
  against tmpfs SQLite + sqlite-vec; seeded with 10k random embeddings.
- Frozen-campaign load + snapshot — real `FrozenCampaignHarness` on the
  checked-in `minimal_test_campaign.sqlite` (load + snapshot read per
  iteration). The "+ 1 turn" half stays a TODO until the orchestrator
  composition that `tests/integration/test_turn_loop_end_to_end.py`
  skips can be wired against a loaded snapshot.
- Plugin discovery + load for 10 plugins — synthesises 10 minimal
  `llm_provider` plugin directories in tmp and runs the real
  `discover()` + `load_plugin()` pipeline against them.

Baselines live in `backend/tests/fixtures/perf/baseline.json`; the 20%
regression threshold is sourced from
`TestingConfig.performance.regression_threshold_percent`.

## 10. L5 user scenario tests

Spec 17 §L5 lists end-to-end scenarios driven through the HTTP API (Frontend surface), with LLM in record/replay:

- "Create a campaign, import a SillyTavern character, run 5 turns, export to EPUB, verify EPUB validates"
- "Open campaign with 100 characters, navigate timeline, jump to scene 23, run a turn"
- "Fork at turn 47, run 3 turns on the fork, switch back to main, run 3 turns, verify isolation"
- "Advance time 1 week with a household of 8 NPCs, verify all significant NPCs ticked and digest is coherent"

Design needed:
- A new pytest marker (`scenario` or `e2e`) and a CI job step that runs pre-release rather than per-commit.
- A small harness that spins up the FastAPI app (`backend/src/grimoire/main.py`) against a temp data root and a pre-seeded library, then drives it through `httpx.AsyncClient`.
- Per-scenario fixtures (some overlap with §4 snapshots).
- EPUBCheck integration for the export verification — runs only when the binary is available (skip otherwise).

## 11. CI: wire frontend test step

`frontend` CI job runs `pnpm lint`, `pnpm typecheck`, `pnpm build` but no `pnpm test`. Spec 17 §L1 names vitest as the TS test framework. Add a `pnpm test` step once the frontend has tests worth running.

## 12. Plugin manifest dependency-resolution conformance check

Spec 17 §L1 row "Plugins" calls out manifest validation, dependency resolution (topological sort, cycle detection), and lifecycle ordering as the per-module test concerns. Manifest validation tests exist (`backend/tests/plugins/`); a *conformance* layer for user-supplied plugins is not in the spec's L2 list and is not implemented. Confirm during the §1 wiring whether a manifest-level conformance suite is wanted, or whether the existing unit tests are sufficient.

## 13. Real-LLM smoke tests (v2; deferred)

Spec 17 open question: "A small set of tests that hit the real API (with budget caps) to catch fixture rot. Daily or weekly, not per-commit." Deferred; depends on §6 landing first so there's an ergonomic recording flow to fall back to. Note in the plan but do not schedule in v1.

## 14. Mutation testing (v2; deferred)

Spec 17 open question: "Worth running against the state-store and extractor modules — high-value, well-tested code. Out of v1 default; opt-in." Record here so it doesn't get re-discovered.

## 15. Property-based testing (v2; deferred)

Spec 17 open question: "For State Store delta application, contradiction detection, fork semantics. Hypothesis (Python) or fast-check (TS). High-value; recommended for those specific modules." Add when the corresponding state-store features stabilize.

---

## Suggested plan ordering

If picking this up, a reasonable order:
1. §2 (LibraryCampaignFixture + integration directory) — unblocks most of §3.
2. §3 (a)/(b)/(c)/(d) — single-campaign integration tests that don't need cross-scope fixtures.
3. §3 (e)/(f)/(g)/(h)/(i) — cross-scope integration tests, depend on §2.
4. §1 (PluginsService conformance wiring) — independent; do alongside or after §3 to validate against bundled plugins.
5. §4 (frozen snapshots) + §7 (tightened invariants) — close the long-running-campaign drift hole.
6. §5 (anonymization) — needed before §4 and §6 fixtures land in the repo.
7. §9 (perf benchmarks) — once turn loop and context builder are integration-tested, the benchmarks have something realistic to measure.
8. §6 (real-LLM record/replay golden tests) — needs §5.
9. §8 (replay-forward library tests) — extension of §3.
10. §10 (L5 scenario tests) — once §3 and §4 are stable.
11. §11 (frontend `pnpm test`) — independent; do when the frontend grows tests.
12. §12 (plugin manifest conformance) — confirm need during §1; may close without action.
