"""The route-graph memo (`tests/route_memo.py`) changes how often FastAPI
analyses a route, and nothing about what the analysis produces.

Two properties make that true, and each is held here rather than argued:

- **Equivalence.** Every graph the memo holds after a real app has matched a
  request against every router equals what the unmemoised builder produces for
  the same arguments, field by field, recursively.
- **Isolation.** Apps sharing memoised graphs still resolve their own
  dependency overrides: an override is looked up per request through the app,
  never stored on the graph, so one app's override cannot reach another.

On a FastAPI without the seam (the Pydantic 1 gate pins one that builds routes
eagerly) the memo is not installed, and what is checked is exactly that.
"""

from __future__ import annotations

import dataclasses

import fastapi.routing
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from grimoire.main import create_app
from tests import route_memo


def _field(field) -> tuple:
    info = getattr(field, "field_info", None)
    return (getattr(field, "name", None), getattr(field, "mode", None),
            repr(getattr(info, "annotation", None)), getattr(info, "alias", None),
            repr(getattr(info, "default", None)), type(info).__name__)


def _graph(dependant) -> tuple:
    """A dependant as plain, comparable data: every dataclass field, with
    parameter fields and sub-dependants rendered recursively."""
    out = []
    for f in dataclasses.fields(dependant):
        value = getattr(dependant, f.name)
        if f.name == "dependencies":
            value = tuple(_graph(d) for d in value)
        elif f.name.endswith("_params"):
            value = tuple(_field(p) for p in value)
        elif isinstance(value, list):
            value = tuple(value)
        out.append((f.name, value))
    return tuple(out)


def _route_contexts(app) -> list:
    """Every API route context the app builds when a request reaches each of
    its routers -- what a request would be served, built the way it would be."""
    found = []

    def walk(router):
        for candidate in router.effective_candidates():
            if isinstance(candidate, fastapi.routing._IncludedRouter):
                walk(candidate)
            elif getattr(candidate, "dependant", None) is not None:
                found.append(candidate)

    for route in app.router.routes:
        if isinstance(route, fastapi.routing._IncludedRouter):
            walk(route)
    return found


def test_the_memo_is_on_exactly_where_fastapi_has_the_seam():
    assert route_memo.active() == route_memo.available()
    if route_memo.available():
        assert getattr(fastapi.routing, route_memo.TARGET) is route_memo._memoised


def test_every_memoised_graph_equals_a_fresh_build():
    """Read off what the app was actually handed, not off the memo's own
    keys: a memo that keyed too coarsely would serve one route another's
    graph, and only a comparison from the route's side would see it."""
    if not route_memo.active():
        assert not route_memo.available()
        return
    _route_contexts(create_app())                  # first app: fills the memo
    hits = route_memo.stats["hits"]
    contexts = _route_contexts(create_app())       # second: served from it
    assert route_memo.stats["hits"] > hits, "the second app never reached the memo"
    assert len(contexts) > 100, "the app's routes were never built"
    build = route_memo.original()
    differ = []
    for ctx in contexts:
        fresh, *_ = build(path=ctx.path_format, call=ctx.endpoint,
                          dependencies=list(ctx.dependencies))
        if _graph(ctx.dependant) != _graph(fresh):
            differ.append(f"{sorted(ctx.methods)} {ctx.path}")
    assert differ == [], f"routes served a graph that differs from a fresh build: {differ[:10]}"


def test_apps_sharing_a_graph_still_resolve_their_own_overrides():
    def dep() -> str:
        return "real"

    router = APIRouter()

    @router.get("/who")
    def who(value: str = Depends(dep)) -> dict:
        return {"value": value}

    def app_with(override):
        app = FastAPI()
        app.include_router(router)
        if override is not None:
            app.dependency_overrides[dep] = lambda: override
        return app

    before = dict(route_memo.stats)
    with TestClient(app_with("A")) as a, TestClient(app_with("B")) as b, \
            TestClient(app_with(None)) as plain:
        seen = [c.get("/who").json()["value"] for c in (a, b, plain, a, b)]
    assert seen == ["A", "B", "real", "A", "B"]
    if route_memo.active():
        # ...and the second and third apps really were served the first's graph.
        assert route_memo.stats["hits"] >= before["hits"] + 2


def test_one_endpoint_under_two_routers_keeps_each_routers_dependencies():
    """The same path and endpoint, included with different router-level
    dependencies, is two graphs -- so the dependencies are part of the key.
    Grimoire's own app never includes a route twice, which is why the
    equivalence test above cannot see this on its own."""
    def open_door() -> None:
        return None

    def locked_door() -> None:
        raise HTTPException(status_code=403, detail="locked")

    router = APIRouter()

    @router.get("/door")
    def door() -> dict:
        return {"through": True}

    def app_behind(guard):
        app = FastAPI()
        app.include_router(router, dependencies=[Depends(guard)])
        return app

    with TestClient(app_behind(open_door)) as opened, \
            TestClient(app_behind(locked_door)) as locked:
        assert opened.get("/door").status_code == 200
        assert locked.get("/door").status_code == 403


def test_the_switch_turns_it_off(monkeypatch):
    was_on = route_memo.active()
    route_memo.uninstall()
    try:
        monkeypatch.setenv(route_memo.ENV, "0")
        assert route_memo.install() is False
        assert not route_memo.active()
        # A default: on a FastAPI without the seam there is no attribute at all.
        assert getattr(fastapi.routing, route_memo.TARGET, None) is not route_memo._memoised
    finally:
        monkeypatch.delenv(route_memo.ENV)
        if was_on:
            assert route_memo.install() is True
