"""Testing infrastructure (spec 17).

This package is part of the application proper rather than the test tree
so that other tools — frontend, CLI, plugin authors — can exercise the
same harnesses. Tests import what they need:

* :class:`MockLLMGateway` — drop-in fake gateway with per-task response
  queues that fail loudly when exhausted. The default for unit and
  integration tests that don't care about prose realism.
* :class:`RecordReplayLLM` — record real LLM calls to JSON fixtures and
  later replay them deterministically. Use this for golden-path tests
  where the prose matters.
* :class:`ConformanceReport` and the per-kind suites in
  :mod:`grimoire.testing.conformance` — protocol contract checks for
  every plugin the system loads.
* :class:`TestApp` — composes the modules that are buildable today into a
  ready-to-use harness with mock LLM, fixture loading, and snapshot
  helpers.
* :class:`FrozenCampaignHarness` — loads an anonymized SQLite snapshot
  of a campaign and provides invariant checks for regression tests.
* :class:`BenchmarkRunner` — perf regression suite with a configurable
  threshold (default 20%).
"""

from grimoire.testing.anonymizer import Anonymizer, sidecar_path
from grimoire.testing.app import TestApp, TestAppFixture
from grimoire.testing.benchmark import (
    BenchmarkResult,
    BenchmarkRunner,
    BenchmarkSpec,
    RegressionReport,
)
from grimoire.testing.config import TestingConfig
from grimoire.testing.conformance import (
    ConformanceReport,
    ConformanceSuite,
    EmbeddingProviderConformance,
    ExportAdapterConformance,
    ImageGenBackendConformance,
    LLMProviderConformance,
    MechanicsConformance,
)
from grimoire.testing.frozen import (
    FrozenCampaignHarness,
    InvariantReport,
    InvariantSnapshot,
    SnapshotStaleError,
)
from grimoire.testing.mock_llm import (
    MockEmbeddingProvider,
    MockLLMGateway,
    QueueExhaustedError,
)
from grimoire.testing.record_replay import (
    FixtureMissingError,
    RecordReplayLLM,
    ReplayMode,
)
from grimoire.testing.scenario import ScenarioApp

__all__ = [
    "Anonymizer",
    "BenchmarkResult",
    "BenchmarkRunner",
    "BenchmarkSpec",
    "ConformanceReport",
    "ConformanceSuite",
    "EmbeddingProviderConformance",
    "ExportAdapterConformance",
    "FixtureMissingError",
    "FrozenCampaignHarness",
    "ImageGenBackendConformance",
    "InvariantReport",
    "InvariantSnapshot",
    "LLMProviderConformance",
    "MechanicsConformance",
    "MockEmbeddingProvider",
    "MockLLMGateway",
    "QueueExhaustedError",
    "RecordReplayLLM",
    "RegressionReport",
    "ReplayMode",
    "ScenarioApp",
    "SnapshotStaleError",
    "TestApp",
    "TestAppFixture",
    "TestingConfig",
    "sidecar_path",
]
