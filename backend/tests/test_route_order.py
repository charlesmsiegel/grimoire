"""Router initialization cost and lifecycle validation."""

from __future__ import annotations

import contextlib

import fastapi.routing
import pytest
from fastapi import APIRouter

from grimoire import routes
from tests import route_memo


def test_a_first_request_analyses_only_the_domains_it_walks(client, monkeypatch):
    """Where includes nest, FastAPI analyses a branch's routes on the first
    request that walks into it. The aggregate was one flat branch, so the first
    request a fresh backend served -- a world's character list, from a tab left
    open across a restart -- paid for all ~450 routes before it was answered,
    over twice as long as the list itself. Counted in analyses rather than
    time: `get_dependant` runs once per route and once per sub-dependency. On
    a FastAPI that analyses at include time both counts are zero, which holds
    the same property.

    Counted with the suite's route memo (`tests/route_memo.py`) bypassed for
    this test: with it on, `get_dependant` runs only for routes no earlier test
    in this process analysed, so the counts would measure the test order --
    which xdist varies -- rather than FastAPI. On CI, a worker whose earlier
    tests had warmed most of the later routes counted (46, 110)."""
    if route_memo.active():
        monkeypatch.setattr(fastapi.routing, route_memo.TARGET, route_memo.original())
    calls: list[int] = []
    real = fastapi.routing.get_dependant

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(fastapi.routing, "get_dependant", counting)
    assert client.get("/api/config").status_code == 200
    first = len(calls)
    # A path nothing matches walks every branch, analysing all the rest.
    assert client.get("/api/no/route/is/shaped/like/this").status_code == 404
    rest = len(calls) - first
    assert first * 4 <= rest, (first, rest)


@pytest.mark.parametrize("kwargs", ["on_startup", "on_shutdown", "lifespan"])
def test_a_domain_router_that_needs_include_router_is_refused(kwargs):
    """What composing by reference would silently drop -- a router's own event
    handlers and lifespan, the only things `include_router` copies off the
    router itself -- is refused loudly instead. (Refused before anything is
    appended, so this leaves the real aggregate alone.)"""
    @contextlib.asynccontextmanager
    async def lifespan(app):
        yield

    arg = {"lifespan": lifespan} if kwargs == "lifespan" else {kwargs: [lambda: None]}
    before = len(routes.router.routes)
    with pytest.raises(RuntimeError, match="include_router"):
        routes._compose(APIRouter(**arg))
    assert len(routes.router.routes) == before


def test_the_refusal_reads_the_domain_router_not_the_aggregate(monkeypatch):
    """An `include_router` into the aggregate -- the right tool for a router
    that does need one -- replaces the aggregate's lifespan with a merged one.
    A check that compared each domain router against the aggregate's lifespan
    would then refuse every plain router composed after it, naming a lifespan
    that router does not have."""
    aggregate = APIRouter()
    aggregate.include_router(APIRouter(prefix="/elsewhere"))
    monkeypatch.setattr(routes, "router", aggregate)

    plain = APIRouter()

    @plain.get("/plain")
    def _plain() -> None:
        return None

    routes._compose(plain)
    last = aggregate.routes[-1]
    assert (last.original_router is plain) if routes._NESTED else (last is plain.routes[0])
