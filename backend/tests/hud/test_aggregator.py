"""Tests for the HUD aggregator service."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from grimoire.hud.config import HudConfigService
from grimoire.hud.expression import EvaluationContext
from grimoire.hud.service import HudService, HudServiceConfig
from grimoire.types.hud import (
    HudWidget,
    RenderHint,
    WidgetRead,
    WidgetStatus,
)


def _make_service(tmp_path: Path, **kwargs) -> HudService:
    cfg_svc = HudConfigService(tmp_path)
    return HudService(config_service=cfg_svc, **kwargs)


@pytest.mark.asyncio
async def test_aggregate_fans_out_in_parallel(tmp_path: Path) -> None:
    svc = _make_service(
        tmp_path,
        settings=HudServiceConfig(aggregate_timeout_seconds_per_widget=2.0),
    )

    async def slow(widget, *_):
        await asyncio.sleep(0.1)
        return {"id": widget.id}

    # Register fetchers for every core widget so they all fan out together.
    from grimoire.hud.widgets import CORE_WIDGETS

    for w in CORE_WIDGETS:
        svc.register_fetcher(w.id, slow)

    t0 = time.perf_counter()
    result = await svc.aggregate("c_1")
    elapsed = time.perf_counter() - t0
    # 12 widgets x 100ms serial would be 1.2s; parallel should be ~0.1s.
    # `core.temperature` is hidden by default so it never fans out.
    assert elapsed < 0.5, f"expected parallel fan-out, took {elapsed:.3f}s"
    statuses = {w.id: w.status for w in result.widgets}
    assert WidgetStatus.OK in statuses.values()
    assert "core.temperature" not in statuses  # filtered by visible_when=false


@pytest.mark.asyncio
async def test_per_widget_timeout_isolated(tmp_path: Path) -> None:
    svc = _make_service(
        tmp_path,
        settings=HudServiceConfig(aggregate_timeout_seconds_per_widget=0.05),
    )

    async def slow(widget, *_):
        await asyncio.sleep(0.5)
        return "never"

    async def fast(widget, *_):
        return {"ok": True}

    svc.register_fetcher("core.in-game-date", slow)
    svc.register_fetcher("core.in-game-time", fast)
    # Make sure the other core widgets don't error out the test
    from grimoire.hud.widgets import CORE_WIDGETS

    for w in CORE_WIDGETS:
        if w.id not in ("core.in-game-date", "core.in-game-time"):
            svc.register_fetcher(w.id, fast)

    result = await svc.aggregate("c_1")
    by_id = {w.id: w for w in result.widgets}
    assert by_id["core.in-game-date"].status == WidgetStatus.TIMEOUT
    assert by_id["core.in-game-time"].status == WidgetStatus.OK


@pytest.mark.asyncio
async def test_error_from_fetcher_isolated(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)

    async def boom(*_args):
        raise RuntimeError("owner exploded")

    async def fine(*_args):
        return "ok"

    from grimoire.hud.widgets import CORE_WIDGETS

    for w in CORE_WIDGETS:
        if w.id == "core.in-game-date":
            svc.register_fetcher(w.id, boom)
        else:
            svc.register_fetcher(w.id, fine)

    result = await svc.aggregate("c_1")
    by_id = {w.id: w for w in result.widgets}
    assert by_id["core.in-game-date"].status == WidgetStatus.ERROR
    assert "owner exploded" in (by_id["core.in-game-date"].error or "")
    # Others continue.
    assert by_id["core.in-game-time"].status == WidgetStatus.OK


@pytest.mark.asyncio
async def test_visible_when_filters_widget(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)

    async def fine(*_args):
        return "ok"

    # Custom mechanics widget hidden via expression.
    async def mech_widgets(_cid):
        return [
            HudWidget(
                id="wod.never",
                title="Never visible",
                visible_when="false",
                read=WidgetRead(endpoint="/never"),
            ),
            HudWidget(
                id="wod.always",
                title="Always",
                visible_when="true",
                read=WidgetRead(endpoint="/always"),
            ),
        ]

    svc.mechanics_widgets = mech_widgets

    async def ctx_builder(_cid, _scene):
        return EvaluationContext()

    svc.eval_context_builder = ctx_builder

    from grimoire.hud.widgets import CORE_WIDGETS

    for w in CORE_WIDGETS:
        svc.register_fetcher(w.id, fine)
    svc.register_fetcher("wod.always", fine)
    svc.register_fetcher("wod.never", fine)

    result = await svc.aggregate("c_1")
    ids = {w.id for w in result.widgets}
    assert "wod.never" not in ids
    assert "wod.always" in ids


@pytest.mark.asyncio
async def test_fetch_one_returns_single_widget(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)

    async def fetch(_w, _cid, _scene, _obs):
        return {"date": "1894-10-13"}

    svc.register_fetcher("core.in-game-date", fetch)
    snap = await svc.fetch_one("c_1", "core.in-game-date")
    assert snap.status == WidgetStatus.OK
    assert snap.data == {"date": "1894-10-13"}


@pytest.mark.asyncio
async def test_fetch_one_unknown_widget(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    snap = await svc.fetch_one("c_1", "does.not-exist")
    assert snap.status == WidgetStatus.ERROR
    assert "unknown widget" in (snap.error or "")


@pytest.mark.asyncio
async def test_no_fetcher_reports_error(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)
    # Don't register anything — every visible core widget should report an error.
    result = await svc.aggregate("c_1")
    assert all(
        w.status == WidgetStatus.ERROR and "no fetcher" in (w.error or "") for w in result.widgets
    )


@pytest.mark.asyncio
async def test_unknown_render_hint_falls_back_to_block(tmp_path: Path) -> None:
    svc = _make_service(tmp_path)

    async def mech_widgets(_cid):
        return [
            HudWidget(
                id="wod.strange",
                title="Strange",
                render_hint="hologram",  # not in the enum
                read=WidgetRead(endpoint="/x"),
            ),
        ]

    svc.mechanics_widgets = mech_widgets

    async def fine(*_args):
        return "ok"

    from grimoire.hud.widgets import CORE_WIDGETS

    for w in CORE_WIDGETS:
        svc.register_fetcher(w.id, fine)
    svc.register_fetcher("wod.strange", fine)

    snap = await svc.fetch_one("c_1", "wod.strange")
    assert snap.render_hint == RenderHint.BLOCK.value
