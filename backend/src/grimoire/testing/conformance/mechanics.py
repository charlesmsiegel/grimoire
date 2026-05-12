"""Mechanics module conformance suite (spec 17 §Mechanics)."""

from __future__ import annotations

from typing import Any

from grimoire.testing.conformance.types import (
    ConformanceReport,
    elapsed_ms,
    run_check,
    skip,
    started,
)
from grimoire.types.common import Duration
from grimoire.types.mechanics import Roll, TickContext
from grimoire.types.scene import Scene, SceneContext
from grimoire.validation.validator import check_schema


class MechanicsConformance:
    kind = "mechanics"

    async def run(self, adapter: Any) -> ConformanceReport:
        report = ConformanceReport(kind=self.kind, target_id=getattr(adapter, "id", "<unknown>"))
        t0 = started()

        await run_check(
            report,
            "test_sheet_schema_valid_json_schema",
            lambda: self._schema(adapter),
        )
        await run_check(
            report, "test_validate_sheet_accepts_valid", lambda: self._validate_valid(adapter)
        )
        await run_check(
            report, "test_validate_sheet_rejects_invalid", lambda: self._validate_invalid(adapter)
        )
        await run_check(
            report, "test_evaluate_pre_roll_returns_list", lambda: self._pre_roll(adapter)
        )
        await run_check(
            report,
            "test_resolve_roll_deterministic_with_seed",
            lambda: self._resolve_deterministic(adapter),
        )
        await run_check(
            report,
            "test_resolve_roll_within_legal_outcome_space",
            lambda: self._resolve_legal(adapter),
        )
        await run_check(
            report,
            "test_character_creation_steps_resolvable_in_order",
            lambda: self._creation_steps(adapter),
        )
        await run_check(
            report, "test_time_tick_returns_valid_deltas", lambda: self._time_tick(adapter)
        )

        report.duration_ms = elapsed_ms(t0)
        return report

    # -- checks -------------------------------------------------------- #

    async def _schema(self, adapter: Any) -> None:
        schema = adapter.sheet_schema("character")
        if schema is None:
            raise skip("module declines a character schema")
        if not isinstance(schema, dict):
            raise AssertionError("sheet_schema must return a dict")
        # Confirm the schema is itself a well-formed JSON Schema rather
        # than e.g. a plain template.
        schema_check = check_schema(schema)
        if not schema_check.ok:
            raise AssertionError(
                "sheet_schema returned an invalid JSON Schema: "
                + "; ".join(e.message for e in schema_check.errors)
            )

    async def _validate_valid(self, adapter: Any) -> None:
        initial = adapter.initialize_sheet("character", "probe")
        result = adapter.validate_sheet("character", initial)
        if not result.valid:
            raise AssertionError(f"initialize_sheet output failed validate_sheet: {result.errors}")

    async def _validate_invalid(self, adapter: Any) -> None:
        # An empty dict against a schema with required properties should
        # fail; if the schema is permissive, we skip.
        schema = adapter.sheet_schema("character") or {}
        required = schema.get("required") if isinstance(schema, dict) else None
        if not required:
            raise skip("schema has no required fields — permissive validation")
        result = adapter.validate_sheet("character", {})
        if result.valid:
            raise AssertionError("validate_sheet accepted an obviously-invalid empty sheet")

    async def _pre_roll(self, adapter: Any) -> None:
        scene = self._fake_scene()
        proposals = adapter.evaluate_pre_roll("I do something", scene)
        if not isinstance(proposals, list):
            raise AssertionError("evaluate_pre_roll must return a list")

    async def _resolve_deterministic(self, adapter: Any) -> None:
        roll = Roll(id="probe", kind="dice-pool", pool=3, seed=42)
        first = adapter.resolve_roll(roll, 42)
        second = adapter.resolve_roll(roll, 42)
        if first.model_dump() != second.model_dump():
            raise AssertionError("resolve_roll is non-deterministic for the same seed")

    async def _resolve_legal(self, adapter: Any) -> None:
        roll = Roll(id="probe", kind="dice-pool", pool=5, seed=7)
        result = adapter.resolve_roll(roll, 7)
        if result.successes < 0:
            raise AssertionError(f"successes must be non-negative; got {result.successes}")
        if not isinstance(result.dice, list):
            raise AssertionError("dice must be a list")

    async def _creation_steps(self, adapter: Any) -> None:
        steps = adapter.character_creation_steps()
        if not isinstance(steps, list):
            raise AssertionError("character_creation_steps must return a list")
        for step in steps:
            if getattr(step, "id", None) is None or not getattr(step, "title", ""):
                raise AssertionError("creation step missing id/title")

    async def _time_tick(self, adapter: Any) -> None:
        ctx = TickContext(
            campaign_id="probe",
            branch_id="probe:main",
            duration=Duration(iso8601="PT1H"),
        )
        deltas = adapter.time_tick("character:probe", {}, ctx.duration, ctx)
        if deltas is None:
            raise AssertionError("time_tick must return a list (possibly empty)")

    def _fake_scene(self) -> SceneContext:
        scene = Scene(
            id="probe-scene",
            campaign_id="probe-campaign",
            branch_id="probe-campaign:main",
            ordinal=1,
            slug="probe",
            file_path="probe.md",
        )
        return SceneContext(scene=scene)


__all__ = ["MechanicsConformance"]
