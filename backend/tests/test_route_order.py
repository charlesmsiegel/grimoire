"""The one ordering rule the routes package has, checked instead of remembered.

``/worlds/{wid}/{kind}`` and ``/campaigns/{cid}/{kind}`` capture any third path
segment, so a literal-segment route registered after them is unreachable —
FastAPI matches in registration order and never backtracks to a better fit.
Before the package split that rule lived in a dozen "declared before the
generic /{kind} routes" comments; now ``routes.__init__`` includes
``entities`` last and this test fails if anything ends up shadowed.
"""

from __future__ import annotations

from grimoire.main import app
from grimoire.routes import entities, router


def _table() -> list[tuple[frozenset[str], str]]:
    """Every route in the order FastAPI will try to match it."""
    def flatten(routes):
        out = []
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":  # lazily expanded include
                out.extend(flatten(r.effective_candidates()))
            elif hasattr(r, "methods") and hasattr(r, "path"):
                out.append((frozenset(r.methods), r.path))
        return out

    return flatten(app.routes)


def _generalizes(a: str, b: str) -> bool:
    """True if pattern `a` matches every path `b` matches, and is strictly
    looser — i.e. `a` has a `{param}` where `b` has a literal."""
    sa, sb = a.split("/"), b.split("/")
    if len(sa) != len(sb):
        return False
    looser = False
    for x, y in zip(sa, sb):
        if x.startswith("{"):
            if not y.startswith("{"):
                looser = True
            continue
        if x != y:
            return False
    return looser


def test_no_route_is_shadowed_by_an_earlier_one():
    table = _table()
    shadowed = []
    for i, (methods_a, path_a) in enumerate(table):
        for methods_b, path_b in table[i + 1:]:
            if methods_a & methods_b and _generalizes(path_a, path_b):
                shadowed.append(f"{sorted(methods_b)} {path_b} is unreachable: "
                                f"{sorted(methods_a)} {path_a} is registered first")
    assert not shadowed, "\n".join(shadowed)


def test_the_generic_entity_routes_are_included_last():
    """The include order in routes.__init__ is what keeps the rule above true;
    assert it directly so a re-ordered include fails here with a clear reason
    even if no literal route happens to be shadowed yet."""
    generic = {r.path for r in entities.router.routes}
    table = [p for _, p in _table()]
    last_specific = max(i for i, p in enumerate(table) if f"/api{p[4:]}" and p.startswith("/api")
                        and p[4:] not in generic)
    first_generic = min(i for i, p in enumerate(table)
                        if p.startswith("/api") and p[4:] in generic)
    assert first_generic > last_specific, (
        f"a non-generic route ({table[last_specific]}) is registered after the "
        f"generic /{{kind}} routes (first at {table[first_generic]})")


def test_every_domain_router_is_composed():
    """A new module that nobody includes contributes no routes and no test
    fails — so check the assembled router carries every submodule's routes."""
    from grimoire.routes import (campaigns, characters, config, greetings, mechanics,
                                 modules, scenes, weather, worlds)

    composed = {(frozenset(m), p) for m, p in _table() if p.startswith("/api")}
    for mod in (config, modules, worlds, characters, greetings, campaigns, scenes,
                weather, mechanics, entities):
        for route in mod.router.routes:
            assert (frozenset(route.methods), f"/api{route.path}") in composed, \
                f"{mod.__name__} route {route.path} is not in the composed router"
    assert len(router.routes) > 0
