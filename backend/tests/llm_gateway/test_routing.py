"""Tests for the route resolver."""

from __future__ import annotations

import pytest

from grimoire.llm_gateway.errors import RouteNotFoundError
from grimoire.llm_gateway.routing import Route, RouteResolver


def test_route_parse_splits_on_first_dot() -> None:
    route = Route.parse("anthropic.claude-opus-4-7")
    assert route.provider_id == "anthropic"
    assert route.model == "claude-opus-4-7"


def test_route_parse_supports_dots_in_model() -> None:
    # The first dot is the separator; the rest is the model.
    route = Route.parse("provider.model.v1.2")
    assert route.provider_id == "provider"
    assert route.model == "model.v1.2"


@pytest.mark.parametrize("bad", ["", "anthropic", "anthropic.", ".model"])
def test_route_parse_rejects_malformed(bad: str) -> None:
    with pytest.raises(ValueError):
        Route.parse(bad)


def test_resolver_returns_global_route() -> None:
    resolver = RouteResolver({"main": "p.m"})
    assert resolver.resolve("main").raw == "p.m"


def test_resolver_campaign_overrides_default() -> None:
    resolver = RouteResolver({"main": "p.global"})
    resolver.set_route("main", "p.campaign", campaign_id="camp")
    assert resolver.resolve("main", campaign_id="camp").raw == "p.campaign"
    assert resolver.resolve("main").raw == "p.global"


def test_resolver_falls_back_to_default_when_campaign_has_no_override() -> None:
    resolver = RouteResolver({"main": "p.global"})
    resolver.set_route("drift_check", "p.haiku", campaign_id="camp")
    assert resolver.resolve("main", campaign_id="camp").raw == "p.global"


def test_resolver_raises_when_unconfigured() -> None:
    resolver = RouteResolver()
    with pytest.raises(RouteNotFoundError):
        resolver.resolve("main")


def test_resolver_fallback_resolved_separately() -> None:
    resolver = RouteResolver({"main": "p.cloud"}, {"main": "p.local"})
    assert resolver.fallback("main").raw == "p.local"
    assert resolver.fallback("extractor") is None


def test_set_route_validates() -> None:
    resolver = RouteResolver()
    with pytest.raises(ValueError):
        resolver.set_route("main", "nope")


def test_routes_for_merges_campaign_on_top() -> None:
    resolver = RouteResolver({"main": "p.global", "drift_check": "p.haiku"})
    resolver.set_route("main", "p.local", campaign_id="camp")
    merged = resolver.routes_for("camp")
    assert merged == {"main": "p.local", "drift_check": "p.haiku"}


def test_clear_route_removes_entry() -> None:
    resolver = RouteResolver({"main": "p.global"})
    resolver.clear_route("main")
    with pytest.raises(RouteNotFoundError):
        resolver.resolve("main")
