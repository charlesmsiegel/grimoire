"""Routing: task -> `provider_id.model` resolution.

Resolution chain (highest priority first):
  1. Per-campaign per-task override  (``_campaigns[cid][task]``)
  2. Per-campaign tier route          (``_tiers[cid][tier_for_task(task)]``)
  3. App-level default route          (``_defaults[task]``)
  4. Fallback route                   (``_fallbacks[task]``)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from grimoire.llm_gateway.errors import RouteNotFoundError
from grimoire.llm_gateway.tiers import Tier, tier_for_task
from grimoire.types.common import CampaignId

_SEPARATOR: Final[str] = "."


@dataclass(frozen=True)
class Route:
    raw: str
    provider_id: str
    model: str

    @classmethod
    def parse(cls, raw: str) -> Route:
        if not isinstance(raw, str) or _SEPARATOR not in raw:
            raise ValueError(f"route {raw!r} must be of the form 'provider.model'")
        provider_id, _, model = raw.partition(_SEPARATOR)
        if not provider_id or not model:
            raise ValueError(f"route {raw!r} must be of the form 'provider.model'")
        return cls(raw=raw, provider_id=provider_id, model=model)


class RouteResolver:
    """Owns default + per-campaign per-task + per-campaign per-tier + fallback routes."""

    def __init__(
        self,
        default_routes: dict[str, str] | None = None,
        fallback_routes: dict[str, str] | None = None,
    ) -> None:
        self._defaults: dict[str, str] = {}
        self._fallbacks: dict[str, str] = {}
        self._campaigns: dict[CampaignId, dict[str, str]] = {}
        self._tiers: dict[CampaignId, dict[Tier, str]] = {}
        for task, route in (default_routes or {}).items():
            Route.parse(route)
            self._defaults[task] = route
        for task, route in (fallback_routes or {}).items():
            Route.parse(route)
            self._fallbacks[task] = route

    def resolve(self, task: str, campaign_id: CampaignId | None = None) -> Route:
        raw: str | None = None
        if campaign_id is not None:
            raw = self._campaigns.get(campaign_id, {}).get(task)
            if raw is None:
                tier = tier_for_task(task)
                if tier is not None:
                    raw = self._tiers.get(campaign_id, {}).get(tier)
        if raw is None:
            raw = self._defaults.get(task)
        if raw is None:
            raise RouteNotFoundError(task)
        return Route.parse(raw)

    def fallback(self, task: str) -> Route | None:
        raw = self._fallbacks.get(task)
        return Route.parse(raw) if raw else None

    def set_route(
        self,
        task: str,
        route: str,
        campaign_id: CampaignId | None = None,
    ) -> None:
        Route.parse(route)
        if campaign_id is None:
            self._defaults[task] = route
        else:
            self._campaigns.setdefault(campaign_id, {})[task] = route

    def clear_route(self, task: str, campaign_id: CampaignId | None = None) -> None:
        if campaign_id is None:
            self._defaults.pop(task, None)
        else:
            self._campaigns.get(campaign_id, {}).pop(task, None)

    def set_tier_route(
        self,
        campaign_id: CampaignId,
        tier: Tier,
        route: str,
    ) -> None:
        Route.parse(route)
        self._tiers.setdefault(campaign_id, {})[tier] = route

    def clear_tier_route(self, campaign_id: CampaignId, tier: Tier) -> None:
        self._tiers.get(campaign_id, {}).pop(tier, None)

    def tiers_for(self, campaign_id: CampaignId) -> dict[Tier, str]:
        return dict(self._tiers.get(campaign_id, {}))

    def routes_for(self, campaign_id: CampaignId | None = None) -> dict[str, str]:
        merged = dict(self._defaults)
        if campaign_id is not None:
            merged.update(self._campaigns.get(campaign_id, {}))
        return merged
