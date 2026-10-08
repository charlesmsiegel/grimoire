"""Build each route's dependency graph once per test process, not once per app.

Every route test builds a fresh app (`conftest.py`'s `client`), and that is
deliberate: the run registry, the lifespan and every dependency override live
on the app, and sharing one between tests is the isolation the suite exists to
keep. But FastAPI (from the version that includes routers lazily) analyses
every route of a router the first time a new app matches a request against it
-- each endpoint's signature, each parameter's pydantic `TypeAdapter` -- and
with several hundred routes that analysis was most of what a route test spent:
measured at 60-85% of a route-test file's session time, before any test had
run a line of its own (see `docs/superpowers/validation/`).

The expensive step is one private FastAPI function,
`fastapi.routing._build_dependant_with_parameterless_dependencies(path, call,
dependencies)`. Its answer depends on nothing but its three arguments: the
path, the endpoint function, and the `Depends` markers the route and its
router carry (frozen dataclasses, so they compare by value). It never sees the
app -- dependency overrides are looked up per request, through the app, by
`solve_dependencies`, not stored on the graph -- and the one mutation it makes
(prepending the parameterless dependencies) happens before it returns. So the
graph it returns for the same three arguments is the same graph, whichever app
asked; memoising it changes how often FastAPI does that work and nothing about
what it produces. `test_route_memo.py` holds both halves of that: every
memoised graph equals a fresh build, and two apps with different overrides
still each get their own.

Test-only and opt-out:

- Installed by `conftest.py` at import, before any app is built, and only when
  FastAPI has that function with exactly that signature. An older FastAPI (the
  Pydantic 1 gate pins one) builds routes eagerly, through code with no such
  seam, and simply runs without it.
- `GRIMOIRE_TEST_ROUTE_MEMO=0` turns it off, which is the whole rollback: the
  suite then builds every route for every app, as it did before.
- An unhashable dependency marker (a `Security` with a list of scopes) is
  built fresh, never cached.

A test that COUNTS FastAPI's analyses (`test_route_order.py`'s first-request
test) must count with the memo bypassed -- `monkeypatch.setattr(fastapi.routing,
TARGET, original())` -- or it counts the memo's misses, which depend on what
earlier tests in the same worker happened to analyse.

Production builds one app per process and pays the analysis once; nothing in
`backend/src` changes.
"""

from __future__ import annotations

import inspect
import os

import fastapi.routing

#: The FastAPI function memoised, and the keyword-only signature it must have
#: for the memo to be sound. A different signature means FastAPI changed what
#: the graph depends on, and guessing at that is how a cache serves a stale
#: graph -- so it is left alone.
TARGET = "_build_dependant_with_parameterless_dependencies"
SIGNATURE = ("path", "call", "dependencies")

#: The switch: anything but "0" leaves the memo on.
ENV = "GRIMOIRE_TEST_ROUTE_MEMO"

#: The unmemoised builder while the memo is installed, else None.
_state: dict = {"original": None}
_cache: dict = {}
stats = {"hits": 0, "misses": 0, "uncacheable": 0}


def available() -> bool:
    """Whether this FastAPI has the seam, with the signature the memo assumes."""
    target = getattr(fastapi.routing, TARGET, None)
    if target is None:
        return False
    params = inspect.signature(target).parameters.values()
    return (tuple(p.name for p in params) == SIGNATURE
            and all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params))


def original():
    """The unmemoised builder, for comparing against."""
    return _state["original"] or getattr(fastapi.routing, TARGET)


def _memoised(*, path, call, dependencies):
    try:
        key = (path, call, tuple(dependencies))
        hash(key)
    except TypeError:
        stats["uncacheable"] += 1
        return _state["original"](path=path, call=call, dependencies=dependencies)
    built = _cache.get(key)
    if built is None:
        stats["misses"] += 1
        built = _cache[key] = _state["original"](path=path, call=call, dependencies=dependencies)
    else:
        stats["hits"] += 1
    return built


def install() -> bool:
    """Memoise the builder for this process. Returns whether it is on."""
    if _state["original"] is not None:
        return True
    if os.environ.get(ENV, "1") == "0" or not available():
        return False
    _state["original"] = getattr(fastapi.routing, TARGET)
    setattr(fastapi.routing, TARGET, _memoised)
    return True


def uninstall() -> None:
    """Put the original back and forget every cached graph."""
    if _state["original"] is not None:
        setattr(fastapi.routing, TARGET, _state["original"])
        _state["original"] = None
    _cache.clear()


def active() -> bool:
    return _state["original"] is not None


def cached() -> dict:
    """The memo's contents: (path, call, dependencies) -> what it returned."""
    return dict(_cache)
