"""Safe expression evaluator for mechanics modules (#160).

A whitelisted subset of Python expressions parsed via ``ast`` -- never
``eval`` on raw text. Serves sheet derived fields, check roll formulas, and
(later) creation budgets. Pure stdlib: no filesystem, no pydantic.
Spec: docs/superpowers/specs/2026-07-12-mechanics-phase1-modules-design.md.
"""

from __future__ import annotations

import ast
import math


class ExpressionError(ValueError):
    """Unparseable, forbidden, or unevaluable expression."""


_FUNCS = {"min": min, "max": max, "floor": math.floor, "ceil": math.ceil, "abs": abs}

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


def _eval(node: ast.AST, scope: dict) -> int | float | bool:
    if isinstance(node, ast.Expression):
        return _eval(node.body, scope)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in scope:
            raise ExpressionError(f"unknown name {node.id!r}")
        return scope[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, scope)
        return -v if isinstance(node.op, ast.USub) else (not v if isinstance(node.op, ast.Not) else None)  # type: ignore
    if isinstance(node, ast.BinOp):
        a, b = _eval(node.left, scope), _eval(node.right, scope)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            return a / b
        return a // b  # FloorDiv (only remaining allowed BinOp)
    if isinstance(node, ast.BoolOp):
        result = _eval(node.values[0], scope)
        for v in node.values[1:]:
            if isinstance(node.op, ast.And) and not result:
                return result
            if isinstance(node.op, ast.Or) and result:
                return result
            result = _eval(v, scope)
        return result
    if isinstance(node, ast.Compare):
        left = _eval(node.left, scope)
        for op, comp in zip(node.ops, node.comparators):
            right = _eval(comp, scope)
            ok = (
                left == right if isinstance(op, ast.Eq)
                else left != right if isinstance(op, ast.NotEq)
                else left < right if isinstance(op, ast.Lt)
                else left <= right if isinstance(op, ast.LtE)
                else left > right if isinstance(op, ast.Gt)
                else left >= right
            )
            if not ok:
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, scope) if _eval(node.test, scope) else _eval(node.orelse, scope)
    if isinstance(node, ast.Call):
        args = [_eval(a, scope) for a in node.args]
        return _FUNCS[node.func.id](*args)
    raise ExpressionError(f"unhandled node {type(node).__name__}")  # unreachable


def evaluate(text: str, scope: dict[str, int | float]) -> int | float | bool:
    try:
        return _eval(parse(text), scope)
    except ExpressionError:
        raise
    except (ZeroDivisionError, TypeError, ValueError) as e:
        raise ExpressionError(f"cannot evaluate {text!r}: {e}") from None
