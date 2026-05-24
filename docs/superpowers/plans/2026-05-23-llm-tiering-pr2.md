# LLM Tiering PR 2: Integrated Extraction + Observability

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the `integrated_deltas` campaign flag so new campaigns default to single-LLM-call turns (narrator + extraction combined), add three observability events (`tier_resolved`, `summary_skipped`, `integrated_deltas_fallback`), and build the UI toggle.

**Architecture:** The codebase already has `ExtractionMode.TOGETHER` with a full pipeline: context builder appends tracker instructions, the model emits `<!-- TRACKER -->` JSON blocks, the extractor parses them, and falls back to `SEPARATE` on failure. PR 2 adds a campaign-level boolean (`integrated_deltas`) that overrides the extraction mode preference to `TOGETHER`, making this the default for new campaigns. Observability events emit on the existing `EventBus`. The UI toggle goes on the General tab.

**Tech Stack:** Python 3.12 (FastAPI, pydantic v2, pytest). TypeScript / React (vitest). SQLite via `aiosqlite`.

**Spec:** `docs/superpowers/specs/2026-05-23-llm-tiering-design.md` §2, §4 (integrated-deltas toggle), §7.

**Branch:** `feat/llm-tiering-pr1` (builds on PR 1 commits).

---

## File Structure

**New files:**
- `backend/tests/llm_gateway/test_tier_resolved.py` — observability event tests.
- `backend/tests/scenes/test_summary_skipped.py` — summary_skipped event tests.
- `backend/tests/orchestrator/test_integrated_deltas.py` — integrated_deltas flag + fallback event tests.

**Modified files:**
- `backend/src/grimoire/llm_gateway/routing.py` — add `resolve_with_source()` method.
- `backend/src/grimoire/llm_gateway/gateway.py` — emit `tier_resolved` after route resolution.
- `backend/src/grimoire/scenes/manager.py` — emit `summary_skipped` when cadence skips a summary.
- `backend/src/grimoire/orchestrator/service.py` — read `integrated_deltas` flag in `_select_extract_mode`; emit `integrated_deltas_fallback` after extraction.
- `backend/src/grimoire/api/campaigns.py` — `GET/PUT /campaigns/{id}/integrated-deltas` endpoint.
- `backend/tests/api/test_campaign_settings_routes.py` — endpoint contract tests.
- `backend/tests/llm_gateway/test_routing.py` — `resolve_with_source` tests.
- `frontend/src/api/campaign.ts` — client methods.
- `frontend/src/routes/CampaignSettings.tsx` — integrated-deltas toggle on General tab.

---

## Task 1: `resolve_with_source` on RouteResolver

The `tier_resolved` observability event needs to report *how* a route was resolved: per-task override, tier, or default. The current `resolve()` returns only a `Route`. Add a companion method that also reports the source.

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/routing.py`
- Test: `backend/tests/llm_gateway/test_routing.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/llm_gateway/test_routing.py`:

```python
def test_resolve_with_source_per_task() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    r.set_route("main", "openai.gpt-5", campaign_id="camp-1")
    route, source = r.resolve_with_source("main", "camp-1")
    assert route.raw == "openai.gpt-5"
    assert source == "per_task"


def test_resolve_with_source_tier() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    route, source = r.resolve_with_source("main", "camp-1")
    assert route.raw == "deepseek.pro"
    assert source == "tier"


def test_resolve_with_source_default() -> None:
    r = RouteResolver(default_routes={"main": "anthropic.opus"})
    route, source = r.resolve_with_source("main", "camp-1")
    assert route.raw == "anthropic.opus"
    assert source == "default"


def test_resolve_with_source_unknown_task_default() -> None:
    r = RouteResolver(default_routes={"weird.task": "anthropic.opus"})
    r.set_tier_route("camp-1", Tier.HEAVY, "deepseek.pro")
    route, source = r.resolve_with_source("weird.task", "camp-1")
    assert route.raw == "anthropic.opus"
    assert source == "default"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/llm_gateway/test_routing.py::test_resolve_with_source_per_task -v`
Expected: FAIL — `RouteResolver` has no `resolve_with_source` method.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/llm_gateway/routing.py`. Add this method to `RouteResolver` immediately after the existing `resolve` method:

```python
    def resolve_with_source(
        self, task: str, campaign_id: CampaignId | None = None
    ) -> tuple[Route, str]:
        """Like ``resolve`` but also returns the resolution source.

        Returns ``(route, source)`` where source is one of
        ``"per_task"``, ``"tier"``, or ``"default"``.
        """
        raw: str | None = None
        source = "default"
        if campaign_id is not None:
            raw = self._campaigns.get(campaign_id, {}).get(task)
            if raw is not None:
                source = "per_task"
            else:
                tier = tier_for_task(task)
                if tier is not None:
                    raw = self._tiers.get(campaign_id, {}).get(tier)
                    if raw is not None:
                        source = "tier"
        if raw is None:
            raw = self._defaults.get(task)
            source = "default"
        if raw is None:
            raise RouteNotFoundError(task)
        return Route.parse(raw), source
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/llm_gateway/test_routing.py -v`
Expected: all pass (existing + 4 new).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/llm_gateway/routing.py backend/tests/llm_gateway/test_routing.py
git commit -m "feat(llm_gateway): resolve_with_source reports per_task/tier/default origin"
```

---

## Task 2: `tier_resolved` observability event

Emit a `tier_resolved` event on every gateway completion call so the observability view shows which tier each task used and how it was resolved.

**Files:**
- Modify: `backend/src/grimoire/llm_gateway/gateway.py`
- Create: `backend/tests/llm_gateway/test_tier_resolved.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/llm_gateway/test_tier_resolved.py`:

```python
"""Verify the gateway emits tier_resolved on every completion."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from grimoire.event_bus import Event, EventBus
from grimoire.llm_gateway.gateway import LLMGatewayService
from grimoire.llm_gateway.tiers import Tier, tier_for_task


@pytest.fixture
def events() -> list[Event]:
    return []


@pytest.fixture
def bus(events: list[Event]) -> EventBus:
    b = EventBus()

    async def _collect(event: Event) -> None:
        events.append(event)

    b.subscribe("tier_resolved", _collect)
    return b


def test_tier_resolved_emitted(bus: EventBus, events: list[Event], tmp_path) -> None:
    """After resolve, the gateway should emit tier_resolved with task, tier, route, source."""
    from grimoire.llm_gateway.config import GatewayConfig
    from grimoire.llm_gateway.routing import Route
    from grimoire.types.llm import RetryPolicy, TimeoutPolicy

    cfg = GatewayConfig(retry=RetryPolicy(max_attempts=1), timeout=TimeoutPolicy())

    class _NoOpPlugins:
        def get_llm_provider(self, _id):
            return None
        def get_embedding_provider(self, _id):
            return None
        def get_imagegen_backend(self, _id):
            return None
        def llm_providers(self):
            return []
        def embedding_providers(self):
            return []

    gw = LLMGatewayService(plugins=_NoOpPlugins(), config=cfg, data_root=tmp_path)
    gw._event_bus = bus
    gw._router.set_route("main", "fake.model")
    gw._router.set_tier_route("camp-1", Tier.HEAVY, "tier.model")

    # We can't call _complete_inner (needs a real provider), so test the
    # helper directly.
    asyncio.run(gw._emit_tier_resolved("main", "camp-1"))

    assert len(events) == 1
    p = events[0].payload
    assert p["task"] == "main"
    assert p["tier"] == "heavy"
    assert p["source"] == "tier"
    assert p["route"] == "tier.model"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/llm_gateway/test_tier_resolved.py -v`
Expected: FAIL — `LLMGatewayService` has no `_emit_tier_resolved` method.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/llm_gateway/gateway.py`. Add this method to `LLMGatewayService` after the existing `_emit` helper (around line 149):

```python
    async def _emit_tier_resolved(self, task: str, campaign_id: CampaignId | None) -> None:
        """Emit a ``tier_resolved`` event after route resolution."""
        try:
            route, source = self._router.resolve_with_source(task, campaign_id)
        except Exception:
            return
        from grimoire.llm_gateway.tiers import tier_for_task

        tier = tier_for_task(task)
        await self._emit("tier_resolved", {
            "task": task,
            "tier": tier.value if tier is not None else None,
            "route": route.raw,
            "source": source,
            "campaign_id": campaign_id,
        })
```

Then in `_complete_inner` (around line 474), after `primary = self._router.resolve(task, campaign_id)`, add:

```python
        await self._emit_tier_resolved(task, campaign_id)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/llm_gateway/test_tier_resolved.py tests/llm_gateway/test_routing.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/llm_gateway/gateway.py backend/tests/llm_gateway/test_tier_resolved.py
git commit -m "feat(observability): emit tier_resolved on every gateway completion"
```

---

## Task 3: `summary_skipped` observability event

Emit `summary_skipped` when `running_every_n_posts == 0` suppresses a running summary or `final_on_close == false` skips the final summary LLM call.

**Files:**
- Modify: `backend/src/grimoire/scenes/manager.py`
- Create: `backend/tests/scenes/test_summary_skipped.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/scenes/test_summary_skipped.py`:

```python
"""Verify summary_skipped events are emitted."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from grimoire.scenes.manager import SceneManager, SceneManagerConfig


@dataclass
class _RecordingBus:
    events: list[tuple[str, dict]] = field(default_factory=list)

    async def emit(self, event) -> None:
        self.events.append((event.type, event.payload))

    def subscribe(self, *_a, **_kw) -> None:
        pass


@pytest.fixture
def bus() -> _RecordingBus:
    return _RecordingBus()


@pytest.fixture
def manager(tmp_path, bus) -> SceneManager:
    cfg = SceneManagerConfig()
    cfg.running_summary_every_n_posts = 5
    return SceneManager(data_root=tmp_path, event_bus=bus, config=cfg)


def test_summary_skipped_emitted_when_cadence_zero(manager, bus) -> None:
    """When override=0 and post_count would have triggered, emit summary_skipped."""
    manager._emit_summary_skipped_if_suppressed(
        post_count=5,
        override=0,
        campaign_id="camp-1",
        scene_id="scene-1",
    )
    assert len(bus.events) == 1
    etype, payload = bus.events[0]
    assert etype == "summary_skipped"
    assert payload["reason"] == "running_cadence_disabled"
    assert payload["scene_id"] == "scene-1"


def test_no_skip_event_when_cadence_nonzero_and_not_due(manager, bus) -> None:
    """No event when cadence is nonzero and post_count doesn't match."""
    manager._emit_summary_skipped_if_suppressed(
        post_count=3,
        override=5,
        campaign_id="camp-1",
        scene_id="scene-1",
    )
    assert len(bus.events) == 0


def test_final_summary_skipped_event(manager, bus) -> None:
    """Emit summary_skipped when final_on_close is False."""
    manager._emit_final_summary_skipped(
        campaign_id="camp-1",
        scene_id="scene-1",
    )
    assert len(bus.events) == 1
    etype, payload = bus.events[0]
    assert etype == "summary_skipped"
    assert payload["reason"] == "final_on_close_disabled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/scenes/test_summary_skipped.py -v`
Expected: FAIL — no `_emit_summary_skipped_if_suppressed` method.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/scenes/manager.py`. Add two helper methods to the `SceneManager` class near the existing `_should_emit_running_summary`:

```python
    def _emit_summary_skipped_if_suppressed(
        self,
        *,
        post_count: int,
        override: int | None,
        campaign_id: str,
        scene_id: str,
    ) -> None:
        """Emit ``summary_skipped`` when cadence=0 suppresses a summary that
        the default cadence would have fired.

        Synchronous because the caller dispatches via ``asyncio.ensure_future``
        in the hot path — we build the event inline and let the bus handle it.
        """
        if override is not None and override <= 0:
            default_n = self.config.running_summary_every_n_posts
            if default_n > 0 and post_count > 0 and post_count % default_n == 0:
                from grimoire.event_bus import Event

                asyncio.ensure_future(
                    self._bus.emit(Event(type="summary_skipped", payload={
                        "campaign_id": campaign_id,
                        "scene_id": scene_id,
                        "reason": "running_cadence_disabled",
                        "post_count": post_count,
                    }))
                )

    def _emit_final_summary_skipped(
        self,
        *,
        campaign_id: str,
        scene_id: str,
    ) -> None:
        from grimoire.event_bus import Event

        asyncio.ensure_future(
            self._bus.emit(Event(type="summary_skipped", payload={
                "campaign_id": campaign_id,
                "scene_id": scene_id,
                "reason": "final_on_close_disabled",
            }))
        )
```

Add `import asyncio` at the top of the file if not already present.

Then wire the calls into the existing flow. In the post-append section (around line 653-662), after the cadence check:

Find:
```python
            override = await self._campaign_summary_cadence(scene.campaign_id)
            if self._should_emit_running_summary(
                post_count=scene.post_count,
                override=override,
            ):
                await self._emit(
                    RUNNING_SUMMARY_DUE,
                    scene,
                    post_count=scene.post_count,
                )
```

Replace with:
```python
            override = await self._campaign_summary_cadence(scene.campaign_id)
            if self._should_emit_running_summary(
                post_count=scene.post_count,
                override=override,
            ):
                await self._emit(
                    RUNNING_SUMMARY_DUE,
                    scene,
                    post_count=scene.post_count,
                )
            else:
                self._emit_summary_skipped_if_suppressed(
                    post_count=scene.post_count,
                    override=override,
                    campaign_id=scene.campaign_id,
                    scene_id=scene.id,
                )
```

In `close_scene` (around line 537-541), after the `else` branch where `final_on_close` is false:

Find:
```python
            if self._should_run_final_summary(final_on_close_override=final_override):
                final_summary, key_beats = await self._final_summary(scene, posts)
            else:
                final_summary = scene.running_summary or ""
                key_beats = []
```

Replace with:
```python
            if self._should_run_final_summary(final_on_close_override=final_override):
                final_summary, key_beats = await self._final_summary(scene, posts)
            else:
                final_summary = scene.running_summary or ""
                key_beats = []
                self._emit_final_summary_skipped(
                    campaign_id=scene.campaign_id,
                    scene_id=scene.id,
                )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/scenes/test_summary_skipped.py tests/scenes/test_summary_cadence.py -v`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/scenes/manager.py backend/tests/scenes/test_summary_skipped.py
git commit -m "feat(observability): emit summary_skipped when cadence or final_on_close suppresses"
```

---

## Task 4: `integrated_deltas` flag overrides extraction mode

When `campaigns.config["integrated_deltas"]` is `true`, the orchestrator should force `ExtractionMode.TOGETHER` regardless of `ExtractorConfig.mode`. This makes new campaigns (which will default to `integrated_deltas: true`) use the single-call narrator+extraction path.

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py`
- Create: `backend/tests/orchestrator/test_integrated_deltas.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/orchestrator/test_integrated_deltas.py`:

```python
"""integrated_deltas flag overrides extraction mode selection."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from grimoire.extractor.config import ExtractorConfig
from grimoire.llm_gateway.capabilities import ProviderCapabilities
from grimoire.orchestrator.service import OrchestratorService
from grimoire.types.extraction_modes import ExtractionMode


class _FakeAutoDisable:
    async def together_disabled(self, _pid: str, _m: str) -> bool:
        return False

    async def tool_use_disabled(self, _pid: str, _m: str) -> bool:
        return False


class _FakeStore:
    def __init__(self, config_json: str = "{}") -> None:
        self._config = config_json

    class _DB:
        def __init__(self, config_json: str) -> None:
            self._config = config_json

        async def fetchone(self, _q: str, _p: tuple) -> dict | None:
            return {"config": self._config}

    @property
    def db(self):
        return self._DB(self._config)


async def _resolve_mode(
    extractor_config: ExtractorConfig,
    campaign_config_json: str,
) -> ExtractionMode:
    """Helper that builds a minimal orchestrator and calls _select_extract_mode."""
    orch = OrchestratorService.__new__(OrchestratorService)
    orch._extractor_config = extractor_config
    orch._auto_disable = _FakeAutoDisable()
    orch._store = _FakeStore(campaign_config_json)
    orch._config = type("C", (), {"main_llm_task": "main"})()
    # No gateway wired → _select_extract_mode skips the resolve() call
    # and just uses select_mode with the config preference.
    orch._gateway = object()
    return await orch._select_extract_mode(campaign_id="camp-1")


@pytest.mark.asyncio
async def test_integrated_deltas_true_forces_together() -> None:
    cfg = ExtractorConfig(mode=ExtractionMode.SEPARATE)
    mode = await _resolve_mode(cfg, json.dumps({"integrated_deltas": True}))
    assert mode == ExtractionMode.TOGETHER


@pytest.mark.asyncio
async def test_integrated_deltas_false_keeps_separate() -> None:
    cfg = ExtractorConfig(mode=ExtractionMode.SEPARATE)
    mode = await _resolve_mode(cfg, json.dumps({"integrated_deltas": False}))
    assert mode == ExtractionMode.SEPARATE


@pytest.mark.asyncio
async def test_integrated_deltas_absent_keeps_config() -> None:
    cfg = ExtractorConfig(mode=ExtractionMode.SEPARATE)
    mode = await _resolve_mode(cfg, json.dumps({}))
    assert mode == ExtractionMode.SEPARATE


@pytest.mark.asyncio
async def test_integrated_deltas_true_with_auto_uses_together() -> None:
    cfg = ExtractorConfig(mode=ExtractionMode.AUTO)
    mode = await _resolve_mode(cfg, json.dumps({"integrated_deltas": True}))
    assert mode == ExtractionMode.TOGETHER
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/orchestrator/test_integrated_deltas.py -v`
Expected: FAIL — `test_integrated_deltas_true_forces_together` returns `SEPARATE` (no flag wiring yet).

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/orchestrator/service.py`. Find the `_select_extract_mode` method (around line 2595). Add a campaign config check at the top, before the existing `select_mode` call.

Find:
```python
    async def _select_extract_mode(
        self,
        *,
        campaign_id: CampaignId,
        aux_task: Any | None = None,
    ) -> ExtractionMode:
```

After the docstring and before the existing `provider_id = "unknown"` line, add:

```python
        if await self._campaign_integrated_deltas(campaign_id):
            return ExtractionMode.TOGETHER
```

Then add the helper method on the class (place it after `_select_extract_mode`):

```python
    async def _campaign_integrated_deltas(self, campaign_id: CampaignId) -> bool:
        """Read ``campaigns.config["integrated_deltas"]``.

        Returns ``True`` when the campaign has opted into the integrated
        narrator+extraction flow (``ExtractionMode.TOGETHER``). Returns
        ``False`` when the flag is absent or explicitly ``false`` so that
        existing campaigns are unaffected.
        """
        store = getattr(self, "_store", None)
        if store is None:
            return False
        try:
            row = await store.db.fetchone(
                "SELECT config FROM campaigns WHERE id = ?", (campaign_id,)
            )
        except Exception:
            return False
        if not row:
            return False
        raw = row.get("config") if hasattr(row, "get") else row["config"]
        if not raw:
            return False
        import json as _json

        try:
            data = _json.loads(raw)
        except (TypeError, ValueError):
            return False
        return bool(data.get("integrated_deltas")) if isinstance(data, dict) else False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/orchestrator/test_integrated_deltas.py -v`
Expected: 4 passed.

Then sanity-check existing orchestrator tests:
Run: `cd backend && pytest tests/orchestrator/ -v --timeout=30`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/orchestrator/service.py backend/tests/orchestrator/test_integrated_deltas.py
git commit -m "feat(orchestrator): integrated_deltas campaign flag forces TOGETHER extraction"
```

---

## Task 5: `integrated_deltas_fallback` observability event

When the TOGETHER extraction mode falls back to SEPARATE (because the model didn't emit a tracker block or the JSON was malformed), emit an `integrated_deltas_fallback` event. The extractor already appends `ExtractionFlag` entries with codes `together_no_tracker` and `together_malformed` — we detect those flags post-extraction and emit a bus event.

**Files:**
- Modify: `backend/src/grimoire/orchestrator/service.py`
- Add to: `backend/tests/orchestrator/test_integrated_deltas.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/orchestrator/test_integrated_deltas.py`:

```python
from grimoire.event_bus import Event, EventBus
from grimoire.types.extraction import ExtractionFlag, ExtractionResult, FlagLevel


def test_fallback_event_emitted_on_together_flags() -> None:
    """When extraction flags contain together_no_tracker, emit the fallback event."""
    events: list[Event] = []
    bus = EventBus()

    async def _collect(ev: Event) -> None:
        events.append(ev)

    bus.subscribe("integrated_deltas_fallback", _collect)

    orch = OrchestratorService.__new__(OrchestratorService)
    orch._bus = bus

    result = ExtractionResult(
        flags=[
            ExtractionFlag(
                level=FlagLevel.WARNING,
                code="together_no_tracker",
                message="no tracker block found — fell back to SEPARATE",
            )
        ]
    )

    import asyncio

    asyncio.run(
        orch._emit_integrated_deltas_fallback(
            extraction=result,
            turn_id="turn-1",
            campaign_id="camp-1",
        )
    )
    assert len(events) == 1
    p = events[0].payload
    assert p["turn_id"] == "turn-1"
    assert p["reason"] == "no_block"


def test_no_fallback_event_when_no_together_flags() -> None:
    events: list[Event] = []
    bus = EventBus()

    async def _collect(ev: Event) -> None:
        events.append(ev)

    bus.subscribe("integrated_deltas_fallback", _collect)

    orch = OrchestratorService.__new__(OrchestratorService)
    orch._bus = bus

    result = ExtractionResult()

    import asyncio

    asyncio.run(
        orch._emit_integrated_deltas_fallback(
            extraction=result,
            turn_id="turn-1",
            campaign_id="camp-1",
        )
    )
    assert len(events) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/orchestrator/test_integrated_deltas.py::test_fallback_event_emitted_on_together_flags -v`
Expected: FAIL — no `_emit_integrated_deltas_fallback` method.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/orchestrator/service.py`. Add this method to the `OrchestratorService` class:

```python
    async def _emit_integrated_deltas_fallback(
        self,
        *,
        extraction: ExtractionResult | None,
        turn_id: TurnId,
        campaign_id: CampaignId,
    ) -> None:
        """Emit ``integrated_deltas_fallback`` when TOGETHER mode fell back."""
        if extraction is None:
            return
        _FALLBACK_CODES = {"together_no_tracker": "no_block", "together_malformed": "json_parse"}
        for flag in getattr(extraction, "flags", []):
            code = getattr(flag, "code", None)
            if code in _FALLBACK_CODES:
                await self._emit_turn_event(
                    "integrated_deltas_fallback",
                    turn_id,
                    campaign_id,
                    "",
                    reason=_FALLBACK_CODES[code],
                )
                return
```

Then wire it into `_continue_turn_after_pre_roll`. After the `deltas_extracted` event emission (around line 2184), add:

```python
        await self._emit_integrated_deltas_fallback(
            extraction=extraction,
            turn_id=turn_id,
            campaign_id=campaign_id,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/orchestrator/test_integrated_deltas.py -v`
Expected: 6 passed (4 from task 4 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/orchestrator/service.py backend/tests/orchestrator/test_integrated_deltas.py
git commit -m "feat(observability): emit integrated_deltas_fallback on TOGETHER->SEPARATE fallback"
```

---

## Task 6: REST endpoint `GET/PUT /campaigns/{id}/integrated-deltas`

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py`
- Modify: `backend/tests/api/test_campaign_settings_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/api/test_campaign_settings_routes.py`:

```python
def test_integrated_deltas_default_false(settings_client) -> None:
    resp = settings_client.get("/api/campaigns/camp-1/integrated-deltas")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": False}


def test_integrated_deltas_round_trip(settings_client) -> None:
    resp = settings_client.put(
        "/api/campaigns/camp-1/integrated-deltas",
        json={"enabled": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True}

    resp = settings_client.get("/api/campaigns/camp-1/integrated-deltas")
    assert resp.json()["enabled"] is True


def test_integrated_deltas_disable(settings_client) -> None:
    settings_client.put(
        "/api/campaigns/camp-1/integrated-deltas",
        json={"enabled": True},
    )
    resp = settings_client.put(
        "/api/campaigns/camp-1/integrated-deltas",
        json={"enabled": False},
    )
    assert resp.json()["enabled"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/api/test_campaign_settings_routes.py::test_integrated_deltas_default_false -v`
Expected: FAIL with 404.

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/api/campaigns.py`. Add a payload class near the other settings payloads:

```python
class IntegratedDeltasPayload(BaseModel):
    """Toggle for integrated narrator+extraction mode.

    When ``enabled`` is ``True``, the orchestrator combines the narrator
    LLM call with delta extraction into a single response
    (``ExtractionMode.TOGETHER``). When ``False`` or absent, extraction
    runs as a separate LLM call (existing behavior).
    """

    enabled: bool = False
```

Then add the two endpoints alongside the existing summaries endpoints:

```python
@router.get("/{campaign_id}/integrated-deltas")
async def get_integrated_deltas(
    campaign_id: str,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    return {"enabled": bool(cfg.get("integrated_deltas", False))}


@router.put("/{campaign_id}/integrated-deltas")
async def set_integrated_deltas(
    campaign_id: str,
    payload: IntegratedDeltasPayload,
    state_store: StateStoreDep,
) -> Any:
    row = await _require_campaign_row(state_store, campaign_id)
    cfg = _load_campaign_config(row)
    cfg["integrated_deltas"] = payload.enabled
    await _write_campaign_config(state_store, campaign_id, cfg)
    return {"enabled": payload.enabled}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/api/test_campaign_settings_routes.py -v -k integrated`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/campaigns.py backend/tests/api/test_campaign_settings_routes.py
git commit -m "feat(api): GET/PUT /campaigns/{id}/integrated-deltas toggle"
```

---

## Task 7: New campaigns default `integrated_deltas: true`

When creating a campaign, seed `integrated_deltas: true` in the config so new campaigns use the single-call flow by default.

**Files:**
- Modify: `backend/src/grimoire/api/campaigns.py`
- Modify: `backend/tests/api/test_campaign_settings_routes.py` (or `test_campaigns_routes.py`)

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/api/test_campaign_settings_routes.py` (or the file that tests campaign creation — use whichever has the create-campaign fixture):

```python
def test_new_campaign_defaults_integrated_deltas_true(settings_client) -> None:
    """Newly created campaigns should have integrated_deltas=True in config."""
    # Create campaign via the normal endpoint.
    resp = settings_client.post(
        "/api/campaigns",
        json={"id": "new-camp", "name": "New", "greeting_id": None, "composition": {"worlds": []}},
    )
    assert resp.status_code in (200, 201)

    resp = settings_client.get("/api/campaigns/new-camp/integrated-deltas")
    assert resp.json()["enabled"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && pytest tests/api/test_campaign_settings_routes.py::test_new_campaign_defaults_integrated_deltas_true -v`
Expected: FAIL — `enabled` is `False` (no seeding yet).

- [ ] **Step 3: Write minimal implementation**

Edit `backend/src/grimoire/api/campaigns.py`. Find the campaign creation handler (the `create_campaign` endpoint). In the block where tier defaults are seeded (the `try:` block added in PR 1 task 10), add the `integrated_deltas` seed right after the tier seeding:

Find the existing tier-seeding `try` block. After the `await gateway.set_tier_route(...)` calls and before the `except Exception:`, add:

```python
            # Seed integrated_deltas=True for new campaigns so they
            # default to single-call narrator+extraction.
            row = await state_store.db.fetchone(
                "SELECT config FROM campaigns WHERE id = ?", (payload.id,)
            )
            cfg = _load_campaign_config(row or {})
            cfg["integrated_deltas"] = True
            await _write_campaign_config(state_store, payload.id, cfg)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && pytest tests/api/test_campaign_settings_routes.py::test_new_campaign_defaults_integrated_deltas_true -v`
Expected: PASS.

Then sanity-check:
Run: `cd backend && pytest tests/api/ -v --timeout=30`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add backend/src/grimoire/api/campaigns.py backend/tests/api/test_campaign_settings_routes.py
git commit -m "feat(campaigns): new campaigns default to integrated_deltas=true"
```

---

## Task 8: Frontend API client methods

**Files:**
- Modify: `frontend/src/api/campaign.ts`

- [ ] **Step 1: Add the integrated-deltas client methods**

Find the existing `setSummaries` method on the `campaignApi` object in `frontend/src/api/campaign.ts`. Add after it:

```typescript
  getIntegratedDeltas: (campaignId: string) =>
    api.get<{ enabled: boolean }>(
      `/api/campaigns/${enc(campaignId)}/integrated-deltas`,
    ),

  setIntegratedDeltas: (campaignId: string, body: { enabled: boolean }) =>
    api.put<{ enabled: boolean }>(
      `/api/campaigns/${enc(campaignId)}/integrated-deltas`,
      body,
    ),
```

- [ ] **Step 2: Verify TypeScript compiles**

Run: `cd frontend && pnpm tsc --noEmit`
Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/api/campaign.ts
git commit -m "feat(api-client): integrated-deltas client methods"
```

---

## Task 9: Frontend — Integrated-deltas toggle on General tab

Add a checkbox below the existing name/description form on the General tab. Since the General tab uses manual-save and the integrated-deltas toggle is an independent setting stored via its own endpoint, render it as a separate auto-saved section below the form.

**Files:**
- Modify: `frontend/src/routes/CampaignSettings.tsx`

- [ ] **Step 1: Add the toggle component**

In `CampaignSettings.tsx`, add a new component near the `GeneralTab` definition:

```tsx
function IntegratedDeltasToggle({ campaignId }: { campaignId: string }) {
  const { value, setValue, status, error, ready } = useAutoSavedResource<{ enabled: boolean }>(
    campaignId,
    "/integrated-deltas",
    { enabled: false },
  );

  return (
    <div className="settings-form" style={{ marginTop: "var(--space-4)" }}>
      <label className="wizard-field wizard-field-inline">
        <input
          type="checkbox"
          checked={value.enabled}
          onChange={(e) => setValue({ enabled: e.target.checked })}
          disabled={!ready}
        />
        <span>Combine narrator + delta extraction into one LLM call</span>
      </label>
      <p className="wizard-step-help">
        When enabled, the narrator response includes a structured delta block
        inline, eliminating the separate extraction LLM call. Falls back
        automatically if the model omits or malforms the block.
      </p>
      <SaveIndicator status={status} error={error} />
    </div>
  );
}
```

- [ ] **Step 2: Render the toggle inside GeneralTab**

Find the existing `GeneralTab` component's return statement. After the closing `</form>` tag (around line 209), add:

```tsx
      <IntegratedDeltasToggle campaignId={campaign.id} />
```

So the return becomes:

```tsx
  return (
    <>
      <form
        className="settings-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        {/* ... existing name/description/save ... */}
      </form>
      <IntegratedDeltasToggle campaignId={campaign.id} />
    </>
  );
```

- [ ] **Step 3: Verify TypeScript compiles**

Run: `cd frontend && pnpm tsc --noEmit`
Expected: zero errors.

- [ ] **Step 4: Verify visually**

Run the frontend dev server. Open a campaign's settings → General tab. Below the name/description form, the checkbox should appear. Toggling it should auto-save. A new campaign should show it checked by default.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/routes/CampaignSettings.tsx
git commit -m "feat(campaign-settings): integrated-deltas toggle on General tab"
```

---

## Task 10: Smoke test end-to-end

**Files:** none (manual verification).

- [ ] **Step 1: Restart the backend** so all changes load.

- [ ] **Step 2: Verify integrated_deltas on a new campaign**

Create a new campaign. Confirm:
- `GET /api/campaigns/<id>/integrated-deltas` returns `{"enabled": true}`
- The General tab shows the checkbox as checked

- [ ] **Step 3: Verify extraction mode override**

With `integrated_deltas=true`, submit a turn. Check the backend logs:
- The extraction mode should be `TOGETHER`
- If the model includes a `<!-- TRACKER -->` block, only one Heavy LLM call should fire (no separate extractor call)
- If the model omits the tracker, the fallback to SEPARATE should fire and an `integrated_deltas_fallback` event should appear in the event log

- [ ] **Step 4: Verify observability events**

Check the observability/inspector panel:
- `tier_resolved` events should appear for every gateway call
- `summary_skipped` events should appear when cadence=0 is set and a post lands on a cadence boundary

- [ ] **Step 5: Verify existing campaign unchanged**

Open an existing campaign (no `integrated_deltas` in its config). The General tab checkbox should be unchecked. Extraction should use the existing mode (`SEPARATE` or whatever was configured).

- [ ] **Step 6: Commit nothing** — this is verification only.

---

## Final check

- [ ] All backend tests green:

```bash
cd backend && pytest -x
```

- [ ] All frontend tests green:

```bash
cd frontend && pnpm test
```

- [ ] TypeScript compiles:

```bash
cd frontend && pnpm tsc --noEmit
```

- [ ] PR description ready (link to spec §2, §4, §7).

---

## What's NOT in this plan

- **New inline parser with `<!--deltas-->` delimiters** — the existing `ExtractionMode.TOGETHER` with `<!-- TRACKER -->` delimiters already implements the same pattern (prompt injection, block parsing, delta projection, SEPARATE fallback). Building a second parser would duplicate infrastructure. The `integrated_deltas` flag simply forces `TOGETHER` mode.
- **Different JSON schema for the delta block** — the existing tracker schema (`facts`, `character_updates`, etc.) is richer than the spec's proposed slim schema and already tested. Changing the schema would break the existing `project_tracker_to_deltas` pipeline for no benefit.
- **Combining multiple Light calls** — explicitly a non-goal per spec §Non-goals.
