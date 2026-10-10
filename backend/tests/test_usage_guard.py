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
  `backend/scripts/`). The one outside caller that files no ledger row today
  is named, with why, in `UNMETERED_OUTSIDE`: an entry is an exemption, so
  the list is capped, and a stale one fails. (A live eval is metered into
  its throwaway home, spec 01a.)
"""

from __future__ import annotations

import ast
import pathlib

import pytest

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
#: why. Not a marker family: one module, named here, held to having such a
#: call (`test_the_unmetered_outside_list_is_not_stale`). A live eval is
#: metered into its throwaway home (spec 01a, C2), so `evals.runner` is not
#: one.
UNMETERED_OUTSIDE: dict[str, str] = {
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
    assert len(UNMETERED_OUTSIDE) <= 1


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


# ---- "was a request sent?" is asked one way (01g-S3) ----
#: The one module allowed to read a holder's emptiness: it defines the test.
SENT_MODULE = "grimoire.store.usage"
#: How the package spells a usage holder: an attribute or a bare name (or a
#: subscript of one) called one of these -- `m.usage`, `usage`, `holder`,
#: `holders[i]`.
HOLDER_NAMES = frozenset({_HOLDER, "holder", "holders"})
#: Receivers whose `.usage` is NOT a holder, exempt by name: `store.usage`
#: is the ledger module, and `decisions.Decision.usage` is a tuple of ledger
#: rows, bound as `decision` / `decided` / `got` wherever it is read. A
#: binding added under another name is flagged until it is listed here.
NOT_HOLDER_RECEIVERS = frozenset({"store", "decision", "decided", "got"})


def _is_holder(node: ast.AST) -> bool:
    if isinstance(node, ast.Subscript):
        return _is_holder(node.value)
    if isinstance(node, ast.Name):
        return node.id in HOLDER_NAMES
    if isinstance(node, ast.Attribute):
        receiver = node.value
        return (node.attr in HOLDER_NAMES
                and not (isinstance(receiver, ast.Name)
                         and receiver.id in NOT_HOLDER_RECEIVERS))
    return False


def _is_empty_dict(node: ast.AST) -> bool:
    """`{}` or `dict()`."""
    return ((isinstance(node, ast.Dict) and not node.keys)
            or (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "dict" and not node.args and not node.keywords))


def _truth_tested(node: ast.AST):
    """The expressions whose truth value (or length) `node` takes."""
    if isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)):
        yield node.test
    elif isinstance(node, ast.comprehension):
        yield from node.ifs
    elif isinstance(node, ast.BoolOp):
        # `holder or {}` is a None default, not a question about emptiness.
        if not (isinstance(node.op, ast.Or) and _is_empty_dict(node.values[-1])):
            yield from node.values
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        yield node.operand
    elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
          and node.func.id in ("bool", "len") and node.args):
        yield node.args[0]


def _compares_to_empty(node: ast.AST) -> bool:
    """`<holder> == {}`, `!= dict()`, either way round."""
    if not (isinstance(node, ast.Compare) and len(node.ops) == 1
            and isinstance(node.ops[0], (ast.Eq, ast.NotEq))):
        return False
    left, right = node.left, node.comparators[0]
    return ((_is_holder(left) and _is_empty_dict(right))
            or (_is_holder(right) and _is_empty_dict(left)))


def _tested_holder(node: ast.AST) -> ast.AST | None:
    """The holder expression whose emptiness `node` takes, or None."""
    if _compares_to_empty(node):
        return node
    return next((e for e in _truth_tested(node) if _is_holder(e)), None)


def _emptiness_tests(tree: ast.AST, modname: str) -> list[str]:
    """A holder's emptiness taken as an answer: its truth value (`if`,
    `while`, a ternary, `assert`, a comprehension's `if`, `not`, `and`/`or`,
    `bool()`), its `len()`, or a comparison with `{}` / `dict()`. Each reads
    "the holder is non-empty" as "a request went out", which a holder seeded
    before the call (`store.usage.PRE_SEND_KEYS`: a run id, a reasoning
    buffer) makes false. Ask `store.usage.sent`.

    Honest about its reach: a holder is recognised by its NAME (see
    `HOLDER_NAMES`), so one bound under another name is not seen; and
    `x or {}`, the None default, is allowed."""
    if modname == SENT_MODULE:
        return []
    # The holder's own line: a comprehension clause has none of its own.
    return [f"{modname}:{held.lineno}" for node in ast.walk(tree)
            if (held := _tested_holder(node)) is not None]


def test_no_holder_emptiness_test_outside_the_ledger():
    offenders = [o for modname, tree, _pkg in _walk() for o in _emptiness_tests(tree, modname)]
    assert not offenders, (
        "a holder's emptiness read as 'sent' -- a meter seeded with a run id is "
        "non-empty before anything goes out; ask `store.usage.sent(m.usage)`:\n  "
        + "\n  ".join(offenders))


@pytest.mark.parametrize("src", [
    "sent = bool(m.usage)\n", "x = a if m.usage else b\n",
    "if m.usage:\n    pass\n", "if not meter.usage:\n    pass\n",
    "if m.usage and y:\n    pass\n", "sent = m.usage and True\n",
    "n = len(m.usage)\n", "x = m.usage == {}\n", "x = {} != m.usage\n",
    "x = m.usage == dict()\n", "assert m.usage\n", "xs = [h for h in hs if m.usage]\n",
    "if usage:\n    pass\n", "if not usage:\n    pass\n", "x = bool(holder)\n",
    "if holders[i]:\n    pass\n", "while holder:\n    pass\n",
])
def test_the_emptiness_check_flags_each_planted_shape(src):
    assert _emptiness_tests(ast.parse(src), "grimoire.inference"), src


@pytest.mark.parametrize("src", [
    "sent = store.usage.sent(m.usage)\n", "if m.usage is None:\n    pass\n",
    "if usage is None:\n    pass\n", "x = m.usage.get('model')\n",
    "x = (usage or {}).get('k')\n", "x = _served_by(holder or {})\n",
    "if got.usage:\n    pass\n", "n = len(decision.usage)\n",
    "if store.usage:\n    pass\n", "x = m.usage == other\n",
])
def test_the_emptiness_check_passes_what_is_not_one(src):
    assert _emptiness_tests(ast.parse(src), "grimoire.inference") == [], src


def test_the_ledger_module_may_ask_its_own_question():
    assert _emptiness_tests(ast.parse("if not self.usage:\n    pass\n"), SENT_MODULE) == []
