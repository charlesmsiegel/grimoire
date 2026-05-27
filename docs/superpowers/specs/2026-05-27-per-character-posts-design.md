# Per-Character Posts Design

## Problem

Today the orchestrator always creates a single narrator post per turn, regardless of how many characters are present. In multi-character scenes this produces a wall of text attributed to "narrator" rather than individual character posts that can be displayed, styled, and tracked per character.

## Three Narrator Response Modes

| Mode | Constant | Behavior |
|------|----------|----------|
| All at once | `all_at_once` | Current behavior. One narrator post covering all characters. |
| Per character (single call) | `per_character` | One LLM call. Prompt instructs the model to use `<character ref="...">` and `<narrator>` XML tags in whatever sequence is natural. A character can appear multiple times. Response is parsed into an ordered list of posts, each attributed to its character or narrator. |
| Per character (multi call) | `per_character_multi_call` | After the player posts, the orchestrator enters a speaker loop: a light LLM call picks the next speaker, that character's response streams as a single NPC post, and the frontend shows a "Next" button. Loop continues until the player types input instead of clicking "Next". |

Setting hierarchy (unchanged): scene override > campaign default > global default (`all_at_once`).

## Prompt Template — Response Format Instructions

A new template `context_response_format/default.j2` is injected by the assembler when the mode is `per_character`. It receives the list of present NPC refs and their display names.

```jinja2
# Response format

Structure your response as a sequence of character posts and optional narrator
segments using XML tags. Each character post is wrapped in a `<character>` tag
with the character's ref. Narrator prose (scene-setting, environmental description,
transitions) uses a `<narrator>` tag.

Characters present in this scene:
{% for npc in present_npcs %}
- {{ npc.name }} (ref: `{{ npc.ref }}`)
{% endfor %}

Rules:
- Use `<character ref="...">` tags with the exact ref values listed above.
- A character may appear multiple times if the scene calls for it.
- Use `<narrator>` for environmental prose, transitions, or description not
  attributable to a character.
- Write the prose inside each tag as normal narrative — dialogue, action,
  internal thought.
- Do not nest tags or use any tags other than `<character>` and `<narrator>`.

Example:
<narrator>The gas lamps flicker as wind sweeps through the alley.</narrator>
<character ref="alistair-hyde-smythe">Alistair pulls his coat tighter, scanning
the rooftops. "We're not alone."</character>
<character ref="vivienne-blackwood">Vivienne steps from the shadows, heels
clicking on cobblestone. "You never are, darling."</character>
```

For `per_character_multi_call`, a simpler template tells the LLM to write as a single specified character. It receives only the one character being voiced.

For `all_at_once`, no response format template is injected (current behavior).

The assembler injects the rendered template as a `MessageRole.SYSTEM` message immediately after the lock-in block message and before the spotlight/background/archive tier messages. It is always included (never budget-trimmed) — the model must see the output format instructions to comply.

## Response Parsing — `ResponseSplitter`

New module: `backend/src/grimoire/scenes/response_splitter.py`.

### Data structure

```python
@dataclass
class ResponseSegment:
    kind: Literal["character", "narrator"]
    ref: str | None        # character ref for kind="character", None for narrator
    body: str              # prose content inside the tag
```

### Parsing logic

1. Regex scan for `<character ref="...">...</character>` and `<narrator>...</narrator>` tags, preserving order.
2. Text outside tags becomes a `narrator` segment (handles partial compliance or preamble).
3. Zero tags found: entire response becomes a single narrator segment (graceful degradation to `all_at_once`).
4. Unknown character refs are kept but logged as a warning (may be emergent characters).
5. Adjacent segments with the same `kind` and `ref` are merged (bodies joined with `\n\n`).

### Relationship to other modules

- Splitter runs before extraction. The orchestrator creates posts from segments but passes the full unsplit response to the extractor (extract once from full response).
- When `ExtractionMode.TOGETHER` is active, `strip_tracker_block` runs on the full response before splitting.

## Orchestrator Changes

### Single-call mode (`per_character`)

At the post-creation step (~line 960 in `service.py`):

1. Resolve effective response mode from scene/campaign.
2. If `all_at_once`: current behavior (one narrator post).
3. If `per_character`: strip tracker block, run `ResponseSplitter`, create one post per segment:
   - `kind="narrator"` segments: `Post(author_kind=NARRATOR)`
   - `kind="character"` segments: `Post(author_kind=NPC, author_npc_ref=segment.ref)`
4. Append all posts to the scene in order.
5. Extraction receives the full unsplit response text.

### Multi-call mode (`per_character_multi_call`)

After the player's post is appended, the orchestrator enters a speaker loop:

1. **Speaker selection:** Light LLM call with small context (scene summary, recent posts, cast list) asks which character should speak next. Returns a single character ref.
2. **Character turn:** Build context with that character foregrounded (card/voice in spotlight, single-character response format template). Stream the response. Create one NPC post. Run extraction on that response.
3. **Signal frontend:** Emit `speaker_round_waiting` WebSocket event. Frontend shows "Next" button.
4. **Wait:** Orchestrator holds the turn lock and waits for one of:
   - "Next" signal: loop back to step 1.
   - Player input: speaker loop exits, new player post appended, fresh turn begins.
5. Turn lock stays held for the loop duration (matches the advance-trigger pattern).

### New WebSocket events

- `speaker_round_waiting`: sent after each NPC post in multi-call mode; tells frontend the orchestrator is ready.
- `speaker_round_next`: sent from frontend when player clicks "Next".

### New API endpoint

- `POST /api/campaigns/{id}/turns/next-speaker`: analogous to the advance endpoint. Signals the orchestrator to pick the next speaker and continue the loop.

## Frontend Changes

### InputArea — "Next" button

Follows the existing "Advance" button pattern:

- `speaker_round_waiting` WebSocket event sets `nextSpeakerEnabled: true` in the play reducer.
- InputArea shows a "Next" button alongside the text input (not replacing it).
- Clicking "Next" calls `POST /api/campaigns/{id}/turns/next-speaker`.
- Typing and submitting text submits a player post, which implicitly ends the speaker loop.
- Button disables while the next NPC post is streaming.

### Post rendering

Posts already carry `author_kind` and `author_npc_ref`. The frontend can use these to show character names as headers and apply per-character styling. Presentation details are out of scope for this spec.

### playReducer additions

- New state: `nextSpeakerEnabled: boolean`, `speakerRoundActive: boolean`.
- New action: `"set-next-speaker"`.
- New WebSocket handler for `speaker_round_waiting`.

## Fallback and Edge Cases

**Malformed tags (single-call):** Zero valid tags found means the entire response becomes a single narrator post. No error, no retry.

**Partial tags:** Opening tag with no closing tag (e.g., max tokens hit): everything after the opening tag becomes that character's body. Last segment is allowed to be unclosed.

**Empty segments:** Tags with empty body are dropped — no empty posts.

**Adjacent same-character segments:** Consecutive segments with the same `kind` and `ref` are merged into one post (bodies joined with `\n\n`).

**Speaker selection failure (multi-call):** Unrecognizable ref or failure: pick the present NPC who has spoken least recently. Tie-break randomly.

**Player disconnect during speaker loop:** Loop terminates after a configurable timeout (default 5 minutes). Turn completes with however many NPC posts were generated. On reconnect the player sees the posts and continues normally.

**Single NPC in scene:** `per_character` single-call works (one character tag). `per_character_multi_call` skips speaker selection (one candidate), streams directly, then waits. Player can click "Next" to have the same character speak again.

**Extraction:** Single-call extracts once from full response. Multi-call extracts per character response (each is a separate LLM completion).

## Configuration

### `narrator_mode.py`

- Add `PER_CHARACTER_MULTI_CALL = "per_character_multi_call"`.
- Update `RESPONSE_MODES` tuple to include all three values.
- `effective_response_mode` logic unchanged.

### `OrchestratorConfig`

- `speaker_loop_timeout_seconds: float = 300.0` — wait time before ending speaker loop on disconnect.
- `speaker_select_max_tokens: int = 50` — cap for the speaker-selection LLM call.

### Campaign/scene surface

The existing `narrator_response_mode` string field on campaign config and scene sidecar accepts the new mode value once `RESPONSE_MODES` is updated. No schema migration needed.
