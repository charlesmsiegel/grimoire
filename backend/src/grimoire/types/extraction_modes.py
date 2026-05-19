"""Extraction-mode enum (spec extraction-modes-design).

Selects which strategy the Extractor uses to derive `StateDelta`s from a
turn's prose: a secondary LLM call (`SEPARATE`), an inline tracker block
parsed alongside prose (`TOGETHER`), provider-native tool calls
(`TOOL_USE`), no extraction at all (`NONE`, for auxiliary tasks), or
auto-select with fallback (`AUTO`).
"""

from __future__ import annotations

from enum import StrEnum


class ExtractionMode(StrEnum):
    SEPARATE = "separate"
    TOGETHER = "together"
    TOOL_USE = "tool_use"
    NONE = "none"
    AUTO = "auto"


__all__ = ["ExtractionMode"]
