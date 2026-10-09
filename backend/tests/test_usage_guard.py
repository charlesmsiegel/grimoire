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
- **The generation checks scan only ``routes/``, and ``EXTRA_SOURCES``.**
  Nothing else in the package holds an ``LLMClient`` except the operations
  module (``grimoire/inference.py``), whose ``decide`` opens the meter itself,
  so "decide is metered" (spec 14.1) is checked here too. The adapters
  underneath take a holder from the facade, and the facade is covered by its
  own tests. **The embeddings check walks the
  whole package**, because store modules hold the embeddings client: every
  request to it that is not the embed operation (`test_operation_guard.py`'s
  recogniser, imported so there is one) passes ``usage=`` a meter's holder.
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

import grimoire.inference as inference_mod
import grimoire.routes as routes_pkg

from . import guard_markers
from .test_operation_guard import CLIENT_MODULE, _walk, client_calls

ROUTES = pathlib.Path(routes_pkg.__file__).parent
#: Files outside `routes/` that hold an `LLMClient` and are scanned beside it.
EXTRA_SOURCES = (pathlib.Path(inference_mod.__file__),)

#: The `LLMClient` methods that reach a provider. `aclose` does not. `single`
#: is the model test call's one attempt (slice B Task 9), and `decide_native`
#: a native decisions endpoint's (slice H): each spends money like the other
#: two, so each is held to the same rule.
_GENERATORS = ("stream", "complete", "single", "decide_native")
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


def _sources():
    """Every scanned file: `routes/`, then `EXTRA_SOURCES`."""
    return [*sorted(ROUTES.rglob("*.py")), *EXTRA_SOURCES]


def _where(path: pathlib.Path) -> pathlib.Path:
    return path.relative_to(ROUTES.parent)


def _offenders():
    for path in _sources():
        src = path.read_text(encoding="utf-8")
        calls = list(_generation_calls(ast.parse(src)))
        for node in calls:
            if _is_metered(node):
                continue
            others = [n for n in calls if n is not node]
            if guard_markers.marker_reason(MARKER, src, node, others) is None:
                yield f"{_where(path)}:{node.lineno}: {node.func.attr}()"


def test_every_generation_route_meters_what_it_spends():
    offenders = list(_offenders())
    assert not offenders, (
        "LLM call(s) in routes/ or EXTRA_SOURCES that file no ledger row — wrap them in a "
        "`with store.usage.meter(<task>, ...) as m:` and pass `m.usage`, or "
        "annotate the line with `# usage-ok: <why this one is not counted>`:\n  "
        + "\n  ".join(offenders))


def test_the_marker_is_not_a_rubber_stamp():
    """Every exemption is a call missing from every total, so they must stay few
    and must say why. A bare `# usage-ok:` is not a reason."""
    marked = []
    for path in _sources():
        src = path.read_text(encoding="utf-8")
        calls = list(_generation_calls(ast.parse(src)))
        for node in calls:
            if _is_metered(node):
                continue
            others = [n for n in calls if n is not node]
            reason = guard_markers.marker_reason(MARKER, src, node, others)
            if reason is not None:
                marked.append((f"{_where(path)}:{node.lineno}", reason))

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
                "client.stream(messages, conn, meter.usage)",
                "client.single(messages, conn, m.usage)"):
        assert _is_metered(next(_generation_calls(ast.parse(src)))), src
    unmetered = ast.parse("client.single(messages, conn)\n")
    assert not _is_metered(next(_generation_calls(unmetered)))


def test_the_guard_catches_an_unmetered_native_decision():
    """Slice H: a native decision reaches a provider, so a planted
    `client.decide_native(item, conn)` with no meter is an offender, and the
    chain's own spelling (`m.usage` third, `retries=` beside it) is not."""
    planted = ast.parse("client.decide_native(item, conn)\n")
    assert not _is_metered(next(_generation_calls(planted)))
    for src in ("client.decide_native(item, conn, m.usage, retries=call.retries)",
                "client.decide_native(item, conn, usage=m.usage)"):
        assert _is_metered(next(_generation_calls(ast.parse(src)))), src
    found = list(_generation_calls(ast.parse(
        pathlib.Path(inference_mod.__file__).read_text(encoding="utf-8"))))
    assert any(node.func.attr == "decide_native" for node in found), (
        "inference.py makes no client.decide_native call the guard can see")


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


# ---- the embeddings client (slice D, spec 14.1: embed is metered) ----

def _embed_is_metered(node: ast.Call) -> bool:
    """Whether a client `.embed(...)` hands it a holder: the keyword ``usage=``,
    an attribute named ``usage``. Keyword only -- the client's third positional
    is the key, not a holder."""
    return any(k.arg == _HOLDER and isinstance(k.value, ast.Attribute)
               and k.value.attr == _HOLDER for k in node.keywords)


def _unmetered_embeds(tree: ast.AST, modname: str, is_pkg: bool = False) -> list[str]:
    if modname == CLIENT_MODULE:
        return []
    return [f"{modname}:{call.lineno}: .embed()"
            for call in client_calls(tree, modname, is_pkg) if not _embed_is_metered(call)]


def test_every_embeddings_request_is_metered():
    offenders = [o for modname, tree, is_pkg in _walk()
                 for o in _unmetered_embeds(tree, modname, is_pkg)]
    assert not offenders, (
        "embeddings request(s) that file no ledger row -- embed through "
        "`store.inference.embed.embed_sync`, or pass `usage=m.usage` from a "
        "`store.usage.meter(...)`:\n  " + "\n  ".join(offenders))


def test_the_embeddings_check_flags_and_passes_planted_cases():
    where = "grimoire.store.semsearch"
    for src in ("_CLIENT.embed(texts, m, k, u)\n",
                "_CLIENT.embed(texts, m, k, u, usage=None)\n",
                "_CLIENT.embed(texts, m, k.usage, u)\n"):
        assert _unmetered_embeds(ast.parse(src), where), src
    for src in ("_CLIENT.embed(texts, m, k, u, usage=m.usage)\n",
                # An operation call, not a client call: the operation meters.
                ("from .. import store\n"
                 "store.inference.embed.embed('semantic-search', t, space=s, client=c)\n"),
                ("from .inference import embed\n"
                 "embed.embed_sync('semantic-search', t, space=s, client=c)\n")):
        assert _unmetered_embeds(ast.parse(src), where) == [], src


def test_the_extra_sources_are_scanned():
    """Vacuity insurance for `EXTRA_SOURCES`: `decide`'s facade call is seen,
    and it hands over its meter's holder."""
    (path,) = EXTRA_SOURCES
    calls = list(_generation_calls(ast.parse(path.read_text(encoding="utf-8"))))
    assert calls and all(_is_metered(c) for c in calls), path
