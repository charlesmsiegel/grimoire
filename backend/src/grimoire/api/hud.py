"""Scene HUD REST routes.

The HUD aggregator is wired into the service container as ``hud``. These
routes are thin: aggregate, single-widget refresh, config CRUD, and an
``/available`` endpoint that feeds the config editor.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field

from grimoire.api.container import ServiceContainer
from grimoire.api.deps import get_container
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


def _get_hud(container: ServiceContainer) -> Any:
    hud = getattr(container, "extras", {}).get("hud") or getattr(container, "hud", None)
    if hud is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="hud service not configured",
        )
    return hud


def _get_hud_config(container: ServiceContainer) -> Any:
    cfg = getattr(container, "extras", {}).get("hud_config") or getattr(
        container, "hud_config", None
    )
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="hud config service not configured",
        )
    return cfg


def get_hud(request: Request) -> Any:
    return _get_hud(get_container(request))


def get_hud_config(request: Request) -> Any:
    return _get_hud_config(get_container(request))


HudDep = Annotated[Any, Depends(get_hud)]
HudConfigDep = Annotated[Any, Depends(get_hud_config)]


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
        pinned_extras=PinnedExtras(
            by_character={k: list(v) for k, v in p.pinned_extras.items()}
        ),
    )


@router.get("/{campaign_id}/hud", response_model=AggregateResult)
async def get_hud_aggregate(campaign_id: str, hud: HudDep) -> AggregateResult:
    return await hud.aggregate(campaign_id)


@router.get(
    "/{campaign_id}/hud/widgets/available",
    response_model=list[HudWidget],
)
async def get_available_widgets(campaign_id: str, hud: HudDep) -> list[HudWidget]:
    return await hud.available_widgets(campaign_id)


@router.get(
    "/{campaign_id}/hud/widgets/{widget_id:path}",
    response_model=WidgetSnapshot,
)
async def get_hud_widget(
    campaign_id: str, widget_id: str, hud: HudDep
) -> WidgetSnapshot:
    return await hud.fetch_one(campaign_id, widget_id)


@router.get("/{campaign_id}/hud/config", response_model=HudConfigPayload)
async def get_hud_config_route(
    campaign_id: str, cfg: HudConfigDep
) -> HudConfigPayload:
    loaded = cfg.load(campaign_id)
    return HudConfigPayload(**serialize_config(loaded))


@router.put("/{campaign_id}/hud/config", response_model=HudConfigPayload)
async def put_hud_config(
    campaign_id: str, payload: HudConfigPayload, cfg: HudConfigDep
) -> HudConfigPayload:
    new_cfg = _to_config(payload)
    cfg.save(campaign_id, new_cfg)
    return HudConfigPayload(**serialize_config(new_cfg))


@router.post(
    "/{campaign_id}/hud/config/reset", response_model=HudConfigPayload
)
async def reset_hud_config(campaign_id: str, cfg: HudConfigDep) -> HudConfigPayload:
    reset = cfg.reset(campaign_id)
    return HudConfigPayload(**serialize_config(reset))


__all__ = [
    "HudConfigPayload",
    "OrderedWidgetPayload",
    "WidgetGroupPayload",
    "get_hud",
    "get_hud_config",
    "router",
]
