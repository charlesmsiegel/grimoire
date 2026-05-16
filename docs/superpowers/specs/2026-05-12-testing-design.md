# Testing — Design (Shipped)

> Captures the testing strategy as actually built. The matching "remaining" spec at `2026-05-16-testing-remaining-design.md` covers everything from the original `specs/17-testing.md` that did **not** land in this work.

**Commit:** `206672b` — "Build testing infrastructure (task #28)" (followed by `28a4044`, `64d0761`, `dc64d02`)
**Module:** `backend/src/grimoire/testing/`
**Tests:** `backend/tests/testing/`
**CI:** `.github/workflows/ci.yml`
**Runner config:** `backend/pyproject.toml` `[tool.pytest.ini_options]`

## Purpose

Testing is a design constraint rather than a shipped feature. The `grimoire.testing` package — deliberately part of the application proper, not the test tree — provides the harnesses and mocks every other module's tests use. Per-module tests are the canonical unit boundary; on top of those, pytest marker layers (`conformance`, `integration`, `frozen_campaign`, `perf`) give CI a way to fan out the spec's five layers as discrete jobs.

## Test layout

Per-module unit tests live at `backend/tests/<module>/test_<concern>.py` with a co-located `conftest.py` of fakes:

```
backend/tests/
  __init__.py
  test_event_bus.py            # cross-cutting smoke tests at the root
  test_frontmatter.py
  test_hashing.py
  test_health.py
  test_setup.py
  test_slug.py
  test_storage.py
  test_types.py
  test_validation.py
  test_yaml_io.py
  api/        conftest.py + tests
  characters/ conftest.py + tests
  continuity/ conftest.py + tests
  context/    tests
  export/     conftest.py + tests
  extractor/  conftest.py + tests
  imagegen/   conftest.py + tests
  library/    conftest.py + tests
  llm_gateway/ conftest.py + tests
  mechanics/  conftest.py + tests
  observability/ conftest.py + tests
  orchestrator/ conftest.py + tests
  plugins/    conftest.py + tests
  scenes/     tests
  state_store/ conftest.py + tests
  templates/  tests
  testing/    tests (the harness itself)
  time_engine/ conftest.py + tests
  watcher/    conftest.py + tests
  world/      conftest.py + tests
  bundled_plugins/ conftest.py + tests
```

`conftest.py` files own per-module fakes (e.g. `backend/tests/orchestrator/conftest.py` ships `FakeDB`, `FakeStateStore`, `FakeContextBuilder`, `FakeGateway`, `FakeExtractor`, `WSCollector`, and the `scene_manager`/`event_bus`/`fake_*`/`ws` pytest fixtures) so individual tests stay short.

There is no `backend/tests/integration/` directory yet — integration scenarios from spec 17 §L3 live as gaps in the remaining doc.

## Runner configuration

`backend/pyproject.toml` declares the marker vocabulary the spec uses:

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"
pythonpath = ["src"]
markers = [
    "conformance: plugin conformance contract tests (spec 17 §L2)",
    "integration: cross-module integration tests (spec 17 §L3)",
    "frozen_campaign: regression tests over a frozen SQLite snapshot (spec 17 §L4)",
    "perf: performance regression benchmarks (spec 17 §Performance regression tests)",
]
```

`asyncio_mode = "auto"` removes the per-test `@pytest.mark.asyncio` boilerplate. `pythonpath = ["src"]` lets tests import `grimoire.*` without an editable install. Tests inside `backend/tests/testing/` set their layer at module scope, e.g. `pytestmark = pytest.mark.frozen_campaign` (`test_frozen.py:12`), `pytest.mark.conformance` (`test_conformance.py:34`), `pytest.mark.perf` (`test_benchmark.py:12`).

Dev dependencies: `pytest>=8.3`, `pytest-asyncio>=0.24`, `httpx>=0.27`, `ruff>=0.7`.

## CI pipeline

`.github/workflows/ci.yml` implements spec 17 §CI pipeline as sequential `needs:` jobs, all running under `uv`:

1. `backend-lint` — `uv run ruff check` then `uv run ruff format --check`.
2. `backend-unit` (needs lint) — `pytest -m "not conformance and not integration and not frozen_campaign and not perf"`.
3. `backend-conformance` (needs unit) — `pytest -m conformance`.
4. `backend-integration` (needs conformance) — `pytest -m integration`.
5. `backend-frozen-campaign` (needs integration) — `pytest -m frozen_campaign`.
6. `backend-perf` (needs frozen-campaign) — `pytest -m perf`.
7. `frontend` (parallel) — `pnpm lint` / `pnpm typecheck` / `pnpm build`.

Empty marker layers tolerate pytest exit code 5: `pytest -m <layer> || code=$?; [ "${code:-0}" = "5" ] && exit 0 || exit "${code:-0}"`. This keeps the pipeline green while later work fills each bucket.

The first `uv sync --frozen` is `continue-on-error: true` so a lockfile drift doesn't block CI — the second `uv sync --all-extras --dev` is the authoritative install.

## `grimoire.testing` — shipped surface

`backend/src/grimoire/testing/__init__.py` exports the harness API used by other modules' tests:

```python
from grimoire.testing import (
    TestApp, TestAppFixture,
    MockLLMGateway, MockEmbeddingProvider, QueueExhaustedError,
    RecordReplayLLM, ReplayMode, FixtureMissingError,
    ConformanceReport, ConformanceSuite,
    MechanicsConformance, LLMProviderConformance,
    EmbeddingProviderConformance, ImageGenBackendConformance,
    ExportAdapterConformance,
    FrozenCampaignHarness, InvariantSnapshot, InvariantReport,
    BenchmarkRunner, BenchmarkSpec, BenchmarkResult, RegressionReport,
    TestingConfig,
)
```

### `TestApp` — composable unit harness (`testing/app.py`)

Async context manager that wires the modules that exist today:

- in-process `EventBus`
- `Database` + `apply_migrations` on `<data_root>/grimoire.sqlite` (pool size 2)
- `StateStore`
- `MechanicsService` (defaults to `MechanicsConfig(root=data_root/"mechanics")`)
- `ContinuityService`
- `SceneManager`
- `MockLLMGateway` as `app.llm`

```python
async with TestApp(tmp_path) as app:
    app.llm.queue_response("primary", "She nods.")
    await app.scene_manager.start_scene(...)
```

`TestApp.with_fixtures(name_or_fixture, *, root, registry=None)` returns a builder context manager. `TestAppFixture(name, files_root=None, setup=None, overrides=...)` is a thin record: `files_root` gets `shutil.copytree`-merged into `data_root` on entry; `setup(app)` runs after the file copy and DB migrations. Both `TestApp` and `TestAppFixture` set `__test__ = False` so pytest doesn't try to collect them as test classes.

Library-+-campaign fixtures (spec 17 §Library + campaign fixtures) are not yet a distinct type — the `TestAppFixture(files_root=...)` path covers the file side, and the Python `setup` hook handles state seeding. A dedicated `LibraryCampaignFixture` is a gap (see remaining doc).

### `MockLLMGateway` (`testing/mock_llm.py`)

The default LLM for unit and integration tests. Per-task queues seeded with `queue_response(task, str | dict | list)`, `queue_stream(task, chunks)`, `queue_error(task, exc)`, `queue_embeddings(task, vectors)`. Strings pass through verbatim; structured payloads are `json.dumps`'d so consumers parse the same wire format as the real gateway.

- Empty queue → `QueueExhaustedError(AssertionError)` with the task name and served count.
- `assert_all_consumed()` raises if any queue still has items (catches over-queueing).
- Records every call into `llm_calls: list[_LLMCall]` and `embed_calls: list[_EmbedCall]` for assertions.
- Implements the full `LLMGateway` surface: `complete`, `stream`, `embed`, `list_llm_providers`, `list_embedding_providers`, `list_routes`/`set_route` (campaign-scoped), `estimate_tokens`, `estimate_cost`, `health_check`/`health_check_all`.
- Fake embeddings: `_fake_vector(text)` = `[base + i for i in range(self.embedding_dim)]` where `base = sum(ord(c)) % 100`. Deterministic, four-dim by default.

`MockEmbeddingProvider` is a standalone provider for conformance tests with the same byte-sum vector scheme.

### `RecordReplayLLM` (`testing/record_replay.py`)

Wraps a real gateway in one of three `ReplayMode`s:

- `RECORD` — delegate, then write `{request, response}` to `<fixture_dir>/llm/by_hash/<sha256>.json`.
- `REPLAY` — load by `request_hash(request)`; missing fixture raises `FixtureMissingError`.
- `PASSTHROUGH` — delegate, write nothing.

`request_hash` is `sha256(json.dumps({model, system, messages:[{role,content,name}], max_tokens, temperature, stop_sequences}, sort_keys=True))`. Streaming replay yields one delta + one terminal chunk; record-mode streaming drains the underlying stream while yielding through. Embeddings are cached separately under `<fixture_dir>/llm/embeddings/<sha256(text)>.json`. `RECORD`/`PASSTHROUGH` require `real_gateway`; constructing without it raises `ValueError`.

`__getattr__` delegates everything else (`list_routes`, `health_check`, etc.) to the wrapped gateway so a replay gateway is a drop-in.

### Plugin conformance suites (`testing/conformance/`)

`ConformanceReport(kind, target_id, passed, failed, skipped, duration_ms)` aggregates per-check outcomes; `ok` is `not failed`. The `run_check` helper isolates each check, catching `Exception` (→ `failed`) and the internal `_Skip` (→ `skipped`), so a single failure doesn't mask the rest of the suite. Suites match the `ConformanceSuite` protocol (`kind: str`, `async def run(adapter) -> ConformanceReport`).

| Suite | Checks |
|---|---|
| `MechanicsConformance` (kind `mechanics`) | `test_sheet_schema_valid_json_schema` (validates the returned schema via `validation.check_schema`), `test_validate_sheet_accepts_valid` (round-trips `initialize_sheet → validate_sheet`), `test_validate_sheet_rejects_invalid` (empty dict against a required schema), `test_evaluate_pre_roll_returns_list`, `test_resolve_roll_deterministic_with_seed` (two calls, same seed, same `model_dump`), `test_resolve_roll_within_legal_outcome_space`, `test_character_creation_steps_resolvable_in_order`, `test_time_tick_returns_valid_deltas` |
| `LLMProviderConformance` (kind `llm_provider`) | `test_complete_returns_completion_result`, `test_complete_handles_empty_response`, `test_complete_streaming_callback_invoked` (skips when `capabilities.streaming=False`), `test_tokenize_consistent_across_calls`, `test_retry_on_5xx` / `test_no_retry_on_4xx_other_than_429` / `test_429_respects_retry_after` (all require an opt-in `_inject_fault` hook; skipped otherwise), `test_cost_estimate_nonzero_for_paid_models` (computes the 1k/1k cost from `list_models()` and asserts `> 0`) |
| `EmbeddingProviderConformance` (kind `embedding_provider`) | `test_embed_returns_correct_vector_dimensions`, `test_embed_consistent_across_calls` (tolerates `<1e-6` component noise), `test_embed_empty_input_returns_empty` |
| `ImageGenBackendConformance` (kind `imagegen_backend`) | `test_generate_returns_image_bytes`, `test_generate_preserves_seed`, `test_generate_same_seed_same_image` (skips unless `adapter.deterministic_seed`), `test_list_samplers_returns_list` |
| `ExportAdapterConformance` (kind `export_adapter`) | `test_export_produces_nonempty_output`, `test_export_respects_scene_selection`, `test_export_respects_appendix_selection` (skips when `capabilities.supports_appendices=False`), `test_option_schema_is_dict` |

Suites are *available*, not yet *enforced at plugin load*: the `plugins/` module does not currently call them. Spec 17 §L2 "Conformance is checked at plugin load time (in dev mode) or in CI (always)" is partially covered — CI has the `backend-conformance` job, but it runs only the explicitly-marked tests in `backend/tests/testing/test_conformance.py`. Wiring into `PluginsService` is a remaining-doc item.

The complete-LLM suite from spec 17 §LLM Provider does not ship `test_complete_structured_validates_against_schema`; the equivalent for structured-output validation is in the Extractor's own tests.

### `FrozenCampaignHarness` (`testing/frozen.py`)

`async with FrozenCampaignHarness(snapshot_path, data_root)` copies the SQLite snapshot into `data_root/grimoire.sqlite` and runs `apply_migrations` so old fixtures migrate forward.

`snapshot() -> InvariantSnapshot` reads `character_count` (from `campaign_content_index WHERE kind = 'character'`), `scene_count`, `open_commitment_count` (`commitments WHERE status='OPEN'`), `fact_count`, `post_count`, `embedded_row_count`, and `max_turn_id`. `_scalar(...)` returns the default on any error so a snapshot from an early schema doesn't crash the harness.

`validate(before, after, *, scene_broke=False, resolved_commitments=0)` checks the spec's invariants and returns an `InvariantReport(violations, warnings)`:
- `character_count` may not decrease.
- `scene_count` >= `before.scene_count + (1 if scene_broke else 0)`.
- `open_commitment_count + resolved_commitments` >= `before.open_commitment_count`.
- `fact_count` decrease → warning (retirement should flip status, not delete).

Voice-anchor and delta-log-contiguity invariants from spec 17 are placeholders pending other modules landing. Snapshot anonymization (spec 17: "anonymized SQLite dumps") is mentioned in the module docstring but not implemented — see remaining doc.

### `BenchmarkRunner` (`testing/benchmark.py`)

Framework-agnostic perf runner. `BenchmarkSpec(name, fn, budget_ms, iterations=5, setup=None, teardown=None)` is one bench; `fn` can be sync or async (`_maybe_await` handles both); `setup`'s return value is passed positionally to `fn`. `BenchmarkRunner(threshold_pct=20.0).run([...])` collects samples, computes mean/median/stdev, and returns `RegressionReport`. A bench `regressed(threshold)` when `mean_ms > budget_ms * (1 + threshold/100)` or `error is not None`. `save_baseline(path, report)` / `load_baseline(path)` persist `{name: budget_ms}` JSON.

No spec-17 benchmarks (turn submission, context build, vector search, frozen-campaign load, plugin discovery) are wired into the runner yet — only the runner's own tests exercise it.

### `TestingConfig` (`testing/config.py`)

Dataclass shape matching spec 17 §Configuration:

```python
TestingConfig(
    llm_mode=LLMMode.MOCK,              # MOCK | REPLAY | REAL
    fixture_directory=Path("tests/fixtures"),
    fail_on_missing_fixture=True,
    frozen_campaign=FrozenCampaignConfig(enforce_strict=True),
    performance=PerformanceConfig(regression_threshold_percent=20.0,
                                  benchmark_iterations=5),
    conformance=ConformanceConfig(run_on_plugin_load=True, run_in_ci=True),
)
```

The dataclass exists for API stability; nothing in the runtime consumes it yet. Defaults match the spec.

## Test wiring patterns

Other modules' tests follow a consistent shape:

- `conftest.py` defines fakes that duck-type the real collaborators (e.g. `orchestrator/conftest.py`'s `FakeStateStore` exposes `apply_delta`, `queue_for_review`, `get_delta_log`, `reverse_delta`, `fork_branch` — all returning canned ids and recording calls).
- pytest fixtures (`@pytest.fixture`) wire the fakes into the per-test parameters.
- Tests assert on the fake's recorded call list rather than on side-effects of a real backing store.
- `tmp_path` provides isolated data roots; `EventBus` instances are per-test.
- WebSocket interactions use a small `WSCollector` callable that appends `(campaign_id, message)` to a list.

The harness package is import-only for tests; nothing under `backend/tests/` is referenced from the application runtime.
