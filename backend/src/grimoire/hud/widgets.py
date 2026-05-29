"""Built-in (``core.*``) HUD widget table.

These are the always-available widgets the dashboard shows regardless of
which mechanics module a campaign uses. Each entry names the canonical
owner module/service and the endpoint the aggregator dispatches to.
"""

from __future__ import annotations

from grimoire.types.hud import HudWidget, RenderHint, WidgetEdit, WidgetRead, WidgetScope

CORE_WIDGETS: list[HudWidget] = [
    HudWidget(
        id="core.in-game-date",
        title="Date",
        scope=WidgetScope.CAMPAIGN,
        read=WidgetRead(endpoint="/time/date"),
        edit=WidgetEdit(kind="picker", endpoint="/time/date"),
        render_hint=RenderHint.ROW.value,
        refresh_on=["time_advanced"],
        owner_module="time_engine",
    ),
    HudWidget(
        id="core.in-game-time",
        title="Time of day",
        scope=WidgetScope.CAMPAIGN,
        read=WidgetRead(endpoint="/time/time-of-day"),
        edit=WidgetEdit(kind="picker", endpoint="/time/time-of-day"),
        render_hint=RenderHint.ROW.value,
        refresh_on=["time_advanced"],
        owner_module="time_engine",
    ),
    HudWidget(
        id="core.weather",
        title="Weather",
        scope=WidgetScope.SCENE,
        read=WidgetRead(endpoint="/scenes/{sid}/weather"),
        edit=WidgetEdit(kind="enum", endpoint="/scenes/{sid}/weather"),
        render_hint=RenderHint.ROW.value,
        refresh_on=["scene_started", "time_advanced", "weather_changed"],
        owner_module="world",
    ),
    HudWidget(
        id="core.temperature",
        title="Temperature",
        scope=WidgetScope.SCENE,
        # Hidden until world ships a temperature surface — design says this
        # widget lights up when the data is there. Until then ``visible_when``
        # resolves to false and the widget never reaches fan-out.
        visible_when="false",
        read=WidgetRead(endpoint="/scenes/{sid}/temperature"),
        edit=WidgetEdit(kind="slider", endpoint="/scenes/{sid}/temperature"),
        render_hint=RenderHint.ROW.value,
        refresh_on=["scene_started", "time_advanced"],
        owner_module="world",
    ),
    HudWidget(
        id="core.location",
        title="Location",
        scope=WidgetScope.SCENE,
        read=WidgetRead(endpoint="/scenes/{sid}/location"),
        edit=WidgetEdit(kind="picker", endpoint="/scenes/{sid}/location"),
        render_hint=RenderHint.ROW.value,
        refresh_on=["scene_started", "scene_ended"],
        owner_module="scene_manager",
    ),
    HudWidget(
        id="core.present-cast",
        title="Cast",
        scope=WidgetScope.SCENE,
        read=WidgetRead(endpoint="/scenes/{sid}/present-cast"),
        render_hint=RenderHint.CHIP_LIST.value,
        refresh_on=[
            "turn_complete",
            "deltas_extracted",
            "drift_detected",
            "library_file_changed",
        ],
        owner_module="characters",
    ),
    HudWidget(
        id="core.recent-events",
        title="Recent events",
        scope=WidgetScope.SCENE,
        read=WidgetRead(endpoint="/scenes/{sid}/recent-facts?limit=5"),
        render_hint=RenderHint.BLOCK.value,
        refresh_on=["deltas_extracted"],
        owner_module="continuity",
    ),
    HudWidget(
        id="core.active-commitments",
        title="Active commitments",
        scope=WidgetScope.CAMPAIGN,
        read=WidgetRead(endpoint="/campaigns/{id}/commitments?status=active"),
        render_hint=RenderHint.BLOCK.value,
        refresh_on=[
            "deltas_extracted",
            "commitment_created",
            "commitment_paid_off",
        ],
        owner_module="continuity",
    ),
    HudWidget(
        id="core.scene-summary",
        title="Scene summary",
        scope=WidgetScope.SCENE,
        read=WidgetRead(endpoint="/scenes/{sid}/summary"),
        edit=WidgetEdit(kind="inline-text", endpoint="/scenes/{sid}/summary"),
        render_hint=RenderHint.BLOCK.value,
        refresh_on=["scene_started", "turn_complete"],
        owner_module="scene_manager",
    ),
    HudWidget(
        id="core.drift-alerts",
        title="Drift alerts",
        scope=WidgetScope.CAMPAIGN,
        read=WidgetRead(endpoint="/campaigns/{id}/drift-alerts?active=true"),
        render_hint=RenderHint.BANNER.value,
        refresh_on=["drift_detected"],
        owner_module="characters",
    ),
    HudWidget(
        id="core.review-queue",
        title="Review queue",
        scope=WidgetScope.CAMPAIGN,
        read=WidgetRead(endpoint="/campaigns/{id}/review-queue?count=true"),
        render_hint=RenderHint.COMPOSITE.value,
        refresh_on=["review_item_added"],
        owner_module="extractor",
    ),
    HudWidget(
        id="core.active-threads",
        title="Active threads",
        scope=WidgetScope.SCENE,
        read=WidgetRead(endpoint="/scenes/{sid}/threads?status=open"),
        render_hint=RenderHint.BLOCK.value,
        refresh_on=["deltas_extracted", "thread_opened", "thread_closed"],
        owner_module="continuity",
    ),
    HudWidget(
        id="core.inventory",
        title="Inventory",
        scope=WidgetScope.CAMPAIGN,
        read=WidgetRead(endpoint="/campaigns/{id}/inventory"),
        render_hint=RenderHint.BLOCK.value,
        refresh_on=["inventory_changed", "turn_complete", "scene_started"],
        owner_module="inventory",
    ),
]


def core_widget_ids() -> list[str]:
    return [w.id for w in CORE_WIDGETS]


def core_widget_by_id(widget_id: str) -> HudWidget | None:
    for w in CORE_WIDGETS:
        if w.id == widget_id:
            return w
    return None


__all__ = ["CORE_WIDGETS", "core_widget_by_id", "core_widget_ids"]
