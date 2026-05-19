"""Types for the Scene HUD aggregator.

Widget descriptors a mechanics module can declare in its ``manifest.yaml``
under ``hud_widgets``, plus the snapshot/aggregate shapes the HUD service
returns. The HUD owns no state of its own — these structures describe
where to read and edit, and how to render, but the data lives with the
canonical owner module.

See ``docs/superpowers/specs/2026-05-19-scene-hud-design.md``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RenderHint(StrEnum):
    """How the frontend should lay out a widget."""

    ROW = "row"
    BLOCK = "block"
    CHIP_LIST = "chip-list"
    BANNER = "banner"
    COMPOSITE = "composite"


class WidgetScope(StrEnum):
    """What entity the widget applies to."""

    CAMPAIGN = "campaign"
    SCENE = "scene"
    PC = "pc"
    PRESENT_NPC = "present_npc"


class WidgetEditKind(StrEnum):
    INLINE_TEXT = "inline-text"
    PICKER = "picker"
    SLIDER = "slider"
    ENUM = "enum"
    COMPOSITE = "composite"


class WidgetRead(BaseModel):
    endpoint: str
    poll_interval_s: int | None = None


class WidgetEdit(BaseModel):
    kind: str
    endpoint: str
    schema_ref: str | None = None


class HudWidget(BaseModel):
    """A single dashboard widget descriptor."""

    id: str
    title: str
    scope: WidgetScope = WidgetScope.CAMPAIGN
    visible_when: str | None = None
    # Validated at aggregation time — unknown hints log once and fall back
    # to BLOCK so old frontends keep working with new modules.
    render_hint: str = RenderHint.BLOCK.value
    read: WidgetRead
    edit: WidgetEdit | None = None
    refresh_on: list[str] = Field(default_factory=list)
    stale_threshold_s: int | None = None
    owner_module: str | None = None


class WidgetStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    HIDDEN = "hidden"


class WidgetSnapshot(BaseModel):
    """A single widget's resolved state from one aggregate call."""

    id: str
    status: WidgetStatus = WidgetStatus.OK
    data: Any | None = None
    error: str | None = None
    stale: bool = False
    title: str | None = None
    render_hint: str | None = None


class AggregateResult(BaseModel):
    """Full HUD payload returned by ``GET /campaigns/{id}/hud``."""

    campaign_id: str
    scene_id: str | None = None
    generated_at: str
    widgets: list[WidgetSnapshot] = Field(default_factory=list)


__all__ = [
    "AggregateResult",
    "HudWidget",
    "RenderHint",
    "WidgetEdit",
    "WidgetEditKind",
    "WidgetRead",
    "WidgetScope",
    "WidgetSnapshot",
    "WidgetStatus",
]
