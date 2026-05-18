"""Transformation pipeline applied to post prose before formatting.

Each filter is a pure ``str -> str`` function. The pipeline composes them in
the order listed below. Filters that need scene-level context (anonymisation,
POV consolidation hints) read from a small ``FilterContext`` value.

Spec 13 §Filter and transformation hooks lists six filters; here we ship
the prose-level ones (OOC, mechanics, narrator scaffolding, anonymise) and
leave POV consolidation to the adapter (it's a structural concern more than
a text one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Compiled once at import time so per-post work is cheap.
_OOC_PAREN_RE = re.compile(r"\(\s*OOC[:\s][^)]*\)", re.IGNORECASE)
_OOC_BRACKET_RE = re.compile(r"\[\s*OOC[:\s][^\]]*\]", re.IGNORECASE)
_OOC_LINE_RE = re.compile(r"(?m)^\s*OOC[:\s].*$")
_SCENE_BREAK_RE = re.compile(
    r"(?mi)^\s*\[\s*(scene\s*(break|change)|cut\s*to|fade\s*(in|out))[^\]]*\]\s*$"
)
_NARRATOR_PAREN_RE = re.compile(
    r"\(\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+)?(rolls?|attempts?|tries to|"
    r"channels|spends?|casts?)\s+[^)]*\)"
)
_MECHANICS_BRACKET_RE = re.compile(r"\[\s*(roll|mech|stat|sheet)[^\]]*\]", re.IGNORECASE)
_MECHANICS_CHIP_RE = re.compile(r"(?m)^\s*(?:>>?\s*)?(?:Roll|Mechanic|Stat|Result)\s*[:\-—]\s*.*$")
_MULTI_BLANK_RE = re.compile(r"\n{3,}")


@dataclass(slots=True)
class FilterContext:
    """Per-export context the filters draw from."""

    strip_ooc: bool = True
    strip_mechanics: bool = False
    strip_narrator_scaffolding: bool = True
    anonymize: dict[str, str] = field(default_factory=dict)
    skip_tags: list[str] = field(default_factory=list)
    # POV consolidation (spec 13, sixth filter): merge adjacent posts that
    # share an author. Values: "off" | "by_kind" (PCs collapse together,
    # NPCs collapse together) | "by_author" (only literal same author).
    pov_consolidation_mode: str = "off"


def strip_ooc(text: str) -> str:
    text = _OOC_PAREN_RE.sub("", text)
    text = _OOC_BRACKET_RE.sub("", text)
    text = _OOC_LINE_RE.sub("", text)
    return text


def strip_mechanics(text: str) -> str:
    text = _MECHANICS_BRACKET_RE.sub("", text)
    text = _MECHANICS_CHIP_RE.sub("", text)
    return text


def strip_narrator_scaffolding(text: str) -> str:
    text = _SCENE_BREAK_RE.sub("", text)
    text = _NARRATOR_PAREN_RE.sub("", text)
    return text


def anonymize(text: str, mapping: dict[str, str]) -> str:
    if not mapping:
        return text
    # Order longest-first so 'julian Bell' replaces before 'julian'.
    for original in sorted(mapping, key=len, reverse=True):
        replacement = mapping[original]
        if not original:
            continue
        text = re.sub(rf"\b{re.escape(original)}\b", replacement, text)
    return text


def _normalize_whitespace(text: str) -> str:
    text = _MULTI_BLANK_RE.sub("\n\n", text)
    return text.strip()


def apply_filters(text: str, ctx: FilterContext) -> str:
    """Apply the configured filters in canonical order."""
    if ctx.strip_ooc:
        text = strip_ooc(text)
    if ctx.strip_narrator_scaffolding:
        text = strip_narrator_scaffolding(text)
    if ctx.strip_mechanics:
        text = strip_mechanics(text)
    if ctx.anonymize:
        text = anonymize(text, ctx.anonymize)
    return _normalize_whitespace(text)


def anonymize_label(label: str | None, ctx: FilterContext) -> str | None:
    """Return the pseudonym for a speaker label, if one is configured."""
    if not label or not ctx.anonymize:
        return label
    return ctx.anonymize.get(label, label)


__all__ = [
    "FilterContext",
    "anonymize",
    "anonymize_label",
    "apply_filters",
    "strip_mechanics",
    "strip_narrator_scaffolding",
    "strip_ooc",
]
