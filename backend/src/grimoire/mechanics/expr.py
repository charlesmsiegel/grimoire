"""The bounded expression language of authored mechanics modules (086).

An expression is parsed by this module's own small parser into a tree of frozen nodes, type-checked
against the names in scope, and evaluated with exact rational arithmetic. Nothing is ever passed to
Python's ``eval``, no attribute or item is reached, and the only calls are five named arithmetic
functions. An authored formula therefore computes and can do nothing else.

Grammar (lowest precedence first; comparisons do not chain)::

    or      := and ('or' and)*
    and     := not ('and' not)*
    not     := 'not' not | compare
    compare := sum (('=='|'!='|'<'|'<='|'>'|'>=') sum)?
    sum     := term (('+'|'-') term)*
    term    := unary (('*'|'/') unary)*
    unary   := '-' unary | atom
    atom    := INTEGER | 'true' | 'false' | NAME | NAME '(' or (',' or)* ')' | '(' or ')'

Functions: ``floor(x)``, ``ceil(x)``, ``abs(x)``, ``min(a, b, ...)`` and ``max(a, b, ...)``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, Union

#: Longest expression text, deepest nesting, largest literal and largest magnitude any step may reach.
#: These bound the prototype's input; they are not claims about any game.
MAX_TEXT = 256
MAX_DEPTH = 32
MAX_LITERAL = 1_000_000
MAX_MAGNITUDE = 10 ** 12
NAME = re.compile(r"[a-z][a-z0-9_]{0,63}", re.ASCII)
KEYWORDS = frozenset({"and", "or", "not", "true", "false"})
FUNCTIONS = {"floor": (1, 1), "ceil": (1, 1), "abs": (1, 1), "min": (2, 8), "max": (2, 8)}
COMPARE = ("==", "!=", "<=", ">=", "<", ">")
_SPACE = re.compile(r"[ \t\r\n]*")
_TOKEN = re.compile(r"[ \t\r\n]*(?:([0-9]+)|([A-Za-z_][A-Za-z0-9_]*)|(==|!=|<=|>=|[-+*/(),<>]))", re.ASCII)

Kind = Literal["number", "bool"]


class ExprError(ValueError):
    """An expression that cannot be parsed, type-checked or evaluated, with a reason a person can act on."""


@dataclass(frozen=True)
class Num:
    value: int


@dataclass(frozen=True)
class Bool:
    value: bool


@dataclass(frozen=True)
class Name:
    name: str


@dataclass(frozen=True)
class Call:
    function: str
    args: tuple["Node", ...]


@dataclass(frozen=True)
class Unary:
    op: str  # "-" or "not"
    operand: "Node"


@dataclass(frozen=True)
class Binary:
    op: str
    left: "Node"
    right: "Node"


Node = Union[Num, Bool, Name, Call, Unary, Binary]


@dataclass(frozen=True)
class Expr:
    """A parsed expression and the exact text it came from."""

    text: str
    tree: Node

    def names(self) -> frozenset[str]:
        return frozenset(_names(self.tree))


def _tokens(text: str) -> list[str]:
    tokens: list[str] = []
    at = 0
    while at < len(text):
        at = _SPACE.match(text, at).end()  # type: ignore[union-attr]
        if at == len(text):
            break
        match = _TOKEN.match(text, at)
        if match is None:
            raise ExprError(f"unexpected character {text[at]!r}; formulas use plain spaces between parts")
        tokens.append(match.group(match.lastindex or 0))
        at = match.end()
    return tokens


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = _tokens(text)
        self.at = 0
        self.depth = 0

    def peek(self) -> str | None:
        return self.tokens[self.at] if self.at < len(self.tokens) else None

    def take(self, expected: str | None = None) -> str:
        token = self.peek()
        if token is None:
            raise ExprError("the expression ends too early" if expected is None
                            else f"expected {expected!r} at the end")
        if expected is not None and token != expected:
            raise ExprError(f"expected {expected!r}, found {token!r}")
        self.at += 1
        return token

    def nested(self):  # type: ignore[no-untyped-def]
        parser = self

        class _Depth:
            def __enter__(self) -> None:
                parser.depth += 1
                if parser.depth > MAX_DEPTH:
                    raise ExprError(f"nested more than {MAX_DEPTH} levels deep")

            def __exit__(self, *_: object) -> None:
                parser.depth -= 1

        return _Depth()

    def parse(self) -> Node:
        if not self.tokens:
            raise ExprError("the expression is empty")
        tree = self.disjunction()
        if self.peek() is not None:
            raise ExprError(f"unexpected {self.peek()!r} after a complete expression")
        return tree

    def disjunction(self) -> Node:
        with self.nested():
            node = self.conjunction()
            while self.peek() == "or":
                self.take()
                node = Binary("or", node, self.conjunction())
            return node

    def conjunction(self) -> Node:
        node = self.negation()
        while self.peek() == "and":
            self.take()
            node = Binary("and", node, self.negation())
        return node

    def negation(self) -> Node:
        if self.peek() == "not":
            self.take()
            with self.nested():
                return Unary("not", self.negation())
        return self.comparison()

    def comparison(self) -> Node:
        node = self.sum()
        if self.peek() in COMPARE:
            op = self.take()
            node = Binary(op, node, self.sum())
            if self.peek() in COMPARE:
                raise ExprError("comparisons do not chain; join them with 'and'")
        return node

    def sum(self) -> Node:
        node = self.term()
        while self.peek() in ("+", "-"):
            op = self.take()
            node = Binary(op, node, self.term())
        return node

    def term(self) -> Node:
        node = self.unary()
        while self.peek() in ("*", "/"):
            op = self.take()
            node = Binary(op, node, self.unary())
        return node

    def unary(self) -> Node:
        if self.peek() == "-":
            self.take()
            with self.nested():
                return Unary("-", self.unary())
        return self.atom()

    def atom(self) -> Node:
        token = self.take()
        if "0" <= token[0] <= "9":
            if int(token) > MAX_LITERAL:
                raise ExprError(f"number {token} is larger than {MAX_LITERAL}")
            return Num(int(token))
        if token == "(":
            node = self.disjunction()
            self.take(")")
            return node
        if token in ("true", "false"):
            return Bool(token == "true")
        if token in KEYWORDS:
            raise ExprError(f"{token!r} needs something before it")
        if token[0].isalpha() or token[0] == "_":
            if NAME.fullmatch(token) is None:
                raise ExprError(f"name {token!r} must be lowercase letters, digits and _, starting with a letter")
            if self.peek() == "(":
                return self.call(token)
            return Name(token)
        raise ExprError(f"unexpected {token!r}")

    def call(self, function: str) -> Node:
        if function not in FUNCTIONS:
            raise ExprError(f"unknown function {function!r}; the functions are {', '.join(sorted(FUNCTIONS))}")
        self.take("(")
        args = [self.disjunction()]
        while self.peek() == ",":
            self.take()
            args.append(self.disjunction())
        self.take(")")
        low, high = FUNCTIONS[function]
        if not low <= len(args) <= high:
            count = str(low) if low == high else f"{low} to {high}"
            raise ExprError(f"{function}() takes {count} arguments, not {len(args)}")
        return Call(function, tuple(args))


def parse(text: object) -> Expr:
    """Parse ``text``; ``ExprError`` says why it is not an expression."""
    if type(text) is not str:
        raise ExprError("an expression must be text")
    if len(text) > MAX_TEXT:
        raise ExprError(f"an expression is at most {MAX_TEXT} characters")
    return Expr(text, _Parser(text).parse())


def _names(node: Node):  # type: ignore[no-untyped-def]
    if isinstance(node, Name):
        yield node.name
    elif isinstance(node, Call):
        for arg in node.args:
            yield from _names(arg)
    elif isinstance(node, Unary):
        yield from _names(node.operand)
    elif isinstance(node, Binary):
        yield from _names(node.left)
        yield from _names(node.right)


def kind_of(expr: Expr | Node, scope: dict[str, Kind]) -> Kind:
    """The type ``expr`` has when every name in ``scope`` has its given type; ``ExprError`` otherwise."""
    node = expr.tree if isinstance(expr, Expr) else expr
    if isinstance(node, Num):
        return "number"
    if isinstance(node, Bool):
        return "bool"
    if isinstance(node, Name):
        if node.name not in scope:
            raise ExprError(f"unknown name {node.name!r}")
        return scope[node.name]
    if isinstance(node, Call):
        for arg in node.args:
            if kind_of(arg, scope) != "number":
                raise ExprError(f"{node.function}() takes numbers, not true/false")
        return "number"
    if isinstance(node, Unary):
        want: Kind = "bool" if node.op == "not" else "number"
        if kind_of(node.operand, scope) != want:
            raise ExprError(f"{node.op!r} needs {'a condition' if want == 'bool' else 'a number'}")
        return want
    left, right = kind_of(node.left, scope), kind_of(node.right, scope)
    if node.op in ("and", "or"):
        if left != "bool" or right != "bool":
            raise ExprError(f"{node.op!r} joins conditions, not numbers")
        return "bool"
    if node.op in ("==", "!="):
        if left != right:
            raise ExprError(f"{node.op!r} compares a number with true/false")
        return "bool"
    if left != "number" or right != "number":
        raise ExprError(f"{node.op!r} needs numbers on both sides")
    return "bool" if node.op in COMPARE else "number"


Value = Union[Fraction, bool]


def _bounded(value: Fraction) -> Fraction:
    if abs(value) > MAX_MAGNITUDE or value.denominator > MAX_MAGNITUDE:
        raise ExprError(f"a step reached a value beyond {MAX_MAGNITUDE}")
    return value


def evaluate(expr: Expr | Node, scope: dict[str, Value]) -> Value:
    """The exact value of a type-checked expression. ``ExprError`` for division by zero or a runaway value."""
    node = expr.tree if isinstance(expr, Expr) else expr
    if isinstance(node, Num):
        return Fraction(node.value)
    if isinstance(node, Bool):
        return node.value
    if isinstance(node, Name):
        if node.name not in scope:
            raise ExprError(f"unknown name {node.name!r}")
        return scope[node.name]
    if isinstance(node, Call):
        args = [evaluate(arg, scope) for arg in node.args]
        if node.function == "floor":
            return Fraction(math.floor(args[0]))
        if node.function == "ceil":
            return Fraction(math.ceil(args[0]))
        if node.function == "abs":
            return abs(args[0])  # type: ignore[arg-type,return-value]
        return (min if node.function == "min" else max)(args)  # type: ignore[type-var,return-value]
    if isinstance(node, Unary):
        value = evaluate(node.operand, scope)
        return (not value) if node.op == "not" else -value  # type: ignore[operator]
    if node.op == "and":
        return bool(evaluate(node.left, scope)) and bool(evaluate(node.right, scope))
    if node.op == "or":
        return bool(evaluate(node.left, scope)) or bool(evaluate(node.right, scope))
    left, right = evaluate(node.left, scope), evaluate(node.right, scope)
    if node.op == "==":
        return left == right
    if node.op == "!=":
        return left != right
    if node.op == "<":
        return left < right  # type: ignore[operator]
    if node.op == "<=":
        return left <= right  # type: ignore[operator]
    if node.op == ">":
        return left > right  # type: ignore[operator]
    if node.op == ">=":
        return left >= right  # type: ignore[operator]
    if node.op == "+":
        return _bounded(left + right)  # type: ignore[operator]
    if node.op == "-":
        return _bounded(left - right)  # type: ignore[operator]
    if node.op == "*":
        return _bounded(left * right)  # type: ignore[operator]
    if right == 0:
        raise ExprError("division by zero")
    return _bounded(Fraction(left) / Fraction(right))  # type: ignore[arg-type]


def integer(value: Value, what: str) -> int:
    """``value`` as an int; ``ExprError`` when it is a fraction or a condition."""
    if type(value) is bool or not isinstance(value, Fraction):
        raise ExprError(f"{what} must be a number")
    if value.denominator != 1:
        raise ExprError(f"{what} came to {value}, not a whole number; wrap it in floor() or ceil()")
    return int(value)


__all__ = ["ExprError", "Expr", "Kind", "MAX_TEXT", "NAME", "evaluate", "integer", "kind_of", "parse"]
