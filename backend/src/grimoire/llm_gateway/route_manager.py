"""Campaign routing I/O and provider defaults registration."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from grimoire.files.yaml_io import dump_yaml, load_yaml
from grimoire.llm_gateway.routing import Route
from grimoire.llm_gateway.tiers import Tier
from grimoire.types.common import CampaignId

if TYPE_CHECKING:
    from grimoire.llm_gateway.routing import RouteResolver
    from grimoire.types.protocols import Plugins

logger = logging.getLogger(__name__)


class RouteManager:
    """Campaign routing I/O and provider defaults registration."""

    def __init__(
        self,
        *,
        router: RouteResolver,
        plugins: Plugins,
        data_root: Path | None,
        imagegen_routes: dict[CampaignId, dict[str, str]],
        loaded_campaigns: set[CampaignId],
    ) -> None:
        self._router = router
        self._plugins = plugins
        self._data_root = data_root
        self._imagegen_routes = imagegen_routes
        self._loaded_campaigns = loaded_campaigns
        # Serializes every read-modify-write of a campaign's campaign.yaml
        # (and the initial routing load) so concurrent mutations offloaded to
        # threads can't race each other or observe a half-loaded campaign.
        self._campaign_locks: dict[CampaignId, asyncio.Lock] = {}

    def _lock_for(self, campaign_id: CampaignId) -> asyncio.Lock:
        return self._campaign_locks.setdefault(campaign_id, asyncio.Lock())

    def campaign_yaml_path(self, campaign_id: CampaignId) -> Path | None:
        if self._data_root is None:
            return None
        return self._data_root / "campaigns" / campaign_id / "campaign.yaml"

    async def load_campaign_routing(self, campaign_id: CampaignId) -> None:
        async with self._lock_for(campaign_id):
            if campaign_id in self._loaded_campaigns:
                return
            try:
                await self._load_campaign_routing_locked(campaign_id)
            finally:
                # Mark loaded even when the YAML is missing or unparsable so
                # later calls don't retry the disk read every time — but only
                # after the routing blocks have been applied (or load has
                # failed), so concurrent callers never see a half-loaded
                # campaign as loaded.
                self._loaded_campaigns.add(campaign_id)

    async def _load_campaign_routing_locked(self, campaign_id: CampaignId) -> None:
        yaml_path = self.campaign_yaml_path(campaign_id)
        if yaml_path is None or not await asyncio.to_thread(yaml_path.is_file):
            return
        try:
            raw = await asyncio.to_thread(load_yaml, yaml_path)
        except Exception:
            logger.warning(
                "llm_gateway: failed to parse %s; campaign routing not loaded",
                yaml_path,
            )
            return
        if not isinstance(raw, dict):
            return

        await self._apply_routing_block(
            raw.get("model_routing"),
            campaign_id,
            yaml_path,
            block_name="model_routing",
            provider_kind="llm",
        )
        await self._apply_routing_block(
            raw.get("embedding_routing"),
            campaign_id,
            yaml_path,
            block_name="embedding_routing",
            provider_kind="embedding",
        )
        await self._apply_imagegen_routing(raw.get("imagegen_routing"), campaign_id, yaml_path)
        await self._apply_tier_routing(raw.get("model_tiers"), campaign_id, yaml_path)

    async def _apply_tier_routing(
        self, block: object, campaign_id: CampaignId, yaml_path: Path
    ) -> None:
        if not isinstance(block, dict):
            return
        for key, value in block.items():
            try:
                tier = Tier(str(key))
            except ValueError:
                logger.debug("llm_gateway: unknown tier %r in %s; skipping", key, yaml_path)
                continue
            if not isinstance(value, str) or not value:
                continue
            try:
                self._router.set_tier_route(campaign_id, tier, value)
            except ValueError as exc:
                logger.warning(
                    "llm_gateway: bad route %r for tier %s in %s: %s",
                    value,
                    tier.value,
                    yaml_path,
                    exc,
                )

    async def _apply_imagegen_routing(
        self, routing: object, campaign_id: CampaignId, yaml_path: Path
    ) -> None:
        if not isinstance(routing, dict):
            return
        bucket = self._imagegen_routes.setdefault(campaign_id, {})
        for task, route in routing.items():
            try:
                Route.parse(str(route))
            except ValueError:
                logger.warning(
                    "llm_gateway: skipping bad imagegen_routing entry in %s — "
                    "task=%r route=%r is not a valid 'provider.model' string",
                    yaml_path,
                    task,
                    route,
                )
                continue
            bucket[str(task)] = str(route)
            await self._warn_unknown_model(str(task), str(route), provider_kind="imagegen")

    async def _apply_routing_block(
        self,
        routing: object,
        campaign_id: CampaignId,
        yaml_path: Path,
        *,
        block_name: str,
        provider_kind: str,
    ) -> None:
        if not isinstance(routing, dict):
            return
        for task, route in routing.items():
            try:
                self._router.set_route(str(task), str(route), campaign_id)
            except ValueError:
                logger.warning(
                    "llm_gateway: skipping bad %s entry in %s — "
                    "task=%r route=%r is not a valid 'provider.model' string",
                    block_name,
                    yaml_path,
                    task,
                    route,
                )
                continue
            await self._warn_unknown_model(str(task), str(route), provider_kind=provider_kind)

    async def _warn_unknown_model(self, task: str, route: str, *, provider_kind: str) -> None:
        try:
            parsed = Route.parse(route)
        except ValueError:
            return
        if provider_kind == "llm":
            provider = self._plugins.get_llm_provider(parsed.provider_id)
        elif provider_kind == "embedding":
            provider = self._plugins.get_embedding_provider(parsed.provider_id)
        elif provider_kind == "imagegen":
            getter = getattr(self._plugins, "get_imagegen_backend", None)
            provider = getter(parsed.provider_id) if getter is not None else None
        else:
            return
        if provider is None:
            return
        list_models = getattr(provider, "list_models", None)
        if list_models is None:
            return
        try:
            models = await list_models()
        except Exception:
            return
        try:
            known_ids = {m.id for m in models}
        except Exception:
            return
        if parsed.model not in known_ids:
            logger.warning(
                "llm_gateway: route task=%r references model %r on provider %r "
                "which is not in its advertised list (kind=%s); applying anyway",
                task,
                parsed.model,
                parsed.provider_id,
                provider_kind,
            )

    _BLOCK_FOR_KIND: ClassVar[dict[str, str]] = {
        "llm": "model_routing",
        "embedding": "embedding_routing",
        "imagegen": "imagegen_routing",
    }

    async def persist_campaign_route(
        self, campaign_id: CampaignId, task: str, route: str, *, kind: str = "llm"
    ) -> None:
        async with self._lock_for(campaign_id):
            await asyncio.to_thread(
                self._persist_campaign_route_sync, campaign_id, task, route, kind
            )

    def _persist_campaign_route_sync(
        self, campaign_id: CampaignId, task: str, route: str, kind: str
    ) -> None:
        block = self._BLOCK_FOR_KIND.get(kind, "model_routing")
        data = self._read_campaign_yaml_for_write(campaign_id)
        if data is None:
            return
        if block not in data or not isinstance(data[block], dict):
            data[block] = {}
        data[block][task] = route
        self._atomic_write_campaign_yaml(campaign_id, data)

    async def delete_campaign_route(
        self, campaign_id: CampaignId, task: str, *, kind: str = "llm"
    ) -> None:
        async with self._lock_for(campaign_id):
            await asyncio.to_thread(self._delete_campaign_route_sync, campaign_id, task, kind)

    def _delete_campaign_route_sync(self, campaign_id: CampaignId, task: str, kind: str) -> None:
        block = self._BLOCK_FOR_KIND.get(kind, "model_routing")
        data = self._read_campaign_yaml_for_write(campaign_id)
        if data is None:
            return
        existing = data.get(block)
        if not isinstance(existing, dict) or task not in existing:
            return
        existing.pop(task, None)
        if not existing:
            data.pop(block, None)
        self._atomic_write_campaign_yaml(campaign_id, data)

    async def write_tier_block(self, campaign_id: CampaignId) -> None:
        """Serialize the resolver's tier state back to ``campaign.yaml``."""
        async with self._lock_for(campaign_id):
            await asyncio.to_thread(self._write_tier_block_sync, campaign_id)

    def _write_tier_block_sync(self, campaign_id: CampaignId) -> None:
        data = self._read_campaign_yaml_for_write(campaign_id)
        if data is None:
            return
        tiers = self._router.tiers_for(campaign_id)
        if tiers:
            data["model_tiers"] = {tier.value: route for tier, route in tiers.items()}
        else:
            data.pop("model_tiers", None)
        self._atomic_write_campaign_yaml(campaign_id, data)

    def _read_campaign_yaml_for_write(self, campaign_id: CampaignId) -> dict | None:
        yaml_path = self.campaign_yaml_path(campaign_id)
        if yaml_path is None:
            return None
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        if not yaml_path.is_file():
            return {}
        try:
            raw = load_yaml(yaml_path)
        except Exception:
            logger.warning(
                "llm_gateway: could not read %s for route persistence; "
                "existing content may be overwritten",
                yaml_path,
            )
            return {}
        return dict(raw) if isinstance(raw, dict) else {}

    def _atomic_write_campaign_yaml(self, campaign_id: CampaignId, data: dict) -> None:
        yaml_path = self.campaign_yaml_path(campaign_id)
        if yaml_path is None:
            return
        tmp_path = yaml_path.with_suffix(".yaml.tmp")
        tmp_path.write_text(dump_yaml(data), encoding="utf-8")
        os.replace(tmp_path, yaml_path)
