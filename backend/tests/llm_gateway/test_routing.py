"""Tests for the route resolver."""

from __future__ import annotations

import pytest

from grimoire.llm_gateway.errors import RouteNotFoundError
from grimoire.llm_gateway.routing import Route, RouteResolver
from grimoire.llm_gateway.tiers import Tier


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


# ---------------------------------------------------------------------------
# Tier-aware resolution (Task 2)
# ---------------------------------------------------------------------------


def test_per_task_wins_over_tier() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    r.set_route("main", "openai.gpt-5", campaign_id="camp-1")
    assert r.resolve("main", "camp-1").raw == "openai.gpt-5"


def test_tier_used_when_no_per_task_override() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    # No per-task override; tier route wins over the app default.
    assert r.resolve("main", "camp-1").raw == "deepseek.pro"


def test_default_used_when_no_tier_or_per_task() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    assert r.resolve("main", "camp-1").raw == "anthropic.opus"


def test_unknown_task_falls_through_to_default_not_tier() -> None:
    r = RouteResolver(default_routes={"weird.task": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    # "weird.task" isn't in _TASK_TIER → tier lookup fails → default used.
    assert r.resolve("weird.task", "camp-1").raw == "anthropic.opus"


def test_no_route_raises() -> None:
    r = RouteResolver()
    with pytest.raises(RouteNotFoundError):
        r.resolve("main", "camp-1")


def test_clear_tier_route() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    r.clear_tier_route("camp-1", Tier.HEAVY)
    assert r.resolve("main", "camp-1").raw == "anthropic.opus"


def test_tiers_for_campaign() -> None:
    r = RouteResolver()
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    r.set_tier_route("camp-1", Tier.LIGHT, "deepseek.flash")
    tiers = r.tiers_for("camp-1")
    assert tiers == {Tier.HEAVY: "deepseek.pro", Tier.LIGHT: "deepseek.flash"}


# ---------------------------------------------------------------------------
# Gateway integration: model_tiers block loaded from campaign.yaml (Task 3)
# ---------------------------------------------------------------------------

from pathlib import Path

import yaml

from grimoire.llm_gateway.config import GatewayConfig
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.types.llm import RetryPolicy, TimeoutPolicy


def _minimal_config() -> GatewayConfig:
    return GatewayConfig(
        retry=RetryPolicy(max_retries=0, initial_delay_ms=0, backoff_factor=1.0),
        timeout=TimeoutPolicy(total_seconds=5.0, first_token_seconds=2.0),
    )


async def test_gateway_loads_model_tiers_from_yaml(db, plugins, tmp_path: Path) -> None:
    camp_dir = tmp_path / "campaigns" / "camp-1"
    camp_dir.mkdir(parents=True)
    (camp_dir / "campaign.yaml").write_text(
        yaml.safe_dump(
            {
                "model_tiers": {
                    "heavy": "deepseek.deepseek-v4-pro",
                    "light": "deepseek.deepseek-v4-flash",
                }
            }
        )
    )
    gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
    await gw._load_campaign_routing("camp-1")
    tiers = gw._router.tiers_for("camp-1")
    assert tiers[Tier.HEAVY] == "deepseek.deepseek-v4-pro"
    assert tiers[Tier.LIGHT] == "deepseek.deepseek-v4-flash"


async def test_gateway_skips_unknown_tier_keys(db, plugins, tmp_path: Path) -> None:
    camp_dir = tmp_path / "campaigns" / "camp-1"
    camp_dir.mkdir(parents=True)
    (camp_dir / "campaign.yaml").write_text(
        yaml.safe_dump(
            {"model_tiers": {"heavy": "deepseek.pro", "bogus": "x.y"}}
        )
    )
    gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
    await gw._load_campaign_routing("camp-1")
    tiers = gw._router.tiers_for("camp-1")
    assert Tier.HEAVY in tiers
    assert "bogus" not in [t.value for t in tiers]


# ---------------------------------------------------------------------------
# Gateway integration: set/clear_tier_route persist to campaign.yaml (Task 4)
# ---------------------------------------------------------------------------


async def test_gateway_set_tier_route_persists_to_yaml(db, plugins, tmp_path: Path) -> None:
    camp_dir = tmp_path / "campaigns" / "camp-1"
    camp_dir.mkdir(parents=True)
    (camp_dir / "campaign.yaml").write_text(yaml.safe_dump({"id": "camp-1"}))
    gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
    await gw.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    raw = yaml.safe_load((camp_dir / "campaign.yaml").read_text())
    assert raw["model_tiers"] == {"heavy": "deepseek.pro"}

    # Setting another tier preserves the first.
    await gw.set_tier_route("camp-1", Tier.LIGHT, "deepseek.flash")
    raw = yaml.safe_load((camp_dir / "campaign.yaml").read_text())
    assert raw["model_tiers"] == {"heavy": "deepseek.pro", "light": "deepseek.flash"}


async def test_gateway_clear_tier_route_persists(db, plugins, tmp_path: Path) -> None:
    camp_dir = tmp_path / "campaigns" / "camp-1"
    camp_dir.mkdir(parents=True)
    (camp_dir / "campaign.yaml").write_text(
        yaml.safe_dump({"model_tiers": {"heavy": "x.y", "light": "a.b"}})
    )
    gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
    # Load existing tiers so clear_tier_route sees them.
    await gw._load_campaign_routing("camp-1")
    await gw.clear_tier_route("camp-1", Tier.HEAVY)
    raw = yaml.safe_load((camp_dir / "campaign.yaml").read_text())
    assert raw["model_tiers"] == {"light": "a.b"}


async def test_gateway_set_tier_route_rejects_bad_route(db, plugins, tmp_path: Path) -> None:
    gw = LLMGatewayService(plugins, db, _minimal_config(), data_root=tmp_path)
    with pytest.raises(ValueError):
        await gw.set_tier_route("camp-1", Tier.HEAVY, "missing_dot")
