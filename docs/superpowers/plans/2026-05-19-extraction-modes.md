# Extraction Modes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Land everything in `docs/superpowers/specs/2026-05-19-extraction-modes-design.md`. Foundation for `auxiliary-tasks` (`ExtractionMode.NONE` short-circuit).

**Architecture:** Six branches, mostly independent after A.

- **A** `feature/extmodes-A-types-caps` — `ExtractionMode` enum, `ProviderCapabilities` static table, mode-health migration.
- **B** `feature/extmodes-B-select-mode` — `select_mode(...)` decision function with auto-disable lookup.
- **C** `feature/extmodes-C-extract-signature` — `Extractor.extract()` gains `mode` / `together_tracker_text` / `tool_calls` parameters; routing branches.
- **D** `feature/extmodes-D-together` — Together-mode tracker parser + sanity merger; frontend streaming tracker buffer.
- **E** `feature/extmodes-E-tool-use` — Tool-use mode tool declarations + tool-call projector.
- **F** `feature/extmodes-F-context-builder` — Context Builder `extractor_mode` parameter; tracker-instruction / tool-declarations appended per mode.

**Tech Stack:** Python 3.12, FastAPI, pytest-asyncio, Pydantic v2; frontend TS/React.

---

## Conventions

Standard plan conventions (see other plans).
**Latest migration as of writing:** 023; this plan claims `025_extractor_mode_health.sql` after transient-state's 024 + swipes' 024 (renumber if conflict).

---

## Branch setup

- [ ] **Step S1: Worktrees**

```powershell
git worktree add .worktrees/extmodes-A-types-caps     -b feature/extmodes-A-types-caps     main
git worktree add .worktrees/extmodes-B-select-mode    -b feature/extmodes-B-select-mode    main
git worktree add .worktrees/extmodes-C-extract-sig    -b feature/extmodes-C-extract-sig    main
git worktree add .worktrees/extmodes-D-together       -b feature/extmodes-D-together       main
git worktree add .worktrees/extmodes-E-tool-use       -b feature/extmodes-E-tool-use       main
git worktree add .worktrees/extmodes-F-context        -b feature/extmodes-F-context        main
```

---

# Branch A — Types + capabilities + migration

### Task A1: `ExtractionMode` enum

**Files:**
- Create: `backend/src/grimoire/types/extraction_modes.py` (or extend `types/extraction.py`).
- Test: `backend/tests/types/test_extraction_modes.py`.

- [ ] **Step 1: Failing test**

```python
from grimoire.types.extraction_modes import ExtractionMode


def test_enum_values():
    assert ExtractionMode.SEPARATE.value == "separate"
    assert ExtractionMode.TOGETHER.value == "together"
    assert ExtractionMode.TOOL_USE.value == "tool_use"
    assert ExtractionMode.NONE.value == "none"
    assert ExtractionMode.AUTO.value == "auto"
```

- [ ] **Step 2: Implement**

```python
# backend/src/grimoire/types/extraction_modes.py
from enum import StrEnum


class ExtractionMode(StrEnum):
    SEPARATE = "separate"
    TOGETHER = "together"
    TOOL_USE = "tool_use"
    NONE     = "none"
    AUTO     = "auto"
```

- [ ] **Step 3: Commit.**

### Task A2: ProviderCapabilities static table

**Files:**
- Create: `backend/src/grimoire/llm_gateway/capabilities.py`.
- Test: `backend/tests/llm_gateway/test_capabilities.py`.

- [ ] **Step 1: Failing tests**

```python
def test_anthropic_supports_tool_use():
    caps = capabilities_for("anthropic")
    assert caps.supports_tool_use is True
    assert caps.streaming_tool_use is True
    assert caps.max_tool_count >= 64


def test_unknown_provider_safe_default():
    caps = capabilities_for("brand-new-llm-co")
    assert caps.supports_tool_use is False
```

- [ ] **Step 2: Implement**

```python
# backend/src/grimoire/llm_gateway/capabilities.py
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    supports_tool_use: bool = False
    streaming_tool_use: bool = False
    max_tool_count: int = 0


_TABLE: dict[str, ProviderCapabilities] = {
    "anthropic":     ProviderCapabilities(supports_tool_use=True, streaming_tool_use=True, max_tool_count=128),
    "openai":        ProviderCapabilities(supports_tool_use=True, streaming_tool_use=True, max_tool_count=128),
    "google":        ProviderCapabilities(supports_tool_use=True, streaming_tool_use=False, max_tool_count=64),
    "openrouter":    ProviderCapabilities(supports_tool_use=False),
    "local-llamacpp": ProviderCapabilities(supports_tool_use=False),
}


def capabilities_for(provider_id: str) -> ProviderCapabilities:
    return _TABLE.get(provider_id, ProviderCapabilities())
```

- [ ] **Step 3: Wire into LLMGatewayService**

```python
# in backend/src/grimoire/llm_gateway/gateway.py
def capabilities_for(self, provider_id: str) -> ProviderCapabilities:
    from grimoire.llm_gateway.capabilities import capabilities_for as _table
    return _table(provider_id)
```

- [ ] **Step 4: Commit.**

### Task A3: Migration for mode-health

**Files:**
- Create: `backend/src/grimoire/storage/migrations/025_extractor_mode_health.sql`.
- Test: rely on existing migration application test.

- [ ] **Step 1: SQL**

```sql
-- backend/src/grimoire/storage/migrations/025_extractor_mode_health.sql
CREATE TABLE extractor_mode_health (
    provider_id   TEXT NOT NULL,
    model         TEXT NOT NULL,
    mode          TEXT NOT NULL,
    window_start  TEXT NOT NULL,
    total_calls   INTEGER NOT NULL DEFAULT 0,
    failures      INTEGER NOT NULL DEFAULT 0,
    disabled_at   TEXT,
    re_enabled_at TEXT,
    PRIMARY KEY (provider_id, model, mode)
);
```

- [ ] **Step 2: Commit + merge A.**

---

# Branch B — `select_mode`

### Task B1: Decision function

**Files:**
- Create: `backend/src/grimoire/extractor/mode_select.py`.
- Create: `backend/src/grimoire/extractor/auto_disable.py`.
- Test: `backend/tests/extractor/test_mode_select.py`, `test_auto_disable.py`.

- [ ] **Step 1: Failing tests covering the truth table**

```python
# backend/tests/extractor/test_mode_select.py
import pytest

from grimoire.extractor.mode_select import select_mode
from grimoire.types.extraction_modes import ExtractionMode


@pytest.fixture
def caps_tool_use():
    from grimoire.llm_gateway.capabilities import ProviderCapabilities
    return ProviderCapabilities(supports_tool_use=True, streaming_tool_use=True, max_tool_count=64)


@pytest.fixture
def caps_no_tool_use():
    from grimoire.llm_gateway.capabilities import ProviderCapabilities
    return ProviderCapabilities(supports_tool_use=False)


@pytest.fixture
def auto_disable_empty():
    class Stub:
        def together_disabled(self, p, m): return False
        def tool_use_disabled(self, p, m): return False
    return Stub()


def test_auxiliary_task_short_circuits_to_none(caps_tool_use, auto_disable_empty):
    from grimoire.auxiliary.types import AuxiliaryTask, TaskKind
    task = AuxiliaryTask(kind=TaskKind.BRAINSTORM)
    assert select_mode(
        campaign_config=ExtractorConfigFor(mode=ExtractionMode.TOGETHER),
        provider_caps=caps_tool_use,
        auto_disable=auto_disable_empty,
        aux_task=task,
        provider_id="anthropic",
        model="opus",
    ) == ExtractionMode.NONE


def test_auto_picks_tool_use_when_supported(caps_tool_use, auto_disable_empty):
    assert select_mode(
        campaign_config=ExtractorConfigFor(mode=ExtractionMode.AUTO),
        provider_caps=caps_tool_use,
        auto_disable=auto_disable_empty,
        aux_task=None,
        provider_id="anthropic",
        model="opus",
    ) == ExtractionMode.TOOL_USE


def test_auto_falls_to_together_when_no_tool_use(caps_no_tool_use, auto_disable_empty):
    assert select_mode(... ExtractionMode.AUTO, caps_no_tool_use, auto_disable_empty ...) == ExtractionMode.TOGETHER


def test_auto_falls_to_separate_when_both_disabled(...):
    ...


def test_preferred_tool_use_falls_to_separate_without_capability(...):
    ...


def test_preferred_together_falls_to_separate_when_auto_disabled(...):
    ...
```

- [ ] **Step 2: Implement**

```python
# backend/src/grimoire/extractor/mode_select.py
from grimoire.types.extraction_modes import ExtractionMode
from grimoire.llm_gateway.capabilities import ProviderCapabilities


def select_mode(
    *,
    campaign_config,
    provider_caps: ProviderCapabilities,
    auto_disable,
    aux_task,
    provider_id: str,
    model: str,
) -> ExtractionMode:
    if aux_task is not None:
        return ExtractionMode.NONE
    preferred = campaign_config.mode
    if preferred == ExtractionMode.AUTO:
        if provider_caps.supports_tool_use and not auto_disable.tool_use_disabled(provider_id, model):
            return ExtractionMode.TOOL_USE
        if not auto_disable.together_disabled(provider_id, model):
            return ExtractionMode.TOGETHER
        return ExtractionMode.SEPARATE
    if preferred == ExtractionMode.TOOL_USE and not provider_caps.supports_tool_use:
        return ExtractionMode.SEPARATE
    if preferred == ExtractionMode.TOGETHER and auto_disable.together_disabled(provider_id, model):
        return ExtractionMode.SEPARATE
    if preferred == ExtractionMode.TOOL_USE and auto_disable.tool_use_disabled(provider_id, model):
        return ExtractionMode.SEPARATE
    return preferred
```

- [ ] **Step 3: Implement AutoDisableState**

```python
# backend/src/grimoire/extractor/auto_disable.py
class AutoDisableState:
    def __init__(self, store, *, together_threshold=0.15, tool_use_threshold=0.10, min_samples=20):
        self.store = store
        self.together_threshold = together_threshold
        self.tool_use_threshold = tool_use_threshold
        self.min_samples = min_samples

    async def together_disabled(self, provider_id: str, model: str) -> bool:
        return await self._disabled(provider_id, model, "together", self.together_threshold)

    async def tool_use_disabled(self, provider_id: str, model: str) -> bool:
        return await self._disabled(provider_id, model, "tool_use", self.tool_use_threshold)

    async def record_call(self, provider_id, model, mode, *, success: bool):
        async with self.store.db.connect_write() as conn:
            await conn.execute(
                "INSERT INTO extractor_mode_health "
                "(provider_id, model, mode, window_start, total_calls, failures) "
                "VALUES (?, ?, ?, datetime('now'), 1, ?) "
                "ON CONFLICT(provider_id, model, mode) DO UPDATE SET "
                "  total_calls = total_calls + 1, "
                "  failures = failures + excluded.failures",
                (provider_id, model, mode, 0 if success else 1),
            )

    async def re_enable(self, provider_id, model, mode):
        async with self.store.db.connect_write() as conn:
            await conn.execute(
                "UPDATE extractor_mode_health "
                "SET re_enabled_at=datetime('now'), total_calls=0, failures=0, "
                "    disabled_at=NULL, window_start=datetime('now') "
                "WHERE provider_id=? AND model=? AND mode=?",
                (provider_id, model, mode),
            )

    async def _disabled(self, provider_id, model, mode, threshold) -> bool:
        async with self.store.db.connect_read() as conn:
            r = await conn.execute_fetchone(
                "SELECT total_calls, failures, disabled_at "
                "FROM extractor_mode_health WHERE provider_id=? AND model=? AND mode=?",
                (provider_id, model, mode),
            )
        if r is None:
            return False
        if r["disabled_at"] is not None:
            return True
        if r["total_calls"] < self.min_samples:
            return False
        return (r["failures"] / r["total_calls"]) >= threshold
```

- [ ] **Step 4: Tests PASS + commit + merge B.**

---

# Branch C — Extractor signature

### Task C1: Add mode + tracker + tool_calls params

**Files:**
- Modify: `backend/src/grimoire/extractor/service.py:extract` signature.
- Modify: `backend/src/grimoire/extractor/protocols.py:Extractor` protocol.
- Test: `backend/tests/extractor/test_modes.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_separate_mode_runs_three_strategies(extractor, scene_with_text):
    result = await extractor.extract(
        scene_with_text.last_post_text, scene_with_text, "c_1", snapshot,
        mode=ExtractionMode.SEPARATE,
        turn_id="t_1",
    )
    assert result.deltas  # merged from rule + LLM + heuristic strategies (current behaviour)


async def test_none_mode_returns_empty_result(extractor, scene_with_text):
    result = await extractor.extract(
        scene_with_text.last_post_text, scene_with_text, "c_1", snapshot,
        mode=ExtractionMode.NONE,
        turn_id="t_1",
    )
    assert result.deltas == []
    assert result.transient_updates == []


async def test_together_mode_uses_tracker_text(extractor, ...):
    result = await extractor.extract(
        "...prose...", scene_with_text, "c_1", snapshot,
        mode=ExtractionMode.TOGETHER,
        together_tracker_text='{"facts":[],"character_updates":[{"character_id":"x","field":"mood","after":"sad","confidence":0.9}]}',
        turn_id="t_1",
    )
    assert any(d.target_id == "x" for d in result.deltas)


async def test_tool_use_mode_uses_tool_calls(...):
    ...
```

- [ ] **Step 2: Implement signature + mode dispatch**

```python
# backend/src/grimoire/extractor/service.py (extract method)
async def extract(
    self,
    response_text: str,
    scene: Scene,
    campaign_id: str,
    prior_state_snapshot: StateSnapshot,
    *,
    mode: ExtractionMode = ExtractionMode.SEPARATE,
    together_tracker_text: str | None = None,
    tool_calls: list[ToolCall] | None = None,
    turn_id: str,
    pre_roll_resolved: PreRollResolved | None = None,
) -> ExtractionResult:
    if mode == ExtractionMode.NONE:
        return ExtractionResult()      # empty

    if mode == ExtractionMode.SEPARATE:
        return await self._run_separate(response_text, scene, campaign_id, prior_state_snapshot, turn_id)

    if mode == ExtractionMode.TOGETHER:
        from grimoire.extractor.together import parse_tracker_text
        try:
            tracker = parse_tracker_text(together_tracker_text or "")
        except TrackerMalformedError as e:
            await self.auto_disable.record_call(self.provider_id, self.model, "together", success=False)
            # Fall back to SEPARATE on this call
            result = await self._run_separate(response_text, scene, campaign_id, prior_state_snapshot, turn_id)
            result.telemetry.fallback = ("together", "separate", str(e))
            return result
        await self.auto_disable.record_call(self.provider_id, self.model, "together", success=True)
        tracker_deltas = self._project_tracker_to_deltas(tracker)
        sanity = await self._run_sanity_layer(response_text, scene)  # rule + heuristic only, no LLM
        return self._merge(tracker_deltas, sanity)

    if mode == ExtractionMode.TOOL_USE:
        if not tool_calls:
            await self.auto_disable.record_call(self.provider_id, self.model, "tool_use", success=False)
            result = await self._run_separate(...)
            result.telemetry.fallback = ("tool_use", "separate", "no_tool_calls")
            return result
        await self.auto_disable.record_call(self.provider_id, self.model, "tool_use", success=True)
        tool_deltas = self._project_tool_calls_to_deltas(tool_calls)
        sanity = await self._run_sanity_layer(response_text, scene)
        return self._merge(tool_deltas, sanity)

    raise ValueError(f"unknown mode: {mode}")
```

- [ ] **Step 3: Tests PASS + commit + merge C.**

---

# Branch D — Together mode tracker

### Task D1: Tracker parser + projector

**Files:**
- Create: `backend/src/grimoire/extractor/together.py`.
- Test: `backend/tests/extractor/test_together.py`.

- [ ] **Step 1: Failing tests for parser**

```python
from grimoire.extractor.together import parse_tracker_text, TrackerMalformedError


def test_parses_valid_json():
    raw = '{"facts": [{"text": "winifred has a scar"}], "character_updates": []}'
    parsed = parse_tracker_text(raw)
    assert parsed.facts[0]["text"] == "winifred has a scar"


def test_raises_on_invalid_json():
    with pytest.raises(TrackerMalformedError):
        parse_tracker_text("{this is not JSON")


def test_raises_on_missing_required_key():
    raw = '{"facts": []}'  # missing character_updates
    with pytest.raises(TrackerMalformedError, match="character_updates"):
        parse_tracker_text(raw, required=["facts", "character_updates"])


def test_raises_on_unknown_delta_kind():
    raw = '{"facts":[], "character_updates":[], "unknown_kind":[1]}'
    parsed = parse_tracker_text(raw)
    # Unknown keys ignored (forward-compat)
    assert not parsed.unknown
```

- [ ] **Step 2: Implement**

```python
# backend/src/grimoire/extractor/together.py
import json
from dataclasses import dataclass


class TrackerMalformedError(Exception):
    pass


@dataclass
class ParsedTracker:
    facts: list[dict]
    character_updates: list[dict]
    location_updates: list[dict]
    faction_updates: list[dict]
    commitments_added: list[dict]
    commitments_resolved: list[dict]
    new_entities: list[dict]
    advance_time: dict | None
    change_location: dict | None


_REQUIRED = ("facts", "character_updates")


def parse_tracker_text(raw: str, *, required: tuple[str, ...] = _REQUIRED) -> ParsedTracker:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise TrackerMalformedError(f"json decode: {e}") from e
    if not isinstance(obj, dict):
        raise TrackerMalformedError("top-level is not an object")
    for k in required:
        if k not in obj:
            raise TrackerMalformedError(f"missing required key: {k}")
    return ParsedTracker(
        facts=obj.get("facts", []),
        character_updates=obj.get("character_updates", []),
        location_updates=obj.get("location_updates", []),
        faction_updates=obj.get("faction_updates", []),
        commitments_added=obj.get("commitments_added", []),
        commitments_resolved=obj.get("commitments_resolved", []),
        new_entities=obj.get("new_entities", []),
        advance_time=obj.get("advance_time"),
        change_location=obj.get("change_location"),
    )
```

`_project_tracker_to_deltas(parsed)` lives in `extractor/service.py` and maps each section to `StateDelta` objects with confidence 0.9 baseline (overridable per-delta if present).

- [ ] **Step 3: Frontend streaming parser** — state machine in `frontend/src/usePlayState.tsx:262–300` that strips `<!-- TRACKER -->...<!-- /TRACKER -->` from the streamed text. Sends the captured tracker JSON in the existing turn-finalize POST.

- [ ] **Step 4: Tests PASS + commit + merge D.**

---

# Branch E — Tool-use mode

### Task E1: Tool declarations + projector

**Files:**
- Create: `backend/src/grimoire/extractor/tool_use.py`.
- Test: `backend/tests/extractor/test_tool_use.py`.

- [ ] **Step 1: Tool schema declarations**

One tool per delta kind (subset):

```python
# backend/src/grimoire/extractor/tool_use.py
RECORD_FACT_TOOL = ToolDeclaration(
    name="record_fact",
    description="Record a new fact established by this turn.",
    schema={
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "confidence": {"type": "number"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text"],
    },
)
UPDATE_CHARACTER_STATE_TOOL = ToolDeclaration(...)
ADVANCE_TIME_TOOL = ToolDeclaration(...)
CHANGE_LOCATION_TOOL = ToolDeclaration(...)
PROPOSE_NEW_ENTITY_TOOL = ToolDeclaration(...)
CREATE_COMMITMENT_TOOL = ToolDeclaration(...)
UPDATE_COMMITMENT_TOOL = ToolDeclaration(...)
CLOSE_THREAD_TOOL = ToolDeclaration(...)


ALL_TOOLS = [
    RECORD_FACT_TOOL, UPDATE_CHARACTER_STATE_TOOL, ADVANCE_TIME_TOOL,
    CHANGE_LOCATION_TOOL, PROPOSE_NEW_ENTITY_TOOL, CREATE_COMMITMENT_TOOL,
    UPDATE_COMMITMENT_TOOL, CLOSE_THREAD_TOOL,
]


def project_tool_calls(calls: list[ToolCall]) -> list[StateDelta]:
    deltas = []
    for call in calls:
        if call.name == "record_fact":
            deltas.append(StateDelta(kind=DeltaKind.FACT_ADD, ..., after=call.args, ...))
        elif call.name == "update_character_state":
            deltas.append(StateDelta(kind=DeltaKind.CHARACTER_STATE_UPDATE, ..., target_id=call.args["character_id"], ...))
        ...
    return deltas
```

- [ ] **Step 2: Failing test**

```python
def test_record_fact_call_projects_to_fact_add_delta():
    deltas = project_tool_calls([ToolCall(name="record_fact", args={"text": "winifred has scar"})])
    assert deltas[0].kind == DeltaKind.FACT_ADD


def test_unknown_tool_call_logged_and_skipped():
    deltas = project_tool_calls([ToolCall(name="random_unknown", args={})])
    assert deltas == []
```

- [ ] **Step 3: Wire tools onto AssembledPrompt**

`AssembledPrompt` already has a `tools: list[ToolDeclaration]` field per existing gateway support; the Context Builder sets `prompt.tools = ALL_TOOLS` when `extractor_mode == TOOL_USE`. Tools default to empty list otherwise.

- [ ] **Step 4: Tests PASS + commit + merge E.**

---

# Branch F — Context Builder mode parameter

### Task F1: Extend `build()` signature

**Files:**
- Modify: `backend/src/grimoire/context/builder.py:build`.
- Test: `backend/tests/context/test_builder_modes.py`.

- [ ] **Step 1: Failing tests**

```python
async def test_together_appends_tracker_instructions(builder, seeded_state):
    prompt = await builder.build(
        player_input="...",
        campaign_id=seeded_state.campaign_id,
        extractor_mode=ExtractionMode.TOGETHER,
        branch_id=f"{seeded_state.campaign_id}:main",
        pc_ref=seeded_state.pc_ref, turn_id="t_1",
    )
    blob = "\n".join(m.content for m in prompt.messages)
    assert "<!-- TRACKER -->" in blob
    assert "<!-- /TRACKER -->" in blob
    # tracker after prose by default
    assert blob.index("<!-- TRACKER -->") > blob.index("[scene_summary")


async def test_tool_use_attaches_tools(builder, seeded_state):
    prompt = await builder.build(... extractor_mode=ExtractionMode.TOOL_USE ...)
    tool_names = {t.name for t in prompt.tools}
    assert "record_fact" in tool_names


async def test_none_omits_tracker_and_tools(builder, seeded_state):
    prompt = await builder.build(... extractor_mode=ExtractionMode.NONE ...)
    assert prompt.tools == []
    blob = "\n".join(m.content for m in prompt.messages)
    assert "<!-- TRACKER -->" not in blob


async def test_separate_is_unchanged(builder, seeded_state):
    # SEPARATE matches current behavior; smoke test
    prompt = await builder.build(... extractor_mode=ExtractionMode.SEPARATE ...)
    assert prompt.tools == []
```

- [ ] **Step 2: Implement signature + branching**

```python
# backend/src/grimoire/context/builder.py
async def build(
    self,
    player_input: str,
    campaign_id: CampaignId,
    *,
    mechanics_results: list[MechanicsResult] | None = None,
    extra: dict | None = None,
    extractor_mode: ExtractionMode = ExtractionMode.SEPARATE,
    auxiliary_task: AuxiliaryTask | None = None,
    branch_id: str,
    pc_ref: str,
    turn_id: str,
) -> AssembledPrompt:
    # existing assembly produces `prompt`
    prompt = await self._assemble(...)

    # Mode-specific tail
    if extractor_mode == ExtractionMode.TOGETHER and auxiliary_task is None:
        prompt = self._append_tracker_instructions(prompt)
    elif extractor_mode == ExtractionMode.TOOL_USE and auxiliary_task is None:
        from grimoire.extractor.tool_use import ALL_TOOLS
        prompt = replace(prompt, tools=list(ALL_TOOLS))
    # NONE / SEPARATE: no change to prompt

    return prompt


def _append_tracker_instructions(self, prompt: AssembledPrompt) -> AssembledPrompt:
    schema_json = self._tracker_schema_json()
    instruction = (
        "After your prose, emit a JSON tracker block delimited by "
        "<!-- TRACKER --> and <!-- /TRACKER --> with the following shape: \n"
        f"{schema_json}\n"
        "Position the tracker after the prose."
    )
    return prompt.with_appended_system_message(instruction)
```

`_tracker_schema_json` returns the JSON schema corresponding to the `ParsedTracker` shape (statically generated; lives in `extractor/together.py`).

- [ ] **Step 3: Tests PASS + commit + merge F.**

---

# Integration check

- [ ] **Step end1: Full suite + frontend tests pass**

```powershell
pytest backend/tests -v
npm test --prefix frontend
```

- [ ] **Step end2: Orchestrator wires `select_mode`**

```python
# in orchestrator/service.py _continue_turn_after_pre_roll
mode = select_mode(
    campaign_config=self.config.extractor,
    provider_caps=self.llm_gateway.capabilities_for(route.provider_id),
    auto_disable=self.auto_disable,
    aux_task=None,
    provider_id=route.provider_id,
    model=route.model,
)

prompt = await self.context_builder.build(..., extractor_mode=mode, ...)
# ... stream LLM ...
result = await self.extractor.extract(..., mode=mode,
                                       together_tracker_text=tracker_text,
                                       tool_calls=tool_calls)
```

- [ ] **Step end3: COMPLETED doc + delete design.**
