"""Per-campaign HUD config persisted to ``data/campaigns/<id>/hud.yaml``.

The config is small: density, position, an ordered list of (widget id,
visible, options) entries, and named groups. We keep entries for
modules that have since been removed (auto-hidden by the aggregator)
so toggling back to that module restores the user's prior layout.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from grimoire.hud.widgets import CORE_WIDGETS

log = logging.getLogger(__name__)


HUD_YAML_FILENAME = "hud.yaml"


@dataclass
class OrderedWidget:
    id: str
    visible: bool = True
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class WidgetGroup:
    title: str
    widgets: list[str] = field(default_factory=list)


@dataclass
class PinnedExtras:
    """``{character_id: [extra_key, ...]}`` of pinned narrative extras."""

    by_character: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class HudConfig:
    density: str = "comfortable"  # comfortable | compact
    position: str = "right"  # right | bottom
    ordered_widgets: list[OrderedWidget] = field(default_factory=list)
    groups: list[WidgetGroup] = field(default_factory=list)
    pinned_extras: PinnedExtras = field(default_factory=PinnedExtras)

    def widget_visible(self, widget_id: str) -> bool:
        for entry in self.ordered_widgets:
            if entry.id == widget_id:
                return entry.visible
        # Widgets not yet in the user's config (e.g. a freshly enabled
        # module) are visible by default — we append them on first
        # write-back via :meth:`with_ensured_entries`.
        return True

    def widget_options(self, widget_id: str) -> dict[str, Any]:
        for entry in self.ordered_widgets:
            if entry.id == widget_id:
                return dict(entry.options)
        return {}

    def with_ensured_entries(self, widget_ids: list[str]) -> HudConfig:
        """Return a copy with any missing widget ids appended at the end."""
        seen = {e.id for e in self.ordered_widgets}
        new_entries = list(self.ordered_widgets)
        for wid in widget_ids:
            if wid not in seen:
                new_entries.append(OrderedWidget(id=wid, visible=True))
                seen.add(wid)
        return HudConfig(
            density=self.density,
            position=self.position,
            ordered_widgets=new_entries,
            groups=list(self.groups),
            pinned_extras=PinnedExtras(by_character=dict(self.pinned_extras.by_character)),
        )


def default_config() -> HudConfig:
    """Default config when ``hud.yaml`` is absent.

    All ``core.*`` widgets enabled in their canonical order with a
    sensible World/Scene grouping.
    """
    ordered = [OrderedWidget(id=w.id, visible=True) for w in CORE_WIDGETS]
    groups = [
        WidgetGroup(
            title="World",
            widgets=[
                "core.in-game-date",
                "core.in-game-time",
                "core.weather",
                "core.temperature",
                "core.location",
            ],
        ),
        WidgetGroup(
            title="Scene",
            widgets=[
                "core.present-cast",
                "core.scene-summary",
                "core.recent-events",
                "core.active-commitments",
                "core.active-threads",
            ],
        ),
        WidgetGroup(
            title="Alerts",
            widgets=["core.drift-alerts", "core.review-queue"],
        ),
    ]
    return HudConfig(ordered_widgets=ordered, groups=groups)


def serialize(cfg: HudConfig) -> dict[str, Any]:
    return {
        "density": cfg.density,
        "position": cfg.position,
        "ordered_widgets": [
            {"id": e.id, "visible": e.visible, **({"options": e.options} if e.options else {})}
            for e in cfg.ordered_widgets
        ],
        "groups": [{"title": g.title, "widgets": list(g.widgets)} for g in cfg.groups],
        "pinned_extras": dict(cfg.pinned_extras.by_character),
    }


def deserialize(data: Any) -> HudConfig:
    if not isinstance(data, dict):
        raise ValueError("hud.yaml root must be a mapping")
    density = str(data.get("density") or "comfortable")
    position = str(data.get("position") or "right")
    entries_raw = data.get("ordered_widgets") or []
    entries: list[OrderedWidget] = []
    for raw in entries_raw:
        if not isinstance(raw, dict) or "id" not in raw:
            continue
        entries.append(
            OrderedWidget(
                id=str(raw["id"]),
                visible=bool(raw.get("visible", True)),
                options=dict(raw.get("options") or {}),
            )
        )
    groups_raw = data.get("groups") or []
    groups: list[WidgetGroup] = []
    for raw in groups_raw:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "")
        widgets = [str(w) for w in (raw.get("widgets") or [])]
        groups.append(WidgetGroup(title=title, widgets=widgets))
    pinned = data.get("pinned_extras") or {}
    by_character: dict[str, list[str]] = {}
    if isinstance(pinned, dict):
        for cid, keys in pinned.items():
            if isinstance(keys, list):
                by_character[str(cid)] = [str(k) for k in keys]
    return HudConfig(
        density=density,
        position=position,
        ordered_widgets=entries,
        groups=groups,
        pinned_extras=PinnedExtras(by_character=by_character),
    )


class HudConfigService:
    """Reads / writes ``hud.yaml`` for a campaign.

    ``data_root`` is the campaigns-data parent (``data/campaigns/``).
    The file lives at ``<data_root>/<campaign_id>/hud.yaml``.
    """

    def __init__(self, data_root: Path) -> None:
        self._data_root = Path(data_root)

    def _path(self, campaign_id: str) -> Path:
        return self._data_root / campaign_id / HUD_YAML_FILENAME

    def load(self, campaign_id: str) -> HudConfig:
        path = self._path(campaign_id)
        if not path.is_file():
            return default_config()
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            return deserialize(data)
        except Exception as e:
            log.warning("hud.yaml at %s corrupt, falling back to defaults: %s", path, e)
            backup = path.with_suffix(f".broken-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
            with contextlib.suppress(OSError):
                path.replace(backup)
            return default_config()

    def save(self, campaign_id: str, cfg: HudConfig) -> None:
        path = self._path(campaign_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(serialize(cfg), sort_keys=False), encoding="utf-8")

    def reset(self, campaign_id: str) -> HudConfig:
        cfg = default_config()
        self.save(campaign_id, cfg)
        return cfg

    def set_pinned_extras(self, campaign_id: str, character_id: str, keys: list[str]) -> HudConfig:
        cfg = self.load(campaign_id)
        cfg.pinned_extras.by_character[character_id] = list(keys)
        self.save(campaign_id, cfg)
        return cfg


__all__ = [
    "HUD_YAML_FILENAME",
    "HudConfig",
    "HudConfigService",
    "OrderedWidget",
    "PinnedExtras",
    "WidgetGroup",
    "default_config",
    "deserialize",
    "serialize",
]
