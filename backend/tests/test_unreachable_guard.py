"""Guard: nothing follows a `return`, `raise`, `continue` or `break` in its block.

`calendars.base.resolve` shipped with a whole second scan loop sitting after
its final `raise` -- the body it had before the window scan was rewritten to
be forward-first, keep a time of day and let a broken `describe` raise. Every
test passed, because a statement nobody can reach changes no answer; what it
changes is the next reader, who finds two implementations of one rule that
disagree on all three points and has to work out which one runs. Neither
linter here says so: ruff's unreachable-code rule is preview-only, and mypy's
`warn_unreachable` reports every defensive `isinstance` on data read from disk
as well, which is not this.

Mechanical rule, over the package, the scripts and the eval harness: in any
statement list, a `return`, `raise`, `continue` or `break` is the last
statement. The one exception is a bare `yield` straight after it -- the idiom
that makes a function which only raises into a generator, where the line is
the point rather than code that was meant to run. `backend/tests` is left
out on cost: it is more Python than everything else here put together, and
parsing it would double this guard's share of every run.

Honest about its reach: it sees only what follows a terminal statement in the
SAME block. Code after an `if` whose every branch returns, after a
`while True:` with no `break`, or after a call that always raises is
unreachable too, and passes.
"""

from __future__ import annotations

import ast
import itertools
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
ROOTS = (REPO / "backend" / "src" / "grimoire", REPO / "backend" / "scripts",
         REPO / "evals", REPO / "scripts")

TERMINAL = (ast.Return, ast.Raise, ast.Continue, ast.Break)


def _blocks(tree: ast.AST):
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                yield block


def _generator_marker(stmt: ast.stmt) -> bool:
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Yield) and stmt.value.value is None


def unreachable(source: str) -> list[int]:
    """Line numbers of statements in `source` that follow a terminal one."""
    return sorted(after.lineno
                  for block in _blocks(ast.parse(source))
                  for index, (before, after) in enumerate(itertools.pairwise(block), 1)
                  if isinstance(before, TERMINAL)
                  and not (index == len(block) - 1 and _generator_marker(after)))


def test_no_statement_follows_a_terminal_one():
    found = [f"{path.relative_to(REPO).as_posix()}:{line}"
             for root in ROOTS
             for path in sorted(root.rglob("*.py"))
             for line in unreachable(path.read_text(encoding="utf-8"))]
    assert not found, f"statements that can never run: {found}"


def test_the_guard_flags_a_planted_dead_block():
    planted = (
        "def resolve(near):\n"
        "    for day in near:\n"
        "        if day:\n"
        "            continue\n"
        "            print(day)\n"
        "    raise ValueError(near)\n"
        "    try:\n"
        "        return near\n"
        "    except KeyError:\n"
        "        pass\n"
    )
    assert unreachable(planted) == [5, 7]


def test_the_guard_allows_the_generator_idiom():
    fake = (
        "def stream():\n"
        "    raise OSError('the provider is down')\n"
        "    yield  # makes this a generator\n"
    )
    assert unreachable(fake) == []


@pytest.mark.parametrize("value", ["'stale'", "None"])
def test_the_guard_rejects_a_value_yield_after_a_terminal(value):
    assert unreachable(f"def stream():\n    raise OSError\n    yield {value}\n") == [3]


def test_the_guard_rejects_a_nonfinal_generator_marker():
    assert unreachable("def stream():\n    raise OSError\n    yield\n    stale_call()\n") == [3]
