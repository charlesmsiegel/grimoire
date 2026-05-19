"""Tests for the HUD ``visible_when`` expression language."""

from __future__ import annotations

import logging

import pytest

from grimoire.hud.expression import (
    EvaluationContext,
    ParseError,
    evaluate,
    parse_expression,
)


def test_bool_literal_true() -> None:
    assert evaluate("true", EvaluationContext()) is True
    assert evaluate("false", EvaluationContext()) is False


def test_simple_var_resolves_dict() -> None:
    ctx = EvaluationContext(scene={"combat_active": True})
    assert evaluate("scene.combat_active", ctx) is True


def test_and_or_precedence() -> None:
    ctx = EvaluationContext(
        pc={"has_sheet": True},
        scene={"combat_active": False},
    )
    assert evaluate("pc.has_sheet and scene.combat_active", ctx) is False
    assert evaluate("pc.has_sheet or scene.combat_active", ctx) is True
    # `a and b or c` should parse as `(a and b) or c`.
    ctx2 = EvaluationContext(
        pc={"has_sheet": False},
        scene={"combat_active": False, "lit": True},
    )
    assert evaluate("pc.has_sheet and scene.combat_active or scene.lit", ctx2) is True


def test_not_negates() -> None:
    ctx = EvaluationContext(pc={"has_sheet": False})
    assert evaluate("not pc.has_sheet", ctx) is True


def test_parentheses_override_precedence() -> None:
    ctx = EvaluationContext(
        pc={"has_sheet": False, "in_scene": True},
        scene={"combat_active": True},
    )
    assert (
        evaluate(
            "pc.has_sheet and (pc.in_scene or scene.combat_active)", ctx
        )
        is False
    )
    assert (
        evaluate(
            "(pc.has_sheet or pc.in_scene) and scene.combat_active", ctx
        )
        is True
    )


def test_call_with_keyword_arg() -> None:
    ctx = EvaluationContext(
        mechanics={"has_event": lambda kind: kind == "ongoing"}
    )
    assert evaluate('mechanics.has_event(kind="ongoing")', ctx) is True
    assert evaluate('mechanics.has_event(kind="other")', ctx) is False


def test_unknown_root_returns_false_with_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="grimoire.hud.expression"):
        ast = parse_expression("missing.path")
        assert evaluate(ast, EvaluationContext()) is False
    assert any("hud expression evaluation failed" in r.message for r in caplog.records)


def test_missing_dict_key_is_false() -> None:
    ctx = EvaluationContext(scene={})
    ast = parse_expression("scene.combat_active")
    assert evaluate(ast, ctx) is False


def test_parse_error_raises_for_string_input() -> None:
    with pytest.raises(ParseError):
        evaluate("scene.combat_active &&&", EvaluationContext())


def test_parse_empty_string_raises() -> None:
    with pytest.raises(ParseError):
        parse_expression("")


def test_parse_error_on_trailing_token() -> None:
    with pytest.raises(ParseError):
        parse_expression("true false")


def test_call_args_must_be_literal() -> None:
    with pytest.raises(ParseError):
        parse_expression("mechanics.has_event(kind=other.path)")
