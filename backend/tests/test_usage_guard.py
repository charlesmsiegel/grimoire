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
- **`inference.generate` forwards its caller's holder** (slice I). Its
  facade calls (`stream` and `complete`) pass its own `usage` parameter, not a meter of their own:
  the meter belongs at the call site, around the generation, where a route
  opens it. So those two calls are `FORWARDERS`, and the rule moves to the
  door instead -- every `generate` operation call (the operation guard's
  recogniser, through a binding of `grimoire.inference`) passes
  `usage=<meter>.usage`, in the package and outside it (`evals/`,
  `backend/scripts/`). The two outside callers file no ledger row today and
  are named, with why, in `UNMETERED_OUTSIDE`: an entry is an exemption, so
  the list is capped, and a stale one fails.
"""

from __future__ import annotations

import ast
import pathlib

import grimoire.inference as inference_mod
import grimoire.routes as routes_pkg

from . import guard_markers
from .test_operation_guard import (
    CLIENT_MODULE,
    _walk,
    client_calls,
    generate_calls,
    outside_walk,
)

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

#: Functions whose facade calls forward the CALLER's holder rather than open a
#: meter: `{file under the package: function names}`. Such a call is metered
#: where the function is called, which `test_every_generate_call_passes_a_
#: meters_holder` checks.
FORWARDERS: dict[str, tuple[str, ...]] = {"inference.py": ("generate",)}


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


def _forwarded(tree: ast.Module, path: pathlib.Path) -> set[int]:
    """The ids of the facade calls in `path` that a `FORWARDERS` function
    makes with its own `usage` parameter as the holder."""
    names = FORWARDERS.get(_where(path).as_posix(), ())
    out: set[int] = set()
    for fn in tree.body:
        if not (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in names):
            continue
        params = {a.arg for a in (*fn.args.posonlyargs, *fn.args.args, *fn.args.kwonlyargs)}
        if _HOLDER not in params:
            continue
        for node in _generation_calls(fn):
            given = [*node.args[2:3], *(k.value for k in node.keywords if k.arg == _HOLDER)]
            if any(isinstance(a, ast.Name) and a.id == _HOLDER for a in given):
                out.add(id(node))
    return out


def _offenders():
    for path in _sources():
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = list(_generation_calls(tree))
        forwarded = _forwarded(tree, path)
        for node in calls:
            if _is_metered(node) or id(node) in forwarded:
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
    finds nothing and every other assertion here passes trivially. Since
    slice I a route generates through `inference.generate`, so what is
    counted is both: the facade calls left in `routes/` (the model test's)
    and the `generate` calls the holder check reads."""
    direct = sum(len(list(_generation_calls(ast.parse(p.read_text(encoding="utf-8")))))
                 for p in ROUTES.rglob("*.py"))
    through = sum(len(generate_calls(tree, modname, is_pkg))
                  for modname, tree, is_pkg in _walk() if modname.startswith("grimoire.routes"))
    assert direct >= 2, f"only {direct} direct calls found; did the model test move?"
    assert direct + through >= 10, (
        f"only {direct + through} generation call sites found; did routes/ move?")


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
    """Vacuity insurance for `EXTRA_SOURCES`: `decide`'s facade calls are
    seen, and each hands over its meter's holder -- all but `generate`'s,
    which forward their caller's (`FORWARDERS`)."""
    (path,) = EXTRA_SOURCES
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = list(_generation_calls(tree))
    forwarded = _forwarded(tree, path)
    assert {c.func.attr for c in calls if id(c) in forwarded} == {"stream", "complete"}, (
        "generate's stream and complete were not both found")
    own = [c for c in calls if id(c) not in forwarded]
    assert own and all(_is_metered(c) for c in own), path


def test_a_forwarder_forwards_only_its_own_holder():
    """`FORWARDERS` clears a call that passes the function's `usage`
    parameter, and nothing else: a call inside it with no holder, or with a
    name that is not its parameter, is still an offender."""
    path = EXTRA_SOURCES[0]
    src = ("def generate(task, messages, *, client, resolved, usage=None):\n"
           "    client.stream(messages, conn, usage)\n"
           "    client.complete(messages, conn, usage=usage)\n"
           "    client.complete(messages, conn)\n"
           "    client.stream(messages, conn, holder)\n"
           "def other(usage):\n"
           "    client.stream(messages, conn, usage)\n")
    tree = ast.parse(src)
    forwarded = _forwarded(tree, path)
    lines = sorted(n.lineno for n in _generation_calls(tree) if id(n) in forwarded)
    assert lines == [2, 3]


def _unmetered_generates(tree: ast.AST, modname: str, is_pkg: bool = False) -> list[str]:
    """`generate` operation calls that hand it no meter's holder: `usage=`
    must be an attribute named `usage` (`m.usage`, `meter.usage`)."""
    return [f"{modname}:{call.lineno}: generate()"
            for call in generate_calls(tree, modname, is_pkg)
            if not any(k.arg == _HOLDER and isinstance(k.value, ast.Attribute)
                       and k.value.attr == _HOLDER for k in call.keywords)]


#: Outside the package, the modules whose generations file no ledger row, and
#: why. Not a marker family: two modules, named here, each held to having
#: such a call (`test_the_unmetered_outside_list_is_not_stale`).
UNMETERED_OUTSIDE: dict[str, str] = {
    "evals.runner": "a live eval runs against a throwaway store, never the "
                    "library whose ledger it would describe",
    "scripts.ingest_scene": "the ingest script has never filed a ledger row; "
                            "starting to changes what an ingest records",
}


def test_every_generate_call_passes_a_meters_holder():
    offenders = [o for modname, tree, is_pkg in (*_walk(), *outside_walk())
                 if modname not in UNMETERED_OUTSIDE
                 for o in _unmetered_generates(tree, modname, is_pkg)]
    assert not offenders, (
        "generation(s) that file no ledger row -- open `with store.usage.meter(<task>, "
        "...) as m:` around the call and pass `usage=m.usage`:\n  " + "\n  ".join(offenders))


def test_the_unmetered_outside_list_is_not_stale():
    """Each exemption names a module that still generates unmetered, and the
    list stays as short as it is: an exemption is a hole in the total."""
    unmetered = {modname for modname, tree, is_pkg in outside_walk()
                 if _unmetered_generates(tree, modname, is_pkg)}
    assert unmetered == set(UNMETERED_OUTSIDE)
    assert len(UNMETERED_OUTSIDE) <= 2


def test_the_generate_holder_check_flags_planted_calls():
    where = "grimoire.routes.scenes"
    ops = "from .. import inference as operations\n"
    for src in (ops + "operations.generate('chat', m, client=c, resolved=r)\n",
                ops + "operations.generate('chat', m, client=c, resolved=r, usage=None)\n",
                ops + "operations.generate('chat', m, client=c, resolved=r, usage={})\n",
                ops + "operations.generate('chat', m, client=c, resolved=r, usage=holder)\n"):
        assert _unmetered_generates(ast.parse(src), where), src
    for src in (ops + "operations.generate('chat', m, client=c, resolved=r, usage=m.usage)\n",
                ops + ("operations.generate('tagline', m, client=c, resolved=r,\n"
                       "                    usage=meter.usage, stream=False)\n")):
        assert _unmetered_generates(ast.parse(src), where) == [], src
