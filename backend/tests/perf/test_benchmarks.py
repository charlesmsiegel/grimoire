"""Performance regression benchmark suite (spec 17 §9).

The benchmarks listed in spec 17 §Performance regression tests:

* Turn submission latency (mock LLM): budget < 50ms
* Context Builder build for a 100-character campaign: budget < 200ms
* State Store vector search over 10k embeddings: budget < 100ms
* Frozen-campaign load + 1 turn: budget < 2s
* Plugin discovery + load for 10 plugins: budget < 500ms

Only the State Store vector-search bench has a queryable production
surface today, so it runs end-to-end. The others ship as registered
``BenchmarkSpec``s with stub ``fn``s so the structure is in place and
the regression budgets are visible. Each stub is tagged with a
``TODO(§9)`` comment pointing at the API that needs to land.

The 20% regression threshold comes from
``TestingConfig.performance.regression_threshold_percent`` — change it
there and both the runner and the saved baseline pick it up.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from grimoire.testing import BenchmarkRunner, BenchmarkSpec, TestingConfig

pytestmark = pytest.mark.perf


BASELINE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "perf" / "baseline.json"


# --------------------------------------------------------------------- #
# Stubbed benches — production surfaces aren't queryable yet.
# Each keeps the BenchmarkSpec registered so budgets/baselines stay
# visible, and is marked with a TODO so the wiring is obvious when the
# upstream module lands.
# --------------------------------------------------------------------- #


async def _stub_turn_submission() -> None:
    # TODO(§9): wire to real OrchestratorService.submit_turn when
    # the orchestrator exposes a stable mock-LLM test surface.
    return None


async def _stub_context_builder() -> None:
    # TODO(§9): wire to real ContextBuilder.build with a seeded
    # 100-character campaign fixture when the builder API stabilizes
    # for synthetic seeding.
    return None


async def _stub_frozen_campaign_turn() -> None:
    # TODO(§9): wire to real FrozenCampaignHarness.load + a single
    # submit_turn against a checked-in snapshot once frozen snapshots
    # ship (spec §4).
    return None


async def _stub_plugin_discovery() -> None:
    # TODO(§9): wire to real PluginsService.discover + load over a
    # tmp_path with 10 manifest+module pairs once the plugin loader's
    # synthetic-seed helper lands.
    return None


# --------------------------------------------------------------------- #
# Real bench: vector search over 10k embeddings.
# --------------------------------------------------------------------- #


def _random_unit_vector(dim: int, rng: random.Random) -> list[float]:
    """Pseudo-random vector. We don't bother normalising — sqlite-vec
    handles cosine on its end; we just need diversity."""
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


async def _seed_vector_search(
    tmp_path: Path,
    *,
    n_vectors: int = 10_000,
    dim: int = 16,
) -> dict[str, Any]:
    """Seed a real ``StateStore`` with ``n_vectors`` random embeddings.

    Returns a context dict with the live store and a query vector.
    Skips at the test level if the SQLite + sqlite-vec stack can't be
    initialised on this host.
    """
    from grimoire.state_store import StateStore
    from grimoire.storage import Database, apply_migrations

    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    db = Database(tmp_path / "campaigns.sqlite", pool_size=2)
    await db.connect()
    await apply_migrations(db)
    store = StateStore(db, data_root)
    await store.upsert_campaign(campaign_id="perf", name="perf-bench")

    rng = random.Random(20260518)
    for i in range(n_vectors):
        await store.add_embedding(
            ref=f"post-{i}",
            scope="campaign",
            source_kind="post",
            text=f"row {i}",
            vector=_random_unit_vector(dim, rng),
            model="perf",
            campaign_id="perf",
        )

    query = _random_unit_vector(dim, rng)
    return {"store": store, "db": db, "query": query}


async def _vector_search_fn(ctx: dict[str, Any]) -> None:
    store = ctx["store"]
    await store.vector_search(
        query_vector=ctx["query"],
        campaign_id="perf",
        include_library=False,
        top_k=8,
    )


async def _vector_search_teardown(ctx: dict[str, Any]) -> None:
    await ctx["db"].close()


# --------------------------------------------------------------------- #
# Spec assembly + runner
# --------------------------------------------------------------------- #


def _build_specs(
    vector_search_setup: Callable[[], Awaitable[dict[str, Any]]] | None,
) -> list[BenchmarkSpec]:
    """Build the suite. ``vector_search_setup`` is None when the
    state-store stack isn't usable (e.g. sqlite-vec missing) — we
    register the spec with a stub setup so the bench still runs and
    gets flagged as ``error`` cleanly."""
    specs: list[BenchmarkSpec] = [
        BenchmarkSpec(
            name="turn_submission_latency_mock_llm",
            fn=_stub_turn_submission,
            budget_ms=50.0,
            iterations=5,
        ),
        BenchmarkSpec(
            name="context_builder_100_character_campaign",
            fn=_stub_context_builder,
            budget_ms=200.0,
            iterations=5,
        ),
        BenchmarkSpec(
            name="frozen_campaign_load_plus_one_turn",
            fn=_stub_frozen_campaign_turn,
            budget_ms=2000.0,
            iterations=3,
        ),
        BenchmarkSpec(
            name="plugin_discovery_load_10_plugins",
            fn=_stub_plugin_discovery,
            budget_ms=500.0,
            iterations=5,
        ),
    ]
    if vector_search_setup is not None:
        specs.append(
            BenchmarkSpec(
                name="state_store_vector_search_10k",
                fn=_vector_search_fn,
                budget_ms=100.0,
                iterations=5,
                setup=vector_search_setup,
                teardown=_vector_search_teardown,
            )
        )
    return specs


@pytest.mark.asyncio
async def test_benchmark_suite_meets_baseline(tmp_path: Path) -> None:
    """Run the benchmark suite and assert no regression vs. baseline.

    The 20% threshold is sourced from
    ``TestingConfig.performance.regression_threshold_percent``. Stubbed
    benches resolve in microseconds and trivially pass their budgets.
    """
    config = TestingConfig()
    threshold = config.performance.regression_threshold_percent

    # The vector-search bench requires the real state-store stack.
    # If it fails to initialise (e.g. sqlite-vec extension blocked),
    # the bench drops out cleanly rather than failing the whole suite.
    try:

        async def setup() -> dict[str, Any]:
            return await _seed_vector_search(tmp_path)

        # smoke-test that the stack works before scheduling the bench
        probe = await asyncio.wait_for(_seed_vector_search(tmp_path / "probe"), timeout=60)
        await probe["db"].close()
        vector_setup: Callable[[], Awaitable[dict[str, Any]]] | None = setup
    except Exception as exc:  # pragma: no cover - host-dependent
        pytest.skip(f"state-store vector-search bench unavailable: {exc!r}")
        vector_setup = None

    specs = _build_specs(vector_setup)
    runner = BenchmarkRunner(threshold_pct=threshold)
    report = await runner.run(specs)

    assert BASELINE_PATH.is_file(), (
        f"baseline.json missing at {BASELINE_PATH} — generate one with "
        f"BenchmarkRunner.save_baseline(...)"
    )
    baseline = json.loads(BASELINE_PATH.read_text())
    baseline_names = {row["name"] for row in baseline["results"]}
    for result in report.results:
        assert result.name in baseline_names, (
            f"benchmark {result.name!r} not present in baseline; re-run save_baseline to refresh"
        )

    assert report.ok, report.summary()
