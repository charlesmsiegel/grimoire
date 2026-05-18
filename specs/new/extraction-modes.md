# Extraction Modes

Today the Extractor runs three parallel strategies (rule-based, heuristic,
LLM) and merges them — effectively a single "Separate"-style mode. This
spec opens the decision up to three configurable strategies with selection
logic, fallback chain, and per-mode prompt assembly. The `ExtractionResult`
output schema is unchanged regardless of mode.

## Modes

### Separate (default)

Secondary LLM call after the main response.

- Pros: prose stays clean, uses a cheap fast model, parser failures don't
  affect what the user reads, runs in parallel with user reading.
- Cons: two API calls per turn, extra latency before deltas are visible.
- Pick when: a fast cheap secondary model is available, main model is
  large, robustness > latency.

### Together

Main model emits a structured block alongside prose, delimited by
`<!-- TRACKER --> { … } <!-- /TRACKER -->`. The Frontend streams prose,
buffers the tracker, strips it before render. Configurable
`tracker_position: after_prose | before_prose`.

- Pros: one API call, no extra latency, consistent model.
- Cons: prose pollution risk, larger main-model output cost, occasional
  missing closing marker.
- Pick when: single-model setup, latency matters more than small per-turn
  saving, main model handles structured output reliably.

### Tool-use

Provider-native tool / function calls. One tool per delta kind:
`record_fact`, `update_character_state`, `advance_time`, `change_location`,
`propose_new_entity`, `create_commitment`, … mirroring the
`ExtractionResult` sections.

- Pros: strongly typed, no parser fragility, no prose pollution, deltas
  can arrive mid-stream (HUD reacts in near-real-time).
- Cons: not all providers support it well; tool-call costs vary.
- Pick when: provider supports streaming tool-use, robustness is top
  priority.

## Selection logic

```python
def select_mode(campaign_config, provider_caps, auto_disable, aux_task):
    if aux_task is not None:
        return ExtractionMode.NONE
    preferred = campaign_config.extractor.mode
    if preferred == TOOL_USE and not provider_caps.supports_tool_use:
        preferred = SEPARATE
    if preferred == TOGETHER and auto_disable.together_disabled(provider):
        preferred = SEPARATE
    if preferred == TOOL_USE and auto_disable.tool_use_disabled(provider):
        preferred = SEPARATE
    return preferred
```

`mode: auto` picks the best mode the provider supports (Tool-use if
available and not disabled, otherwise Together if allowed, otherwise
Separate).

## Fallback chain

- Together malformed (missing close marker, invalid JSON, missing
  required fields) → fall back to Separate for this turn; log; bump
  failure counter.
- Tool-use produces no tool calls → fall back to Separate.
- Auto-disable threshold reached (default 15% Together, 10% Tool-use) →
  disable that mode for the (provider, model) pair, surface a one-line
  warning. User can re-enable.

## Context Builder integration

Signature gains an explicit mode hint:

```python
async def build(
    self,
    player_input: str,
    campaign_id: str,
    mechanics_results: list[MechanicsResult] = [],
    extractor_mode: ExtractionMode = ExtractionMode.SEPARATE,
    auxiliary_task: Optional[AuxiliaryTask] = None,
) -> AssembledPrompt: ...
```

- Together → tracker-block instructions appended to the system prompt
  with the schema inline.
- Tool-use → tool declarations attached to the assembled prompt.
- Separate → no main-prompt change.

## Extractor surface

```python
class Extractor(Protocol):
    async def extract(
        self,
        response_text: str,
        scene: Scene,
        campaign_id: str,
        prior_state_snapshot: StateSnapshot,
        mode: ExtractionMode,
        together_tracker_text: Optional[str] = None,
        tool_calls: Optional[list[ToolCall]] = None,
    ) -> ExtractionResult: ...
```

Together mode's tracker text is separated by the Frontend's streaming
parser before this call.

## Configuration

```yaml
extractor:
  mode: separate                  # separate | together | tool_use | auto
  fallback_chain: [separate]

  separate:
    model: claude-haiku-4-5
    timeout_seconds: 30

  together:
    delimiter_open: "<!-- TRACKER -->"
    delimiter_close: "<!-- /TRACKER -->"
    tracker_position: after_prose
    strip_from_user_view: true
    fallback_on_malformed: true
    auto_disable_threshold: 0.15

  tool_use:
    tools: [record_fact, update_character_state, advance_time, ...]
    auto_disable_threshold: 0.10
    fallback_on_no_tool_call: true

  auto_apply_threshold: 0.85
  review_threshold: 0.60
  max_new_entities_per_turn: 5
```

## Observability

Per turn: chosen mode, fallback (from → to), parse success, latency,
token usage (Together inflates main-model output; Separate adds secondary
tokens; Tool-use adds tool-call overhead).

Aggregate metrics per (provider, model, mode): malformed rate, mean
latency, cost per turn. Surfaced in a per-campaign "extraction health"
panel so the user can decide whether to switch modes.

## Interactions

- `auxiliary-tasks.md`: extraction skipped for auxiliary tasks regardless
  of mode; Context Builder omits Together's instructions and Tool-use's
  tool declarations.
- `05-llm-gateway.md`: provider capability detection (supports tool-use?
  streaming tool-use? max-tool-count?). LLM Gateway exposes; Extractor
  consumes.
- `10-scene-manager.md`: Together's tracker text is stripped before the
  post is appended; scene files never contain tracker JSON.
- `16-observability.md`: mode, fallbacks, parse failures, token usage all
  logged.
