## Extraction Modes — Design

> **Status:** Design ready for implementation plan. Foundation feature: `auxiliary-tasks` needs `ExtractionMode.NONE` short-circuit and provider-capability awareness.

**Source idea:** `specs/new/extraction-modes.md`
**Module:** `backend/src/grimoire/extractor/`, `backend/src/grimoire/context/`, `backend/src/grimoire/llm_gateway/`

## Purpose

Today the Extractor runs three parallel strategies (rule-based, structured-LLM, heuristic) and merges them (`backend/src/grimoire/extractor/service.py:123–261`). That's effectively a single "Separate"-style mode. This spec replaces the implicit single mode with three configurable strategies plus an `auto` selector with a fallback chain. The `ExtractionResult` output schema is unchanged regardless of mode.

## Modes

### `SEPARATE` (default)

The current behavior, formalized: a secondary LLM call after the main response. Cheap fast model (Haiku), parses prose post-stream, parser failures don't pollute what the user sees. Two calls per turn, secondary latency.

### `TOGETHER`

Main model emits a structured block alongside prose delimited by `<!-- TRACKER -->` / `<!-- /TRACKER -->`. The frontend streams prose, buffers the tracker, strips it before render. Configurable `tracker_position: after_prose | before_prose`. One API call; main-model output larger; risk of malformed JSON.

### `TOOL_USE`

Provider-native tool / function calls. One tool per delta kind (`record_fact`, `update_character_state`, `advance_time`, `change_location`, `propose_new_entity`, `create_commitment`, …) mirroring `ExtractionResult` sections. Deltas arrive mid-stream → HUD reacts in near-real-time. Provider support varies.

### `NONE`

Synthesized mode for auxiliary tasks. Extractor is not invoked; Context Builder suppresses tracker instructions and tool declarations. Hard short-circuit so the auxiliary-tasks spec doesn't need to leak its semantics into multiple call sites.

## Per Theme F decision: tool-use capability is hard-coded

`backend/src/grimoire/llm_gateway/` does not currently expose `supports_tool_use` — spec 05 §16 deferred it. This spec adds a static capability table rather than blocking on the gateway upgrade:

```python
# grimoire/llm_gateway/capabilities.py (new)
PROVIDER_CAPABILITIES = {
    "anthropic": ProviderCapabilities(supports_tool_use=True, streaming_tool_use=True, max_tool_count=128),
    "openai":    ProviderCapabilities(supports_tool_use=True, streaming_tool_use=True, max_tool_count=128),
    "google":    ProviderCapabilities(supports_tool_use=True, streaming_tool_use=False, max_tool_count=64),
    "openrouter": ProviderCapabilities(supports_tool_use=False),    # depends on routed provider; safe default
    "local-llamacpp": ProviderCapabilities(supports_tool_use=False),
}
```

Dynamic detection (probing the provider on first connection) is a follow-up; the static table is sufficient because Grimoire pins providers per route and the matrix is small. A `LLMGatewayService.capabilities_for(provider_id) -> ProviderCapabilities` accessor is added.

## Selection logic

```python
def select_mode(
    campaign_config: ExtractorConfig,
    provider_caps: ProviderCapabilities,
    auto_disable: AutoDisableState,
    aux_task: AuxiliaryTask | None,
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

**Where the selector lives:** Orchestrator owns it. Selection happens once at turn start (`orchestrator/service.py` near the existing `_continue_turn_after_pre_roll`); the chosen mode is passed to both Context Builder and Extractor via the existing call sites. Justification: the Orchestrator already owns provider routing and the auxiliary-task hook, so this is the natural home; centralizing the call also keeps observability tidy (one log entry per turn).

## Auto-disable state

Per-(provider, model, mode) failure-rate tracking. **Persists in SQLite** (per the open question — the in-memory option loses calibration on restart, which defeats the feedback loop):

```sql
CREATE TABLE extractor_mode_health (
    provider_id  TEXT NOT NULL,
    model        TEXT NOT NULL,
    mode         TEXT NOT NULL,
    window_start TEXT NOT NULL,           -- ISO timestamp; rolling 24h window
    total_calls  INTEGER NOT NULL DEFAULT 0,
    failures     INTEGER NOT NULL DEFAULT 0,
    disabled_at  TEXT,                     -- nullable; set when threshold crossed
    re_enabled_at TEXT,                    -- nullable; cleared on user re-enable
    PRIMARY KEY (provider_id, model, mode)
);
```

A row is "disabled" when `failures / total_calls >= threshold` (default 0.15 for Together, 0.10 for Tool-use) **and** `total_calls >= 20` (minimum sample size to avoid early-disable on tiny windows). The `AutoDisableState` service reads this on demand. User re-enable writes `re_enabled_at = now()`, resets counters; failures accumulate again from zero.

A daily roll-window task (lives with the existing observability cron) resets `window_start` and counters at the configured cadence (default: 24h sliding window).

## Context Builder integration

`backend/src/grimoire/context/builder.py:125–145` gains a parameter:

```python
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
) -> AssembledPrompt: ...
```

Branching at the end of assembly:
- `SEPARATE`: no change.
- `TOGETHER`: append tracker-instruction message to the system prompt; embed the JSON schema (lifted from `ExtractionResult` shape) inline. Position controlled by `extractor.together.tracker_position`.
- `TOOL_USE`: attach `tools: list[ToolDeclaration]` to `AssembledPrompt`; each tool maps to one delta kind. The LLM Gateway already accepts a `tools` arg per provider (currently always empty); this spec's wire-up lights it up.
- `NONE` (auxiliary): neither tracker instructions nor tool declarations; mechanics tools also omitted.

The auxiliary-task suppression matrix lives in `auxiliary-tasks-design.md`; the only piece this spec owns is the **mode-driven** tracker/tool suppression.

## Extractor surface

```python
class Extractor(Protocol):
    async def extract(
        self,
        response_text: str,
        scene: Scene,
        campaign_id: str,
        prior_state_snapshot: StateSnapshot,
        *,
        mode: ExtractionMode,
        together_tracker_text: Optional[str] = None,
        tool_calls: Optional[list[ToolCall]] = None,
        turn_id: str,
        pre_roll_resolved: PreRollResolved | None = None,
    ) -> ExtractionResult: ...
```

Internal routing in `extractor/service.py`:

- `SEPARATE` (existing behavior): run the three parallel strategies + merge. No tracker / tool data needed.
- `TOGETHER`: parse `together_tracker_text` as JSON against the schema, project into deltas. **No secondary LLM call.** The rule-based + heuristic strategies still run as a sanity layer (cheap, deterministic); their outputs merge with the tracker's via the existing `merge.py:25–60` precedence (highest confidence wins; tracker source treated as `confidence=0.9` baseline, overridable per-delta in the JSON).
- `TOOL_USE`: project `tool_calls` directly into deltas; same sanity-layer merge as Together. The LLM Gateway accumulates `tool_calls` during streaming and hands the complete list to the extractor at finish.
- `NONE`: `extract()` is not called; the Orchestrator skips invocation entirely.

## "Malformed" definition (Together)

A Together response is malformed iff any of:

1. Missing close marker (no `<!-- /TRACKER -->` before stream end).
2. Tracker JSON fails to parse (`json.loads` raises).
3. JSON object missing a required top-level key (`facts`, `character_updates`, etc. — the schema declares which are required, default `[]`).
4. Any delta has an unknown `kind` field (open enum is rejected; closed list defined alongside `DeltaKind`).

Items 3–4 use the existing pydantic validators on `ExtractionResult` types. Item 1 is caught by the frontend streaming parser before the request reaches the extractor.

`tracker_position` semantics: per the open question, position is **per-complete-response**, not per chunk. The frontend buffer accumulates the entire main-model output; when stream completes, it splits on the markers. `before_prose` means the JSON arrives in the first part of the response (model is instructed to emit it first), but the frontend doesn't render prose until the marker boundary is seen. The trade-off: `before_prose` gives the HUD earlier data but delays prose render; `after_prose` is the inverse. Default: `after_prose`.

## Tool-use fallback

"Tool-use produces no tool calls" means: the model's `finish_reason` is `stop` or `length` and `tool_calls` is empty after stream completes. In that case:

- Trigger only at finish (mid-stream "no tools yet" is fine).
- The main response **text** is preserved (it might be valid prose); fall back to `SEPARATE` on the text. No re-invoke of the model.
- Count as a tool-use failure for `extractor_mode_health` accounting.

## Streaming parser (frontend)

`frontend/src/usePlayState.tsx:262–300` handles WS `token` events. The Together support adds a small state machine:

```
state = PROSE | IN_TRACKER
buffer.append(token)
on each token boundary:
  if state == PROSE and buffer contains "<!-- TRACKER -->":
      flush prose up to marker; state = IN_TRACKER
  if state == IN_TRACKER and buffer contains "<!-- /TRACKER -->":
      capture tracker_text up to closing marker; state = PROSE
on stream end:
  send POST /campaigns/{id}/turns/{turn_id}/finalize-extraction
       with body { tracker_text } if any.
```

The tracker text is forwarded back to the backend via the existing finalize-turn path. The Orchestrator pulls it from the finalize payload and threads it into `extract(..., together_tracker_text=...)`. No new WebSocket event is required — the existing finalize handshake carries the payload.

## Configuration

```yaml
extractor:
  mode: separate                  # separate | together | tool_use | auto | none(internal only)
  fallback_chain: [separate]      # always lands on separate after a fallback

  separate:
    model: claude-haiku-4-5
    timeout_seconds: 30

  together:
    delimiter_open:  "<!-- TRACKER -->"
    delimiter_close: "<!-- /TRACKER -->"
    tracker_position: after_prose
    strip_from_user_view: true
    fallback_on_malformed: true
    auto_disable_threshold: 0.15
    auto_disable_min_samples: 20

  tool_use:
    tools: [record_fact, update_character_state, advance_time, change_location,
            propose_new_entity, create_commitment, update_commitment,
            close_thread, ...]
    auto_disable_threshold: 0.10
    auto_disable_min_samples: 20
    fallback_on_no_tool_call: true

  auto_apply_threshold: 0.85
  review_threshold: 0.60
  max_new_entities_per_turn: 5
```

## Observability

Per turn: chosen mode, fallback (from → to), parse success/failure reason, latency, token usage. Logged into the existing `turn_audits` blob under `extraction.mode_telemetry`.

Aggregate metrics from `extractor_mode_health`: surfaced in a per-campaign "extraction health" panel (REST: `GET /campaigns/{id}/diagnostics/extraction-health`). The frontend renders it in the existing Worlds debug view.

Audit example:
```
[extract] turn=t_4710 mode=together parse_ok=true tracker_tokens=312 latency_ms=820
[extract] turn=t_4711 mode=together→separate fallback=malformed reason=missing_close_marker
```

## Cross-spec hooks

- **`auxiliary-tasks`** — `ExtractionMode.NONE` short-circuit is the contract; auxiliary tasks always pass an `AuxiliaryTask` and Mode selector returns `NONE`.
- **Scene Manager** — never sees tracker JSON; the frontend strips before the post body is committed.
- **Observability** — mode + fallback events feed the same audit blob.

## Failure handling

| Failure | Behavior |
|---|---|
| Together malformed | Fall back to SEPARATE for this turn; bump failure counter; log |
| Tool-use produces no calls | Fall back to SEPARATE on the prose; bump failure counter |
| Auto-disable threshold crossed | Mode disabled for (provider, model); user notified via one-line banner; can re-enable from diagnostics panel |
| Provider returns 4xx during tool-use | Disable mode immediately (don't accumulate failures — bad config); log loud |
| Tracker text returned but auxiliary task active | Discard tracker; never affects state |

## Test wiring

`backend/tests/extractor/test_modes.py` (new):
- `select_mode` truth table across (preferred, caps, auto-disable, aux) cases.
- Together malformed scenarios → fallback.
- Tool-use no-tool-calls → fallback.
- Mode-health threshold + min-samples behavior; re-enable path.
- Auxiliary task → NONE (no extractor call at all).

`backend/tests/context/test_builder_modes.py`:
- TOGETHER appends tracker instructions to system prompt.
- TOOL_USE attaches tools list.
- NONE includes neither.
- Mode is independent of all existing tier assembly.

`backend/tests/llm_gateway/test_capabilities.py`:
- `capabilities_for(provider_id)` returns the static table entry.
- Unknown provider → `ProviderCapabilities(supports_tool_use=False)` (safe default).

## Wiring touchpoints

- `extractor/service.py`: `extract()` signature extended; mode-branching in `_run()`.
- `extractor/together.py` (new): JSON parser + sanity merger.
- `extractor/tool_use.py` (new): tool-call projector.
- `extractor/auto_disable.py` (new): `AutoDisableState` reading from `extractor_mode_health`.
- `context/builder.py`: signature + mode-branching at end of assembly.
- `llm_gateway/capabilities.py` (new): static provider table.
- `orchestrator/service.py`: `select_mode` call site; thread `extractor_mode` into Context Builder + Extractor.
- `api/campaigns.py`: `finalize-extraction` payload accepts `tracker_text`; `GET diagnostics/extraction-health`.
- Migration adds `extractor_mode_health` table.
- `frontend/src/usePlayState.tsx`: streaming tracker buffer state machine.
