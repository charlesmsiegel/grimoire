"""Guard: every generation route meters what it spends (#152).

The ledger is only worth reading if it is complete, and completeness is exactly
the property that decays silently. Wiring a meter into eleven call sites is a
one-time act; the twelfth is written six months from now by someone adding a
route, and nothing fails when they forget. The rollup just quietly
under-reports, which is worse than having no rollup at all -- a number that is
wrong in an unknown direction is what people budget against.

This is the same shape, and the same reasoning, as ``test_atomic_guard.py``: a
store-wide rule that drift can break without breaking anything else, so it is
checked by walking the package's own ASTs rather than remembered.

Honest about its reach, the house standard:

- **The LLM client is recognized by the receiver name ``client``.** That is the
  codebase's own convention -- the dependency is injected as
  ``client: LLMClient = Depends(get_llm)`` at every route -- not a proof. A
  generation reached through a differently-named binding is not seen.
- **Only ``routes/`` is scanned.** Nothing else in the package holds an
  ``LLMClient``; the adapters underneath take a holder from the facade, and the
  facade is covered by its own tests.
- **"Metered" is approximated by the argument being ``<something>.usage``.**
  The real property is "this holder belongs to a ``store.usage.Meter`` that will
  file a row", which no static check can decide. What this catches is the actual
  failure mode -- a call site passing no holder at all -- and it deliberately
  does not try to prove the meter is ever finished. `Meter` records from
  ``__exit__``, so a ``with`` that is entered is a row; a caller who builds a
  meter and never enters it is a bug this cannot see.
- **A marker clears a call that genuinely should not be counted**, with a reason,
  and the count of markers is capped: an exemption is a hole in the total.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire.routes as routes_pkg

from . import guard_markers

ROUTES = pathlib.Path(routes_pkg.__file__).parent

#: The two `LLMClient` methods that reach a provider. `aclose` does not.
_GENERATORS = ("stream", "complete")
#: How the injected client is spelled at every route (see the module docstring).
_CLIENT = "client"
#: The holder attribute a `store.usage.Meter` exposes.
_HOLDER = "usage"

MARKER = "usage-ok:"


def _generation_calls(tree: ast.AST):
    """Every `client.stream(...)` / `client.complete(...)` in one module."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr in _GENERATORS
                and isinstance(f.value, ast.Name) and f.value.id == _CLIENT):
            yield node


def _is_metered(node: ast.Call) -> bool:
    """Whether this call hands the facade an accounting holder.

    Both spellings the signature allows: the third positional argument
    (``client.complete(messages, conn, m.usage)``) and the keyword
    (``usage=m.usage``). The value has to be an attribute named ``usage`` --
    passing `None`, or a bare dict nothing will ever file, is not metering.
    """
    candidates = list(node.args[2:3])
    candidates += [k.value for k in node.keywords if k.arg == _HOLDER]
    return any(isinstance(a, ast.Attribute) and a.attr == _HOLDER for a in candidates)


def _offenders():
    for path in sorted(ROUTES.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        calls = list(_generation_calls(ast.parse(src)))
        for node in calls:
            if _is_metered(node):
                continue
            others = [n for n in calls if n is not node]
            if guard_markers.marker_reason(MARKER, src, node, others) is None:
                yield f"{path.relative_to(ROUTES)}:{node.lineno}: {node.func.attr}()"


def test_every_generation_route_meters_what_it_spends():
    offenders = list(_offenders())
    assert not offenders, (
        "LLM call(s) in routes/ that file no ledger row — wrap them in a "
        "`with store.usage.meter(<task>, ...) as m:` and pass `m.usage`, or "
        "annotate the line with `# usage-ok: <why this one is not counted>`:\n  "
        + "\n  ".join(offenders))


def test_the_marker_is_not_a_rubber_stamp():
    """Every exemption is a call missing from every total, so they must stay few
    and must say why. A bare `# usage-ok:` is not a reason."""
    marked = []
    for path in sorted(ROUTES.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        calls = list(_generation_calls(ast.parse(src)))
        for node in calls:
            if _is_metered(node):
                continue
            others = [n for n in calls if n is not node]
            reason = guard_markers.marker_reason(MARKER, src, node, others)
            if reason is not None:
                marked.append((f"{path.relative_to(ROUTES)}:{node.lineno}", reason))

    unexplained = [loc for loc, reason in marked if len(reason) < 15]
    assert not unexplained, f"`usage-ok` with no real reason: {unexplained}"
    assert len(marked) <= 2, (
        f"{len(marked)} usage-ok exemptions; each is a call the ledger will "
        f"never see, so they need review rather than a raised limit: {marked}")


def test_the_guard_actually_detects_an_unmetered_call():
    """A guard that cannot fail reads as coverage without being any."""
    tree = ast.parse("client.complete(messages, conn)\n")
    assert len(list(_generation_calls(tree))) == 1
    assert not _is_metered(next(_generation_calls(tree)))

    for src in ("client.complete(messages, conn, m.usage)",
                "client.stream(messages, conn, usage=m.usage)",
                "client.stream(messages, conn, meter.usage)"):
        assert _is_metered(next(_generation_calls(ast.parse(src)))), src


def test_the_guard_is_not_fooled_by_a_holder_that_files_nothing():
    """`None` and a throwaway dict both satisfy the signature and neither ends
    up in the ledger -- which is the whole point of the rule."""
    for src in ("client.complete(messages, conn, None)",
                "client.complete(messages, conn, {})",
                "client.stream(messages, conn, usage=None)"):
        assert not _is_metered(next(_generation_calls(ast.parse(src)))), src


def test_the_guard_ignores_calls_that_are_not_generations():
    """`aclose` reaches no provider, and an unrelated object's `.complete()` is
    not this client's."""
    for src in ("client.aclose()", "job.complete(messages, conn)",
                "store.absorb.stream(messages, conn)"):
        assert list(_generation_calls(ast.parse(src))) == [], src


def test_the_guard_sees_the_call_sites_it_is_meant_to_cover():
    """Vacuous-pass insurance: if the receiver convention ever changes, this
    finds nothing and every other assertion here passes trivially."""
    found = sum(len(list(_generation_calls(ast.parse(p.read_text(encoding="utf-8")))))
                for p in ROUTES.rglob("*.py"))
    assert found >= 10, f"only {found} generation call sites found; did routes/ move?"
