"""Tests for the `ExtractionMode` enum (spec extraction-modes-design)."""

from __future__ import annotations

from grimoire.types.extraction_modes import ExtractionMode


def test_enum_values():
    assert ExtractionMode.SEPARATE.value == "separate"
    assert ExtractionMode.TOGETHER.value == "together"
    assert ExtractionMode.TOOL_USE.value == "tool_use"
    assert ExtractionMode.NONE.value == "none"
    assert ExtractionMode.AUTO.value == "auto"


def test_enum_round_trips_through_string():
    for mode in ExtractionMode:
        assert ExtractionMode(mode.value) is mode
