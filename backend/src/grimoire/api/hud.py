"""Scene HUD REST routes.

The HUD aggregator is wired into the service container as ``hud``. These
routes are thin: aggregate, single-widget refresh, config CRUD, and an
``/available`` endpoint that feeds the config editor.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from grimoire.api.deps import HudConfigDep, HudDep
from grimoire.api.util import map_lookup_errors
from grimoire.hud.config import (
    HudConfig,
    OrderedWidget,
    PinnedExtras,
    WidgetGroup,
)
from grimoire.hud.config import (
    serialize as serialize_config,
)
from grimoire.types.hud import AggregateResult, HudWidget, WidgetSnapshot

router = APIRouter(prefix="/campaigns")


class OrderedWidgetPayload(BaseModel):
    id: str
    visible: bool = True
    options: dict[str, Any] = Field(default_factory=dict)


class WidgetGroupPayload(BaseModel):
    title: str
    widgets: list[str] = Field(default_factory=list)


class HudConfigPayload(BaseModel):
    density: str = "comfortable"
    position: str = "right"
    ordered_widgets: list[OrderedWidgetPayload] = Field(default_factory=list)
    groups: list[WidgetGroupPayload] = Field(default_factory=list)
    pinned_extras: dict[str, list[str]] = Field(default_factory=dict)


def _to_config(p: HudConfigPayload) -> HudConfig:
    return HudConfig(
        density=p.density,
        position=p.position,
        ordered_widgets=[
            OrderedWidget(id=e.id, visible=e.visible, options=dict(e.options))
            for e in p.ordered_widgets
        ],
        groups=[WidgetGroup(title=g.title, widgets=list(g.widgets)) for g in p.groups],
        pinned_extras=PinnedExtras(by_character={k: list(v) for k, v in p.pinned_extras.items()}),
    )


@router.get("/{campaign_id}/hud", response_model=AggregateResult)
async def get_hud_aggregate(
    campaign_id: str, hud: HudDep, scene_id: str | None = None
) -> AggregateResult:
    try:
        return await hud.aggregate(campaign_id, scene_id=scene_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get(
    "/{campaign_id}/hud/widgets/available",
    response_model=list[HudWidget],
)
async def get_available_widgets(campaign_id: str, hud: HudDep) -> list[HudWidget]:
    try:
        return await hud.available_widgets(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get(
    "/{campaign_id}/hud/widgets/{widget_id:path}",
    response_model=WidgetSnapshot,
)
async def get_hud_widget(
    campaign_id: str, widget_id: str, hud: HudDep, scene_id: str | None = None
) -> WidgetSnapshot:
    try:
        return await hud.fetch_one(campaign_id, widget_id, scene_id=scene_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc


@router.get("/{campaign_id}/hud/config", response_model=HudConfigPayload)
async def get_hud_config_route(campaign_id: str, cfg: HudConfigDep) -> HudConfigPayload:
    try:
        loaded = cfg.load(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return HudConfigPayload(**serialize_config(loaded))


@router.put("/{campaign_id}/hud/config", response_model=HudConfigPayload)
async def put_hud_config(
    campaign_id: str, payload: HudConfigPayload, cfg: HudConfigDep
) -> HudConfigPayload:
    new_cfg = _to_config(payload)
    try:
        cfg.save(campaign_id, new_cfg)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return HudConfigPayload(**serialize_config(new_cfg))


@router.post("/{campaign_id}/hud/config/reset", response_model=HudConfigPayload)
async def reset_hud_config(campaign_id: str, cfg: HudConfigDep) -> HudConfigPayload:
    try:
        reset = cfg.reset(campaign_id)
    except Exception as exc:
        raise map_lookup_errors(exc) from exc
    return HudConfigPayload(**serialize_config(reset))


__all__ = [
    "HudConfigPayload",
    "OrderedWidgetPayload",
    "WidgetGroupPayload",
    "router",
]
