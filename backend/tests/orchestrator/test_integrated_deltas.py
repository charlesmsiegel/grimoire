"""integrated_deltas flag overrides extraction mode selection."""

from __future__ import annotations

import json

from grimoire.event_bus import Event, EventBus
from grimoire.extractor.config import ExtractorConfig
from grimoire.orchestrator.service import OrchestratorService
from grimoire.types.extraction import ExtractionFlag, ExtractionResult, FlagLevel
from grimoire.types.extraction_modes import ExtractionMode


class _FakeAutoDisable:
    async def together_disabled(self, _pid: str, _m: str) -> bool:
        return False

    async def tool_use_disabled(self, _pid: str, _m: str) -> bool:
        return False


class _FakeDB:
    def __init__(self, config_json: str) -> None:
        self._config = config_json

    async def fetchone(self, _q: str, _p: tuple = ()) -> dict | None:
        return {"config": self._config}


class _FakeStore:
    def __init__(self, config_json: str = "{}") -> None:
        self.db = _FakeDB(config_json)


class _MinimalConfig:
    main_llm_task = "main"


async def _resolve_mode(
    extractor_config: ExtractorConfig,
    campaign_config_json: str,
) -> ExtractionMode:
    """Build a minimal orchestrator and call _select_extract_mode."""
    orch = OrchestratorService.__new__(OrchestratorService)
    orch._extractor_config = extractor_config
    orch._auto_disable = _FakeAutoDisable()
    orch._store = _FakeStore(campaign_config_json)
    orch._config = _MinimalConfig()
    orch._gateway = object()
    return await orch._select_extract_mode(campaign_id="camp-1")


async def test_integrated_deltas_true_forces_together() -> None:
    cfg = ExtractorConfig(mode=ExtractionMode.SEPARATE)
    mode = await _resolve_mode(cfg, json.dumps({"integrated_deltas": True}))
    assert mode == ExtractionMode.TOGETHER


async def test_integrated_deltas_false_keeps_separate() -> None:
    cfg = ExtractorConfig(mode=ExtractionMode.SEPARATE)
    mode = await _resolve_mode(cfg, json.dumps({"integrated_deltas": False}))
    assert mode == ExtractionMode.SEPARATE


async def test_integrated_deltas_absent_keeps_config() -> None:
    cfg = ExtractorConfig(mode=ExtractionMode.SEPARATE)
    mode = await _resolve_mode(cfg, json.dumps({}))
    assert mode == ExtractionMode.SEPARATE


async def test_integrated_deltas_true_overrides_auto() -> None:
    cfg = ExtractorConfig(mode=ExtractionMode.AUTO)
    mode = await _resolve_mode(cfg, json.dumps({"integrated_deltas": True}))
    assert mode == ExtractionMode.TOGETHER


async def test_fallback_event_emitted_on_together_no_tracker() -> None:
    """When extraction flags contain together_no_tracker, emit the fallback event."""
    events: list[Event] = []
    bus = EventBus()

    async def _collect(ev: Event) -> None:
        events.append(ev)

    bus.subscribe("integrated_deltas_fallback", _collect)

    orch = OrchestratorService.__new__(OrchestratorService)
    orch._bus = bus
    orch._ws_push = None

    result = ExtractionResult(
        flags=[
            ExtractionFlag(
                level=FlagLevel.WARNING,
                code="together_no_tracker",
                message="no tracker block found",
            )
        ]
    )

    await orch._emit_integrated_deltas_fallback(
        extraction=result,
        turn_id="turn-1",
        campaign_id="camp-1",
        scene_id="scene-1",
    )
    assert len(events) == 1
    p = events[0].payload
    assert p["turn_id"] == "turn-1"
    assert p["reason"] == "no_block"
    assert p["campaign_id"] == "camp-1"


async def test_fallback_event_emitted_on_together_malformed() -> None:
    events: list[Event] = []
    bus = EventBus()

    async def _collect(ev: Event) -> None:
        events.append(ev)

    bus.subscribe("integrated_deltas_fallback", _collect)

    orch = OrchestratorService.__new__(OrchestratorService)
    orch._bus = bus
    orch._ws_push = None

    result = ExtractionResult(
        flags=[
            ExtractionFlag(
                level=FlagLevel.WARNING,
                code="together_malformed",
                message="tracker malformed",
            )
        ]
    )

    await orch._emit_integrated_deltas_fallback(
        extraction=result,
        turn_id="turn-1",
        campaign_id="camp-1",
        scene_id="scene-1",
    )
    assert len(events) == 1
    assert events[0].payload["reason"] == "json_parse"


async def test_no_fallback_event_when_no_together_flags() -> None:
    events: list[Event] = []
    bus = EventBus()

    async def _collect(ev: Event) -> None:
        events.append(ev)

    bus.subscribe("integrated_deltas_fallback", _collect)

    orch = OrchestratorService.__new__(OrchestratorService)
    orch._bus = bus
    orch._ws_push = None

    result = ExtractionResult()

    await orch._emit_integrated_deltas_fallback(
        extraction=result,
        turn_id="turn-1",
        campaign_id="camp-1",
        scene_id="scene-1",
    )
    assert len(events) == 0


async def test_no_fallback_event_when_extraction_is_none() -> None:
    events: list[Event] = []
    bus = EventBus()

    async def _collect(ev: Event) -> None:
        events.append(ev)

    bus.subscribe("integrated_deltas_fallback", _collect)

    orch = OrchestratorService.__new__(OrchestratorService)
    orch._bus = bus
    orch._ws_push = None

    await orch._emit_integrated_deltas_fallback(
        extraction=None,
        turn_id="turn-1",
        campaign_id="camp-1",
        scene_id="scene-1",
    )
    assert len(events) == 0
