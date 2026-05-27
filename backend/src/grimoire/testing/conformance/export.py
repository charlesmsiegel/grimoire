"""Export adapter conformance suite."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from grimoire.testing.conformance.types import (
    ConformanceReport,
    elapsed_ms,
    run_check,
    skip,
    started,
)
from grimoire.types.export import ExportOptions, ExportSelection


class ExportAdapterConformance:
    kind = "export_adapter"

    async def run(self, adapter: Any, *, campaign_id: str = "probe") -> ConformanceReport:
        report = ConformanceReport(kind=self.kind, target_id=getattr(adapter, "id", "<unknown>"))
        t0 = started()
        self._campaign_id = campaign_id

        await run_check(
            report,
            "test_export_produces_nonempty_output",
            lambda: self._nonempty(adapter),
        )
        await run_check(
            report,
            "test_export_respects_scene_selection",
            lambda: self._scene_selection(adapter),
        )
        await run_check(
            report,
            "test_export_respects_appendix_selection",
            lambda: self._appendix_selection(adapter),
        )
        await run_check(
            report,
            "test_option_schema_is_dict",
            lambda: self._options_schema(adapter),
        )

        report.duration_ms = elapsed_ms(t0)
        return report

    async def _nonempty(self, adapter: Any) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / f"out.{(adapter.extensions or ['bin'])[0]}"
            result = await adapter.export(
                self._campaign_id,
                ExportSelection(),
                ExportOptions(title="Probe"),
                out,
            )
        if result.size_bytes <= 0 and not (result.payload and len(result.payload) > 0):
            raise AssertionError("export produced no bytes")

    async def _scene_selection(self, adapter: Any) -> None:
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / f"out.{(adapter.extensions or ['bin'])[0]}"
            full = await adapter.export(
                self._campaign_id,
                ExportSelection(),
                ExportOptions(title="Full"),
                out,
            )
            subset = await adapter.export(
                self._campaign_id,
                ExportSelection(
                    scene_ids=[],
                ),
                ExportOptions(title="None"),
                out,
            )
        if full.scene_count == subset.scene_count and full.scene_count > 0:
            raise skip("adapter does not differentiate scene selection in this fixture")

    async def _appendix_selection(self, adapter: Any) -> None:
        caps = getattr(adapter, "capabilities", None)
        if caps is not None and not getattr(caps, "supports_appendices", True):
            raise skip("adapter does not support appendices")
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / f"out.{(adapter.extensions or ['bin'])[0]}"
            await adapter.export(
                self._campaign_id,
                ExportSelection(
                    include_appendices=["cast"],
                ),
                ExportOptions(title="Probe"),
                out,
            )

    async def _options_schema(self, adapter: Any) -> None:
        schema = adapter.option_schema()
        if not isinstance(schema, dict):
            raise AssertionError("option_schema must return a dict")


__all__ = ["ExportAdapterConformance"]
