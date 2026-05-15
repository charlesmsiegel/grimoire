# 22 — Extraction Modes

## Purpose

`04-extractor.md` describes *what* the Extractor does: parse a model response into structured `StateDelta` proposals. It assumes one strategy: a secondary LLM call after the main response (call it **Separate** mode). This spec opens that decision up.

Three viable extraction strategies exist, each with different cost / latency / quality / robustness tradeoffs:

| Mode | How structured data is produced | Calls per turn | Prose quality | Robustness |
|---|---|---|---|---|
| **Separate** (default) | Secondary LLM call after the main response | 2 | High (prose is clean) | High |
| **Together** | Main response contains a delimited structured block alongside prose | 1 | Medium (some pollution risk) | Medium |
| **Tool-use** | Main response uses provider tool/function calls to emit deltas | 1 (with tool-call branches) | High | High when provider supports it |

This spec defines each mode, the selection logic, the fallback chain, the prompt and parser shapes, and the configuration surface. It supersedes the implicit single-mode assumption in `04-extractor.md`'s "Extraction strategies" section by lifting that section to a configurable strategy.

The SillyTavern RPG-companion plugin's "Together" and "Separate" modes inspired this spec; Grimoire adds Tool-use as a first-class third option for providers that support it.

## Why all three exist

- **Separate** is the safest default: prose stays pure, extraction uses a cheap fast model, malformed extraction never affects what the user reads.
- **Together** is the cheapest for the high-end model case: when the main model is expensive and the only one available (e.g., a single local-llama-cpp-python instance running a 70B model), running the same model twice doubles cost / latency.
- **Tool-use** is the highest-fidelity when supported (Anthropic, OpenAI, Google all support some form of structured tool output). Eliminates parser fragility and gives the model explicit affordances.

A real deployment will mix and match — Together for solo local-model play, Tool-use for Anthropic, Separate as the universal fallback.

## Responsibilities (delta against `04-extractor.md`)

- Select an extraction mode per campaign based on provider capabilities and user config
- Adjust the prompt assembled by Context Builder per mode (e.g., add tracker block instructions for Together)
- Parse responses per mode and produce the same `ExtractionResult` shape regardless
- Fall back gracefully when a mode fails (malformed output, tool-use not supported, etc.)
- Track per-mode quality metrics for observability and auto-tuning

## Non-responsibilities

- Does not change the `ExtractionResult` schema (modes are alternative input shapes; output is identical)
- Does not own provider capability detection (LLM Gateway does)
- Does not retry the main turn (auxiliary failures fall back per mode policy)

## Mode 1: Separate (default)

The current `04-extractor.md` design. Recap:

1. Main turn: Context Builder assembles → LLM Gateway calls main model → response streams to UI
2. Extractor task: after main response completes (or in parallel as it streams), Extractor builds a secondary prompt with:
   - The new response text
   - A compact state snapshot
   - A schema requesting structured deltas
3. Extractor calls a (typically cheaper) model via LLM Gateway
4. Parse the JSON output, validate, produce `ExtractionResult`

### Prompt shape (Separate)

```
You are an extraction model. Read the following narrative excerpt and return
a JSON object describing state changes implied by the prose. Use only the
fields in the schema; ignore irrelevant prose.

Schema: <inline schema>

Recent state snapshot:
<compact snapshot>

Narrative excerpt:
<the model's just-generated response>

Return only the JSON object. Do not include prose.
```

### Pros and cons

- **Pros**: prose is uncontaminated; uses a cheap fast extraction model; failures don't affect the user's reading experience; can be parallelized with the user reading the response
- **Cons**: two API calls per turn; slightly more end-to-end latency before deltas are visible; extra cost

### When to pick Separate

- Provider has fast cheap secondary model available (e.g., Anthropic with Haiku)
- Main model is large and expensive (don't pay it twice; let cheap model do the second pass)
- Robustness matters more than latency

## Mode 2: Together

The main model response contains both prose and a structured block. The Extractor parses the block, the Frontend renders only the prose.

### Prompt shape (Together)

The Context Builder adds a strict instruction near the end of the system block:

```
At the end of your response, AFTER the prose narrative, emit a structured
tracker block delimited by:

  <!-- TRACKER -->
  { ...JSON conforming to the schema below... }
  <!-- /TRACKER -->

The tracker block is for the system, not the reader. Do not reference it
in prose. Do not omit it. Output the block exactly once, at the end.

Schema: <inline schema>

Snapshot of state to consider when filling the tracker:
<compact snapshot>

(Then proceed with the normal turn instructions and assembled context.)
```

### Parser (Together)

1. After the response completes, locate the `<!-- TRACKER -->` and `<!-- /TRACKER -->` markers
2. Extract the JSON between them
3. Parse and validate against the schema
4. Strip the tracker block from the rendered prose (the Frontend never sees the tracker; the scene file never stores it)
5. Produce `ExtractionResult` identical to Separate's output

### Streaming

The response streams to the user as it arrives. The Frontend uses a streaming parser that:
- Buffers the tail of the stream looking for the opening `<!-- TRACKER -->`
- Once the opening marker is seen, stops rendering further tokens to the user
- Stores the tracker content (and trailing closing marker) as it arrives
- On stream end, runs the parser

If the tracker block is malformed (no closing marker, invalid JSON, missing required fields), fallback (see below).

### Pros and cons

- **Pros**: single API call; no extra latency for extraction; consistent model (same model interprets the scene and emits structured data, less drift between the two)
- **Cons**: prose pollution risk if the model leaks tracker syntax into prose; larger output token cost on the expensive main model; harder to debug parser failures; the model occasionally forgets the closing marker

### When to pick Together

- Single-model setup (only one model available, e.g., local llama-cpp-python with one loaded model)
- Latency is more important than the small per-turn cost saving
- The main model handles structured output reliably (e.g., good local 70B+ models; mediocre on smaller models)
- The user is comfortable with the rare prose pollution incident (debugged via observability)

### Tracker block placement variants

By default, tracker comes **after** prose. Two reasons to prefer this:
- The user sees prose first; the tracker is processed during their reading time
- The model writes prose first (its natural mode) before being asked for structure

Alternative: tracker **before** prose. Some models are more reliable when asked for structure first. Configurable per provider:

```yaml
extractor:
  together:
    tracker_position: after_prose      # after_prose | before_prose
```

### Together-block fallback

If the tracker block is missing or malformed:

1. Log the failure with the raw response (Observability)
2. Fall back to **Separate** mode for this turn: run a secondary extraction call against the prose
3. Track the failure rate for this (provider, model) pair
4. If failure rate exceeds threshold (configurable, default 15%), automatically disable Together for that pair and surface a warning to the user

## Mode 3: Tool-use

The main model is given access to provider-native tool / function calls that emit deltas. Instead of inlining JSON in prose, the model invokes tools mid-stream.

### Tool shape

A small set of tools, named to mirror the `ExtractionResult` schema sections:

```python
tools = [
    {
        "name": "record_fact",
        "description": "Record a new fact about the world or a character.",
        "parameters": {...same schema as ExtractionResult.facts entry...}
    },
    {
        "name": "update_character_state",
        "description": "Update a character's transient or sheet state.",
        "parameters": {...}
    },
    {
        "name": "advance_time",
        "description": "Advance in-game time.",
        "parameters": {...}
    },
    {
        "name": "change_location",
        "description": "Update the current scene location.",
        "parameters": {...}
    },
    {
        "name": "propose_new_entity",
        "description": "Propose a campaign-local new character, location, item.",
        "parameters": {...}
    },
    {
        "name": "create_commitment",
        "description": "Note a commitment or foreshadowing.",
        "parameters": {...}
    },
    # ... mirroring the ExtractionResult sections
]
```

The full tool set covers the same surface as `04-extractor.md`'s output schema.

### Turn flow (Tool-use)

1. Context Builder assembles the prompt with tool declarations attached
2. LLM Gateway calls the main model in tool-use mode
3. The model streams prose AND emits tool calls interleaved
4. The Frontend renders prose; the Extractor collects tool calls
5. After stream completes, the Extractor assembles tool calls into an `ExtractionResult` and produces deltas
6. No secondary call needed

### Pros and cons

- **Pros**: strongly typed; no parser fragility; provider-validated; one API call; no prose pollution; the model explicitly reasons about state changes (often improves narrative coherence as a side effect)
- **Cons**: not all providers support tool-use; tool-call latency varies per provider; some providers (or models) handle tool calls awkwardly mid-stream; tool-call costs vary per provider

### When to pick Tool-use

- Provider natively supports streaming tool calls (Anthropic, OpenAI as of recent versions)
- Robustness is highest priority
- The user wants the lowest possible failure rate on extraction

### Tool-use fallback

If tool-use produces no tool calls (model decided not to use them), fall back to:
- **Separate** mode for this turn (run a secondary extraction call)

This is rare for well-tuned tool descriptions but can happen with verbose-narrative prompts.

## Selection logic

The Extractor picks a mode at the start of each turn based on:

1. **Per-campaign config** (user-set preference)
2. **Provider capability** (queried via LLM Gateway)
3. **Auto-disable state** (mode disabled for this provider due to recent failures)
4. **Auxiliary task suppression** (`23-impersonation-mode.md`: auxiliary tasks skip extraction entirely)

Pseudo:

```python
def select_mode(campaign_config, provider_caps, auto_disable_state, auxiliary_task):
    if auxiliary_task is not None:
        return ExtractionMode.NONE
    preferred = campaign_config.extractor.mode
    if preferred == ExtractionMode.TOOL_USE and not provider_caps.supports_tool_use:
        preferred = ExtractionMode.SEPARATE
    if preferred == ExtractionMode.TOGETHER and auto_disable_state.together_disabled(provider_caps.provider_id):
        preferred = ExtractionMode.SEPARATE
    if preferred == ExtractionMode.TOOL_USE and auto_disable_state.tool_use_disabled(provider_caps.provider_id):
        preferred = ExtractionMode.SEPARATE
    return preferred
```

The Extractor logs the chosen mode per turn (Observability) so the user can audit.

## Configuration

```yaml
# Per-campaign extractor settings
extractor:
  mode: separate                  # separate | together | tool_use | auto
  fallback_chain: [separate]      # if chosen mode fails, try these in order

  separate:
    model: claude-haiku-4-5       # cheap fast model
    timeout_seconds: 30

  together:
    delimiter_open: "<!-- TRACKER -->"
    delimiter_close: "<!-- /TRACKER -->"
    tracker_position: after_prose # after_prose | before_prose
    strip_from_user_view: true
    fallback_on_malformed: true
    auto_disable_threshold: 0.15  # 15% malformed rate → disable

  tool_use:
    tools: [record_fact, update_character_state, advance_time, ...]
    auto_disable_threshold: 0.10
    fallback_on_no_tool_call: true

  # Common
  auto_apply_threshold: 0.85
  review_threshold: 0.60
  max_new_entities_per_turn: 5
```

### `mode: auto`

A fourth value: `auto` picks the best mode for the provider:
- Tool-use if supported and not auto-disabled
- Otherwise Together if the campaign config explicitly allowed it
- Otherwise Separate

`auto` is the recommended starting point for new campaigns; users opt into specific modes when they have a reason.

## Observability

Every turn logs:
- Selected mode
- Whether a fallback was triggered, and from what to what
- Parse success / failure
- Per-mode latency
- Token usage per mode (Together inflates main-model tokens; Separate adds secondary-model tokens)

Aggregate metrics (`16-observability.md`):
- Per-(provider, model, mode) malformed rate
- Per-mode mean latency
- Per-mode cost per turn (USD where applicable)

The Frontend's per-campaign settings view shows a small "extraction health" panel summarizing these so the user can decide whether to switch modes.

## Backend contract

The Extractor exposes:

```python
class Extractor(Protocol):
    async def extract(
        self,
        response_text: str,
        scene: Scene,
        campaign_id: str,
        prior_state_snapshot: StateSnapshot,
        # New: mode-specific inputs
        mode: ExtractionMode,
        together_tracker_text: Optional[str] = None,     # for Together (already separated by Frontend stream parser)
        tool_calls: Optional[list[ToolCall]] = None,     # for Tool-use
    ) -> ExtractionResult: ...
```

The Context Builder accepts a mode hint:

```python
class ContextBuilder(Protocol):
    async def build(
        self,
        player_input: str,
        campaign_id: str,
        mechanics_results: list[MechanicsResult] = [],
        extractor_mode: ExtractionMode = ExtractionMode.SEPARATE,
        auxiliary_task: Optional[AuxiliaryTask] = None,
    ) -> AssembledPrompt: ...
```

Together mode adds instructions to the assembled prompt. Tool-use mode attaches tool declarations to `AssembledPrompt`. Separate mode adds nothing to the main prompt.

## Interaction with other modules

- **`23-impersonation-mode.md`** (Auxiliary tasks): all extraction is suppressed for auxiliary tasks regardless of mode. The Context Builder omits Together's tracker instructions and Tool-use's tool declarations.
- **`05-llm-gateway.md`**: capability detection per provider (supports tool-use? streaming tool-use? max-tool-count? per-tool max-params?). LLM Gateway exposes this; Extractor consumes it.
- **`19-scene-hud.md`** and **`20-transient-state.md`**: extracted transient updates flow through the same path regardless of mode; the HUD doesn't know which mode produced the data.
- **`10-scene-manager.md`**: Together mode's tracker text is stripped before the post is appended to the scene file; the scene file never contains tracker JSON.
- **`16-observability.md`**: mode selection, fallbacks, parse failures, token usage are logged.

## Performance implications

Approximate (orders of magnitude, not benchmarks):

| Mode | Main call cost | Secondary call cost | Time-to-first-delta | Time-to-prose-complete |
|---|---|---|---|---|
| Separate | base | + secondary (Haiku-tier) | after secondary call (~1-3s post-prose) | base |
| Together | base + ~5-10% output tokens | 0 | end of main stream | end of main stream + tracker parse |
| Tool-use | base + tool-call overhead | 0 | during main stream (as tool calls arrive) | end of main stream |

Tool-use can produce deltas *before* the main response finishes streaming — useful for the HUD to react in near-real-time.

## Failure modes

| Failure | Behavior |
|---|---|
| Together: missing closing marker | Fallback to Separate; log; bump failure counter |
| Together: invalid JSON in block | Fallback to Separate; log |
| Together: model leaks tracker syntax into prose | Strip aggressively (regex-based); if user-visible characters slipped through, log and surface in review queue ("response had partial tracker syntax") |
| Tool-use: provider returns malformed tool call | Log; treat as if absent; if it was the only tool call, fall back to Separate |
| Tool-use: provider doesn't support it (despite capability flag) | Caught at LLM Gateway; downgrade to Separate; warn user |
| Separate: extraction timeout | Apply rule-based extraction only; queue full extraction for retry |
| All modes: extraction model unavailable | Apply rule-based only; surface a one-time warning |
| Together: model emits multiple tracker blocks | Use the last complete one; log the anomaly |

## Open questions

- **Streaming partial extraction in Separate mode**. If the main response streams, can the secondary call also stream — and apply deltas incrementally as they're parsed? Probably yes for high-confidence deltas; would need careful ordering with the Extractor's existing thresholding.
- **Per-turn auto-mode**. The selection is per-campaign now. Could be per-turn: "this turn looks short and mechanical → skip extraction" or "this turn's prompt was injected impersonation → no extraction." The auxiliary suppression handles the impersonation case; the short-turn heuristic is a possible v2 polish.
- **Tool-use tool granularity**. The tool set above is one tool per delta kind. Alternative: one mega-tool `emit_deltas(deltas: list)`. The mega-tool is simpler to declare; the per-kind tools are easier for the model to reason about. Empirically the per-kind tools win for current generation models.
- **Together-mode JSON streaming validator**. Validate the JSON as it streams rather than after stream end; abort the stream if it's clearly broken. Saves tokens. v2 polish.
- **Mode hot-swapping mid-campaign**. Switching mode resets the auto-disable counter. Should it? Probably keep counters per-(provider, mode) and survive campaign mode toggles.
- **Cost-aware auto mode**. Pick the cheapest mode that meets a quality threshold. Requires per-campaign quality and cost telemetry. v2.
- **Tool-use with cached prompts**. Anthropic's prompt caching invalidates on tool changes. If we cache the system prompt + tools, we should keep tool declarations stable across turns. Pin the tool list to the campaign config and invalidate the cache only when it changes.
