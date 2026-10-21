"""Safe expression evaluator for mechanics modules (#160).

A whitelisted subset of Python expressions parsed via ``ast`` -- never
``eval`` on raw text. Serves sheet derived fields, check roll formulas, and
(later) creation budgets. Pure stdlib: no filesystem, no pydantic.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase1-modules-design.md.
"""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any, cast


class ExpressionError(ValueError):
    """Unparseable, forbidden, or unevaluable expression."""


_FUNCS: dict[str, Callable[..., Any]] = {"min": min, "max": max, "floor": math.floor, "ceil": math.ceil, "abs": abs}

_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare,
    ast.IfExp, ast.Call, ast.Name, ast.Load, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.USub,
    ast.And, ast.Or, ast.Not,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
)


def parse(text: str) -> ast.Expression:
    """Parse ``text`` into a validated ast.Expression or raise ExpressionError."""
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, TypeError) as e:
        raise ExpressionError(f"unparseable expression {text!r}: {e}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ExpressionError(
                f"forbidden construct {type(node).__name__} in {text!r}"
            )
        if isinstance(node, ast.Constant) and (not isinstance(node.value, (int, float)) or isinstance(node.value, bool)):
            raise ExpressionError(f"non-numeric literal in {text!r}")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS:
                raise ExpressionError(f"forbidden call in {text!r}")
            if node.keywords:
                raise ExpressionError(f"keyword arguments not allowed in {text!r}")
    return tree


def names(text: str) -> set[str]:
    """Field names referenced by the expression (call names excluded)."""
    tree = parse(text)
    return {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and n.id not in _FUNCS
    }


Value = int | float | bool

# One operator per allowed AST operator node; `parse` has already refused any
# other, so a lookup here cannot miss on a validated tree.
_UNARY: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.USub: operator.neg, ast.Not: operator.not_,
}
_BINARY: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
}
_COMPARE: dict[type[ast.cmpop], Callable[[Any, Any], Any]] = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}


def _eval_name(node: ast.Name, scope: dict) -> Value:
    if node.id not in scope:
        raise ExpressionError(f"unknown name {node.id!r}")
    return scope[node.id]


def _eval_binary(node: ast.BinOp, scope: dict) -> Value:
    a, b = _eval(node.left, scope), _eval(node.right, scope)
    return _BINARY[type(node.op)](a, b)


def _eval_bool(node: ast.BoolOp, scope: dict) -> Value:
    """Python's short circuit, returning the deciding operand itself: `and`
    stops at the first falsy value, `or` at the first truthy one."""
    stop_when = isinstance(node.op, ast.Or)
    result = _eval(node.values[0], scope)
    for value in node.values[1:]:
        if bool(result) == stop_when:
            return result
        result = _eval(value, scope)
    return result


def _eval_compare(node: ast.Compare, scope: dict) -> Value:
    """A chained comparison, evaluated pairwise and stopping at the first
    false link, as Python does."""
    left = _eval(node.left, scope)
    for op, comp in zip(node.ops, node.comparators, strict=True):
        right = _eval(comp, scope)
        if not _COMPARE[type(op)](left, right):
            return False
        left = right
    return True


def _eval_call(node: ast.Call, scope: dict) -> Value:
    args = [_eval(a, scope) for a in node.args]
    # `parse` admits only a bare call of a `_FUNCS` name.
    return _FUNCS[cast(ast.Name, node.func).id](*args)


_EVALUATORS: dict[type[ast.AST], Callable[[Any, dict], Value]] = {
    ast.Expression: lambda node, scope: _eval(node.body, scope),
    ast.Constant: lambda node, _scope: node.value,
    ast.Name: _eval_name,
    ast.UnaryOp: lambda node, scope: _UNARY[type(node.op)](_eval(node.operand, scope)),
    ast.BinOp: _eval_binary,
    ast.BoolOp: _eval_bool,
    ast.Compare: _eval_compare,
    ast.IfExp: lambda node, scope: (_eval(node.body, scope) if _eval(node.test, scope)
                                    else _eval(node.orelse, scope)),
    ast.Call: _eval_call,
}


def _eval(node: ast.AST, scope: dict) -> Value:
    evaluator = _EVALUATORS.get(type(node))
    if evaluator is None:
        raise ExpressionError(f"unhandled node {type(node).__name__}")  # unreachable
    return evaluator(node, scope)


def evaluate(text: str, scope: dict[str, int | float]) -> int | float | bool:
    try:
        return _eval(parse(text), scope)
    except ExpressionError:
        raise
    except (ZeroDivisionError, TypeError, ValueError, OverflowError) as e:
        raise ExpressionError(f"cannot evaluate {text!r}: {e}") from None
