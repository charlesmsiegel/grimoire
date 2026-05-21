"""Tests for plugin-conformance wiring inside ``PluginsService``.

Covers spec ``docs/superpowers/specs/2026-05-18-testing-design.md`` §1: the
service now runs the appropriate :class:`ConformanceSuite` for every
registered instance kind at install time, refuses to register plugins whose
suites fail, caches the outcome so subsequent rescans don't re-pay the cost,
and exposes ``recheck_conformance`` for plugin authors.

§12 — *Manifest dependency-resolution conformance suite* — is closed without
action. The L1 row in spec 17 calls out "Manifest validation, dependency
resolution (topological sort, cycle detection), lifecycle ordering" for the
Plugins module, but today's manifest (see
``grimoire/validation/manifests.py``) has no inter-plugin dependency field and
``grimoire/plugins/loader.py`` has no topological sort or cycle detector to
exercise — there is nothing for a conformance suite to assert against.
Manifest *validation* is already covered by the loader tests in
``test_loader.py::test_load_plugin_reports_invalid_manifest`` and the schema
unit tests in ``tests/validation/test_manifests.py``. When/if a real
dependency-resolution feature lands in the Plugins module, add a
``ManifestConformance`` suite under
``grimoire/testing/conformance/manifest.py`` and wire it in alongside the
per-kind suites here.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from grimoire.plugins.config import ConfigStoreConfig, PluginsConfig
from grimoire.plugins.config_store import InMemoryKeyring
from grimoire.plugins.service import PluginsService
from grimoire.testing.config import ConformanceConfig, TestingConfig
from grimoire.types.plugins import PluginLifecycle

from .conftest import write_plugin

# A plugin.py body that passes the LLM provider conformance suite: it returns
# valid ``CompletionResponse`` objects, yields stream chunks, declares a paid
# model with non-zero cost, and a stable token estimator.
PASSING_LLM_PY = textwrap.dedent(
    """
    from grimoire.types.common import HealthLevel, HealthStatus
    from grimoire.types.llm import (
        CompletionChunk,
        CompletionResponse,
        ModelInfo,
        ProviderCapabilities,
        TokenUsage,
    )


    class Provider:
        def __init__(self, config=None):
            self.config = config or {}
            self.id = "alpha"
            self.name = "Alpha"
            self.capabilities = ProviderCapabilities(streaming=True)

        async def complete(self, request):
            return CompletionResponse(
                text="ok",
                model=request.model,
                finish_reason="stop",
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )

        async def stream(self, request):
            yield CompletionChunk(delta="o", is_final=False)
            yield CompletionChunk(
                delta="k",
                is_final=True,
                usage=TokenUsage(input_tokens=1, output_tokens=1),
            )

        async def list_models(self):
            return [
                ModelInfo(
                    id="m1",
                    name="M1",
                    input_cost_per_1k=0.001,
                    output_cost_per_1k=0.002,
                ),
            ]

        async def estimate_tokens(self, text):
            return max(1, len(text) // 4)

        async def health_check(self):
            return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
    """
).strip()


# A plugin.py body that satisfies the LLM protocol member check but trips
# the conformance suite: ``complete`` returns ``None`` (no ``text`` field).
FAILING_LLM_PY = textwrap.dedent(
    """
    from grimoire.types.common import HealthLevel, HealthStatus
    from grimoire.types.llm import CompletionChunk, ProviderCapabilities


    class Provider:
        def __init__(self, config=None):
            self.config = config or {}
            self.id = "bad"
            self.name = "Bad"
            self.capabilities = ProviderCapabilities(streaming=True)

        async def complete(self, request):
            return None  # fails test_complete_returns_completion_result

        async def stream(self, request):
            yield CompletionChunk(delta="x", is_final=True)

        async def list_models(self):
            return []

        async def estimate_tokens(self, text):
            return 1

        async def health_check(self):
            return HealthStatus(level=HealthLevel.HEALTHY, target_id=self.id)
    """
).strip()


def _service(
    plugins_root: Path,
    config_root: Path,
    *,
    testing_config: TestingConfig | ConformanceConfig | None = None,
) -> PluginsService:
    cfg = PluginsConfig(
        root=plugins_root,
        config_store=ConfigStoreConfig(root=config_root),
    )
    return PluginsService(
        cfg,
        keyring_backend=InMemoryKeyring(),
        testing_config=testing_config,
    )


# --- passing plugins ------------------------------------------------- #


async def test_conformance_default_is_disabled(plugins_root: Path, config_root: Path) -> None:
    """No ``testing_config`` → conformance never runs (back-compat)."""
    write_plugin(plugins_root, "alpha", plugin_py=FAILING_LLM_PY)
    svc = _service(plugins_root, config_root)
    report = await svc.rescan()
    assert "alpha" in report.loaded
    assert svc.get_llm_provider("alpha") is not None


async def test_passing_plugin_loads_with_conformance_enabled(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha", plugin_py=PASSING_LLM_PY)
    svc = _service(plugins_root, config_root, testing_config=TestingConfig())
    report = await svc.rescan()

    assert "alpha" in report.loaded
    assert report.failed == []
    assert svc.get_llm_provider("alpha") is not None
    reports = svc.conformance_reports("alpha")
    assert "llm_provider" in reports
    assert reports["llm_provider"].ok


# --- failing plugins ------------------------------------------------- #


async def test_failing_plugin_goes_to_failed_map(plugins_root: Path, config_root: Path) -> None:
    write_plugin(plugins_root, "bad", plugin_py=FAILING_LLM_PY)
    svc = _service(plugins_root, config_root, testing_config=TestingConfig())
    report = await svc.rescan()

    assert "bad" not in report.loaded
    failed_ids = [pid for pid, _ in report.failed]
    assert "bad" in failed_ids
    reason = next(msg for pid, msg in report.failed if pid == "bad")
    assert "conformance" in reason
    assert "llm_provider" in reason
    # Failed plugins are unregistered from the per-kind registries.
    assert svc.get_llm_provider("bad") is None
    assert "bad" in svc.failed_plugins()
    status = await svc.get_status("bad")
    assert status.lifecycle == PluginLifecycle.FAILED
    assert status.error and "conformance" in status.error


async def test_one_failing_plugin_does_not_block_a_passing_one(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "good", plugin_py=PASSING_LLM_PY)
    write_plugin(plugins_root, "bad", plugin_py=FAILING_LLM_PY)
    svc = _service(plugins_root, config_root, testing_config=TestingConfig())
    report = await svc.rescan()

    assert report.loaded == ["good"]
    failed_ids = [pid for pid, _ in report.failed]
    assert failed_ids == ["bad"]


# --- recheck_conformance --------------------------------------------- #


async def test_recheck_conformance_returns_fresh_reports(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha", plugin_py=PASSING_LLM_PY)
    svc = _service(plugins_root, config_root, testing_config=TestingConfig())
    await svc.rescan()

    reports = await svc.recheck_conformance("alpha")
    assert "llm_provider" in reports
    assert reports["llm_provider"].ok
    assert "alpha" not in svc.failed_plugins()


async def test_recheck_conformance_moves_to_failed_on_failure(
    plugins_root: Path, config_root: Path
) -> None:
    # Install with conformance off so the failing plugin is registered.
    write_plugin(plugins_root, "alpha", plugin_py=FAILING_LLM_PY)
    svc = _service(plugins_root, config_root)
    await svc.rescan()
    assert svc.get_llm_provider("alpha") is not None

    # Now turn conformance on by hand and re-check the loaded instance.
    svc._conformance_config = ConformanceConfig()  # type: ignore[attr-defined]
    reports = await svc.recheck_conformance("alpha")
    assert "llm_provider" in reports
    assert not reports["llm_provider"].ok
    # Should have been unregistered from the registry and moved to _failed.
    assert svc.get_llm_provider("alpha") is None
    assert "alpha" in svc.failed_plugins()


async def test_recheck_conformance_unknown_plugin_raises(
    plugins_root: Path, config_root: Path
) -> None:
    svc = _service(plugins_root, config_root, testing_config=TestingConfig())
    with pytest.raises(KeyError):
        await svc.recheck_conformance("nope")


# --- caching / opt-out ---------------------------------------------- #


async def test_default_config_skips_rerun_on_subsequent_rescan(
    plugins_root: Path, config_root: Path
) -> None:
    """Default ``run_on_install=True, run_on_plugin_load=False`` runs once."""
    write_plugin(plugins_root, "alpha", plugin_py=PASSING_LLM_PY)
    svc = _service(plugins_root, config_root, testing_config=TestingConfig())
    await svc.rescan()
    reports_v1 = svc.conformance_reports("alpha")
    assert "llm_provider" in reports_v1

    # Subsequent rescan must not re-run conformance — we detect that by
    # swapping the cached suite for one that raises on call.
    from grimoire.plugins import service as service_module

    sentinel_called = {"called": False}

    class _Boom:
        kind = "llm_provider"

        async def run(self, _adapter):
            sentinel_called["called"] = True
            raise AssertionError("conformance should not re-run on rescan")

    saved = service_module._CONFORMANCE_SUITES["llm_provider"]
    service_module._CONFORMANCE_SUITES["llm_provider"] = _Boom()
    try:
        report = await svc.rescan()
    finally:
        service_module._CONFORMANCE_SUITES["llm_provider"] = saved
    assert "alpha" in report.loaded
    assert sentinel_called["called"] is False


async def test_run_on_plugin_load_reruns_every_rescan(
    plugins_root: Path, config_root: Path
) -> None:
    write_plugin(plugins_root, "alpha", plugin_py=PASSING_LLM_PY)
    svc = _service(
        plugins_root,
        config_root,
        testing_config=ConformanceConfig(run_on_install=True, run_on_plugin_load=True),
    )
    await svc.rescan()

    from grimoire.plugins import service as service_module

    calls = {"n": 0}
    saved = service_module._CONFORMANCE_SUITES["llm_provider"]

    class _Counter:
        kind = "llm_provider"

        async def run(self, adapter):
            calls["n"] += 1
            return await saved.run(adapter)

    service_module._CONFORMANCE_SUITES["llm_provider"] = _Counter()
    try:
        await svc.rescan()
    finally:
        service_module._CONFORMANCE_SUITES["llm_provider"] = saved
    assert calls["n"] == 1


async def test_conformance_disabled_skips_for_failing_plugin(
    plugins_root: Path, config_root: Path
) -> None:
    """``run_on_install=False, run_on_plugin_load=False`` is a hard opt-out."""
    write_plugin(plugins_root, "bad", plugin_py=FAILING_LLM_PY)
    svc = _service(
        plugins_root,
        config_root,
        testing_config=ConformanceConfig(run_on_install=False, run_on_plugin_load=False),
    )
    report = await svc.rescan()
    assert "bad" in report.loaded
    assert svc.get_llm_provider("bad") is not None


# --- §12 closure tripwire ------------------------------------------- #
#
# Spec 17 §L1 "Plugins" mentions dependency resolution (topological sort,
# cycle detection) and lifecycle ordering as test concerns. §12 of the
# testing spec was closed without action because no inter-plugin
# dependency surface exists today, so there's nothing for a
# ``ManifestConformance`` suite to assert against. The two guards below
# fail the build if that premise changes, prompting the dev who adds the
# feature to also wire the conformance suite.


def test_plugin_manifest_schema_has_no_dependency_surface() -> None:
    """If a ``depends_on``/``requires_plugin`` field shows up, write the suite.

    See module docstring §12 — when this fails, add
    ``grimoire/testing/conformance/manifest.py`` (topological sort + cycle
    detection) and register it alongside the per-kind suites in
    ``grimoire.plugins.service._CONFORMANCE_SUITES``.
    """
    from grimoire.validation.manifests import PLUGIN_MANIFEST_SCHEMA

    properties = set(PLUGIN_MANIFEST_SCHEMA.get("properties", {}).keys())
    forbidden = {"depends_on", "requires_plugin", "plugin_dependencies"}
    leaked = properties & forbidden
    assert not leaked, (
        f"manifest schema added inter-plugin dependency field(s) {sorted(leaked)}; "
        "wire a ManifestConformance suite per the §12 note in this module."
    )


def test_plugin_loader_has_no_topological_sort() -> None:
    """If the loader gains a topological sort, write the suite.

    See module docstring §12. The loader currently iterates discovered
    plugins independently; once it sorts by declared deps, the
    conformance suite gains real invariants to check (acyclicity,
    activation order, missing-dependency errors).
    """
    from grimoire.plugins import loader

    public = {name for name in dir(loader) if not name.startswith("_")}
    forbidden = {"topological_sort", "resolve_dependencies", "order_by_deps"}
    leaked = public & forbidden
    assert not leaked, (
        f"loader exposes dependency-resolution symbol(s) {sorted(leaked)}; "
        "wire a ManifestConformance suite per the §12 note in this module."
    )
