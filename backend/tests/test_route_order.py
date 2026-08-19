"""The ordering rules the routes package has, checked instead of remembered.

FastAPI matches routes in registration order and never backtracks to a better
fit, so `routes.__init__`'s include order decides two things:

  * `/worlds/{wid}/{kind}` and `/campaigns/{cid}/{kind}` capture any third path
    segment, so a literal-segment route registered after them is unreachable;
  * where two patterns *cross* — neither more general, but some concrete URL
    matches both — whichever is registered first wins.

Before the package split the first rule lived in a dozen "declared before the
generic /{kind} routes" comments and the second was invisible. Both are checked
here.
"""

from __future__ import annotations

import importlib
import pkgutil

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


def _intersects(a: str, b: str) -> bool:
    """True if some concrete URL matches both patterns."""
    sa, sb = a.split("/"), b.split("/")
    if len(sa) != len(sb) or a == b:
        return False
    return all(x.startswith("{") or y.startswith("{") or x == y
               for x, y in zip(sa, sb))


def test_no_route_is_shadowed_by_an_earlier_one():
    table = _table()
    shadowed = []
    for i, (methods_a, path_a) in enumerate(table):
        for methods_b, path_b in table[i + 1:]:
            if methods_a & methods_b and _generalizes(path_a, path_b):
                shadowed.append(f"{sorted(methods_b)} {path_b} is unreachable: "
                                f"{sorted(methods_a)} {path_a} is registered first")
    assert not shadowed, "\n".join(shadowed)


# Pairs where neither pattern is more general than the other, but a concrete URL
# matches both — so which handler runs is decided by include order alone and
# nothing else would catch a change. Each entry is (winner, loser); the winners
# are the ones that were in effect before the routes package was split out
# (#241), so this pins existing behaviour rather than asserting a preference.
CROSSING_PAIRS = [
    ("/api/worlds/{wid}/sheets/{mid}/{kind}/{eid}",
     "/api/worlds/{wid}/{kind}/{eid}/images/{name}"),
    ("/api/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}",
     "/api/worlds/{wid}/characters/{cid}/tagline/generate"),
    # Same shape, same decision: only a character whose id is literally
    # "instantiate" could reach the crossing, and instantiate is registered
    # first, so it keeps winning.
    ("/api/worlds/{wid}/{kind}/instantiate/{mid}/{content_id}",
     "/api/worlds/{wid}/characters/{cid}/voice-anchor/generate"),
    # Same shape as the world-side voice-anchor crossing above: only a character
    # whose id is literally "instantiate" could reach it, and instantiate is
    # registered first, so it keeps winning.
    ("/api/campaigns/{cid}/characters/{char}/voice-anchor/generate",
     "/api/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}"),
    ("/api/campaigns/{cid}/scenes/{sid}/cast/batch",
     "/api/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}"),
    # Same shape and same decision as `cast/batch` directly above: only
    # `/campaigns/{cid}/scenes/instantiate/cast/emergent` matches both, and
    # "scenes" is not an entity kind, so the instantiate pattern can never
    # legitimately claim it. The scene route is registered first and wins.
    ("/api/campaigns/{cid}/scenes/{sid}/cast/emergent",
     "/api/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}"),
    # `.../scenes/instantiate/alternates/{id}` matches both. The scene route
    # wins, like every other five-segment scene route above: `{kind}` there is
    # an entity kind, and "scenes" is not one, so the instantiate pattern can
    # never legitimately claim a URL under /scenes/.
    ("/api/campaigns/{cid}/scenes/{sid}/alternates/{vid}",
     "/api/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}"),
    ("/api/campaigns/{cid}/scenes/{sid}/suggestions/dismiss",
     "/api/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}"),
    ("/api/campaigns/{cid}/sheets/{kind}/{eid}",
     "/api/campaigns/{cid}/{kind}/{eid}/images"),
    ("/api/campaigns/{cid}/sheets/{kind}/{eid}/creation",
     "/api/campaigns/{cid}/{kind}/{eid}/images/{name}"),
    ("/api/campaigns/{cid}/sheets/{kind}/{eid}/advance",
     "/api/campaigns/{cid}/{kind}/instantiate/{mid}/{content_id}"),
]


def test_crossing_routes_keep_their_winner():
    """Per method, not per path: two routes can share a path pattern with
    different method sets, so collapsing them would hide a one-method flip."""
    table = _table()
    for winner, loser in CROSSING_PAIRS:
        shared = set()
        for methods, path in table:
            if path == winner:
                shared |= methods
        loser_methods = set()
        for methods, path in table:
            if path == loser:
                loser_methods |= methods
        shared &= loser_methods
        assert shared, f"{winner} and {loser} no longer share a method"
        for method in sorted(shared):
            first = next(p for m, p in table if method in m and p in (winner, loser))
            assert first == winner, (
                f"{method} {winner} used to win over {loser} but is now registered "
                f"after it; a URL matching both now reaches the wrong handler")


def test_the_crossing_pair_list_is_complete():
    """A new route that crosses an existing one must be added to CROSSING_PAIRS
    (with a decision about which should win), not left ordered by luck."""
    table = _table()
    listed = {frozenset(p) for p in CROSSING_PAIRS}
    unlisted = []
    for i, (methods_a, path_a) in enumerate(table):
        for methods_b, path_b in table[i + 1:]:
            if (methods_a & methods_b and _intersects(path_a, path_b)
                    and not _generalizes(path_a, path_b)
                    and not _generalizes(path_b, path_a)
                    and frozenset((path_a, path_b)) not in listed):
                unlisted.append(
                    f"{path_a} crosses {path_b} ({sorted(methods_a & methods_b)})")
    assert not unlisted, (
        "these route patterns overlap ambiguously and are not pinned in "
        "CROSSING_PAIRS:\n" + "\n".join(sorted(set(unlisted))))


def test_no_duplicate_route_registrations():
    """The same (method, path) registered twice would silently make the second
    unreachable, and the overlap checks above skip identical paths."""
    seen, dupes = set(), []
    for methods, path in _table():
        for method in methods:
            if (method, path) in seen:
                dupes.append(f"{method} {path}")
            seen.add((method, path))
    assert not dupes, "duplicate registrations: " + ", ".join(sorted(set(dupes)))


def test_the_generic_entity_routes_are_included_last():
    """The include order in routes.__init__ is what keeps the rules above true;
    assert it directly so a re-ordered include fails here with a clear reason
    even if nothing happens to be shadowed yet."""
    generic = {r.path for r in entities.router.routes}
    table = [p for _, p in _table() if p.startswith("/api")]
    last_specific = max(i for i, p in enumerate(table) if p[4:] not in generic)
    first_generic = min(i for i, p in enumerate(table) if p[4:] in generic)
    assert first_generic > last_specific, (
        f"a non-generic route ({table[last_specific]}) is registered after the "
        f"generic /{{kind}} routes (first at {table[first_generic]})")


def test_every_domain_router_is_composed():
    """A module that nobody includes contributes no routes and no other test
    fails — so walk the package and check each router made it in."""
    import grimoire.routes as pkg

    composed = {(frozenset(m), p) for m, p in _table() if p.startswith("/api")}
    found = 0
    for info in pkgutil.iter_modules(pkg.__path__):
        mod = importlib.import_module(f"{pkg.__name__}.{info.name}")
        sub = getattr(mod, "router", None)
        if sub is None:  # common / models / streaming hold no routes
            continue
        found += 1
        for route in sub.routes:
            assert (frozenset(route.methods), f"/api{route.path}") in composed, \
                f"{mod.__name__} route {route.path} is not in the composed router"
    assert found >= 10, f"only found {found} domain routers; did the package move?"
    assert len(router.routes) > 0
