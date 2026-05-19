"""Tests for the core expression vocabulary + namespacing helpers."""

from __future__ import annotations

import logging

from grimoire.types.expressions import (
    CORE_EXPRESSION_VALUES,
    CoreExpression,
    is_known_label,
    is_valid_extension_label,
    namespace_label,
    resolve_label,
)


def test_core_expression_complete() -> None:
    assert {e.value for e in CoreExpression} == {
        "neutral",
        "happy",
        "sad",
        "angry",
        "surprised",
        "fearful",
        "disgusted",
        "smug",
        "thoughtful",
        "embarrassed",
        "determined",
        "hurt",
        "tired",
        "suspicious",
    }


def test_core_values_set_is_frozen() -> None:
    assert isinstance(CORE_EXPRESSION_VALUES, frozenset)
    assert "happy" in CORE_EXPRESSION_VALUES


def test_valid_extension_label() -> None:
    assert is_valid_extension_label("seductive")
    assert is_valid_extension_label("terrified")
    assert is_valid_extension_label("a")
    assert is_valid_extension_label("a" * 32)
    assert not is_valid_extension_label("")
    assert not is_valid_extension_label("Happy")  # uppercase
    assert not is_valid_extension_label("a" * 33)
    assert not is_valid_extension_label("1leading_digit")
    assert not is_valid_extension_label("contains-dash")


def test_resolve_core_label_passes_through() -> None:
    assert resolve_label("happy") == "happy"
    assert resolve_label("happy", modules=["wod"]) == "happy"


def test_resolve_bare_label_namespaces_single_module() -> None:
    out = resolve_label("seductive", module_extensions={"wod": ["seductive", "awakened"]})
    assert out == "wod.seductive"


def test_resolve_namespaced_label_passes_through() -> None:
    out = resolve_label("wod.seductive", modules=["wod"])
    assert out == "wod.seductive"


def test_resolve_ambiguous_logs_and_picks_first(caplog) -> None:
    caplog.set_level(logging.WARNING)
    out = resolve_label(
        "seductive",
        module_extensions={"wod": ["seductive"], "dnd": ["seductive"]},
    )
    # Whichever wins, it must be a fully-qualified ref.
    assert out.endswith(".seductive")
    assert "ambiguous" in caplog.text


def test_namespace_label() -> None:
    assert namespace_label("wod", "seductive") == "wod.seductive"


def test_is_known_label() -> None:
    assert is_known_label("happy")
    assert not is_known_label("wod.seductive")
    assert is_known_label(
        "wod.seductive",
        module_extensions={"wod": ["seductive"]},
    )
    assert not is_known_label(
        "wod.absent",
        module_extensions={"wod": ["seductive"]},
    )
