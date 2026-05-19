"""Tiny safe expression language for HUD ``visible_when``.

Hand-rolled tokenizer + recursive-descent parser + closed evaluator so
mechanics modules can express simple visibility rules without giving us
a Python ``eval`` foothold. Grammar (see design doc):

    expr     ::= and_expr ( "or" and_expr )*
    and_expr ::= atom ( "and" atom )*
    atom     ::= "not"? primary
    primary  ::= bool_literal | call | path_var | "(" expr ")"
    call     ::= path_var "(" arg ("," arg)* ")"
    path_var ::= ident ( "." ident )*
    arg      ::= ident "=" ( string | int | bool )

The evaluator resolves ``path_var`` against a closed
:class:`EvaluationContext` namespace and returns ``False`` (with a
single warning) on any runtime resolution error so a broken module
widget hides rather than crashing the dashboard.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)


class ParseError(ValueError):
    """Raised by :func:`parse_expression` on grammar / token errors."""


@dataclass
class Token:
    kind: str
    value: Any
    pos: int = 0


_TOKEN_RE = re.compile(
    r"""
    (?P<WS>\s+)
    | (?P<STRING>"(?:[^"\\]|\\.)*")
    | (?P<INT>\d+)
    | (?P<KW>\b(?:and|or|not|true|false)\b)
    | (?P<IDENT>[a-zA-Z_][a-zA-Z_0-9]*)
    | (?P<DOT>\.)
    | (?P<LPAREN>\()
    | (?P<RPAREN>\))
    | (?P<COMMA>,)
    | (?P<EQ>=)
    """,
    re.VERBOSE,
)


_KW_KINDS = {"and": "AND", "or": "OR", "not": "NOT"}


def _tokenize(s: str) -> list[Token]:
    pos = 0
    toks: list[Token] = []
    while pos < len(s):
        m = _TOKEN_RE.match(s, pos)
        if m is None:
            raise ParseError(f"unexpected character at {pos}: {s[pos]!r}")
        kind = m.lastgroup
        text = m.group(0)
        end = m.end()
        if kind == "WS":
            pos = end
            continue
        if kind == "KW":
            if text in ("true", "false"):
                toks.append(Token("BOOL", text == "true", pos))
            else:
                toks.append(Token(_KW_KINDS[text], text, pos))
        elif kind == "STRING":
            toks.append(Token("STRING", text[1:-1].encode().decode("unicode_escape"), pos))
        elif kind == "INT":
            toks.append(Token("INT", int(text), pos))
        else:
            toks.append(Token(kind, text, pos))
        pos = end
    return toks


@dataclass
class Expr:
    pass


@dataclass
class Var(Expr):
    path: tuple[str, ...]


@dataclass
class Call(Expr):
    path: tuple[str, ...]
    args: dict[str, Any]


@dataclass
class Not(Expr):
    inner: Expr


@dataclass
class And(Expr):
    left: Expr
    right: Expr


@dataclass
class Or(Expr):
    left: Expr
    right: Expr


@dataclass
class BoolLit(Expr):
    value: bool


class _Parser:
    def __init__(self, toks: list[Token]) -> None:
        self.toks = toks
        self.i = 0

    def _peek(self) -> Token | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _consume(self, kind: str | None = None) -> Token:
        if self.i >= len(self.toks):
            raise ParseError(f"unexpected end of expression (expected {kind})")
        t = self.toks[self.i]
        if kind is not None and t.kind != kind:
            raise ParseError(f"expected {kind} at {t.pos}, got {t.kind} ({t.value!r})")
        self.i += 1
        return t

    def parse(self) -> Expr:
        if not self.toks:
            raise ParseError("empty expression")
        e = self._or()
        if self._peek() is not None:
            t = self._peek()
            raise ParseError(f"trailing token {t.kind} ({t.value!r}) at {t.pos}")
        return e

    def _or(self) -> Expr:
        left = self._and()
        while (t := self._peek()) and t.kind == "OR":
            self._consume("OR")
            left = Or(left, self._and())
        return left

    def _and(self) -> Expr:
        left = self._atom()
        while (t := self._peek()) and t.kind == "AND":
            self._consume("AND")
            left = And(left, self._atom())
        return left

    def _atom(self) -> Expr:
        t = self._peek()
        if t is None:
            raise ParseError("expected expression")
        if t.kind == "NOT":
            self._consume("NOT")
            return Not(self._atom())
        return self._primary()

    def _primary(self) -> Expr:
        t = self._peek()
        if t is None:
            raise ParseError("expected primary")
        if t.kind == "LPAREN":
            self._consume("LPAREN")
            inner = self._or()
            self._consume("RPAREN")
            return inner
        if t.kind == "BOOL":
            self._consume("BOOL")
            return BoolLit(bool(t.value))
        if t.kind != "IDENT":
            raise ParseError(f"unexpected {t.kind} ({t.value!r}) at {t.pos}")
        path: list[str] = [self._consume("IDENT").value]
        while (nxt := self._peek()) and nxt.kind == "DOT":
            self._consume("DOT")
            path.append(self._consume("IDENT").value)
        if (nxt := self._peek()) and nxt.kind == "LPAREN":
            self._consume("LPAREN")
            args: dict[str, Any] = {}
            if (nxt := self._peek()) and nxt.kind != "RPAREN":
                while True:
                    key = self._consume("IDENT").value
                    self._consume("EQ")
                    v = self._consume()
                    if v.kind not in ("STRING", "INT", "BOOL"):
                        raise ParseError(f"call args must be literal, got {v.kind} at {v.pos}")
                    args[key] = v.value
                    if (nxt := self._peek()) and nxt.kind == "COMMA":
                        self._consume("COMMA")
                        continue
                    break
            self._consume("RPAREN")
            return Call(tuple(path), args)
        return Var(tuple(path))


def parse_expression(s: str) -> Expr:
    """Parse ``s`` into an AST. Raises :class:`ParseError` on syntax errors."""
    return _Parser(_tokenize(s)).parse()


@dataclass
class EvaluationContext:
    """Closed namespace passed to :func:`evaluate`.

    Each root key is a dict (or object with attributes); resolving a path
    walks dict keys then attributes. Callable entries are invoked for
    ``Call`` nodes. Anything outside these roots raises and is treated
    as ``False``.
    """

    scene: dict[str, Any] = field(default_factory=dict)
    pc: dict[str, Any] = field(default_factory=dict)
    mechanics: dict[str, Any] = field(default_factory=dict)
    present_npc: dict[str, Any] = field(default_factory=dict)


def evaluate(expr: str | Expr, ctx: EvaluationContext) -> bool:
    """Evaluate ``expr`` against ``ctx``; ``False`` on any error."""
    try:
        node = parse_expression(expr) if isinstance(expr, str) else expr
        return bool(_eval(node, ctx))
    except ParseError:
        # Re-raise parse errors so manifest validation fails loudly.
        if isinstance(expr, str):
            raise
        return False
    except Exception as e:
        log.warning("hud expression evaluation failed for %r: %s", expr, e)
        return False


def _eval(expr: Expr, ctx: EvaluationContext) -> Any:
    if isinstance(expr, BoolLit):
        return expr.value
    if isinstance(expr, Var):
        return _resolve_path(expr.path, ctx)
    if isinstance(expr, Call):
        target = _resolve_path(expr.path, ctx)
        if not callable(target):
            raise TypeError(f"call on non-callable path {'.'.join(expr.path)}")
        return target(**expr.args)
    if isinstance(expr, Not):
        return not bool(_eval(expr.inner, ctx))
    if isinstance(expr, And):
        return bool(_eval(expr.left, ctx)) and bool(_eval(expr.right, ctx))
    if isinstance(expr, Or):
        return bool(_eval(expr.left, ctx)) or bool(_eval(expr.right, ctx))
    raise TypeError(f"unknown expression node: {type(expr).__name__}")


def _resolve_path(path: tuple[str, ...], ctx: EvaluationContext) -> Any:
    roots = {
        "scene": ctx.scene,
        "pc": ctx.pc,
        "mechanics": ctx.mechanics,
        "present_npc": ctx.present_npc,
    }
    if not path:
        raise KeyError("empty path")
    if path[0] not in roots:
        raise KeyError(f"unknown root {path[0]!r}")
    cur: Any = roots[path[0]]
    for seg in path[1:]:
        if isinstance(cur, dict):
            if seg not in cur:
                raise KeyError(f"missing key {seg!r} on {'.'.join(path)}")
            cur = cur[seg]
        else:
            cur = getattr(cur, seg)
    return cur


__all__ = [
    "EvaluationContext",
    "ParseError",
    "evaluate",
    "parse_expression",
]
