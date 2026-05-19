"""HUD aggregator service.

Fans out widget reads to their owner modules in parallel, collects
per-widget status (ok/error/timeout) within a configurable per-widget
timeout, and applies ``visible_when`` filtering before fan-out so
hidden widgets never reach the network.

Owner dispatch is in-process — each widget id maps to a method on a
domain service we already hold in the service container. We avoid
re-entering FastAPI's HTTP stack for the aggregate path because
sub-100ms p50 latency is the design target.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from grimoire.hud.config import HudConfig, HudConfigService
from grimoire.hud.expression import EvaluationContext, evaluate
from grimoire.hud.widgets import CORE_WIDGETS
from grimoire.types.hud import (
    AggregateResult,
    HudWidget,
    RenderHint,
    WidgetSnapshot,
    WidgetStatus,
)

log = logging.getLogger(__name__)


_KNOWN_RENDER_HINTS = {h.value for h in RenderHint}


def _normalize_render_hint(hint: str | None) -> str:
    if hint is None:
        return RenderHint.BLOCK.value
    if hint in _KNOWN_RENDER_HINTS:
        return hint
    log.warning("unknown render_hint %r — falling back to %r", hint, RenderHint.BLOCK.value)
    return RenderHint.BLOCK.value


# An owner fetcher is an ``async def fn(widget, campaign_id, scene, observer) -> Any``.
WidgetFetcher = Callable[[HudWidget, str, Any, Any], Awaitable[Any]]


@dataclass
class HudServiceConfig:
    """Tunables for :class:`HudService`."""

    aggregate_timeout_seconds_per_widget: float = 1.0
    default_stale_threshold_seconds: int = 60


@dataclass
class HudService:
    """Aggregator. Construct with a config service and a fetcher registry."""

    config_service: HudConfigService
    fetchers: dict[str, WidgetFetcher] = field(default_factory=dict)
    settings: HudServiceConfig = field(default_factory=HudServiceConfig)
    # Optional hook for mechanics-contributed widgets — returns the
    # current campaign's mechanics module manifest widget list.
    mechanics_widgets: Callable[[str], Awaitable[list[HudWidget]]] | None = None
    # Optional hook for building the visible_when evaluation context.
    eval_context_builder: Callable[[str, Any], Awaitable[EvaluationContext]] | None = None
    # Optional hook for the current scene given a campaign id.
    current_scene: Callable[[str], Awaitable[Any]] | None = None

    def register_fetcher(self, widget_id: str, fn: WidgetFetcher) -> None:
        self.fetchers[widget_id] = fn

    async def _collect_widgets(self, campaign_id: str) -> list[HudWidget]:
        widgets = list(CORE_WIDGETS)
        if self.mechanics_widgets is not None:
            try:
                extra = await self.mechanics_widgets(campaign_id)
                widgets.extend(extra)
            except Exception as e:
                log.warning("mechanics_widgets failed for %s: %s", campaign_id, e)
        return widgets

    async def aggregate(
        self,
        campaign_id: str,
        *,
        observer: Any = None,
        only: list[str] | None = None,
    ) -> AggregateResult:
        """Read every visible widget in parallel and return a snapshot."""
        scene = await self._scene(campaign_id)
        ctx = await self._context(campaign_id, scene)
        widgets = await self._collect_widgets(campaign_id)
        config = self.config_service.load(campaign_id)

        visible: list[HudWidget] = []
        for w in widgets:
            if only is not None and w.id not in only:
                continue
            if not config.widget_visible(w.id):
                continue
            if w.visible_when and not evaluate(w.visible_when, ctx):
                continue
            visible.append(w)

        snapshots = await asyncio.gather(
            *(self._fetch(w, campaign_id, scene, observer, config) for w in visible)
        )
        scene_id = getattr(scene, "id", None) if scene is not None else None
        return AggregateResult(
            campaign_id=campaign_id,
            scene_id=scene_id,
            generated_at=datetime.now(UTC).isoformat(),
            widgets=list(snapshots),
        )

    async def fetch_one(
        self,
        campaign_id: str,
        widget_id: str,
        *,
        observer: Any = None,
    ) -> WidgetSnapshot:
        """Refresh exactly one widget; honors the per-widget timeout.

        Applies the same visibility filters as :meth:`aggregate` so a
        hidden widget never reaches its fetcher — both the config's
        ``visible`` toggle and the widget's ``visible_when`` expression.
        """
        scene = await self._scene(campaign_id)
        widget = next(
            (w for w in await self._collect_widgets(campaign_id) if w.id == widget_id),
            None,
        )
        if widget is None:
            return WidgetSnapshot(
                id=widget_id,
                status=WidgetStatus.ERROR,
                error="unknown widget",
            )
        config = self.config_service.load(campaign_id)
        if not config.widget_visible(widget.id):
            return WidgetSnapshot(
                id=widget.id,
                status=WidgetStatus.HIDDEN,
                title=widget.title,
                render_hint=_normalize_render_hint(widget.render_hint),
            )
        if widget.visible_when:
            ctx = await self._context(campaign_id, scene)
            if not evaluate(widget.visible_when, ctx):
                return WidgetSnapshot(
                    id=widget.id,
                    status=WidgetStatus.HIDDEN,
                    title=widget.title,
                    render_hint=_normalize_render_hint(widget.render_hint),
                )
        return await self._fetch(widget, campaign_id, scene, observer, config)

    async def _fetch(
        self,
        w: HudWidget,
        campaign_id: str,
        scene: Any,
        observer: Any,
        config: HudConfig,
    ) -> WidgetSnapshot:
        fetcher = self.fetchers.get(w.id)
        render_hint = _normalize_render_hint(w.render_hint)
        if fetcher is None:
            return WidgetSnapshot(
                id=w.id,
                status=WidgetStatus.ERROR,
                error="no fetcher registered",
                title=w.title,
                render_hint=render_hint,
            )
        timeout = self.settings.aggregate_timeout_seconds_per_widget
        started = time.perf_counter()
        try:
            data = await asyncio.wait_for(
                fetcher(w, campaign_id, scene, observer),
                timeout=timeout,
            )
            return WidgetSnapshot(
                id=w.id,
                status=WidgetStatus.OK,
                data=data,
                title=w.title,
                render_hint=render_hint,
            )
        except TimeoutError:
            log.info(
                "hud widget %s timed out after %.2fs (campaign=%s)",
                w.id,
                time.perf_counter() - started,
                campaign_id,
            )
            return WidgetSnapshot(
                id=w.id,
                status=WidgetStatus.TIMEOUT,
                error=f"owner endpoint timeout after {timeout:.2f}s",
                title=w.title,
                render_hint=render_hint,
            )
        except Exception as e:
            log.exception("hud widget %s failed (campaign=%s)", w.id, campaign_id)
            return WidgetSnapshot(
                id=w.id,
                status=WidgetStatus.ERROR,
                error=str(e),
                title=w.title,
                render_hint=render_hint,
            )

    async def _scene(self, campaign_id: str) -> Any:
        if self.current_scene is None:
            return None
        try:
            return await self.current_scene(campaign_id)
        except Exception as e:
            log.warning("current_scene lookup failed for %s: %s", campaign_id, e)
            return None

    async def _context(self, campaign_id: str, scene: Any) -> EvaluationContext:
        if self.eval_context_builder is None:
            return EvaluationContext()
        try:
            return await self.eval_context_builder(campaign_id, scene)
        except Exception as e:
            log.warning("eval_context_builder failed for %s: %s", campaign_id, e)
            return EvaluationContext()

    async def available_widgets(self, campaign_id: str) -> list[HudWidget]:
        """Union of core + mechanics widgets — feeds the config editor."""
        return await self._collect_widgets(campaign_id)


__all__ = [
    "HudService",
    "HudServiceConfig",
    "WidgetFetcher",
]
