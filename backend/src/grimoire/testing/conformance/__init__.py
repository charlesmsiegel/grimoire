"""Plugin conformance suites (spec 17 §L2).

Every plugin kind has a standard suite. The suite calls the adapter's
methods and asserts they meet the protocol's contract. Failing
conformance means the plugin should not be registered.

A suite returns a :class:`ConformanceReport` rather than raising so a
single failed test doesn't mask the rest. Callers (`PluginsService`
in CI mode, plugin authors locally) can pretty-print or assert on the
report.
"""

from grimoire.testing.conformance.embedding import EmbeddingProviderConformance
from grimoire.testing.conformance.export import ExportAdapterConformance
from grimoire.testing.conformance.imagegen import ImageGenBackendConformance
from grimoire.testing.conformance.llm_provider import LLMProviderConformance
from grimoire.testing.conformance.mechanics import MechanicsConformance
from grimoire.testing.conformance.types import ConformanceReport, ConformanceSuite

__all__ = [
    "ConformanceReport",
    "ConformanceSuite",
    "EmbeddingProviderConformance",
    "ExportAdapterConformance",
    "ImageGenBackendConformance",
    "LLMProviderConformance",
    "MechanicsConformance",
]
